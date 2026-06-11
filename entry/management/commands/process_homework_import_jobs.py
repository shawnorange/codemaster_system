from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from entry.homework_online import parse_homework_import_job
from entry.models import HomeworkImportJob


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued HomeworkImportJob rows outside the HTTP request path."

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
                parse_homework_import_job(import_job)
                import_job.refresh_from_db(fields=["parse_status"])
                self.stdout.write(f"processed import_job={import_job.id} status={import_job.parse_status}")
            except Exception as exc:
                logger.exception("homework import worker crashed import_job=%s", import_job.id)
                HomeworkImportJob.objects.filter(id=import_job.id).update(
                    parse_status=HomeworkImportJob.STATUS_FAILED,
                    candidates_json=[],
                    parse_notes="\n".join(
                        [
                            "页面提示：文件解析时发生后端异常，未生成候选题，请联系管理员查看日志。",
                            "失败步骤：导入解析",
                            f"失败原因：{type(exc).__name__}: {exc}",
                        ]
                    ),
                    updated_at=timezone.now(),
                )
                self.stderr.write(f"failed import_job={import_job.id}: {type(exc).__name__}: {exc}")

            processed_count += 1
            if run_once or (limit and processed_count >= limit):
                break

        self.stdout.write(f"homework import worker processed {processed_count} job(s).")

    def _claim_next_job(self) -> HomeworkImportJob | None:
        with transaction.atomic():
            import_job = (
                HomeworkImportJob.objects.filter(
                    parse_status=HomeworkImportJob.STATUS_UPLOADED,
                    is_active=True,
                )
                .order_by("created_at", "id")
                .first()
            )
            if import_job is None:
                return None

            claimed_count = HomeworkImportJob.objects.filter(
                id=import_job.id,
                parse_status=HomeworkImportJob.STATUS_UPLOADED,
            ).update(
                parse_status=HomeworkImportJob.STATUS_PARSING,
                parse_notes="后台解析任务已领取，正在识别候选题。",
                updated_at=timezone.now(),
            )
            if claimed_count != 1:
                return None

            import_job.refresh_from_db()
            return import_job
