from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import random
import re
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils.html import escape
from django.utils import timezone
from django.utils.safestring import mark_safe

from .content_visibility import (
    CONTENT_PERMISSION_ORDER,
    infer_content_permission_code,
    get_visible_cpp_stage_codes,
    get_visible_permission_codes,
    permission_code_allows,
    pick_highest_permission_code,
)
from .course_identity import resolve_course_slug, summarize_course_level_labels
from .homework_completion_stats import (
    build_homework_completion_stats,
    get_homework_due_localdate,
    get_homework_submission_effective_submitted_at,
    normalize_homework_completion_period_type,
    resolve_homework_completion_period_datetimes,
    resolve_homework_completion_period_dates,
    resolve_homework_due_datetime_range,
)
from .homework_batch import (
    build_homework_import_job_source_metadata,
    build_homework_import_job_question_payloads,
    get_visible_homework_import_jobs,
)
from .html_sanitizer import sanitize_rich_html
from .homework_online import (
    decode_sql_ascii_json_text,
    normalize_candidate_editor_rows,
)
from .homework_option_formatting import format_homework_option_display
from .homework_question_rendering import (
    QUESTION_STATE_ANSWERING,
    QUESTION_STATE_PRINT_BLANK,
    build_homework_question_view_models,
    get_question_render_state,
)
from .gesp2_catalog import (
    ASCII_CHAR_ENCODING_CONTENT_SLUG,
    ENUMERATION_METHOD_CONTENT_SLUG,
    GESP2_KNOWLEDGE_DEFINITIONS,
    GESP2_KNOWLEDGE_MAP,
    GESP2_KNOWLEDGE_SLUGS,
    GESP2_PHASE,
)
from .gesp4_catalog import (
    BINARY_SEARCH_CONTENT_SLUG,
    ARRAY_2D_CONTENT_SLUG,
    GESP4_PHASE,
    GESP4_TOPIC_DEFINITIONS,
    GESP4_TOPIC_MAP,
    GESP4_TOPIC_SLUGS,
    SORTING_CONTENT_SLUG,
    STRINGS_CONTENT_SLUG,
)
from .models import (
    Course,
    CourseCategory,
    CourseContent,
    CourseLevel,
    ExamPaper,
    ExamProctorEvent,
    ExamQuestion,
    ExamQuestionAnalysisBlock,
    ExamQuestionAnalysisSuggestion,
    ExamQuestionBankItem,
    ExamQuestionBankPaper,
    ExamQuestionBankQuestion,
    ExamKnowledgePointMap,
    ExamSession,
    ExamSubmissionAnswer,
    HomeworkAssignment,
    HomeworkImportJob,
    HomeworkQuestion,
    HomeworkSummary,
    HomeworkSubmission,
    HomeworkSubmissionAnswer,
    LessonHourLedger,
    PortalUser,
    RewardRecord,
    Student,
    StudentContentAccess,
    StudentSiteMessage,
    Teacher,
    TeacherEvaluation,
    TeacherStudentAssignment,
)
from .shell_content import ROLE_SHELL_CONTENT
from .student_import import teacher_can_import_students
from .exam_online import (
    EXAM_PRACTICE_SESSION_TYPES,
    FINISHED_EXAM_SESSION_STATUSES,
    FREE_PRACTICE_MAX_QUESTION_COUNT,
    build_exam_entry_state,
    get_first_finished_exam_session_for_wrong_practice,
    get_exam_session_questions,
    get_exam_window_end,
    get_wrong_question_ids_from_first_exam_session,
    is_auto_gradable_exam_question,
)
from .student_learning_api import build_student_learning_overview
from .student_portal_content import STUDENT_PORTAL_CONTENT
from .teacher_course_catalog import TEACHER_COURSE_DEFINITIONS, TEACHER_COURSE_MAP


GRID_PAGE_SIZE_OPTIONS = [10, 15, 50, 100]
DEFAULT_GRID_PAGE_SIZE = 10
CPP_PORTAL_CATEGORY_GESP_SLUG = "gesp"
CPP_PORTAL_CATEGORY_CSP_SLUG = "csp"
CPP_PORTAL_CATEGORY_ROBOTICS_SLUG = "robotics"
HOMEWORK_STATUS_LABELS = {
    HomeworkAssignment.STATUS_ASSIGNED: "待完成",
    HomeworkAssignment.STATUS_COMPLETED: "已完成",
    HomeworkAssignment.STATUS_REVIEWED: "已评阅",
    HomeworkAssignment.STATUS_CANCELLED: "已取消",
}
HOMEWORK_STATUS_TONES = {
    HomeworkAssignment.STATUS_ASSIGNED: "trial",
    HomeworkAssignment.STATUS_COMPLETED: "open",
    HomeworkAssignment.STATUS_REVIEWED: "future",
    HomeworkAssignment.STATUS_CANCELLED: "locked",
}
HOMEWORK_IMPORT_STATUS_LABELS = {
    HomeworkImportJob.STATUS_UPLOADED: "已上传",
    HomeworkImportJob.STATUS_PARSING: "解析中",
    HomeworkImportJob.STATUS_PARSED: "待确认",
    HomeworkImportJob.STATUS_CONFIRMED: "已确认",
    HomeworkImportJob.STATUS_FAILED: "解析失败",
    HomeworkImportJob.STATUS_CANCELLED: "已取消",
}
HOMEWORK_IMPORT_STATUS_TONES = {
    HomeworkImportJob.STATUS_UPLOADED: "trial",
    HomeworkImportJob.STATUS_PARSING: "future",
    HomeworkImportJob.STATUS_PARSED: "open",
    HomeworkImportJob.STATUS_CONFIRMED: "future",
    HomeworkImportJob.STATUS_FAILED: "locked",
    HomeworkImportJob.STATUS_CANCELLED: "locked",
}
HOMEWORK_SUBMISSION_STATUS_LABELS = {
    HomeworkSubmission.STATUS_IN_PROGRESS: "作答中",
    HomeworkSubmission.STATUS_SUBMITTED: "已提交",
    HomeworkSubmission.STATUS_AUTO_CHECKED: "已自动判分",
    HomeworkSubmission.STATUS_REVIEWED: "已复核",
}
HOMEWORK_SUBMISSION_STATUS_TONES = {
    HomeworkSubmission.STATUS_IN_PROGRESS: "trial",
    HomeworkSubmission.STATUS_SUBMITTED: "open",
    HomeworkSubmission.STATUS_AUTO_CHECKED: "future",
    HomeworkSubmission.STATUS_REVIEWED: "future",
}
HOMEWORK_COMPLETION_STATUSES = {
    HomeworkSubmission.STATUS_SUBMITTED,
    HomeworkSubmission.STATUS_AUTO_CHECKED,
    HomeworkSubmission.STATUS_REVIEWED,
}
EXAM_SESSION_STATUS_LABELS = {
    ExamSession.STATUS_ASSIGNED: "待开始",
    ExamSession.STATUS_IN_PROGRESS: "考试中",
    ExamSession.STATUS_SUBMITTED: "已交卷",
    ExamSession.STATUS_AUTO_CHECKED: "已判分",
    ExamSession.STATUS_EXPIRED: "已过期",
    ExamSession.STATUS_INVALIDATED: "已作废",
}
EXAM_SESSION_STATUS_TONES = {
    ExamSession.STATUS_ASSIGNED: "trial",
    ExamSession.STATUS_IN_PROGRESS: "open",
    ExamSession.STATUS_SUBMITTED: "future",
    ExamSession.STATUS_AUTO_CHECKED: "future",
    ExamSession.STATUS_EXPIRED: "locked",
    ExamSession.STATUS_INVALIDATED: "locked",
}
EXAM_MODE_LABELS = {
    ExamPaper.MODE_TIMED: "定时模式",
    ExamPaper.MODE_DEADLINE: "DL模式",
}
EXAM_SESSION_TYPE_LABELS = {
    ExamSession.SESSION_TYPE_EXAM: "正式考试",
    ExamSession.SESSION_TYPE_FULL_PRACTICE: "整卷练习",
    ExamSession.SESSION_TYPE_WRONG_PRACTICE: "错题练习",
    ExamSession.SESSION_TYPE_FREE_PRACTICE: "自由练习",
}
TEACHER_EXAM_PRACTICE_SESSION_TYPE_LABELS = {
    ExamSession.SESSION_TYPE_FULL_PRACTICE: "全卷练习",
    ExamSession.SESSION_TYPE_WRONG_PRACTICE: "只练错题",
    ExamSession.SESSION_TYPE_FREE_PRACTICE: "自由练习",
}
TEACHER_HOMEWORK_ALL_LEVEL_FILTER_VALUE = "all"
TEACHER_HOMEWORK_UNGROUPED_LEVEL_FILTER_VALUE = "__ungrouped__"
TEACHER_HOMEWORK_UNGROUPED_LEVEL_LABEL = "未分组"
CPP_HOMEWORK_LEVEL_ORDER = [
    ("C1", "GESP1"),
    ("C2", "GESP2"),
    ("C3", "GESP3"),
    ("C4", "GESP4"),
]


def get_homework_summary_title(summary: HomeworkSummary | None, assignment: HomeworkAssignment | dict) -> str:
    assignment_title = (
        str(assignment.get("title") or "").strip()
        if isinstance(assignment, dict)
        else str(getattr(assignment, "title", "") or "").strip()
    )
    raw_title = str(getattr(summary, "title", "") or "").strip() if summary else ""
    return raw_title or f"{assignment_title} · 本周总结"


def get_homework_summary_rendered_html(summary: HomeworkSummary | None) -> str:
    raw_html = getattr(summary, "summary_html", "") if summary else ""
    sanitized = sanitize_rich_html(raw_html)
    if sanitized:
        return mark_safe(sanitized)
    return mark_safe("<p>当前还没有填入本周总结内容。</p>")


def get_week_date_range(anchor_date: date) -> tuple[date, date]:
    start_date = anchor_date - timedelta(days=anchor_date.weekday())
    end_date = start_date + timedelta(days=6)
    return start_date, end_date


def get_assignment_assigned_localdate(assignment: HomeworkAssignment) -> date:
    assigned_at = getattr(assignment, "assigned_at", None)
    if not assigned_at:
        return timezone.localdate()
    return timezone.localtime(assigned_at).date()


def resolve_homework_summary_default_range(assignments: list[HomeworkAssignment]) -> tuple[date, date]:
    if assignments:
        latest_assignment = max(assignments, key=get_assignment_assigned_localdate)
        return get_week_date_range(get_assignment_assigned_localdate(latest_assignment))
    return get_week_date_range(timezone.localdate())


def filter_homework_assignments_by_assigned_date(
    assignments: list[HomeworkAssignment],
    *,
    start_date: date,
    end_date: date,
) -> list[HomeworkAssignment]:
    return [
        assignment
        for assignment in assignments
        if start_date <= get_assignment_assigned_localdate(assignment) <= end_date
    ]


def build_default_homework_summary_title(student: Student, *, start_date: date, end_date: date) -> str:
    return f"{student.display_name} · {start_date.isoformat()} 至 {end_date.isoformat()} 本周总结"


def build_default_batch_homework_summary_title(*, course_label: str, anchor_date: date) -> str:
    normalized_course_label = str(course_label or "").strip() or "批量作业"
    return f"{normalized_course_label} · {anchor_date.isoformat()} 课后总结"


UAV_HOMEWORK_LEVEL_ORDER = [
    ("S1",),
    ("S2",),
    ("S3",),
]


def build_cascading_level_code_map(level_groups: list[tuple[str, ...]]) -> dict[str, set[str]]:
    cascading_map: dict[str, set[str]] = {}
    for index, group in enumerate(level_groups):
        visible_codes = {
            code
            for visible_group in level_groups[: index + 1]
            for code in visible_group
        }
        for code in group:
            cascading_map[code] = set(visible_codes)
    return cascading_map


CPP_HOMEWORK_LEVEL_CODE_MAP = build_cascading_level_code_map(CPP_HOMEWORK_LEVEL_ORDER)
UAV_HOMEWORK_LEVEL_CODE_MAP = build_cascading_level_code_map(UAV_HOMEWORK_LEVEL_ORDER)
STUDENT_PORTAL_EXCEPTION_NAMES = {"林一诺"}


def normalize_positive_value(value, *, default: int = 0, minimum: int = 0) -> int:
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return normalized if normalized >= minimum else default


def format_datetime(value) -> str:
    if not value:
        return "暂无记录"
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")


def shorten_text(text: str, *, limit: int = 48) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def normalize_grid_page_size(value, *, default: int = DEFAULT_GRID_PAGE_SIZE) -> int:
    try:
        page_size = int(value)
    except (TypeError, ValueError):
        return default
    return page_size if page_size in GRID_PAGE_SIZE_OPTIONS else default


def normalize_grid_page(value) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return max(page, 1)


def build_datagrid_context(
    rows: list[dict],
    *,
    path: str,
    page: int,
    page_size: int,
    query_params: dict[str, str | int],
) -> dict:
    paginator = Paginator(rows, page_size)
    page_obj = paginator.get_page(page)
    current_page = page_obj.number
    total_pages = paginator.num_pages or 1

    def build_href(*, page_number: int | None = None, next_page_size: int | None = None) -> str:
        params = {
            key: value
            for key, value in query_params.items()
            if value not in ("", None)
        }
        params["page"] = page_number if page_number is not None else current_page
        params["page_size"] = next_page_size if next_page_size is not None else page_size
        encoded = urlencode(params)
        return f"{path}?{encoded}" if encoded else path

    page_numbers = range(max(1, current_page - 2), min(total_pages, current_page + 2) + 1)
    return {
        "rows": list(page_obj.object_list),
        "pagination": {
            "page": current_page,
            "page_size": page_size,
            "total_count": paginator.count,
            "total_pages": total_pages,
            "start_index": (current_page - 1) * page_size + 1 if paginator.count else 0,
            "end_index": min(current_page * page_size, paginator.count) if paginator.count else 0,
            "has_previous": page_obj.has_previous(),
            "has_next": page_obj.has_next(),
            "previous_href": build_href(page_number=page_obj.previous_page_number()) if page_obj.has_previous() else "",
            "next_href": build_href(page_number=page_obj.next_page_number()) if page_obj.has_next() else "",
            "page_links": [
                {
                    "number": number,
                    "href": build_href(page_number=number),
                    "is_current": number == current_page,
                }
                for number in page_numbers
            ],
            "page_size_options": [
                {
                    "value": option,
                    "label": str(option),
                    "selected": option == page_size,
                }
                for option in GRID_PAGE_SIZE_OPTIONS
            ],
        },
    }


def get_student_learning_parts(student: Student) -> list[str]:
    return [
        value.strip()
        for value in [
            student.primary_course_name,
            student.primary_track_name,
            student.primary_level_name,
        ]
        if value and value.strip()
    ]


def build_student_learning_path(student: Student) -> str:
    parts = get_student_learning_parts(student)
    return " > ".join(parts) if parts else "课程待分配"


def get_student_primary_course(student: Student) -> str:
    return (student.primary_course_name or "").strip()


def is_student_portal_exception(student: Student) -> bool:
    candidate_names = {
        (student.display_name or "").strip(),
        (student.user.full_name or "").strip() if student.user_id else "",
    }
    return bool(STUDENT_PORTAL_EXCEPTION_NAMES.intersection(candidate_names))


def get_student_by_user(portal_user: PortalUser) -> Student:
    return Student.objects.select_related("user", "parent_user", "teacher_user").get(user=portal_user)


def get_parent_student(portal_user: PortalUser) -> Student | None:
    return (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(parent_user=portal_user)
        .order_by("id")
        .first()
    )


def get_teacher_students(portal_user: PortalUser) -> QuerySet[Student]:
    return (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(teacher_assignments__teacher=portal_user, teacher_assignments__is_active=True)
        .distinct()
        .order_by("id")
    )


def get_teacher_active_assignments(portal_user: PortalUser) -> QuerySet[TeacherStudentAssignment]:
    queryset = (
        TeacherStudentAssignment.objects.select_related(
            "teacher",
            "course",
            "student",
            "student__user",
            "student__parent_user",
            "student__teacher_user",
        )
        .filter(is_active=True)
        .order_by("course_id", "level_code", "student_id", "id")
    )
    if portal_user.role == PortalUser.ROLE_PRINCIPAL:
        return queryset
    return queryset.filter(teacher=portal_user)


def sync_teacher_profile(portal_user: PortalUser) -> Teacher | None:
    if portal_user.role != PortalUser.ROLE_TEACHER:
        return None

    teacher, _ = Teacher.objects.get_or_create(
        user=portal_user,
        defaults={
            "display_name": portal_user.full_name,
            "phone": portal_user.phone,
        },
    )
    update_fields = []
    if teacher.display_name != portal_user.full_name:
        teacher.display_name = portal_user.full_name
        update_fields.append("display_name")
    if teacher.phone != portal_user.phone:
        teacher.phone = portal_user.phone
        update_fields.append("phone")
    if update_fields:
        teacher.save(update_fields=update_fields)

    assignment_courses = Course.objects.filter(
        teacher_student_assignments__teacher=portal_user,
        teacher_student_assignments__is_active=True,
    ).distinct()
    if assignment_courses.exists():
        teacher.courses.add(*assignment_courses)
    if not teacher.subject:
        course_titles = list(teacher.courses.values_list("title", flat=True))
        if course_titles:
            teacher.subject = "、".join(course_titles)
            teacher.save(update_fields=["subject"])
    return teacher


def get_teacher_profile_courses(portal_user: PortalUser) -> list[Course]:
    teacher = sync_teacher_profile(portal_user)
    if teacher is None:
        return []
    return list(teacher.courses.all().order_by("id"))


def summarize_teacher_assignment_scope(assignments: list[TeacherStudentAssignment]) -> str:
    return summarize_course_level_labels(
        [(assignment.course.title, assignment.level_code) for assignment in assignments]
    )


def format_assignment_levels(assignments: list[TeacherStudentAssignment], *, empty: str = "未分级") -> str:
    level_codes = sorted({assignment.level_code for assignment in assignments if assignment.level_code})
    return ", ".join(level_codes) if level_codes else empty


def build_default_teacher_course_definition(course: Course) -> dict:
    return {
        "slug": course.slug,
        "level": "未分级",
        "title": course.title,
        "summary": "当前已纳入教师负责课程范围。",
        "state": "active",
        "detail_title": f"{course.title} 课程分类页",
        "detail_description": "当前先承接数据库中真实存在的课程入口，分类、级别和知识点都以数据库为准。",
        "category_items": [],
    }


def get_student_content_access(student: Student, content: CourseContent) -> StudentContentAccess | None:
    return (
        StudentContentAccess.objects.select_related("content", "granted_by")
        .filter(student=student, content=content)
        .first()
    )


def get_content_permission_code(content: CourseContent) -> str:
    level_code = content.level.code if content.level_id and content.level else ""
    return infer_content_permission_code(
        content.course.slug,
        permission_code=content.permission_code,
        level_code=level_code,
        phase=content.phase,
    )


def get_student_course_level_map(
    student: Student,
    *,
    course_ids: set[int] | None = None,
) -> dict[int, str]:
    queryset = TeacherStudentAssignment.objects.filter(student=student, is_active=True)
    if course_ids:
        queryset = queryset.filter(course_id__in=course_ids)

    level_map: dict[int, str] = {}
    for course_id, level_code in queryset.values_list("course_id", "level_code"):
        level_map[course_id] = pick_highest_permission_code((level_map.get(course_id), level_code))
    return level_map


def get_student_course_level_code(student: Student, *, course_slug: str) -> str:
    course_id = Course.objects.filter(slug=course_slug).values_list("id", flat=True).first()
    if course_id is None:
        return ""
    return get_student_course_level_map(student, course_ids={course_id}).get(course_id, "")


def should_show_cpp_portal_category(category_slug: str, level_code: str) -> bool:
    normalized_category_slug = (category_slug or "").strip().lower()
    if normalized_category_slug == CPP_PORTAL_CATEGORY_GESP_SLUG:
        return True
    if normalized_category_slug == CPP_PORTAL_CATEGORY_CSP_SLUG:
        return permission_code_allows(level_code, "C3")
    if normalized_category_slug == CPP_PORTAL_CATEGORY_ROBOTICS_SLUG:
        return False
    return True


def build_portal_state_summary_cards(portal_cards: list[dict]) -> list[dict[str, str]]:
    state_titles = {
        state: [card["title"] for card in portal_cards if card.get("state") == state]
        for state in ("open", "trial", "locked")
    }
    return [
        {"label": "已开放", "value": " / ".join(state_titles["open"]) if state_titles["open"] else "无"},
        {"label": "体验中", "value": " / ".join(state_titles["trial"]) if state_titles["trial"] else "无"},
        {"label": "未开放", "value": " / ".join(state_titles["locked"]) if state_titles["locked"] else "无"},
    ]


def get_student_content_visibility(student: Student, content: CourseContent) -> dict[str, object]:
    access = get_student_content_access(student, content)
    course_level_code = get_student_course_level_map(student, course_ids={content.course_id}).get(content.course_id, "")
    permission_code = get_content_permission_code(content)
    default_visible = permission_code_allows(course_level_code, permission_code)
    override_mode = "default"
    if access is not None:
        override_mode = "allow" if access.is_open else "deny"
    is_visible = access.is_open if access is not None else default_visible
    return {
        "access": access,
        "course_level_code": course_level_code,
        "permission_code": permission_code,
        "default_visible": default_visible,
        "is_visible": is_visible,
        "override_mode": override_mode,
    }


def set_student_content_visibility(
    student: Student,
    content: CourseContent,
    *,
    is_visible: bool,
    granted_by: PortalUser | None,
) -> StudentContentAccess | None:
    visibility = get_student_content_visibility(student, content)
    access = visibility["access"]
    default_visible = bool(visibility["default_visible"])
    if is_visible == default_visible:
        if access and access.pk:
            access.delete()
        return None
    if access is None:
        access = StudentContentAccess(student=student, content=content)
    elif access.is_open == is_visible:
        return access
    access.set_open_state(is_open=is_visible, granted_by=granted_by)
    access.save()
    return access


def get_teacher_course_scope(portal_user: PortalUser, course_slug: str) -> dict:
    course_assignments = list(get_teacher_active_assignments(portal_user).filter(course__slug=course_slug))
    if not course_assignments:
        profile_course_ids = {course.id for course in get_teacher_profile_courses(portal_user)}
        course = Course.objects.filter(slug=course_slug).order_by("id").first()
        if course is None:
            raise Course.DoesNotExist(course_slug)
        if (
            portal_user.role == PortalUser.ROLE_PRINCIPAL
            or course.id in profile_course_ids
        ):
            return {
                "course": course,
                "course_assignments": [],
                "assignments_by_student": defaultdict(list),
                "related_students": [],
                "student_ids": [],
                "student_map": {},
            }
        raise Course.DoesNotExist(course_slug)

    assignments_by_student: dict[int, list[TeacherStudentAssignment]] = defaultdict(list)
    for assignment in course_assignments:
        assignments_by_student[assignment.student_id].append(assignment)

    related_students = [assignments_by_student[student_id][0].student for student_id in sorted(assignments_by_student)]
    return {
        "course": course_assignments[0].course,
        "course_assignments": course_assignments,
        "assignments_by_student": assignments_by_student,
        "related_students": related_students,
        "student_ids": list(assignments_by_student),
        "student_map": {student.id: student for student in related_students},
    }


def get_teacher_assignable_scope_options(
    portal_user: PortalUser,
    *,
    include_assignment: TeacherStudentAssignment | None = None,
) -> list[dict]:
    option_map: dict[tuple[int, str], dict] = {}
    for assignment in get_teacher_active_assignments(portal_user):
        scope_key = (assignment.course_id, assignment.level_code)
        if scope_key in option_map:
            continue
        option_map[scope_key] = {
            "scope_key": f"{assignment.course_id}:{assignment.level_code}",
            "course_id": assignment.course_id,
            "course_slug": assignment.course.slug,
            "course_title": assignment.course.title,
            "level_code": assignment.level_code,
            "label": f"{assignment.course.title} / {assignment.level_code}",
        }

    if include_assignment:
        scope_key = (include_assignment.course_id, include_assignment.level_code)
        if scope_key not in option_map:
            option_map[scope_key] = {
                "scope_key": f"{include_assignment.course_id}:{include_assignment.level_code}",
                "course_id": include_assignment.course_id,
                "course_slug": include_assignment.course.slug,
                "course_title": include_assignment.course.title,
                "level_code": include_assignment.level_code,
                "label": f"{include_assignment.course.title} / {include_assignment.level_code}",
            }

    return sorted(
        option_map.values(),
        key=lambda item: (item["course_title"], item["level_code"], item["course_id"]),
    )


def build_teacher_assignment_student_options(portal_user: PortalUser) -> list[dict]:
    active_assignments = list(get_teacher_active_assignments(portal_user))
    assignments_by_student: dict[int, list[TeacherStudentAssignment]] = defaultdict(list)
    for assignment in active_assignments:
        assignments_by_student[assignment.student_id].append(assignment)

    student_options = []
    for student in (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .order_by("display_name", "id")
    ):
        current_assignments = assignments_by_student.get(student.id, [])
        current_scope_text = (
            summarize_teacher_assignment_scope(current_assignments)
            if current_assignments
            else "当前不在我名下"
        )
        student_options.append(
            {
                "id": student.id,
                "label": student.display_name,
                "grade": student.grade or "待补充",
                "parent_phone": student.parent_user.phone if student.parent_user and student.parent_user.phone else "未录入",
                "description": f"{student.grade or '年级待补充'} · {current_scope_text}",
                "current_scope_text": current_scope_text,
                "is_current_student": bool(current_assignments),
            }
        )

    return student_options


def get_teacher_course_categories(course: Course) -> QuerySet[CourseCategory]:
    return course.categories.filter(is_active=True).order_by("sort_order", "id")


def get_teacher_course_levels(category: CourseCategory) -> QuerySet[CourseLevel]:
    return category.levels.filter(is_active=True).order_by("sort_order", "id")


def get_teacher_level_contents(level: CourseLevel, *, search_query: str = "") -> QuerySet[CourseContent]:
    queryset = (
        CourseContent.objects.select_related("course", "level", "level__category")
        .filter(level=level, is_active=True)
        .order_by("sort_order", "id")
    )
    if search_query:
        queryset = queryset.filter(title__icontains=search_query)
    return queryset


def build_teacher_student_assignment_list_context(portal_user: PortalUser, student_id: int) -> dict:
    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id)
        .get()
    )
    assignment_queryset = (
        TeacherStudentAssignment.objects.select_related("course", "student", "teacher")
        .filter(teacher=portal_user, student=student)
        .order_by("-is_active", "course_id", "level_code", "id")
    )
    assignment_rows = []
    active_rows = 0
    inactive_rows = 0
    for assignment in assignment_queryset:
        if assignment.is_active:
            active_rows += 1
        else:
            inactive_rows += 1
        assignment_rows.append(
            {
                "id": assignment.id,
                "course_title": assignment.course.title,
                "course_slug": assignment.course.slug,
                "level_code": assignment.level_code,
                "status_text": "生效中" if assignment.is_active else "已移除",
                "assigned_at_text": format_datetime(assignment.assigned_at),
                "updated_at_text": format_datetime(assignment.updated_at),
                "can_edit": assignment.is_active,
                "edit_href": reverse(
                    "teacher-student-assignment-edit",
                    args=[student.id, assignment.id],
                )
                if assignment.is_active
                else "",
                "remove_href": reverse(
                    "teacher-student-assignment-remove",
                    args=[student.id, assignment.id],
                )
                if assignment.is_active
                else "",
            }
        )

    active_assignments = [row for row in assignment_rows if row["can_edit"]]
    current_scope_text = (
        summarize_course_level_labels(
            [(row["course_title"], row["level_code"]) for row in active_assignments]
        )
        if active_assignments
        else "当前不在我名下"
    )
    has_active_assignment = bool(active_assignments)

    return {
        "student": student,
        "page_title": f"{student.display_name} · 学生关系管理",
        "page_description": "这里只管理当前老师与该学生之间的 assignment，不改学生主档，也不跨老师操作。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "学生关系管理"},
            {"label": student.display_name},
        ],
        "summary_cards": [
            {"label": "当前生效", "value": f"{active_rows} 条", "hint": "当前老师对该学生仍在生效的 assignment"},
            {"label": "已移除", "value": f"{inactive_rows} 条", "hint": "历史上存在但已停用的 assignment"},
            {"label": "当前范围", "value": current_scope_text, "hint": "按当前老师视角汇总"},
            {"label": "学生主学习线", "value": build_student_learning_path(student), "hint": student.grade or "年级待补充"},
        ],
        "student_identity_items": [
            {"label": "学生姓名", "value": student.display_name},
            {"label": "年级", "value": student.grade or "待补充"},
            {"label": "校区", "value": student.campus or "待补充"},
            {"label": "家长手机", "value": student.parent_user.phone if student.parent_user and student.parent_user.phone else "待补充"},
        ],
        "assignment_rows": assignment_rows,
        "assignment_table_rows": assignment_rows,
        "new_assignment_href": f"{reverse('teacher-assignment-new')}?student_id={student.id}",
        "student_detail_href": reverse("teacher-student-detail", args=[student.id]) if has_active_assignment else "",
        "back_href": reverse("teacher-students") + "?tab=students",
        "support_items": [
            {"title": "当前边界", "description": "这里只允许当前老师维护自己的 assignment，不允许改到别的老师名下。"},
            {"title": "移除方式", "description": "移除不会删除学生主档，只会把 assignment 改成 is_active=false。"},
            {"title": "恢复方式", "description": "如果同一学生、课程、级别曾被移除，后续新增时会恢复旧 assignment，而不是插重复记录。"},
        ],
    }


def build_teacher_assignment_form_context(
    portal_user: PortalUser,
    *,
    action_label: str,
    submit_label: str,
    student_id: int | None = None,
    assignment: TeacherStudentAssignment | None = None,
    form_data: dict | None = None,
) -> dict:
    student_options = build_teacher_assignment_student_options(portal_user)
    student_map = {
        student.id: student
        for student in Student.objects.select_related("user", "parent_user", "teacher_user").all()
    }
    available_scopes = get_teacher_assignable_scope_options(portal_user, include_assignment=assignment)
    available_scope_keys = {option["scope_key"] for option in available_scopes}

    preset_student_id = assignment.student_id if assignment else student_id
    selected_student_id = (
        preset_student_id
        if preset_student_id is not None
        else normalize_positive_value(form_data.get("student_id") if form_data else None, default=0, minimum=1)
    )
    if not selected_student_id and student_options:
        selected_student_id = student_options[0]["id"]
    selected_student = student_map.get(selected_student_id)
    if preset_student_id and not selected_student:
        raise Student.DoesNotExist(preset_student_id)

    current_scope_key = f"{assignment.course_id}:{assignment.level_code}" if assignment else ""
    selected_scope_key = (
        str(form_data.get("scope_key")).strip()
        if form_data and form_data.get("scope_key") is not None
        else current_scope_key
    )
    if not selected_scope_key and available_scopes:
        selected_scope_key = available_scopes[0]["scope_key"]
    selected_scope = next((option for option in available_scopes if option["scope_key"] == selected_scope_key), None)

    teacher_assignments_by_student: dict[int, list[TeacherStudentAssignment]] = defaultdict(list)
    for teacher_assignment in get_teacher_active_assignments(portal_user):
        teacher_assignments_by_student[teacher_assignment.student_id].append(teacher_assignment)
    selected_student_scope_text = (
        summarize_teacher_assignment_scope(teacher_assignments_by_student[selected_student.id])
        if selected_student and teacher_assignments_by_student.get(selected_student.id)
        else "当前不在我名下"
    )
    lock_student = bool(assignment or student_id)
    student_selector_options = (
        [option for option in student_options if option["id"] == selected_student_id]
        if lock_student
        else student_options
    )
    student_selector_rows = [
        {
            "student_id": option["id"],
            "name": option["label"],
            "grade": option["grade"],
            "parent_phone": option["parent_phone"],
            "current_scope_text": option["current_scope_text"],
            "selected": option["id"] == selected_student_id,
        }
        for option in student_selector_options
    ]
    scope_selector_rows = [
        {
            "scope_key": option["scope_key"],
            "course_title": option["course_title"],
            "course_slug": option["course_slug"],
            "level_code": option["level_code"],
            "label": option["label"],
            "selected": option["scope_key"] == selected_scope_key,
        }
        for option in available_scopes
    ]

    back_href = (
        reverse("teacher-student-assignments", args=[selected_student.id])
        if selected_student
        else reverse("teacher-students") + "?tab=students"
    )

    return {
        "page_title": f"{action_label} · 学生关系",
        "page_description": "这一步只维护当前老师自己的 assignment。课程和级别范围来自当前老师已有的负责范围，不扩展全局课程结构。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "学生关系管理", "href": back_href if selected_student else reverse("teacher-students") + "?tab=students"},
            {"label": action_label},
        ],
        "summary_cards": [
            {"label": "可选学生", "value": f"{len(student_options)} 人", "hint": "从现有学生主档中选择，不复制学生记录"},
            {"label": "可选范围", "value": f"{len(available_scopes)} 项", "hint": "仅限当前老师已有课程 / 级别范围"},
            {"label": "当前学生状态", "value": selected_student_scope_text, "hint": "当前老师对该学生的现有范围"},
            {"label": "操作模式", "value": action_label, "hint": submit_label},
        ],
        "identity_items": [
            {"label": "当前老师", "value": portal_user.full_name},
            {"label": "当前学生", "value": selected_student.display_name if selected_student else "未选择"},
            {"label": "当前年级", "value": selected_student.grade if selected_student and selected_student.grade else "待补充"},
            {"label": "当前范围", "value": selected_scope["label"] if selected_scope else "未选择"},
        ],
        "student_options": student_options,
        "student_option_ids": {option["id"] for option in student_options},
        "selected_student": selected_student,
        "student_selector_rows": student_selector_rows,
        "available_scopes": available_scopes,
        "available_scope_keys": available_scope_keys,
        "selected_scope": selected_scope,
        "scope_selector_rows": scope_selector_rows,
        "assignment": assignment,
        "form_values": {
            "student_id": selected_student_id,
            "scope_key": selected_scope_key,
        },
        "lock_student": lock_student,
        "action_label": action_label,
        "submit_label": submit_label,
        "back_href": back_href,
        "support_items": [
            {"title": "新增规则", "description": "同一老师、同一学生、同一课程、同一级别若已有 inactive assignment，会直接恢复为 active。"},
            {"title": "修改规则", "description": "编辑时只允许切换当前老师已有的课程 / 级别范围，不允许改到别的老师名下。"},
            {"title": "学生主档", "description": "学生主档只保留一份，assignment 才是教师与学生的关系载体。"},
        ],
    }


def build_teacher_assignment_remove_context(
    portal_user: PortalUser,
    student_id: int,
    assignment_id: int,
) -> dict:
    assignment = (
        TeacherStudentAssignment.objects.select_related("student", "course", "teacher")
        .filter(id=assignment_id, teacher=portal_user, student_id=student_id, is_active=True)
        .get()
    )
    return {
        "assignment": assignment,
        "student": assignment.student,
        "page_title": f"{assignment.student.display_name} · 移除 assignment",
        "page_description": "确认后只会停用这条 assignment，不会删除学生主档，也不会影响其他老师的关系。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "学生关系管理", "href": reverse("teacher-student-assignments", args=[assignment.student_id])},
            {"label": "移除 assignment"},
        ],
        "summary_cards": [
            {"label": "学生", "value": assignment.student.display_name, "hint": assignment.student.grade or "年级待补充"},
            {"label": "课程方向", "value": assignment.course.title, "hint": assignment.course.slug},
            {"label": "级别", "value": assignment.level_code, "hint": "当前将被停用的 assignment"},
            {"label": "当前状态", "value": "生效中", "hint": "确认后会改成 is_active=false"},
        ],
        "identity_items": [
            {"label": "学生姓名", "value": assignment.student.display_name},
            {"label": "课程方向", "value": assignment.course.title},
            {"label": "级别", "value": assignment.level_code},
            {"label": "分配时间", "value": format_datetime(assignment.assigned_at)},
        ],
        "confirm_action": reverse("teacher-student-assignment-remove", args=[assignment.student_id, assignment.id]),
        "back_href": reverse("teacher-student-assignments", args=[assignment.student_id]),
    }


def build_teacher_course_structure_export_payload(
    portal_user: PortalUser,
    course_slug: str,
    *,
    category_slug: str | None = None,
    level_code: str | None = None,
) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]

    def serialize_content(content: CourseContent) -> dict:
        return {
            "title": content.title,
            "slug": content.slug,
            "summary": content.summary,
            "route_path": content.route_path,
            "has_real_content": content.has_real_content,
            "sort_order": content.sort_order,
            "phase": content.phase,
        }

    def serialize_level(level: CourseLevel) -> dict:
        knowledge_points = [serialize_content(content) for content in get_teacher_level_contents(level)]
        return {
            "code": level.code,
            "title": level.title,
            "summary": level.summary,
            "sort_order": level.sort_order,
            "knowledge_point_count": len(knowledge_points),
            "knowledge_points": knowledge_points,
        }

    def serialize_category(category: CourseCategory) -> dict:
        levels = [serialize_level(level) for level in get_teacher_course_levels(category)]
        return {
            "slug": category.slug,
            "title": category.title,
            "summary": category.summary,
            "sort_order": category.sort_order,
            "level_count": len(levels),
            "levels": levels,
        }

    payload = {
        "export_scope": "course",
        "generated_at": timezone.localtime().isoformat(),
        "teacher": {
            "id": portal_user.id,
            "username": portal_user.username,
            "full_name": portal_user.full_name,
        },
        "course": {
            "slug": course.slug,
            "title": course.title,
            "summary": course.summary,
            "visible_assignment_levels": sorted({assignment.level_code for assignment in scope["course_assignments"]}),
            "related_student_count": len(scope["related_students"]),
        },
    }

    if category_slug and level_code:
        category = get_teacher_course_category(course, category_slug)
        level = get_teacher_course_level(category, level_code)
        payload["export_scope"] = "level"
        payload["category"] = {
            "slug": category.slug,
            "title": category.title,
            "summary": category.summary,
        }
        payload["level"] = {
            "code": level.code,
            "title": level.title,
            "summary": level.summary,
            "sort_order": level.sort_order,
        }
        payload["knowledge_points"] = [serialize_content(content) for content in get_teacher_level_contents(level)]
        return payload

    if category_slug:
        category = get_teacher_course_category(course, category_slug)
        payload["export_scope"] = "category"
        payload["category"] = {
            "slug": category.slug,
            "title": category.title,
            "summary": category.summary,
            "sort_order": category.sort_order,
        }
        payload["levels"] = [serialize_level(level) for level in get_teacher_course_levels(category)]
        return payload

    payload["categories"] = [serialize_category(category) for category in get_teacher_course_categories(course)]
    return payload


def student_has_content_access(student: Student, content_slug: str) -> bool:
    try:
        content = CourseContent.objects.select_related("course", "level").get(slug=content_slug, is_active=True)
    except CourseContent.DoesNotExist:
        return False
    return bool(get_student_content_visibility(student, content)["is_visible"])


def get_gesp4_topic_contents() -> list[CourseContent]:
    content_map = {
        content.slug: content
        for content in CourseContent.objects.select_related("course", "level").filter(
            course__slug="cpp",
            phase=GESP4_PHASE,
            slug__in=GESP4_TOPIC_SLUGS,
            is_active=True,
        )
    }
    missing = [slug for slug in GESP4_TOPIC_SLUGS if slug not in content_map]
    if missing:
        raise CourseContent.DoesNotExist(f"Missing GESP4 contents: {', '.join(missing)}")
    return [content_map[slug] for slug in GESP4_TOPIC_SLUGS]


def get_gesp2_knowledge_contents() -> list[CourseContent]:
    content_map = {
        content.slug: content
        for content in CourseContent.objects.select_related("course", "level").filter(
            course__slug="cpp",
            phase=GESP2_PHASE,
            slug__in=GESP2_KNOWLEDGE_SLUGS,
            is_active=True,
        )
    }
    missing = [slug for slug in GESP2_KNOWLEDGE_SLUGS if slug not in content_map]
    if missing:
        raise CourseContent.DoesNotExist(f"Missing GESP2 contents: {', '.join(missing)}")
    return [content_map[slug] for slug in GESP2_KNOWLEDGE_SLUGS]


def get_gesp4_topic_content(topic_slug: str) -> CourseContent:
    if topic_slug not in GESP4_TOPIC_MAP:
        raise CourseContent.DoesNotExist(f"Unknown GESP4 topic: {topic_slug}")
    return CourseContent.objects.select_related("course", "level").get(
        course__slug="cpp",
        phase=GESP4_PHASE,
        slug=topic_slug,
        is_active=True,
    )


def get_gesp2_knowledge_content(topic_slug: str) -> CourseContent:
    if topic_slug not in GESP2_KNOWLEDGE_MAP:
        raise CourseContent.DoesNotExist(f"Unknown GESP2 knowledge point: {topic_slug}")
    return CourseContent.objects.select_related("course", "level").get(
        course__slug="cpp",
        phase=GESP2_PHASE,
        slug=topic_slug,
        is_active=True,
    )


def get_locked_topic_message(topic_slug: str) -> str:
    topic_definition = GESP4_TOPIC_MAP.get(topic_slug)
    if not topic_definition:
        return "当前专题未开放，教师开放后才能进入真实内容。"
    return f"{topic_definition['title']}当前未开放，教师开放后才能进入。"


def get_content_mode_text(topic_slug: str) -> str:
    topic_definition = GESP4_TOPIC_MAP[topic_slug]
    return "真实内容" if topic_definition["content_mode"] == "real" else "内容预留"


def get_gesp2_content_mode_text(topic_slug: str) -> str:
    topic_definition = GESP2_KNOWLEDGE_MAP[topic_slug]
    return "真实内容" if topic_definition["content_mode"] == "real" else "内容预留"


def build_phase_label(open_count: int) -> str:
    if open_count <= 0:
        return "GESP4 专题待开放"
    return f"GESP4 已开放 {open_count} 个专题"


def summarize_open_topics(items: list[dict], *, limit: int = 2) -> str:
    open_titles = [item["title"] for item in items if item["is_open"]]
    if not open_titles:
        return "暂无已开放专题"
    preview = "、".join(open_titles[:limit])
    if len(open_titles) > limit:
        return f"{preview} 等 {len(open_titles)} 个专题"
    return preview


def format_delta_hours(value: int) -> str:
    if value > 0:
        return f"+{value}"
    if value < 0:
        return str(value)
    return "0"


def format_completion_rate(rate: float) -> str:
    normalized = round(float(rate or 0), 1)
    if normalized.is_integer():
        return f"{int(normalized)}%"
    return f"{normalized:.1f}%"


def build_teacher_workbench_tabs(
    active_key: str,
    *,
    message_count: int = 0,
    workspace_role: str = "teacher",
) -> list[dict[str, object]]:
    base_href = reverse("principal-dashboard") if workspace_role == "principal" else reverse("teacher-students")
    tabs = [
        {
            "key": "students",
            "label": "学生",
            "href": f"{base_href}?tab=students",
            "is_active": active_key == "students",
        },
    ]
    if workspace_role == "principal":
        tabs.append(
            {
                "key": "teachers",
                "label": "教师",
                "href": f"{base_href}?tab=teachers",
                "is_active": active_key == "teachers",
            }
        )
    tabs.extend(
        [
        {
            "key": "courses",
            "label": "课程",
            "href": f"{base_href}?tab=courses",
            "is_active": active_key == "courses",
        },
        {
            "key": "messages",
            "label": "消息",
            "href": f"{base_href}?tab=messages",
            "is_active": active_key == "messages",
            "badge_count": message_count,
        },
        {
            "key": "homework-stats",
            "label": "学生作业统计",
            "href": reverse("teacher-homework-stats"),
            "is_active": active_key == "homework-stats",
        },
        ]
    )
    return tabs


def build_principal_teacher_rows() -> list[dict[str, object]]:
    for user in PortalUser.objects.filter(role=PortalUser.ROLE_TEACHER, is_active=True).order_by("id"):
        sync_teacher_profile(user)

    teachers = list(
        Teacher.objects.select_related("user")
        .prefetch_related("courses")
        .filter(user__role=PortalUser.ROLE_TEACHER)
        .order_by("display_name", "id")
    )
    assignments = list(
        TeacherStudentAssignment.objects.select_related("teacher", "student", "course")
        .filter(teacher__role=PortalUser.ROLE_TEACHER, teacher__is_active=True, is_active=True)
        .order_by("teacher_id", "course_id", "student__display_name", "id")
    )
    assignments_by_teacher: dict[int, list[TeacherStudentAssignment]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_teacher[assignment.teacher_id].append(assignment)

    rows = []
    for teacher in teachers:
        teacher_assignments = assignments_by_teacher.get(teacher.user_id, [])
        student_names = sorted({assignment.student.display_name for assignment in teacher_assignments})
        teacher_course_items = list(teacher.courses.all())
        course_titles = [course.title for course in teacher_course_items]
        if not course_titles:
            course_titles = sorted({assignment.course.title for assignment in teacher_assignments})
        subject_parts = [
            part.strip()
            for part in re.split(r"[、,，;；\n]+", teacher.subject or "")
            if part.strip()
        ]
        manual_subject_parts = [
            part
            for part in subject_parts
            if part not in set(course_titles)
        ]
        primary_course_id = str(teacher_course_items[0].id) if teacher_course_items else ""
        rows.append(
            {
                "teacher_id": teacher.id,
                "portal_user_id": teacher.user_id,
                "name": teacher.display_name,
                "phone": teacher.phone or teacher.user.phone or "未录入",
                "subject": teacher.subject or "、".join(course_titles) or "未设置",
                "course_id": primary_course_id,
                "course_ids": [str(course.id) for course in teacher_course_items],
                "subject_manual": "、".join(manual_subject_parts),
                "is_active": teacher.is_active,
                "status_text": "在职" if teacher.is_active else "离职",
                "course_titles": "、".join(course_titles) if course_titles else "未关联课程",
                "student_count": len(student_names),
                "students_text": "、".join(student_names) if student_names else "暂未负责学生",
                "edit_action": reverse("principal-dashboard"),
                "delete_action": reverse("principal-dashboard"),
                "restore_action": reverse("principal-dashboard"),
                "search_text": " ".join(
                    [
                        teacher.display_name,
                        teacher.phone or "",
                        teacher.user.phone or "",
                        teacher.subject or "",
                        "在职" if teacher.is_active else "离职",
                        " ".join(course_titles),
                        " ".join(student_names),
                    ]
                ),
            }
        )
    return rows


def normalize_teacher_homework_stats_period(period: str) -> str:
    return normalize_homework_completion_period_type(period)


def resolve_teacher_homework_stats_period_bounds(
    period: str,
    *,
    anchor_date: date | None = None,
) -> tuple[datetime, datetime, str]:
    start_at, end_at, _, _, label = resolve_homework_completion_period_datetimes(period, anchor_date=anchor_date)
    return start_at, end_at, label


def resolve_homework_assignment_week_window(assignment: HomeworkAssignment) -> tuple[datetime, datetime, str]:
    assignment_due_date = get_homework_due_localdate(assignment.due_date) or timezone.localdate()
    start_date, end_date, label = resolve_homework_completion_period_dates("week", anchor_date=assignment_due_date)
    start_at, end_at = resolve_homework_due_datetime_range(start_date, end_date)
    return start_at, end_at, label


def is_homework_submission_within_assignment_week_window(submission: HomeworkSubmission) -> bool:
    assignment_start_at, assignment_end_at, _ = resolve_homework_assignment_week_window(submission.assignment)
    effective_submitted_at = get_homework_submission_effective_submitted_at(submission)
    return assignment_start_at <= effective_submitted_at < assignment_end_at


def filter_teacher_homework_stats_assignments(
    queryset: QuerySet[HomeworkAssignment],
    *,
    start_at: datetime,
    end_at: datetime,
) -> tuple[QuerySet[HomeworkAssignment], str]:
    return queryset.filter(due_date__gte=start_at, due_date__lt=end_at), "截止日期"


def normalize_homework_knowledge_point_name(source_filename: str) -> str:
    filename = str(source_filename or "").strip()
    normalized = re.sub(r"\.(txt|xlsx)$", "", filename, flags=re.IGNORECASE).strip()
    return normalized or "未命名知识点"


def get_teacher_homework_assignment_knowledge_point(assignment: HomeworkAssignment) -> str:
    import_job = assignment.source_import_job
    if import_job and import_job.is_active and str(import_job.source_filename or "").strip():
        return normalize_homework_knowledge_point_name(import_job.source_filename)
    content_title = str(getattr(assignment.content, "title", "") or "").strip()
    return content_title or str(assignment.title or "").strip() or "未命名知识点"


def get_teacher_homework_completion_reason_text(reason: str, *, is_completed: bool) -> str:
    if is_completed:
        return "已完成"
    reason_map = {
        "online_missing": "在线题没有完成态提交",
        "requirement_not_marked_completed": "要求型作业未标记完成",
        "undated": "无截止日期，未纳入周期统计",
    }
    return reason_map.get(str(reason or ""), "未完成")


def normalize_teacher_homework_level_code(level_code: str) -> str:
    return str(level_code or "").strip()


def format_teacher_homework_level_code_display(level_code: str) -> str:
    normalized = normalize_teacher_homework_level_code(level_code)
    return normalized or TEACHER_HOMEWORK_UNGROUPED_LEVEL_LABEL


def build_teacher_homework_student_level_code_map(
    *,
    portal_user: PortalUser,
    managed_student_ids: list[int],
) -> dict[int, str]:
    if not managed_student_ids:
        return {}

    rows = (
        TeacherStudentAssignment.objects.filter(
            teacher=portal_user,
            student_id__in=managed_student_ids,
        )
        .order_by("student_id", "-is_active", "-updated_at", "-assigned_at", "-id")
        .values("student_id", "level_code")
    )

    level_code_by_student_id: dict[int, str] = {}
    for row in rows:
        student_id = int(row["student_id"])
        if student_id in level_code_by_student_id:
            continue
        level_code_by_student_id[student_id] = normalize_teacher_homework_level_code(str(row["level_code"] or ""))
    return level_code_by_student_id


def build_teacher_homework_level_filter_options(*, student_rows: list[dict[str, object]]) -> list[dict[str, str]]:
    level_codes = sorted(
        {
            normalize_teacher_homework_level_code(str(row.get("level_code") or ""))
            for row in student_rows
            if normalize_teacher_homework_level_code(str(row.get("level_code") or ""))
        },
        key=lambda value: value.upper(),
    )
    has_ungrouped = any(not normalize_teacher_homework_level_code(str(row.get("level_code") or "")) for row in student_rows)

    options = [{"value": TEACHER_HOMEWORK_ALL_LEVEL_FILTER_VALUE, "label": "全部"}]
    options.extend({"value": level_code, "label": level_code} for level_code in level_codes)
    if has_ungrouped:
        options.append(
            {
                "value": TEACHER_HOMEWORK_UNGROUPED_LEVEL_FILTER_VALUE,
                "label": TEACHER_HOMEWORK_UNGROUPED_LEVEL_LABEL,
            }
        )
    return options


def build_teacher_homework_stats_scored_submission_queryset(
    *,
    assignment_queryset: QuerySet[HomeworkAssignment],
    managed_student_ids: list[int],
    include_missing_submitted_at: bool = False,
) -> QuerySet[HomeworkSubmission]:
    if not managed_student_ids:
        return HomeworkSubmission.objects.none()
    queryset = HomeworkSubmission.objects.select_related(
        "student",
        "assignment",
        "assignment__source_import_job",
    ).filter(
        assignment__in=assignment_queryset,
        student_id__in=managed_student_ids,
        student_id=F("assignment__student_id"),
        is_active=True,
        status__in=HOMEWORK_COMPLETION_STATUSES,
    )
    if not include_missing_submitted_at:
        queryset = queryset.filter(submitted_at__isnull=False)
    return queryset


def build_correct_wrong_rate_summary(*, correct_count: int, wrong_count: int) -> dict[str, object]:
    total_answered = max(int(correct_count or 0), 0) + max(int(wrong_count or 0), 0)
    correct_rate = round((correct_count / total_answered) * 100, 1) if total_answered else 0.0
    wrong_rate = round((wrong_count / total_answered) * 100, 1) if total_answered else 0.0
    return {
        "correct_count": int(correct_count or 0),
        "wrong_count": int(wrong_count or 0),
        "total_answered": total_answered,
        "correct_rate": correct_rate,
        "wrong_rate": wrong_rate,
        "correct_rate_text": format_completion_rate(correct_rate),
        "wrong_rate_text": format_completion_rate(wrong_rate),
    }


def build_teacher_homework_assignment_rate_details(assignment_item: dict[str, object]) -> list[dict[str, object]]:
    if not assignment_item.get("has_online_questions"):
        return []

    rate_summary = build_correct_wrong_rate_summary(
        correct_count=int(assignment_item.get("correct_count") or 0),
        wrong_count=int(assignment_item.get("wrong_count") or 0),
    )
    if int(rate_summary["total_answered"]) <= 0:
        return []

    assignment = assignment_item["assignment"]
    knowledge_point = get_teacher_homework_assignment_knowledge_point(assignment)
    return [
        {
            "assignment_id": int(assignment.id),
            "assignment_title": str(assignment.title or "").strip(),
            "knowledge_point": knowledge_point,
            "knowledge_point_name": knowledge_point,
            "show_knowledge_point_name": False,
            **rate_summary,
        }
    ]


def build_teacher_homework_student_summary_answer_rate_details(
    *,
    selected_period: str,
    assignment_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    if selected_period == "week":
        details: list[dict[str, object]] = []
        for assignment_item in assignment_items:
            assignment_details = build_teacher_homework_assignment_rate_details(assignment_item)
            if not assignment_details:
                continue
            detail = assignment_details[0]
            details.append(
                {
                    **detail,
                    "show_knowledge_point_name": True,
                }
            )
        return sorted(
            details,
            key=lambda item: (
                str(item.get("knowledge_point_name") or ""),
                str(item.get("assignment_title") or ""),
                int(item.get("assignment_id") or 0),
            ),
        )

    total_correct_count = 0
    total_wrong_count = 0
    for assignment_item in assignment_items:
        if not assignment_item.get("has_online_questions"):
            continue
        total_correct_count += int(assignment_item.get("correct_count") or 0)
        total_wrong_count += int(assignment_item.get("wrong_count") or 0)
    rate_summary = build_correct_wrong_rate_summary(
        correct_count=total_correct_count,
        wrong_count=total_wrong_count,
    )
    if int(rate_summary["total_answered"]) <= 0:
        return []
    return [
        {
            "knowledge_point": "",
            "knowledge_point_name": "",
            "show_knowledge_point_name": False,
            **rate_summary,
        }
    ]


def build_teacher_homework_student_overall_rate_summaries(
    *,
    managed_students: list[Student],
    submissions: list[HomeworkSubmission],
) -> dict[int, dict[str, object]]:
    counts_by_student: dict[int, dict[str, int]] = {
        student.id: {"correct_count": 0, "wrong_count": 0}
        for student in managed_students
    }
    for submission in submissions:
        bucket = counts_by_student.setdefault(submission.student_id, {"correct_count": 0, "wrong_count": 0})
        bucket["correct_count"] += int(submission.correct_count or 0)
        bucket["wrong_count"] += int(submission.wrong_count or 0)

    return {
        student.id: build_correct_wrong_rate_summary(
            correct_count=counts_by_student.get(student.id, {}).get("correct_count", 0),
            wrong_count=counts_by_student.get(student.id, {}).get("wrong_count", 0),
        )
        for student in managed_students
    }


def format_teacher_homework_answer_rate_detail_text(detail: dict[str, object]) -> str:
    knowledge_point_name = str(detail.get("knowledge_point") or detail.get("knowledge_point_name") or "")
    knowledge_point_prefix = f"{knowledge_point_name}：" if detail.get("show_knowledge_point_name") and knowledge_point_name else ""
    return (
        f"{knowledge_point_prefix}正确 {int(detail.get('correct_count') or 0)}，"
        f"错误 {int(detail.get('wrong_count') or 0)}，"
        f"正确率 {detail.get('correct_rate_text') or '0%'}，"
        f"错误率 {detail.get('wrong_rate_text') or '0%'}"
    )


def build_teacher_homework_student_answer_rate_details(
    *,
    selected_period: str,
    managed_students: list[Student],
    submissions: list[HomeworkSubmission],
) -> dict[int, list[dict[str, object]]]:
    details_by_student_id: dict[int, list[dict[str, object]]] = {
        student.id: []
        for student in managed_students
    }

    if selected_period == "week":
        counts_by_student_and_assignment: dict[tuple[int, int], dict[str, object]] = defaultdict(
            lambda: {
                "assignment_id": 0,
                "assignment_title": "",
                "knowledge_point_name": "",
                "correct_count": 0,
                "wrong_count": 0,
            }
        )
        for submission in submissions:
            assignment = submission.assignment
            import_job = submission.assignment.source_import_job
            if not import_job or not import_job.is_active:
                continue
            knowledge_point_name = normalize_homework_knowledge_point_name(import_job.source_filename)
            bucket = counts_by_student_and_assignment[(submission.student_id, assignment.id)]
            bucket["assignment_id"] = assignment.id
            bucket["assignment_title"] = str(assignment.title or "").strip()
            bucket["knowledge_point_name"] = knowledge_point_name
            bucket["correct_count"] += int(submission.correct_count or 0)
            bucket["wrong_count"] += int(submission.wrong_count or 0)

        for (student_id, assignment_id), bucket in counts_by_student_and_assignment.items():
            knowledge_point_name = str(bucket.get("knowledge_point_name") or "")
            rate_summary = build_correct_wrong_rate_summary(
                correct_count=int(bucket["correct_count"] or 0),
                wrong_count=int(bucket["wrong_count"] or 0),
            )
            if int(rate_summary["total_answered"]) <= 0:
                continue
            details_by_student_id.setdefault(student_id, []).append(
                {
                    "assignment_id": assignment_id,
                    "assignment_title": str(bucket.get("assignment_title") or "").strip(),
                    "knowledge_point": knowledge_point_name,
                    "knowledge_point_name": knowledge_point_name,
                    "show_knowledge_point_name": True,
                    **rate_summary,
                }
            )
        for student_id, items in details_by_student_id.items():
            items.sort(
                key=lambda item: (
                    str(item["knowledge_point_name"]),
                    str(item.get("assignment_title") or ""),
                    int(item.get("assignment_id") or 0),
                    -int(item["total_answered"]),
                )
            )
        return details_by_student_id

    counts_by_student: dict[int, dict[str, int]] = defaultdict(lambda: {"correct_count": 0, "wrong_count": 0})
    for submission in submissions:
        bucket = counts_by_student[submission.student_id]
        bucket["correct_count"] += int(submission.correct_count or 0)
        bucket["wrong_count"] += int(submission.wrong_count or 0)

    for student_id, bucket in counts_by_student.items():
        rate_summary = build_correct_wrong_rate_summary(
            correct_count=bucket["correct_count"],
            wrong_count=bucket["wrong_count"],
        )
        if int(rate_summary["total_answered"]) <= 0:
            continue
        details_by_student_id.setdefault(student_id, []).append(
            {
                "knowledge_point": "",
                "knowledge_point_name": "",
                "show_knowledge_point_name": False,
                **rate_summary,
            }
        )
    return details_by_student_id


def build_teacher_homework_student_table_answer_rate_details(
    *,
    selected_period: str,
    managed_students: list[Student],
    submissions: list[HomeworkSubmission],
) -> dict[int, list[dict[str, object]]]:
    if selected_period != "month":
        return build_teacher_homework_student_answer_rate_details(
            selected_period=selected_period,
            managed_students=managed_students,
            submissions=submissions,
        )

    details_by_student_id: dict[int, list[dict[str, object]]] = {
        student.id: []
        for student in managed_students
    }
    counts_by_student_and_assignment: dict[tuple[int, int], dict[str, object]] = defaultdict(
        lambda: {
            "assignment_id": 0,
            "assignment_title": "",
            "knowledge_point_name": "",
            "correct_count": 0,
            "wrong_count": 0,
        }
    )

    for submission in submissions:
        assignment = submission.assignment
        import_job = assignment.source_import_job
        if not import_job or not import_job.is_active:
            continue
        knowledge_point_name = normalize_homework_knowledge_point_name(import_job.source_filename)
        bucket = counts_by_student_and_assignment[(submission.student_id, assignment.id)]
        bucket["assignment_id"] = assignment.id
        bucket["assignment_title"] = str(assignment.title or "").strip()
        bucket["knowledge_point_name"] = knowledge_point_name
        bucket["correct_count"] += int(submission.correct_count or 0)
        bucket["wrong_count"] += int(submission.wrong_count or 0)

    for (student_id, assignment_id), bucket in counts_by_student_and_assignment.items():
        rate_summary = build_correct_wrong_rate_summary(
            correct_count=int(bucket["correct_count"] or 0),
            wrong_count=int(bucket["wrong_count"] or 0),
        )
        if int(rate_summary["total_answered"]) <= 0:
            continue
        details_by_student_id.setdefault(student_id, []).append(
            {
                "assignment_id": assignment_id,
                "assignment_title": str(bucket.get("assignment_title") or "").strip(),
                "knowledge_point": "",
                "knowledge_point_name": "",
                "show_knowledge_point_name": False,
                **rate_summary,
            }
        )

    for student_id, items in details_by_student_id.items():
        items.sort(
            key=lambda item: (
                -float(item.get("wrong_rate") or 0),
                int(item.get("assignment_id") or 0),
                str(item.get("assignment_title") or ""),
            )
        )
    return details_by_student_id


def build_teacher_homework_student_detail_rows(
    *,
    selected_period: str,
    student_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            **student_row,
            "student_sort_index": student_index,
        }
        for student_index, student_row in enumerate(student_rows)
    ]


def format_teacher_homework_correct_rate_text(correct_rate: object) -> str:
    if correct_rate is None:
        return "暂无"
    try:
        numeric_rate = float(correct_rate)
    except (TypeError, ValueError):
        return "暂无"
    return format_completion_rate(max(numeric_rate, 0.0) * 100)


def build_teacher_homework_knowledge_point_lines(
    knowledge_points: list[dict[str, object]],
) -> list[str]:
    lines: list[str] = []
    for knowledge_point in knowledge_points:
        name = str(knowledge_point.get("name") or "").strip() or "未命名作业"
        mastery_status = knowledge_point.get("mastery_status")
        correct_rate_text = (
            str(knowledge_point.get("correct_rate_text") or "").strip()
            or format_teacher_homework_correct_rate_text(knowledge_point.get("correct_rate"))
        )
        if mastery_status is None:
            lines.append(f"{name}｜要求型作业")
            continue
        lines.append(f"{name}｜{str(mastery_status).strip() or '未作答'}｜正确率 {correct_rate_text}")
    return lines


def build_teacher_homework_knowledge_point_short_lines(
    knowledge_points: list[dict[str, object]],
) -> list[str]:
    lines: list[str] = []
    for knowledge_point in knowledge_points:
        mastery_status = knowledge_point.get("mastery_status")
        correct_rate = knowledge_point.get("correct_rate")
        if correct_rate is not None:
            lines.append(
                str(knowledge_point.get("correct_rate_text") or "").strip()
                or format_teacher_homework_correct_rate_text(correct_rate)
            )
        elif mastery_status == "未作答":
            lines.append("未作答")
        else:
            lines.append("—")
    return lines


def build_teacher_homework_lesson_feedback_search_text(
    lesson_feedbacks: list[dict[str, object]],
) -> str:
    if not lesson_feedbacks:
        return "本周暂无可评价作业"
    parts: list[str] = []
    for item in lesson_feedbacks:
        assignment_id = int(item.get("assignment_id") or 0)
        title = str(item.get("title") or "").strip()
        due_date = str(item.get("due_date") or "").strip()
        source_import_job_id = int(item.get("source_import_job_id") or 0)
        highlights = str(item.get("highlights") or "").strip()
        areas_for_growth = str(item.get("areas_for_growth") or "").strip()
        section_parts = [
            f"assignment_id {assignment_id}" if assignment_id else "",
            due_date,
            title,
            f"source_import_job_id {source_import_job_id}" if source_import_job_id else "",
            highlights,
            areas_for_growth,
        ]
        parts.append(" | ".join(part for part in section_parts if part))
    return " || ".join(parts)


def build_teacher_homework_student_table_column_titles(*, selected_period: str) -> list[str]:
    if selected_period in {"month", "quarter"}:
        return [
            "学生姓名",
            "当前级别",
            "应完成",
            "已完成",
            "按时完成",
            "延迟完成",
            "未完成",
            "未设置截止日期",
            "最近布置时间",
            "最近截止日期",
            "操作",
        ]

    return [
        "学生姓名",
        "当前级别",
        "应完成",
        "已完成",
        "按时完成",
        "正确率",
        "教师评价",
        "操作",
    ]


def build_teacher_homework_submission_question_detail(answer: HomeworkSubmissionAnswer) -> str:
    question = answer.homework_question
    lines: list[str] = []
    if question.question_no:
        lines.append(f"【题目标题】第 {question.question_no} 题")
    if str(question.stem or "").strip():
        lines.append(f"【题目】{str(question.stem).strip()}")

    option_items = build_homework_option_items(
        question.options_json,
        selected_answer=answer.selected_answer,
        correct_answer=answer.correct_answer_snapshot,
    )
    if option_items:
        lines.append("【选项】")
        lines.extend(f"{item['key']}. {item['text']}" for item in option_items)

    selected_answer = str(answer.selected_answer or "").strip()
    correct_answer = str(answer.correct_answer_snapshot or "").strip()
    lines.append(f"【学生答案】{selected_answer or '未作答'}")
    if correct_answer:
        lines.append(f"【正确答案】{correct_answer}")
    lines.append(f"【结果】{'正确' if answer.is_correct else '错误'}")
    return "\n".join(lines)


def build_teacher_homework_submission_detail_rows(
    *,
    portal_user: PortalUser,
    student: Student,
    period_start: datetime,
    period_end: datetime,
) -> list[dict[str, object]]:
    submissions = list(
        HomeworkSubmission.objects.select_related(
            "assignment",
            "assignment__source_import_job",
            "student",
        ).filter(
            student=student,
            student__teacher_user=portal_user,
            student_id=F("assignment__student_id"),
            assignment__teacher=portal_user,
            assignment__is_active=True,
            assignment__due_date__gte=period_start,
            assignment__due_date__lt=period_end,
            is_active=True,
        ).order_by(
            "-submitted_at",
            "-created_at",
            "-id",
        )
    )

    rows: list[dict[str, object]] = []
    for submission in submissions:
        assignment = submission.assignment
        knowledge_point = get_teacher_homework_assignment_knowledge_point(assignment)
        rate_summary = build_correct_wrong_rate_summary(
            correct_count=int(submission.correct_count or 0),
            wrong_count=int(submission.wrong_count or 0),
        )
        total_answered = int(rate_summary["total_answered"])
        rows.append(
            {
                "row_key": f"submission-{submission.id}",
                "submitted_at": submission.submitted_at or submission.created_at,
                "submitted_at_text": format_datetime(submission.submitted_at or submission.created_at),
                "knowledge_point": knowledge_point,
                "correct_count": int(submission.correct_count or 0),
                "wrong_count": int(submission.wrong_count or 0),
                "correct_rate": float(rate_summary["correct_rate"]) if total_answered > 0 else 0.0,
                "correct_rate_text": str(rate_summary["correct_rate_text"]) if total_answered > 0 else "暂无统计",
                "submission_rate_search_text": (
                    f"正确 {int(submission.correct_count or 0)}，错误 {int(submission.wrong_count or 0)}，正确率 "
                    f"{str(rate_summary['correct_rate_text']) if total_answered > 0 else '暂无统计'}"
                ),
                "detail_answer_href": (
                    f"{reverse('teacher-homework-stats-submission-answer-detail')}?{urlencode({'submission_id': submission.id})}"
                ),
            }
        )
    return rows


def build_teacher_homework_assignment_submission_detail_rows(
    *,
    portal_user: PortalUser,
    student: Student,
    assignment: HomeworkAssignment,
    period_start: datetime,
    period_end: datetime,
    period: str = "month",
) -> list[dict[str, object]]:
    raw_submissions = list(
        HomeworkSubmission.objects.select_related(
            "assignment",
            "assignment__source_import_job",
            "student",
        ).filter(
            student=student,
            assignment=assignment,
            student__teacher_user=portal_user,
            student_id=F("assignment__student_id"),
            assignment__teacher=portal_user,
            assignment__student=student,
            assignment__is_active=True,
            is_active=True,
        )
    )
    submissions = sorted(
        raw_submissions,
        key=lambda item: (
            get_homework_submission_effective_submitted_at(item),
            item.created_at,
            item.id,
        ),
        reverse=True,
    )

    knowledge_point = get_teacher_homework_assignment_knowledge_point(assignment)
    rows: list[dict[str, object]] = []
    for submission in submissions:
        total_count = int(submission.total_count or 0)
        correct_count = int(submission.correct_count or 0)
        wrong_count = int(submission.wrong_count or 0)
        rate_summary = build_correct_wrong_rate_summary(
            correct_count=correct_count,
            wrong_count=wrong_count,
        )
        rows.append(
            {
                "row_key": f"assignment-submission-{submission.id}",
                "submission_id": int(submission.id),
                "assignment_id": int(assignment.id),
                "knowledge_point": knowledge_point,
                "status": str(submission.status or ""),
                "status_text": get_homework_submission_status_text(submission.status),
                "total_count": total_count,
                "correct_count": correct_count,
                "wrong_count": wrong_count,
                "score_text": f"{submission.score}",
                "started_at_text": format_datetime(submission.started_at) if submission.started_at else "未开始",
                "submitted_at_text": format_datetime(submission.submitted_at) if submission.submitted_at else "未提交",
                "checked_at_text": format_datetime(submission.checked_at) if submission.checked_at else "未判分",
                "created_at_text": format_datetime(submission.created_at),
                "correct_rate": float(rate_summary["correct_rate"]) if total_count > 0 else 0.0,
                "correct_rate_text": str(rate_summary["correct_rate_text"]) if total_count > 0 else "暂无统计",
                "detail_answer_href": (
                    f"{reverse('teacher-homework-stats-submission-answer-detail')}?{urlencode({'submission_id': submission.id, 'period': period})}"
                ),
                "submission_search_text": " | ".join(
                    [
                        f"提交记录 ID {submission.id}",
                        f"assignment_id {assignment.id}",
                        knowledge_point,
                        get_homework_submission_status_text(submission.status),
                        f"总题数 {total_count}",
                        f"正确 {correct_count}",
                        f"错误 {wrong_count}",
                        f"分数 {submission.score}",
                    ]
                ),
            }
        )
    return rows


def build_teacher_homework_student_period_assignment_detail_rows(
    *,
    portal_user: PortalUser,
    student: Student,
    period: str,
    anchor_date: date | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    stats_result = build_homework_completion_stats(
        portal_user,
        student=student,
        period_type=period,
        anchor_date=anchor_date,
    )
    student_stat = stats_result["student_stats"][0] if stats_result["student_stats"] else None
    if student_stat is None:
        return [], stats_result

    def latest_submission_id(assignment_item: dict[str, object]) -> int:
        submissions = list(assignment_item.get("active_submissions") or [])
        if not submissions:
            return 0
        latest_submission = max(
            submissions,
            key=lambda submission: (
                get_homework_submission_effective_submitted_at(submission),
                submission.created_at,
                submission.id,
            ),
        )
        return int(latest_submission.id)

    rows: list[dict[str, object]] = []
    for assignment_item in sorted(
        student_stat["assignments"],
        key=lambda item: (
            item["assignment"].due_date,
            item["assignment"].assigned_at,
            item["assignment_id"],
        ),
        reverse=True,
    ):
        assignment = assignment_item["assignment"]
        has_online_questions = bool(assignment_item.get("has_online_questions"))
        latest_completed_submission = assignment_item.get("latest_completed_submission")
        rows.append(
            {
                "row_key": f"student-period-assignment-{assignment.id}",
                "assignment_id": int(assignment.id),
                "assignment_title": str(assignment.title or "").strip(),
                "assigned_at": assignment.assigned_at,
                "assigned_at_text": format_datetime(assignment.assigned_at),
                "due_date": assignment.due_date,
                "due_date_text": format_date(assignment.due_date),
                "knowledge_point": get_teacher_homework_assignment_knowledge_point(assignment),
                "has_online_questions": has_online_questions,
                "homework_mode_text": (
                    f"在线题 {int(assignment_item.get('online_question_count') or 0)} 题"
                    if has_online_questions
                    else "要求型作业"
                ),
                "completion_state": str(assignment_item.get("completion_state") or ""),
                "completion_state_text": "已完成" if assignment_item.get("is_completed") else "未完成",
                "completion_reason": str(assignment_item.get("completion_reason") or ""),
                "completion_reason_text": get_teacher_homework_completion_reason_text(
                    str(assignment_item.get("completion_reason") or ""),
                    is_completed=bool(assignment_item.get("is_completed")),
                ),
                "completed_at": assignment.completed_at,
                "completed_at_text": format_datetime(assignment.completed_at) if assignment.completed_at else "未完成",
                "submission_count": int(assignment_item.get("active_submission_count") or 0),
                "latest_submission_id": latest_submission_id(assignment_item),
                "latest_completed_submission_id": int(latest_completed_submission.id) if latest_completed_submission else 0,
                "detail_href": (
                    f"{reverse('teacher-homework-stats-assignment-submissions')}?"
                    f"{urlencode({'student_id': student.id, 'assignment_id': assignment.id, 'period': period, **({'anchor_date': anchor_date.isoformat()} if anchor_date else {})})}"
                    if has_online_questions
                    else ""
                ),
                "detail_label": "查看提交记录" if has_online_questions else "",
            }
        )
    return rows, stats_result


def build_teacher_homework_submission_answer_detail_rows(
    *,
    submission: HomeworkSubmission,
) -> list[dict[str, object]]:
    assignment = submission.assignment
    knowledge_point = get_teacher_homework_assignment_knowledge_point(assignment)
    submitted_at_text = format_datetime(submission.submitted_at or submission.created_at)

    answers = list(
        submission.answers.select_related("homework_question")
        .filter(homework_question__is_active=True)
        .order_by("homework_question__question_no", "id")
    )
    rows: list[dict[str, object]] = []
    for index, answer in enumerate(answers, start=1):
        question_detail = build_teacher_homework_submission_question_detail(answer)
        rows.append(
            {
                "row_key": f"answer-{index}",
                "submitted_at_text": submitted_at_text,
                "knowledge_point": knowledge_point,
                "question_detail": question_detail,
                "question_detail_search_text": question_detail,
            }
        )
    return rows


def build_lesson_hour_summary(student: Student) -> dict:
    balance = student.lesson_hour_ledgers.aggregate(total=Sum("delta_hours")).get("total") or 0
    latest = student.lesson_hour_ledgers.select_related("teacher").first()
    return {
        "balance": balance,
        "balance_text": format_delta_hours(balance),
        "latest": latest,
        "latest_text": format_delta_hours(latest.delta_hours) if latest else "暂无",
        "latest_note": latest.note if latest and latest.note else "暂无最近课时变动记录",
        "latest_created_at_text": format_datetime(latest.created_at) if latest else "暂无记录",
    }


def serialize_evaluation(record: TeacherEvaluation) -> dict:
    return {
        "text": record.evaluation_text,
        "teacher_name": record.teacher.full_name if record.teacher else "教师",
        "created_at": record.created_at,
        "created_at_text": format_datetime(record.created_at),
    }


def serialize_reward(record: RewardRecord) -> dict:
    return {
        "text": record.reward_text,
        "teacher_name": record.teacher.full_name if record.teacher else "教师",
        "created_at": record.created_at,
        "created_at_text": format_datetime(record.created_at),
    }


def serialize_lesson_hour(record: LessonHourLedger) -> dict:
    return {
        "delta_hours": record.delta_hours,
        "delta_hours_text": format_delta_hours(record.delta_hours),
        "note": record.note or "未填写备注",
        "teacher_name": record.teacher.full_name if record.teacher else "教师",
        "created_at": record.created_at,
        "created_at_text": format_datetime(record.created_at),
    }


def format_date(value: date | datetime | None) -> str:
    if not value:
        return "暂无"
    normalized_date = get_homework_due_localdate(value)
    if normalized_date is None:
        return "暂无"
    return normalized_date.strftime("%Y-%m-%d")


def get_week_date_range(today: date | None = None) -> tuple[date, date]:
    current_day = today or timezone.localdate()
    week_start = current_day - timedelta(days=current_day.weekday())
    return week_start, week_start + timedelta(days=6)


def get_homework_status_text(status: str) -> str:
    return HOMEWORK_STATUS_LABELS.get(status, status or "未知状态")


def get_homework_status_tone(status: str) -> str:
    return HOMEWORK_STATUS_TONES.get(status, "future")


def get_homework_import_status_text(status: str) -> str:
    return HOMEWORK_IMPORT_STATUS_LABELS.get(status, status or "未知状态")


def get_homework_import_status_tone(status: str) -> str:
    return HOMEWORK_IMPORT_STATUS_TONES.get(status, "future")


def get_homework_submission_status_text(status: str) -> str:
    return HOMEWORK_SUBMISSION_STATUS_LABELS.get(status, status or "未知状态")


def get_homework_submission_status_tone(status: str) -> str:
    return HOMEWORK_SUBMISSION_STATUS_TONES.get(status, "future")


def get_homework_status_note(status: str) -> str:
    status_notes = {
        HomeworkAssignment.STATUS_ASSIGNED: "等待学生完成，老师可先补充提醒型评语。",
        HomeworkAssignment.STATUS_COMPLETED: "学生已标记完成，建议老师尽快查看并给出评语。",
        HomeworkAssignment.STATUS_REVIEWED: "最新评语已保存，学生端会同步看到当前版本。",
        HomeworkAssignment.STATUS_CANCELLED: "作业已取消，但记录会保留在当前列表里。",
    }
    return status_notes.get(status, "当前作业状态待确认。")


def get_homework_level_codes(course_slug: str, raw_level_code: str) -> set[str]:
    normalized = (raw_level_code or "").strip().upper()
    if not normalized:
        return set()
    if course_slug == "cpp":
        stage_codes = get_visible_cpp_stage_codes(normalized)
        return stage_codes or {normalized}
    if course_slug == "uav":
        return UAV_HOMEWORK_LEVEL_CODE_MAP.get(normalized, {normalized})
    return {normalized}


def get_homework_content_level_label(content: CourseContent) -> str:
    if content.level_id and content.level:
        return content.level.title
    return (content.phase or "").strip() or "未分级"


def format_homework_content_label(content: CourseContent) -> str:
    return f"{content.course.title} / {get_homework_content_level_label(content)} / {content.title}"


def serialize_homework_content_option(content: CourseContent) -> dict:
    return {
        "id": content.id,
        "title": content.title,
        "summary": content.summary or "当前知识点未补充额外说明。",
        "route_path": content.route_path,
        "phase": (content.phase or "").strip(),
        "level_id": content.level_id or 0,
        "level_label": get_homework_content_level_label(content),
        "label": format_homework_content_label(content),
    }


def build_homework_content_knowledge_form_values(
    *,
    course_slug: str,
    selected_content_option: dict | None,
) -> dict[str, object]:
    title_parts = [
        part.strip()
        for part in str((selected_content_option or {}).get("title") or "").split("/")
        if part.strip()
    ]
    return {
        "content_id": int((selected_content_option or {}).get("id") or 0),
        "target_subject": normalize_knowledge_map_subject(course_slug),
        "target_category_code": str(
            (selected_content_option or {}).get("phase")
            or (selected_content_option or {}).get("level_label")
            or ""
        ).strip().upper(),
        "target_level_1": title_parts[0] if len(title_parts) >= 1 else "",
        "target_level_2": title_parts[1] if len(title_parts) >= 2 else "",
        "target_level_3": title_parts[2] if len(title_parts) >= 3 else "",
    }


def get_teacher_question_source_level_options(portal_user: PortalUser, course_slug: str) -> list[dict]:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    visible_level_codes: set[str] = set()
    for assignment in scope["course_assignments"]:
        if course.slug == "cpp":
            visible_level_codes.update(get_visible_cpp_stage_codes(assignment.level_code))
        else:
            visible_level_codes.update(get_homework_level_codes(course.slug, assignment.level_code))

    if not visible_level_codes:
        return []

    level_queryset = (
        CourseLevel.objects.select_related("category", "category__course")
        .filter(category__course=course, is_active=True, code__in=visible_level_codes)
        .order_by("category__sort_order", "category__id", "sort_order", "id")
    )
    return [
        {
            "id": level.id,
            "code": level.code,
            "title": level.title,
            "category_slug": level.category.slug,
            "category_title": level.category.title,
            "label": f"{level.category.title} / {level.title}",
        }
        for level in level_queryset
    ]


def annotate_homework_online_question_counts(queryset: QuerySet[HomeworkAssignment]) -> QuerySet[HomeworkAssignment]:
    return queryset.annotate(
        direct_online_question_count=Count("questions", filter=Q(questions__is_active=True), distinct=True),
        source_online_question_count=Count(
            "source_import_job__questions",
            filter=Q(source_import_job__questions__is_active=True),
            distinct=True,
        ),
    )


def get_teacher_student_homework_queryset(portal_user: PortalUser, student: Student) -> QuerySet[HomeworkAssignment]:
    return (
        annotate_homework_online_question_counts(
            HomeworkAssignment.objects.select_related(
                "teacher",
                "student",
                "content",
                "content__course",
                "content__level",
                "summary",
                "source_import_job",
            )
        )
        .filter(teacher=portal_user, student=student, is_active=True)
        .order_by("-due_date", "-assigned_at", "-id")
    )


def get_student_homework_queryset(student: Student) -> QuerySet[HomeworkAssignment]:
    return (
        annotate_homework_online_question_counts(
            HomeworkAssignment.objects.select_related(
                "teacher",
                "student",
                "content",
                "content__course",
                "content__level",
                "summary",
                "source_import_job",
            )
        )
        .filter(student=student, is_active=True)
        .order_by("-due_date", "-assigned_at", "-id")
    )


def get_teacher_student_homework_scope_assignments(
    portal_user: PortalUser,
    student: Student,
) -> list[TeacherStudentAssignment]:
    return list(
        TeacherStudentAssignment.objects.select_related("course")
        .filter(teacher=portal_user, student=student, is_active=True)
        .order_by("course_id", "level_code", "id")
    )


def get_teacher_student_homework_contents(portal_user: PortalUser, student: Student) -> list[CourseContent]:
    assignments = get_teacher_student_homework_scope_assignments(portal_user, student)
    if not assignments:
        return []

    content_map: dict[int, CourseContent] = {}
    for assignment in assignments:
        queryset = CourseContent.objects.select_related("course", "level").filter(course=assignment.course, is_active=True)
        if assignment.course.slug == "cpp":
            visible_permission_codes = get_visible_permission_codes(assignment.level_code)
            visible_stage_codes = get_visible_cpp_stage_codes(assignment.level_code)
            queryset = queryset.filter(
                Q(permission_code__in=visible_permission_codes)
                | (
                    Q(permission_code="")
                    & (Q(level__code__in=visible_stage_codes) | Q(phase__in=visible_stage_codes))
                )
            )
        else:
            level_codes = get_homework_level_codes(assignment.course.slug, assignment.level_code)
            if level_codes:
                queryset = queryset.filter(Q(level__code__in=level_codes) | Q(phase__in=level_codes))
        for content in queryset.order_by("course_id", "phase", "sort_order", "id"):
            content_map.setdefault(content.id, content)

    return sorted(
        content_map.values(),
        key=lambda item: (
            item.course.title.lower(),
            get_homework_content_level_label(item).lower(),
            item.sort_order,
            item.id,
        ),
    )


def get_teacher_course_homework_contents(portal_user: PortalUser, course_slug: str) -> list[CourseContent]:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    course_assignments = scope["course_assignments"]
    if not course_assignments:
        return []

    content_map: dict[int, CourseContent] = {}
    for assignment in course_assignments:
        queryset = CourseContent.objects.select_related("course", "level").filter(course=course, is_active=True)
        if course.slug == "cpp":
            visible_permission_codes = get_visible_permission_codes(assignment.level_code)
            visible_stage_codes = get_visible_cpp_stage_codes(assignment.level_code)
            queryset = queryset.filter(
                Q(permission_code__in=visible_permission_codes)
                | (
                    Q(permission_code="")
                    & (Q(level__code__in=visible_stage_codes) | Q(phase__in=visible_stage_codes))
                )
            )
        else:
            level_codes = get_homework_level_codes(course.slug, assignment.level_code)
            if level_codes:
                queryset = queryset.filter(Q(level__code__in=level_codes) | Q(phase__in=level_codes))
        for content in queryset.order_by("course_id", "phase", "sort_order", "id"):
            content_map.setdefault(content.id, content)

    return sorted(
        content_map.values(),
        key=lambda item: (
            item.course.title.lower(),
            get_homework_content_level_label(item).lower(),
            item.sort_order,
            item.id,
        ),
    )


def get_teacher_batch_homework_contents(
    portal_user: PortalUser,
    *,
    course_slug: str = "",
) -> list[CourseContent]:
    normalized_course_slug = str(course_slug or "").strip().lower()
    if normalized_course_slug:
        return get_teacher_course_homework_contents(portal_user, normalized_course_slug)

    content_map: dict[int, CourseContent] = {}
    for assignment in get_teacher_active_assignments(portal_user):
        queryset = CourseContent.objects.select_related("course", "level").filter(course=assignment.course, is_active=True)
        if assignment.course.slug == "cpp":
            visible_permission_codes = get_visible_permission_codes(assignment.level_code)
            visible_stage_codes = get_visible_cpp_stage_codes(assignment.level_code)
            queryset = queryset.filter(
                Q(permission_code__in=visible_permission_codes)
                | (
                    Q(permission_code="")
                    & (Q(level__code__in=visible_stage_codes) | Q(phase__in=visible_stage_codes))
                )
            )
        else:
            level_codes = get_homework_level_codes(assignment.course.slug, assignment.level_code)
            if level_codes:
                queryset = queryset.filter(Q(level__code__in=level_codes) | Q(phase__in=level_codes))
        for content in queryset.order_by("course_id", "phase", "sort_order", "id"):
            content_map.setdefault(content.id, content)

    return sorted(
        content_map.values(),
        key=lambda item: (
            item.course.title.lower(),
            get_homework_content_level_label(item).lower(),
            item.sort_order,
            item.id,
        ),
    )


def build_teacher_student_homework_empty_state(
    portal_user: PortalUser,
    student: Student,
    *,
    assignments: list[TeacherStudentAssignment] | None = None,
) -> dict:
    scope_assignments = assignments if assignments is not None else get_teacher_student_homework_scope_assignments(portal_user, student)
    if not scope_assignments:
        return {
            "title": "当前没有可布置内容",
            "message": "当前老师对这个学生还没有启用中的 TeacherStudentAssignment。先检查学生关系管理里的课程与级别范围。",
            "scope_rows": [],
        }

    scope_rows = []
    matched_content_total = 0
    for assignment in scope_assignments:
        queryset = CourseContent.objects.filter(course=assignment.course, is_active=True)
        if assignment.course.slug == "cpp":
            visible_permission_codes = get_visible_permission_codes(assignment.level_code)
            visible_stage_codes = get_visible_cpp_stage_codes(assignment.level_code)
            queryset = queryset.filter(
                Q(permission_code__in=visible_permission_codes)
                | (
                    Q(permission_code="")
                    & (Q(level__code__in=visible_stage_codes) | Q(phase__in=visible_stage_codes))
                )
            )
            mapped_level_codes = sorted(visible_stage_codes)
        else:
            mapped_level_codes = sorted(get_homework_level_codes(assignment.course.slug, assignment.level_code))
            if mapped_level_codes:
                queryset = queryset.filter(Q(level__code__in=mapped_level_codes) | Q(phase__in=mapped_level_codes))
        matched_count = queryset.count()
        matched_content_total += matched_count
        scope_rows.append(
            {
                "course_title": assignment.course.title,
                "raw_level_code": assignment.level_code,
                "mapped_level_codes_text": " / ".join(mapped_level_codes) if mapped_level_codes else "未映射",
                "matched_content_count": matched_count,
            }
        )

    if matched_content_total == 0:
        message = "当前老师和学生之间已有负责范围，但该范围下没有可用的 CourseContent。先检查 TeacherStudentAssignment 的级别是否匹配，或补齐对应课程内容。"
    else:
        message = "当前页面没有拿到可布置内容。请先检查 TeacherStudentAssignment 与 CourseContent 的课程/级别映射。"

    return {
        "title": "当前负责范围内没有可布置知识点",
        "message": message,
        "scope_rows": scope_rows,
    }


def get_teacher_student_homework_content_options(portal_user: PortalUser, student: Student) -> list[dict]:
    return [
        serialize_homework_content_option(content)
        for content in get_teacher_student_homework_contents(portal_user, student)
    ]


def build_teacher_student_content_restriction_context(
    portal_user: PortalUser,
    student: Student,
) -> dict:
    items = []
    for content in get_teacher_student_homework_contents(portal_user, student):
        visibility = get_student_content_visibility(student, content)
        if not visibility["default_visible"]:
            continue
        is_restricted = not visibility["is_visible"]
        items.append(
            {
                "id": content.id,
                "title": content.title,
                "label": format_homework_content_label(content),
                "course_title": content.course.title,
                "level_label": get_homework_content_level_label(content),
                "summary": content.summary or "当前知识点未补充额外说明。",
                "route_path": content.route_path,
                "permission_code": visibility["permission_code"] or "未配置",
                "course_level_code": visibility["course_level_code"] or "未分配",
                "is_restricted": is_restricted,
                "selected": is_restricted,
                "state": "locked" if is_restricted else "open",
                "status_text": "已限制" if is_restricted else "默认可见",
                "note": (
                    "当前已写入学生级限制，学生端不会显示该内容。"
                    if is_restricted
                    else f"当前等级 {visibility['course_level_code'] or '未分配'} 默认可见。"
                ),
            }
        )

    restricted_items = [item for item in items if item["is_restricted"]]
    visible_items = [item for item in items if not item["is_restricted"]]
    if restricted_items:
        summary_text = "已限制：" + "、".join(item["title"] for item in restricted_items[:3])
        if len(restricted_items) > 3:
            summary_text += f" 等 {len(restricted_items)} 个内容"
    elif items:
        summary_text = "当前还没有限制记录，学生会直接看到这些默认可见内容。"
    else:
        summary_text = "当前老师负责范围内还没有可限制的默认可见内容。"

    return {
        "content_restriction_items": items,
        "restricted_content_items": restricted_items,
        "content_restriction_total_count": len(items),
        "content_restriction_restricted_count": len(restricted_items),
        "content_restriction_visible_count": len(visible_items),
        "content_restriction_summary_text": summary_text,
    }


def ensure_homework_content_access(
    student: Student,
    content: CourseContent,
    teacher: PortalUser,
) -> StudentContentAccess | None:
    access = set_student_content_visibility(student, content, is_visible=True, granted_by=teacher)
    return access or get_student_content_access(student, content)


def serialize_homework_assignment(assignment: HomeworkAssignment) -> dict:
    status = assignment.status
    teacher_comment = assignment.teacher_comment.strip()
    completed_like = {HomeworkAssignment.STATUS_COMPLETED, HomeworkAssignment.STATUS_REVIEWED}
    has_comment = bool(teacher_comment)
    summary = getattr(assignment, "summary", None)
    has_summary = bool(summary)
    online_question_count = assignment.get_effective_online_question_count()
    return {
        "id": assignment.id,
        "title": assignment.title,
        "description": assignment.description or "当前老师没有补充额外说明，先进入知识点页完成本次任务。",
        "due_date": assignment.due_date,
        "due_date_text": format_date(assignment.due_date),
        "status": status,
        "status_text": get_homework_status_text(status),
        "status_tone": get_homework_status_tone(status),
        "status_note": get_homework_status_note(status),
        "teacher_name": assignment.teacher.full_name,
        "teacher_comment": teacher_comment,
        "teacher_comment_text": teacher_comment or "当前老师还没有填写评语。",
        "teacher_comment_summary": shorten_text(teacher_comment, limit=36) if teacher_comment else "暂无评语",
        "has_teacher_comment": has_comment,
        "content_id": assignment.content_id,
        "content_title": assignment.content.title,
        "content_level_label": get_homework_content_level_label(assignment.content),
        "content_path_label": format_homework_content_label(assignment.content),
        "content_route_path": assignment.content.route_path,
        "assigned_at": assignment.assigned_at,
        "assigned_at_text": format_datetime(assignment.assigned_at),
        "created_at": assignment.created_at,
        "completed_at": assignment.completed_at,
        "completed_at_text": format_datetime(assignment.completed_at) if assignment.completed_at else "未完成",
        "reviewed_at": assignment.reviewed_at,
        "reviewed_at_text": format_datetime(assignment.reviewed_at) if assignment.reviewed_at else "未评阅",
        "highlights": str(assignment.highlights or "").strip(),
        "areas_for_growth": str(assignment.areas_for_growth or "").strip(),
        "online_question_count": online_question_count,
        "is_online_homework": online_question_count > 0,
        "homework_mode_text": f"在线选择题 {online_question_count} 题" if online_question_count else "知识点任务型作业",
        "has_summary": has_summary,
        "summary_id": summary.id if has_summary else 0,
        "summary_title": get_homework_summary_title(summary, assignment) if has_summary else "",
        "summary_updated_at": summary.updated_at if has_summary else None,
        "summary_updated_at_text": format_datetime(summary.updated_at) if has_summary else "",
        "summary_status_text": "查看总结" if has_summary else "未生成",
        "is_completed": status in completed_like,
        "is_reviewed": status == HomeworkAssignment.STATUS_REVIEWED,
        "is_cancelled": status == HomeworkAssignment.STATUS_CANCELLED,
        "can_mark_completed": status == HomeworkAssignment.STATUS_ASSIGNED,
        "can_cancel": status != HomeworkAssignment.STATUS_CANCELLED,
        "review_action_label": "修改评语" if status == HomeworkAssignment.STATUS_REVIEWED else "写评语",
        "review_action_class": "teacher-anchor-link" if status == HomeworkAssignment.STATUS_REVIEWED else "login-button",
    }


def build_homework_summary_items(assignments: list[HomeworkAssignment]) -> list[dict]:
    completed_like = {HomeworkAssignment.STATUS_COMPLETED, HomeworkAssignment.STATUS_REVIEWED}
    total_count = len(assignments)
    assigned_count = sum(1 for assignment in assignments if assignment.status == HomeworkAssignment.STATUS_ASSIGNED)
    completed_count = sum(1 for assignment in assignments if assignment.status in completed_like)
    cancelled_count = sum(1 for assignment in assignments if assignment.status == HomeworkAssignment.STATUS_CANCELLED)
    return [
        {"label": "作业总数", "value": f"{total_count} 条", "hint": "当前学生下的全部作业记录"},
        {"label": "待完成", "value": f"{assigned_count} 条", "hint": "学生还没有标记完成的作业"},
        {"label": "已完成", "value": f"{completed_count} 条", "hint": "包含已完成与已评阅作业"},
        {"label": "已取消", "value": f"{cancelled_count} 条", "hint": "教师手动取消的作业"},
    ]


def build_teacher_student_homework_context(
    portal_user: PortalUser,
    student: Student,
    *,
    homework_form_values: dict[str, object] | None = None,
    homework_error_message: str = "",
    homework_success_message: str = "",
    homework_summary_form_values: dict[str, object] | None = None,
    homework_summary_error_message: str = "",
) -> dict:
    assignments = list(get_teacher_student_homework_queryset(portal_user, student))
    homework_items = [serialize_homework_assignment(item) for item in assignments]
    for item in homework_items:
        item["question_builder_href"] = reverse("teacher-homework-builder", args=[student.id, item["id"]])
        item["question_builder_label"] = (
            f"管理在线题目（{item['online_question_count']}）"
            if item["online_question_count"]
            else "上传生成选择题"
        )
    default_summary_start_date, default_summary_end_date = resolve_homework_summary_default_range(assignments)
    summary_start_raw = str((homework_summary_form_values or {}).get("start_date") or default_summary_start_date.isoformat())
    summary_end_raw = str((homework_summary_form_values or {}).get("end_date") or default_summary_end_date.isoformat())
    try:
        summary_start_date = date.fromisoformat(summary_start_raw)
    except ValueError:
        summary_start_date = default_summary_start_date
        summary_start_raw = default_summary_start_date.isoformat()
    try:
        summary_end_date = date.fromisoformat(summary_end_raw)
    except ValueError:
        summary_end_date = default_summary_end_date
        summary_end_raw = default_summary_end_date.isoformat()
    summary_scope_assignments = (
        filter_homework_assignments_by_assigned_date(
            assignments,
            start_date=summary_start_date,
            end_date=summary_end_date,
        )
        if summary_start_date <= summary_end_date
        else []
    )
    summary_scope_rows = [
        {
            "id": assignment.id,
            "title": assignment.title,
            "content_title": assignment.content.title,
            "assigned_at_text": format_datetime(assignment.assigned_at),
            "status_text": get_homework_status_text(assignment.status),
            "summary_title": get_homework_summary_title(assignment.summary, assignment) if assignment.summary_id else "",
        }
        for assignment in summary_scope_assignments
    ]
    content_options = get_teacher_student_homework_content_options(portal_user, student)
    scope_assignments = get_teacher_student_homework_scope_assignments(portal_user, student)
    empty_state = build_teacher_student_homework_empty_state(
        portal_user,
        student,
        assignments=scope_assignments,
    )
    selected_content_id = normalize_positive_value((homework_form_values or {}).get("content_id"), default=0, minimum=0)
    if not selected_content_id and content_options:
        selected_content_id = content_options[0]["id"]
    selected_option = next((item for item in content_options if item["id"] == selected_content_id), None)
    return {
        "homework_items": homework_items,
        "homework_summary_items": build_homework_summary_items(assignments),
        "homework_content_options": content_options,
        "homework_content_option_ids": [item["id"] for item in content_options],
        "homework_selected_content_option": selected_option,
        "homework_scope_assignments": empty_state["scope_rows"],
        "homework_empty_state_title": empty_state["title"],
        "homework_empty_state_message": empty_state["message"],
        "homework_create_disabled_reason": empty_state["message"] if not content_options else "",
        "homework_error_message": homework_error_message,
        "homework_success_message": homework_success_message,
        "homework_modal_should_open": bool(homework_error_message),
        "homework_form_values": {
            "content_id": selected_content_id,
            "title": str((homework_form_values or {}).get("title") or (selected_option["title"] if selected_option else "")),
            "description": str((homework_form_values or {}).get("description") or ""),
            "due_date": str((homework_form_values or {}).get("due_date") or timezone.localdate().isoformat()),
        },
        "homework_summary_error_message": homework_summary_error_message,
        "homework_summary_modal_should_open": bool(homework_summary_error_message),
        "homework_summary_form_values": {
            "title": str(
                (homework_summary_form_values or {}).get("title")
                or build_default_homework_summary_title(
                    student,
                    start_date=summary_start_date,
                    end_date=summary_end_date,
                )
            ),
            "start_date": summary_start_raw,
            "end_date": summary_end_raw,
            "summary_html": str((homework_summary_form_values or {}).get("summary_html") or ""),
            "highlights": str((homework_summary_form_values or {}).get("highlights") or ""),
            "areas_for_growth": str((homework_summary_form_values or {}).get("areas_for_growth") or ""),
        },
        "homework_summary_scope_rows": summary_scope_rows,
        "homework_summary_scope_count": len(summary_scope_rows),
        "homework_summary_create_disabled_reason": "当前还没有作业，先布置作业后再上传本周总结。" if not assignments else "",
    }


def get_exam_session_status_text(status: str) -> str:
    return EXAM_SESSION_STATUS_LABELS.get(status, status or "未知")


def get_exam_session_status_tone(status: str) -> str:
    return EXAM_SESSION_STATUS_TONES.get(status, "trial")


def get_exam_session_type_text(session_type: str) -> str:
    return EXAM_SESSION_TYPE_LABELS.get(session_type, session_type or "考试")


def format_exam_score(value: object) -> str:
    return f"{value or 0}"


EXAM_QUESTION_NO_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def normalize_exam_question_no_for_sort(value: object) -> int:
    raw_text = str(value or "").strip()
    if not raw_text:
        return 0
    digit_match = re.search(r"\d+", raw_text)
    if digit_match:
        return int(digit_match.group(0))

    chinese_match = re.search(r"[零〇一二两三四五六七八九十百]+", raw_text)
    if not chinese_match:
        return 0
    text = chinese_match.group(0)
    if text == "十":
        return 10
    if "百" in text:
        left, _, right = text.partition("百")
        hundred = EXAM_QUESTION_NO_CHINESE_DIGITS.get(left, 1 if not left else 0) * 100
        return hundred + normalize_exam_question_no_for_sort(right)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = EXAM_QUESTION_NO_CHINESE_DIGITS.get(left, 1 if not left else 0) * 10
        ones = EXAM_QUESTION_NO_CHINESE_DIGITS.get(right, 0)
        return tens + ones
    value_number = 0
    for char in text:
        value_number = value_number * 10 + EXAM_QUESTION_NO_CHINESE_DIGITS.get(char, 0)
    return value_number


EXAM_MARKDOWN_MATH_REPLACEMENTS = {
    r"\leq": "≤",
    r"\le": "≤",
    r"\geq": "≥",
    r"\ge": "≥",
    r"\neq": "≠",
    r"\ne": "≠",
    r"\times": "×",
    r"\cdot": "·",
    r"\lt": "<",
    r"\gt": ">",
}


def normalize_exam_inline_math_for_display(value: object) -> str:
    normalized = str(value or "")
    for source, replacement in EXAM_MARKDOWN_MATH_REPLACEMENTS.items():
        normalized = normalized.replace(source, replacement)
    normalized = re.sub(r"\$([^$\n]+)\$", lambda match: match.group(1).strip(), normalized)
    normalized = normalized.replace(r"\(", "").replace(r"\)", "")
    return normalized


EXAM_CODE_FENCE_RE = re.compile(r"^\s*```")
EXAM_CODE_LINE_NUMBER_PIPE_RE = re.compile(r"^(\s*)\d{1,4}\s+\|\s(.*)$")
EXAM_CODE_LINE_NUMBER_SPACE_RE = re.compile(r"^(\s*)\d{1,4}\s(?=[A-Za-z_#{};/])(.*)$")
EXAM_CODE_LINE_NUMBER_ONLY_RE = re.compile(r"^(\s*)\d{1,4}\s*$")


def strip_exam_display_numbered_code_line(line: str) -> str | None:
    if EXAM_CODE_LINE_NUMBER_ONLY_RE.match(line):
        return ""
    match = EXAM_CODE_LINE_NUMBER_PIPE_RE.match(line)
    if match:
        return f"{match.group(1)}{match.group(2)}".rstrip()
    generic_match = re.match(r"^(\s*)\d{1,4}\s(.*\S)\s*$", line)
    if generic_match:
        leading_space = generic_match.group(1)
        remainder = generic_match.group(2)
        first_content = remainder.lstrip()[:1]
        if first_content in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_#{};/":
            return f"{leading_space}{remainder}".rstrip()
    return None


def strip_exam_display_code_line_numbers(value: object) -> str:
    text = str(value or "")
    if "\n" not in text and "\\n" in text and not re.search(r"""["'][^"']*\\n[^"']*["']""", text):
        text = text.replace("\\n", "\n")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    in_code_block = False
    cleaned_lines: list[str] = []
    for line in lines:
        if EXAM_CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            cleaned_lines.append(line)
            continue
        if in_code_block:
            stripped_line = strip_exam_display_numbered_code_line(line)
            if stripped_line is not None:
                cleaned_lines.append(stripped_line)
                continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def render_exam_inline_markdown_for_display(value: object) -> str:
    raw_text = normalize_exam_inline_math_for_display(value)

    code_placeholders: list[str] = []
    image_placeholders: list[str] = []

    def stash_image(match: re.Match) -> str:
        alt = str(match.group(1) or "题目图片").strip() or "题目图片"
        url = str(match.group(2) or "").strip()
        if not url:
            return ""
        image_placeholders.append(
            '<img class="exam-markdown-body__image" src="'
            + escape(url)
            + '" alt="'
            + escape(alt)
            + '">'
        )
        return f"@@IMG{len(image_placeholders) - 1}@@"

    raw_text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", stash_image, raw_text)
    escaped_text = escape(raw_text)

    def stash_inline_code(match: re.Match) -> str:
        code_placeholders.append(
            '<code class="exam-markdown-body__inline-code">' + match.group(1).strip() + "</code>"
        )
        return f"@@CODE{len(code_placeholders) - 1}@@"

    rendered = re.sub(r"`([^`\n]+)`", stash_inline_code, escaped_text)
    rendered = re.sub(r"\*\*([^*\n][^*\n]*(?:\*[^*\n]+)*)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"__([^_\n][^_\n]*(?:_[^_\n]+)*)__", r"<strong>\1</strong>", rendered)
    for index, code_html in enumerate(code_placeholders):
        rendered = rendered.replace(f"@@CODE{index}@@", code_html)
    for index, image_html in enumerate(image_placeholders):
        rendered = rendered.replace(f"@@IMG{index}@@", image_html)
    return rendered


def render_exam_markdown_for_display(value: object) -> str:
    text = strip_exam_display_code_line_numbers(value)
    if not text:
        return mark_safe("<p>当前还没有题干。</p>")

    html_parts: list[str] = []
    paragraph_lines: list[str] = []
    code_lines: list[str] = []
    in_code_block = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        stripped_lines = [line.strip() for line in paragraph_lines]
        unordered_items = []
        ordered_items = []
        for line in stripped_lines:
            unordered_match = re.match(r"^[-*]\s+(.+)$", line)
            ordered_match = re.match(r"^\d+[.)]\s+(.+)$", line)
            if unordered_match:
                unordered_items.append(unordered_match.group(1))
            if ordered_match:
                ordered_items.append(ordered_match.group(1))
        if unordered_items and len(unordered_items) == len(stripped_lines):
            html_parts.append(
                '<ul class="exam-markdown-body__list">'
                + "".join(
                    "<li>" + render_exam_inline_markdown_for_display(item) + "</li>"
                    for item in unordered_items
                )
                + "</ul>"
            )
        elif ordered_items and len(ordered_items) == len(stripped_lines):
            html_parts.append(
                '<ol class="exam-markdown-body__list">'
                + "".join(
                    "<li>" + render_exam_inline_markdown_for_display(item) + "</li>"
                    for item in ordered_items
                )
                + "</ol>"
            )
        else:
            rendered_lines = [render_exam_inline_markdown_for_display(line) for line in paragraph_lines]
            html_parts.append('<p class="exam-markdown-body__paragraph">' + "<br>".join(rendered_lines) + "</p>")
        paragraph_lines = []

    def flush_code() -> None:
        nonlocal code_lines
        html_parts.append(
            '<pre class="exam-markdown-body__code"><code>'
            + escape("\n".join(code_lines).rstrip())
            + "</code></pre>"
        )
        code_lines = []

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            opening_match = re.match(r"^```\s*([A-Za-z][\w+-]*)?[\t ]*(.*)$", stripped)
            inline_code = opening_match.group(2) if opening_match else ""
            closes_inline = inline_code.endswith("```")
            if closes_inline:
                inline_code = inline_code[:-3].rstrip()
            if in_code_block:
                if inline_code:
                    code_lines.append(inline_code)
                flush_code()
                in_code_block = False
            else:
                flush_paragraph()
                in_code_block = True
                code_lines = []
                if inline_code:
                    code_lines.append(inline_code)
                if closes_inline:
                    flush_code()
                    in_code_block = False
            continue

        if in_code_block:
            if line.rstrip().endswith("```"):
                closing_line = line.rstrip()[:-3].rstrip()
                if closing_line:
                    code_lines.append(closing_line)
                flush_code()
                in_code_block = False
                continue
            code_lines.append(line.rstrip())
            continue

        if not stripped:
            flush_paragraph()
            continue

        heading_match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading_match:
            flush_paragraph()
            html_parts.append(
                '<h4 class="exam-markdown-body__heading">'
                + render_exam_inline_markdown_for_display(heading_match.group(2))
                + "</h4>"
            )
            continue

        paragraph_lines.append(line)

    if in_code_block:
        flush_code()
    flush_paragraph()
    return mark_safe("\n".join(part for part in html_parts if part))


def extract_exam_markdown_image_paths(value: object) -> set[str]:
    paths: set[str] = set()
    for match in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", str(value or "")):
        raw_path = str(match.group(1) or "").strip()
        if not raw_path:
            continue
        normalized_path = raw_path.split("?", 1)[0].split("#", 1)[0].strip()
        if normalized_path.startswith("/media/"):
            normalized_path = normalized_path[len("/media/"):]
        paths.add(normalized_path.lstrip("/"))
    return paths


def exclude_markdown_embedded_image_paths(paths: list[str], markdown_text: object) -> list[str]:
    embedded_paths = extract_exam_markdown_image_paths(markdown_text)
    if not embedded_paths:
        return paths
    return [
        path
        for path in paths
        if str(path or "").strip().lstrip("/") not in embedded_paths
    ]


def get_exam_mode_text(mode: str) -> str:
    return EXAM_MODE_LABELS.get(mode, mode or "未知模式")


def format_datetime_input_value(value) -> str:
    if not value:
        return ""
    return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M")


def build_exam_time_rule_text(paper: ExamPaper) -> str:
    window_end = get_exam_window_end(paper)
    if paper.mode == ExamPaper.MODE_TIMED:
        start_text = format_datetime(paper.start_at) if paper.start_at else "未设置"
        end_text = format_datetime(window_end) if window_end else "未设置"
        return f"{start_text} 开始，固定 {paper.duration_minutes} 分钟，{end_text} 结束"
    end_text = format_datetime(window_end) if window_end else "未设置"
    return f"DL {end_text} 前均可进入"


def is_exam_paper_window_open(paper: ExamPaper, *, now=None) -> bool:
    current_time = now or timezone.now()
    window_end = get_exam_window_end(paper)
    if paper.mode == ExamPaper.MODE_TIMED:
        if not paper.start_at or not window_end:
            return False
        return paper.start_at <= current_time <= window_end
    if not window_end:
        return False
    return current_time <= window_end


def get_exam_bank_paper_id_from_exam_description(description: str) -> int | None:
    marker_match = re.search(r"(?:^|\n)exam_question_bank_paper_id=(\d+)(?:\n|$)", str(description or ""))
    if not marker_match:
        return None
    try:
        return int(marker_match.group(1))
    except (TypeError, ValueError):
        return None


def infer_exam_paper_subject_title(paper: ExamPaper) -> str:
    if paper.course_id and paper.course:
        return paper.course.title
    bank_paper_id = get_exam_bank_paper_id_from_exam_description(paper.description)
    if bank_paper_id:
        bank_paper = ExamQuestionBankPaper.objects.filter(id=bank_paper_id).first()
        if bank_paper is not None:
            return format_exam_subject_filter_label(infer_exam_bank_paper_subject(bank_paper))
    return "未绑定学科"


def get_exam_bank_paper_ids_with_exam_management_records(teacher: PortalUser | None = None) -> set[int]:
    bank_paper_ids: set[int] = set()
    queryset = ExamPaper.objects.filter(is_active=True).exclude(description="")
    if teacher is not None:
        queryset = queryset.filter(teacher=teacher)
    descriptions = queryset.values_list("description", flat=True)
    for description in descriptions:
        bank_paper_id = get_exam_bank_paper_id_from_exam_description(str(description or ""))
        if bank_paper_id:
            bank_paper_ids.add(bank_paper_id)
    return bank_paper_ids


def expire_stale_exam_bank_paper_knowledge_statuses(questions: list[ExamQuestionBankQuestion]) -> int:
    active_questions: list[ExamQuestionBankQuestion] = []
    for question in questions:
        full_json = question.full_json if isinstance(question.full_json, dict) else {}
        if str(full_json.get("knowledge_status") or "").strip() in {"pending", "running"}:
            active_questions.append(question)
    if not active_questions:
        return 0

    configured_minutes = int(getattr(settings, "EXAM_AI_KNOWLEDGE_STALE_MINUTES", 0) or 0)
    qwen_timeout_seconds = max(int(getattr(settings, "QWEN_TIMEOUT_SECONDS", 180)), 1)
    timeout_based_minutes = max((qwen_timeout_seconds * 2 + 59) // 60 + 1, 5)
    stale_minutes = max(configured_minutes, timeout_based_minutes)
    cutoff = timezone.now() - timedelta(minutes=stale_minutes)
    latest_active_update = max(
        (question.updated_at for question in active_questions if question.updated_at),
        default=None,
    )
    if latest_active_update and latest_active_update >= cutoff:
        return 0

    error_message = f"知识点识别任务超过 {stale_minutes} 分钟没有进展，已自动标记失败，请重新点击知识点识别。"
    expired_count = 0
    for question in active_questions:
        full_json = dict(question.full_json) if isinstance(question.full_json, dict) else {}
        if str(full_json.get("knowledge_level_1") or "").strip() and str(full_json.get("knowledge_level_2") or "").strip():
            full_json["knowledge_status"] = "done"
            full_json["knowledge_error"] = ""
        else:
            full_json["knowledge_status"] = "failed"
            full_json["knowledge_error"] = error_message
        question.full_json = full_json
        question.save(update_fields=["full_json", "updated_at"])
        expired_count += 1
    return expired_count


def collect_exam_paper_question_meta(paper: ExamPaper) -> dict[str, str]:
    questions = list(
        paper.questions.filter(is_active=True)
        .order_by("question_no", "id")
        .values("wrong_point_label", "source_snapshot_json")
    )
    knowledge_points: list[str] = []
    level_codes: list[str] = []
    for question in questions:
        snapshot = question.get("source_snapshot_json") if isinstance(question.get("source_snapshot_json"), dict) else {}
        knowledge_point = str(question.get("wrong_point_label") or snapshot.get("knowledge_point") or "").strip()
        level_code = str(snapshot.get("level_code") or "").strip()
        if knowledge_point and knowledge_point not in knowledge_points:
            knowledge_points.append(knowledge_point)
        if level_code and level_code not in level_codes:
            level_codes.append(level_code)
    bank_paper_id = get_exam_bank_paper_id_from_exam_description(paper.description)
    if bank_paper_id:
        bank_paper = ExamQuestionBankPaper.objects.filter(id=bank_paper_id, is_active=True).only("level", "title").first()
        if bank_paper:
            if bank_paper.level and bank_paper.level not in level_codes:
                level_codes.insert(0, bank_paper.level)
            if bank_paper.title and bank_paper.title not in knowledge_points:
                knowledge_points.insert(0, bank_paper.title)
    return {
        "knowledge_text": " / ".join(knowledge_points[:3]) if knowledge_points else "未归类",
        "level_text": " / ".join(level_codes[:3]) if level_codes else "未分级",
        "search_text": " ".join(knowledge_points + level_codes),
    }


def build_exam_paper_status_summary(paper: ExamPaper) -> dict[str, object]:
    in_progress_count = int(getattr(paper, "in_progress_count", 0) or 0)
    now = timezone.now()
    window_end = get_exam_window_end(paper)
    has_started = paper.status == ExamPaper.STATUS_PUBLISHED or bool(paper.access_code)
    if has_started and window_end and now > window_end:
        return {"text": "已结束", "tone": "locked", "is_running": False}
    is_running = has_started and (in_progress_count > 0 or is_exam_paper_window_open(paper, now=now))
    if is_running:
        return {"text": "进行中", "tone": "open", "is_running": True}
    return {"text": "未开始", "tone": "trial", "is_running": False}


def shorten_exam_bank_text(value: object, *, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def serialize_exam_bank_item(item: ExamQuestionBankItem) -> dict:
    options = item.options_json if isinstance(item.options_json, dict) else {}
    option_text = " / ".join(
        f"{key}. {str(options.get(key) or '').strip()}"
        for key in ["A", "B", "C", "D"]
        if str(options.get(key) or "").strip()
    )
    return {
        "id": item.id,
        "course_id": item.course_id,
        "course_title": item.course.title if item.course_id and item.course else "未绑定课程",
        "source": item.source,
        "source_text": dict(ExamQuestionBankItem.SOURCE_CHOICES).get(item.source, item.source),
        "source_label": item.source_label or dict(ExamQuestionBankItem.SOURCE_CHOICES).get(item.source, item.source),
        "level_code": item.level_code or "未分级",
        "knowledge_point": item.knowledge_point,
        "stem": item.stem,
        "stem_preview": shorten_exam_bank_text(item.stem, limit=96),
        "options_text": option_text,
        "correct_answer": item.correct_answer,
        "analysis": item.analysis,
        "analysis_preview": shorten_exam_bank_text(item.analysis, limit=96),
        "score": format_exam_score(item.score),
        "created_at_text": format_datetime(item.created_at),
        "search_text": " ".join(
            [
                item.source_label,
                item.source,
                item.level_code,
                item.knowledge_point,
                item.stem,
                option_text,
                item.correct_answer,
                item.analysis,
            ]
        ).lower(),
    }


def infer_exam_bank_paper_subject(paper: ExamQuestionBankPaper) -> str:
    raw_text = " ".join(
        [
            str(paper.level or ""),
            str(paper.title or ""),
            str(paper.source_file or ""),
            str(paper.source_pdf_id or ""),
        ]
    ).lower()
    if "python" in raw_text:
        return "Python"
    if "scratch" in raw_text or "图形化" in raw_text:
        return "Scratch"
    if "无人机" in raw_text or "uav" in raw_text:
        return "无人机"
    if "ai" in raw_text or "人工智能" in raw_text:
        return "AI"
    if "gesp" in raw_text or "csp" in raw_text or "c++" in raw_text or "cpp" in raw_text:
        return "C++"
    return "未绑定学科"


def normalize_exam_subject_key(value: object) -> str:
    return normalize_knowledge_map_subject(value)


def split_teacher_subject_text(value: object) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"[;；、,，/\n]+", str(value or ""))
        if part.strip()
    ]


def format_exam_subject_filter_label(value: object) -> str:
    normalized = normalize_exam_subject_key(value)
    if normalized == "cpp":
        return "C++"
    if normalized == "python":
        return "Python"
    if normalized == "scratch":
        return "Scratch"
    if normalized == "drone":
        return "无人机"
    if normalized == "ai":
        return "AI"
    return str(value or "").strip()


def build_teacher_exam_subject_scope(portal_user: PortalUser) -> dict[str, object]:
    all_subjects = ["C++", "Python", "无人机", "AI", "Scratch"]
    if portal_user.role == PortalUser.ROLE_PRINCIPAL:
        return {
            "is_restricted": False,
            "allowed_keys": set(),
            "subject_filter_options": all_subjects,
            "default_subject": "",
        }

    teacher = sync_teacher_profile(portal_user)
    ordered_subjects: list[str] = []

    def add_subject(value: object) -> None:
        label = format_exam_subject_filter_label(value)
        key = normalize_exam_subject_key(label)
        existing_keys = {normalize_exam_subject_key(item) for item in ordered_subjects}
        if key and label and key not in existing_keys:
            ordered_subjects.append(label)

    if teacher is not None:
        for part in split_teacher_subject_text(teacher.subject):
            add_subject(part)
        for course in teacher.courses.all().order_by("id"):
            add_subject(course.title)
    for assignment in get_teacher_active_assignments(portal_user):
        if assignment.course_id and assignment.course:
            add_subject(assignment.course.title)

    allowed_keys = {normalize_exam_subject_key(subject) for subject in ordered_subjects if normalize_exam_subject_key(subject)}
    return {
        "is_restricted": True,
        "allowed_keys": allowed_keys,
        "subject_filter_options": ordered_subjects,
        "default_subject": ordered_subjects[0] if ordered_subjects else "",
    }


def teacher_can_operate_exam_subject(portal_user: PortalUser, subject_title: object) -> bool:
    if portal_user.role == PortalUser.ROLE_PRINCIPAL:
        return True
    scope = build_teacher_exam_subject_scope(portal_user)
    allowed_keys = scope.get("allowed_keys") or set()
    if not allowed_keys:
        return False
    return normalize_exam_subject_key(subject_title) in allowed_keys


def serialize_available_exam_bank_paper(
    paper: ExamQuestionBankPaper,
    *,
    publisher_name: str = "",
    published_bank_paper_ids: set[int] | None = None,
) -> dict:
    subject_title = infer_exam_bank_paper_subject(paper)
    normalized_publisher_name = publisher_name or dict(ExamQuestionBankPaper.SOURCE_CHOICES).get(
        paper.source, paper.source or "系统"
    )
    created_at_value = timezone.localtime(paper.created_at).date().isoformat() if paper.created_at else ""
    has_exam_management_record = (
        paper.id in published_bank_paper_ids
        if published_bank_paper_ids is not None
        else paper.id in get_exam_bank_paper_ids_with_exam_management_records()
    )
    analysis_status_labels = dict(ExamQuestionBankPaper.ANALYSIS_STATUS_CHOICES)
    analysis_status = paper.analysis_generation_status or ExamQuestionBankPaper.ANALYSIS_STATUS_NOT_STARTED
    analysis_total_count = int(paper.analysis_generation_total_count or 0)
    analysis_done_count = int(paper.analysis_generation_done_count or 0)
    analysis_failed_count = int(paper.analysis_generation_failed_count or 0)
    if analysis_status == ExamQuestionBankPaper.ANALYSIS_STATUS_RUNNING:
        analysis_status_text = f"解析中 {analysis_done_count}/{analysis_total_count or '?'}"
    elif analysis_status == ExamQuestionBankPaper.ANALYSIS_STATUS_COMPLETED:
        analysis_status_text = "解析完成"
    elif analysis_status == ExamQuestionBankPaper.ANALYSIS_STATUS_PARTIAL:
        analysis_status_text = f"部分完成 {analysis_done_count}/{analysis_total_count or '?'}"
    elif analysis_status == ExamQuestionBankPaper.ANALYSIS_STATUS_FAILED:
        analysis_status_text = "解析失败"
    else:
        analysis_status_text = analysis_status_labels.get(analysis_status, "未生成")
    knowledge_questions = list(paper.questions.all())
    expire_stale_exam_bank_paper_knowledge_statuses(knowledge_questions)
    knowledge_total_count = len(knowledge_questions)
    knowledge_done_count = 0
    knowledge_running_count = 0
    knowledge_failed_count = 0
    for question in knowledge_questions:
        full_json = question.full_json if isinstance(question.full_json, dict) else {}
        if str(full_json.get("knowledge_level_1") or "").strip() and str(full_json.get("knowledge_level_2") or "").strip():
            knowledge_done_count += 1
        knowledge_status = str(full_json.get("knowledge_status") or "").strip()
        if knowledge_status in {"pending", "running"}:
            knowledge_running_count += 1
        elif knowledge_status == "failed":
            knowledge_failed_count += 1
    if knowledge_total_count <= 0:
        knowledge_status_text = "无题目"
        knowledge_status_tone = "trial"
    elif knowledge_running_count > 0:
        knowledge_status_text = f"识别中 {knowledge_done_count}/{knowledge_total_count}"
        knowledge_status_tone = "trial"
    elif knowledge_done_count <= 0:
        knowledge_status_text = "识别失败" if knowledge_failed_count else "未识别"
        knowledge_status_tone = "trial"
    elif knowledge_done_count >= knowledge_total_count:
        knowledge_status_text = "识别完成"
        knowledge_status_tone = "open"
    else:
        knowledge_status_text = f"部分识别 {knowledge_done_count}/{knowledge_total_count}"
        knowledge_status_tone = "trial"
    can_generate_analysis = analysis_status in {
        ExamQuestionBankPaper.ANALYSIS_STATUS_NOT_STARTED,
        ExamQuestionBankPaper.ANALYSIS_STATUS_FAILED,
    }
    can_generate_knowledge = (
        knowledge_total_count > 0
        and knowledge_running_count == 0
        and knowledge_done_count < knowledge_total_count
    )
    return {
        "id": paper.id,
        "paper_id": paper.id,
        "title": paper.title,
        "subject_title": subject_title,
        "level_text": paper.level or "未分级",
        "publisher_name": normalized_publisher_name,
        "created_at_text": format_datetime(paper.created_at),
        "created_at_value": created_at_value,
        "source_pdf_id": paper.source_pdf_id,
        "source_file": paper.source_file,
        "analysis_status": analysis_status,
        "analysis_status_text": analysis_status_text,
        "analysis_total_count": analysis_total_count,
        "analysis_done_count": analysis_done_count,
        "analysis_failed_count": analysis_failed_count,
        "analysis_error": paper.analysis_generation_error,
        "can_generate_analysis": can_generate_analysis,
        "analysis_action_disabled_reason": "" if can_generate_analysis else "解析已开始处理，只有解析失败时才允许重新生成。",
        "edit_href": reverse("teacher-exam-bank-paper-edit", args=[paper.id]),
        "knowledge_status_text": knowledge_status_text,
        "knowledge_status_tone": knowledge_status_tone,
        "knowledge_total_count": knowledge_total_count,
        "knowledge_done_count": knowledge_done_count,
        "knowledge_running_count": knowledge_running_count,
        "knowledge_failed_count": knowledge_failed_count,
        "can_generate_knowledge": can_generate_knowledge,
        "knowledge_action_disabled_reason": "" if can_generate_knowledge else "知识点识别已开始处理，只有识别失败时才允许重新识别。",
        "operation_label": "发布",
        "has_exam_management_record": has_exam_management_record,
        "delete_label": "删除" if has_exam_management_record else "硬删除",
        "delete_confirm_message": (
            "这张试卷已经发布过，删除后会进入已删除试卷，可恢复。是否继续？"
            if has_exam_management_record
            else "这张试卷还没有发布过，将硬删除题库快照和对应识别任务，之后可重新上传识别。是否继续？"
        ),
        "search_text": " ".join(
            [
                paper.title,
                subject_title,
                paper.level or "",
                normalized_publisher_name,
                paper.source_file or "",
                paper.source_pdf_id or "",
                analysis_status_text,
                knowledge_status_text,
            ]
        ).lower(),
    }


def build_exam_option_items(
    options: dict[str, object],
    *,
    selected_answer: str = "",
    correct_answer: str = "",
) -> list[dict[str, object]]:
    normalized_selected_answer = str(selected_answer or "").strip().upper()
    normalized_correct_answer = str(correct_answer or "").strip().upper()
    option_items: list[dict[str, object]] = []
    for key in ["A", "B", "C", "D"]:
        raw_text = str(options.get(key) or "").strip()
        if not raw_text:
            continue
        formatted = format_homework_option_display(raw_text)
        is_selected = key == normalized_selected_answer and bool(normalized_selected_answer)
        is_correct_answer = key == normalized_correct_answer and bool(normalized_correct_answer)
        option_items.append(
            {
                "key": key,
                "text": formatted["text"],
                "display_text": formatted["display_text"],
                "display_html": render_exam_markdown_for_display(raw_text),
                "is_code_option": formatted["is_code_option"],
                "is_selected": is_selected,
                "is_correct_answer": is_correct_answer,
                "is_wrong_selected": is_selected and normalized_selected_answer != normalized_correct_answer,
            }
        )
    return option_items


def build_exam_analysis_block_items(question: ExamQuestion) -> list[dict[str, object]]:
    source_labels = {
        ExamQuestionAnalysisBlock.SOURCE_AI: "AI 解析",
        ExamQuestionAnalysisBlock.SOURCE_TEACHER: "老师解析",
        ExamQuestionAnalysisBlock.SOURCE_STUDENT: "学生贡献",
        ExamQuestionAnalysisBlock.SOURCE_MERGED: "汇总解析",
    }
    blocks = list(question.analysis_blocks.filter(is_visible=True).order_by("sort_order", "id"))
    if not blocks:
        legacy_analysis = str(question.analysis or "当前老师没有补充解析。").strip()
        return [
            {
                "id": 0,
                "source_type": "legacy",
                "source_label": "解析",
                "content_md": legacy_analysis,
                "content_html": render_exam_markdown_for_display(legacy_analysis),
                "contributors_text": "",
                "is_ai": False,
            }
        ]
    items = []
    for block in blocks:
        contributors = block.contributors_json if isinstance(block.contributors_json, list) else []
        contributor_names = [
            str(item.get("name") or "").strip()
            for item in contributors
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        items.append(
            {
                "id": block.id,
                "source_type": block.source_type,
                "source_label": source_labels.get(block.source_type, "解析"),
                "content_md": block.content_md,
                "content_html": render_exam_markdown_for_display(block.content_md),
                "contributors_text": "、".join(contributor_names),
                "is_ai": block.source_type == ExamQuestionAnalysisBlock.SOURCE_AI,
            }
        )
    return items


def build_exam_analysis_html(question: ExamQuestion) -> str:
    rendered_blocks = [
        str(block["content_html"])
        for block in build_exam_analysis_block_items(question)
        if str(block.get("content_md") or "").strip()
    ]
    return mark_safe("\n".join(rendered_blocks))


def build_pending_analysis_suggestion_items(question: ExamQuestion) -> list[dict[str, object]]:
    suggestions = (
        question.analysis_suggestions.select_related("student")
        .filter(status=ExamQuestionAnalysisSuggestion.STATUS_PENDING)
        .order_by("created_at", "id")
    )
    return [
        {
            "id": suggestion.id,
            "student_name": suggestion.student.display_name,
            "content_md": suggestion.content_md,
            "content_html": render_exam_markdown_for_display(suggestion.content_md),
            "created_at_text": format_datetime(suggestion.created_at),
        }
        for suggestion in suggestions
    ]


def get_exam_analysis_suggestion_status_text(status: str) -> str:
    return dict(ExamQuestionAnalysisSuggestion.STATUS_CHOICES).get(status, "待审核")


def truncate_plain_text(value: str, limit: int = 80) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(limit - 1, 0)]}..."


def build_student_analysis_suggestion_items(question: ExamQuestion, student: Student) -> list[dict[str, object]]:
    suggestions = (
        question.analysis_suggestions.select_related("reviewed_by")
        .filter(student=student)
        .order_by("-created_at", "-id")
    )
    return [
        {
            "id": suggestion.id,
            "content_md": suggestion.content_md,
            "content_html": render_exam_markdown_for_display(suggestion.content_md),
            "status": suggestion.status,
            "status_text": get_exam_analysis_suggestion_status_text(suggestion.status),
            "is_accepted": suggestion.status == ExamQuestionAnalysisSuggestion.STATUS_ACCEPTED,
            "is_rejected": suggestion.status == ExamQuestionAnalysisSuggestion.STATUS_REJECTED,
            "created_at_text": format_datetime(suggestion.created_at),
            "reviewed_at_text": format_datetime(suggestion.reviewed_at) if suggestion.reviewed_at else "",
        }
        for suggestion in suggestions
    ]


def build_teacher_analysis_message_rows(portal_user: PortalUser) -> list[dict[str, object]]:
    suggestions = ExamQuestionAnalysisSuggestion.objects.select_related(
        "student",
        "question",
        "question__paper",
        "session",
    ).filter(
        question__paper__is_active=True,
        question__is_active=True,
        status=ExamQuestionAnalysisSuggestion.STATUS_PENDING,
    )
    if portal_user.role != PortalUser.ROLE_PRINCIPAL:
        suggestions = suggestions.filter(question__paper__teacher=portal_user)
    suggestions = (
        suggestions
        .order_by("-created_at", "-id")
    )
    rows = []
    for suggestion in suggestions:
        question = suggestion.question
        paper = question.paper
        rows.append(
            {
                "id": suggestion.id,
                "student_name": suggestion.student.display_name,
                "paper_title": paper.title,
                "question_no": question.question_no,
                "content_preview": truncate_plain_text(suggestion.content_md, 80),
                "created_at_text": format_datetime(suggestion.created_at),
                "detail_href": f"{reverse('teacher-exam-detail', args=[paper.id])}#analysis-suggestion-{suggestion.id}",
                "search_text": " ".join(
                    [
                        suggestion.student.display_name,
                        paper.title,
                        str(question.question_no),
                        suggestion.content_md,
                        format_datetime(suggestion.created_at),
                    ]
                ),
            }
        )
    return rows


def build_student_reviewed_analysis_message_rows(student: Student) -> list[dict[str, object]]:
    suggestions = (
        ExamQuestionAnalysisSuggestion.objects.select_related(
            "question",
            "question__paper",
            "session",
        )
        .filter(
            student=student,
            session__is_active=True,
            question__paper__is_active=True,
            question__is_active=True,
            status__in=[
                ExamQuestionAnalysisSuggestion.STATUS_ACCEPTED,
                ExamQuestionAnalysisSuggestion.STATUS_REJECTED,
            ],
        )
        .order_by("-reviewed_at", "-updated_at", "-id")
    )
    rows = []
    for suggestion in suggestions:
        question = suggestion.question
        paper = question.paper
        status_text = get_exam_analysis_suggestion_status_text(suggestion.status)
        rows.append(
            {
                "id": suggestion.id,
                "status": suggestion.status,
                "status_text": status_text,
                "paper_title": paper.title,
                "question_no": question.question_no,
                "reviewed_at_text": format_datetime(suggestion.reviewed_at or suggestion.updated_at),
                "detail_href": f"{reverse('student-exam-detail', args=[suggestion.session_id])}#student-analysis-suggestion-{suggestion.id}",
                "summary": f"{paper.title} · 第 {question.question_no} 题 · {status_text}",
            }
        )
    return rows


def build_student_site_message_rows(student: Student) -> list[dict[str, object]]:
    messages = student.site_messages.select_related("source_suggestion").order_by("-created_at", "-id")
    return [
        {
            "id": message.id,
            "title": message.title,
            "body": message.body,
            "target_href": message.target_href,
            "open_href": reverse("student-site-message-open", args=[message.id]),
            "is_read": message.is_read,
            "status_text": "已读" if message.is_read else "未读",
            "created_at_text": format_datetime(message.created_at),
            "read_at_text": format_datetime(message.read_at) if message.read_at else "",
        }
        for message in messages
    ]


def build_student_site_message_context(portal_user: PortalUser) -> dict:
    student = get_student_by_user(portal_user)
    message_rows = build_student_site_message_rows(student)
    unread_count = sum(1 for row in message_rows if not row["is_read"])
    return {
        "page_title": "消息",
        "page_description": "老师处理你的解析挑战后，会在这里生成一条站内信。",
        "breadcrumbs": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "消息"},
        ],
        "summary_cards": [
            {"label": "未读消息", "value": f"{unread_count} 条", "hint": "点击消息后自动标记为已读"},
            {"label": "全部消息", "value": f"{len(message_rows)} 条", "hint": "包含已读和未读"},
            {"label": "解析挑战", "value": f"{len(message_rows)} 条", "hint": "当前只接入解析挑战处理通知"},
            {"label": "入口", "value": "站内信", "hint": "每条消息可跳到对应题目"},
        ],
        "message_rows": message_rows,
        "empty_message": "当前还没有站内信。",
        "back_href": reverse("student-courses"),
    }


def serialize_exam_question(
    question: ExamQuestion,
    *,
    answer: ExamSubmissionAnswer | None = None,
    show_feedback: bool = False,
    requires_explanation: bool = False,
    selected_answer_override: str | None = None,
    student_explanation_override: str | None = None,
    include_teacher_analysis_suggestions: bool = False,
    analysis_suggestion_student: Student | None = None,
) -> dict:
    options = question.options_json if isinstance(question.options_json, dict) else {}
    selected_answer = (
        str(selected_answer_override or "").strip().upper()
        if selected_answer_override is not None
        else (str(answer.selected_answer or "").strip().upper() if answer else "")
    )
    correct_answer = str(question.correct_answer or "").strip().upper()
    student_explanation = (
        str(student_explanation_override or "").strip()
        if student_explanation_override is not None
        else (str(answer.explanation_text or "").strip() if answer else "")
    )
    question_type_text = {
        ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE: "单选题",
        getattr(ExamQuestion, "QUESTION_TYPE_TRUE_FALSE", "true_false"): "判断题",
        getattr(ExamQuestion, "QUESTION_TYPE_PROGRAMMING", "programming"): "编程题",
    }.get(question.question_type, "考试题")
    is_gradable = is_auto_gradable_exam_question(question)
    snapshot = question.source_snapshot_json if isinstance(question.source_snapshot_json, dict) else {}
    raw_image_paths = snapshot.get("image_paths") if isinstance(snapshot.get("image_paths"), list) else []
    image_paths = [
        str(path or "").strip()
        for path in raw_image_paths
        if str(path or "").strip()
    ]
    if question.image_path and question.image_path not in image_paths:
        image_paths.insert(0, question.image_path)
    material_image_paths = [
        str(path or "").strip()
        for path in (snapshot.get("material_image_paths") if isinstance(snapshot.get("material_image_paths"), list) else [])
        if str(path or "").strip()
    ]
    question_image_paths = [
        str(path or "").strip()
        for path in (snapshot.get("question_image_paths") if isinstance(snapshot.get("question_image_paths"), list) else [])
        if str(path or "").strip()
    ]
    image_paths = exclude_markdown_embedded_image_paths(image_paths, question.stem)
    material_image_paths = exclude_markdown_embedded_image_paths(material_image_paths, question.stem)
    question_image_paths = exclude_markdown_embedded_image_paths(question_image_paths, question.stem)
    analysis_blocks = build_exam_analysis_block_items(question)
    knowledge_level_1 = str(snapshot.get("knowledge_level_1") or "").strip()
    knowledge_level_2 = str(snapshot.get("knowledge_level_2") or "").strip()
    knowledge_level_3 = str(snapshot.get("knowledge_level_3") or "").strip()
    knowledge_parts = [part for part in [knowledge_level_1, knowledge_level_2, knowledge_level_3] if part]
    knowledge_display = " / ".join(knowledge_parts)
    if not knowledge_display:
        knowledge_display = str(question.wrong_point_label or snapshot.get("knowledge_point") or "未标注").strip() or "未标注"
    pending_suggestions = (
        build_pending_analysis_suggestion_items(question)
        if include_teacher_analysis_suggestions
        else []
    )
    student_analysis_suggestions = (
        build_student_analysis_suggestion_items(question, analysis_suggestion_student)
        if analysis_suggestion_student
        else []
    )
    return {
        "id": question.id,
        "question_no": question.question_no,
        "question_type": question.question_type,
        "question_type_text": question_type_text,
        "stem": question.stem,
        "stem_html": render_exam_markdown_for_display(question.stem),
        "option_items": build_exam_option_items(
            options,
            selected_answer=selected_answer,
            correct_answer=correct_answer if show_feedback else "",
        ),
        "correct_answer": correct_answer if show_feedback and is_gradable else "",
        "analysis": question.analysis or "\n\n".join(str(block["content_md"]) for block in analysis_blocks),
        "analysis_html": mark_safe("\n".join(str(block["content_html"]) for block in analysis_blocks)),
        "analysis_blocks": analysis_blocks,
        "pending_analysis_suggestions": pending_suggestions,
        "pending_analysis_suggestion_count": len(pending_suggestions),
        "student_analysis_suggestions": student_analysis_suggestions,
        "student_analysis_suggestion_count": len(student_analysis_suggestions),
        "is_important": question.is_important,
        "important_note": question.important_note.strip(),
        "important_note_html": render_exam_markdown_for_display(question.important_note.strip()) if question.important_note.strip() else "",
        "score": format_exam_score(question.score),
        "wrong_point_label": question.wrong_point_label or "未标注",
        "knowledge_level_1": knowledge_level_1,
        "knowledge_level_2": knowledge_level_2,
        "knowledge_level_3": knowledge_level_3,
        "knowledge_display": knowledge_display,
        "image_path": question.image_path,
        "image_paths": image_paths,
        "material_image_paths": material_image_paths,
        "question_image_paths": question_image_paths,
        "display_mode": str(snapshot.get("display_mode") or ""),
        "material_group_no": int(snapshot.get("material_group_no") or 0),
        "student_answer": selected_answer,
        "student_answer_text": (student_explanation if question.question_type == ExamQuestion.QUESTION_TYPE_PROGRAMMING else selected_answer) or "未作答",
        "programming_submission_text": student_explanation,
        "programming_submission_html": render_exam_markdown_for_display(student_explanation) if student_explanation else "",
        "student_explanation": student_explanation,
        "student_explanation_html": render_exam_markdown_for_display(student_explanation) if student_explanation else "",
        "is_correct": bool(answer and answer.is_correct),
        "is_wrong": bool(show_feedback and is_gradable and (not answer or not answer.is_correct)),
        "is_gradable": is_gradable,
        "requires_explanation": requires_explanation,
        "show_feedback": show_feedback,
    }


def serialize_exam_session(session: ExamSession) -> dict:
    paper = session.paper
    scope = session.question_scope_json if isinstance(session.question_scope_json, dict) else {}
    entry_state = build_exam_entry_state(session)
    window_end = entry_state.get("window_end")
    is_finished = session.status in {
        ExamSession.STATUS_SUBMITTED,
        ExamSession.STATUS_AUTO_CHECKED,
        ExamSession.STATUS_EXPIRED,
        ExamSession.STATUS_INVALIDATED,
    }
    return {
        "id": session.id,
        "paper_id": paper.id,
        "title": str(scope.get("display_title") or "").strip() or paper.title,
        "description": paper.description or "当前老师没有补充考试说明。",
        "course_title": paper.course.title if paper.course_id and paper.course else "未绑定课程",
        "teacher_name": paper.teacher.full_name or paper.teacher.username,
        "mode": paper.mode,
        "mode_text": get_exam_mode_text(paper.mode),
        "session_type": session.session_type,
        "session_type_text": get_exam_session_type_text(session.session_type),
        "requires_explanations": session.session_type == ExamSession.SESSION_TYPE_WRONG_PRACTICE,
        "time_rule_text": build_exam_time_rule_text(paper),
        "duration_minutes": paper.duration_minutes,
        "start_at_text": format_datetime(paper.start_at) if paper.start_at else "未设置",
        "end_at_text": format_datetime(window_end) if window_end else "未设置",
        "proctoring_enabled": paper.proctoring_enabled,
        "status": session.status,
        "status_text": get_exam_session_status_text(session.status),
        "status_tone": get_exam_session_status_tone(session.status),
        "total_count": session.total_count,
        "correct_count": session.correct_count,
        "wrong_count": session.wrong_count,
        "total_score": format_exam_score(session.total_score),
        "earned_score": format_exam_score(session.earned_score),
        "switch_count": session.switch_count,
        "created_at_text": format_datetime(session.created_at),
        "started_at_text": format_datetime(session.started_at) if session.started_at else "未开始",
        "submitted_at_text": format_datetime(session.submitted_at) if session.submitted_at else "未提交",
        "is_finished": is_finished,
        "is_in_progress": session.status == ExamSession.STATUS_IN_PROGRESS,
        "can_start": bool(entry_state["can_start"]) and session.status == ExamSession.STATUS_ASSIGNED,
        "entry_message": str(entry_state["message"]),
    }


def build_teacher_exam_course_options(portal_user: PortalUser, student: Student) -> list[dict]:
    options = []
    seen_course_ids = set()
    assignments = (
        TeacherStudentAssignment.objects.select_related("course")
        .filter(teacher=portal_user, student=student, is_active=True)
        .order_by("course_id", "level_code", "id")
    )
    for assignment in assignments:
        if assignment.course_id in seen_course_ids:
            continue
        seen_course_ids.add(assignment.course_id)
        options.append(
            {
                "id": assignment.course_id,
                "title": assignment.course.title,
                "label": f"{assignment.course.title} / {assignment.level_code}",
            }
        )
    return options


def build_teacher_student_exam_context(
    portal_user: PortalUser,
    student: Student,
    *,
    exam_form_values: dict[str, object] | None = None,
    exam_error_message: str = "",
    exam_success_message: str = "",
) -> dict:
    sessions = list(
        ExamSession.objects.select_related("paper", "paper__teacher", "paper__course")
        .filter(paper__teacher=portal_user, student=student, is_active=True, paper__is_active=True)
        .order_by("-created_at", "-id")
    )
    exam_items = [serialize_exam_session(session) for session in sessions]
    for item in exam_items:
        item["teacher_detail_href"] = reverse("teacher-student-exam-detail", args=[student.id, item["id"]])
    completed_count = sum(1 for session in sessions if session.status == ExamSession.STATUS_AUTO_CHECKED)
    in_progress_count = sum(1 for session in sessions if session.status == ExamSession.STATUS_IN_PROGRESS)
    assigned_count = sum(1 for session in sessions if session.status == ExamSession.STATUS_ASSIGNED)
    course_options = build_teacher_exam_course_options(portal_user, student)
    selected_course_id = normalize_positive_value((exam_form_values or {}).get("course_id"), default=0, minimum=0)
    if not selected_course_id and course_options:
        selected_course_id = course_options[0]["id"]
    return {
        "exam_items": exam_items,
        "exam_summary_items": [
            {"label": "考试总数", "value": f"{len(sessions)} 场", "hint": "当前学生下的考试记录"},
            {"label": "待开始", "value": f"{assigned_count} 场", "hint": "学生还没有开始的考试"},
            {"label": "考试中", "value": f"{in_progress_count} 场", "hint": "学生已打开但未交卷"},
            {"label": "已判分", "value": f"{completed_count} 场", "hint": "已提交并自动判分"},
        ],
        "exam_course_options": course_options,
        "exam_create_disabled_reason": "当前学生不在你的负责课程范围内，暂时不能创建考试。" if not course_options else "",
        "exam_error_message": exam_error_message,
        "exam_success_message": exam_success_message,
        "exam_modal_should_open": bool(exam_error_message),
        "exam_form_values": {
            "course_id": selected_course_id,
            "title": str((exam_form_values or {}).get("title") or "阶段测验"),
            "description": str((exam_form_values or {}).get("description") or ""),
            "duration_minutes": str((exam_form_values or {}).get("duration_minutes") or "60"),
            "proctoring_enabled": bool((exam_form_values or {}).get("proctoring_enabled")),
            "questions": list((exam_form_values or {}).get("questions") or []),
        },
    }


def build_teacher_exam_page_context(
    portal_user: PortalUser,
    *,
    form_values: dict[str, object] | None = None,
    error_message: str = "",
    success_message: str = "",
) -> dict:
    assignments = list(get_teacher_active_assignments(portal_user))
    exam_subject_scope = build_teacher_exam_subject_scope(portal_user)
    allowed_exam_subject_keys = set(exam_subject_scope.get("allowed_keys") or set())
    default_exam_subject_filter = str(exam_subject_scope.get("default_subject") or "")
    is_exam_subject_restricted = bool(exam_subject_scope.get("is_restricted"))
    assignments_by_course: dict[int, list[TeacherStudentAssignment]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_course[assignment.course_id].append(assignment)

    course_options = []
    for course_id, course_assignments in assignments_by_course.items():
        course = course_assignments[0].course
        unique_student_ids = {assignment.student_id for assignment in course_assignments}
        course_options.append(
            {
                "id": course_id,
                "title": course.title,
                "label": f"{course.title} / {len(unique_student_ids)} 人",
                "student_count": len(unique_student_ids),
            }
        )
    course_options.sort(key=lambda item: item["title"])
    selected_course_id = normalize_positive_value((form_values or {}).get("course_id"), default=0, minimum=0)
    if not selected_course_id and course_options:
        selected_course_id = course_options[0]["id"]

    selected_student_ids = {
        normalize_positive_value(value, default=0, minimum=1)
        for value in ((form_values or {}).get("student_ids") or [])
    }
    selected_student_ids.discard(0)
    selected_question_ids = [
        normalize_positive_value(value, default=0, minimum=1)
        for value in ((form_values or {}).get("question_bank_item_ids") or [])
    ]
    selected_question_ids = [item_id for item_id in selected_question_ids if item_id]
    student_options = []
    seen_pairs = set()
    for assignment in assignments:
        pair_key = (assignment.course_id, assignment.student_id)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        is_default_selected = assignment.course_id == selected_course_id and not selected_student_ids
        student_options.append(
            {
                "id": assignment.student_id,
                "course_id": assignment.course_id,
                "name": assignment.student.display_name,
                "grade": assignment.student.grade or "待补充",
                "scope": f"{assignment.course.title} / {assignment.level_code}",
                "parent_phone": assignment.student.parent_user.phone if assignment.student.parent_user else "",
                "is_selected": assignment.student_id in selected_student_ids or is_default_selected,
            }
        )

    teacher_course_ids = sorted(assignments_by_course.keys())
    question_bank_items = list(
        ExamQuestionBankItem.objects.select_related("course", "content")
        .filter(course_id__in=teacher_course_ids, is_active=True)
        .order_by("course_id", "level_code", "knowledge_point", "id")
    )
    question_bank_rows = [serialize_exam_bank_item(item) for item in question_bank_items]
    available_papers = list(
        ExamQuestionBankPaper.objects.filter(is_active=True)
        .prefetch_related("questions")
        .order_by("-year", "-month", "level", "source_pdf_id", "id")
    )
    current_teacher_name = portal_user.full_name or portal_user.username
    published_bank_paper_ids = get_exam_bank_paper_ids_with_exam_management_records()
    current_teacher_published_bank_paper_ids = get_exam_bank_paper_ids_with_exam_management_records(portal_user)
    available_paper_rows = [
        serialize_available_exam_bank_paper(
            paper,
            publisher_name=current_teacher_name,
            published_bank_paper_ids=published_bank_paper_ids,
        )
        for paper in available_papers
    ]
    for row in available_paper_rows:
        subject_allowed = (
            True
            if not is_exam_subject_restricted
            else normalize_exam_subject_key(row.get("subject_title")) in allowed_exam_subject_keys
        )
        subject_restricted_reason = "" if subject_allowed else "这张试卷不属于当前老师负责的学科，不能操作。"
        row["can_operate_subject"] = subject_allowed
        row["subject_operation_disabled_reason"] = subject_restricted_reason
        row["can_generate_analysis"] = bool(row.get("can_generate_analysis")) and subject_allowed
        if subject_restricted_reason:
            row["analysis_action_disabled_reason"] = subject_restricted_reason
        row["can_generate_knowledge"] = bool(row.get("can_generate_knowledge")) and subject_allowed
        if subject_restricted_reason:
            row["knowledge_action_disabled_reason"] = subject_restricted_reason
        row["can_publish_to_exam_management"] = (
            subject_allowed
            and int(row["id"]) not in current_teacher_published_bank_paper_ids
        )
        if not subject_allowed:
            row["publish_disabled_reason"] = subject_restricted_reason
            row["delete_disabled_reason"] = subject_restricted_reason
            row["edit_disabled_reason"] = subject_restricted_reason
        elif int(row["id"]) in current_teacher_published_bank_paper_ids:
            row["publish_disabled_reason"] = "已在试卷管理中，删除后可再次发布"
        else:
            row["publish_disabled_reason"] = ""
            row["delete_disabled_reason"] = ""
            row["edit_disabled_reason"] = ""
    teacher_filter_options = [
        {
            "id": teacher.id,
            "label": teacher.full_name or teacher.username,
        }
        for teacher in PortalUser.objects.filter(role=PortalUser.ROLE_TEACHER, is_active=True).order_by("full_name", "id")
    ]
    subject_filter_options = list(exam_subject_scope.get("subject_filter_options") or [])
    if not subject_filter_options:
        subject_filter_options = [] if is_exam_subject_restricted else ["C++", "Python", "无人机", "AI", "Scratch"]
    level_filter_options_by_subject: dict[str, list[str]] = {subject: [] for subject in subject_filter_options}

    def add_level_filter_option(subject_title: str, level_code: object) -> None:
        normalized_subject = str(subject_title or "").strip()
        normalized_level = str(level_code or "").strip()
        if not normalized_subject or not normalized_level:
            return
        subject_key = next(
            (subject for subject in subject_filter_options if subject.lower() == normalized_subject.lower()),
            normalized_subject,
        )
        level_filter_options_by_subject.setdefault(subject_key, [])
        if normalized_level not in level_filter_options_by_subject[subject_key]:
            level_filter_options_by_subject[subject_key].append(normalized_level)

    course_levels = (
        CourseLevel.objects.select_related("category", "category__course")
        .filter(is_active=True, category__is_active=True)
        .order_by("category__course__title", "category__sort_order", "category_id", "sort_order", "id")
    )
    for level in course_levels:
        if is_exam_subject_restricted and normalize_exam_subject_key(level.category.course.title if level.category_id and level.category and level.category.course else "") not in allowed_exam_subject_keys:
            continue
        add_level_filter_option(
            level.category.course.title if level.category_id and level.category and level.category.course else "",
            level.code or level.title,
        )
    for assignment in assignments:
        add_level_filter_option(assignment.course.title if assignment.course_id and assignment.course else "", assignment.level_code)
    for item in question_bank_items:
        add_level_filter_option(item.course.title if item.course_id and item.course else "", item.level_code)
    for row in available_paper_rows:
        if is_exam_subject_restricted and normalize_exam_subject_key(row["subject_title"]) not in allowed_exam_subject_keys:
            continue
        add_level_filter_option(row["subject_title"], row["level_text"])
    if not is_exam_subject_restricted or "cpp" in allowed_exam_subject_keys:
        for fallback_level in ["CSP-J", "CSP-S", *[f"GESP{index}" for index in range(1, 9)]]:
            add_level_filter_option("C++", fallback_level)
    level_filter_options = []
    for levels in level_filter_options_by_subject.values():
        for level in levels:
            if level not in level_filter_options:
                level_filter_options.append(level)
    knowledge_management_rows = build_exam_knowledge_management_rows(portal_user)
    teacher_profile = sync_teacher_profile(portal_user)
    default_knowledge_subject = normalize_teacher_subject_for_knowledge(
        teacher_profile.subject if teacher_profile is not None else ""
    )
    if not default_knowledge_subject and course_options:
        default_knowledge_subject = normalize_knowledge_map_subject(course_options[0].get("title"))
    default_knowledge_subject = default_knowledge_subject or "cpp"
    (
        knowledge_level_options_by_subject,
        knowledge_exam_options_by_subject_level,
        knowledge_level1_options_by_subject_level_exam,
    ) = build_knowledge_management_cascade_options(knowledge_management_rows)
    knowledge_create_subject_options = [
        {
            "value": normalize_knowledge_map_subject(subject),
            "label": format_knowledge_map_subject_label(subject),
        }
        for subject in subject_filter_options
        if normalize_knowledge_map_subject(subject)
    ]
    if not knowledge_create_subject_options and not is_exam_subject_restricted:
        knowledge_create_subject_options = build_knowledge_management_subject_options()
    seen_create_subjects: set[str] = set()
    knowledge_create_subject_options = [
        option
        for option in knowledge_create_subject_options
        if option["value"] and not (option["value"] in seen_create_subjects or seen_create_subjects.add(option["value"]))
    ]
    knowledge_create_level_options_by_subject: dict[str, list[str]] = {}
    for subject_label, levels in level_filter_options_by_subject.items():
        normalized_subject = normalize_knowledge_map_subject(subject_label)
        if normalized_subject:
            knowledge_create_level_options_by_subject[normalized_subject] = list(levels)
    for subject, levels in knowledge_level_options_by_subject.items():
        normalized_subject = normalize_knowledge_map_subject(subject)
        if not normalized_subject:
            continue
        target_levels = knowledge_create_level_options_by_subject.setdefault(normalized_subject, [])
        for level in levels:
            if level not in target_levels:
                target_levels.append(level)

    now = timezone.localtime(timezone.now())
    default_start_at = now + timedelta(minutes=10)
    default_deadline = now + timedelta(days=7)
    selected_mode = str((form_values or {}).get("mode") or ExamPaper.MODE_TIMED)
    if selected_mode not in {ExamPaper.MODE_TIMED, ExamPaper.MODE_DEADLINE}:
        selected_mode = ExamPaper.MODE_TIMED

    papers_queryset = (
        ExamPaper.objects.select_related("course", "teacher")
        .filter(teacher=portal_user, is_active=True)
        .exclude(description__contains="free_practice_exam_question_bank_paper_id=")
    )
    papers = list(
        papers_queryset.annotate(
            question_count=Count("questions", filter=Q(questions__is_active=True)),
            session_count=Count("sessions", filter=Q(sessions__is_active=True)),
            assigned_count=Count(
                "sessions",
                filter=Q(sessions__is_active=True, sessions__status=ExamSession.STATUS_ASSIGNED),
            ),
            in_progress_count=Count(
                "sessions",
                filter=Q(sessions__is_active=True, sessions__status=ExamSession.STATUS_IN_PROGRESS),
            ),
            checked_count=Count(
                "sessions",
                filter=Q(sessions__is_active=True, sessions__status=ExamSession.STATUS_AUTO_CHECKED),
            ),
        )
        .order_by("-created_at", "-id")[:50]
    )
    exam_items = []
    for paper in papers:
        paper_subject_title = infer_exam_paper_subject_title(paper)
        if is_exam_subject_restricted and normalize_exam_subject_key(paper_subject_title) not in allowed_exam_subject_keys:
            continue
        meta = collect_exam_paper_question_meta(paper)
        status_summary = build_exam_paper_status_summary(paper)
        window_end = get_exam_window_end(paper)
        exam_items.append(
            {
            "id": paper.id,
            "title": paper.title,
            "subject_title": paper_subject_title,
            "level_text": meta["level_text"],
            "knowledge_text": meta["knowledge_text"],
            "course_title": paper.course.title if paper.course_id and paper.course else "未绑定课程",
            "exam_type_text": get_exam_mode_text(paper.mode),
            "creator_name": paper.teacher.full_name or paper.teacher.username,
            "mode_text": get_exam_mode_text(paper.mode),
            "time_rule_text": build_exam_time_rule_text(paper),
            "status_text": status_summary["text"],
            "status_tone": status_summary["tone"],
            "is_running": status_summary["is_running"],
            "session_count": paper.session_count,
            "assigned_count": paper.assigned_count,
            "in_progress_count": paper.in_progress_count,
            "checked_count": paper.checked_count,
            "question_count": paper.question_count,
            "access_code": paper.access_code,
            "access_code_text": paper.access_code or "未生成",
            "can_start_exam": not bool(status_summary["is_running"]),
            "start_disabled_reason": "正在考试中，不允许重新生成口令。" if status_summary["is_running"] else "",
            "start_action": reverse("teacher-exams"),
            "detail_href": reverse("teacher-exam-detail", args=[paper.id]),
            "edit_disabled_reason": "",
            "schedule_mode": "scheduled" if paper.mode == ExamPaper.MODE_TIMED else "countdown",
            "start_at_input": format_datetime_input_value(paper.start_at),
            "end_at_input": format_datetime_input_value(window_end),
            "duration_minutes": int(paper.duration_minutes or 45),
            "created_at_text": format_datetime(paper.created_at),
            "created_at_value": timezone.localtime(paper.created_at).date().isoformat() if paper.created_at else "",
            "proctoring_enabled": paper.proctoring_enabled,
            "search_text": " ".join(
                [
                    paper.title,
                    paper.course.title if paper.course_id and paper.course else "",
                    meta["search_text"],
                    paper.teacher.full_name or paper.teacher.username,
                    get_exam_mode_text(paper.mode),
                    str(status_summary["text"]),
                ]
            ),
            }
        )

    return {
        "page_title": "考试管理",
        "page_description": "从这里统一创建考试并发布给当前负责学生。考试入口不挂在每个学生行上。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "考试管理"},
        ],
        "summary_cards": [
            {"label": "可发布课程", "value": f"{len(course_options)} 门", "hint": "来自当前教师负责关系"},
            {"label": "可选学生", "value": f"{len({item['id'] for item in student_options})} 人", "hint": "按课程关系过滤"},
            {"label": "题库题目", "value": f"{len(question_bank_rows)} 题", "hint": "当前负责课程下的可选题目"},
            {"label": "已发布考试", "value": f"{len(exam_items)} 场", "hint": "最近 50 场"},
        ],
        "course_options": course_options,
        "student_options": student_options,
        "teacher_filter_options": teacher_filter_options,
        "subject_filter_options": subject_filter_options,
        "default_exam_subject_filter": default_exam_subject_filter,
        "level_filter_options": level_filter_options,
        "level_filter_options_by_subject": level_filter_options_by_subject,
        "question_bank_rows": question_bank_rows,
        "knowledge_management_rows": knowledge_management_rows,
        "knowledge_subject_options": build_knowledge_management_subject_options(),
        "knowledge_create_subject_options": knowledge_create_subject_options,
        "knowledge_create_level_options_by_subject": knowledge_create_level_options_by_subject,
        "default_knowledge_subject": default_knowledge_subject,
        "knowledge_level_options_by_subject": knowledge_level_options_by_subject,
        "knowledge_exam_options_by_subject_level": knowledge_exam_options_by_subject_level,
        "knowledge_level1_options_by_subject_level_exam": knowledge_level1_options_by_subject_level_exam,
        "available_paper_rows": available_paper_rows,
        "selected_question_ids": selected_question_ids,
        "exam_items": exam_items,
        "exam_table_rows": exam_items,
        "error_message": error_message,
        "success_message": success_message,
        "form_values": {
            "course_id": selected_course_id,
            "mode": selected_mode,
            "title": str((form_values or {}).get("title") or "阶段测验"),
            "description": str((form_values or {}).get("description") or ""),
            "duration_minutes": str((form_values or {}).get("duration_minutes") or "45"),
            "start_at": str((form_values or {}).get("start_at") or default_start_at.strftime("%Y-%m-%dT%H:%M")),
            "end_at": str((form_values or {}).get("end_at") or default_deadline.strftime("%Y-%m-%dT%H:%M")),
            "proctoring_enabled": bool((form_values or {}).get("proctoring_enabled")),
            "question_bank_item_ids": selected_question_ids,
        },
        "exam_bank_import_action": reverse("teacher-exams"),
        "exam_bank_import_accept": ".json,application/json",
        "exam_bank_import_sample": json.dumps(
            {
                "questions": [
                    {
                        "level_code": "P1",
                        "knowledge_point": "加法基础",
                        "stem": "1 + 1 = ?",
                        "options": {"A": "2", "B": "3", "C": "4", "D": "5"},
                        "correct_answer": "A",
                        "analysis": "1 加 1 等于 2。",
                        "score": 1,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        "mode_options": [
            {"value": ExamPaper.MODE_TIMED, "label": "定时模式"},
            {"value": ExamPaper.MODE_DEADLINE, "label": "DL模式"},
        ],
        "back_href": f"{reverse('teacher-students')}?tab=students",
        "empty_message": "当前还没有发布过考试。",
        "create_disabled_reason": "当前老师还没有负责课程，暂时不能创建考试。" if not course_options else "",
    }


def build_teacher_exam_detail_context(
    portal_user: PortalUser,
    paper_id: int,
    *,
    selected_student_id: int = 0,
    selected_exam_run_id: int = 0,
) -> dict:
    paper_queryset = ExamPaper.objects.select_related("teacher", "course").filter(id=paper_id, is_active=True)
    if portal_user.role != PortalUser.ROLE_PRINCIPAL:
        paper_queryset = paper_queryset.filter(teacher=portal_user)
    paper = paper_queryset.get()
    all_sessions = list(
        paper.sessions.select_related("student", "exam_run")
        .filter(is_active=True)
        .order_by("-earned_score", "submitted_at", "id")
    )
    window_end = get_exam_window_end(paper)
    eligible_exam_sessions = [
        session
        for session in all_sessions
        if session.session_type == ExamSession.SESSION_TYPE_EXAM
        and session.status in {ExamSession.STATUS_SUBMITTED, ExamSession.STATUS_AUTO_CHECKED}
        and session.submitted_at
        and (window_end is None or session.submitted_at <= window_end)
    ]
    eligible_exam_sessions.sort(
        key=lambda session: (
            -(session.earned_score or 0),
            session.submitted_at or timezone.now(),
            session.id,
        )
    )
    eligible_exam_run_ids = {
        int(session.exam_run_id)
        for session in eligible_exam_sessions
        if session.exam_run_id
    }
    selected_exam_run_id = selected_exam_run_id if selected_exam_run_id in eligible_exam_run_ids else 0
    stats_exam_sessions = [
        session
        for session in eligible_exam_sessions
        if not selected_exam_run_id or session.exam_run_id == selected_exam_run_id
    ]
    eligible_exam_session_ids = [session.id for session in stats_exam_sessions]
    eligible_sessions_by_student_id = {
        session.student_id: session
        for session in stats_exam_sessions
    }
    selected_student_id = selected_student_id if selected_student_id in eligible_sessions_by_student_id else 0
    selected_student_session = eligible_sessions_by_student_id.get(selected_student_id)
    run_option_source_sessions = [
        session
        for session in eligible_exam_sessions
        if not selected_student_id or session.student_id == selected_student_id
    ]
    run_sequence_items = {}
    for session in eligible_exam_sessions:
        if session.exam_run_id and session.exam_run:
            run_sequence_items.setdefault(session.exam_run_id, session.exam_run)
    run_sequence_map = {
        run_id: index
        for index, (run_id, _run) in enumerate(
            sorted(
                run_sequence_items.items(),
                key=lambda item: (item[1].generated_at, item[0]),
            ),
            start=1,
        )
    }
    run_option_map: dict[int, dict[str, object]] = {}
    for session in run_option_source_sessions:
        if not session.exam_run_id or not session.exam_run:
            continue
        option = run_option_map.setdefault(
            session.exam_run_id,
            {
                "id": session.exam_run_id,
                "label": "",
                "search_text": "",
                "student_ids": set(),
                "student_names": [],
                "submitted_count": 0,
                "selected": session.exam_run_id == selected_exam_run_id,
            },
        )
        option["submitted_count"] = int(option["submitted_count"]) + 1
        student_ids = option["student_ids"]
        if isinstance(student_ids, set):
            student_ids.add(session.student_id)
        student_names = option["student_names"]
        if isinstance(student_names, list) and session.student.display_name not in student_names:
            student_names.append(session.student.display_name)
        if not option["label"]:
            run_no = run_sequence_map.get(session.exam_run_id, 1)
            generated_text = format_datetime(session.exam_run.generated_at)
            code_text = session.exam_run.access_code or "无口令"
            option["label"] = f"场次 {run_no} · {generated_text} · 口令 {code_text}"
    stats_exam_run_options = []
    for option in sorted(
        run_option_map.values(),
        key=lambda item: str(item["label"]),
        reverse=True,
    ):
        student_names = option["student_names"] if isinstance(option["student_names"], list) else []
        student_ids = option["student_ids"] if isinstance(option["student_ids"], set) else set()
        submitted_count = int(option["submitted_count"])
        label = f"{option['label']} · {submitted_count} 人"
        stats_exam_run_options.append(
            {
                "id": option["id"],
                "label": label,
                "selected": option["selected"],
                "student_ids_text": ",".join(str(student_id) for student_id in sorted(student_ids)),
                "student_names_text": " ".join(str(name) for name in student_names),
                "search_text": f"{label} {' '.join(str(name) for name in student_names)}",
            }
        )
    selected_exam_run_label = next(
        (str(option["label"]) for option in stats_exam_run_options if option["selected"]),
        "",
    )
    practice_sessions = [
        session
        for session in all_sessions
        if session.session_type in EXAM_PRACTICE_SESSION_TYPES
        and session.status in FINISHED_EXAM_SESSION_STATUSES
        and session.submitted_at
    ]
    practice_sessions.sort(key=lambda session: (session.submitted_at, session.id), reverse=True)
    questions = list(
        paper.questions.filter(is_active=True)
        .prefetch_related("analysis_blocks", "analysis_suggestions__student")
        .order_by("question_no", "id")
    )
    answers = list(
        ExamSubmissionAnswer.objects.select_related("session", "session__student", "question")
        .filter(session_id__in=eligible_exam_session_ids)
        .order_by("question_id", "session_id")
    )
    answers_by_question: dict[int, list[ExamSubmissionAnswer]] = defaultdict(list)
    selected_answers_by_question: dict[int, ExamSubmissionAnswer] = {}
    for answer in answers:
        answers_by_question[answer.question_id].append(answer)
        if selected_student_session and answer.session_id == selected_student_session.id:
            selected_answers_by_question[answer.question_id] = answer

    question_rows = []
    for question in questions:
        question_answers = answers_by_question.get(question.id, [])
        correct_count = sum(1 for answer in question_answers if answer.is_correct)
        wrong_student_rows = [
            {
                "student_id": answer.session.student_id,
                "student_name": answer.session.student.display_name,
                "selected_answer": str(answer.selected_answer or "未作答").strip() or "未作答",
                "submitted_at_text": format_datetime(answer.session.submitted_at),
            }
            for answer in question_answers
            if not answer.is_correct
        ]
        wrong_answers = [
            row["selected_answer"]
            for row in wrong_student_rows
        ]
        distinct_wrong_answers = []
        for wrong_answer in wrong_answers:
            if wrong_answer not in distinct_wrong_answers:
                distinct_wrong_answers.append(wrong_answer)
        answer_count = correct_count + len(wrong_answers)
        wrong_rate_value = len(wrong_answers) / answer_count if answer_count else 0
        wrong_rate_percent = round(wrong_rate_value * 100, 1)
        serialized_question = serialize_exam_question(
            question,
            show_feedback=True,
            include_teacher_analysis_suggestions=True,
        )
        selected_answer = selected_answers_by_question.get(question.id)
        selected_student_answer_text = str(selected_answer.selected_answer or "未作答").strip() if selected_answer else ""
        selected_student_is_wrong = bool(selected_answer and not selected_answer.is_correct)
        question_rows.append(
            {
                **serialized_question,
                "correct_count": correct_count,
                "wrong_count": len(wrong_answers),
                "answer_count": answer_count,
                "wrong_rate_value": wrong_rate_value,
                "wrong_rate_text": f"{wrong_rate_percent:g}%",
                "wrong_answers_text": "，".join(distinct_wrong_answers),
                "wrong_summary_text": (
                    f"{len(wrong_answers)}；错误答案还有：{'，'.join(distinct_wrong_answers)}"
                    if wrong_answers
                    else "0"
                ),
                "wrong_student_rows": wrong_student_rows,
                "selected_student_answer_text": selected_student_answer_text,
                "selected_student_is_wrong": selected_student_is_wrong,
                "selected_student_status_text": (
                    f"该生答案：{selected_student_answer_text}，错误"
                    if selected_student_is_wrong
                    else (f"该生答案：{selected_student_answer_text}，正确" if selected_answer else "")
                ),
            }
        )
    if selected_student_session:
        question_rows.sort(
            key=lambda row: (
                0 if row["selected_student_is_wrong"] else 1,
                -float(row["wrong_rate_value"] or 0),
                normalize_exam_question_no_for_sort(row["question_no"]),
            ),
        )
    else:
        question_rows.sort(
            key=lambda row: (
                -float(row["wrong_rate_value"] or 0),
                normalize_exam_question_no_for_sort(row["question_no"]),
            ),
        )

    leaderboard_rows = []
    for index, session in enumerate(
        stats_exam_sessions[:10],
        start=1,
    ):
        leaderboard_rows.append(
            {
                "rank": index,
                "student_name": session.student.display_name,
                "earned_score": format_exam_score(session.earned_score),
                "total_score": format_exam_score(session.total_score),
                "correct_count": session.correct_count,
                "wrong_count": session.wrong_count,
                "submitted_at_text": format_datetime(session.submitted_at),
                "switch_count": session.switch_count,
            }
        )
    practice_session_rows = [
        {
            "id": session.id,
            "student_name": session.student.display_name,
            "submitted_at_text": format_datetime(session.submitted_at),
            "submitted_at_value": timezone.localtime(session.submitted_at).isoformat() if session.submitted_at else "",
            "session_type": session.session_type,
            "session_type_text": TEACHER_EXAM_PRACTICE_SESSION_TYPE_LABELS.get(
                session.session_type,
                get_exam_session_type_text(session.session_type),
            ),
            "score_summary": f"{session.correct_count} / {session.total_count}",
            "correct_count": session.correct_count,
            "total_count": session.total_count,
            "earned_score": format_exam_score(session.earned_score),
            "total_score": format_exam_score(session.total_score),
            "detail_href": reverse("teacher-student-exam-detail", args=[session.student_id, session.id]),
            "search_text": " ".join(
                [
                    session.student.display_name,
                    get_exam_session_type_text(session.session_type),
                    format_datetime(session.submitted_at),
                    f"{session.correct_count}/{session.total_count}",
                ]
            ),
        }
        for session in practice_sessions
    ]

    status_summary = build_exam_paper_status_summary(
        ExamPaper.objects.filter(id=paper.id)
        .annotate(
            session_count=Count("sessions", filter=Q(sessions__is_active=True)),
            in_progress_count=Count(
                "sessions",
                filter=Q(sessions__is_active=True, sessions__status=ExamSession.STATUS_IN_PROGRESS),
            ),
            checked_count=Count(
                "sessions",
                filter=Q(sessions__is_active=True, sessions__status=ExamSession.STATUS_AUTO_CHECKED),
            ),
        )
        .get()
    )
    meta = collect_exam_paper_question_meta(paper)
    return {
        "page_title": paper.title,
        "page_description": "这里展示单场考试排行榜、完整试卷内容、答案解析和逐题正误统计。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "考试管理", "href": reverse("teacher-exams")},
            {"label": paper.title},
        ],
        "summary_cards": [
            {"label": "考试状态", "value": str(status_summary["text"]), "hint": build_exam_time_rule_text(paper)},
            {"label": "考试提交", "value": f"{len(stats_exam_sessions)} 条", "hint": "当前筛选下考试结束前的正式提交"},
            {"label": "题目数量", "value": f"{len(questions)} 题", "hint": meta["knowledge_text"]},
            {"label": "口令", "value": paper.access_code or "未生成", "hint": "开始考试后生成 6 位口令"},
        ],
        "paper": paper,
        "leaderboard_rows": leaderboard_rows,
        "practice_session_rows": practice_session_rows,
        "question_rows": question_rows,
        "stats_exam_run_options": stats_exam_run_options,
        "selected_stats_exam_run_id": selected_exam_run_id,
        "selected_stats_exam_run_label": selected_exam_run_label,
        "stats_student_options": [
            {
                "id": session.student_id,
                "name": session.student.display_name,
                "selected": session.student_id == selected_student_id,
                "session_ids_text": ",".join(
                    str(item.exam_run_id)
                    for item in eligible_exam_sessions
                    if item.student_id == session.student_id and item.exam_run_id
                ),
            }
            for session in sorted(eligible_sessions_by_student_id.values(), key=lambda item: item.student.display_name)
        ],
        "selected_stats_student_id": selected_student_id,
        "selected_stats_student_name": selected_student_session.student.display_name if selected_student_session else "",
        "back_href": reverse("teacher-exams"),
        "empty_leaderboard_message": "当前还没有已提交并判分的学生记录。",
        "empty_practice_message": "当前还没有学生独立练习记录。",
    }


def build_teacher_student_exam_detail_context(
    portal_user: PortalUser,
    student_id: int,
    session_id: int,
) -> dict:
    session = (
        ExamSession.objects.select_related("paper", "paper__teacher", "paper__course", "student")
        .filter(id=session_id, student_id=student_id, paper__teacher=portal_user, is_active=True, paper__is_active=True)
        .get()
    )
    questions = get_exam_session_questions(session)
    answers = {
        answer.question_id: answer
        for answer in session.answers.select_related("question").all()
    }
    question_rows = [
        serialize_exam_question(question, answer=answers.get(question.id), show_feedback=True)
        for question in questions
    ]
    wrong_rows = [question for question in question_rows if question["is_wrong"]]
    proctor_events = list(session.proctor_events.order_by("occurred_at", "id")[:100])
    serialized = serialize_exam_session(session)
    return {
        "page_title": f"{session.student.display_name} · {session.paper.title}",
        "page_description": "查看学生本次提交的完整题目、答案、解析和监考事件。",
        "breadcrumbs": [
            {"label": "教师学生列表", "href": reverse("teacher-students")},
            {"label": "考试管理", "href": reverse("teacher-exams")},
            {"label": session.student.display_name, "href": reverse("teacher-student-detail", args=[session.student_id])},
            {"label": session.paper.title},
        ],
        "summary_cards": [
            {"label": "得分", "value": f"{serialized['earned_score']} / {serialized['total_score']}", "hint": serialized["status_text"]},
            {"label": "正确题数", "value": f"{serialized['correct_count']} / {serialized['total_count']}", "hint": "自动判分结果"},
            {"label": "提交类型", "value": serialized["session_type_text"], "hint": f"错题 {serialized['wrong_count']} 题"},
            {"label": "切屏次数", "value": f"{serialized['switch_count']} 次", "hint": "来自浏览器监考事件"},
        ],
        "session": serialized,
        "student": session.student,
        "question_rows": question_rows,
        "wrong_question_rows": wrong_rows,
        "proctor_event_rows": [
            {
                "event_type": event.event_type,
                "event_type_text": dict(ExamProctorEvent.EVENT_CHOICES).get(event.event_type, event.event_type),
                "occurred_at_text": format_datetime(event.occurred_at),
            }
            for event in proctor_events
        ],
        "back_href": reverse("teacher-exam-detail", args=[session.paper_id]),
    }


def build_student_practice_page_shell(portal_user: PortalUser) -> dict:
    student = get_student_by_user(portal_user)
    assignments = list(get_student_homework_queryset(student))
    exam_sessions = list(
        ExamSession.objects.filter(student=student, is_active=True, paper__is_active=True)
    )
    completed_like = {HomeworkAssignment.STATUS_COMPLETED, HomeworkAssignment.STATUS_REVIEWED}
    pending_count = sum(1 for assignment in assignments if assignment.status == HomeworkAssignment.STATUS_ASSIGNED)
    completed_count = sum(1 for assignment in assignments if assignment.status in completed_like)
    pending_exam_count = sum(
        1
        for session in exam_sessions
        if session.status in {ExamSession.STATUS_ASSIGNED, ExamSession.STATUS_IN_PROGRESS}
    )
    return {
        "page_mode": "entry_grid",
        "grid_variant": "courses",
        "hero_eyebrow": "Practice Portal",
        "page_title": "练习",
        "page_description": "先在这里看老师布置的任务，再按作业详情进入对应知识点练习。",
        "breadcrumb_items": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习"},
        ],
        "summary_cards": [
            {"label": "我的作业", "value": f"{len(assignments)} 条"},
            {"label": "待完成", "value": f"{pending_count} 条"},
            {"label": "我的考试", "value": f"{len(exam_sessions)} 场"},
            {"label": "自由练习", "value": f"最多 {FREE_PRACTICE_MAX_QUESTION_COUNT} 题"},
        ],
        "entry_hint": "可以按老师布置完成任务，也可以进入自由练习按知识点选题。",
        "portal_cards": [
            {
                "slug": "free-practice",
                "title": "自由练习",
                "meta": "自主选题",
                "subtitle": "按等级和知识点一级目录筛题",
                "note": f"单次最多 {FREE_PRACTICE_MAX_QUESTION_COUNT} 题，超过会自动回到上限。",
                "state": "open",
                "status_text": "已开放",
                "featured": True,
                "action_label": "进入自由练习",
                "action_href": reverse("student-free-practice"),
            },
            {
                "slug": "homework",
                "title": "我的作业",
                "meta": "教师布置",
                "subtitle": "查看老师布置的当前任务",
                "note": f"当前共有 {len(assignments)} 条作业，待完成 {pending_count} 条。",
                "state": "open",
                "status_text": "已开放",
                "featured": True,
                "action_label": "进入我的作业",
                "action_href": reverse("student-homework-list"),
            },
            {
                "slug": "exams",
                "title": "我的考试",
                "meta": "在线考试",
                "subtitle": "查看老师发布的考试",
                "note": f"当前共有 {len(exam_sessions)} 场考试，待完成 {pending_exam_count} 场。",
                "state": "open",
                "status_text": "已开放",
                "featured": True,
                "action_label": "进入我的考试",
                "action_href": reverse("student-exam-list"),
            },
            {
                "slug": "oj",
                "title": "OJ 练习",
                "meta": "外部练习",
                "subtitle": "占位入口",
                "note": "后续再接统一信息。",
                "state": "trial",
                "status_text": "占位中",
                "action_label": "打开 OJ",
                "action_href": "http://oi.dashima.com:88",
            },
        ],
    }


def normalize_student_free_practice_level_code(value: object) -> str:
    code = str(value or "").strip().upper().replace("_", "-")
    if code in {"CSPJ", "CSP-J"}:
        return "CSP-J"
    if code in {"CSPS", "CSP-S"}:
        return "CSP-S"
    if re.fullmatch(r"GESP[1-8]", code):
        return code
    return ""


def get_student_free_practice_level_options(student: Student | None = None) -> list[dict[str, str]]:
    mapped_codes = {
        str(code or "").strip().upper()
        for code in ExamKnowledgePointMap.objects.filter(subject="cpp", is_active=True)
        .exclude(category_code="")
        .values_list("category_code", flat=True)
    }
    ordered_codes = [f"GESP{index}" for index in range(1, 9)] + ["CSP-J", "CSP-S"]
    allowed_codes: set[str] = set(ordered_codes)
    if student:
        student_cpp_level_code = student.primary_level_name
        visible_stage_codes = {normalize_student_free_practice_level_code(code) for code in get_visible_cpp_stage_codes(student_cpp_level_code)}
        visible_stage_codes.discard("")
        if visible_stage_codes:
            allowed_codes = visible_stage_codes
    return [
        {"value": code, "label": code}
        for code in ordered_codes
        if code in allowed_codes and (code in mapped_codes or code.startswith("CSP-"))
    ]


def get_student_free_practice_level1_options(level_code: str) -> list[str]:
    level_code = normalize_student_free_practice_level_code(level_code)
    queryset = ExamKnowledgePointMap.objects.filter(subject="cpp", is_active=True)
    if level_code and level_code not in {"CSP-J", "CSP-S"}:
        queryset = queryset.filter(category_code=level_code)
    rows = queryset.exclude(level_1="").values_list("level_1", flat=True).distinct().order_by("level_1")
    return [str(row or "").strip() for row in rows if str(row or "").strip()]


def get_student_free_practice_level2_options(level_code: str, level_1_query: str) -> list[dict[str, str]]:
    level_code = normalize_student_free_practice_level_code(level_code)
    queryset = ExamKnowledgePointMap.objects.filter(subject="cpp", is_active=True)
    if level_code and level_code not in {"CSP-J", "CSP-S"}:
        queryset = queryset.filter(category_code=level_code)
    level_1_query = str(level_1_query or "").strip()
    if level_1_query:
        queryset = queryset.filter(level_1__icontains=level_1_query)
    rows = queryset.exclude(level_2="").values("level_1", "level_2").distinct().order_by("level_1", "level_2")
    return [
        {
            "level_1": str(row.get("level_1") or "").strip(),
            "value": str(row.get("level_2") or "").strip(),
        }
        for row in rows
        if str(row.get("level_2") or "").strip()
    ]


def normalize_knowledge_map_subject(value: object) -> str:
    normalized = str(value or "").strip().lower()
    compact = normalized.replace(" ", "").replace("+", "p")
    if normalized in {"c++", "cpp"} or compact in {"cpp", "cxx", "cpptype"}:
        return "cpp"
    if normalized in {"python", "py"}:
        return "python"
    if normalized == "scratch":
        return "scratch"
    if normalized in {"drone", "uav", "无人机"}:
        return "drone"
    return normalized


def normalize_teacher_subject_for_knowledge(value: object) -> str:
    raw_text = str(value or "").strip()
    if not raw_text:
        return ""
    direct = normalize_knowledge_map_subject(raw_text)
    if direct in {"cpp", "python", "scratch", "drone"}:
        return direct
    for part in re.split(r"[、,，;/\s]+", raw_text):
        normalized = normalize_knowledge_map_subject(part)
        if normalized in {"cpp", "python", "scratch", "drone"}:
            return normalized
    compact = raw_text.replace(" ", "").lower()
    if any(token in compact for token in ("c++", "cpp")):
        return "cpp"
    if "scratch" in compact:
        return "scratch"
    return direct


def format_knowledge_map_subject_label(subject: object) -> str:
    normalized = normalize_knowledge_map_subject(subject)
    if normalized == "cpp":
        return "C++"
    if normalized == "python":
        return "Python"
    if normalized == "scratch":
        return "Scratch"
    if normalized == "drone":
        return "无人机"
    return str(subject or normalized).strip() or "未标注学科"


def build_exam_knowledge_management_rows(portal_user: PortalUser | None = None) -> list[dict[str, object]]:
    grouped_rows: dict[tuple[str, str, str, str], dict[str, object]] = {}
    current_operator_name = ""
    if portal_user is not None:
        current_operator_name = portal_user.full_name or portal_user.username
    queryset = (
        ExamKnowledgePointMap.objects.select_related("uploaded_by")
        .filter(is_active=True)
        .exclude(subject="")
        .exclude(category_code="")
        .exclude(level_1="")
        .order_by("subject", "course_level_code", "category_code", "level_1", "sort_order", "id")
    )
    for item in queryset:
        subject = normalize_knowledge_map_subject(item.subject)
        category_code = str(item.category_code or "").strip().upper()
        course_level_code = str(item.course_level_code or "").strip().upper()
        if not course_level_code:
            category_match = re.fullmatch(r"GESP([1-8])", category_code)
            if category_match:
                course_level_code = "C1" if int(category_match.group(1)) <= 4 else "C2"
            elif category_code == "CSP-J":
                course_level_code = "C3"
            elif category_code == "CSP-S":
                course_level_code = "C4"
        level_1 = str(item.level_1 or "").strip()
        if not subject or not category_code or not level_1:
            continue
        key = (subject, course_level_code, category_code, level_1)
        operator_name = ""
        if item.uploaded_by_id and item.uploaded_by:
            operator_name = item.uploaded_by.full_name or item.uploaded_by.username
        imported_at = item.updated_at or item.created_at
        existing = grouped_rows.get(key)
        if not existing:
            grouped_rows[key] = {
                "id": item.id,
                "subject": subject,
                "subject_title": format_knowledge_map_subject_label(subject),
                "course_level_code": course_level_code,
                "course_level_text": course_level_code or "未设置",
                "category_code": category_code,
                "level_1": level_1,
                "imported_at": imported_at,
                "imported_at_text": format_datetime(imported_at),
                "operator_name": operator_name or current_operator_name or "未记录",
                "row_count": 1,
                "search_text": " ".join(
                    [
                        format_knowledge_map_subject_label(subject),
                        subject,
                        course_level_code,
                        category_code,
                        level_1,
                        operator_name,
                    ]
                ),
            }
            continue
        existing["row_count"] = int(existing.get("row_count") or 0) + 1
        existing_imported_at = existing.get("imported_at")
        if imported_at and (not existing_imported_at or imported_at >= existing_imported_at):
            existing["id"] = item.id
            existing["imported_at"] = imported_at
            existing["imported_at_text"] = format_datetime(imported_at)
            existing["operator_name"] = operator_name or current_operator_name or str(existing.get("operator_name") or "未记录")

    rows = list(grouped_rows.values())
    for row in rows:
        row["detail_href"] = reverse("teacher-exam-knowledge-detail", args=[row["id"]])
    rows.sort(
        key=lambda row: (
            str(row.get("subject_title") or ""),
            str(row.get("course_level_code") or ""),
            str(row.get("category_code") or ""),
            str(row.get("level_1") or ""),
        )
    )
    return rows


def build_knowledge_management_subject_options() -> list[dict[str, str]]:
    base_subjects = ["cpp", "python", "scratch", "drone", "ai"]
    db_subjects = {
        normalize_knowledge_map_subject(subject)
        for subject in ExamKnowledgePointMap.objects.filter(is_active=True).values_list("subject", flat=True)
    }
    course_subjects = {
        normalize_knowledge_map_subject(title)
        for title in Course.objects.values_list("title", flat=True)
    }
    subjects = [subject for subject in base_subjects if subject]
    for subject in sorted((db_subjects | course_subjects) - set(subjects)):
        if subject:
            subjects.append(subject)
    return [
        {"value": subject, "label": format_knowledge_map_subject_label(subject)}
        for subject in subjects
    ]


def build_knowledge_management_cascade_options(
    rows: list[dict[str, object]],
) -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]], dict[str, dict[str, dict[str, list[str]]]]]:
    level_options_by_subject: dict[str, list[str]] = defaultdict(list)
    exam_options_by_subject_level: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    level1_options_by_subject_level_exam: dict[str, dict[str, dict[str, list[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    def add_level(subject: str, level: str) -> None:
        subject = normalize_knowledge_map_subject(subject)
        level = str(level or "").strip().upper()
        if subject and level and level not in level_options_by_subject[subject]:
            level_options_by_subject[subject].append(level)

    def add_exam(subject: str, level: str, exam_name: str) -> None:
        subject = normalize_knowledge_map_subject(subject)
        level = str(level or "").strip().upper()
        exam_name = str(exam_name or "").strip()
        if subject and level and exam_name and exam_name not in exam_options_by_subject_level[subject][level]:
            exam_options_by_subject_level[subject][level].append(exam_name)

    def add_level1(subject: str, level: str, exam_name: str, level_1: str) -> None:
        subject = normalize_knowledge_map_subject(subject)
        level = str(level or "").strip().upper()
        exam_name = str(exam_name or "").strip()
        level_1 = str(level_1 or "").strip()
        if subject and level and exam_name and level_1 and level_1 not in level1_options_by_subject_level_exam[subject][level][exam_name]:
            level1_options_by_subject_level_exam[subject][level][exam_name].append(level_1)

    for row in rows:
        subject = str(row.get("subject") or "")
        level = str(row.get("course_level_code") or "")
        exam_name = str(row.get("category_code") or "")
        level_1 = str(row.get("level_1") or "")
        add_level(subject, level)
        add_exam(subject, level, exam_name)
        add_level1(subject, level, exam_name, level_1)

    for level in ["C1", "C2", "C3", "C4"]:
        add_level("cpp", level)
    for index in range(1, 5):
        add_exam("cpp", "C1", f"GESP{index}")
    for index in range(5, 9):
        add_exam("cpp", "C2", f"GESP{index}")
    add_exam("cpp", "C3", "CSP-J")
    add_exam("cpp", "C4", "CSP-S")

    def sorted_level_key(value: str) -> tuple[int, str]:
        match = re.fullmatch(r"([A-Z]+)(\d+)", value)
        if match:
            return (int(match.group(2)), match.group(1))
        return (99, value)

    normalized_levels = {
        subject: sorted(levels, key=sorted_level_key)
        for subject, levels in level_options_by_subject.items()
    }
    normalized_exams = {
        subject: {
            level: sorted(exams, key=lambda value: (0 if value.startswith("GESP") else 1, value))
            for level, exams in levels.items()
        }
        for subject, levels in exam_options_by_subject_level.items()
    }
    normalized_level1 = {
        subject: {
            level: {
                exam: sorted(level1_values)
                for exam, level1_values in exams.items()
            }
            for level, exams in levels.items()
        }
        for subject, levels in level1_options_by_subject_level_exam.items()
    }
    return normalized_levels, normalized_exams, normalized_level1


def get_knowledge_map_rows_for_selector() -> list[dict[str, str]]:
    rows = (
        ExamKnowledgePointMap.objects.filter(is_active=True)
        .exclude(subject="")
        .exclude(category_code="")
        .values("subject", "category_code", "level_1", "level_2", "level_3")
        .distinct()
        .order_by("subject", "category_code", "level_1", "level_2", "level_3")
    )
    return [
        {
            "subject": normalize_knowledge_map_subject(row.get("subject")),
            "category_code": str(row.get("category_code") or "").strip().upper(),
            "level_1": str(row.get("level_1") or "").strip(),
            "level_2": str(row.get("level_2") or "").strip(),
            "level_3": str(row.get("level_3") or "").strip(),
        }
        for row in rows
        if normalize_knowledge_map_subject(row.get("subject")) and str(row.get("category_code") or "").strip()
    ]


def build_knowledge_map_selector_context(*, default_subject: str = "cpp") -> dict[str, object]:
    rows = get_knowledge_map_rows_for_selector()
    subject_values = {row["subject"] for row in rows if row["subject"]}
    subject_values.update(
        normalize_knowledge_map_subject(value)
        for value in Course.objects.values_list("title", flat=True)
    )
    subject_values.discard("")
    subject_options = [
        {"value": subject, "label": format_knowledge_map_subject_label(subject)}
        for subject in sorted(subject_values, key=lambda value: format_knowledge_map_subject_label(value).lower())
    ]
    category_order = {f"GESP{index}": index for index in range(1, 9)}
    category_options_by_subject: dict[str, list[dict[str, str]]] = {}
    for subject in subject_values:
        categories = {
            row["category_code"]
            for row in rows
            if row["subject"] == subject and row["category_code"]
        }
        category_options_by_subject[subject] = [
            {"value": category, "label": category}
            for category in sorted(categories, key=lambda value: (category_order.get(value, 99), value))
        ]
    normalized_default_subject = normalize_knowledge_map_subject(default_subject) or (subject_options[0]["value"] if subject_options else "")
    return {
        "knowledge_subject_options": subject_options,
        "knowledge_category_options_by_subject": category_options_by_subject,
        "knowledge_map_rows": rows,
        "default_knowledge_subject": normalized_default_subject,
        "knowledge_create_href": reverse("teacher-question-source-create-content"),
    }


def _question_matches_free_practice_filters(
    question: ExamQuestion,
    *,
    level_code: str,
    level_1_query: str,
    level_2_query: str,
) -> bool:
    snapshot = question.source_snapshot_json if isinstance(question.source_snapshot_json, dict) else {}
    if snapshot.get("free_practice_source_question_id"):
        return False
    if snapshot.get("free_practice_homework_source") or snapshot.get("homework_question_id"):
        return False
    question_level = str(snapshot.get("level_code") or "").strip().upper()
    if level_code and level_code not in {"CSP-J", "CSP-S"} and question_level != level_code:
        return False
    knowledge_level_1 = str(snapshot.get("knowledge_level_1") or "").strip()
    knowledge_level_2 = str(snapshot.get("knowledge_level_2") or "").strip()
    level_1_search_text = knowledge_level_1
    if not level_1_search_text:
        level_1_search_text = " ".join(
            [
                str(question.wrong_point_label or "").strip(),
                str(snapshot.get("knowledge_point") or "").strip(),
            ]
        )
    if level_1_query and level_1_query.lower() not in level_1_search_text.lower():
        return False
    if level_2_query and level_2_query.lower() not in knowledge_level_2.lower():
        return False
    return True


def _knowledge_values_match_free_practice_filters(
    *,
    question_level: str,
    knowledge_level_1: str,
    knowledge_level_2: str,
    level_code: str,
    level_1_query: str,
    level_2_query: str,
) -> bool:
    question_level = normalize_student_free_practice_level_code(question_level)
    if level_code and level_code not in {"CSP-J", "CSP-S"} and question_level != level_code:
        return False
    if level_1_query and level_1_query.lower() not in str(knowledge_level_1 or "").lower():
        return False
    if level_2_query and level_2_query.lower() not in str(knowledge_level_2 or "").lower():
        return False
    return True


def get_bank_question_exam_options_for_free_practice(question: ExamQuestionBankQuestion) -> dict[str, str]:
    full_json = question.full_json if isinstance(question.full_json, dict) else {}
    raw_options = full_json.get("options") if isinstance(full_json.get("options"), dict) else {}
    options = {key: str(raw_options.get(key) or raw_options.get(key.lower()) or "").strip() for key in ["A", "B", "C", "D"]}
    for option in question.options.all():
        key = str(option.option_key or "").strip().upper()[:1]
        if key in options and not options[key]:
            options[key] = str(option.option_text_md or "").strip()
    if question.question_type == ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE and not any(options.values()):
        options = {"A": "正确", "B": "错误", "C": "", "D": ""}
    return options


def get_bank_question_exam_answer_for_free_practice(question: ExamQuestionBankQuestion) -> str:
    full_json = question.full_json if isinstance(question.full_json, dict) else {}
    answer_json = question.answer_json if isinstance(question.answer_json, dict) else {}
    raw_answer = full_json.get("correct_answer") or full_json.get("answer") or answer_json.get("correct_answer") or answer_json.get("answer")
    if isinstance(raw_answer, list):
        raw_answer = raw_answer[0] if raw_answer else ""
    answer = str(raw_answer or "").strip().upper()[:1]
    if question.question_type == ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE:
        if answer in {"T", "Y", "对", "正", "√", "✓"}:
            return "A"
        if answer in {"F", "N", "错", "误", "×", "✗", "X"}:
            return "B"
    return answer if answer in {"A", "B", "C", "D"} else ""


def _bank_question_matches_free_practice_filters(
    question: ExamQuestionBankQuestion,
    *,
    level_code: str,
    level_1_query: str,
    level_2_query: str,
) -> bool:
    full_json = question.full_json if isinstance(question.full_json, dict) else {}
    return _knowledge_values_match_free_practice_filters(
        question_level=str(question.paper.level if question.paper_id and question.paper else full_json.get("level_code") or "").strip(),
        knowledge_level_1=str(full_json.get("knowledge_level_1") or "").strip(),
        knowledge_level_2=str(full_json.get("knowledge_level_2") or "").strip(),
        level_code=level_code,
        level_1_query=level_1_query,
        level_2_query=level_2_query,
    )


def get_homework_question_knowledge_values(question: HomeworkQuestion) -> dict[str, str]:
    snapshot = decode_sql_ascii_json_text(question.source_snapshot_json)
    if not isinstance(snapshot, dict):
        snapshot = {}
    import_job = question.import_job
    content = import_job.content if import_job and import_job.content_id else None
    title_parts = [
        part.strip()
        for part in str(content.title if content else "").split("/")
        if part.strip()
    ]
    return {
        "level_code": str(
            snapshot.get("level_code")
            or (content.level.code if content and content.level_id and content.level else "")
            or (content.phase if content else "")
            or ""
        ).strip().upper(),
        "knowledge_level_1": str(snapshot.get("knowledge_level_1") or (title_parts[0] if len(title_parts) >= 1 else "")).strip(),
        "knowledge_level_2": str(snapshot.get("knowledge_level_2") or (title_parts[1] if len(title_parts) >= 2 else "")).strip(),
        "knowledge_level_3": str(snapshot.get("knowledge_level_3") or (title_parts[2] if len(title_parts) >= 3 else "")).strip(),
    }


def _homework_question_matches_free_practice_filters(
    question: HomeworkQuestion,
    *,
    level_code: str,
    level_1_query: str,
    level_2_query: str,
) -> bool:
    values = get_homework_question_knowledge_values(question)
    return _knowledge_values_match_free_practice_filters(
        question_level=values["level_code"],
        knowledge_level_1=values["knowledge_level_1"],
        knowledge_level_2=values["knowledge_level_2"],
        level_code=level_code,
        level_1_query=level_1_query,
        level_2_query=level_2_query,
    )


def get_student_free_practice_question_queryset(level_code: str) -> QuerySet[ExamQuestion]:
    level_code = normalize_student_free_practice_level_code(level_code)
    queryset = (
        ExamQuestion.objects.select_related("paper", "paper__teacher", "paper__course")
        .prefetch_related("analysis_blocks")
        .filter(is_active=True, paper__is_active=True)
        .exclude(paper__title__startswith="自由练习 ·")
        .order_by("-updated_at", "-id")
    )
    if not level_code:
        return ExamQuestion.objects.none()
    return queryset


def get_student_free_practice_homework_question_queryset(level_code: str) -> QuerySet[HomeworkQuestion]:
    level_code = normalize_student_free_practice_level_code(level_code)
    queryset = (
        HomeworkQuestion.objects.select_related(
            "import_job",
            "import_job__teacher",
            "import_job__content",
            "import_job__content__level",
            "import_job__content__course",
        )
        .filter(
            is_active=True,
            assignment__isnull=True,
            import_job__is_active=True,
            import_job__assignment__isnull=True,
            import_job__parse_status=HomeworkImportJob.STATUS_CONFIRMED,
        )
        .order_by("-updated_at", "-id")
    )
    if not level_code:
        return HomeworkQuestion.objects.none()
    return queryset


def get_student_free_practice_bank_question_queryset(level_code: str) -> QuerySet[ExamQuestionBankQuestion]:
    level_code = normalize_student_free_practice_level_code(level_code)
    queryset = (
        ExamQuestionBankQuestion.objects.select_related("paper")
        .prefetch_related("options", "assets")
        .filter(
            paper__is_active=True,
            question_type__in=[
                ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
                ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
                ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
            ],
        )
        .exclude(full_json__knowledge_level_1="")
        .order_by("-updated_at", "-id")
    )
    if not level_code:
        return ExamQuestionBankQuestion.objects.none()
    return queryset


def build_free_practice_question_rows_for_filters(
    *,
    level_code: str,
    knowledge_query: str = "",
    knowledge_level_2: str = "",
    selected_question_ids: list[object] | None = None,
) -> list[dict[str, object]]:
    normalized_level_code = normalize_student_free_practice_level_code(level_code)
    normalized_level_1 = str(knowledge_query or "").strip()
    normalized_level_2 = str(knowledge_level_2 or "").strip()
    selected_keys = {str(question_id) for question_id in (selected_question_ids or [])}
    selected_ids = {
        int(question_id)
        for question_id in (selected_question_ids or [])
        if str(question_id).strip().isdigit()
    }
    selected_homework_ids = {
        normalize_positive_value(str(question_id).split(":", 1)[1], default=0, minimum=1)
        for question_id in (selected_question_ids or [])
        if re.fullmatch(r"homework:\d+", str(question_id).strip())
    }
    selected_homework_ids.discard(0)
    queryset = get_student_free_practice_question_queryset(normalized_level_code)
    candidate_items = []
    seen_bank_question_ids = set()
    for question in queryset:
        if _question_matches_free_practice_filters(
            question,
            level_code=normalized_level_code,
            level_1_query=normalized_level_1,
            level_2_query=normalized_level_2,
        ):
            candidate_items.append(("exam", question))
            snapshot = question.source_snapshot_json if isinstance(question.source_snapshot_json, dict) else {}
            bank_question_id = normalize_positive_value(snapshot.get("bank_question_id"), default=0, minimum=1)
            if bank_question_id:
                seen_bank_question_ids.add(bank_question_id)

    bank_queryset = get_student_free_practice_bank_question_queryset(normalized_level_code)
    for bank_question in bank_queryset:
        if bank_question.id in seen_bank_question_ids:
            continue
        if not _bank_question_matches_free_practice_filters(
            bank_question,
            level_code=normalized_level_code,
            level_1_query=normalized_level_1,
            level_2_query=normalized_level_2,
        ):
            continue
        candidate_items.append(("bank", bank_question))
        seen_bank_question_ids.add(bank_question.id)

    homework_queryset = get_student_free_practice_homework_question_queryset(normalized_level_code)
    for homework_question in homework_queryset:
        if not _homework_question_matches_free_practice_filters(
            homework_question,
            level_code=normalized_level_code,
            level_1_query=normalized_level_1,
            level_2_query=normalized_level_2,
        ):
            continue
        candidate_items.append(("homework", homework_question))

    selected_candidate_items = []
    remaining_candidate_items = []
    for source_type, source_question in candidate_items:
        if source_type == "exam":
            source_key = str(source_question.id)
        elif source_type == "bank":
            source_key = f"bank:{source_question.id}"
        else:
            source_key = f"homework:{source_question.id}"
        is_selected = source_key in selected_keys or (source_type == "exam" and int(source_question.id) in selected_ids)
        if source_type == "homework" and int(source_question.id) in selected_homework_ids:
            is_selected = True
        if is_selected:
            selected_candidate_items.append((source_type, source_question))
        else:
            remaining_candidate_items.append((source_type, source_question))
    random.shuffle(remaining_candidate_items)
    candidate_items = (selected_candidate_items + remaining_candidate_items)[:FREE_PRACTICE_MAX_QUESTION_COUNT]

    question_rows = []
    for source_type, source_question in candidate_items:
        if source_type == "exam":
            question = source_question
            snapshot = question.source_snapshot_json if isinstance(question.source_snapshot_json, dict) else {}
            raw_image_paths = snapshot.get("image_paths") if isinstance(snapshot.get("image_paths"), list) else []
            image_paths = [
                str(path or "").strip()
                for path in raw_image_paths
                if str(path or "").strip()
            ]
            if question.image_path and question.image_path not in image_paths:
                image_paths.insert(0, question.image_path)
            material_image_paths = [
                str(path or "").strip()
                for path in (snapshot.get("material_image_paths") if isinstance(snapshot.get("material_image_paths"), list) else [])
                if str(path or "").strip()
            ]
            question_image_paths = [
                str(path or "").strip()
                for path in (snapshot.get("question_image_paths") if isinstance(snapshot.get("question_image_paths"), list) else [])
                if str(path or "").strip()
            ]
            image_paths = exclude_markdown_embedded_image_paths(image_paths, question.stem)
            material_image_paths = exclude_markdown_embedded_image_paths(material_image_paths, question.stem)
            question_image_paths = exclude_markdown_embedded_image_paths(question_image_paths, question.stem)
            level_parts = [
                str(snapshot.get("knowledge_level_1") or "").strip(),
                str(snapshot.get("knowledge_level_2") or "").strip(),
                str(snapshot.get("knowledge_level_3") or "").strip(),
            ]
            knowledge_display = " / ".join(part for part in level_parts if part)
            if not knowledge_display:
                knowledge_display = str(question.wrong_point_label or snapshot.get("knowledge_point") or "未标注").strip() or "未标注"
            question_rows.append(
                {
                    "id": question.id,
                    "source_key": str(question.id),
                    "source_type": "exam",
                    "question_no": question.question_no,
                    "paper_title": question.paper.title,
                    "stem_preview": truncate_plain_text(question.stem, 90),
                    "stem_html": render_exam_markdown_for_display(question.stem),
                    "image_paths": image_paths,
                    "material_image_paths": material_image_paths,
                    "question_image_paths": question_image_paths,
                    "option_items": build_exam_option_items(question.options_json if isinstance(question.options_json, dict) else {}),
                    "knowledge_level_1": str(snapshot.get("knowledge_level_1") or "").strip() or "未标注",
                    "knowledge_level_2": str(snapshot.get("knowledge_level_2") or "").strip() or "未标注",
                    "knowledge_level_3": str(snapshot.get("knowledge_level_3") or "").strip() or "选填",
                    "knowledge_display": knowledge_display,
                    "level_code": str(snapshot.get("level_code") or "").strip() or normalized_level_code,
                    "is_selected": question.id in selected_ids or str(question.id) in selected_keys,
                }
            )
            continue

        if source_type == "homework":
            homework_question = source_question
            values = get_homework_question_knowledge_values(homework_question)
            options = decode_sql_ascii_json_text(homework_question.options_json)
            options = options if isinstance(options, dict) else {}
            source_key = f"homework:{homework_question.id}"
            import_job = homework_question.import_job
            question_rows.append(
                {
                    "id": source_key,
                    "source_key": source_key,
                    "source_type": "homework",
                    "question_no": homework_question.question_no,
                    "paper_title": import_job.source_filename if import_job else "公共作业题源",
                    "stem_preview": truncate_plain_text(homework_question.stem, 90),
                    "stem_html": render_exam_markdown_for_display(homework_question.stem),
                    "image_paths": [],
                    "material_image_paths": [],
                    "question_image_paths": [],
                    "option_items": build_exam_option_items(options),
                    "knowledge_level_1": values["knowledge_level_1"] or "未标注",
                    "knowledge_level_2": values["knowledge_level_2"] or "未标注",
                    "knowledge_level_3": values["knowledge_level_3"] or "选填",
                    "knowledge_display": " / ".join(
                        part
                        for part in [values["knowledge_level_1"], values["knowledge_level_2"], values["knowledge_level_3"]]
                        if part
                    ) or "未标注",
                    "level_code": values["level_code"] or normalized_level_code,
                    "is_selected": source_key in selected_keys or homework_question.id in selected_homework_ids,
                }
            )
            continue

        bank_question = source_question
        full_json = bank_question.full_json if isinstance(bank_question.full_json, dict) else {}
        options = get_bank_question_exam_options_for_free_practice(bank_question)
        content_assets = [
            asset
            for asset in bank_question.assets.all()
            if asset.asset_role == "content" and asset.relative_path
        ]
        image_paths = [asset.relative_path for asset in content_assets if asset.relative_path]
        material_image_paths = [
            str(path or "").strip()
            for path in (full_json.get("material_image_paths") if isinstance(full_json.get("material_image_paths"), list) else [])
            if str(path or "").strip()
        ]
        question_image_paths = [
            str(path or "").strip()
            for path in (full_json.get("question_image_paths") if isinstance(full_json.get("question_image_paths"), list) else [])
            if str(path or "").strip()
        ]
        image_paths = exclude_markdown_embedded_image_paths(image_paths, bank_question.stem_md)
        material_image_paths = exclude_markdown_embedded_image_paths(material_image_paths, bank_question.stem_md)
        question_image_paths = exclude_markdown_embedded_image_paths(question_image_paths, bank_question.stem_md)
        level_parts = [
            str(full_json.get("knowledge_level_1") or "").strip(),
            str(full_json.get("knowledge_level_2") or "").strip(),
            str(full_json.get("knowledge_level_3") or "").strip(),
        ]
        knowledge_display = " / ".join(part for part in level_parts if part)
        source_key = f"bank:{bank_question.id}"
        question_rows.append(
            {
                "id": source_key,
                "source_key": source_key,
                "source_type": "bank",
                "question_no": bank_question.question_no,
                "paper_title": bank_question.paper.title if bank_question.paper_id and bank_question.paper else "试卷管理题库",
                "stem_preview": truncate_plain_text(bank_question.stem_md, 90),
                "stem_html": render_exam_markdown_for_display(bank_question.stem_md),
                "image_paths": image_paths,
                "material_image_paths": material_image_paths,
                "question_image_paths": question_image_paths,
                "option_items": build_exam_option_items(options),
                "knowledge_level_1": str(full_json.get("knowledge_level_1") or "").strip() or "未标注",
                "knowledge_level_2": str(full_json.get("knowledge_level_2") or "").strip() or "未标注",
                "knowledge_level_3": str(full_json.get("knowledge_level_3") or "").strip() or "选填",
                "knowledge_display": knowledge_display or "未标注",
                "level_code": str(bank_question.paper.level if bank_question.paper_id and bank_question.paper else "").strip() or normalized_level_code,
                "is_selected": source_key in selected_keys,
            }
        )
    return question_rows


def build_student_free_practice_context(
    portal_user: PortalUser,
    *,
    level_code: str = "",
    knowledge_query: str = "",
    knowledge_level_2: str = "",
    selected_question_ids: list[object] | None = None,
    error_message: str = "",
    limit_warning: str = "",
) -> dict:
    normalized_level_code = normalize_student_free_practice_level_code(level_code)
    normalized_level_1 = str(knowledge_query or "").strip()
    normalized_level_2 = str(knowledge_level_2 or "").strip()
    if not normalized_level_code:
        normalized_level_1 = ""
        normalized_level_2 = ""
    if not normalized_level_1:
        normalized_level_2 = ""
    selected_ids = {str(question_id).strip() for question_id in (selected_question_ids or []) if str(question_id).strip()}
    question_rows = build_free_practice_question_rows_for_filters(
        level_code=normalized_level_code,
        knowledge_query=normalized_level_1,
        knowledge_level_2=normalized_level_2,
        selected_question_ids=selected_question_ids,
    )
    level1_options = get_student_free_practice_level1_options(normalized_level_code)
    level2_options = get_student_free_practice_level2_options(normalized_level_code, "")
    return {
        "page_title": "自由练习",
        "page_description": "按等级和知识点一级目录、二级目录搜索题目，选择后生成一场自由练习。",
        "breadcrumbs": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习", "href": reverse("student-practice")},
            {"label": "自由练习"},
        ],
        "summary_cards": [
            {"label": "单次上限", "value": f"{FREE_PRACTICE_MAX_QUESTION_COUNT} 题", "hint": "超过会自动回到上限"},
            {"label": "候选题", "value": f"{len(question_rows)} 题", "hint": "按当前条件筛选"},
            {"label": "所选等级", "value": normalized_level_code or "请选择", "hint": "CSP-J/S 可跨类别搜索知识点一级目录"},
        ],
        "level_options": get_student_free_practice_level_options(get_student_by_user(portal_user)),
        "level1_options": level1_options,
        "level2_options": level2_options,
        "knowledge_map_rows": get_knowledge_map_rows_for_selector(),
        "selected_level_code": normalized_level_code,
        "knowledge_query": normalized_level_1,
        "knowledge_level_2": normalized_level_2,
        "level1_disabled": not normalized_level_code,
        "level2_disabled": not normalized_level_1,
        "question_rows": question_rows,
        "selected_question_ids": sorted(selected_ids),
        "max_question_count": FREE_PRACTICE_MAX_QUESTION_COUNT,
        "error_message": error_message,
        "limit_warning": limit_warning,
        "csp_unrestricted": normalized_level_code in {"CSP-J", "CSP-S"},
        "back_href": reverse("student-practice"),
    }


def build_student_homework_list_context(portal_user: PortalUser) -> dict:
    student = get_student_by_user(portal_user)
    assignments = list(
        get_student_homework_queryset(student)
        .annotate(student_list_sort_assigned_at=Coalesce("assigned_at", "created_at"))
        .order_by(F("student_list_sort_assigned_at").desc(nulls_last=True), "-id")
    )
    completed_like = {HomeworkAssignment.STATUS_COMPLETED, HomeworkAssignment.STATUS_REVIEWED}
    pending_count = sum(1 for assignment in assignments if assignment.status == HomeworkAssignment.STATUS_ASSIGNED)
    completed_count = sum(1 for assignment in assignments if assignment.status in completed_like)
    reviewed_count = sum(1 for assignment in assignments if assignment.status == HomeworkAssignment.STATUS_REVIEWED)
    homework_items = [serialize_homework_assignment(item) for item in assignments]
    for item in homework_items:
        item["detail_href"] = reverse("student-homework-detail", args=[item["id"]])
        item["summary_href"] = reverse("student-homework-summary", args=[item["id"]]) if item["has_summary"] else ""
    return {
        "page_title": "我的作业",
        "page_description": "这里集中展示当前学生账号下的全部作业，按布置时间倒序排列。",
        "breadcrumbs": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习", "href": reverse("student-practice")},
            {"label": "我的作业"},
        ],
        "summary_cards": [
            {"label": "作业总数", "value": f"{len(assignments)} 条", "hint": "当前学生全部作业"},
            {"label": "待完成", "value": f"{pending_count} 条", "hint": "还没有标记完成"},
            {"label": "已完成", "value": f"{completed_count} 条", "hint": "包含已评阅作业"},
            {"label": "已评语", "value": f"{reviewed_count} 条", "hint": "老师已写评语并完成评阅"},
        ],
        "homework_items": homework_items,
        "homework_table_rows": build_homework_assignment_table_rows(homework_items),
        "empty_message": "当前还没有老师布置的作业，先继续按课程进度学习。",
    }


def build_student_exam_list_context(
    portal_user: PortalUser,
    *,
    error_message: str = "",
    success_message: str = "",
) -> dict:
    student = get_student_by_user(portal_user)
    sessions = list(
        ExamSession.objects.select_related("paper", "paper__teacher", "paper__course")
        .filter(student=student, is_active=True, paper__is_active=True)
        .order_by("-created_at", "-id")
    )
    assigned_count = sum(1 for session in sessions if session.status == ExamSession.STATUS_ASSIGNED)
    in_progress_count = sum(1 for session in sessions if session.status == ExamSession.STATUS_IN_PROGRESS)
    checked_count = sum(1 for session in sessions if session.status == ExamSession.STATUS_AUTO_CHECKED)
    exam_items = [serialize_exam_session(session) for session in sessions]
    for item in exam_items:
        item["detail_href"] = reverse("student-exam-detail", args=[item["id"]])
        if item["is_finished"]:
            item["action_label"] = "查看结果"
        elif item["is_in_progress"]:
            item["action_label"] = "继续作答"
        else:
            item["action_label"] = "查看并开始"

    sessions_by_paper: dict[int, list[ExamSession]] = defaultdict(list)
    for session in sessions:
        sessions_by_paper[session.paper_id].append(session)
    exam_table_rows = []
    for paper_id, paper_sessions in sessions_by_paper.items():
        latest_session = sorted(paper_sessions, key=lambda item: (item.created_at, item.id), reverse=True)[0]
        paper = latest_session.paper
        meta = collect_exam_paper_question_meta(paper)
        serialized = serialize_exam_session(latest_session)
        exam_table_rows.append(
            {
                "paper_id": paper_id,
                "latest_session_id": latest_session.id,
                "title": paper.title,
                "knowledge_point": meta["knowledge_text"],
                "level_text": meta["level_text"],
                "course_title": serialized["course_title"],
                "teacher_name": serialized["teacher_name"],
                "session_type_text": serialized["session_type_text"],
                "exam_time_text": serialized["time_rule_text"],
                "exam_time_value": (
                    timezone.localtime(paper.start_at or paper.end_at or paper.created_at).date().isoformat()
                    if (paper.start_at or paper.end_at or paper.created_at)
                    else ""
                ),
                "exam_time_sort_value": timezone.localtime(
                    latest_session.created_at or paper.start_at or paper.end_at or paper.created_at
                ).isoformat(),
                "status_text": serialized["status_text"],
                "status_tone": serialized["status_tone"],
                "attempt_count": len(paper_sessions),
                "latest_score": f"{serialized['earned_score']} / {serialized['total_score']}",
                "latest_started_at_text": serialized["started_at_text"],
                "latest_submitted_at_text": serialized["submitted_at_text"],
                "detail_href": reverse("student-exam-record-detail", args=[paper_id]),
                "search_text": " ".join(
                    [
                        paper.title,
                        meta["knowledge_text"],
                        meta["level_text"],
                        serialized["course_title"],
                        serialized["teacher_name"],
                        serialized["session_type_text"],
                        serialized["time_rule_text"],
                        serialized["status_text"],
                    ]
                ),
            }
        )
    exam_table_rows.sort(key=lambda row: (row["exam_time_sort_value"], row["paper_id"]), reverse=True)
    return {
        "page_title": "我的考试",
        "page_description": "这里按试卷展示你的考试记录，输入老师给出的 6 位口令后可开启考试。",
        "breadcrumbs": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习", "href": reverse("student-practice")},
            {"label": "我的考试"},
        ],
        "summary_cards": [
            {"label": "考试总数", "value": f"{len(sessions)} 场", "hint": "当前账号下的全部考试"},
            {"label": "待开始", "value": f"{assigned_count} 场", "hint": "还没有进入考试"},
            {"label": "考试中", "value": f"{in_progress_count} 场", "hint": "已开始但未交卷"},
            {"label": "已判分", "value": f"{checked_count} 场", "hint": "已提交并自动判分"},
        ],
        "exam_items": exam_items,
        "exam_table_rows": exam_table_rows,
        "error_message": error_message,
        "success_message": success_message,
        "empty_message": "当前还没有老师发布的考试。",
    }


def build_student_exam_record_detail_context(portal_user: PortalUser, paper_id: int) -> dict:
    student = get_student_by_user(portal_user)
    paper = (
        ExamPaper.objects.select_related("teacher", "course")
        .filter(id=paper_id, sessions__student=student, sessions__is_active=True, is_active=True)
        .distinct()
        .get()
    )
    sessions = list(
        ExamSession.objects.select_related("paper", "paper__teacher", "paper__course")
        .filter(paper=paper, student=student, is_active=True)
        .order_by("-attempt_no", "-created_at", "-id")
    )
    session_rows = []
    for session in sessions:
        serialized = serialize_exam_session(session)
        if serialized["is_finished"]:
            action_label = "查看结果"
        elif serialized["is_in_progress"]:
            action_label = "继续作答"
        else:
            action_label = "查看并开始"
        session_rows.append(
            {
                **serialized,
                "attempt_no": session.attempt_no,
                "attempt_label": f"第 {session.attempt_no} 次",
                "session_type_text": serialized["session_type_text"],
                "detail_href": reverse("student-exam-detail", args=[session.id]),
                "action_label": action_label,
            }
        )
    meta = collect_exam_paper_question_meta(paper)
    completed_count = sum(
        1
        for session in sessions
        if session.status in {
            ExamSession.STATUS_SUBMITTED,
            ExamSession.STATUS_AUTO_CHECKED,
            ExamSession.STATUS_EXPIRED,
            ExamSession.STATUS_INVALIDATED,
        }
    )
    best_score = max((session.earned_score for session in sessions), default=0)
    return {
        "page_title": paper.title,
        "page_description": "这里展示同一场考试的全部作答记录，点击某一次记录后再进入考试或查看结果。",
        "breadcrumbs": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习", "href": reverse("student-practice")},
            {"label": "我的考试", "href": reverse("student-exam-list")},
            {"label": paper.title},
        ],
        "summary_cards": [
            {"label": "作答记录", "value": f"{len(sessions)} 条", "hint": "同一考试下的全部记录"},
            {"label": "已完成", "value": f"{completed_count} 条", "hint": "已提交、过期或作废的记录"},
            {"label": "最高得分", "value": format_exam_score(best_score), "hint": "按历史记录统计"},
            {"label": "知识点", "value": meta["knowledge_text"], "hint": meta["level_text"]},
        ],
        "paper": paper,
        "session_rows": session_rows,
        "empty_message": "当前考试还没有作答记录。",
        "back_list_href": reverse("student-exam-list"),
    }


def build_student_exam_detail_context(
    portal_user: PortalUser,
    session_id: int,
    *,
    selected_answer_overrides: dict[int, str] | None = None,
    explanation_overrides: dict[int, str] | None = None,
) -> dict:
    student = get_student_by_user(portal_user)
    session = (
        ExamSession.objects.select_related("paper", "paper__teacher", "paper__course")
        .filter(id=session_id, student=student, is_active=True, paper__is_active=True)
        .get()
    )
    questions = get_exam_session_questions(session)
    show_feedback = session.status in {
        ExamSession.STATUS_SUBMITTED,
        ExamSession.STATUS_AUTO_CHECKED,
        ExamSession.STATUS_EXPIRED,
        ExamSession.STATUS_INVALIDATED,
    }
    answers = {
        answer.question_id: answer
        for answer in session.answers.select_related("question").all()
    }
    requires_explanations = session.session_type == ExamSession.SESSION_TYPE_WRONG_PRACTICE
    question_rows = [
        serialize_exam_question(
            question,
            answer=answers.get(question.id),
            show_feedback=show_feedback,
            requires_explanation=requires_explanations,
            selected_answer_override=(selected_answer_overrides or {}).get(question.id),
            student_explanation_override=(explanation_overrides or {}).get(question.id),
            analysis_suggestion_student=student if show_feedback else None,
        )
        for question in questions
    ]
    serialized = serialize_exam_session(session)
    show_answer_sheet = session.status == ExamSession.STATUS_IN_PROGRESS
    show_start_gate = session.status == ExamSession.STATUS_ASSIGNED
    first_exam_wrong_question_count = len(get_wrong_question_ids_from_first_exam_session(session))
    return {
        "page_title": serialized["title"],
        "page_description": "先确认考试模式和时间规则，点击开始后进入作答页。",
        "breadcrumbs": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习", "href": reverse("student-practice")},
            {"label": "我的考试", "href": reverse("student-exam-list")},
            {"label": serialized["title"]},
        ],
        "summary_cards": [
            {"label": "状态", "value": serialized["status_text"], "hint": serialized["session_type_text"]},
            {"label": "模式", "value": serialized["mode_text"], "hint": serialized["time_rule_text"]},
            {"label": "题数", "value": f"{len(question_rows)} 题", "hint": "支持单选、判断，编程题暂展示题面"},
            {"label": "答案缓存", "value": "已开启", "hint": "刷新或返回后会尽量恢复本次作答"},
        ],
        "session": serialized,
        "session_id": session.id,
        "paper": session.paper,
        "question_rows": question_rows,
        "wrong_question_count": first_exam_wrong_question_count,
        "show_feedback": show_feedback,
        "show_start_gate": show_start_gate,
        "show_answer_sheet": show_answer_sheet,
        "requires_explanations": requires_explanations,
        "full_practice_action": "start_full_practice",
        "wrong_practice_action": "start_wrong_practice",
        "print_blank_full_href": reverse("student-exam-print", args=[session.id]) + "?variant=blank_full",
        "print_result_full_href": reverse("student-exam-print", args=[session.id]) + "?variant=result_full",
        "print_blank_wrong_href": reverse("student-exam-print", args=[session.id]) + "?variant=blank_wrong",
        "print_result_wrong_href": reverse("student-exam-print", args=[session.id]) + "?variant=result_wrong",
        "proctor_event_api": reverse("api-student-exam-proctor-event", args=[session.id]),
        "back_list_href": reverse("student-exam-list"),
    }


def build_student_exam_print_context(
    portal_user: PortalUser,
    session_id: int,
    *,
    variant: str,
    hide_important_marks: bool = False,
) -> dict:
    student = get_student_by_user(portal_user)
    session = (
        ExamSession.objects.select_related("paper", "paper__teacher", "paper__course")
        .filter(id=session_id, student=student, is_active=True, paper__is_active=True)
        .get()
    )
    normalized_variant = variant if variant in {"blank_full", "result_full", "blank_wrong", "result_wrong"} else "blank_full"
    blank_only = normalized_variant.startswith("blank_")
    wrong_only = normalized_variant.endswith("_wrong")
    show_result = normalized_variant.startswith("result_")
    questions = get_exam_session_questions(session)
    answer_source_session = session
    answers = {
        answer.question_id: answer
        for answer in answer_source_session.answers.select_related("question").all()
    }
    if wrong_only:
        answer_source_session = get_first_finished_exam_session_for_wrong_practice(session)
        answers = {
            answer.question_id: answer
            for answer in answer_source_session.answers.select_related("question").all()
        }
        wrong_question_ids = set(get_wrong_question_ids_from_first_exam_session(session))
        questions = [
            question
            for question in session.paper.questions.filter(is_active=True).order_by("question_no", "id")
            if question.id in wrong_question_ids
        ]
    question_rows = [
        serialize_exam_question(
            question,
            answer=answers.get(question.id),
            show_feedback=show_result,
        )
        for question in questions
    ]
    serialized = serialize_exam_session(session)
    mode_text = {
        "blank_full": "空白整卷",
        "result_full": "带结果整卷",
        "blank_wrong": "空白错题卷",
        "result_wrong": "完整错题卷",
    }[normalized_variant]
    return {
        "page_title": f"{session.paper.title} - {mode_text}",
        "paper": session.paper,
        "session": serialized,
        "question_rows": question_rows,
        "blank_only": blank_only,
        "wrong_only": wrong_only,
        "show_result": show_result,
        "show_important_marks": not hide_important_marks,
        "show_important_note_text": show_result and session.session_type == ExamSession.SESSION_TYPE_EXAM,
        "print_mode_text": mode_text,
        "meta_items": [
            {"label": "学生", "value": student.display_name},
            {"label": "场次", "value": serialized["session_type_text"]},
            {"label": "记录", "value": f"第 {session.attempt_no} 次"},
            {"label": "得分", "value": f"{serialized['earned_score']} / {serialized['total_score']}"},
        ],
        "empty_message": "这次记录当前没有错题。" if wrong_only else "当前试卷还没有题目。",
    }


def build_homework_option_items(
    options: dict[str, object],
    *,
    selected_answer: str = "",
    correct_answer: str = "",
) -> list[dict[str, object]]:
    decoded_options = decode_sql_ascii_json_text(options)
    if isinstance(decoded_options, dict):
        options = decoded_options
    normalized_selected_answer = str(selected_answer or "").strip().upper()
    normalized_correct_answer = str(correct_answer or "").strip().upper()
    option_items: list[dict[str, object]] = []

    for key in ["A", "B", "C", "D"]:
        raw_text = str(options.get(key) or "").strip()
        if not raw_text:
            continue
        formatted = format_homework_option_display(raw_text)
        is_selected = key == normalized_selected_answer and bool(normalized_selected_answer)
        is_correct_answer = key == normalized_correct_answer and bool(normalized_correct_answer)
        option_items.append(
            {
                "key": key,
                "text": formatted["text"],
                "display_text": formatted["display_text"],
                "is_code_option": formatted["is_code_option"],
                "is_selected": is_selected,
                "is_correct_answer": is_correct_answer,
                "is_wrong_selected": is_selected and normalized_selected_answer != normalized_correct_answer,
            }
        )

    return option_items


def serialize_homework_question(question: HomeworkQuestion) -> dict:
    decoded_options = decode_sql_ascii_json_text(question.options_json)
    options = decoded_options if isinstance(decoded_options, dict) else {}
    analysis = question.analysis or "当前老师没有补充解析。"
    return {
        "id": question.id,
        "question_no": question.question_no,
        "question_type": question.question_type,
        "question_type_text": "单选题",
        "stem": question.stem,
        "stem_display_html": render_exam_markdown_for_display(question.stem),
        "option_items": build_homework_option_items(options),
        "correct_answer": question.correct_answer,
        "analysis": analysis,
        "analysis_display_html": render_exam_markdown_for_display(analysis),
    }


def serialize_homework_import_job(
    import_job: HomeworkImportJob,
    *,
    can_confirm: bool | None = None,
    confirm_disabled_reason: str = "",
) -> dict:
    candidate_rows = normalize_candidate_editor_rows(import_job.candidates_json)
    parse_notes = import_job.parse_notes or "当前没有额外解析备注。"
    ocr_preview = ""
    manual_review_message = ""
    page_message = ""
    for raw_line in parse_notes.splitlines():
        line = raw_line.strip()
        if line.startswith("OCR 文本预览："):
            ocr_preview = line.split("：", 1)[1].strip()
        elif line.startswith("已完成 OCR"):
            manual_review_message = line
        elif line.startswith("页面提示："):
            page_message = line.split("：", 1)[1].strip()
    return {
        "id": import_job.id,
        "source_filename": import_job.source_filename,
        "source_type": import_job.source_type,
        "source_type_text": dict(HomeworkImportJob.SOURCE_TYPE_CHOICES).get(import_job.source_type, import_job.source_type),
        "parse_status": import_job.parse_status,
        "parse_status_text": get_homework_import_status_text(import_job.parse_status),
        "parse_status_tone": get_homework_import_status_tone(import_job.parse_status),
        "parse_notes": parse_notes,
        "candidate_rows": candidate_rows,
        "candidate_count": len(candidate_rows),
        "manual_candidate_start_index": len(candidate_rows),
        "total_candidate_input_count": len(candidate_rows),
        "has_candidates": bool(candidate_rows),
        "ocr_preview": ocr_preview,
        "manual_review_message": manual_review_message,
        "page_message": page_message,
        "source_file_url": import_job.source_file.url if import_job.source_file else "",
        "created_at_text": format_datetime(import_job.created_at),
        "confirmed_at_text": format_datetime(import_job.confirmed_at) if import_job.confirmed_at else "未确认",
        "can_confirm": (
            can_confirm
            if can_confirm is not None
            else import_job.parse_status == HomeworkImportJob.STATUS_PARSED
        ),
        "confirm_disabled_reason": confirm_disabled_reason,
    }


def serialize_homework_submission(submission: HomeworkSubmission | None) -> dict | None:
    if not submission:
        return None
    submitted_at = submission.submitted_at or submission.created_at
    return {
        "id": submission.id,
        "status": submission.status,
        "status_text": get_homework_submission_status_text(submission.status),
        "status_tone": get_homework_submission_status_tone(submission.status),
        "total_count": submission.total_count,
        "correct_count": submission.correct_count,
        "wrong_count": submission.wrong_count,
        "score_text": f"{submission.score}",
        "score_ratio_text": f"{submission.correct_count} / {submission.total_count}" if submission.total_count else "0 / 0",
        "result_summary_text": (
            f"正确 {submission.correct_count} 题，错误 {submission.wrong_count} 题"
            if submission.total_count
            else "当前还没有判分结果"
        ),
        "started_at_text": format_datetime(submission.started_at),
        "submitted_at_text": format_datetime(submitted_at) if submitted_at else "未提交",
        "checked_at_text": format_datetime(submission.checked_at) if submission.checked_at else "未判分",
        "is_locked": is_homework_submission_locked(submission),
    }


def is_homework_submission_locked(submission: HomeworkSubmission | None) -> bool:
    return bool(
        submission
        and submission.status
        in {
            HomeworkSubmission.STATUS_SUBMITTED,
            HomeworkSubmission.STATUS_AUTO_CHECKED,
            HomeworkSubmission.STATUS_REVIEWED,
        }
    )


def get_homework_submission_queryset(
    assignment: HomeworkAssignment,
    student: Student,
) -> QuerySet[HomeworkSubmission]:
    return (
        HomeworkSubmission.objects.select_related("assignment", "student")
        .filter(assignment=assignment, student=student, is_active=True)
        .order_by("-submitted_at", "-created_at", "-id")
    )


def serialize_homework_submission_row(
    submission: HomeworkSubmission,
    *,
    attempt_no: int,
) -> dict:
    serialized = serialize_homework_submission(submission) or {}
    serialized["attempt_no"] = attempt_no
    serialized["attempt_label"] = f"第 {attempt_no} 次提交"
    return serialized


def build_homework_submission_rows(
    submissions: list[HomeworkSubmission],
    *,
    detail_href_builder,
) -> list[dict]:
    attempt_no_by_id = {
        submission.id: index + 1
        for index, submission in enumerate(reversed(submissions))
    }
    rows = []
    for index, submission in enumerate(submissions):
        row = serialize_homework_submission_row(
            submission,
            attempt_no=attempt_no_by_id[submission.id],
        )
        row["detail_href"] = detail_href_builder(submission)
        row["is_latest"] = index == 0
        rows.append(row)
    return rows


def build_homework_assignment_table_rows(homework_items: list[dict]) -> list[dict]:
    completed_like = {HomeworkAssignment.STATUS_COMPLETED, HomeworkAssignment.STATUS_REVIEWED}
    rows = []
    for item in homework_items:
        assignment_title = str(item.get("title") or "").strip()
        topic_title = str(item.get("content_title") or assignment_title).strip()
        level_label = str(item.get("content_level_label") or "").strip()
        topic_subtitle = str(item.get("content_path_label") or "").strip()
        published_at = item.get("assigned_at") or item.get("created_at")
        published_at_display = str(item.get("assigned_at_text") or format_datetime(item.get("created_at")) or "").strip()
        published_at_value = published_at.strftime("%Y-%m-%d") if published_at else ""
        published_at_sort_value = published_at.isoformat() if published_at else ""
        display_title = assignment_title or topic_title
        knowledge_point = topic_title if topic_title and topic_title != display_title else level_label
        if not knowledge_point:
            knowledge_point = topic_subtitle
        status_text = str(item.get("status_text") or "").strip()
        completion_status = "completed" if item.get("status") in completed_like or item.get("is_completed") else "pending"
        completion_status_label = "已完成" if completion_status == "completed" else "未完成"
        rows.append(
            {
                "id": item["id"],
                "assignment_title": assignment_title,
                "topic_title": topic_title,
                "display_title": str(display_title or "").strip(),
                "knowledge_point": str(knowledge_point or "").strip(),
                "topic_subtitle": topic_subtitle,
                "published_at_display": published_at_display,
                "published_at_value": str(published_at_value or "").strip(),
                "published_at_sort_value": str(published_at_sort_value or "").strip(),
                "teacher_name": str(item.get("teacher_name") or "").strip(),
                "status_text": status_text,
                "status_label": status_text,
                "status_tone": str(item.get("status_tone") or "future").strip() or "future",
                "completion_status": completion_status,
                "completion_status_label": completion_status_label,
                "has_summary": bool(item.get("has_summary")),
                "summary_label": str(item.get("summary_status_text") or "未生成").strip() or "未生成",
                "summary_title": str(item.get("summary_title") or "").strip(),
                "summary_href": str(item.get("summary_href") or "").strip(),
                "detail_label": "查看详情",
                "detail_href": str(item.get("detail_href") or "").strip(),
            }
        )
    return rows


def build_homework_answer_map(submission: HomeworkSubmission | None) -> dict[int, HomeworkSubmissionAnswer]:
    if not submission:
        return {}
    return {
        answer.homework_question_id: answer
        for answer in submission.answers.select_related("homework_question").all()
    }


def get_homework_assignment_questions(assignment: HomeworkAssignment) -> list[HomeworkQuestion]:
    return list(assignment.get_effective_questions_queryset())


def build_homework_question_rows_for_assignment(
    assignment: HomeworkAssignment,
    *,
    state: str,
    submission: HomeworkSubmission | None = None,
) -> list[dict]:
    return build_homework_question_view_models(
        get_homework_assignment_questions(assignment),
        state=state,
        answer_map=build_homework_answer_map(submission),
    )


def build_student_submission_question_rows(
    submission: HomeworkSubmission,
    *,
    state: str | None = None,
) -> list[dict]:
    answer_map = {
        answer.homework_question_id: answer
        for answer in submission.answers.select_related("homework_question").all()
    }
    questions = get_homework_assignment_questions(submission.assignment)
    return build_homework_question_view_models(
        questions,
        state=state or get_question_render_state(submission_status=submission.status),
        answer_map=answer_map,
    )


def build_teacher_homework_builder_context(
    portal_user: PortalUser,
    student_id: int,
    assignment_id: int,
    *,
    upload_error_message: str = "",
    upload_success_message: str = "",
    selected_import_job_id: int = 0,
) -> dict:
    assignment = (
        annotate_homework_online_question_counts(
            HomeworkAssignment.objects.select_related(
                "teacher",
                "student",
                "content",
                "content__course",
                "content__level",
                "summary",
                "source_import_job",
            )
        )
        .filter(id=assignment_id, teacher=portal_user, student_id=student_id, is_active=True)
        .get()
    )
    student = assignment.student
    has_submission = assignment.submissions.filter(is_active=True).exists()
    base_confirm_allowed = assignment.status != HomeworkAssignment.STATUS_CANCELLED and not has_submission
    confirm_disabled_reason = ""
    if assignment.status == HomeworkAssignment.STATUS_CANCELLED:
        confirm_disabled_reason = "当前作业已取消，不能再确认导入正式题目。"
    elif has_submission:
        confirm_disabled_reason = "当前作业已有学生提交记录，不能再覆盖正式题目。"
    import_jobs = list(
        assignment.import_jobs.select_related("teacher")
        .filter(is_active=True)
        .order_by("-created_at", "-id")
    )
    if assignment.source_import_job_id and all(job.id != assignment.source_import_job_id for job in import_jobs):
        shared_source_job = (
            HomeworkImportJob.objects.select_related("teacher")
            .filter(id=assignment.source_import_job_id, is_active=True)
            .first()
        )
        if shared_source_job is not None:
            import_jobs.insert(0, shared_source_job)
    serialized_jobs = []
    for job in import_jobs:
        serialized_job = serialize_homework_import_job(
            job,
            can_confirm=base_confirm_allowed and job.parse_status == HomeworkImportJob.STATUS_PARSED,
            confirm_disabled_reason=confirm_disabled_reason,
        )
        serialized_job["detail_href"] = (
            f"{reverse('teacher-homework-builder', args=[student.id, assignment.id])}"
            f"?{urlencode({'import_job_id': job.id})}#candidate-editor"
        )
        serialized_job["is_selected"] = job.id == selected_import_job_id
        serialized_jobs.append(serialized_job)
    latest_job = next((job for job in serialized_jobs if job["id"] == selected_import_job_id), None)
    if latest_job is None:
        latest_job = serialized_jobs[0] if serialized_jobs else None
    confirmed_questions = build_homework_question_view_models(
        get_homework_assignment_questions(assignment),
        state=QUESTION_STATE_PRINT_BLANK,
    )
    return {
        "student": student,
        "assignment": serialize_homework_assignment(assignment),
        "page_title": f"{assignment.title} · 在线选择题",
        "page_description": "在当前作业下上传题目源文件、确认候选单选题，再让学生在线作答和自动判分。",
        "breadcrumbs": [
            {"label": "教师学生列表", "href": reverse("teacher-students")},
            {"label": student.display_name, "href": reverse("teacher-student-detail", args=[student.id])},
            {"label": assignment.title},
        ],
        "summary_cards": [
            {"label": "当前学生", "value": student.display_name, "hint": student.grade or "年级待补充"},
            {"label": "所属知识点", "value": assignment.content.title, "hint": assignment.content.route_path},
            {"label": "正式题目", "value": f"{len(confirmed_questions)} 题", "hint": "老师确认后写入 HomeworkQuestion"},
            {"label": "导入任务", "value": f"{len(serialized_jobs)} 个", "hint": latest_job["parse_status_text"] if latest_job else "还没有导入任务"},
        ],
        "upload_accept": ".pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.html,.htm,.txt,.docx,.xlsx",
        "upload_error_message": upload_error_message,
        "upload_success_message": upload_success_message,
        "latest_import_job": latest_job,
        "import_jobs": serialized_jobs,
        "confirmed_questions": confirmed_questions,
        "question_builder_href": reverse("teacher-homework-builder", args=[student.id, assignment.id]),
        "back_href": reverse("teacher-student-detail", args=[student.id]),
    }


def build_teacher_question_source_import_context(
    portal_user: PortalUser,
    *,
    selected_course_slug: str = "",
    selected_content_id: int = 0,
    selected_import_job_id: int = 0,
    upload_error_message: str = "",
    upload_success_message: str = "",
) -> dict:
    normalized_course_slug = str(selected_course_slug or "").strip().lower()
    if normalized_course_slug:
        scope = get_teacher_course_scope(portal_user, normalized_course_slug)
        selected_course = scope["course"]
    else:
        first_assignment = get_teacher_active_assignments(portal_user).order_by("course_id", "id").first()
        if first_assignment is None:
            raise Course.DoesNotExist("teacher-question-source-import")
        selected_course = first_assignment.course
        normalized_course_slug = selected_course.slug

    content_options = [
        serialize_homework_content_option(content)
        for content in get_teacher_course_homework_contents(portal_user, normalized_course_slug)
    ]
    content_create_level_options = get_teacher_question_source_level_options(portal_user, normalized_course_slug)
    content_option_ids = {item["id"] for item in content_options}
    normalized_content_id = selected_content_id if selected_content_id in content_option_ids else 0
    if not normalized_content_id and content_options:
        normalized_content_id = int(content_options[0]["id"])
    selected_content_option = next(
        (item for item in content_options if item["id"] == normalized_content_id),
        None,
    )

    public_import_jobs = list(
        HomeworkImportJob.objects.select_related("teacher", "content", "content__course")
        .filter(
            teacher=portal_user,
            assignment__isnull=True,
            is_active=True,
        )
        .filter(Q(content__course=selected_course) | Q(content__isnull=True))
        .order_by("-created_at", "-id")
    )
    serialized_jobs = []
    for job in public_import_jobs:
        detail_params = {
            "course": normalized_course_slug,
            "content_id": job.content_id or normalized_content_id,
            "import_job_id": job.id,
        }
        serialized_job = {
            **serialize_homework_import_job(
                job,
                can_confirm=job.parse_status == HomeworkImportJob.STATUS_PARSED,
            ),
            **build_homework_import_job_source_metadata(job),
        }
        serialized_job["detail_href"] = (
            f"{reverse('teacher-question-source-import')}?{urlencode(detail_params)}#candidate-editor"
        )
        serialized_job["is_selected"] = job.id == selected_import_job_id
        serialized_jobs.append(serialized_job)
    selected_job_object = next((job for job in public_import_jobs if job.id == selected_import_job_id), None)
    latest_job_object = selected_job_object or (public_import_jobs[0] if public_import_jobs else None)
    latest_job = next((job for job in serialized_jobs if job["id"] == latest_job_object.id), None) if latest_job_object else None
    confirmed_questions = (
        [serialize_homework_question(question) for question in HomeworkQuestion.objects.filter(import_job=latest_job_object, is_active=True).order_by("question_no", "id")]
        if latest_job_object is not None
        else []
    )

    batch_ready_import_job = (
        get_visible_homework_import_jobs(
            portal_user,
            course_id=selected_course.id,
        )
        .filter(assignment__isnull=True)
        .first()
    )

    back_href = reverse("teacher-homework-batch-create")
    back_params: dict[str, object] = {}
    if normalized_course_slug:
        back_params["course"] = normalized_course_slug
    if batch_ready_import_job is not None:
        back_params["import_job_id"] = batch_ready_import_job.id
    if back_params:
        back_href = f"{back_href}?{urlencode(back_params)}"

    return {
        "page_title": "导入练习题",
        "page_description": "公共题池导入：上传源文件后进入统一解析和确认流程，只创建 HomeworkImportJob 与 HomeworkQuestion，不绑定具体学生作业。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "批量布置作业", "href": back_href},
            {"label": "导入练习题"},
        ],
        "summary_cards": [
            {"label": "当前老师", "value": portal_user.full_name, "hint": portal_user.username},
            {"label": "当前课程", "value": selected_course.title, "hint": selected_course.slug},
            {"label": "可选知识点", "value": f"{len(content_options)} 个", "hint": "按当前老师在该课程下的负责范围汇总"},
            {"label": "公共导入任务", "value": f"{len(serialized_jobs)} 个", "hint": latest_job["parse_status_text"] if latest_job else "还没有公共导入任务"},
        ],
        "upload_accept": ".pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.html,.htm,.txt,.docx,.xlsx",
        "upload_error_message": upload_error_message,
        "upload_success_message": upload_success_message,
        "selected_course_slug": normalized_course_slug,
        "selected_course_label": selected_course.title,
        "selected_content_id": normalized_content_id,
        "selected_content_option": selected_content_option,
        "form_values": build_homework_content_knowledge_form_values(
            course_slug=normalized_course_slug,
            selected_content_option=selected_content_option,
        ),
        "content_options": content_options,
        "content_create_level_options": content_create_level_options,
        "content_create_href": reverse("teacher-question-source-create-content"),
        **build_knowledge_map_selector_context(default_subject=selected_course.slug),
        "content_create_disabled_message": (
            "当前课程下还没有可创建知识点的 Level，请先检查 TeacherStudentAssignment 的课程 / 级别范围。"
            if not content_create_level_options
            else ""
        ),
        "latest_import_job": latest_job,
        "import_jobs": serialized_jobs,
        "confirmed_questions": confirmed_questions,
        "question_source_empty_message": (
            "当前课程下还没有可用知识点。先检查 TeacherStudentAssignment 的课程 / 级别范围。"
            if not content_options
            else ""
        ),
        "back_href": back_href,
    }


def build_student_homework_detail_context(portal_user: PortalUser, assignment_id: int) -> dict:
    student = get_student_by_user(portal_user)
    assignment = get_student_homework_queryset(student).get(id=assignment_id)
    serialized = serialize_homework_assignment(assignment)
    serialized["summary_href"] = reverse("student-homework-summary", args=[assignment.id]) if serialized["has_summary"] else ""
    submissions = list(get_homework_submission_queryset(assignment, student))
    has_online_questions = serialized["is_online_homework"]
    submission_rows = build_homework_submission_rows(
        submissions,
        detail_href_builder=lambda submission: reverse(
            "student-homework-submission-detail",
            args=[assignment.id, submission.id],
        ),
    )
    latest_submission = submission_rows[0] if submission_rows else None
    return {
        "page_title": serialized["title"],
        "page_description": (
            "先看清老师要求，再决定是进入知识点页复习，还是打开新的在线练习。"
            if has_online_questions
            else "先看清老师要求，再决定是进入知识点页完成任务，还是标记作业完成。"
        ),
        "breadcrumbs": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习", "href": reverse("student-practice")},
            {"label": "我的作业", "href": reverse("student-homework-list")},
            {"label": serialized["title"]},
        ],
        "assignment": serialized,
        "has_online_questions": has_online_questions,
        "submission_rows": submission_rows,
        "submission_count": len(submission_rows),
        "latest_submission": latest_submission,
        "practice_href": (
            reverse("student-homework-practice", args=[assignment.id])
            if has_online_questions and not serialized["is_cancelled"]
            else ""
        ),
        "practice_label": "重新练习" if submission_rows else "开始第一次练习",
        "blank_print_href": reverse("student-homework-print-blank", args=[assignment.id]) if has_online_questions else "",
        "submission_empty_message": "这份作业还没有提交记录。开始第一次练习后，每次提交都会新增一条历史记录。",
        "allow_content_entry": True,
        "allow_completion_actions": True,
        "show_mark_completed": serialized["can_mark_completed"] and not has_online_questions,
        "back_list_href": reverse("student-homework-list"),
    }


def build_student_homework_practice_context(portal_user: PortalUser, assignment_id: int) -> dict:
    student = get_student_by_user(portal_user)
    assignment = get_student_homework_queryset(student).get(id=assignment_id)
    serialized = serialize_homework_assignment(assignment)
    if not serialized["is_online_homework"]:
        raise HomeworkAssignment.DoesNotExist
    submissions = list(get_homework_submission_queryset(assignment, student))
    submission_rows = build_homework_submission_rows(
        submissions,
        detail_href_builder=lambda submission: reverse(
            "student-homework-submission-detail",
            args=[assignment.id, submission.id],
        ),
    )
    return {
        "page_title": f"{serialized['title']} · {'重新练习' if submission_rows else '在线作答'}",
        "page_description": "每次提交都会生成一条新的 HomeworkSubmission 历史记录，不会覆盖旧结果。",
        "breadcrumbs": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习", "href": reverse("student-practice")},
            {"label": "我的作业", "href": reverse("student-homework-list")},
            {"label": serialized["title"], "href": reverse("student-homework-detail", args=[assignment.id])},
            {"label": "在线作答"},
        ],
        "assignment": serialized,
        "online_question_rows": build_homework_question_rows_for_assignment(
            assignment,
            state=QUESTION_STATE_ANSWERING,
        ),
        "submission_count": len(submission_rows),
        "latest_submission": submission_rows[0] if submission_rows else None,
        "back_href": reverse("student-homework-detail", args=[assignment.id]),
    }


def build_student_homework_submission_detail_context(
    portal_user: PortalUser,
    assignment_id: int,
    submission_id: int,
) -> dict:
    student = get_student_by_user(portal_user)
    assignment = get_student_homework_queryset(student).get(id=assignment_id)
    submissions = list(get_homework_submission_queryset(assignment, student))
    submission = next((item for item in submissions if item.id == submission_id), None)
    if not submission:
        raise HomeworkSubmission.DoesNotExist
    submission_rows = build_homework_submission_rows(
        submissions,
        detail_href_builder=lambda item: reverse(
            "student-homework-submission-detail",
            args=[assignment.id, item.id],
        ),
    )
    submission_result = next(item for item in submission_rows if item["id"] == submission.id)
    result_question_rows = build_student_submission_question_rows(submission)
    wrong_question_rows = [item for item in result_question_rows if item["is_wrong"]]
    serialized_assignment = serialize_homework_assignment(assignment)
    return {
        "page_title": f"{serialized_assignment['title']} · {submission_result['attempt_label']}",
        "page_description": "这里展示这一次提交的完整判分结果。历史记录会持续保留，可随时切换查看。",
        "breadcrumbs": [
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习", "href": reverse("student-practice")},
            {"label": "我的作业", "href": reverse("student-homework-list")},
            {"label": serialized_assignment["title"], "href": reverse("student-homework-detail", args=[assignment.id])},
            {"label": submission_result["attempt_label"]},
        ],
        "assignment": serialized_assignment,
        "submission": submission_result,
        "submission_rows": submission_rows,
        "result_question_rows": result_question_rows,
        "wrong_question_rows": wrong_question_rows,
        "print_all_href": reverse("student-homework-print", args=[assignment.id, submission.id]),
        "print_wrong_href": reverse("student-homework-print-wrong", args=[assignment.id, submission.id]) if wrong_question_rows else "",
        "print_blank_href": reverse("student-homework-print-blank", args=[assignment.id]),
        "repractice_href": (
            reverse("student-homework-practice", args=[assignment.id])
            if not serialized_assignment["is_cancelled"]
            else ""
        ),
        "back_href": reverse("student-homework-detail", args=[assignment.id]),
        "allow_repractice": not serialized_assignment["is_cancelled"],
    }


def build_parent_homework_list_context(portal_user: PortalUser) -> dict:
    student = get_parent_student(portal_user)
    assignments = list(get_student_homework_queryset(student)) if student else []
    completed_like = {HomeworkAssignment.STATUS_COMPLETED, HomeworkAssignment.STATUS_REVIEWED}
    pending_count = sum(1 for assignment in assignments if assignment.status == HomeworkAssignment.STATUS_ASSIGNED)
    completed_count = sum(1 for assignment in assignments if assignment.status in completed_like)
    homework_items = [serialize_homework_assignment(item) for item in assignments]
    for item in homework_items:
        item["detail_href"] = reverse("parent-homework-detail", args=[item["id"]])
        item["summary_href"] = reverse("parent-homework-summary", args=[item["id"]]) if item["has_summary"] else ""
    return {
        "page_title": "孩子作业记录",
        "page_description": (
            f"这里按 assignment 展示 {student.display_name} 的全部作业记录。"
            if student
            else "当前家长账号还没有绑定学生，暂时无法查看作业记录。"
        ),
        "breadcrumbs": [
            {"label": "家长主页", "href": reverse("parent-student-profile")},
            {"label": "作业记录"},
        ],
        "summary_cards": [
            {"label": "关联孩子", "value": student.display_name if student else "未关联", "hint": student.grade if student else "请先绑定学生"},
            {"label": "作业总数", "value": f"{len(assignments)} 条", "hint": "当前孩子名下的全部 assignment"},
            {"label": "待完成", "value": f"{pending_count} 条", "hint": "当前仍处于待完成状态"},
            {"label": "已完成", "value": f"{completed_count} 条", "hint": "包含已完成和已评阅作业"},
        ],
        "student": student,
        "homework_items": homework_items,
        "homework_table_rows": build_homework_assignment_table_rows(homework_items),
        "empty_message": "当前还没有可查看的作业记录。",
    }


def build_parent_homework_detail_context(portal_user: PortalUser, assignment_id: int) -> dict:
    student = get_parent_student(portal_user)
    if not student:
        raise HomeworkAssignment.DoesNotExist
    assignment = get_student_homework_queryset(student).get(id=assignment_id)
    serialized = serialize_homework_assignment(assignment)
    serialized["summary_href"] = reverse("parent-homework-summary", args=[assignment.id]) if serialized["has_summary"] else ""
    submissions = list(get_homework_submission_queryset(assignment, student))
    submission_rows = build_homework_submission_rows(
        submissions,
        detail_href_builder=lambda submission: reverse(
            "parent-homework-submission-detail",
            args=[assignment.id, submission.id],
        ),
    )
    return {
        "page_title": f"{serialized['title']} · 家长查看",
        "page_description": "家长只读查看模式：可以查看孩子的作业记录、submission 列表和打印页面。",
        "breadcrumbs": [
            {"label": "家长主页", "href": reverse("parent-student-profile")},
            {"label": "作业记录", "href": reverse("parent-homework-list")},
            {"label": serialized["title"]},
        ],
        "assignment": serialized,
        "student": student,
        "has_online_questions": serialized["is_online_homework"],
        "submission_rows": submission_rows,
        "submission_count": len(submission_rows),
        "latest_submission": submission_rows[0] if submission_rows else None,
        "practice_href": "",
        "practice_label": "",
        "blank_print_href": reverse("parent-homework-print-blank", args=[assignment.id]) if serialized["is_online_homework"] else "",
        "submission_empty_message": "孩子目前还没有这份在线作业的提交记录。",
        "allow_content_entry": False,
        "allow_completion_actions": False,
        "show_mark_completed": False,
        "back_list_href": reverse("parent-homework-list"),
    }


def build_homework_summary_detail_context_payload(
    *,
    assignment: HomeworkAssignment,
    student: Student,
    page_title: str,
    page_description: str,
    breadcrumbs: list[dict[str, str]],
    back_href: str,
    back_label: str,
) -> dict:
    summary = getattr(assignment, "summary", None)
    if not summary:
        raise HomeworkSummary.DoesNotExist
    serialized_assignment = serialize_homework_assignment(assignment)
    summary_title = get_homework_summary_title(summary, assignment)
    return {
        "page_title": page_title,
        "page_description": page_description,
        "breadcrumbs": breadcrumbs,
        "assignment": serialized_assignment,
        "student": student,
        "summary": {
            "id": summary.id,
            "title": summary_title,
            "created_at_text": format_datetime(summary.created_at),
            "updated_at_text": format_datetime(summary.updated_at),
            "created_by_name": summary.created_by.full_name if summary.created_by else assignment.teacher.full_name,
            "rendered_html": get_homework_summary_rendered_html(summary),
        },
        "summary_meta_items": [
            {"label": "当前学生", "value": student.display_name},
            {"label": "对应作业", "value": assignment.title},
            {"label": "知识点", "value": serialized_assignment["content_title"]},
            {"label": "最近更新", "value": format_datetime(summary.updated_at)},
        ],
        "summary_overview_cards": [
            {"label": "截止日期", "value": serialized_assignment["due_date_text"], "hint": "沿用 assignment 截止日期"},
            {"label": "当前状态", "value": serialized_assignment["status_text"], "hint": serialized_assignment["homework_mode_text"]},
            {"label": "负责老师", "value": serialized_assignment["teacher_name"], "hint": serialized_assignment["reviewed_at_text"]},
            {"label": "返回入口", "value": back_label, "hint": "打印后可继续回到 assignment 详情"},
        ],
        "back_href": back_href,
    }


def build_student_homework_summary_detail_context(portal_user: PortalUser, assignment_id: int) -> dict:
    student = get_student_by_user(portal_user)
    assignment = get_student_homework_queryset(student).get(id=assignment_id)
    summary = getattr(assignment, "summary", None)
    summary_title = get_homework_summary_title(summary, assignment)
    return build_homework_summary_detail_context_payload(
        assignment=assignment,
        student=student,
        page_title=summary_title,
        page_description="这里集中查看这份作业对应的本周总结，页面支持直接打印。",
        breadcrumbs=[
            {"label": "学生课程页", "href": reverse("student-courses")},
            {"label": "练习", "href": reverse("student-practice")},
            {"label": "我的作业", "href": reverse("student-homework-list")},
            {"label": assignment.title, "href": reverse("student-homework-detail", args=[assignment.id])},
            {"label": "本周总结"},
        ],
        back_href=reverse("student-homework-detail", args=[assignment.id]),
        back_label="返回 assignment 详情",
    )


def build_parent_homework_summary_detail_context(portal_user: PortalUser, assignment_id: int) -> dict:
    student = get_parent_student(portal_user)
    if not student:
        raise HomeworkSummary.DoesNotExist
    assignment = get_student_homework_queryset(student).get(id=assignment_id)
    summary = getattr(assignment, "summary", None)
    summary_title = get_homework_summary_title(summary, assignment)
    return build_homework_summary_detail_context_payload(
        assignment=assignment,
        student=student,
        page_title=f"{summary_title} · 家长查看",
        page_description="家长只读查看模式：可以查看这份作业对应的本周总结，并直接打印。",
        breadcrumbs=[
            {"label": "家长主页", "href": reverse("parent-student-profile")},
            {"label": "作业记录", "href": reverse("parent-homework-list")},
            {"label": assignment.title, "href": reverse("parent-homework-detail", args=[assignment.id])},
            {"label": "本周总结"},
        ],
        back_href=reverse("parent-homework-detail", args=[assignment.id]),
        back_label="返回 assignment 详情",
    )


def build_parent_homework_submission_detail_context(
    portal_user: PortalUser,
    assignment_id: int,
    submission_id: int,
) -> dict:
    student = get_parent_student(portal_user)
    if not student:
        raise HomeworkSubmission.DoesNotExist
    assignment = get_student_homework_queryset(student).get(id=assignment_id)
    submissions = list(get_homework_submission_queryset(assignment, student))
    submission = next((item for item in submissions if item.id == submission_id), None)
    if not submission:
        raise HomeworkSubmission.DoesNotExist
    submission_rows = build_homework_submission_rows(
        submissions,
        detail_href_builder=lambda item: reverse(
            "parent-homework-submission-detail",
            args=[assignment.id, item.id],
        ),
    )
    submission_result = next(item for item in submission_rows if item["id"] == submission.id)
    result_question_rows = build_student_submission_question_rows(submission)
    wrong_question_rows = [item for item in result_question_rows if item["is_wrong"]]
    serialized_assignment = serialize_homework_assignment(assignment)
    return {
        "page_title": f"{serialized_assignment['title']} · {submission_result['attempt_label']} · 家长查看",
        "page_description": "家长只读查看模式：可以查看孩子这一次提交的完整结果并打印。",
        "breadcrumbs": [
            {"label": "家长主页", "href": reverse("parent-student-profile")},
            {"label": "作业记录", "href": reverse("parent-homework-list")},
            {"label": serialized_assignment["title"], "href": reverse("parent-homework-detail", args=[assignment.id])},
            {"label": submission_result["attempt_label"]},
        ],
        "assignment": serialized_assignment,
        "student": student,
        "submission": submission_result,
        "submission_rows": submission_rows,
        "result_question_rows": result_question_rows,
        "wrong_question_rows": wrong_question_rows,
        "print_all_href": reverse("parent-homework-print", args=[assignment.id, submission.id]),
        "print_wrong_href": reverse("parent-homework-print-wrong", args=[assignment.id, submission.id]) if wrong_question_rows else "",
        "print_blank_href": reverse("parent-homework-print-blank", args=[assignment.id]),
        "repractice_href": "",
        "back_href": reverse("parent-homework-detail", args=[assignment.id]),
        "allow_repractice": False,
    }


def build_homework_print_context_payload(
    assignment: HomeworkAssignment,
    *,
    submission: HomeworkSubmission | None = None,
    submission_view: dict | None = None,
    wrong_only: bool = False,
    blank_only: bool = False,
) -> dict:
    serialized_assignment = serialize_homework_assignment(assignment)
    if blank_only:
        question_rows = build_homework_question_rows_for_assignment(
            assignment,
            state=QUESTION_STATE_PRINT_BLANK,
        )
    else:
        question_rows = build_student_submission_question_rows(
            submission,
            state=get_question_render_state(is_print=True),
        )
        if wrong_only:
            question_rows = [item for item in question_rows if item["is_wrong"]]

    meta_items = [
        {"label": "所属知识点", "value": serialized_assignment["content_title"]},
        {"label": "题目总数", "value": str(serialized_assignment["online_question_count"])},
    ]
    if blank_only:
        meta_items.extend(
            [
                {"label": "打印类型", "value": "空白练习卷"},
                {"label": "内容说明", "value": "仅保留题干和选项，不展示作答结果信息"},
            ]
        )
        page_title = f"{assignment.title} · 空白练习卷"
    else:
        meta_items.extend(
            [
                {"label": "提交批次", "value": submission_view["attempt_label"] if submission_view else "提交记录"},
                {"label": "分数", "value": submission_view["score_text"] if submission_view else "0"},
                {"label": "正确 / 错误", "value": f"{submission_view['correct_count']} / {submission_view['wrong_count']}" if submission_view else "0 / 0"},
            ]
        )
        page_title = f"{assignment.title} · {submission_view['attempt_label'] if submission_view else '提交记录'} · {'错题打印' if wrong_only else '整份结果打印'}"
    return {
        "assignment": serialized_assignment,
        "submission": submission_view,
        "question_rows": question_rows,
        "wrong_only": wrong_only,
        "blank_only": blank_only,
        "meta_items": meta_items,
        "page_title": page_title,
    }


def build_student_homework_print_context(
    portal_user: PortalUser,
    assignment_id: int,
    *,
    submission_id: int | None = None,
    wrong_only: bool,
    blank_only: bool = False,
) -> dict:
    student = get_student_by_user(portal_user)
    assignment = get_student_homework_queryset(student).get(id=assignment_id)
    if blank_only:
        return build_homework_print_context_payload(
            assignment,
            wrong_only=False,
            blank_only=True,
        )
    submissions = list(get_homework_submission_queryset(assignment, student))
    submission = next((item for item in submissions if item.id == submission_id), None)
    if not submission:
        raise HomeworkSubmission.DoesNotExist
    submission_rows = build_homework_submission_rows(
        submissions,
        detail_href_builder=lambda item: reverse(
            "student-homework-submission-detail",
            args=[assignment.id, item.id],
        ),
    )
    submission_view = next(item for item in submission_rows if item["id"] == submission.id)
    return build_homework_print_context_payload(
        assignment,
        submission=submission,
        submission_view=submission_view,
        wrong_only=wrong_only,
        blank_only=False,
    )


def build_parent_homework_print_context(
    portal_user: PortalUser,
    assignment_id: int,
    *,
    submission_id: int | None = None,
    wrong_only: bool,
    blank_only: bool = False,
) -> dict:
    student = get_parent_student(portal_user)
    if not student:
        raise HomeworkAssignment.DoesNotExist
    assignment = get_student_homework_queryset(student).get(id=assignment_id)
    if blank_only:
        return build_homework_print_context_payload(
            assignment,
            wrong_only=False,
            blank_only=True,
        )
    submissions = list(get_homework_submission_queryset(assignment, student))
    submission = next((item for item in submissions if item.id == submission_id), None)
    if not submission:
        raise HomeworkSubmission.DoesNotExist
    submission_rows = build_homework_submission_rows(
        submissions,
        detail_href_builder=lambda item: reverse(
            "parent-homework-submission-detail",
            args=[assignment.id, item.id],
        ),
    )
    submission_view = next(item for item in submission_rows if item["id"] == submission.id)
    return build_homework_print_context_payload(
        assignment,
        submission=submission,
        submission_view=submission_view,
        wrong_only=wrong_only,
        blank_only=False,
    )


def build_weekly_homework_summary(student: Student) -> dict:
    # 本周口径统一按 due_date 所在周计算；assigned_at 只用于排序和展示。
    week_start, week_end = get_week_date_range()
    week_start_at, week_end_at = resolve_homework_due_datetime_range(week_start, week_end)
    week_queryset = (
        HomeworkAssignment.objects.select_related("teacher", "content", "content__course", "content__level")
        .filter(student=student, is_active=True, due_date__gte=week_start_at, due_date__lt=week_end_at)
        .order_by("-due_date", "-assigned_at", "-id")
    )
    assignments = list(week_queryset)
    visible_assignments = [item for item in assignments if item.status != HomeworkAssignment.STATUS_CANCELLED]
    completed_like = {HomeworkAssignment.STATUS_COMPLETED, HomeworkAssignment.STATUS_REVIEWED}
    latest_comment_candidates = [item for item in visible_assignments if item.teacher_comment.strip()]
    latest_comment_assignment = max(
        latest_comment_candidates,
        key=lambda item: item.reviewed_at or item.updated_at or item.assigned_at,
        default=None,
    )
    return {
        "week_label": f"{format_date(week_start)} - {format_date(week_end)}",
        "total_count": len(visible_assignments),
        "completed_count": sum(1 for item in visible_assignments if item.status in completed_like),
        "pending_count": sum(1 for item in visible_assignments if item.status == HomeworkAssignment.STATUS_ASSIGNED),
        "latest_comment": {
            "text": latest_comment_assignment.teacher_comment.strip(),
            "teacher_name": latest_comment_assignment.teacher.full_name,
            "updated_at_text": format_datetime(latest_comment_assignment.reviewed_at or latest_comment_assignment.updated_at),
        }
        if latest_comment_assignment
        else None,
        "items": [serialize_homework_assignment(item) for item in visible_assignments[:4]],
    }

def _get_access_map(student: Student, contents: list[CourseContent]) -> dict[int, StudentContentAccess]:
    return {
        access.content_id: access
        for access in StudentContentAccess.objects.select_related("content", "granted_by").filter(
            student=student,
            content__in=contents,
        )
    }


def _get_content_visibility_map(student: Student, contents: list[CourseContent]) -> dict[int, dict[str, object]]:
    access_map = _get_access_map(student, contents)
    course_level_map = get_student_course_level_map(
        student,
        course_ids={content.course_id for content in contents},
    )
    visibility_map: dict[int, dict[str, object]] = {}
    for content in contents:
        access = access_map.get(content.id)
        course_level_code = course_level_map.get(content.course_id, "")
        permission_code = get_content_permission_code(content)
        default_visible = permission_code_allows(course_level_code, permission_code)
        override_mode = "default"
        if access is not None:
            override_mode = "allow" if access.is_open else "deny"
        visibility_map[content.id] = {
            "access": access,
            "course_level_code": course_level_code,
            "permission_code": permission_code,
            "default_visible": default_visible,
            "is_visible": access.is_open if access is not None else default_visible,
            "override_mode": override_mode,
        }
    return visibility_map


def _build_topic_access_item(content: CourseContent, visibility: dict[str, object]) -> dict:
    topic_definition = GESP4_TOPIC_MAP[content.slug]
    is_real_content = topic_definition["content_mode"] == "real"
    content_mode_text = "真实内容" if is_real_content else "内容预留"
    access = visibility["access"]
    is_visible = bool(visibility["is_visible"])
    override_mode = str(visibility["override_mode"])
    permission_code = str(visibility["permission_code"] or "未配置")
    course_level_code = str(visibility["course_level_code"] or "未分配")
    state = "open" if is_visible else "locked"
    open_note = "已接入真实内容页。" if is_real_content else "当前先进入内容预留页。"
    if is_visible:
        if override_mode == "allow" and access is not None:
            student_note = f"教师已单独开放，{open_note}"
            teacher_note = f"单独开放时间：{format_datetime(access.granted_at)}；{open_note}"
            parent_hint = f"教师已单独开放：{format_datetime(access.granted_at)}"
            status_text = "已额外开放"
        else:
            student_note = f"当前等级已覆盖，{open_note}"
            teacher_note = f"按当前等级 {course_level_code} 默认可见；{open_note}"
            parent_hint = f"按当前等级 {course_level_code} 默认可见"
            status_text = "默认可见"
        action_label = "进入专题" if is_real_content else "查看预留页"
        action_href = content.route_path
    else:
        if override_mode == "deny":
            student_note = f"当前已被教师单独关闭；{open_note}"
            teacher_note = f"当前已单独禁用；{open_note}"
            parent_hint = "当前已被教师单独关闭。"
            status_text = "已禁用"
        else:
            student_note = f"当前等级未覆盖 {permission_code}；{open_note}"
            teacher_note = f"当前等级 {course_level_code} 不覆盖 {permission_code}；{open_note}"
            parent_hint = f"当前等级 {course_level_code} 暂不覆盖 {permission_code}。"
            status_text = "默认不可见"
        action_label = ""
        action_href = ""

    return {
        "slug": content.slug,
        "title": content.title,
        "subtitle": topic_definition["subtitle"],
        "summary": content.summary or topic_definition["summary"],
        "route_path": content.route_path,
        "order_label": topic_definition["order_label"],
        "content_mode": topic_definition["content_mode"],
        "content_mode_text": content_mode_text,
        "is_real_content": is_real_content,
        "is_open": is_visible,
        "state": state,
        "status_text": status_text,
        "granted_at": access.granted_at if access else None,
        "granted_at_text": format_datetime(access.granted_at) if access and access.granted_at else "按默认规则生效",
        "granted_by_name": access.granted_by.full_name if access and access.granted_by else "默认规则",
        "student_note": student_note,
        "teacher_note": teacher_note,
        "parent_hint": parent_hint,
        "action_label": action_label,
        "action_href": action_href,
        "toggle_label": "关闭专题" if is_visible else "开放专题",
        "toggle_help": (
            "关闭后会写入学生级禁用 override。"
            if is_visible
            else "开放后会按默认规则恢复，或写入学生级开放 override。"
        ),
        "path_items": ["C++", "GESP", "GESP4", content.title],
        "permission_code": permission_code,
        "course_level_code": course_level_code,
        "override_mode": override_mode,
    }


def get_gesp4_topic_access_items(student: Student) -> list[dict]:
    contents = get_gesp4_topic_contents()
    visibility_map = _get_content_visibility_map(student, contents)
    return [_build_topic_access_item(content, visibility_map[content.id]) for content in contents]


def get_gesp2_knowledge_items(student: Student | None = None) -> list[dict]:
    items = []
    contents = get_gesp2_knowledge_contents()
    visibility_map = _get_content_visibility_map(student, contents) if student else {}
    for content in contents:
        definition = GESP2_KNOWLEDGE_MAP[content.slug]
        is_real_content = definition["content_mode"] == "real"
        if student and not visibility_map[content.id]["is_visible"]:
            continue
        items.append(
            {
                "slug": content.slug,
                "title": content.title,
                "subtitle": definition["subtitle"],
                "summary": content.summary or definition["summary"],
                "route_path": content.route_path,
                "order_label": definition["order_label"],
                "content_mode_text": get_gesp2_content_mode_text(content.slug),
                "is_real_content": is_real_content,
                "state": "open" if is_real_content else "trial",
                "status_text": "真实内容" if is_real_content else "内容预留",
                "action_label": "进入知识点" if is_real_content else "查看预留",
                "note": (
                    "真实知识点页已接入，题目区采用数据库优先、静态兜底。"
                    if is_real_content
                    else "当前先进入统一预留页，后续可直接替换成真实知识点内容。"
                ),
            }
        )
    return items


def _count_course_students(students: list[Student], course_title: str) -> int:
    return sum(1 for student in students if get_student_primary_course(student) == course_title)


def build_student_portal_page(
    page_key: str,
    portal_user: PortalUser,
    *,
    locked_topic_slug: str | None = None,
) -> dict:
    page_shell = deepcopy(STUDENT_PORTAL_CONTENT[page_key])
    student = get_student_by_user(portal_user)

    if page_key == "courses":
        unread_message_count = student.site_messages.filter(is_read=False).count()
        message_summary_card = {
            "label": "消息",
            "value": f"{unread_message_count} 条",
            "href": reverse("student-site-messages"),
            "hint": "老师处理解析挑战后的站内信",
        }
        live_classroom_card = {
            "slug": "live-classroom",
            "title": "实时课堂",
            "meta": "Live",
            "subtitle": "加入老师正在进行的课堂",
            "note": "需要共享整个电脑屏幕，窗口和浏览器标签页会被拒绝。",
            "state": "open",
            "status_text": "可用",
            "action_href": reverse("student-live-classroom"),
            "action_label": "进入课堂",
        }
        if not any(card.get("slug") == live_classroom_card["slug"] for card in page_shell["portal_cards"]):
            page_shell["portal_cards"].insert(0, live_classroom_card)
        if not is_student_portal_exception(student):
            preferred_course_slug = resolve_course_slug(student.primary_course_name, student.primary_level_name)
            filtered_cards = []
            for card in page_shell["portal_cards"]:
                if card["slug"] == "live-classroom":
                    filtered_cards.append(card)
                    continue
                if card["slug"] == "practice":
                    filtered_cards.append(card)
                    continue
                if preferred_course_slug and card["slug"] == preferred_course_slug:
                    filtered_cards.append(card)
            page_shell["portal_cards"] = filtered_cards
            state_counts = {
                "open": sum(1 for card in filtered_cards if card["state"] == "open"),
                "trial": sum(1 for card in filtered_cards if card["state"] == "trial"),
                "locked": sum(1 for card in filtered_cards if card["state"] == "locked"),
            }
            page_shell["summary_cards"] = [
                {"label": "已开放", "value": f"{state_counts['open']} 个"},
                {"label": "体验中", "value": f"{state_counts['trial']} 个"},
                {"label": "未开放", "value": f"{state_counts['locked']} 个"},
                message_summary_card,
            ]
            if preferred_course_slug:
                page_shell["entry_hint"] = "当前只显示与你当前课程匹配的入口，以及作业入口。"
            else:
                page_shell["entry_hint"] = "当前只保留作业入口；课程入口会按学生主课程方向显示。"
        else:
            state_counts = {
                "open": sum(1 for card in page_shell["portal_cards"] if card["state"] == "open"),
                "trial": sum(1 for card in page_shell["portal_cards"] if card["state"] == "trial"),
                "locked": sum(1 for card in page_shell["portal_cards"] if card["state"] == "locked"),
            }
            page_shell["summary_cards"] = [
                {"label": "已开放", "value": f"{state_counts['open']} 个"},
                {"label": "体验中", "value": f"{state_counts['trial']} 个"},
                {"label": "未开放", "value": f"{state_counts['locked']} 个"},
                message_summary_card,
            ]
        return page_shell

    if page_key == "cpp":
        cpp_level_code = get_student_course_level_code(student, course_slug="cpp")
        filtered_cards = [
            card
            for card in page_shell["portal_cards"]
            if should_show_cpp_portal_category(card.get("slug", ""), cpp_level_code)
        ]
        page_shell["portal_cards"] = filtered_cards
        page_shell["summary_cards"] = build_portal_state_summary_cards(filtered_cards)
        if permission_code_allows(cpp_level_code, "C3"):
            page_shell["entry_hint"] = f"当前按等级 {cpp_level_code or '未分配'} 显示 GESP 与 CSP 入口；机器人编程入口已隐藏。"
        else:
            page_shell["entry_hint"] = f"当前按等级 {cpp_level_code or '未分配'} 只显示 GESP 入口；CSP 与机器人编程入口已隐藏。"
        return page_shell

    if page_key == "cpp_gesp":
        gesp2_contents = get_gesp2_knowledge_contents()
        gesp4_contents = get_gesp4_topic_contents()
        gesp2_real_titles = [
            item["title"]
            for item in GESP2_KNOWLEDGE_DEFINITIONS
            if item["content_mode"] == "real"
        ]
        page_shell["summary_cards"] = [
            {"label": "已接入层级", "value": "2 个"},
            {"label": "GESP2 真实内容", "value": f"{len(gesp2_real_titles)} 个"},
            {"label": "专题目录", "value": f"GESP4 · {len(gesp4_contents)} 个"},
        ]
        page_shell["entry_hint"] = (
            f"GESP2 已接入知识点目录，当前真实内容包括 {'、'.join(gesp2_real_titles)}；"
            "GESP4 继续保留多专题目录。"
        )
        page_shell["portal_cards"] = [
            {
                "slug": "gesp1",
                "title": "GESP1",
                "meta": "Level 1",
                "subtitle": "入门基础",
                "note": "当前仍作为层级占位保留。",
                "state": "locked",
                "status_text": "未开放",
            },
            {
                "slug": "gesp2",
                "title": "GESP2",
                "meta": "Level 2",
                "subtitle": "知识点目录已接入",
                "note": f"当前共 {len(gesp2_contents)} 个知识点目录项，已接入 {len(gesp2_real_titles)} 个真实知识点页。",
                "state": "open",
                "status_text": "已接入",
                "featured": True,
                "action_label": "进入 GESP2",
                "action_href": "/student/cpp/gesp/gesp2",
            },
            {
                "slug": "gesp3",
                "title": "GESP3",
                "meta": "Level 3",
                "subtitle": "进阶过渡",
                "note": "当前仍作为层级占位保留。",
                "state": "locked",
                "status_text": "未开放",
            },
            {
                "slug": "gesp4",
                "title": "GESP4",
                "meta": "Level 4",
                "subtitle": "多专题目录已接入",
                "note": f"当前共 {len(gesp4_contents)} 个专题目录项，二维数组专题保留真实内容。",
                "state": "open",
                "status_text": "已接入",
                "featured": True,
                "action_label": "进入 GESP4",
                "action_href": "/student/cpp/gesp/gesp4",
            },
        ]
        return page_shell

    if page_key == "cpp_gesp2":
        knowledge_items = get_gesp2_knowledge_items(student)
        real_count = sum(1 for item in knowledge_items if item["is_real_content"])
        reserved_count = len(knowledge_items) - real_count
        real_titles = [item["title"] for item in knowledge_items if item["is_real_content"]]
        page_shell["portal_cards"] = [
            {
                "slug": item["slug"],
                "title": item["title"],
                "meta": f"{item['order_label']} · {item['content_mode_text']}",
                "subtitle": item["subtitle"],
                "note": item["note"],
                "state": item["state"],
                "status_text": item["status_text"],
                "featured": item["is_real_content"],
                "action_label": item["action_label"],
                "action_href": item["route_path"],
            }
            for item in knowledge_items
        ]
        page_shell["summary_cards"] = [
            {"label": "知识点总数", "value": f"{len(knowledge_items)} 个"},
            {"label": "真实内容", "value": f"{real_count} 个"},
            {"label": "预留内容", "value": f"{reserved_count} 个"},
        ]
        page_shell["entry_hint"] = (
            f"{'、'.join(real_titles)} 已作为 GESP2 真实知识点网页接入，其它知识点当前先进入统一预留页。"
            if knowledge_items
            else "当前等级下还没有可见的 GESP2 知识点。"
        )
        return page_shell

    if page_key != "cpp_gesp4":
        return page_shell

    topic_items = get_gesp4_topic_access_items(student)
    visible_topic_items = [item for item in topic_items if item["is_open"]]
    open_count = len(visible_topic_items)
    total_count = len(topic_items)

    page_shell["portal_cards"] = []
    for item in visible_topic_items:
        card = {
            "slug": item["slug"],
            "title": item["title"],
            "meta": f"{item['order_label']} · {item['content_mode_text']}",
            "subtitle": "真实内容已开放" if item["is_real_content"] else "内容预留已开放",
            "note": item["student_note"],
            "state": item["state"],
            "status_text": item["status_text"],
            "featured": item["is_real_content"],
        }
        card["action_label"] = item["action_label"]
        card["action_href"] = item["action_href"]
        page_shell["portal_cards"].append(card)

    page_shell["summary_cards"] = [
        {"label": "已开放", "value": f"{open_count} 个"},
        {"label": "待开放", "value": f"{total_count - open_count} 个"},
        {"label": "当前阶段", "value": "GESP4"},
    ]
    if locked_topic_slug:
        page_shell["entry_hint"] = get_locked_topic_message(locked_topic_slug)
    elif open_count:
        page_shell["entry_hint"] = f"当前已开放 {open_count} 个 GESP4 专题，可继续进入已开放内容。"
    else:
        page_shell["entry_hint"] = "当前等级下还没有可见的 GESP4 专题。"
    return page_shell


def build_gesp2_reserved_topic_page(topic_slug: str) -> dict:
    content = get_gesp2_knowledge_content(topic_slug)
    topic_definition = GESP2_KNOWLEDGE_MAP[topic_slug]

    return {
        "page_mode": "reserved",
        "hero_eyebrow": "GESP2 Knowledge Placeholder",
        "page_title": f"{content.title} 内容预留页",
        "page_description": f"{content.title} 已进入 GESP2 知识点目录，当前先用统一预留页承接，后续可直接替换成真实知识点网页。",
        "breadcrumb_items": [
            {"label": "学生课程页", "href": "/student/courses"},
            {"label": "C++", "href": "/student/cpp"},
            {"label": "GESP", "href": "/student/cpp/gesp"},
            {"label": "GESP2", "href": "/student/cpp/gesp/gesp2"},
            {"label": content.title},
        ],
        "summary_cards": [
            {"label": "当前知识点", "value": content.title, "hint": topic_definition["order_label"]},
            {"label": "内容状态", "value": "内容预留", "hint": "已纳入真实 CourseContent 目录"},
            {"label": "所属层级", "value": "GESP2", "hint": "后续只需替换内容主体"},
        ],
        "section_eyebrow": "Reserved Knowledge Page",
        "section_title": f"{content.title} 内容预留页",
        "section_description": "当前知识点已经进入 GESP2 知识点目录体系。后续继续补正文时，可以直接替换这里的内容主体，不需要改数据库或路由。",
        "portal_notice": {
            "title": "当前接入边界",
            "description": "这批先把 GESP2 知识点目录落库，并优先把枚举法做成真实内容页。其它知识点先在这里占位。",
            "items": [
                "当前知识点已经拥有真实 slug、标题、排序和内容路由",
                "学生端可以从 GESP2 目录直接进入这个预留承载页",
                "后续只需要替换正文，不需要重做目录层",
            ],
        },
        "reserved_entry": {
            "label": topic_definition["order_label"],
            "status_text": "内容预留",
            "path": ["C++", "GESP", "GESP2", content.title],
            "title": content.title,
            "description": content.summary or topic_definition["summary"],
        },
        "reserved_notes": [
            {
                "title": "目录已落库",
                "description": "当前知识点已经成为数据库中的真实 CourseContent 记录，而不只是页面上的一张卡片。",
            },
            {
                "title": "路由已接通",
                "description": "学生端可以从 GESP2 知识点目录直接进入这里，后续接真内容时不需要改入口。",
            },
            {
                "title": "后续升级方式",
                "description": "将来可以像枚举法一样，直接替换成完整知识点网页。",
            },
        ],
        "support_label": "Knowledge Notes",
        "support_title": "知识点预留说明",
        "support_description": "这里说明当前知识点已经属于真实目录体系，以及后续怎样平滑升级成真实内容页。",
        "support_items": [
            {"title": "目录化承载", "description": "当前知识点已进入 GESP2 目录，而不是挂在静态文案里。"},
            {"title": "可继续扩展", "description": "后续其它 GESP2 知识点可沿用同一条接入路径补页。"},
            {"title": "与 GESP4 并存", "description": "这次新增 GESP2，不会影响 GESP4 多专题链路和二维数组页。"},
        ],
    }


def build_gesp4_reserved_topic_page(topic_slug: str) -> dict:
    content = get_gesp4_topic_content(topic_slug)
    topic_definition = GESP4_TOPIC_MAP[topic_slug]

    return {
        "page_mode": "reserved",
        "hero_eyebrow": "GESP4 Topic Placeholder",
        "page_title": f"{content.title} 内容预留页",
        "page_description": f"{content.title} 已纳入 GESP4 多专题开放框架，当前先用统一预留页承载，后续可平滑替换为真实内容。",
        "breadcrumb_items": [
            {"label": "学生课程页", "href": "/student/courses"},
            {"label": "C++", "href": "/student/cpp"},
            {"label": "GESP", "href": "/student/cpp/gesp"},
            {"label": "GESP4", "href": "/student/cpp/gesp/gesp4"},
            {"label": content.title},
        ],
        "summary_cards": [
            {"label": "当前专题", "value": content.title, "hint": topic_definition["order_label"]},
            {"label": "内容状态", "value": "内容预留", "hint": "已纳入真实 CourseContent 和开放体系"},
            {"label": "所属阶段", "value": "GESP4", "hint": "路由与访问控制已经打通"},
        ],
        "section_eyebrow": "Reserved Topic",
        "section_title": f"{content.title} 内容预留页",
        "section_description": "当前专题已经被纳入统一的多专题开放框架。教师可以开放，学生开放后可以进入这个预留承载页。",
        "portal_notice": {
            "title": "当前接入边界",
            "description": "这个专题已经拥有真实内容项、真实路由和真实访问控制，当前缺少的只是最终内容主体。",
            "items": [
                "当前专题已经是 GESP4 目录中的真实 CourseContent 记录",
                "教师开放后，学生端会从目录页直接进入这个路由",
                "后续只需要替换这里的内容主体，不需要重做开放逻辑",
            ],
        },
        "reserved_entry": {
            "label": topic_definition["order_label"],
            "status_text": "内容预留",
            "path": ["C++", "GESP", "GESP4", content.title],
            "title": content.title,
            "description": content.summary or topic_definition["summary"],
        },
        "reserved_notes": [
            {
                "title": "当前作用",
                "description": "作为 GESP4 多专题框架下的统一预留内容页，先承接开放与访问控制。",
            },
            {
                "title": "访问控制",
                "description": "只有教师已开放的学生才能进入；未开放时后端会直接拦截。",
            },
            {
                "title": "后续升级",
                "description": "将来可直接把这个预留页替换成专题首页或讲次目录，不需要改数据库结构。",
            },
        ],
        "support_label": "Topic Notes",
        "support_title": "专题预留说明",
        "support_description": "这里说明当前专题为什么已经属于真实内容体系，以及后续怎样平滑升级为真实专题页。",
        "support_items": [
            {"title": "已纳入内容项", "description": "该专题已经拥有 slug、标题、阶段、路由和开放状态。"},
            {"title": "教师可控", "description": "教师端已经可以对这个专题执行开放和关闭操作。"},
            {"title": "学生可进入", "description": "一旦开放，学生会从 GESP4 专题目录页直接进入这个预留页。"},
        ],
    }


def build_parent_page_shell(portal_user: PortalUser) -> dict:
    page_shell = deepcopy(ROLE_SHELL_CONTENT["parent"])
    student = get_parent_student(portal_user)
    if not student:
        page_shell["summary_cards"] = [
            {"label": "关联孩子", "value": "0 位", "hint": "当前账号还未关联学生"},
            {"label": "已开放专题", "value": "0 项", "hint": "暂无可查看内容"},
            {"label": "最近开放", "value": "暂无", "hint": "等待教师开放"},
        ]
        page_shell["student_profile"] = {
            "name": "未关联学生",
            "summary": "当前账号还没有绑定学生信息。",
            "campus": "待补充",
            "grade": "待补充",
            "mentor": "待补充",
            "recent_lesson": "暂无数据",
            "avatar": "未",
        }
        page_shell["profile_items"] = []
        page_shell["profile_highlight_label"] = "当前已开放专题"
        page_shell["profile_highlight_value"] = "暂无"
        page_shell["open_content_items"] = []
        page_shell["content_items"] = []
        page_shell["latest_evaluation"] = None
        page_shell["reward_records"] = []
        page_shell["homework_portal_href"] = ""
        page_shell["homework_portal_note"] = ""
        page_shell["weekly_homework_summary"] = {
            "week_label": "",
            "total_count": 0,
            "completed_count": 0,
            "pending_count": 0,
            "latest_comment": None,
            "items": [],
        }
        page_shell["lesson_hour_summary"] = {
            "balance_text": "0",
            "latest_text": "暂无",
            "latest_note": "暂无最近课时变动记录",
            "latest_created_at_text": "暂无记录",
        }
        page_shell["lesson_hour_records"] = []
        return page_shell

    topic_items = get_gesp4_topic_access_items(student)
    open_items = [item for item in topic_items if item["is_open"]]
    evaluation_records = list(student.teacher_evaluations.select_related("teacher")[:3])
    reward_records = list(student.reward_records.select_related("teacher")[:3])
    lesson_hour_records = list(student.lesson_hour_ledgers.select_related("teacher")[:3])
    lesson_hour_summary = build_lesson_hour_summary(student)
    weekly_homework_summary = build_weekly_homework_summary(student)
    latest_open = max(
        (item for item in open_items if item["granted_at"]),
        key=lambda item: item["granted_at"],
        default=None,
    )

    page_shell["summary_cards"] = [
        {"label": "关联孩子", "value": "1 位", "hint": "当前家长账号已绑定学生"},
        {"label": "已开放专题", "value": f"{len(open_items)} 项", "hint": "当前可进入的 GESP4 专题"},
        {
            "label": "最近开放",
            "value": latest_open["granted_at_text"] if latest_open else "暂无",
            "hint": "教师最近一次专题开放记录",
        },
    ]
    page_shell["student_profile"] = {
        "name": student.display_name,
        "summary": f"在读学员 · {build_student_learning_path(student)}",
        "campus": student.campus or "校区待补充",
        "grade": student.grade or "年级待补充",
        "mentor": student.teacher_user.full_name if student.teacher_user else "教师待分配",
        "recent_lesson": summarize_open_topics(topic_items, limit=3),
        "avatar": student.display_name[:1] if student.display_name else "学",
    }
    page_shell["profile_items"] = [
        {"label": "当前学习线", "value": build_student_learning_path(student)},
        {"label": "阶段标签", "value": build_phase_label(len(open_items))},
        {"label": "当前课时余额", "value": lesson_hour_summary["balance_text"]},
        {"label": "GESP4 专题总数", "value": f"{len(topic_items)} 个"},
    ]
    page_shell["profile_highlight_label"] = "当前已开放专题"
    page_shell["profile_highlight_value"] = summarize_open_topics(topic_items, limit=3)
    page_shell["open_content_items"] = open_items
    page_shell["content_items"] = [
        {
            "title": item["title"],
            "subtitle": f"C++ > GESP > GESP4 · {item['content_mode_text']}",
            "description": item["summary"],
            "state": item["state"],
            "status_text": item["status_text"],
            "route_path": item["action_href"],
            "hint": item["parent_hint"],
        }
        for item in topic_items
    ]
    page_shell["latest_evaluation"] = serialize_evaluation(evaluation_records[0]) if evaluation_records else None
    page_shell["reward_records"] = [serialize_reward(record) for record in reward_records]
    page_shell["homework_portal_href"] = reverse("parent-homework-list")
    page_shell["homework_portal_note"] = "进入作业记录页后，可以按 assignment 查看 submission 历史、整份结果、错题页和空白练习卷。"
    page_shell["weekly_homework_summary"] = weekly_homework_summary
    page_shell["lesson_hour_summary"] = lesson_hour_summary
    page_shell["lesson_hour_records"] = [serialize_lesson_hour(record) for record in lesson_hour_records]
    return page_shell


def build_teacher_page_shell(portal_user: PortalUser, *, active_tab: str = "students") -> dict:
    workspace_role = "principal" if portal_user.role == PortalUser.ROLE_PRINCIPAL else "teacher"
    page_shell = deepcopy(ROLE_SHELL_CONTENT["teacher"])
    teacher_courses = {course.id: course for course in get_teacher_profile_courses(portal_user)}
    if workspace_role == "principal":
        for course in Course.objects.filter(teacher_profiles__user__is_active=True).distinct().order_by("id"):
            teacher_courses[course.id] = course
    assignments = list(get_teacher_active_assignments(portal_user))
    allowed_tabs = {"students", "courses", "messages", "teachers"} if workspace_role == "principal" else {"students", "courses", "messages"}
    active_tab = active_tab if active_tab in allowed_tabs else "students"
    assignments_by_student: dict[int, list[TeacherStudentAssignment]] = defaultdict(list)
    assignments_by_course: dict[int, list[TeacherStudentAssignment]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_student[assignment.student_id].append(assignment)
        assignments_by_course[assignment.course_id].append(assignment)
        teacher_courses[assignment.course_id] = assignment.course

    student_rows = []
    total_open_records = 0
    for student_id in sorted(assignments_by_student):
        student_assignments = assignments_by_student[student_id]
        student = student_assignments[0].student
        has_cpp_assignment = any(assignment.course.slug == "cpp" for assignment in student_assignments)
        topic_items = get_gesp4_topic_access_items(student) if has_cpp_assignment else []
        open_items = [item for item in topic_items if item["is_open"]]
        total_open_records += len(open_items)
        lesson_hour_summary = build_lesson_hour_summary(student)
        assignment_scope_text = summarize_teacher_assignment_scope(student_assignments)

        student_rows.append(
            {
                "name": student.display_name,
                "grade": student.grade,
                "parent_phone": student.parent_user.phone if student.parent_user and student.parent_user.phone else "未录入",
                "program": assignment_scope_text,
                "open_topics": f"{len(open_items)}/{len(topic_items)}" if topic_items else "0/0",
                "remaining_hours": lesson_hour_summary["balance_text"],
                "status": f"{len(open_items)}/{len(topic_items)} 已开放" if open_items else "全部未开放",
                "state": "open" if open_items else "locked",
                "note": (
                    f"负责范围：{assignment_scope_text}；已开放：{summarize_open_topics(topic_items, limit=2)}"
                    if open_items
                    else f"负责范围：{assignment_scope_text}。"
                    if not topic_items
                    else f"负责范围：{assignment_scope_text}；GESP4 目录下 6 个专题当前均未开放。"
                ),
                "action_href": reverse("teacher-student-detail", args=[student.id]),
                "action_label": "查看详情",
                "relation_href": reverse("teacher-student-assignments", args=[student.id]),
                "relation_label": "关系管理",
            }
        )

    course_rows = []
    course_order_map = {
        definition["slug"]: index for index, definition in enumerate(TEACHER_COURSE_DEFINITIONS, start=1)
    }
    for course_id, course in teacher_courses.items():
        course_assignments = assignments_by_course.get(course_id, [])
        definition = TEACHER_COURSE_MAP.get(course.slug, build_default_teacher_course_definition(course))
        student_ids = {assignment.student_id for assignment in course_assignments}
        if course.slug == "cpp":
            open_content_count = StudentContentAccess.objects.filter(
                student_id__in=student_ids,
                content__course_id=course_id,
                is_open=True,
            ).count()
        else:
            open_content_count = 0
        course_rows.append(
            {
                "slug": course.slug,
                "level": format_assignment_levels(course_assignments, empty=definition["level"]),
                "title": course.title,
                "student_count": len(student_ids),
                "open_content_count": open_content_count,
                "state": "open",
                "note": (
                    f"{definition['summary']} 当前负责级别：{format_assignment_levels(course_assignments)}"
                    if course_assignments
                    else f"{definition['summary']} 当前课程暂未关联学生。"
                ),
                "action_href": reverse("teacher-course-detail", args=[course.slug]),
                "student_pool_href": reverse("teacher-course-student-pool", args=[course.slug]),
                "action_label": "查看分类" if course.slug == "cpp" else "查看课程",
                "order": course_order_map.get(course.slug, len(course_order_map) + course.id),
            }
        )
    course_rows.sort(key=lambda row: (row["order"], row["title"]))

    current_course_count = len(course_rows)
    teacher_rows = build_principal_teacher_rows() if workspace_role == "principal" else []
    message_rows = build_teacher_analysis_message_rows(portal_user)
    message_count = len(message_rows)
    page_shell["summary_cards"] = [
        {"label": "负责学生", "value": f"{len(assignments_by_student)} 人", "hint": "当前 assignment 覆盖的学生数"},
        {
            "label": "教师数量" if workspace_role == "principal" else "当前课程",
            "value": f"{len(teacher_rows)} 位" if workspace_role == "principal" else f"{current_course_count} 门",
            "hint": "当前可登录教师账号数" if workspace_role == "principal" else "当前有学生在学的课程方向",
        },
        {"label": "已开放内容", "value": f"{total_open_records} 项", "hint": "GESP4 多专题的已开放记录总数"},
        {"label": "消息", "value": f"{message_count} 条", "hint": "待处理的学生解析挑战"},
    ]
    page_shell["section_eyebrow"] = "Principal Workbench" if workspace_role == "principal" else "Teacher Workbench"
    page_shell["section_title"] = "校长工作台" if workspace_role == "principal" else "教师工作台"
    page_shell["section_description"] = ""
    page_shell["tabs"] = build_teacher_workbench_tabs(
        active_tab,
        message_count=message_count,
        workspace_role=workspace_role,
    )
    page_shell["active_tab"] = active_tab
    page_shell["workspace_role"] = workspace_role
    page_shell["students"] = student_rows
    page_shell["courses"] = course_rows
    page_shell["teachers"] = teacher_rows
    page_shell["student_table_rows"] = student_rows
    page_shell["course_table_rows"] = course_rows
    page_shell["teacher_table_rows"] = teacher_rows
    page_shell["message_table_rows"] = message_rows
    page_shell["message_count"] = message_count
    page_shell["student_pool_links"] = [
        {
            "label": "添加新学生",
            "href": course["student_pool_href"],
            "import_label": "导入学生" if teacher_can_import_students(portal_user) else "",
            "import_href": (
                f"{reverse('teacher-course-students-detail', args=[course['slug']])}?open_import=1"
                if teacher_can_import_students(portal_user)
                else ""
            ),
            "homework_batch_label": "布置作业",
            "homework_batch_href": f"{reverse('teacher-homework-batch-create')}?course={course['slug']}",
        }
        for course in course_rows
    ]
    page_shell["student_pool_links"].insert(
        0,
        {
            "label": "考试管理",
            "href": reverse("teacher-exams"),
            "import_label": "",
            "import_href": "",
            "homework_batch_label": "",
            "homework_batch_href": "",
        },
    )
    page_shell["student_pool_links"].insert(
        1,
        {
            "label": "进入实时课堂",
            "href": reverse("teacher-live-classroom"),
            "import_label": "",
            "import_href": "",
            "homework_batch_label": "",
            "homework_batch_href": "",
        },
    )
    if teacher_can_import_students(portal_user) and not course_rows:
        for course in get_teacher_profile_courses(portal_user):
            page_shell["student_pool_links"].insert(
                0,
                {
                    "label": "添加新学生",
                    "href": reverse("teacher-course-student-pool", args=[course.slug]),
                    "import_label": "导入学生",
                    "import_href": f"{reverse('teacher-course-students-detail', args=[course.slug])}?open_import=1",
                    "homework_batch_label": "布置作业",
                    "homework_batch_href": f"{reverse('teacher-homework-batch-create')}?course={course.slug}",
                },
            )
            break
    return page_shell


def build_teacher_homework_stats_context(
    portal_user: PortalUser,
    *,
    period: str = "week",
    anchor_date: date | None = None,
) -> dict:
    selected_period = normalize_teacher_homework_stats_period(period)
    selected_anchor_date = anchor_date or timezone.localdate()
    stats_result = build_homework_completion_stats(
        portal_user,
        period_type=selected_period,
        anchor_date=selected_anchor_date,
    )
    period_start = stats_result["period_start"]
    period_end = stats_result["period_end"]
    period_label = str(stats_result["period_label"])
    managed_students = [item["student"] for item in stats_result["student_stats"]]
    learning_payload = build_student_learning_overview(
        students=managed_students,
        anchor_date=selected_anchor_date,
        include_teacher_fields=True,
        teacher=portal_user,
        use_assignment_status_completion=False,
    )
    learning_rows_by_student_id = {
        int(item["student_id"]): item
        for item in learning_payload.get("students", [])
    }
    period_field_label = "截止日期"
    student_rows: list[dict[str, object]] = []
    for student_stat in stats_result["student_stats"]:
        student = student_stat["student"]
        assignment_items = list(student_stat["assignments"])
        learning_row = learning_rows_by_student_id.get(student.id)
        if learning_row is None:
            continue
        selected_period_summary = dict(learning_row.get(selected_period) or {})
        assignment_count = int(selected_period_summary.get("assignment_count") or 0)
        completed_count = int(selected_period_summary.get("completed_count") or 0)
        on_time_completed_count = int(selected_period_summary.get("on_time_completed_count") or 0)
        delayed_completed_count = int(selected_period_summary.get("delayed_completed_count") or 0)
        incomplete_count = int(selected_period_summary.get("incomplete_count") or 0)
        excluded_undated_count = int(selected_period_summary.get("excluded_undated_count") or 0)
        completion_rate = round((completed_count / assignment_count) * 100, 1) if assignment_count else 0.0
        total_correct_count = sum(
            int(item.get("correct_count") or 0)
            for item in assignment_items
            if item.get("has_online_questions")
        )
        total_wrong_count = sum(
            int(item.get("wrong_count") or 0)
            for item in assignment_items
            if item.get("has_online_questions")
        )
        overall_rate_summary = build_correct_wrong_rate_summary(
            correct_count=total_correct_count,
            wrong_count=total_wrong_count,
        )
        primary_level_name = str(learning_row.get("primary_level_name") or "").strip()
        primary_level_name_display = primary_level_name or TEACHER_HOMEWORK_UNGROUPED_LEVEL_LABEL
        detail_href = ""
        detail_label = ""
        if assignment_count > 0:
            detail_params = {
                "student_id": student.id,
                "period": selected_period,
            }
            if anchor_date is not None:
                detail_params["anchor_date"] = selected_anchor_date.isoformat()
            detail_href = (
                f"{reverse('teacher-homework-stats-student-period-assignments')}?"
                f"{urlencode(detail_params)}"
            )
            detail_label = "查看详情"
        assigned_at_dates = sorted(
            {
                timezone.localtime(item["assignment"].assigned_at).date().isoformat()
                for item in assignment_items
                if getattr(item["assignment"], "assigned_at", None) is not None
            }
        )
        row = {
            "student_id": student.id,
            "display_name": str(learning_row.get("display_name") or student.display_name or "").strip(),
            "student_name": str(learning_row.get("display_name") or student.display_name or "").strip(),
            "teacher_id": int(learning_row.get("teacher_id") or portal_user.id),
            "teacher_name": str(learning_row.get("teacher_name") or portal_user.full_name or portal_user.username).strip(),
            "period": selected_period,
            "period_start": period_start,
            "period_end": period_end,
            "primary_level_name": primary_level_name_display,
            "level_code": primary_level_name,
            "level_code_display": primary_level_name_display,
            "level_code_filter_value": primary_level_name or TEACHER_HOMEWORK_UNGROUPED_LEVEL_FILTER_VALUE,
            "assignment_count": assignment_count,
            "assigned_count": assignment_count,
            "completed_count": completed_count,
            "submitted_count": completed_count,
            "on_time_completed_count": on_time_completed_count,
            "delayed_completed_count": delayed_completed_count,
            "incomplete_count": incomplete_count,
            "missing_count": incomplete_count,
            "excluded_undated_count": excluded_undated_count,
            "completion_rate": completion_rate,
            "completion_rate_text": format_completion_rate(completion_rate),
            "overall_correct_count": int(overall_rate_summary["correct_count"]),
            "overall_wrong_count": int(overall_rate_summary["wrong_count"]),
            "overall_total_answered": int(overall_rate_summary["total_answered"]),
            "overall_correct_rate": float(overall_rate_summary["correct_rate"]),
            "overall_correct_rate_text": str(overall_rate_summary["correct_rate_text"]),
            "overall_wrong_rate": float(overall_rate_summary["wrong_rate"]),
            "overall_wrong_rate_text": str(overall_rate_summary["wrong_rate_text"]),
            "online_assignment_count": int(student_stat["online_assignment_count"] or 0),
            "online_completed_count": int(student_stat["online_completed_count"] or 0),
            "requirement_assignment_count": int(student_stat["requirement_assignment_count"] or 0),
            "requirement_completed_count": int(student_stat["requirement_completed_count"] or 0),
            "incomplete_assignment_ids": list(student_stat["incomplete_assignment_ids"] or []),
            "latest_assigned_at": student_stat.get("latest_assigned_at"),
            "latest_due_date": student_stat.get("latest_due_date"),
            "latest_assigned_at_text": format_datetime(student_stat.get("latest_assigned_at")),
            "latest_due_date_text": format_date(student_stat.get("latest_due_date")),
            "assigned_at_dates": assigned_at_dates,
            "detail_label": detail_label,
            "detail_href": detail_href,
        }
        if selected_period == "week":
            knowledge_points = list((learning_row.get("knowledge_points_by_period") or {}).get("week") or [])
            knowledge_point_lines = build_teacher_homework_knowledge_point_lines(knowledge_points)
            knowledge_point_short_lines = build_teacher_homework_knowledge_point_short_lines(knowledge_points)
            lesson_feedbacks = list(selected_period_summary.get("lesson_feedbacks") or [])
            lesson_feedback_entered = any(
                str(item.get("highlights") or "").strip()
                or str(item.get("areas_for_growth") or "").strip()
                for item in lesson_feedbacks
                if isinstance(item, dict)
            )
            lesson_feedback_status_text = (
                "本周无作业不可评价"
                if not lesson_feedbacks
                else ("本周已评价" if lesson_feedback_entered else "本周未评价")
            )
            highlights = [
                str(item).strip()
                for item in selected_period_summary.get("highlights") or []
                if str(item).strip()
            ]
            areas_for_growth = [
                str(item).strip()
                for item in selected_period_summary.get("areas_for_growth") or []
                if str(item).strip()
            ]
            row.update(
                {
                    "knowledge_points": knowledge_points,
                    "knowledge_points_by_period": {"week": knowledge_points},
                    "knowledge_points_text": "\n".join(knowledge_point_lines),
                    "knowledge_points_short_text": "\n".join(knowledge_point_short_lines),
                    "knowledge_points_search_text": " | ".join(knowledge_point_lines) or "暂无知识点统计",
                    "lesson_feedbacks": lesson_feedbacks,
                    "highlights": highlights,
                    "areas_for_growth": areas_for_growth,
                    "lesson_feedback_count": len(lesson_feedbacks),
                    "lesson_feedback_available": bool(lesson_feedbacks),
                    "lesson_feedback_entered": lesson_feedback_entered,
                    "lesson_feedback_status_text": lesson_feedback_status_text,
                    "lesson_feedback_action_text": "教师评价",
                    "teacher_feedback_disabled_reason": (
                        ""
                        if lesson_feedbacks
                        else "本周无作业不可评价"
                    ),
                    "lesson_feedback_search_text": build_teacher_homework_lesson_feedback_search_text(
                        lesson_feedbacks
                    ),
                }
            )
        student_rows.append(row)

    student_rows = sorted(
        student_rows,
        key=lambda row: (
            -float(row["completion_rate"]),
            -float(row["overall_correct_rate"]),
            str(row["student_name"]),
            int(row["student_id"]),
        ),
    )

    student_level_filter_options = build_teacher_homework_level_filter_options(student_rows=student_rows)
    student_detail_rows = build_teacher_homework_student_detail_rows(
        selected_period=selected_period,
        student_rows=student_rows,
    )
    student_table_column_titles = build_teacher_homework_student_table_column_titles(selected_period=selected_period)

    done_top10 = sorted(
        [row for row in student_rows if row["submitted_count"] > 0],
        key=lambda row: (
            -int(row["submitted_count"]),
            -float(row["completion_rate"]),
            -float(row["overall_correct_rate"]),
            str(row["student_name"]),
            int(row["student_id"]),
        ),
    )[:10]
    missing_top10 = sorted(
        [row for row in student_rows if row["assigned_count"] > 0 and row["missing_count"] > 0],
        key=lambda row: (
            -int(row["missing_count"]),
            -float(row["completion_rate"]),
            -float(row["overall_correct_rate"]),
            str(row["student_name"]),
            int(row["student_id"]),
        ),
    )[:10]

    assigned_count = int(stats_result["summary"]["assignment_count"])
    submitted_count = int(stats_result["summary"]["completed_count"])
    missing_count = int(stats_result["summary"]["incomplete_count"])
    completion_rate = round((submitted_count / assigned_count) * 100, 1) if assigned_count else 0.0
    summary = {
        "assigned_count": assigned_count,
        "submitted_count": submitted_count,
        "completed_count": submitted_count,
        "missing_count": missing_count,
        "incomplete_count": missing_count,
        "excluded_undated_count": int(stats_result["summary"]["excluded_undated_count"]),
        "completion_rate": completion_rate,
        "completion_rate_text": format_completion_rate(completion_rate),
    }
    week_previous_anchor_date = selected_anchor_date - timedelta(days=7)
    week_next_anchor_date = selected_anchor_date + timedelta(days=7)
    week_control_params = {"period": "week"}
    week_previous_href = (
        f"{reverse('teacher-homework-stats')}?"
        f"{urlencode({**week_control_params, 'anchor_date': week_previous_anchor_date.isoformat()})}"
    )
    week_next_href = (
        f"{reverse('teacher-homework-stats')}?"
        f"{urlencode({**week_control_params, 'anchor_date': week_next_anchor_date.isoformat()})}"
    )
    week_range_display_text = (
        f"{period_start.month}月{period_start.day}日 至 {period_end.month}月{period_end.day}日"
        if selected_period == "week"
        else ""
    )

    return {
        "page_title": "学生作业统计",
        "page_description": "按当前教师负责学生实时聚合作业完成情况，周期归属统一按 HomeworkAssignment.due_date 判断；assigned_at 只用于学生明细表展示与搜索。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "学生作业统计"},
        ],
        "summary_cards": [
            {"label": "应交作业数", "value": str(summary["assigned_count"]), "hint": f"按{period_field_label}落在当前周期"},
            {"label": "已完成作业数", "value": str(summary["completed_count"]), "hint": "在线题只要存在完成态提交就算完成；要求型看 assignment 完成状态"},
            {"label": "未完成作业数", "value": str(summary["incomplete_count"]), "hint": "当前周期内仍未完成的作业数量"},
            {"label": "整体完成率", "value": summary["completion_rate_text"], "hint": f"{period_label}老师名下学生整体完成情况"},
        ],
        "tabs": build_teacher_workbench_tabs("homework-stats"),
        "selected_period": selected_period,
        "period_label": period_label,
        "period_range_text": f"{period_start.isoformat()} 至 {period_end.isoformat()}",
        "week_range_display_text": week_range_display_text,
        "week_previous_href": week_previous_href,
        "week_next_href": week_next_href,
        "period_field_label": period_field_label,
        "anchor_date_iso": selected_anchor_date.isoformat(),
        "period_options": [
            {
                "key": option_key,
                "label": option_label,
                "href": (
                    f"{reverse('teacher-homework-stats')}?"
                    f"{urlencode({'period': option_key, **({'anchor_date': selected_anchor_date.isoformat()} if anchor_date is not None else {})})}"
                ),
                "is_active": selected_period == option_key,
            }
            for option_key, option_label in (
                ("week", "周度"),
                ("month", "本月"),
                ("quarter", "本季度"),
            )
        ],
        "managed_student_count": len(managed_students),
        "summary": summary,
        "done_top10": done_top10,
        "missing_top10": missing_top10,
        "students": student_rows,
        "student_table_rows": student_detail_rows,
        "student_detail_rows": student_detail_rows,
        "student_table_column_titles": student_table_column_titles,
        "student_level_filter_options": student_level_filter_options,
    }


def build_teacher_homework_submission_detail_context(
    portal_user: PortalUser,
    *,
    student: Student,
    anchor_date: date | None = None,
) -> dict:
    period_start, period_end, period_label = resolve_teacher_homework_stats_period_bounds("week", anchor_date=anchor_date)
    submission_rows = build_teacher_homework_submission_detail_rows(
        portal_user=portal_user,
        student=student,
        period_start=period_start,
        period_end=period_end,
    )
    return {
        "page_title": "Homework Submission Detail",
        "page_description": "这里只展示当前教师学生在本周截止日期范围内相关作业的 HomeworkSubmission 记录；作业是否进入本周统计由 HomeworkAssignment.due_date 决定。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "学生作业统计", "href": f"{reverse('teacher-homework-stats')}?period=week"},
            {"label": "提交记录详情"},
        ],
        "summary_cards": [
            {"label": "提交记录数", "value": str(len(submission_rows)), "hint": "按 HomeworkSubmission 记录计数"},
            {"label": "统计窗口", "value": period_label, "hint": f"{period_start.strftime('%Y-%m-%d')} 至 {period_end.strftime('%Y-%m-%d')}"},
            {"label": "知识点来源", "value": "assignment/source", "hint": "优先取 source_filename，否则回退到 assignment 内容标题"},
            {"label": "数据范围", "value": "本周 assignment 提交记录", "hint": "统计口径按 due_date，提交时间仅用于记录展示"},
        ],
        "selected_period": "week",
        "period_label": period_label,
        "period_range_text": f"{period_start.strftime('%Y-%m-%d %H:%M')} 至 {period_end.strftime('%Y-%m-%d %H:%M')}",
        "back_href": (
            f"{reverse('teacher-homework-stats')}?"
            f"{urlencode({'period': 'week', **({'anchor_date': anchor_date.isoformat()} if anchor_date else {})})}"
        ),
        "submission_rows": submission_rows,
        "submission_detail_rows": submission_rows,
        "student": student,
    }


def build_teacher_homework_student_period_assignment_detail_context(
    portal_user: PortalUser,
    *,
    student: Student,
    period: str = "month",
    anchor_date: date | None = None,
) -> dict:
    selected_period = normalize_teacher_homework_stats_period(period)
    assignment_rows, stats_result = build_teacher_homework_student_period_assignment_detail_rows(
        portal_user=portal_user,
        student=student,
        period=selected_period,
        anchor_date=anchor_date,
    )
    period_start = stats_result["period_start"]
    period_end = stats_result["period_end"]
    period_label = str(stats_result["period_label"])
    student_stat = stats_result["student_stats"][0] if stats_result["student_stats"] else None
    student_stat = student_stat or {
        "assignment_count": 0,
        "completed_count": 0,
        "incomplete_count": 0,
        "online_assignment_count": 0,
        "online_completed_count": 0,
        "requirement_assignment_count": 0,
        "requirement_completed_count": 0,
        "excluded_undated_count": 0,
    }

    return {
        "page_title": "Student Period Assignments",
        "page_description": f"这里只展示 {student.display_name} 在{period_label}内按 due_date 命中的 assignment 列表；month / quarter 主表只保留学生聚合结果。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "学生作业统计", "href": f"{reverse('teacher-homework-stats')}?period={selected_period}"},
            {"label": "学生周期作业详情"},
        ],
        "summary_cards": [
            {"label": "周期作业数", "value": str(int(student_stat["assignment_count"] or 0)), "hint": f"按 due_date 落在{period_label}范围内"},
            {"label": "已完成", "value": str(int(student_stat["completed_count"] or 0)), "hint": "在线题看完成态 submission；要求型看 assignment 完成状态"},
            {"label": "未完成", "value": str(int(student_stat["incomplete_count"] or 0)), "hint": "当前周期内仍未完成的作业数量"},
            {"label": "排除无截止日期", "value": str(int(student_stat["excluded_undated_count"] or 0)), "hint": "未纳入该周期统计"},
        ],
        "selected_period": selected_period,
        "period_label": period_label,
        "period_range_text": f"{period_start.isoformat()} 至 {period_end.isoformat()}",
        "back_href": (
            f"{reverse('teacher-homework-stats')}?"
            f"{urlencode({'period': selected_period, **({'anchor_date': anchor_date.isoformat()} if anchor_date else {})})}"
        ),
        "assignment_rows": assignment_rows,
        "student_period_assignment_rows": assignment_rows,
        "student": student,
        "student_period_summary": student_stat,
    }


def build_teacher_homework_assignment_submission_detail_context(
    portal_user: PortalUser,
    *,
    student: Student,
    assignment: HomeworkAssignment,
    period: str = "month",
    anchor_date: date | None = None,
) -> dict:
    selected_period = normalize_teacher_homework_stats_period(period)
    period_start, period_end, period_label = resolve_teacher_homework_stats_period_bounds(
        selected_period,
        anchor_date=anchor_date,
    )
    submission_rows = build_teacher_homework_assignment_submission_detail_rows(
        portal_user=portal_user,
        student=student,
        assignment=assignment,
        period_start=period_start,
        period_end=period_end,
        period=selected_period,
    )
    knowledge_point = get_teacher_homework_assignment_knowledge_point(assignment)
    return {
        "page_title": "Homework Submission Detail",
        "page_description": "这里只展示当前教师在所选统计周期内命中的这一份 assignment 的全部 HomeworkSubmission 历史记录，不会对多次提交做去重。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "学生作业统计", "href": f"{reverse('teacher-homework-stats')}?period={selected_period}"},
            {"label": "提交记录详情"},
        ],
        "summary_cards": [
            {"label": "提交记录数", "value": str(len(submission_rows)), "hint": "同一 assignment 下的 HomeworkSubmission 全量记录"},
            {"label": "来源统计周期", "value": period_label, "hint": f"assignment 截止日期位于 {period_start.strftime('%Y-%m-%d')} 至 {period_end.strftime('%Y-%m-%d')}"},
            {"label": "学生", "value": student.display_name, "hint": "只允许查看当前教师负责学生"},
            {"label": "作业 / 知识点", "value": knowledge_point, "hint": f"assignment_id = {assignment.id}"},
        ],
        "selected_period": selected_period,
        "period_label": period_label,
        "period_range_text": f"{period_start.strftime('%Y-%m-%d %H:%M')} 至 {period_end.strftime('%Y-%m-%d %H:%M')}",
        "back_href": (
            f"{reverse('teacher-homework-stats')}?"
            f"{urlencode({'period': selected_period, **({'anchor_date': anchor_date.isoformat()} if anchor_date else {})})}"
        ),
        "submission_rows": submission_rows,
        "assignment_submission_rows": submission_rows,
        "student": student,
        "assignment": assignment,
    }


def build_teacher_homework_submission_answer_detail_context(
    *,
    submission: HomeworkSubmission,
    period: str = "month",
    anchor_date: date | None = None,
) -> dict:
    selected_period = normalize_teacher_homework_stats_period(period)
    period_start, period_end, period_label = resolve_teacher_homework_stats_period_bounds(
        selected_period,
        anchor_date=anchor_date,
    )
    answer_detail_rows = build_teacher_homework_submission_answer_detail_rows(submission=submission)
    knowledge_point = get_teacher_homework_assignment_knowledge_point(submission.assignment)
    submitted_at_text = format_datetime(submission.submitted_at or submission.created_at)

    return {
        "page_title": "Homework Submission Answer Detail",
        "page_description": "这里只展示当前这一条 HomeworkSubmission 记录对应的逐题作答详情；所属统计周期仍由 assignment.due_date 决定。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "学生作业统计", "href": f"{reverse('teacher-homework-stats')}?period={selected_period}"},
            {
                "label": "提交记录详情",
                "href": (
                    f"{reverse('teacher-homework-stats-assignment-submissions')}?"
                    f"{urlencode({'student_id': submission.student_id, 'assignment_id': submission.assignment_id, 'period': selected_period, **({'anchor_date': anchor_date.isoformat()} if anchor_date else {})})}"
                ),
            },
            {"label": "做题详情"},
        ],
        "summary_cards": [
            {"label": "提交时间", "value": submitted_at_text, "hint": "当前这一次 HomeworkSubmission 的提交时间"},
            {"label": "知识点", "value": knowledge_point, "hint": "优先取 HomeworkImportJob.source_filename，否则回退到 assignment 内容标题"},
            {"label": "题目数量", "value": str(len(answer_detail_rows)), "hint": "按 HomeworkSubmissionAnswer 记录逐题展开"},
            {"label": "来源统计周期", "value": period_label, "hint": f"assignment 截止日期位于 {period_start.strftime('%Y-%m-%d')} 至 {period_end.strftime('%Y-%m-%d')}"},
        ],
        "selected_period": selected_period,
        "period_label": period_label,
        "period_range_text": f"{period_start.strftime('%Y-%m-%d %H:%M')} 至 {period_end.strftime('%Y-%m-%d %H:%M')}",
        "back_href": (
            f"{reverse('teacher-homework-stats-assignment-submissions')}?"
            f"{urlencode({'student_id': submission.student_id, 'assignment_id': submission.assignment_id, 'period': selected_period, **({'anchor_date': anchor_date.isoformat()} if anchor_date else {})})}"
        ),
        "answer_detail_rows": answer_detail_rows,
        "submission": submission,
    }


def get_teacher_course_category(course: Course, category_slug: str) -> CourseCategory:
    return get_teacher_course_categories(course).get(slug=category_slug)


def get_teacher_course_level(category: CourseCategory, level_code: str) -> CourseLevel:
    return get_teacher_course_levels(category).get(code=level_code)


def get_teacher_course_content(course: Course, level: CourseLevel, content_slug: str) -> CourseContent:
    return CourseContent.objects.select_related("course", "level", "level__category").get(
        course=course,
        level=level,
        slug=content_slug,
        is_active=True,
    )


def get_teacher_knowledge_teaching_page_link(
    course_slug: str,
    category_slug: str,
    level_code: str,
    content: CourseContent,
) -> dict:
    if course_slug == "cpp" and category_slug == "gesp" and level_code == "GESP2":
        if content.slug == ENUMERATION_METHOD_CONTENT_SLUG:
            return {
                "href": reverse("teacher-cpp-gesp2-enumeration"),
                "label": "进入 Teaching Page",
                "status_text": "已接入",
                "is_placeholder": False,
            }
        if content.slug == ASCII_CHAR_ENCODING_CONTENT_SLUG:
            return {
                "href": reverse("teacher-cpp-gesp2-ascii-char-encoding"),
                "label": "进入 Teaching Page",
                "status_text": "已接入",
                "is_placeholder": False,
            }
    if course_slug == "cpp" and category_slug == "gesp" and level_code == "GESP4":
        if content.slug == ARRAY_2D_CONTENT_SLUG:
            return {
                "href": reverse("teacher-cpp-gesp4-array-2d"),
                "label": "进入 Teaching Page",
                "status_text": "已接入",
                "is_placeholder": False,
            }
        if content.slug == BINARY_SEARCH_CONTENT_SLUG:
            return {
                "href": reverse("teacher-cpp-gesp4-binary-search"),
                "label": "进入 Teaching Page",
                "status_text": "已接入",
                "is_placeholder": False,
            }
        if content.slug == SORTING_CONTENT_SLUG:
            return {
                "href": reverse("teacher-cpp-gesp4-sorting"),
                "label": "进入 Teaching Page",
                "status_text": "已接入",
                "is_placeholder": False,
            }
        if content.slug == STRINGS_CONTENT_SLUG:
            return {
                "href": reverse("teacher-cpp-gesp4-strings"),
                "label": "进入 Teaching Page",
                "status_text": "已接入",
                "is_placeholder": False,
            }

    return {
        "href": reverse(
            "teacher-course-knowledge-point-teaching-page",
            args=[course_slug, category_slug, level_code, content.slug],
        ),
        "label": "查看接入状态",
        "status_text": "暂未接入真实教学页",
        "is_placeholder": True,
    }


def build_teacher_course_detail_context(portal_user: PortalUser, course_slug: str) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    definition = TEACHER_COURSE_MAP.get(course_slug, build_default_teacher_course_definition(course))
    category_queryset = list(get_teacher_course_categories(course))
    content_counts = {
        row["level__category_id"]: row
        for row in (
            CourseContent.objects.filter(course=course, level__isnull=False, is_active=True)
            .values("level__category_id")
            .annotate(
                knowledge_point_count=Count("id"),
                real_content_count=Count("id", filter=Q(has_real_content=True)),
            )
        )
    }
    level_counts = {
        row["category_id"]: row["level_count"]
        for row in (
            CourseLevel.objects.filter(category__course=course, is_active=True)
            .values("category_id")
            .annotate(level_count=Count("id"))
        )
    }

    category_items = []
    for category in category_queryset:
        category_content_summary = content_counts.get(category.id, {})
        knowledge_point_count = category_content_summary.get("knowledge_point_count", 0)
        real_content_count = category_content_summary.get("real_content_count", 0)
        category_items.append(
            {
                "slug": category.slug,
                "title": category.title,
                "summary": category.summary or "当前分类已落库，可继续补 Level 与知识点。",
                "level_count": level_counts.get(category.id, 0),
                "knowledge_point_count": knowledge_point_count,
                "real_content_count": real_content_count,
                "status_text": "已接入" if knowledge_point_count else "内容预留",
                "action_href": reverse("teacher-course-category-detail", args=[course_slug, category.slug]),
            }
        )

    total_open_records = StudentContentAccess.objects.filter(
        student_id__in=scope["student_ids"],
        content__course=course,
        is_open=True,
    ).count()
    total_level_count = sum(item["level_count"] for item in category_items)
    total_knowledge_point_count = sum(item["knowledge_point_count"] for item in category_items)

    return {
        "course_slug": course_slug,
        "course_title": definition["title"],
        "page_title": f"{definition['title']} · Course Categories",
        "page_description": "课程分类页已切到数据库驱动。当前先承接分类入口，再往下进入 Level 和 Knowledge Point。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": definition["title"]},
        ],
        "summary_cards": [
            {"label": "课程级别", "value": format_assignment_levels(scope["course_assignments"], empty=definition["level"]), "hint": "当前教师负责的课程范围"},
            {"label": "分类数", "value": f"{len(category_items)} 个", "hint": "当前课程下已启用的分类"},
            {"label": "Level 数", "value": f"{total_level_count} 个", "hint": "当前分类下已启用的 Level"},
            {"label": "知识点数", "value": f"{total_knowledge_point_count} 个", "hint": f"已开放记录 {total_open_records} 条"},
        ],
        "category_items": category_items,
        "related_student_count": len(scope["related_students"]),
        "related_students_href": reverse("teacher-course-students-detail", args=[course_slug]),
        "student_pool_href": reverse("teacher-course-student-pool", args=[course_slug]),
        "support_items": [
            {"title": "当前作用", "description": "这里是教师课程工作流的第一层，只展示数据库中的课程分类。"},
            {"title": "数据来源", "description": "分类和 Level 都从 PostgreSQL 读取，不再靠静态配置全量写死。"},
            {"title": "权限边界", "description": "课程可见性仍由 TeacherStudentAssignment 决定，分类可见性由当前课程主数据决定。"},
        ],
        "export_href": reverse("teacher-course-export", args=[course_slug]),
    }


def build_teacher_course_category_detail_context(portal_user: PortalUser, course_slug: str, category_slug: str) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    category = get_teacher_course_category(course, category_slug)
    level_queryset = list(get_teacher_course_levels(category))
    content_summary_map = {
        row["level_id"]: row
        for row in (
            CourseContent.objects.filter(course=course, level__category=category, is_active=True)
            .values("level_id")
            .annotate(
                knowledge_point_count=Count("id"),
                real_content_count=Count("id", filter=Q(has_real_content=True)),
            )
        )
    }

    level_items = []
    for level in level_queryset:
        content_summary = content_summary_map.get(level.id, {})
        knowledge_point_count = content_summary.get("knowledge_point_count", 0)
        level_items.append(
            {
                "code": level.code,
                "title": level.title,
                "summary": level.summary or f"{level.title} 当前尚未补说明。",
                "summary_short": shorten_text(level.summary or f"{level.title} 当前尚未补说明。", limit=28),
                "knowledge_point_count": knowledge_point_count,
                "real_content_count": content_summary.get("real_content_count", 0),
                "status_text": "已接入" if knowledge_point_count else "内容预留",
                "action_href": reverse("teacher-course-level-detail", args=[course_slug, category.slug, level.code]),
            }
        )

    return {
        "course": course,
        "course_slug": course_slug,
        "course_title": course.title,
        "category_slug": category.slug,
        "category_title": category.title,
        "page_title": f"{category.title} · Level Grid",
        "page_description": "分类下的 Level 以数据库为准，当前先跑通 data-grid 骨架。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": course.title, "href": reverse("teacher-course-detail", args=[course_slug])},
            {"label": category.title},
        ],
        "summary_cards": [
            {"label": "Level 数", "value": f"{len(level_items)} 个", "hint": "当前分类下启用的 Level"},
            {
                "label": "知识点数",
                "value": f"{sum(item['knowledge_point_count'] for item in level_items)} 个",
                "hint": "当前分类下已落库的 Knowledge Point",
            },
            {"label": "真实内容", "value": f"{sum(item['real_content_count'] for item in level_items)} 个", "hint": "已有真实教学页接入的内容"},
            {"label": "负责学生", "value": f"{len(scope['related_students'])} 人", "hint": "当前老师在该课程方向下的学生数"},
        ],
        "level_items": level_items,
        "level_table_rows": [
            {
                "code": item["code"],
                "title": item["title"],
                "summary": item["summary"],
                "knowledge_point_count": item["knowledge_point_count"],
                "real_content_count": item["real_content_count"],
                "status_text": item["status_text"],
                "action_href": item["action_href"],
            }
            for item in level_items
        ],
        "support_items": [
            {"title": "Level Grid", "description": "这里承接第二层结构，每个 Level 再进入对应的 Knowledge Point grid。"},
            {"title": "当前收口", "description": "这次先落 GESP1 到 GESP8，不继续扩成完整课程后台。"},
        ],
        "export_href": reverse("teacher-course-category-export", args=[course_slug, category.slug]),
    }


def build_teacher_course_students_detail_context(
    portal_user: PortalUser,
    course_slug: str,
    *,
    search_query: str = "",
    page: int = 1,
    page_size: int = DEFAULT_GRID_PAGE_SIZE,
) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    normalized_search_query = search_query.strip()

    student_rows = []
    for student in scope["related_students"]:
        row = {
            "name": student.display_name,
            "grade": student.grade or "待补充",
            "program": summarize_teacher_assignment_scope(scope["assignments_by_student"][student.id]),
            "action_href": reverse("teacher-student-detail", args=[student.id]),
            "action_label": "详情",
            "relation_href": reverse("teacher-student-assignments", args=[student.id]),
            "relation_label": "关系",
        }
        student_rows.append(row)

    return {
        "course": course,
        "course_slug": course_slug,
        "course_title": course.title,
        "page_title": f"{course.title} · 学生列表",
        "page_description": "这里承接课程入口下的学生 datagrid，统一改为 Tabulator 本地分页和过滤。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": course.title, "href": reverse("teacher-course-detail", args=[course_slug])},
            {"label": "学生列表"},
        ],
        "summary_cards": [
            {"label": "课程方向", "value": course.title, "hint": "当前课程方向"},
            {"label": "学生总数", "value": f"{len(student_rows)} 人", "hint": "当前课程方向下的学生数"},
            {"label": "分页模式", "value": "本地分页", "hint": "每页支持 10 / 15 / 50 / 100"},
            {"label": "过滤方式", "value": "姓名模糊查找", "hint": "通过 Tabulator 前端过滤"},
        ],
        "search_query": normalized_search_query,
        "student_rows": student_rows,
        "student_table_rows": student_rows,
        "student_pool_href": reverse("teacher-course-student-pool", args=[course_slug]),
        "homework_batch_href": f"{reverse('teacher-homework-batch-create')}?course={course.slug}",
    }


def build_teacher_homework_batch_create_context(
    portal_user: PortalUser,
    *,
    selected_course_slug: str = "",
    form_values: dict[str, object] | None = None,
    error_message: str = "",
    success_message: str = "",
    batch_result: dict[str, object] | None = None,
) -> dict:
    normalized_course_slug = str(selected_course_slug or "").strip().lower()
    selected_course = None
    if normalized_course_slug:
        selected_course = Course.objects.filter(slug=normalized_course_slug).order_by("id").first()
        if selected_course is None:
            raise Course.DoesNotExist(normalized_course_slug)

    course_assignments = list(get_teacher_active_assignments(portal_user))
    assignments_by_student: dict[int, list[TeacherStudentAssignment]] = defaultdict(list)
    for assignment in course_assignments:
        assignments_by_student[assignment.student_id].append(assignment)

    selected_student_ids = {
        normalize_positive_value(value, default=0, minimum=1)
        for value in (form_values or {}).get("student_ids", [])
    }
    selected_student_ids.discard(0)
    selected_import_job_id = normalize_positive_value((form_values or {}).get("import_job_id"), default=0, minimum=1)
    target_subject = normalize_knowledge_map_subject((form_values or {}).get("target_subject") or "cpp")
    target_category_code = normalize_student_free_practice_level_code((form_values or {}).get("target_category_code") or "")
    target_level_1 = str((form_values or {}).get("target_level_1") or "").strip()
    target_level_2 = str((form_values or {}).get("target_level_2") or "").strip()
    target_level_3 = str((form_values or {}).get("target_level_3") or "").strip()
    free_question_ids = [
        normalize_positive_value(value, default=0, minimum=1)
        for value in (form_values or {}).get("free_question_ids", [])
    ]
    free_question_ids = [value for value in free_question_ids if value]
    batch_content_options = [
        serialize_homework_content_option(content)
        for content in get_teacher_batch_homework_contents(
            portal_user,
            course_slug=normalized_course_slug,
        )
    ]
    batch_content_option_ids = {item["id"] for item in batch_content_options}

    teacher_students = list(
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(teacher_user=portal_user)
        .order_by("id")
    )

    student_rows = []
    for student in teacher_students:
        student_assignments = assignments_by_student.get(student.id, [])
        primary_course_name = str(student.primary_course_name or "").strip()
        primary_level_name = str(student.primary_level_name or "").strip()
        student_rows.append(
            {
                "student_id": student.id,
                "name": student.display_name,
                "grade": student.grade or "待补充",
                "primary_course_name": primary_course_name,
                "primary_level_name": primary_level_name,
                "parent_phone": student.parent_user.phone if student.parent_user and student.parent_user.phone else "未录入",
                "scope_text": (
                    summarize_teacher_assignment_scope(student_assignments)
                    if student_assignments
                    else build_student_learning_path(student)
                ),
                "selected": student.id in selected_student_ids,
            }
        )

    student_level_options_by_subject: dict[str, list[str]] = defaultdict(list)
    permission_order = {code: index for index, code in enumerate(CONTENT_PERMISSION_ORDER)}
    for row in student_rows:
        subject = str(row.get("primary_course_name") or "").strip()
        level = str(row.get("primary_level_name") or "").strip()
        if subject and level:
            student_level_options_by_subject[subject].append(level)
    student_subject_filter_options = [
        {"value": subject, "label": subject}
        for subject in sorted(student_level_options_by_subject.keys(), key=lambda value: value.lower())
    ]
    student_level_filter_options_by_subject = {
        subject: sorted(set(levels), key=lambda value: (permission_order.get(value, len(permission_order)), value.upper(), value))
        for subject, levels in student_level_options_by_subject.items()
    }

    visible_import_jobs = list(
        get_visible_homework_import_jobs(
            portal_user,
            course_id=selected_course.id if selected_course is not None else None,
        )
    )
    import_job_rows = []
    for import_job in visible_import_jobs:
        question_count = len(build_homework_import_job_question_payloads(import_job))
        stored_questions = list(import_job.questions.filter(is_active=True).order_by("question_no", "id"))
        analysis_done_count = 0
        analysis_running_count = 0
        analysis_failed_count = 0
        knowledge_done_count = 0
        knowledge_running_count = 0
        knowledge_failed_count = 0
        for question in stored_questions:
            snapshot = decode_sql_ascii_json_text(question.source_snapshot_json)
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            analysis_status = str(snapshot.get("analysis_status") or "").strip()
            knowledge_status = str(snapshot.get("knowledge_status") or "").strip()
            if analysis_status in {"pending", "running"}:
                analysis_running_count += 1
            elif analysis_status == "failed":
                analysis_failed_count += 1
            elif str(question.analysis or "").strip() or analysis_status == "done":
                analysis_done_count += 1
            if knowledge_status in {"pending", "running"}:
                knowledge_running_count += 1
            elif knowledge_status == "failed":
                knowledge_failed_count += 1
            elif (
                str(snapshot.get("knowledge_level_1") or "").strip()
                and str(snapshot.get("knowledge_level_2") or "").strip()
            ):
                knowledge_done_count += 1
        recognition_total = len(stored_questions) or question_count
        if recognition_total <= 0:
            analysis_status_text = "无题目"
            knowledge_status_text = "无题目"
            can_generate_analysis = False
            can_generate_knowledge = False
        else:
            if analysis_running_count:
                analysis_status_text = f"识别中 {analysis_done_count}/{recognition_total}"
            elif analysis_done_count >= recognition_total:
                analysis_status_text = "识别完成"
            elif analysis_failed_count:
                analysis_status_text = f"部分失败 {analysis_done_count}/{recognition_total}"
            else:
                analysis_status_text = "未识别"
            if knowledge_running_count:
                knowledge_status_text = f"识别中 {knowledge_done_count}/{recognition_total}"
            elif knowledge_done_count >= recognition_total:
                knowledge_status_text = "识别完成"
            elif knowledge_failed_count:
                knowledge_status_text = f"部分失败 {knowledge_done_count}/{recognition_total}"
            else:
                knowledge_status_text = "未识别"
            can_generate_analysis = analysis_running_count == 0
            can_generate_knowledge = knowledge_running_count == 0
        source_metadata = build_homework_import_job_source_metadata(import_job)
        import_job_rows.append(
            {
                "import_job_id": import_job.id,
                "source_filename": import_job.source_filename,
                "teacher_display_name": import_job.teacher.full_name or import_job.teacher.username,
                "teacher_username": import_job.teacher.username,
                **source_metadata,
                "question_count": question_count,
                "created_at_text": format_datetime(import_job.created_at),
                "preview_href": reverse("teacher-homework-import-job-preview", args=[import_job.id]),
                "analysis_status_text": analysis_status_text,
                "knowledge_status_text": knowledge_status_text,
                "can_generate_analysis": can_generate_analysis,
                "can_generate_knowledge": can_generate_knowledge,
                "analysis_action_href": reverse("teacher-homework-import-job-recognition", args=[import_job.id, "analysis"]),
                "knowledge_action_href": reverse("teacher-homework-import-job-recognition", args=[import_job.id, "knowledge"]),
                "selected": import_job.id == selected_import_job_id,
            }
        )

    selected_import_job_row = next(
        (item for item in import_job_rows if item["import_job_id"] == selected_import_job_id),
        None,
    )
    selected_content_id = normalize_positive_value((form_values or {}).get("content_id"), default=0, minimum=1)
    if selected_import_job_row and selected_import_job_row.get("content_id") in batch_content_option_ids:
        selected_content_id = int(selected_import_job_row["content_id"])
    elif selected_content_id not in batch_content_option_ids:
        selected_content_id = int(batch_content_options[0]["id"]) if batch_content_options else 0
    selected_content_option = next(
        (item for item in batch_content_options if item["id"] == selected_content_id),
        None,
    )
    course_filter_label = selected_course.title if selected_course is not None else "全部课程"
    default_summary_title = build_default_batch_homework_summary_title(
        course_label=course_filter_label,
        anchor_date=timezone.localdate(),
    )
    save_action = reverse("teacher-homework-batch-create")
    if selected_course is not None:
        save_action += "?" + urlencode({"course": selected_course.slug})

    question_source_import_href = reverse("teacher-question-source-import")
    if selected_course is not None:
        question_source_import_href += "?" + urlencode({"course": selected_course.slug})
    knowledge_selector_context = build_knowledge_map_selector_context(default_subject=target_subject)
    target_level1_options = [
        row["level_1"]
        for row in knowledge_selector_context["knowledge_map_rows"]
        if row["subject"] == target_subject
        and (not target_category_code or row["category_code"] == target_category_code)
        and row["level_1"]
    ]
    target_level2_options = [
        {
            "level_1": row["level_1"],
            "value": row["level_2"],
        }
        for row in knowledge_selector_context["knowledge_map_rows"]
        if row["subject"] == target_subject
        and (not target_category_code or row["category_code"] == target_category_code)
        and row["level_2"]
    ]
    free_question_rows = build_free_practice_question_rows_for_filters(
        level_code=target_category_code,
        knowledge_query=target_level_1,
        knowledge_level_2=target_level_2,
        selected_question_ids=free_question_ids,
    )

    return {
        "page_title": "批量布置作业",
        "page_description": "先选当前老师名下学生，再选题目来源、填写作业要求，并可一次上传同一篇课后总结统一关联到整批作业。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "批量布置作业"},
        ],
        "summary_cards": [],
        "identity_items": [],
        "student_rows": student_rows,
        "student_table_rows": student_rows,
        "student_subject_filter_options": student_subject_filter_options,
        "student_level_filter_options_by_subject": student_level_filter_options_by_subject,
        "import_job_rows": import_job_rows,
        "import_job_table_rows": import_job_rows,
        "selected_course_slug": selected_course.slug if selected_course is not None else "",
        "selected_course_label": course_filter_label,
        "selected_import_job_row": selected_import_job_row,
        "batch_content_options": batch_content_options,
        "batch_selected_content_option": selected_content_option,
        **knowledge_selector_context,
        "target_level1_options": sorted(set(target_level1_options)),
        "target_level2_options": sorted(
            {f"{item['level_1']}\u241f{item['value']}" for item in target_level2_options}
        ),
        "free_question_rows": free_question_rows,
        "free_practice_max_question_count": FREE_PRACTICE_MAX_QUESTION_COUNT,
        "selected_student_ids": sorted(selected_student_ids),
        "batch_create_error_message": error_message,
        "batch_create_success_message": success_message,
        "import_job_selector_should_open": bool(import_job_rows) or bool(error_message and not selected_import_job_id),
        "form_values": {
            "student_ids": sorted(selected_student_ids),
            "import_job_id": selected_import_job_id or "",
            "content_id": selected_content_id or "",
            "target_subject": target_subject,
            "target_category_code": target_category_code,
            "target_level_1": target_level_1,
            "target_level_2": target_level_2,
            "target_level_3": target_level_3,
            "free_question_ids": free_question_ids,
            "assignment_requirement": str((form_values or {}).get("assignment_requirement") or ""),
            "due_date": str((form_values or {}).get("due_date") or (timezone.localdate() + timedelta(days=6 - timezone.localdate().weekday())).isoformat()),
            "summary_title": str((form_values or {}).get("summary_title") or ""),
            "summary_html": str((form_values or {}).get("summary_html") or ""),
            "summary_highlights": str((form_values or {}).get("summary_highlights") or ""),
            "summary_areas_for_growth": str((form_values or {}).get("summary_areas_for_growth") or ""),
        },
        "summary_title_suggestion": default_summary_title,
        "batch_result": batch_result,
        "save_action": save_action,
        "question_source_import_href": question_source_import_href,
        "back_href": f"{reverse('teacher-students')}?tab=students",
        "support_items": [
            {"title": "学生范围", "description": "后端会再次校验 student_ids 必须都属于当前老师。"},
            {"title": "题目来源", "description": "HomeworkImportJob 现在是可选项；如果选了题源，整批 assignment 会共享同一条 source_import_job。"},
            {"title": "目标知识点", "description": "目标知识点来自知识点映射表，新增后会同步进入当前课程可选知识点。"},
            {"title": "课后总结", "description": "上传 / 粘贴 HTML 时会创建 1 条 HomeworkSummary，并绑定到整批学生作业。"},
            {"title": "交互边界", "description": "整批校验通过后再统一创建，避免半成功半失败让老师难以判断结果。"},
        ],
    }


def build_teacher_course_student_pool_context(
    portal_user: PortalUser,
    course_slug: str,
    *,
    selected_level_code: str = "",
) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    available_level_codes = []
    for level_code in CourseLevel.objects.filter(category__course=course, is_active=True).order_by(
        "category__sort_order",
        "category_id",
        "sort_order",
        "id",
    ).values_list("code", flat=True):
        normalized_level_code = str(level_code or "").strip()
        if normalized_level_code and normalized_level_code not in available_level_codes:
            available_level_codes.append(normalized_level_code)
    for level_code in sorted({assignment.level_code for assignment in scope["course_assignments"] if assignment.level_code}):
        if level_code not in available_level_codes:
            available_level_codes.append(level_code)
    normalized_level_code = selected_level_code.strip().upper()
    if normalized_level_code not in available_level_codes and available_level_codes:
        normalized_level_code = available_level_codes[0]

    active_course_student_ids = set(scope["student_ids"])
    candidate_students = list(
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .exclude(id__in=active_course_student_ids)
        .order_by("display_name", "id")
    )
    candidate_student_ids = [student.id for student in candidate_students]
    active_assignments = list(
        TeacherStudentAssignment.objects.select_related("teacher", "course", "student")
        .filter(student_id__in=candidate_student_ids, is_active=True)
        .order_by("student_id", "teacher_id", "course_id", "level_code", "id")
    )
    assignments_by_student: dict[int, list[TeacherStudentAssignment]] = defaultdict(list)
    for assignment in active_assignments:
        assignments_by_student[assignment.student_id].append(assignment)

    pool_rows = []
    for student in candidate_students:
        student_assignments = assignments_by_student.get(student.id, [])
        if not student_assignments:
            current_scope_text = "当前未挂到任何老师 / 课程"
            pool_reason_text = "未在任何老师名下，可直接加入当前课程方向。"
        else:
            assignment_labels = [
                f"{assignment.teacher.full_name} / {assignment.course.title} {assignment.level_code}"
                for assignment in student_assignments
            ]
            current_scope_text = "；".join(assignment_labels)
            if any(assignment.course_id == course.id and assignment.teacher_id != portal_user.id for assignment in student_assignments):
                pool_reason_text = "当前课程方向已在其他老师名下，但不在你当前课程方向下。"
            elif any(assignment.teacher_id == portal_user.id and assignment.course_id != course.id for assignment in student_assignments):
                pool_reason_text = "当前在你名下的其他课程方向，可补加入当前课程方向。"
            else:
                pool_reason_text = "当前不在你当前课程方向下，可加入当前课程方向。"

        pool_rows.append(
            {
                "student_id": student.id,
                "name": student.display_name,
                "grade": student.grade or "待补充",
                "parent_phone": student.parent_user.phone if student.parent_user and student.parent_user.phone else "未录入",
                "current_scope_text": current_scope_text,
                "pool_reason_text": pool_reason_text,
                "selected": False,
            }
        )

    single_student_permission_level_codes = list(CONTENT_PERMISSION_ORDER) if course.slug == "cpp" else available_level_codes
    single_student_level_name_options = (
        [
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
        ]
        if course.slug == "cpp"
        else available_level_codes
    )
    default_primary_level_name_map = {
        "C1": "GESP1",
        "C2": "GESP5",
        "C3": "CSP-J",
        "C4": "CSP-S",
    }
    default_permission_level_code = (
        normalized_level_code
        if normalized_level_code in single_student_permission_level_codes
        else (single_student_permission_level_codes[0] if single_student_permission_level_codes else "")
    )
    default_primary_level_name = (
        default_primary_level_name_map.get(default_permission_level_code)
        if course.slug == "cpp"
        else (single_student_level_name_options[0] if single_student_level_name_options else "")
    )
    if default_primary_level_name not in single_student_level_name_options and single_student_level_name_options:
        default_primary_level_name = single_student_level_name_options[0]

    teacher_courses = {item.id: item for item in get_teacher_profile_courses(portal_user)}
    teacher_courses[course.id] = course
    course_switch_options = [
        {
            "value": item.slug,
            "label": item.title,
            "href": reverse("teacher-course-student-pool", args=[item.slug]),
            "selected": item.id == course.id,
        }
        for item in sorted(teacher_courses.values(), key=lambda item: (item.slug != "cpp", item.id))
    ]

    return {
        "course": course,
        "course_slug": course_slug,
        "course_title": course.title,
        "course_switch_options": course_switch_options,
        "selected_level_code": normalized_level_code,
        "available_level_codes": available_level_codes,
        "single_student_permission_level_codes": single_student_permission_level_codes,
        "single_student_level_name_options": single_student_level_name_options,
        "single_student_course_options": [
            {
                "value": str(course.id),
                "label": course.title,
                "selected": True,
            }
        ],
        "single_student_form_values": {
            "student_name": "",
            "parent_phone": "",
            "course_id": str(course.id),
            "permission_level_code": default_permission_level_code,
            "primary_level_name": default_primary_level_name,
        },
        "single_student_error_message": "",
        "single_student_modal_should_open": False,
        "page_title": "添加新学生",
        "page_description": "这里集中展示当前不在这个课程方向下的学生，用来批量加入我名下。学生主档不复制，只写 TeacherStudentAssignment。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": course.title, "href": reverse("teacher-course-detail", args=[course_slug])},
            {"label": "学生池"},
        ],
        "summary_cards": [
            {"label": "当前课程方向", "value": course.title, "hint": "只按当前课程方向筛学生池"},
            {"label": "可加入学生", "value": f"{len(pool_rows)} 人", "hint": "当前不在你这个课程方向下的学生"},
            {"label": "当前课程已有人数", "value": f"{len(scope['student_ids'])} 人", "hint": "当前老师在该课程方向下已有 active assignment 的学生"},
            {"label": "目标级别", "value": normalized_level_code or "待选择", "hint": "保存时会统一按这个 level_code 建 assignment"},
        ],
        "identity_items": [
            {"label": "当前课程方向", "value": course.title},
            {"label": "当前老师", "value": portal_user.full_name},
            {"label": "当前目标级别", "value": normalized_level_code or "待选择"},
            {"label": "当前可加入人数", "value": f"{len(pool_rows)} 人"},
        ],
        "search_query": "",
        "pool_rows": pool_rows,
        "pool_table_rows": pool_rows,
        "pool_student_ids": {row["student_id"] for row in pool_rows},
        "save_action": reverse("teacher-course-student-pool", args=[course_slug]),
        "back_href": reverse("teacher-course-students-detail", args=[course_slug]),
        "support_items": [
            {"title": "查询边界", "description": "只排除当前老师在当前课程方向下已有 active assignment 的学生。"},
            {"title": "保存边界", "description": "保存时只操作 TeacherStudentAssignment，不改学生主档，不改其他老师关系。"},
            {"title": "级别处理", "description": "当前先由页面顶部统一选择一个 level_code，再批量加入，先保证最小可用。"},
            {"title": "单个新增", "description": "通过弹窗新增单个学生时，会补齐最小主档、家长联系人和当前老师 / 课程 assignment。"},
        ],
    }


def build_teacher_course_level_detail_context(
    portal_user: PortalUser,
    course_slug: str,
    category_slug: str,
    level_code: str,
    *,
    search_query: str = "",
    page: int = 1,
    page_size: int = DEFAULT_GRID_PAGE_SIZE,
) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    category = get_teacher_course_category(course, category_slug)
    level = get_teacher_course_level(category, level_code)
    knowledge_contents = list(get_teacher_level_contents(level))
    content_ids = [content.id for content in knowledge_contents]
    student_level_map = {
        student_id: pick_highest_permission_code(
            assignment.level_code for assignment in scope["assignments_by_student"][student_id]
        )
        for student_id in scope["student_ids"]
    }
    content_permission_map = {
        content.id: get_content_permission_code(content)
        for content in knowledge_contents
    }
    access_map = {
        (access.student_id, access.content_id): access
        for access in StudentContentAccess.objects.select_related("granted_by").filter(
            student_id__in=scope["student_ids"],
            content_id__in=content_ids,
        )
    } if content_ids else {}
    open_count_map = {
        content.id: sum(
            1
            for student_id in scope["student_ids"]
            if (
                access_map[(student_id, content.id)].is_open
                if (student_id, content.id) in access_map
                else permission_code_allows(student_level_map.get(student_id, ""), content_permission_map.get(content.id, ""))
            )
        )
        for content in knowledge_contents
    }

    knowledge_point_rows = []
    for content in knowledge_contents:
        teaching_page = get_teacher_knowledge_teaching_page_link(course_slug, category.slug, level.code, content)
        row_has_real_content = content.has_real_content or not teaching_page["is_placeholder"]
        knowledge_point_rows.append(
            {
                "title": content.title,
                "slug": content.slug,
                "has_real_content_text": "是" if row_has_real_content else "否",
                "has_real_content": row_has_real_content,
                "permission_href": reverse(
                    "teacher-course-knowledge-point-permissions",
                    args=[course_slug, category.slug, level.code, content.slug],
                ),
                "permission_text": f"{open_count_map.get(content.id, 0)}/{len(scope['student_ids'])} 已开放",
                "permission_label": "管理",
                "teaching_page_href": teaching_page["href"],
                "teaching_page_label": "进入" if not teaching_page["is_placeholder"] else "待接入",
                "teaching_page_status_text": teaching_page["status_text"],
                "edit_href": reverse(
                    "teacher-course-knowledge-point-edit",
                    args=[course_slug, category.slug, level.code, content.slug],
                ),
                "delete_href": reverse(
                    "teacher-course-knowledge-point-delete",
                    args=[course_slug, category.slug, level.code, content.slug],
                ),
            }
        )

    return {
        "course_slug": course_slug,
        "course_title": course.title,
        "category_slug": category.slug,
        "category_title": category.title,
        "level_code": level.code,
        "level_title": level.title,
        "page_title": f"{level.title} · Knowledge Point Grid",
        "page_description": "当前先做最小 Knowledge Point data-grid，支持搜索、权限管理和最小表单操作。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": course.title, "href": reverse("teacher-course-detail", args=[course_slug])},
            {"label": category.title, "href": reverse("teacher-course-category-detail", args=[course_slug, category.slug])},
            {"label": level.title},
        ],
        "summary_cards": [
            {"label": "知识点数", "value": f"{len(knowledge_point_rows)} 个", "hint": "当前 Level 下的 Knowledge Point"},
            {"label": "真实内容", "value": f"{sum(1 for row in knowledge_point_rows if row['has_real_content'])} 个", "hint": "已接入 Teaching Page 的知识点"},
            {"label": "负责学生", "value": f"{len(scope['student_ids'])} 人", "hint": "学生权限管理的候选学生数"},
            {"label": "分页模式", "value": "本地分页", "hint": "每页支持 10 / 15 / 50 / 100"},
        ],
        "search_query": search_query,
        "add_knowledge_point_href": reverse(
            "teacher-course-knowledge-point-new",
            args=[course_slug, category.slug, level.code],
        ),
        "knowledge_point_rows": knowledge_point_rows,
        "knowledge_point_table_rows": knowledge_point_rows,
        "support_items": [
            {"title": "搜索", "description": "当前仅支持 Title 模糊查找，改由 Tabulator 前端过滤。"},
            {"title": "学生权限", "description": "学生权限管理复用 StudentContentAccess，学生集合来自当前老师在该课程方向下的 assignment。"},
            {"title": "按钮状态", "description": "新增 / 修改已接最小表单，删除已接确认页；Teaching Page 对已接入内容可直接进入。"},
        ],
        "export_href": reverse("teacher-course-level-export", args=[course_slug, category.slug, level.code]),
    }


def build_teacher_course_content_access_context(
    portal_user: PortalUser,
    course_slug: str,
    category_slug: str,
    level_code: str,
    content_slug: str,
    *,
    search_query: str = "",
) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    category = get_teacher_course_category(course, category_slug)
    level = get_teacher_course_level(category, level_code)
    content = get_teacher_course_content(course, level, content_slug)

    normalized_search_query = search_query.strip()
    access_map = {
        access.student_id: access
        for access in StudentContentAccess.objects.select_related("granted_by").filter(
            student_id__in=scope["student_ids"],
            content=content,
        )
    }
    content_permission_code = get_content_permission_code(content)
    student_level_map = {
        student_id: pick_highest_permission_code(
            assignment.level_code for assignment in scope["assignments_by_student"][student_id]
        )
        for student_id in scope["student_ids"]
    }
    all_student_rows = []
    for student in scope["related_students"]:
        access = access_map.get(student.id)
        course_level_code = student_level_map.get(student.id, "")
        default_visible = permission_code_allows(course_level_code, content_permission_code)
        is_open = access.is_open if access else default_visible
        if access and access.is_open:
            status_text = "已额外开放"
            hint = format_datetime(access.granted_at)
        elif access and not access.is_open:
            status_text = "已禁用"
            hint = "当前已写入学生级禁用 override"
        elif default_visible:
            status_text = "默认可见"
            hint = f"当前等级 {course_level_code or '未分配'} 自动覆盖 {content_permission_code or '未配置'}"
        else:
            status_text = "默认不可见"
            hint = f"当前等级 {course_level_code or '未分配'} 不覆盖 {content_permission_code or '未配置'}"
        row = {
            "student_id": student.id,
            "name": student.display_name,
            "grade": student.grade or "待补充",
            "scope_text": summarize_teacher_assignment_scope(scope["assignments_by_student"][student.id]),
            "is_open": is_open,
            "selected": is_open,
            "status_text": status_text,
            "hint": hint,
        }
        all_student_rows.append(row)

    open_student_rows = [row for row in all_student_rows if row["is_open"]]
    locked_student_rows = [row for row in all_student_rows if not row["is_open"]]
    open_count = len(open_student_rows)
    filtered_student_count = len(all_student_rows)
    return {
        "course_slug": course_slug,
        "course_title": course.title,
        "category_slug": category.slug,
        "category_title": category.title,
        "level_code": level.code,
        "level_title": level.title,
        "content_slug": content.slug,
        "content_title": content.title,
        "page_title": f"{content.title} · 学生权限管理",
        "page_description": "当前先用独立页面承接学生权限管理，后续再收敛成弹窗交互。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": course.title, "href": reverse("teacher-course-detail", args=[course_slug])},
            {"label": category.title, "href": reverse("teacher-course-category-detail", args=[course_slug, category.slug])},
            {"label": level.title, "href": reverse("teacher-course-level-detail", args=[course_slug, category.slug, level.code])},
            {"label": content.title},
        ],
        "summary_cards": [
            {"label": "适用学生", "value": f"{len(scope['related_students'])} 人", "hint": "当前老师在该课程方向下的有效学生"},
            {"label": "筛选结果", "value": f"{filtered_student_count} 人", "hint": "当前知识点可配置的学生数"},
            {"label": "已开通", "value": f"{open_count} 人", "hint": "当前知识点已开通学生数"},
            {"label": "未开通", "value": f"{filtered_student_count - open_count} 人", "hint": "当前知识点未开通学生数"},
        ],
        "identity_items": [
            {
                "label": "知识点名称",
                "value": content.title,
            },
            {"label": "所属课程方向", "value": course.title},
            {"label": "所属分类", "value": category.title},
            {"label": "所属 Level", "value": level.title},
            {"label": "权限归属", "value": content_permission_code or "未配置"},
        ],
        "content": content,
        "open_count": open_count,
        "total_count": len(scope["related_students"]),
        "search_query": normalized_search_query,
        "student_rows": all_student_rows,
        "open_student_rows": open_student_rows,
        "locked_student_rows": locked_student_rows,
        "student_access_table_rows": all_student_rows,
        "student_map": scope["student_map"],
        "save_action": reverse(
            "teacher-course-knowledge-point-permissions",
            args=[course_slug, category.slug, level.code, content.slug],
        ),
        "back_href": reverse("teacher-course-level-detail", args=[course_slug, category.slug, level.code]),
    }


def build_teacher_course_workflow_placeholder_context(
    portal_user: PortalUser,
    course_slug: str,
    category_slug: str,
    level_code: str,
    *,
    action_label: str,
    content_slug: str | None = None,
    placeholder_message: str | None = None,
) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    category = get_teacher_course_category(course, category_slug)
    level = get_teacher_course_level(category, level_code)
    content = get_teacher_course_content(course, level, content_slug) if content_slug else None
    target_title = content.title if content else level.title

    return {
        "page_title": f"{action_label} · {target_title}",
        "page_description": "这一步当前先保留路由和页面占位，后续再接真实交互。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": course.title, "href": reverse("teacher-course-detail", args=[course_slug])},
            {"label": category.title, "href": reverse("teacher-course-category-detail", args=[course_slug, category.slug])},
            {"label": level.title, "href": reverse("teacher-course-level-detail", args=[course_slug, category.slug, level.code])},
            {"label": action_label},
        ],
        "summary_cards": [
            {"label": "课程", "value": course.title, "hint": "当前课程方向"},
            {"label": "分类", "value": category.title, "hint": "当前分类"},
            {"label": "Level", "value": level.title, "hint": "当前级别"},
            {"label": "对象", "value": target_title, "hint": "当前操作目标"},
        ],
        "target_title": target_title,
        "action_label": action_label,
        "placeholder_message": placeholder_message or "这条路由已经接好，但具体交互还没有展开。",
        "back_href": reverse("teacher-course-level-detail", args=[course_slug, category.slug, level.code]),
    }


def build_teacher_student_detail_context(
    portal_user: PortalUser,
    student_id: int,
    *,
    homework_form_values: dict[str, object] | None = None,
    homework_error_message: str = "",
    homework_success_message: str = "",
    homework_summary_form_values: dict[str, object] | None = None,
    homework_summary_error_message: str = "",
    exam_form_values: dict[str, object] | None = None,
    exam_error_message: str = "",
    exam_success_message: str = "",
) -> dict:
    student_assignments = list(
        TeacherStudentAssignment.objects.select_related("course", "student", "student__user", "student__parent_user", "student__teacher_user")
        .filter(teacher=portal_user, student_id=student_id, is_active=True)
        .order_by("course_id", "level_code", "id")
    )
    if not student_assignments:
        raise Student.DoesNotExist(student_id)

    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id)
        .get()
    )
    assignment_scope_text = summarize_teacher_assignment_scope(student_assignments)
    has_cpp_assignment = any(assignment.course.slug == "cpp" for assignment in student_assignments)
    topic_items = get_gesp4_topic_access_items(student) if has_cpp_assignment else []
    open_items = [item for item in topic_items if item["is_open"]]
    evaluation_records = list(student.teacher_evaluations.select_related("teacher")[:5])
    reward_records = list(student.reward_records.select_related("teacher")[:5])
    lesson_hour_records = list(student.lesson_hour_ledgers.select_related("teacher")[:5])
    evaluation_items = [serialize_evaluation(record) for record in evaluation_records]
    reward_items = [serialize_reward(record) for record in reward_records]
    lesson_hour_items = [serialize_lesson_hour(record) for record in lesson_hour_records]
    lesson_hour_summary = build_lesson_hour_summary(student)
    learning_path = build_student_learning_path(student)
    phase_text = build_phase_label(len(open_items)) if has_cpp_assignment else "当前负责课程未接入专题开放"
    latest_evaluation = evaluation_items[0] if evaluation_items else None
    latest_reward = reward_items[0] if reward_items else None
    latest_lesson_hour = lesson_hour_items[0] if lesson_hour_items else None
    latest_open = max(
        (item for item in open_items if item["granted_at"]),
        key=lambda item: item["granted_at"],
        default=None,
    )
    status_candidates = []
    if latest_open:
        status_candidates.append(
            {
                "created_at": latest_open["granted_at"],
                "label": "最近状态",
                "value": f"专题开放 · {latest_open['title']}",
                "hint": latest_open["granted_at_text"],
            }
        )
    if latest_evaluation:
        status_candidates.append(
            {
                "created_at": latest_evaluation["created_at"],
                "label": "最近状态",
                "value": "已录入教师评价",
                "hint": shorten_text(latest_evaluation["text"]),
            }
        )
    if latest_reward:
        status_candidates.append(
            {
                "created_at": latest_reward["created_at"],
                "label": "最近状态",
                "value": "已录入奖励记录",
                "hint": shorten_text(latest_reward["text"]),
            }
        )
    if latest_lesson_hour:
        status_candidates.append(
            {
                "created_at": latest_lesson_hour["created_at"],
                "label": "最近状态",
                "value": f"课时变动 {latest_lesson_hour['delta_hours_text']}",
                "hint": latest_lesson_hour["note"],
            }
        )
    latest_workspace_status = (
        max(status_candidates, key=lambda item: item["created_at"])
        if status_candidates
        else {
            "label": "最近状态",
            "value": "等待教师首次操作",
            "hint": "当前还没有专题开放或教学记录，可以先从专题开放管理开始。",
        }
    )
    topic_summary_items = [
        {"label": "已开放专题", "value": f"{len(open_items)} 个", "hint": "学生端当前可直接进入的专题"},
        {
            "label": "待开放专题",
            "value": f"{len(topic_items) - len(open_items)} 个",
            "hint": "教师可以继续按学习进度安排开放",
        },
        {
            "label": "真实内容",
            "value": f"{sum(1 for item in topic_items if item['is_real_content'])} 个",
            "hint": "二维数组专题已接入真实内容页",
        },
        {
            "label": "内容预留",
            "value": f"{sum(1 for item in topic_items if not item['is_real_content'])} 个",
            "hint": "其余专题当前先进入统一预留页",
        },
    ]
    record_summary_items = [
        {
            "label": "教师评价",
            "value": f"{len(evaluation_items)} 条",
            "hint": latest_evaluation["created_at_text"] if latest_evaluation else "当前还没有评价记录",
        },
        {
            "label": "奖励记录",
            "value": f"{len(reward_items)} 条",
            "hint": latest_reward["created_at_text"] if latest_reward else "当前还没有奖励记录",
        },
        {
            "label": "课时变动",
            "value": f"{len(lesson_hour_items)} 条",
            "hint": latest_lesson_hour["created_at_text"] if latest_lesson_hour else "当前还没有课时变动记录",
        },
    ]
    recent_record_sections = [
        {
            "eyebrow": "Recent Evaluation",
            "title": "最近教师评价",
            "empty_text": "当前还没有教师评价记录。",
            "items": [
                {
                    "primary": record["text"],
                    "secondary": "",
                    "meta": f"{record['teacher_name']} · {record['created_at_text']}",
                }
                for record in evaluation_items
            ],
        },
        {
            "eyebrow": "Recent Rewards",
            "title": "最近奖励记录",
            "empty_text": "当前还没有奖励记录。",
            "items": [
                {
                    "primary": record["text"],
                    "secondary": "",
                    "meta": f"{record['teacher_name']} · {record['created_at_text']}",
                }
                for record in reward_items
            ],
        },
        {
            "eyebrow": "Recent Lesson Hours",
            "title": "最近课时变动",
            "empty_text": "当前还没有课时变动记录。",
            "items": [
                {
                    "primary": f"{record['delta_hours_text']} 课时",
                    "secondary": record["note"],
                    "meta": f"{record['teacher_name']} · {record['created_at_text']}",
                }
                for record in lesson_hour_items
            ],
        },
    ]
    homework_context = build_teacher_student_homework_context(
        portal_user,
        student,
        homework_form_values=homework_form_values,
        homework_error_message=homework_error_message,
        homework_success_message=homework_success_message,
        homework_summary_form_values=homework_summary_form_values,
        homework_summary_error_message=homework_summary_error_message,
    )
    exam_context = build_teacher_student_exam_context(
        portal_user,
        student,
        exam_form_values=exam_form_values,
        exam_error_message=exam_error_message,
        exam_success_message=exam_success_message,
    )
    content_restriction_context = build_teacher_student_content_restriction_context(portal_user, student)
    topic_access_table_rows = [
        {
            "title": item["title"],
            "content_mode_text": item["content_mode_text"],
            "status_text": item["status_text"],
            "state": item["state"],
            "summary": item["summary"],
            "action_label": "管理权限",
        }
        for item in topic_items
    ]
    content_restriction_table_rows = [
        {
            "title": item["title"],
            "course_title": item["course_title"],
            "level_label": item["level_label"],
            "permission_code": item["permission_code"],
            "course_level_code": item["course_level_code"],
            "status_text": item["status_text"],
            "state": item["state"],
            "note": item["note"],
            "action_label": "批量限制",
        }
        for item in content_restriction_context["content_restriction_items"]
    ]
    homework_table_rows = []
    homework_item_map = {item["id"]: item for item in homework_context["homework_items"]}
    for row in build_homework_assignment_table_rows(homework_context["homework_items"]):
        item = homework_item_map.get(row["id"], {})
        homework_table_rows.append(
            {
                **row,
                "due_date_text": item.get("due_date_text", ""),
                "teacher_comment": item.get("teacher_comment", ""),
                "teacher_comment_text": item.get("teacher_comment_text", ""),
                "highlights": item.get("highlights", ""),
                "areas_for_growth": item.get("areas_for_growth", ""),
                "review_action_label": item.get("review_action_label", "写评语"),
                "can_cancel": bool(item.get("can_cancel")),
                "question_builder_href": item.get("question_builder_href", ""),
                "question_builder_label": item.get("question_builder_label", "管理在线题目"),
            }
        )
    exam_table_rows = [
        {
            "id": item["id"],
            "title": item["title"],
            "course_title": item["course_title"],
            "teacher_name": item["teacher_name"],
            "status_text": item["status_text"],
            "status_tone": item["status_tone"],
            "earned_score": item["earned_score"],
            "total_score": item["total_score"],
            "score_text": f"{item['earned_score']} / {item['total_score']}",
            "correct_count": item["correct_count"],
            "wrong_count": item["wrong_count"],
            "mode_text": item["mode_text"],
            "time_rule_text": item["time_rule_text"],
            "proctoring_text": "已开启" if item["proctoring_enabled"] else "未开启",
            "switch_count": item["switch_count"],
            "started_at_text": item["started_at_text"],
            "submitted_at_text": item["submitted_at_text"],
            "teacher_detail_href": item["teacher_detail_href"],
            "detail_label": "查看详情",
        }
        for item in exam_context["exam_items"]
    ]

    return {
        "student": student,
        "page_title": f"{student.display_name} · 教师工作台",
        "page_description": "当前页将学生概览、GESP4 专题开放管理、作业、考试、教学记录录入和最近记录整理在同一个教师工作台中。",
        "summary_cards": [
            {
                "label": "已开放专题",
                "value": f"{len(open_items)}/{len(topic_items)}",
                "hint": "该学生当前可进入的 GESP4 专题数量" if topic_items else "当前负责课程暂无专题开放链路",
            },
            {"label": "教师评价", "value": f"{len(evaluation_records)} 条", "hint": "当前学生已有的评价记录数"},
            {"label": "最近开放", "value": latest_open["title"] if latest_open else "暂无", "hint": latest_open["granted_at_text"] if latest_open else "等待教师第一次开放"},
        ],
        "breadcrumbs": [
            {"label": "教师学生列表", "href": reverse("teacher-students")},
            {"label": student.display_name},
        ],
        "overview_intro": (
            f"当前负责范围 {assignment_scope_text}；学生主学习线 {learning_path}；"
            f"已开放 {len(open_items)} 个专题，当前课时余额 {lesson_hour_summary['balance_text']}。"
        ),
        "overview_badges": [
            student.grade or "年级待补充",
            assignment_scope_text,
            phase_text,
        ],
        "overview_items": [
            {
                "label": "所在校区",
                "value": student.campus or "待补充",
                "hint": student.grade or "年级待补充",
            },
            {
                "label": "家长联系人",
                "value": student.parent_user.full_name if student.parent_user else "待绑定家长",
                "hint": student.parent_user.phone if student.parent_user and student.parent_user.phone else "家长手机号待补充",
            },
            {
                "label": "当前负责范围",
                "value": assignment_scope_text,
                "hint": learning_path,
            },
            {
                "label": "已开放专题",
                "value": f"{len(open_items)}/{len(topic_items)}",
                "hint": summarize_open_topics(topic_items, limit=2) if topic_items else "当前负责课程暂无专题权限链路",
            },
            {
                "label": "当前课时余额",
                "value": lesson_hour_summary["balance_text"],
                "hint": lesson_hour_summary["latest_note"],
            },
            {
                "label": latest_workspace_status["label"],
                "value": latest_workspace_status["value"],
                "hint": latest_workspace_status["hint"],
            },
        ],
        "latest_workspace_status": latest_workspace_status,
        "topic_summary_items": topic_summary_items,
        "open_topic_items": open_items,
        "topic_access_open_count": len(open_items),
        "topic_access_locked_count": len(topic_items) - len(open_items),
        "topic_access_summary_text": summarize_open_topics(topic_items, limit=3) if topic_items else "当前负责课程暂无专题权限链路",
        "topic_access_items": topic_items,
        "record_summary_items": record_summary_items,
        "latest_evaluation": latest_evaluation,
        "latest_reward": latest_reward,
        "latest_lesson_hour": latest_lesson_hour,
        "evaluation_records": evaluation_items,
        "reward_records": reward_items,
        "lesson_hour_records": lesson_hour_items,
        "recent_record_sections": recent_record_sections,
        "lesson_hour_summary": lesson_hour_summary,
        "topic_access_table_rows": topic_access_table_rows,
        "content_restriction_table_rows": content_restriction_table_rows,
        "homework_table_rows": homework_table_rows,
        "exam_table_rows": exam_table_rows,
        "support_items": [
            {"title": "当前负责范围", "description": f"本页按 assignment 判定访问权限，当前教师负责：{assignment_scope_text}。"},
            {
                "title": "当前动作范围",
                "description": "这次覆盖 GESP4 目录下的 6 个专题，而不再只控制二维数组专题。"
                if has_cpp_assignment
                else "当前仍可录入评价、奖励和课时，但暂未进入 C++ 专题开放链路。",
            },
            {
                "title": "学生端生效方式",
                "description": "学生端目录页会按真实开放状态显示，并对每个专题路由做后端拦截。"
                if has_cpp_assignment
                else "等后续对应课程接入真实内容后，再补对应课程的学生端访问控制。",
            },
            {
                "title": "内容预留策略",
                "description": "二维数组专题是实时内容页，其余专题当前先进入统一预留页。"
                if has_cpp_assignment
                else "当前负责课程仍处于最小接入阶段，先完成 assignment 与教师记录闭环。",
            },
            {"title": "教师记录闭环", "description": "评价、奖励和课时变动会同步展示到家长端和校长端。"},
        ],
        "assignment_management_href": reverse("teacher-student-assignments", args=[student.id]),
        **homework_context,
        **exam_context,
        **content_restriction_context,
    }


def build_principal_page_shell() -> dict:
    page_shell = deepcopy(ROLE_SHELL_CONTENT["principal"])
    topic_contents = get_gesp4_topic_contents()
    student_count = Student.objects.count()
    teacher_count = PortalUser.objects.filter(role=PortalUser.ROLE_TEACHER, is_active=True).count()
    evaluation_count = TeacherEvaluation.objects.count()
    reward_count = RewardRecord.objects.count()
    lesson_hour_count = LessonHourLedger.objects.count()
    open_access_count = StudentContentAccess.objects.filter(
        content__slug__in=GESP4_TOPIC_SLUGS,
        is_open=True,
    ).count()
    recent_accesses = list(
        StudentContentAccess.objects.select_related("student", "content", "granted_by")
        .filter(content__slug__in=GESP4_TOPIC_SLUGS, is_open=True)
        .order_by("-granted_at", "-updated_at")[:8]
    )
    recent_records = []
    for record in TeacherEvaluation.objects.select_related("student", "teacher")[:4]:
        recent_records.append(
            {
                "kind": "教师评价",
                "title": record.student.display_name,
                "detail": record.evaluation_text,
                "meta": f"{record.teacher.full_name if record.teacher else '教师'} · {format_datetime(record.created_at)}",
                "created_at": record.created_at,
            }
        )
    for record in RewardRecord.objects.select_related("student", "teacher")[:4]:
        recent_records.append(
            {
                "kind": "奖励记录",
                "title": record.student.display_name,
                "detail": record.reward_text,
                "meta": f"{record.teacher.full_name if record.teacher else '教师'} · {format_datetime(record.created_at)}",
                "created_at": record.created_at,
            }
        )
    for record in LessonHourLedger.objects.select_related("student", "teacher")[:4]:
        recent_records.append(
            {
                "kind": "课时变动",
                "title": record.student.display_name,
                "detail": f"{format_delta_hours(record.delta_hours)} 课时 · {record.note or '未填写备注'}",
                "meta": f"{record.teacher.full_name if record.teacher else '教师'} · {format_datetime(record.created_at)}",
                "created_at": record.created_at,
            }
        )
    recent_records.sort(key=lambda item: item["created_at"], reverse=True)

    page_shell["summary_cards"] = [
        {"label": "学生数量", "value": str(student_count), "hint": "当前最小学生档案数"},
        {"label": "教师数量", "value": str(teacher_count), "hint": "当前教师账号数"},
        {"label": "已开放内容", "value": str(open_access_count), "hint": "GESP4 多专题开放记录中的开放项"},
    ]
    page_shell["overview_cards"] = [
        {"title": "学生数量", "value": str(student_count), "description": "当前已建学生业务档案数量。"},
        {"title": "教师数量", "value": str(teacher_count), "description": "当前可登录教师账号数量。"},
        {"title": "GESP4 专题", "value": str(len(topic_contents)), "description": "当前纳入通用开放框架的 GESP4 专题数量。"},
        {
            "title": "已开放记录",
            "value": str(open_access_count),
            "description": "所有学生在 GESP4 多专题下的已开放记录总数。",
        },
    ]
    page_shell["record_overview_items"] = [
        {"title": "教师评价", "value": str(evaluation_count), "description": "当前教师录入的评价总数。"},
        {"title": "奖励记录", "value": str(reward_count), "description": "当前教师录入的奖励记录总数。"},
        {"title": "课时变动", "value": str(lesson_hour_count), "description": "当前教师录入的课时变动总数。"},
    ]
    page_shell["recent_access_records"] = [
        {
            "student_name": access.student.display_name,
            "content_title": access.content.title,
            "granted_by": access.granted_by.full_name if access.granted_by else "系统",
            "granted_at": format_datetime(access.granted_at),
        }
        for access in recent_accesses
    ]
    page_shell["recent_record_items"] = recent_records[:6]
    page_shell["topic_overview_items"] = [
        {
            "title": content.title,
            "subtitle": get_content_mode_text(content.slug),
            "value": f"{StudentContentAccess.objects.filter(content=content, is_open=True).count()}/{student_count or 0}",
            "description": "当前学生开放数 / 学生总数",
        }
        for content in topic_contents
    ]
    return page_shell
