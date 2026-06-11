from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from entry.exam_paper_import import fail_exam_question_bank_import_job, process_exam_question_bank_import_job
from entry.models import ExamQuestionBankImportJob


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued ExamQuestionBankImportJob rows outside the HTTP request path."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process available jobs once and then exit.")
        parser.add_argument("--limit", type=int, default=0, help="Maximum jobs to process before exiting. 0 means unlimited.")
        parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds to wait when no queued job exists.")

    def handle(self, *args, **options):
        run_once = bool(options["once"])
        limit = max(int(options["limit"] or 0), 0)
        poll_interval = max(float(options["poll_interval"] or 0), 0.2)
        processed_count = 0

        while True:
            import_job = self._claim_next_job()
            if import_job is None:
                if run_once or (limit and processed_count >= limit):
                    break
                time.sleep(poll_interval)
                continue

            try:
                process_exam_question_bank_import_job(import_job)
                import_job.refresh_from_db(fields=["status"])
                self.stdout.write(f"processed exam_import_job={import_job.id} status={import_job.status}")
            except Exception as exc:
                logger.exception("exam paper import worker crashed import_job=%s", import_job.id)
                fail_exam_question_bank_import_job(import_job, exc)
                self.stderr.write(f"failed exam_import_job={import_job.id}: {type(exc).__name__}: {exc}")

            processed_count += 1
            if run_once or (limit and processed_count >= limit):
                break

        self.stdout.write(f"exam paper import worker processed {processed_count} job(s).")

    def _claim_next_job(self) -> ExamQuestionBankImportJob | None:
        with transaction.atomic():
            import_job = (
                ExamQuestionBankImportJob.objects.filter(
                    status=ExamQuestionBankImportJob.STATUS_UPLOADED,
                    is_active=True,
                )
                .order_by("created_at", "id")
                .first()
            )
            if import_job is None:
                return None

            claimed_count = ExamQuestionBankImportJob.objects.filter(
                id=import_job.id,
                status=ExamQuestionBankImportJob.STATUS_UPLOADED,
                is_active=True,
            ).update(
                status=ExamQuestionBankImportJob.STATUS_RENDERING,
                status_notes="后台识别任务已领取，正在准备渲染 PDF。",
                updated_at=timezone.now(),
            )
            if claimed_count != 1:
                return None

            import_job.refresh_from_db()
            return import_job
