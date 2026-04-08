from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from entry.account_identity import build_student_default_username, ensure_unique_username
from entry.models import PortalUser, Student


class Command(BaseCommand):
    help = "把错误映射成学生姓名的学生账号，恢复为独立登录账号。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只预览，不真正写库",
        )

    def handle(self, *args, **options):
        students = list(Student.objects.select_related("user", "parent_user").order_by("id"))
        used_usernames = set(PortalUser.objects.values_list("username", flat=True))
        renamed_count = 0

        with transaction.atomic():
            for student in students:
                current_username = student.user.username.strip()
                display_name = student.display_name.strip()
                if not display_name or current_username != display_name:
                    continue

                used_usernames.discard(current_username)
                target_username = ensure_unique_username(
                    build_student_default_username(display_name, student.parent_user.phone if student.parent_user else ""),
                    used_usernames,
                )
                student.user.username = target_username
                student.user.save(update_fields=["username", "updated_at"])
                renamed_count += 1
                self.stdout.write(f"{display_name}: {current_username} -> {target_username}")

            if options["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("dry-run 已回滚，没有修改数据库。"))

        self.stdout.write(self.style.SUCCESS(f"处理完成，共修正 {renamed_count} 个学生账号。"))
