from __future__ import annotations

import json
import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from entry.models import (
    ExamQuestionBankAsset,
    ExamQuestionBankOption,
    ExamQuestionBankPaper,
    ExamQuestionBankQuestion,
)


SUPPORTED_IMPORT = ("GESP1", 2026, 3, "2026_3_c_1")
ALLOWED_QUESTION_TYPES = {
    ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
    ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
    ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
}

HERMES_PAPER_SQL = """
SELECT
  q.question_uid,
  q.level,
  q.source_pdf_id,
  q.source_file,
  q.import_batch_uid,
  q.question_no,
  q.question_type,
  q.stem_md,
  q.answer_json,
  q.analysis_md,
  q.full_json,
  q.full_json->'programming' AS programming,

  COALESCE((
    SELECT jsonb_agg(
      jsonb_build_object(
        'key', o.option_key,
        'text', o.option_text_md,
        'sort_order', o.sort_order
      )
      ORDER BY o.sort_order
    )
    FROM qb_question_options o
    WHERE o.question_uid = q.question_uid
  ), '[]'::jsonb) AS options,

  COALESCE((
    SELECT jsonb_agg(
      jsonb_build_object(
        'asset_uid', a.asset_uid,
        'role', a.asset_role,
        'type', a.asset_type,
        'relative_path', a.relative_path,
        'public_url', a.public_url,
        'alt', a.alt,
        'width', a.width,
        'height', a.height
      )
      ORDER BY a.id
    )
    FROM qb_question_assets a
    WHERE a.question_uid = q.question_uid
      AND a.asset_role = 'content'
  ), '[]'::jsonb) AS content_assets

FROM qb_questions q
WHERE q.level = %s
  AND q.source_pdf_id = %s
ORDER BY q.question_no
"""


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_positive_int(value: Any, *, field_name: str) -> int:
    raw_value = normalize_text(value)
    if not raw_value:
        raise CommandError(f"{field_name} 不能为空。")
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{field_name} 不是有效整数：{value!r}") from exc
    if parsed <= 0:
        raise CommandError(f"{field_name} 必须为正整数：{parsed}")
    return parsed


def normalize_optional_positive_int(value: Any, *, field_name: str) -> int | None:
    raw_value = normalize_text(value)
    if not raw_value:
        return None
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{field_name} 不是有效整数：{value!r}") from exc
    if parsed <= 0:
        raise CommandError(f"{field_name} 必须为正整数：{parsed}")
    return parsed


def normalize_json_value(value: Any, *, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value


def normalize_json_list(value: Any, *, field_name: str) -> list[dict[str, Any]]:
    normalized = normalize_json_value(value, default=[])
    if not isinstance(normalized, list):
        raise CommandError(f"{field_name} 必须是数组。")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(normalized, start=1):
        if not isinstance(item, dict):
            raise CommandError(f"{field_name}[{index}] 必须是对象。")
        result.append(item)
    return result


def build_paper_title(*, level: str, year: int, month: int) -> str:
    if level == "GESP1":
        return f"{level} {year}年{month}月C++1级真题"
    return f"{level} {year}年{month}月真题"


class Command(BaseCommand):
    help = "从 Hermes 源 PostgreSQL 读取指定真题卷，并同步到当前考试系统题库快照表。"

    def add_arguments(self, parser):
        parser.add_argument("--level", required=True, help="级别，例如 GESP1。")
        parser.add_argument("--year", required=True, type=int, help="年份，例如 2026。")
        parser.add_argument("--month", required=True, type=int, help="月份，例如 3。")
        parser.add_argument("--source-pdf-id", required=True, help="Hermes source_pdf_id，例如 2026_3_c_1。")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="读取源库并执行校验，但回滚目标库写入。",
        )

    def handle(self, *args, **options):
        level = normalize_text(options["level"])
        year = int(options["year"])
        month = int(options["month"])
        source_pdf_id = normalize_text(options["source_pdf_id"])
        dry_run = bool(options["dry_run"])

        requested_import = (level, year, month, source_pdf_id)
        if requested_import != SUPPORTED_IMPORT:
            supported_level, supported_year, supported_month, supported_source_pdf_id = SUPPORTED_IMPORT
            raise CommandError(
                "当前命令本轮只允许导入 "
                f"{supported_level} {supported_year}年{supported_month}月 "
                f"source_pdf_id={supported_source_pdf_id}。"
            )

        database_url = normalize_text(os.environ.get("HERMES_SOURCE_DATABASE_URL"))
        if not database_url:
            raise CommandError("未设置 HERMES_SOURCE_DATABASE_URL，无法读取 Hermes 源库。")

        rows = self.fetch_hermes_rows(
            database_url=database_url,
            level=level,
            source_pdf_id=source_pdf_id,
        )
        if not rows:
            raise CommandError(f"Hermes 源库没有找到 {level} / {source_pdf_id}。")

        with transaction.atomic():
            summary = self.import_rows(
                rows,
                level=level,
                year=year,
                month=month,
                source_pdf_id=source_pdf_id,
            )
            if dry_run:
                transaction.set_rollback(True)

        if dry_run:
            self.stdout.write(self.style.WARNING("dry-run 已回滚，目标库没有写入。"))

        self.stdout.write(self.style.SUCCESS("Hermes 试卷同步完成。"))
        self.stdout.write(f"卷：导入 {summary['papers_created']}，更新 {summary['papers_updated']}")
        self.stdout.write(f"题：导入 {summary['questions_created']}，更新 {summary['questions_updated']}")
        self.stdout.write(f"选项：导入 {summary['options_created']}，更新 {summary['options_updated']}")
        self.stdout.write(f"content 图片：导入 {summary['assets_created']}，更新 {summary['assets_updated']}")
        if summary["options_deleted"] or summary["assets_deleted"]:
            self.stdout.write(
                f"已清理旧快照：选项 {summary['options_deleted']}，content 图片 {summary['assets_deleted']}"
            )

    def fetch_hermes_rows(self, *, database_url: str, level: str, source_pdf_id: str) -> list[dict[str, Any]]:
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError as exc:
            raise CommandError("当前环境缺少 psycopg2，无法连接 Hermes PostgreSQL。") from exc

        connection = None
        try:
            connection = psycopg2.connect(database_url)
            connection.set_session(readonly=True, autocommit=True)
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(HERMES_PAPER_SQL, [level, source_pdf_id])
                return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            raise CommandError(
                f"Hermes 源库连接或读取失败：{type(exc).__name__}。请检查 HERMES_SOURCE_DATABASE_URL 和源库权限。"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def import_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        level: str,
        year: int,
        month: int,
        source_pdf_id: str,
    ) -> dict[str, int]:
        summary = {
            "papers_created": 0,
            "papers_updated": 0,
            "questions_created": 0,
            "questions_updated": 0,
            "options_created": 0,
            "options_updated": 0,
            "options_deleted": 0,
            "assets_created": 0,
            "assets_updated": 0,
            "assets_deleted": 0,
        }

        normalized_rows = [self.normalize_row(row, row_number=index) for index, row in enumerate(rows, start=1)]
        first_row = normalized_rows[0]
        paper, paper_created = ExamQuestionBankPaper.objects.update_or_create(
            source=ExamQuestionBankPaper.SOURCE_HERMES,
            source_pdf_id=source_pdf_id,
            defaults={
                "level": level,
                "year": year,
                "month": month,
                "source_file": first_row["source_file"],
                "title": build_paper_title(level=level, year=year, month=month),
                "import_batch_uid": first_row["import_batch_uid"],
                "is_active": True,
            },
        )
        summary["papers_created" if paper_created else "papers_updated"] += 1

        for row in normalized_rows:
            if row["level"] != level or row["source_pdf_id"] != source_pdf_id:
                raise CommandError(
                    "Hermes 源库返回了目标卷以外的数据："
                    f"{row['level']} / {row['source_pdf_id']} / {row['question_uid']}"
                )

            question, question_created = ExamQuestionBankQuestion.objects.update_or_create(
                paper=paper,
                question_uid=row["question_uid"],
                defaults={
                    "question_no": row["question_no"],
                    "question_type": row["question_type"],
                    "stem_md": row["stem_md"],
                    "answer_json": row["answer_json"],
                    "analysis_md": row["analysis_md"],
                    "programming_json": row["programming_json"],
                    "full_json": row["full_json"],
                },
            )
            summary["questions_created" if question_created else "questions_updated"] += 1

            summary = self.sync_options(question=question, options=row["options"], summary=summary)
            summary = self.sync_assets(question=question, assets=row["content_assets"], summary=summary)

        return summary

    def normalize_row(self, row: dict[str, Any], *, row_number: int) -> dict[str, Any]:
        question_uid = normalize_text(row.get("question_uid"))
        question_type = normalize_text(row.get("question_type"))
        if not question_uid:
            raise CommandError(f"第 {row_number} 行缺少 question_uid。")
        if question_type not in ALLOWED_QUESTION_TYPES:
            raise CommandError(f"第 {row_number} 行 question_type 不支持：{question_type}")

        return {
            "question_uid": question_uid,
            "level": normalize_text(row.get("level")),
            "source_pdf_id": normalize_text(row.get("source_pdf_id")),
            "source_file": normalize_text(row.get("source_file")),
            "import_batch_uid": normalize_text(row.get("import_batch_uid")),
            "question_no": normalize_positive_int(row.get("question_no"), field_name=f"第 {row_number} 行 question_no"),
            "question_type": question_type,
            "stem_md": normalize_text(row.get("stem_md")),
            "answer_json": normalize_json_value(row.get("answer_json"), default={}),
            "analysis_md": normalize_text(row.get("analysis_md")),
            "full_json": normalize_json_value(row.get("full_json"), default={}),
            "programming_json": normalize_json_value(row.get("programming"), default={}),
            "options": normalize_json_list(row.get("options"), field_name=f"第 {row_number} 行 options"),
            "content_assets": normalize_json_list(row.get("content_assets"), field_name=f"第 {row_number} 行 content_assets"),
        }

    def sync_options(
        self,
        *,
        question: ExamQuestionBankQuestion,
        options: list[dict[str, Any]],
        summary: dict[str, int],
    ) -> dict[str, int]:
        seen_keys: set[str] = set()
        for index, option in enumerate(options, start=1):
            option_key = normalize_text(option.get("key"))
            if not option_key:
                raise CommandError(f"第 {question.question_no} 题第 {index} 个选项缺少 key。")
            seen_keys.add(option_key)
            sort_order = normalize_positive_int(
                option.get("sort_order") if option.get("sort_order") is not None else index,
                field_name=f"第 {question.question_no} 题选项 {option_key} sort_order",
            )
            _, created = ExamQuestionBankOption.objects.update_or_create(
                question=question,
                option_key=option_key,
                defaults={
                    "option_text_md": normalize_text(option.get("text")),
                    "sort_order": sort_order,
                },
            )
            summary["options_created" if created else "options_updated"] += 1

        stale_options = question.options.exclude(option_key__in=seen_keys)
        deleted_count, _ = stale_options.delete()
        summary["options_deleted"] += deleted_count
        return summary

    def sync_assets(
        self,
        *,
        question: ExamQuestionBankQuestion,
        assets: list[dict[str, Any]],
        summary: dict[str, int],
    ) -> dict[str, int]:
        seen_asset_uids: set[str] = set()
        seen_asset_paths: set[tuple[str, str]] = set()

        for index, asset in enumerate(assets, start=1):
            asset_role = normalize_text(asset.get("role") or asset.get("asset_role") or "content")
            if asset_role != "content":
                continue

            asset_uid = normalize_text(asset.get("asset_uid"))
            relative_path = normalize_text(asset.get("relative_path"))
            public_url = normalize_text(asset.get("public_url"))
            if not asset_uid and not relative_path and not public_url:
                raise CommandError(f"第 {question.question_no} 题第 {index} 个图片缺少 asset_uid/relative_path/public_url。")

            asset_defaults = {
                "asset_role": asset_role,
                "asset_type": normalize_text(asset.get("type") or asset.get("asset_type")),
                "relative_path": relative_path,
                "public_url": public_url,
                "alt": normalize_text(asset.get("alt")),
                "width": normalize_optional_positive_int(asset.get("width"), field_name="width"),
                "height": normalize_optional_positive_int(asset.get("height"), field_name="height"),
            }
            if asset_uid:
                seen_asset_uids.add(asset_uid)
            else:
                seen_asset_paths.add((asset_role, relative_path))

            _, created = self.upsert_asset(
                question=question,
                asset_uid=asset_uid,
                asset_role=asset_role,
                relative_path=relative_path,
                defaults=asset_defaults,
            )
            summary["assets_created" if created else "assets_updated"] += 1

        stale_assets = question.assets.filter(asset_role="content")
        if seen_asset_uids:
            stale_assets = stale_assets.exclude(asset_uid__in=seen_asset_uids)
        if seen_asset_paths:
            for asset_role, relative_path in seen_asset_paths:
                stale_assets = stale_assets.exclude(asset_role=asset_role, relative_path=relative_path)
        deleted_count, _ = stale_assets.delete()
        summary["assets_deleted"] += deleted_count
        return summary

    def upsert_asset(
        self,
        *,
        question: ExamQuestionBankQuestion,
        asset_uid: str,
        asset_role: str,
        relative_path: str,
        defaults: dict[str, Any],
    ) -> tuple[ExamQuestionBankAsset, bool]:
        if asset_uid:
            asset = ExamQuestionBankAsset.objects.filter(question=question, asset_uid=asset_uid).first()
            if asset is None and relative_path:
                asset = ExamQuestionBankAsset.objects.filter(
                    question=question,
                    asset_role=asset_role,
                    relative_path=relative_path,
                ).first()
            if asset is not None:
                for field_name, value in {**defaults, "asset_uid": asset_uid}.items():
                    setattr(asset, field_name, value)
                asset.save()
                return asset, False

            return ExamQuestionBankAsset.objects.create(
                question=question,
                asset_uid=asset_uid,
                **defaults,
            ), True

        defaults["asset_uid"] = ""
        return ExamQuestionBankAsset.objects.update_or_create(
            question=question,
            asset_role=asset_role,
            relative_path=relative_path,
            defaults=defaults,
        )
