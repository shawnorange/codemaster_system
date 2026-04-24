from __future__ import annotations

import csv
import io
import secrets
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone

from .account_identity import normalize_phone
from .content_visibility import infer_cpp_permission_code, normalize_stage_code
from .models import Course, PortalUser, Student, TeacherStudentAssignment

STUDENT_IMPORT_ADMIN_USERNAME = "teacher001"
DEFAULT_IMPORTED_ACCOUNT_PASSWORD = "123456"
CSV_IMPORT_ALLOWED_CPP_LEVELS = (
    "GESP1",
    "GESP2",
    "GESP3",
    "GESP4",
    "GESP5",
    "GESP6",
    "GESP7",
    "GESP8",
    "CSP-J",
    "CSP-S",
)
XLSX_XML_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


class StudentImportError(Exception):
    """Raised when a row cannot be imported safely."""


@dataclass(frozen=True)
class StudentImportRow:
    row_number: int
    student_name: str
    parent_phone: str
    primary_track_name: str
    primary_level_name: str


@dataclass(frozen=True)
class StudentProvisionResult:
    student: Student
    student_user: PortalUser
    parent_user: PortalUser
    assignment: TeacherStudentAssignment
    created_student: bool
    created_student_user: bool
    created_parent_user: bool
    created_assignment: bool
    updated_assignment: bool


def teacher_can_import_students(portal_user: PortalUser) -> bool:
    return (
        portal_user.is_active
        and portal_user.role == PortalUser.ROLE_TEACHER
        and portal_user.username == STUDENT_IMPORT_ADMIN_USERNAME
    )


def normalize_student_import_level_name(value: str | None) -> str:
    normalized = normalize_stage_code(value)
    if normalized == "CSPJ":
        return "CSP-J"
    if normalized == "CSPS":
        return "CSP-S"
    return normalized


def find_existing_student_by_name_and_parent_phone(*, student_name: str, parent_phone: str) -> Student | None:
    queryset = Student.objects.select_related("user", "parent_user").filter(display_name=student_name)
    if parent_phone:
        queryset = queryset.filter(parent_user__phone=parent_phone)
    return queryset.order_by("id").first()


def infer_student_primary_track_name(*, course: Course, primary_level_name: str) -> str:
    normalized_primary_level_name = normalize_student_import_level_name(primary_level_name)
    if course.slug == "cpp":
        if normalized_primary_level_name.startswith("GESP"):
            return "GESP"
        if normalized_primary_level_name.startswith("CSP"):
            return "CSP"
    return ""


def parse_student_import_file(uploaded_file: UploadedFile) -> list[StudentImportRow]:
    filename = str(getattr(uploaded_file, "name", "") or "").strip().lower()
    payload = uploaded_file.read()
    if not payload:
        raise ValidationError("请先选择一个非空的 CSV / XLSX 文件。")

    if filename.endswith(".xlsx"):
        parsed_rows = _parse_student_import_xlsx_rows(payload)
        file_label = "XLSX"
    elif filename.endswith(".csv") or not filename:
        parsed_rows = _parse_student_import_csv_rows(payload)
        file_label = "CSV"
    else:
        raise ValidationError("当前只支持 CSV / XLSX 文件。")

    if not parsed_rows:
        raise ValidationError(f"{file_label} 文件没有可导入的数据。")

    has_header = _looks_like_header(parsed_rows[0])
    data_rows = parsed_rows[1:] if has_header else parsed_rows
    if not data_rows:
        raise ValidationError(f"{file_label} 文件没有可导入的数据行。")

    start_row_number = 2 if has_header else 1
    rows: list[StudentImportRow] = []
    for offset, row in enumerate(data_rows, start=start_row_number):
        padded = row[:4] + [""] * max(0, 4 - len(row))
        rows.append(
            StudentImportRow(
                row_number=offset,
                student_name=padded[0].strip(),
                parent_phone=normalize_phone(padded[1]),
                primary_track_name=padded[2].strip(),
                primary_level_name=normalize_student_import_level_name(padded[3]),
            )
        )
    return rows


def parse_student_import_csv(uploaded_file: UploadedFile) -> list[StudentImportRow]:
    return parse_student_import_file(uploaded_file)


def import_students_from_rows(
    *,
    teacher_user: PortalUser,
    course: Course,
    rows: list[StudentImportRow],
    default_password: str = DEFAULT_IMPORTED_ACCOUNT_PASSWORD,
) -> dict[str, object]:
    used_usernames = set(PortalUser.objects.values_list("username", flat=True))
    success_count = 0
    failure_items: list[dict[str, object]] = []

    for row in rows:
        try:
            level_code = validate_student_import_row(row=row, course=course)
            with transaction.atomic():
                create_or_update_student_with_parent_and_assignment(
                    teacher_user=teacher_user,
                    course=course,
                    student_name=row.student_name,
                    parent_phone=row.parent_phone,
                    primary_track_name=row.primary_track_name,
                    primary_level_name=row.primary_level_name,
                    level_code=level_code,
                    used_usernames=used_usernames,
                    default_password=default_password,
                    allow_existing_student=True,
                )
        except (StudentImportError, ValidationError) as exc:
            failure_items.append(
                {
                    "row_number": row.row_number,
                    "student_name": row.student_name,
                    "reason": str(exc),
                }
            )
            continue

        success_count += 1

    return {
        "success_count": success_count,
        "failure_count": len(failure_items),
        "failure_items": failure_items,
    }


def validate_student_import_row(*, row: StudentImportRow, course: Course) -> str:
    if not row.student_name:
        raise StudentImportError("学生姓名不能为空。")
    if not row.parent_phone:
        raise StudentImportError("家长手机号不能为空。")
    if not row.primary_track_name:
        raise StudentImportError("当前学习内容不能为空。")
    if not row.primary_level_name:
        raise StudentImportError("当前级别不能为空。")

    if course.slug != "cpp":
        raise StudentImportError("当前仅支持在 C++ 课程下导入学生。")
    if row.primary_level_name not in CSV_IMPORT_ALLOWED_CPP_LEVELS:
        raise StudentImportError("当前级别必须是 GESP1~8 / CSP-J / CSP-S。")

    level_code = infer_cpp_permission_code(row.primary_level_name)
    if not level_code:
        raise StudentImportError("当前级别无法映射到权限等级。")
    return level_code


def create_or_update_student_with_parent_and_assignment(
    *,
    teacher_user: PortalUser,
    course: Course,
    student_name: str,
    parent_phone: str,
    primary_track_name: str,
    primary_level_name: str,
    level_code: str,
    used_usernames: set[str] | None = None,
    default_password: str = DEFAULT_IMPORTED_ACCOUNT_PASSWORD,
    allow_existing_student: bool = True,
) -> StudentProvisionResult:
    normalized_student_name = str(student_name or "").strip()
    normalized_parent_phone = normalize_phone(parent_phone)
    normalized_track_name = str(primary_track_name or "").strip()
    normalized_level_name = normalize_student_import_level_name(primary_level_name)
    normalized_level_code = str(level_code or "").strip().upper()

    if not normalized_student_name:
        raise StudentImportError("学生姓名不能为空。")
    if not normalized_parent_phone:
        raise StudentImportError("家长手机号不能为空。")
    if not normalized_track_name:
        raise StudentImportError("当前学习内容不能为空。")
    if not normalized_level_name:
        raise StudentImportError("当前级别不能为空。")
    if not normalized_level_code:
        raise StudentImportError("权限等级不能为空。")

    existing_student = find_existing_student_by_name_and_parent_phone(
        student_name=normalized_student_name,
        parent_phone=normalized_parent_phone,
    )
    if existing_student and not allow_existing_student:
        raise StudentImportError("该学生已经在数据库中，添加失败")

    username_pool = used_usernames if used_usernames is not None else set(PortalUser.objects.values_list("username", flat=True))
    parent_user, created_parent_user = _get_or_create_parent_user(
        student_name=normalized_student_name,
        parent_phone=normalized_parent_phone,
        default_password=default_password,
    )
    student_user, created_student_user = _get_or_create_student_user(
        existing_student=existing_student,
        student_name=normalized_student_name,
        parent_phone=normalized_parent_phone,
        used_usernames=username_pool,
        default_password=default_password,
    )
    student, created_student = _get_or_create_student_profile(
        existing_student=existing_student,
        student_user=student_user,
        parent_user=parent_user,
        teacher_user=teacher_user,
        course=course,
        student_name=normalized_student_name,
        primary_track_name=normalized_track_name,
        primary_level_name=normalized_level_name,
    )
    assignment, created_assignment, updated_assignment = _get_or_update_assignment(
        teacher_user=teacher_user,
        student=student,
        course=course,
        level_code=normalized_level_code,
    )
    return StudentProvisionResult(
        student=student,
        student_user=student_user,
        parent_user=parent_user,
        assignment=assignment,
        created_student=created_student,
        created_student_user=created_student_user,
        created_parent_user=created_parent_user,
        created_assignment=created_assignment,
        updated_assignment=updated_assignment,
    )


def _looks_like_header(row: list[str]) -> bool:
    normalized = [_normalize_header_cell(cell) for cell in row[:4]]
    if len(normalized) < 4:
        return False
    return (
        "学生姓名" in normalized[0]
        and ("家长手机号" in normalized[1] or "家长手机" in normalized[1] or "手机号" in normalized[1])
        and "当前学习内容" in normalized[2]
        and "当前级别" in normalized[3]
    )


def _parse_student_import_csv_rows(payload: bytes) -> list[list[str]]:
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValidationError("当前无法识别这个 CSV 文件的编码，请改用 UTF-8。")

    return [
        [str(cell or "").strip() for cell in row]
        for row in csv.reader(io.StringIO(text))
        if any(str(cell or "").strip() for cell in row)
    ]


def _normalize_header_cell(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .replace(" ", "")
        .replace("\ufeff", "")
        .replace("_", "")
    )


def _parse_student_import_xlsx_rows(payload: bytes) -> list[list[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            shared_strings = _load_xlsx_shared_strings(archive)
            worksheet_name = _get_first_xlsx_worksheet_name(archive)
            root = ElementTree.fromstring(archive.read(worksheet_name))
    except zipfile.BadZipFile as exc:
        raise ValidationError("当前 XLSX 文件损坏，无法解析。") from exc
    except KeyError as exc:
        raise ValidationError("当前 XLSX 文件缺少必要 worksheet，无法解析。") from exc
    except ElementTree.ParseError as exc:
        raise ValidationError("当前 XLSX 文件格式异常，无法解析。") from exc

    rows: list[list[str]] = []
    for row in root.findall(".//a:sheetData/a:row", XLSX_XML_NS):
        cell_values: dict[int, str] = {}
        for position, cell in enumerate(row.findall("a:c", XLSX_XML_NS), start=1):
            reference = cell.attrib.get("r", "")
            column_index = _xlsx_column_index(reference) if reference else position
            cell_text = _extract_xlsx_cell_text(cell, shared_strings)
            cell_values[column_index] = cell_text
        if cell_values and any(value for value in cell_values.values()):
            width = max(cell_values)
            rows.append([cell_values.get(index, "").strip() for index in range(1, width + 1)])
    return rows


def _load_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml_bytes = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(xml_bytes)
    strings: list[str] = []
    for item in root.iter():
        if item.tag.endswith("}si"):
            strings.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")).strip())
    return strings


def _get_first_xlsx_worksheet_name(archive: zipfile.ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationship_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in relationships}
    sheets = workbook.find("a:sheets", XLSX_XML_NS)
    if sheets is None or not list(sheets):
        raise ValidationError("XLSX 文件中没有可读取的工作表。")
    first_sheet = list(sheets)[0]
    relation_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    if not relation_id or relation_id not in relationship_map:
        raise ValidationError("XLSX 文件中缺少 worksheet 关系信息。")
    target = relationship_map[relation_id]
    if not target.startswith("xl/"):
        target = f"xl/{target.lstrip('/')}"
    return target


def _extract_xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t")).strip()

    value = ""
    for node in cell.iter():
        if node.tag.endswith("}v") and node.text:
            value = node.text.strip()
            break
    if not value:
        return ""
    if cell_type == "s":
        try:
            return str(shared_strings[int(value)]).strip()
        except (ValueError, IndexError):
            return ""
    return value


def _xlsx_column_index(cell_reference: str) -> int:
    value = 0
    for char in str(cell_reference or ""):
        if char.isalpha():
            value = value * 26 + ord(char.upper()) - 64
    return value


def _generate_student_username(*, parent_phone: str, used_usernames: set[str]) -> str:
    prefix = f"student_{parent_phone}_"
    for _ in range(10000):
        candidate = f"{prefix}{secrets.randbelow(10000):04d}"
        if candidate in used_usernames:
            continue
        if PortalUser.objects.filter(username=candidate).exists():
            used_usernames.add(candidate)
            continue
        used_usernames.add(candidate)
        return candidate
    raise StudentImportError("学生账号生成失败，请重试。")


def _get_or_create_parent_user(
    *,
    student_name: str,
    parent_phone: str,
    default_password: str,
) -> tuple[PortalUser, bool]:
    parent_username = f"parent_{parent_phone}"
    existing_parent_user = PortalUser.objects.filter(
        role=PortalUser.ROLE_PARENT,
        phone=parent_phone,
    ).order_by("id").first()
    if existing_parent_user is None:
        username_owner = PortalUser.objects.filter(username=parent_username).order_by("id").first()
        if username_owner and username_owner.role != PortalUser.ROLE_PARENT:
            raise StudentImportError("家长默认账号已被其他角色占用，当前无法导入该学生。")
        if username_owner and username_owner.phone and username_owner.phone != parent_phone:
            raise StudentImportError("家长默认账号与当前手机号不一致，当前无法导入该学生。")
        if username_owner:
            existing_parent_user = username_owner

    if existing_parent_user is None:
        parent_user = PortalUser(
            username=parent_username,
            role=PortalUser.ROLE_PARENT,
            full_name=f"{student_name}家长",
            phone=parent_phone,
            is_active=True,
        )
        parent_user.set_password(default_password)
        parent_user.save()
        return parent_user, True

    changed_fields: list[str] = []
    if existing_parent_user.role != PortalUser.ROLE_PARENT:
        raise StudentImportError("家长默认账号已被其他角色占用，当前无法导入该学生。")
    if not existing_parent_user.full_name:
        existing_parent_user.full_name = f"{student_name}家长"
        changed_fields.append("full_name")
    if parent_phone and existing_parent_user.phone != parent_phone:
        existing_parent_user.phone = parent_phone
        changed_fields.append("phone")
    if not existing_parent_user.is_active:
        existing_parent_user.is_active = True
        changed_fields.append("is_active")
    if changed_fields:
        existing_parent_user.save(update_fields=changed_fields + ["updated_at"])
    return existing_parent_user, False


def _get_or_create_student_user(
    *,
    existing_student: Student | None,
    student_name: str,
    parent_phone: str,
    used_usernames: set[str],
    default_password: str,
) -> tuple[PortalUser, bool]:
    if existing_student is not None:
        student_user = existing_student.user
        if student_user.role != PortalUser.ROLE_STUDENT:
            raise StudentImportError("已有关联学生账号角色异常，当前无法安全导入。")

        changed_fields: list[str] = []
        if student_user.full_name != student_name:
            student_user.full_name = student_name
            changed_fields.append("full_name")
        if not student_user.is_active:
            student_user.is_active = True
            changed_fields.append("is_active")
        if changed_fields:
            student_user.save(update_fields=changed_fields + ["updated_at"])
        used_usernames.add(student_user.username)
        return student_user, False

    student_user = PortalUser(
        username=_generate_student_username(parent_phone=parent_phone, used_usernames=used_usernames),
        role=PortalUser.ROLE_STUDENT,
        full_name=student_name,
        phone="",
        is_active=True,
    )
    student_user.set_password(default_password)
    student_user.save()
    return student_user, True


def _get_or_create_student_profile(
    *,
    existing_student: Student | None,
    student_user: PortalUser,
    parent_user: PortalUser,
    teacher_user: PortalUser,
    course: Course,
    student_name: str,
    primary_track_name: str,
    primary_level_name: str,
) -> tuple[Student, bool]:
    if existing_student is None:
        return (
            Student.objects.create(
                user=student_user,
                parent_user=parent_user,
                teacher_user=teacher_user,
                display_name=student_name,
                grade="",
                campus="",
                primary_course_name=course.title,
                primary_track_name=primary_track_name,
                primary_level_name=primary_level_name,
            ),
            True,
        )

    changed_fields: list[str] = []
    if existing_student.user_id != student_user.id:
        existing_student.user = student_user
        changed_fields.append("user")
    if existing_student.parent_user_id != parent_user.id:
        existing_student.parent_user = parent_user
        changed_fields.append("parent_user")
    if existing_student.teacher_user_id != teacher_user.id:
        existing_student.teacher_user = teacher_user
        changed_fields.append("teacher_user")
    if existing_student.display_name != student_name:
        existing_student.display_name = student_name
        changed_fields.append("display_name")
    if existing_student.primary_course_name != course.title:
        existing_student.primary_course_name = course.title
        changed_fields.append("primary_course_name")
    if existing_student.primary_track_name != primary_track_name:
        existing_student.primary_track_name = primary_track_name
        changed_fields.append("primary_track_name")
    if existing_student.primary_level_name != primary_level_name:
        existing_student.primary_level_name = primary_level_name
        changed_fields.append("primary_level_name")
    if changed_fields:
        existing_student.save(update_fields=changed_fields)
    return existing_student, False


def _get_or_update_assignment(
    *,
    teacher_user: PortalUser,
    student: Student,
    course: Course,
    level_code: str,
) -> tuple[TeacherStudentAssignment, bool, bool]:
    existing_assignments = list(
        TeacherStudentAssignment.objects.filter(
            teacher=teacher_user,
            student=student,
            course=course,
        ).order_by("id")
    )
    assignment = next((item for item in existing_assignments if item.level_code == level_code), None)
    created = False
    updated = False

    if assignment is not None:
        changed_fields: list[str] = []
        if not assignment.is_active:
            assignment.is_active = True
            changed_fields.append("is_active")
        if changed_fields:
            assignment.save(update_fields=changed_fields + ["updated_at"])
            updated = True
    elif existing_assignments:
        assignment = existing_assignments[0]
        changed_fields = []
        if assignment.level_code != level_code:
            assignment.level_code = level_code
            changed_fields.append("level_code")
        if not assignment.is_active:
            assignment.is_active = True
            changed_fields.append("is_active")
        if changed_fields:
            assignment.save(update_fields=changed_fields + ["updated_at"])
            updated = True
    else:
        assignment = TeacherStudentAssignment.objects.create(
            teacher=teacher_user,
            student=student,
            course=course,
            level_code=level_code,
            is_active=True,
        )
        created = True

    duplicate_active_ids = [item.id for item in existing_assignments if item.id != assignment.id and item.is_active]
    if duplicate_active_ids:
        TeacherStudentAssignment.objects.filter(id__in=duplicate_active_ids).update(
            is_active=False,
            updated_at=timezone.now(),
        )
        updated = True

    return assignment, created, updated
