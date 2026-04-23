from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from entry.enumeration_pdf_repair import (
    TARGET_CONTENT_SLUG,
    TARGET_LEVEL_CODE,
    ParsedPdfDocument,
    RepairCandidate,
    build_source_pdf_index,
    extract_program_short_title,
    extract_programming_candidate,
    extract_single_choice_candidate,
    load_pdf_document,
    normalize_title_key,
    quality_score,
)
from entry.models import Question


DEFAULT_SOURCE_DIR = Path(settings.BASE_DIR) / "project_inputs" / "gesp2_exam_sources"
OCR_SCRIPT_PATH = Path(settings.BASE_DIR) / "entry" / "tools" / "pdf_vision_ocr.swift"


class Command(BaseCommand):
    help = (
        "只修复 public.questions 中 GESP2 / enumeration-method 的导入质量；"
        "按数据库现有年份/月反查 PDF，做文本层 + Vision OCR 混合抽取。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="真正写回数据库；默认只 dry-run 输出报告。",
        )
        parser.add_argument(
            "--source-dir",
            type=str,
            default=str(DEFAULT_SOURCE_DIR),
            help="源 PDF 目录，默认使用项目内 gesp2_exam_sources。",
        )
        parser.add_argument(
            "--question-code",
            type=str,
            default="",
            help="只修复指定 code，便于局部调试。",
        )

    def handle(self, *args, **options):
        source_dir = Path(options["source_dir"]).expanduser()
        if not source_dir.exists():
            raise CommandError(f"源目录不存在：{source_dir}")
        if not OCR_SCRIPT_PATH.exists():
            raise CommandError(f"OCR 脚本不存在：{OCR_SCRIPT_PATH}")

        queryset = Question.objects.filter(
            level_code=TARGET_LEVEL_CODE,
            content_slug=TARGET_CONTENT_SLUG,
        ).order_by("source_year", "source_month", "source_question_no", "sort_order", "id")

        question_code = (options.get("question_code") or "").strip()
        if question_code:
            queryset = queryset.filter(code=question_code)

        questions = list(queryset)
        if not questions:
            self.stdout.write(self.style.WARNING("没有命中任何目标题。"))
            return

        pdf_index = build_source_pdf_index(source_dir)
        required_keys = {
            (question.source_year, question.source_month)
            for question in questions
            if question.source_year is not None and question.source_month is not None
        }
        missing_pdf_keys = sorted(key for key in required_keys if key not in pdf_index)
        if missing_pdf_keys:
            missing_labels = ", ".join(f"{year}-{month:02d}" for year, month in missing_pdf_keys)
            raise CommandError(f"未找到目标 PDF：{missing_labels}")

        parsed_docs: dict[tuple[int, int], ParsedPdfDocument] = {}
        report_rows: list[dict[str, Any]] = []
        changed_count = 0
        reviewed_count = 0

        with transaction.atomic():
            for question in questions:
                row = self._repair_question(
                    question=question,
                    pdf_index=pdf_index,
                    parsed_docs=parsed_docs,
                    apply_changes=bool(options["apply"]),
                )
                report_rows.append(row)
                if row["saved"]:
                    changed_count += 1
                if row["review_reasons"]:
                    reviewed_count += 1

            if not options["apply"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("dry-run 已回滚，本次没有写回 questions。"))

        self.stdout.write(self.style.SUCCESS("枚举法题库导入修复完成。"))
        self.stdout.write(f"processed_questions: {len(report_rows)}")
        self.stdout.write(f"changed_questions: {changed_count}")
        self.stdout.write(f"needs_review_questions: {reviewed_count}")
        self.stdout.write("")

        for row in report_rows:
            fields = ", ".join(row["updated_fields"]) or "-"
            reviews = " | ".join(row["review_reasons"]) or "-"
            pdf_name = row["pdf_name"] or "-"
            self.stdout.write(
                f"{row['code']}: pdf={pdf_name} updated={fields} needs_review={reviews}"
            )

    def _repair_question(
        self,
        *,
        question: Question,
        pdf_index: dict[tuple[int, int], Path],
        parsed_docs: dict[tuple[int, int], ParsedPdfDocument],
        apply_changes: bool,
    ) -> dict[str, Any]:
        payload = dict(question.payload or {})
        review_reasons: list[str] = []
        updated_fields: list[str] = []
        saved = False
        pdf_name = ""

        if question.code.find("-card-") >= 0:
            review_reasons.append("衍生教学卡片，未自动套用源题 PDF 内容。")
            saved = self._save_review_flags(
                question=question,
                payload=payload,
                review_reasons=review_reasons,
                apply_changes=apply_changes,
            )
            return {
                "code": question.code,
                "pdf_name": pdf_name,
                "updated_fields": updated_fields,
                "review_reasons": review_reasons,
                "saved": saved,
            }

        pdf_key = (question.source_year, question.source_month)
        pdf_path = pdf_index.get(pdf_key)
        if pdf_path is None:
            review_reasons.append("未命中对应年份/月的 PDF。")
            saved = self._save_review_flags(
                question=question,
                payload=payload,
                review_reasons=review_reasons,
                apply_changes=apply_changes,
            )
            return {
                "code": question.code,
                "pdf_name": pdf_name,
                "updated_fields": updated_fields,
                "review_reasons": review_reasons,
                "saved": saved,
            }

        pdf_name = pdf_path.name
        parsed_doc = parsed_docs.get(pdf_key)
        if parsed_doc is None:
            self.stdout.write(f"加载 PDF：{pdf_name}")
            parsed_doc = load_pdf_document(pdf_path, ocr_script_path=OCR_SCRIPT_PATH)
            parsed_docs[pdf_key] = parsed_doc

        candidate = self._build_candidate(question=question, parsed_doc=parsed_doc)
        if candidate is None:
            review_reasons.append("未能从命中 PDF 中定位到题目块。")
        else:
            review_reasons.extend(candidate.needs_review_reasons)
            for field_name, field_value in candidate.payload_updates.items():
                current_value = payload.get(field_name)
                if self._should_update_field(
                    question=question,
                    field_name=field_name,
                    current_value=current_value,
                    candidate_value=field_value,
                ):
                    payload[field_name] = field_value
                    updated_fields.append(field_name)

        saved = self._save_question_payload(
            question=question,
            payload=payload,
            updated_fields=updated_fields,
            review_reasons=review_reasons,
            apply_changes=apply_changes,
        )
        return {
            "code": question.code,
            "pdf_name": pdf_name,
            "updated_fields": updated_fields,
            "review_reasons": review_reasons,
            "saved": saved,
        }

    def _build_candidate(self, *, question: Question, parsed_doc: ParsedPdfDocument) -> RepairCandidate | None:
        payload = dict(question.payload or {})
        if question.question_type == "programming":
            short_title = extract_program_short_title(question.title, payload)
            title_key = normalize_title_key(short_title)
            text_block = parsed_doc.program_blocks_text.get(title_key)
            ocr_block = parsed_doc.program_blocks_ocr.get(title_key)
            if text_block is None and ocr_block is None:
                return None
            start_page = min(
                block.start_page
                for block in (text_block, ocr_block)
                if block is not None
            )
            end_page = max(
                block.end_page
                for block in (text_block, ocr_block)
                if block is not None
            )
            page_text = "\n".join(parsed_doc.text_pages[start_page - 1 : end_page])
            return extract_programming_candidate(
                question_code=question.code,
                pdf_path=parsed_doc.pdf_path,
                short_title=short_title,
                block_text=(text_block.text if text_block else ""),
                block_ocr=(ocr_block.text if ocr_block else ""),
                page_text=page_text,
            )

        if question.source_question_no is None:
            return None

        text_block = parsed_doc.question_blocks_text.get(question.source_question_no)
        ocr_block = parsed_doc.question_blocks_ocr.get(question.source_question_no)
        if text_block is None and ocr_block is None:
            return None

        return extract_single_choice_candidate(
            question_code=question.code,
            pdf_path=parsed_doc.pdf_path,
            block_text=(text_block.text if text_block else ""),
            block_ocr=(ocr_block.text if ocr_block else ""),
        )

    def _save_review_flags(
        self,
        *,
        question: Question,
        payload: dict[str, Any],
        review_reasons: list[str],
        apply_changes: bool,
    ) -> bool:
        return self._save_question_payload(
            question=question,
            payload=payload,
            updated_fields=[],
            review_reasons=review_reasons,
            apply_changes=apply_changes,
        )

    def _save_question_payload(
        self,
        *,
        question: Question,
        payload: dict[str, Any],
        updated_fields: list[str],
        review_reasons: list[str],
        apply_changes: bool,
    ) -> bool:
        changed = False
        deduped_reasons = list(dict.fromkeys(reason.strip() for reason in review_reasons if reason.strip()))

        if deduped_reasons:
            if payload.get("needs_review") is not True:
                payload["needs_review"] = True
                changed = True
            if payload.get("needs_review_reasons") != deduped_reasons:
                payload["needs_review_reasons"] = deduped_reasons
                changed = True
        else:
            if "needs_review" in payload:
                payload.pop("needs_review", None)
                changed = True
            if "needs_review_reasons" in payload:
                payload.pop("needs_review_reasons", None)
                changed = True

        if updated_fields:
            changed = True

        if changed and apply_changes:
            question.payload = payload
            question.save(update_fields=["payload"])
            return True

        return changed and not apply_changes

    def _should_update_field(
        self,
        *,
        question: Question,
        field_name: str,
        current_value: Any,
        candidate_value: Any,
    ) -> bool:
        candidate_score = quality_score(field_name, candidate_value)
        current_score = quality_score(field_name, current_value)
        if candidate_score == 0:
            return False
        if current_score == 0:
            return True
        if field_name == "code" and question.question_type != "programming":
            return False
        if field_name == "options":
            return False
        return candidate_score > current_score
