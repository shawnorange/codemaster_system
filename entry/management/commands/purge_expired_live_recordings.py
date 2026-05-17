from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from entry.live_recording import resolve_recording_file_path
from entry.models import ClassroomLiveRecording


class Command(BaseCommand):
    help = "Delete expired live classroom recording files and mark their download links unavailable."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without changing files or rows.")

    def handle(self, *args, **options):
        now = timezone.now()
        fallback_cutoff = now - timedelta(days=15)
        dry_run = bool(options["dry_run"])
        recordings = ClassroomLiveRecording.objects.filter(
            Q(expires_at__lte=now) | Q(expires_at__isnull=True, ended_at__lte=fallback_cutoff),
            status=ClassroomLiveRecording.STATUS_COMPLETED,
            deleted_at__isnull=True,
        )

        marked_count = 0
        deleted_file_count = 0
        for recording in recordings.iterator():
            file_path = resolve_recording_file_path(recording)
            if dry_run:
                self.stdout.write(f"would purge recording={recording.id} path={file_path or ''}")
                continue

            if file_path and file_path.exists() and file_path.is_file():
                try:
                    file_path.unlink()
                    deleted_file_count += 1
                except OSError as exc:
                    self.stderr.write(f"failed to delete recording={recording.id} path={file_path}: {exc}")
                    continue

            recording.deleted_at = now
            recording.file_url = ""
            recording.save(update_fields=["deleted_at", "file_url", "updated_at"])
            marked_count += 1

        if dry_run:
            self.stdout.write(f"{recordings.count()} expired live classroom recordings would be purged.")
        else:
            self.stdout.write(
                f"Purged {marked_count} expired live classroom recordings; deleted {deleted_file_count} files."
            )
