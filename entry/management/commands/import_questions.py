from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from entry.models import Question


ALLOWED_QUESTION_TYPES = {
    "single_choice",
    "judgement",
    "programming",
}
REQUIRED_TEXT_FIELDS = (
    "code",
    "content_slug",
    "level_code",
    "question_type",
    "title",
)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_positive_small_int(value: Any, *, field_name: str) -> int | None:
    raw_value = normalize_text(value)
    if not raw_value:
        return None

    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{field_name} 不是有效整数：{value!r}") from exc

    if parsed < 0:
        raise CommandError(f"{field_name} 不能为负数：{parsed}")
    return parsed


def normalize_positive_int(value: Any, *, field_name: str, default: int = 0) -> int:
    raw_value = normalize_text(value)
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{field_name} 不是有效整数：{value!r}") from exc

    if parsed < 0:
        raise CommandError(f"{field_name} 不能为负数：{parsed}")
    return parsed


def normalize_bool(value: Any, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value

    normalized = normalize_text(value).lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    raise CommandError(f"布尔字段值不合法：{value!r}")


def load_records(path: Path) -> list[dict[str, Any]]:
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CommandError(f"JSON 解析失败：{path}") from exc

    if isinstance(raw_data, list):
        records = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("questions"), list):
        records = raw_data["questions"]
    else:
        raise CommandError("JSON 顶层必须是题目数组，或包含 questions 数组的对象。")

    normalized_records: list[dict[str, Any]] = []
    for index, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            raise CommandError(f"第 {index} 条记录不是对象。")
        normalized_records.append(item)
    return normalized_records


class Command(BaseCommand):
    help = "从 JSON 文件导入 questions 表，按 code 执行 update_or_create。"

    def add_arguments(self, parser):
        parser.add_argument("json_path", type=str, help="题目 JSON 文件路径")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只解析和校验，不真正写入数据库。",
        )

    def handle(self, *args, **options):
        json_path = Path(options["json_path"]).expanduser()
        if not json_path.exists():
            raise CommandError(f"文件不存在：{json_path}")

        records = load_records(json_path)
        summary = {
            "processed_rows": 0,
            "created_questions": 0,
            "updated_questions": 0,
        }

        with transaction.atomic():
            for index, record in enumerate(records, start=1):
                question, created = self._upsert_question(record, row_number=index)
                summary["processed_rows"] += 1
                summary["created_questions" if created else "updated_questions"] += 1
                self.stdout.write(
                    f"[row {index}] {question.code} -> {question.content_slug} / "
                    f"{question.level_code} / {question.question_type}"
                )

            if options["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("dry-run 已回滚，本次没有写入数据库。"))

        self.stdout.write(self.style.SUCCESS("题目导入完成。"))
        for key, value in summary.items():
            self.stdout.write(f"{key}: {value}")

    def _upsert_question(self, record: dict[str, Any], *, row_number: int) -> tuple[Question, bool]:
        normalized_record = {key: record.get(key) for key in record}

        missing_fields = [field for field in REQUIRED_TEXT_FIELDS if not normalize_text(normalized_record.get(field))]
        if missing_fields:
            raise CommandError(f"第 {row_number} 条记录缺少必要字段：{', '.join(missing_fields)}")

        question_type = normalize_text(normalized_record["question_type"])
        if question_type not in ALLOWED_QUESTION_TYPES:
            raise CommandError(
                f"第 {row_number} 条记录 question_type 不合法：{question_type}；"
                f"允许值：{', '.join(sorted(ALLOWED_QUESTION_TYPES))}"
            )

        payload = normalized_record.get("payload") or {}
        if not isinstance(payload, dict):
            raise CommandError(f"第 {row_number} 条记录 payload 必须是对象。")

        defaults = {
            "content_slug": normalize_text(normalized_record["content_slug"]),
            "level_code": normalize_text(normalized_record["level_code"]),
            "question_type": question_type,
            "source_year": normalize_positive_small_int(normalized_record.get("source_year"), field_name="source_year"),
            "source_month": normalize_positive_small_int(normalized_record.get("source_month"), field_name="source_month"),
            "source_question_no": normalize_positive_small_int(
                normalized_record.get("source_question_no"),
                field_name="source_question_no",
            ),
            "title": normalize_text(normalized_record["title"]),
            "payload": payload,
            "sort_order": normalize_positive_int(normalized_record.get("sort_order"), field_name="sort_order"),
            "is_active": normalize_bool(normalized_record.get("is_active"), default=True),
            "is_demo": normalize_bool(normalized_record.get("is_demo"), default=False),
        }
        if defaults["source_month"] is not None and not 1 <= defaults["source_month"] <= 12:
            raise CommandError(f"第 {row_number} 条记录 source_month 必须在 1 到 12 之间。")

        return Question.objects.update_or_create(
            code=normalize_text(normalized_record["code"]),
            defaults=defaults,
        )
