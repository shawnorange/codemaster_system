from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from entry.account_identity import build_student_default_username, ensure_unique_username, normalize_phone
from entry.course_identity import get_course_title_from_slug, normalize_assignment_level, resolve_course_slug
from entry.models import PortalUser, Student

XML_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

HEADER_NAME = "name(学生姓名)"
HEADER_PHONE = "phone(家长手机)"
HEADER_SCHOOL = "school(所在学校)"
HEADER_LEVEL = "level(所学级别)"
HEADER_CCF_LEVEL = "ccf_level(等级考或竞赛)"
HEADER_CREATE_DATE = "create_date(创建日期)"


def normalize_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_level_tokens(raw_level: str) -> list[str]:
    return [token for token in re.split(r"[;；,/、\s]+", normalize_text(raw_level)) if token]


def infer_level_candidate(raw_level: str, raw_ccf_level: str) -> str:
    ccf_level = normalize_text(raw_ccf_level).upper()
    if ccf_level:
        return ccf_level
    tokens = parse_level_tokens(raw_level)
    return tokens[0].upper() if tokens else ""


def infer_primary_course(raw_level: str, raw_ccf_level: str) -> str:
    level_candidate = infer_level_candidate(raw_level, raw_ccf_level)
    course_slug = resolve_course_slug(raw_level, level_candidate) or "cpp"
    return get_course_title_from_slug(course_slug, default="C++")


def infer_primary_track(raw_ccf_level: str) -> str:
    upper = normalize_text(raw_ccf_level).upper()
    if upper.startswith("GESP"):
        return "GESP"
    if upper.startswith("CSP"):
        return "CSP"
    return ""


def infer_primary_level(raw_level: str, raw_ccf_level: str) -> str:
    level_candidate = infer_level_candidate(raw_level, raw_ccf_level)
    course_slug = resolve_course_slug(raw_level, level_candidate)
    normalized_level = normalize_assignment_level(course_slug or "", level_candidate)
    return normalized_level or level_candidate


def parent_username_from_phone(phone: str, row_number: int) -> str:
    if phone:
        return f"parent_{phone}"
    return f"parent_import_{row_number:03d}"


def _column_index(column_ref: str) -> int:
    value = 0
    for char in column_ref:
        if char.isalpha():
            value = value * 26 + ord(char.upper()) - 64
    return value


def read_excel_rows(path: Path) -> list[dict[str, str]]:
    with ZipFile(path) as workbook_zip:
        workbook = ET.fromstring(workbook_zip.read("xl/workbook.xml"))
        relationships = ET.fromstring(workbook_zip.read("xl/_rels/workbook.xml.rels"))
        relationship_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in relationships}
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook_zip.namelist():
            shared_root = ET.fromstring(workbook_zip.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("a:si", XML_NS):
                shared_strings.append("".join(node.text or "" for node in item.iterfind(".//a:t", XML_NS)))

        sheets = workbook.find("a:sheets", XML_NS)
        if sheets is None or not list(sheets):
            raise CommandError("Excel 中没有可读取的工作表。")

        first_sheet = list(sheets)[0]
        relation_id = first_sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = relationship_map[relation_id]
        if not target.startswith("xl/"):
            target = f"xl/{target}"

        sheet_root = ET.fromstring(workbook_zip.read(target))
        all_rows: list[list[str]] = []
        for row in sheet_root.findall(".//a:sheetData/a:row", XML_NS):
            cell_values: dict[int, str] = {}
            for cell in row.findall("a:c", XML_NS):
                reference = cell.attrib.get("r", "")
                column_ref = re.sub(r"\d+", "", reference)
                index = _column_index(column_ref)
                cell_type = cell.attrib.get("t")
                raw_value = cell.find("a:v", XML_NS)
                value = normalize_text(raw_value.text if raw_value is not None else "")
                if cell_type == "s" and value:
                    value = shared_strings[int(value)]
                cell_values[index] = value
            if cell_values:
                width = max(cell_values)
                all_rows.append([cell_values.get(i, "") for i in range(1, width + 1)])

    if not all_rows:
        raise CommandError("Excel 工作表为空，无法导入。")

    headers = [normalize_text(value) for value in all_rows[0]]
    required_headers = {HEADER_NAME, HEADER_PHONE, HEADER_LEVEL, HEADER_CCF_LEVEL}
    missing_headers = [header for header in required_headers if header not in headers]
    if missing_headers:
        raise CommandError(f"Excel 缺少必要列：{', '.join(missing_headers)}")

    records: list[dict[str, str]] = []
    for row in all_rows[1:]:
        padded_row = row + [""] * (len(headers) - len(row))
        record = {header: normalize_text(value) for header, value in zip(headers, padded_row)}
        if any(record.values()):
            records.append(record)
    return records


def iter_rows_with_index(rows: Iterable[dict[str, str]]) -> Iterable[tuple[int, dict[str, str]]]:
    for offset, row in enumerate(rows, start=2):
        yield offset, row


class Command(BaseCommand):
    help = "导入 C++ 学生 Excel，创建并更新 PortalUser / Student 记录。"

    def add_arguments(self, parser):
        default_import_password = str(getattr(settings, "IMPORT_CPP_DEFAULT_PASSWORD", "") or "")
        parser.add_argument("xlsx_path", type=str, help="Excel 文件路径")
        parser.add_argument(
            "--teacher-username",
            default="teacher001",
            help="导入后的默认负责教师账号，默认 teacher001",
        )
        parser.add_argument(
            "--default-password",
            default=default_import_password,
            help="新建学生账号和家长账号的默认密码；默认读取 CODEMASTER_IMPORT_DEFAULT_PASSWORD",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只解析和校验，不真正写库",
        )

    def handle(self, *args, **options):
        xlsx_path = Path(options["xlsx_path"]).expanduser()
        if not xlsx_path.exists():
            raise CommandError(f"文件不存在：{xlsx_path}")

        try:
            teacher_user = PortalUser.objects.get(
                username=options["teacher_username"],
                role=PortalUser.ROLE_TEACHER,
                is_active=True,
            )
        except PortalUser.DoesNotExist as exc:
            raise CommandError(f"未找到教师账号：{options['teacher_username']}") from exc

        rows = read_excel_rows(xlsx_path)
        summary = {
            "processed_rows": 0,
            "created_parent_users": 0,
            "updated_parent_users": 0,
            "created_student_users": 0,
            "updated_student_users": 0,
            "created_students": 0,
            "updated_students": 0,
        }
        used_usernames = set(PortalUser.objects.values_list("username", flat=True))

        with transaction.atomic():
            for row_number, row in iter_rows_with_index(rows):
                student_name = normalize_text(row.get(HEADER_NAME))
                parent_phone = normalize_phone(row.get(HEADER_PHONE))
                school_name = normalize_text(row.get(HEADER_SCHOOL))
                raw_level = normalize_text(row.get(HEADER_LEVEL))
                raw_ccf_level = normalize_text(row.get(HEADER_CCF_LEVEL))
                raw_create_date = normalize_text(row.get(HEADER_CREATE_DATE))

                if not student_name:
                    self.stdout.write(self.style.WARNING(f"第 {row_number} 行缺少学生姓名，已跳过。"))
                    continue

                parent_username = parent_username_from_phone(parent_phone, row_number)
                parent_user, parent_created = self._upsert_parent_user(
                    username=parent_username,
                    phone=parent_phone,
                    display_name=f"{student_name}家长",
                    default_password=options["default_password"],
                )
                existing_student = self._find_existing_student(student_name=student_name, parent_phone=parent_phone)
                if existing_student:
                    student_username = existing_student.user.username
                    used_usernames.add(student_username)
                else:
                    student_username = ensure_unique_username(
                        build_student_default_username(student_name, parent_phone),
                        used_usernames,
                    )
                student_user, student_user_created = self._upsert_student_user(
                    username=student_username,
                    full_name=student_name,
                    default_password=options["default_password"],
                    existing_student=existing_student,
                )
                student_profile, student_created = self._upsert_student_profile(
                    student_user=student_user,
                    parent_user=parent_user,
                    teacher_user=teacher_user,
                    student_name=student_name,
                    school_name=school_name,
                    raw_level=raw_level,
                    raw_ccf_level=raw_ccf_level,
                )

                summary["processed_rows"] += 1
                summary["created_parent_users" if parent_created else "updated_parent_users"] += 1
                summary["created_student_users" if student_user_created else "updated_student_users"] += 1
                summary["created_students" if student_created else "updated_students"] += 1

                self.stdout.write(
                    f"[row {row_number}] {student_name} -> "
                    f"parent={parent_user.username}, student={student_user.username}, "
                    f"path={student_profile.primary_course_name}"
                    f"{' > ' + student_profile.primary_track_name if student_profile.primary_track_name else ''}"
                    f"{' > ' + student_profile.primary_level_name if student_profile.primary_level_name else ''}"
                    f"{' | create_date=' + raw_create_date if raw_create_date else ''}"
                )

            if options["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("dry-run 已回滚，本次没有写入数据库。"))

        self.stdout.write(self.style.SUCCESS("导入完成。"))
        for key, value in summary.items():
            self.stdout.write(f"{key}: {value}")

    def _find_existing_student(self, *, student_name: str, parent_phone: str) -> Student | None:
        queryset = Student.objects.select_related("user", "parent_user").filter(display_name=student_name)
        if parent_phone:
            queryset = queryset.filter(parent_user__phone=parent_phone)
        return queryset.order_by("id").first()

    def _upsert_parent_user(self, *, username: str, phone: str, display_name: str, default_password: str) -> tuple[PortalUser, bool]:
        parent_user = PortalUser.objects.filter(username=username).first()
        if parent_user is None:
            parent_user = PortalUser(
                username=username,
                role=PortalUser.ROLE_PARENT,
                full_name=display_name,
                phone=phone,
                is_active=True,
            )
            parent_user.set_password(default_password)
            parent_user.save()
            return parent_user, True

        changed_fields: list[str] = []
        if parent_user.role != PortalUser.ROLE_PARENT:
            raise CommandError(f"账号 {username} 已存在，但角色不是家长，无法安全导入。")
        if phone and parent_user.phone != phone:
            parent_user.phone = phone
            changed_fields.append("phone")
        if not parent_user.full_name and display_name:
            parent_user.full_name = display_name
            changed_fields.append("full_name")
        if changed_fields:
            parent_user.save(update_fields=changed_fields + ["updated_at"])
        return parent_user, False

    def _upsert_student_user(
        self,
        *,
        username: str,
        full_name: str,
        default_password: str,
        existing_student: Student | None = None,
    ) -> tuple[PortalUser, bool]:
        if existing_student is not None:
            student_user = existing_student.user
            changed_fields: list[str] = []
            if student_user.role != PortalUser.ROLE_STUDENT:
                raise CommandError(f"账号 {student_user.username} 已存在，但角色不是学生，无法安全导入。")
            if student_user.full_name != full_name:
                student_user.full_name = full_name
                changed_fields.append("full_name")
            if changed_fields:
                student_user.save(update_fields=changed_fields + ["updated_at"])
            return student_user, False

        student_user = PortalUser.objects.filter(username=username).first()
        if student_user is None:
            student_user = PortalUser(
                username=username,
                role=PortalUser.ROLE_STUDENT,
                full_name=full_name,
                is_active=True,
            )
            student_user.set_password(default_password)
            student_user.save()
            return student_user, True

        changed_fields: list[str] = []
        if student_user.role != PortalUser.ROLE_STUDENT:
            raise CommandError(f"账号 {username} 已存在，但角色不是学生，无法安全导入。")
        if student_user.full_name != full_name:
            student_user.full_name = full_name
            changed_fields.append("full_name")
        if changed_fields:
            student_user.save(update_fields=changed_fields + ["updated_at"])
        return student_user, False

    def _upsert_student_profile(
        self,
        *,
        student_user: PortalUser,
        parent_user: PortalUser,
        teacher_user: PortalUser,
        student_name: str,
        school_name: str,
        raw_level: str,
        raw_ccf_level: str,
    ) -> tuple[Student, bool]:
        defaults = {
            "parent_user": parent_user,
            "teacher_user": teacher_user,
            "display_name": student_name,
            "grade": "",
            "campus": school_name,
            "primary_course_name": infer_primary_course(raw_level, raw_ccf_level),
            "primary_track_name": infer_primary_track(raw_ccf_level),
            "primary_level_name": infer_primary_level(raw_level, raw_ccf_level),
        }
        return Student.objects.update_or_create(user=student_user, defaults=defaults)
