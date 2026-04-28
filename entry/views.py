import logging
import json
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import transaction
from django.db.models import Max
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.utils.text import slugify

from .account_identity import normalize_phone
from .auth import (
    ROLE_CONFIG,
    authenticate_credentials,
    build_user_payload,
    clear_auth_cookie,
    get_authenticated_user,
    role_required,
    set_auth_cookie,
)
from .content_visibility import infer_content_permission_code
from .course_identity import normalize_assignment_level
from .gesp2_catalog import ASCII_CHAR_ENCODING_CONTENT_SLUG, ENUMERATION_METHOD_CONTENT_SLUG
from .gesp4_catalog import ARRAY_2D_CONTENT_SLUG
from .gesp4_catalog import BINARY_SEARCH_CONTENT_SLUG, SORTING_CONTENT_SLUG, STRINGS_CONTENT_SLUG
from .homework_batch import (
    build_homework_import_job_preview_payload,
    get_visible_homework_import_jobs,
)
from .homework_online import (
    HomeworkImportParseError,
    compute_uploaded_file_sha256,
    confirm_homework_import_job,
    detect_homework_source_type,
    encode_sql_ascii_json_text,
    extract_import_job_user_facing_message,
    grade_homework_submission,
    parse_homework_import_job,
)
from .manual_overrides import update_question_manual_override
from .models import (
    Course,
    CourseContent,
    HomeworkAssignment,
    HomeworkImportJob,
    HomeworkSummary,
    LessonHourLedger,
    PortalUser,
    Question,
    RewardRecord,
    Student,
    TeacherEvaluation,
    TeacherStudentAssignment,
)
from .portal_context import (
    build_gesp2_reserved_topic_page,
    build_gesp4_reserved_topic_page,
    build_parent_homework_detail_context,
    build_parent_homework_list_context,
    build_parent_homework_print_context,
    build_parent_homework_summary_detail_context,
    build_parent_homework_submission_detail_context,
    build_student_homework_detail_context,
    build_student_homework_list_context,
    build_student_homework_practice_context,
    build_student_homework_submission_detail_context,
    build_student_homework_summary_detail_context,
    build_student_practice_page_shell,
    build_teacher_assignment_form_context,
    build_teacher_assignment_remove_context,
    build_teacher_course_category_detail_context,
    build_teacher_course_content_access_context,
    build_teacher_course_student_pool_context,
    build_teacher_course_students_detail_context,
    build_teacher_course_structure_export_payload,
    build_teacher_homework_batch_create_context,
    build_parent_page_shell,
    build_principal_page_shell,
    build_student_portal_page,
    build_student_homework_print_context,
    build_teacher_course_detail_context,
    build_teacher_course_level_detail_context,
    build_teacher_course_workflow_placeholder_context,
    build_teacher_homework_stats_context,
    build_teacher_homework_builder_context,
    build_teacher_page_shell,
    build_teacher_student_assignment_list_context,
    build_teacher_student_detail_context,
    build_default_batch_homework_summary_title,
    build_default_homework_summary_title,
    ensure_homework_content_access,
    filter_homework_assignments_by_assigned_date,
    get_gesp2_knowledge_content,
    get_teacher_student_homework_queryset,
    get_teacher_course_category,
    get_teacher_course_content,
    get_teacher_course_level,
    get_teacher_course_levels,
    get_teacher_course_scope,
    get_gesp4_topic_access_items,
    get_gesp4_topic_content,
    normalize_grid_page,
    normalize_grid_page_size,
    get_student_by_user,
    set_student_content_visibility,
    student_has_content_access,
    get_teacher_student_homework_contents,
)
from .student_import import (
    DEFAULT_IMPORTED_ACCOUNT_PASSWORD,
    StudentImportError,
    create_or_update_student_with_parent_and_assignment,
    import_students_from_rows,
    infer_student_primary_track_name,
    parse_student_import_file,
    teacher_can_import_students,
)
from .topic_content.gesp2_enumeration.context import get_topic_page_context as get_gesp2_enumeration_page_context
from .topic_content.gesp2_ascii_char_encoding.context import (
    get_topic_page_context as get_gesp2_ascii_char_encoding_page_context,
)
from .topic_content.gesp4_array_2d.context import (
    get_student_topic_page_context as get_gesp4_array_2d_student_page_context,
)
from .topic_content.gesp4_array_2d.context import get_topic_page_context as get_gesp4_array_2d_page_context
from .topic_content.gesp4_shared.context import get_topic_page_context as get_generic_gesp4_topic_page_context

logger = logging.getLogger(__name__)


def build_shell_identity_context(request: HttpRequest) -> dict[str, str]:
    user = request.codemaster_user
    return {
        "viewer_display_name": user.get("full_name") or user["username"],
        "viewer_username": user["username"],
        "account_settings_href": reverse("student-account-settings") if user["role"] == "student" else "",
    }


def render_shell_page(request: HttpRequest, role_key: str, template_name: str, context: dict) -> HttpResponse:
    return render(
        request,
        template_name,
        {
            "role_label": ROLE_CONFIG[role_key]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


def login_page(request: HttpRequest) -> HttpResponse:
    current_user = get_authenticated_user(request)
    if current_user:
        return redirect(current_user["landing_url"])

    context = {}

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        context["username"] = username

        user = authenticate_credentials(username, password)
        if user:
            response = redirect(user["landing_url"])
            set_auth_cookie(response, user)
            return response

        context["error_message"] = "账号或密码错误。"

    return render(request, "entry/login.html", context)


def logout_view(request: HttpRequest) -> HttpResponse:
    response = redirect("login")
    clear_auth_cookie(response)
    return response


def get_portal_user_from_request(request: HttpRequest) -> PortalUser:
    return PortalUser.objects.get(
        username=request.codemaster_user["username"],
        role=request.codemaster_user["role"],
        is_active=True,
    )


def build_knowledge_point_default_route_path(course_slug: str, category_slug: str, level_code: str, slug: str) -> str:
    return f"/student/{course_slug}/{category_slug}/{level_code.lower()}/{slug}"


def normalize_positive_int(value: object, *, default: int = 0, minimum: int = 0) -> int:
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return normalized if normalized >= minimum else default


def normalize_positive_int_list(values: list[object]) -> list[int]:
    normalized_values: list[int] = []
    seen: set[int] = set()
    for value in values:
        normalized = normalize_positive_int(value, default=0, minimum=1)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_values.append(normalized)
    return normalized_values


def parse_iso_date(value: object) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def decode_uploaded_summary_html(uploaded_file: UploadedFile) -> str:
    filename = str(getattr(uploaded_file, "name", "") or "").lower()
    if not filename.endswith((".html", ".htm")):
        raise ValidationError("当前只支持上传 html / htm 文件。")
    payload = uploaded_file.read()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError("当前无法识别这个 HTML 文件的编码，请改用 UTF-8 或 GB18030。")


def parse_assignment_scope_key(scope_key: object) -> tuple[int, str]:
    raw = str(scope_key or "").strip()
    if ":" not in raw:
        return 0, ""
    course_id_str, level_code = raw.split(":", 1)
    try:
        course_id = int(course_id_str)
    except (TypeError, ValueError):
        return 0, ""
    return course_id, level_code.strip()


def build_teacher_course_student_pool_single_student_form_values(
    context: dict,
    form_data: dict[str, object] | None = None,
) -> dict[str, str]:
    defaults = dict(context.get("single_student_form_values") or {})
    data = form_data or {}
    return {
        "student_name": str(data.get("student_name") or defaults.get("student_name") or "").strip(),
        "parent_phone": str(data.get("parent_phone") or defaults.get("parent_phone") or "").strip(),
        "course_id": str(data.get("course_id") or defaults.get("course_id") or "").strip(),
        "permission_level_code": str(
            data.get("permission_level_code") or defaults.get("permission_level_code") or ""
        ).strip().upper(),
        "primary_level_name": str(
            data.get("primary_level_name") or defaults.get("primary_level_name") or ""
        ).strip().upper(),
    }


def build_json_download_response(*, payload: dict, filename: str) -> HttpResponse:
    response = HttpResponse(
        json.dumps(payload, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def build_manual_override_feedback(request: HttpRequest) -> dict[str, object]:
    status = (request.GET.get("manual_override_status") or "").strip()
    question_id = normalize_positive_int(request.GET.get("manual_override_question"), default=0, minimum=0)
    message = (request.GET.get("manual_override_message") or "").strip()
    if not status or not question_id:
        return {"status": "", "message": "", "question_id": 0}
    default_message = "截图人工修正已保存。" if status == "saved" else "截图人工修正保存失败。"
    return {
        "status": status,
        "message": message or default_message,
        "question_id": question_id,
    }


def build_redirect_with_query(base_path: str, *, params: dict[str, object], anchor: str = "") -> str:
    split_result = urlsplit(base_path)
    current_query = dict(parse_qsl(split_result.query, keep_blank_values=True))
    for key, value in params.items():
        if value in (None, ""):
            current_query.pop(key, None)
        else:
            current_query[key] = str(value)
    query = urlencode(current_query)
    fragment = anchor.lstrip("#")
    return urlunsplit((split_result.scheme, split_result.netloc, split_result.path, query, fragment))


def get_safe_next_path(request: HttpRequest, fallback_url_name: str) -> str:
    next_path = (request.POST.get("next") or "").strip()
    if next_path and url_has_allowed_host_and_scheme(
        next_path,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_path
    return reverse(fallback_url_name)


def build_knowledge_point_form_context(
    portal_user: PortalUser,
    course_slug: str,
    category_slug: str,
    level_code: str,
    *,
    action_label: str,
    submit_label: str,
    content: CourseContent | None = None,
    form_data: dict[str, object] | None = None,
    error_message: str = "",
) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    category = get_teacher_course_category(course, category_slug)
    level = get_teacher_course_level(category, level_code)
    available_levels = list(get_teacher_course_levels(category))
    available_level_ids = {item.id for item in available_levels}
    requested_level_id = normalize_positive_int((form_data or {}).get("level_id"), default=content.level_id if content and content.level_id else level.id, minimum=1)
    selected_level = next((item for item in available_levels if item.id == requested_level_id), level)
    base_slug = slugify(content.title) if content else ""
    suggested_slug = base_slug or f"{level.code.lower()}-knowledge-point"
    initial_slug = (form_data or {}).get("slug") or (content.slug if content else suggested_slug)
    initial_route_path = (form_data or {}).get("route_path") or (
        content.route_path if content else build_knowledge_point_default_route_path(course_slug, category_slug, selected_level.code, str(initial_slug))
    )
    initial_summary = (form_data or {}).get("summary") or (content.summary if content else "")
    initial_title = (form_data or {}).get("title") or (content.title if content else "")
    initial_sort_order = (form_data or {}).get("sort_order")
    if initial_sort_order in ("", None):
        initial_sort_order = content.sort_order if content else (
            (CourseContent.objects.filter(level=selected_level).aggregate(max_sort=Max("sort_order"))["max_sort"] or 0) + 1
        )
    initial_has_real_content = bool(
        (form_data or {}).get("has_real_content")
        if form_data is not None
        else (content.has_real_content if content else False)
    )
    level_selector_rows = [
        {
            "level_id": item.id,
            "code": item.code,
            "title": item.title,
            "summary": item.summary or f"{item.title} 当前尚未补说明。",
            "selected": item.id == selected_level.id,
        }
        for item in available_levels
    ]

    return {
        "page_title": f"{action_label} · {level.title}",
        "page_description": "当前先提供最小表单，直接写入 CourseContent；更复杂的课程后台暂不展开。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": course.title, "href": reverse("teacher-course-detail", args=[course_slug])},
            {"label": category.title, "href": reverse("teacher-course-category-detail", args=[course_slug, category.slug])},
            {"label": level.title, "href": reverse("teacher-course-level-detail", args=[course_slug, category.slug, level.code])},
            {"label": action_label},
        ],
        "summary_cards": [
            {"label": "课程方向", "value": course.title, "hint": "当前课程方向"},
            {"label": "分类", "value": category.title, "hint": "当前课程分类"},
            {"label": "Level", "value": level.title, "hint": "当前级别"},
            {"label": "目标", "value": content.title if content else "新知识点", "hint": "当前操作对象"},
        ],
        "identity_items": [
            {"label": "所属课程方向", "value": course.title},
            {"label": "所属分类", "value": category.title},
            {"label": "所属 Level", "value": selected_level.title},
        ],
        "error_message": error_message,
        "action_label": action_label,
        "submit_label": submit_label,
        "back_href": reverse("teacher-course-level-detail", args=[course_slug, category.slug, level.code]),
        "form_values": {
            "title": initial_title,
            "slug": initial_slug,
            "route_path": initial_route_path,
            "summary": initial_summary,
            "has_real_content": initial_has_real_content,
            "sort_order": initial_sort_order,
            "level_id": selected_level.id,
        },
        "editing_content": content,
        "course": course,
        "category": category,
        "level": level,
        "selected_level": selected_level,
        "available_levels": available_levels,
        "available_level_ids": available_level_ids,
        "level_selector_rows": level_selector_rows,
    }


def build_knowledge_point_delete_context(
    portal_user: PortalUser,
    course_slug: str,
    category_slug: str,
    level_code: str,
    content_slug: str,
) -> dict:
    scope = get_teacher_course_scope(portal_user, course_slug)
    course = scope["course"]
    category = get_teacher_course_category(course, category_slug)
    level = get_teacher_course_level(category, level_code)
    content = get_teacher_course_content(course, level, content_slug)
    open_count = CourseContent.objects.filter(id=content.id, student_accesses__is_open=True).count()

    return {
        "page_title": f"删除知识点 · {content.title}",
        "page_description": "这一步先做确认页。确认后不会硬删数据，而是将该知识点标记为停用。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": course.title, "href": reverse("teacher-course-detail", args=[course_slug])},
            {"label": category.title, "href": reverse("teacher-course-category-detail", args=[course_slug, category.slug])},
            {"label": level.title, "href": reverse("teacher-course-level-detail", args=[course_slug, category.slug, level.code])},
            {"label": "删除知识点"},
        ],
        "summary_cards": [
            {"label": "知识点", "value": content.title, "hint": "当前准备停用的知识点"},
            {"label": "Slug", "value": content.slug, "hint": "唯一标识"},
            {"label": "Level", "value": level.title, "hint": "当前所属级别"},
            {"label": "Route Path", "value": content.route_path, "hint": "当前访问路由"},
            {"label": "已开放记录", "value": str(open_count), "hint": "当前处于开放状态的 StudentContentAccess 记录数"},
        ],
        "content": content,
        "level": level,
        "open_count": open_count,
        "back_href": reverse("teacher-course-level-detail", args=[course_slug, category.slug, level.code]),
        "confirm_action": reverse(
            "teacher-course-knowledge-point-delete",
            args=[course_slug, category.slug, level.code, content.slug],
        ),
    }


def render_role_page(request: HttpRequest, role_key: str, page_shell: dict) -> HttpResponse:
    role_config = ROLE_CONFIG[role_key]
    return render(
        request,
        "entry/role_page.html",
        {
            "role_key": role_key,
            "role_label": role_config["label"],
            "page_title": role_config["page_title"],
            "page_description": role_config["page_description"],
            "page_shell": page_shell,
            **build_shell_identity_context(request),
        },
    )


def render_student_portal_page(request: HttpRequest, page_key: str) -> HttpResponse:
    role_config = ROLE_CONFIG["student"]
    user = request.codemaster_user
    portal_user = get_portal_user_from_request(request)
    locked_topic_slug = request.GET.get("locked_content") if page_key == "cpp_gesp4" else None
    page_shell = build_student_portal_page(page_key, portal_user, locked_topic_slug=locked_topic_slug)
    return render(
        request,
        "entry/student_portal_page.html",
        {
            "role_label": role_config["label"],
            "page_title": page_shell["page_title"],
            "page_description": page_shell["page_description"],
            "page_shell": page_shell,
            **build_shell_identity_context(request),
        },
    )


@role_required("student")
def student_courses(request: HttpRequest) -> HttpResponse:
    return render_student_portal_page(request, "courses")


@role_required("student")
def student_practice(request: HttpRequest) -> HttpResponse:
    role_config = ROLE_CONFIG["student"]
    portal_user = get_portal_user_from_request(request)
    page_shell = build_student_practice_page_shell(portal_user)
    return render(
        request,
        "entry/student_portal_page.html",
        {
            "role_label": role_config["label"],
            "page_title": page_shell["page_title"],
            "page_description": page_shell["page_description"],
            "page_shell": page_shell,
            **build_shell_identity_context(request),
        },
    )


@role_required("student")
def student_cpp(request: HttpRequest) -> HttpResponse:
    return render_student_portal_page(request, "cpp")


@role_required("student")
def student_cpp_gesp(request: HttpRequest) -> HttpResponse:
    return render_student_portal_page(request, "cpp_gesp")


@role_required("student")
def student_cpp_gesp2(request: HttpRequest) -> HttpResponse:
    return render_student_portal_page(request, "cpp_gesp2")


@role_required("student")
def student_cpp_gesp4(request: HttpRequest) -> HttpResponse:
    return render_student_portal_page(request, "cpp_gesp4")


@role_required("student")
def student_homework_list(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    context = build_student_homework_list_context(portal_user)
    return render_shell_page(request, "student", "entry/student_homework_list.html", context)


@role_required("student")
def student_homework_detail(request: HttpRequest, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)

    def render_detail(*, error_message: str = "") -> HttpResponse:
        try:
            detail_context = build_student_homework_detail_context(portal_user, assignment_id)
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该作业") from exc
        if request.GET.get("op") == "completed":
            detail_context["success_message"] = "作业已标记完成，可以等待老师填写评语。"
        if error_message:
            detail_context["error_message"] = error_message
        return render_shell_page(request, "student", "entry/student_homework_detail.html", detail_context)

    if request.method == "POST":
        action = request.POST.get("form_action", "").strip()
        if action == "mark_completed":
            student = get_student_by_user(portal_user)
            assignment = (
                HomeworkAssignment.objects.select_related("student")
                .filter(id=assignment_id, student=student, is_active=True)
                .first()
            )
            if not assignment:
                raise Http404("未找到该作业")
            if assignment.get_effective_online_question_count() > 0:
                return render_detail(error_message="这份作业已经切到在线选择题模式，请提交整份作业完成。")
            if assignment.mark_completed():
                assignment.save(update_fields=["status", "completed_at", "updated_at"])
            return redirect(build_redirect_with_query(reverse("student-homework-detail", args=[assignment_id]), params={"op": "completed"}))

    return render_detail()


@role_required("student")
def student_homework_practice(request: HttpRequest, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)

    def render_practice(*, error_message: str = "") -> HttpResponse:
        try:
            context = build_student_homework_practice_context(portal_user, assignment_id)
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该在线作业") from exc
        if error_message:
            context["error_message"] = error_message
        return render_shell_page(request, "student", "entry/student_homework_practice.html", context)

    if request.method == "POST":
        student = get_student_by_user(portal_user)
        assignment = (
            HomeworkAssignment.objects.select_related("student")
            .filter(id=assignment_id, student=student, is_active=True)
            .first()
        )
        if not assignment:
            raise Http404("未找到该作业")
        selected_answers = {}
        for key, value in request.POST.items():
            if not key.startswith("question_"):
                continue
            question_id = normalize_positive_int(key.split("_", 1)[1], default=0, minimum=1)
            if question_id:
                selected_answers[question_id] = str(value).strip().upper()
        try:
            submission = grade_homework_submission(
                assignment,
                student,
                selected_answers=selected_answers,
            )
        except HomeworkImportParseError as exc:
            return render_practice(error_message=str(exc))
        return redirect(
            build_redirect_with_query(
                reverse("student-homework-submission-detail", args=[assignment.id, submission.id]),
                params={"op": "submitted"},
            )
        )

    return render_practice()


@role_required("student")
def student_homework_submission_detail(request: HttpRequest, assignment_id: int, submission_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_student_homework_submission_detail_context(portal_user, assignment_id, submission_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该提交记录") from exc
    if request.GET.get("op") == "submitted":
        context["success_message"] = "本次练习已提交并自动判分，历史记录已保留。"
    return render_shell_page(request, "student", "entry/homework_submission_detail.html", context)


@role_required("student")
def student_homework_summary_detail(request: HttpRequest, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_student_homework_summary_detail_context(portal_user, assignment_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该本周总结") from exc
    return render_shell_page(request, "student", "entry/homework_summary_detail.html", context)


@role_required("student")
def student_homework_print(request: HttpRequest, assignment_id: int, submission_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_student_homework_print_context(
            portal_user,
            assignment_id,
            submission_id=submission_id,
            wrong_only=False,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该打印结果") from exc
    return render(request, "entry/student_homework_print.html", context)


@role_required("student")
def student_homework_print_wrong(request: HttpRequest, assignment_id: int, submission_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_student_homework_print_context(
            portal_user,
            assignment_id,
            submission_id=submission_id,
            wrong_only=True,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该错题打印页") from exc
    return render(request, "entry/student_homework_print.html", context)


@role_required("student")
def student_homework_print_blank(request: HttpRequest, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_student_homework_print_context(
            portal_user,
            assignment_id,
            wrong_only=False,
            blank_only=True,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该空白练习卷") from exc
    return render(request, "entry/student_homework_print.html", context)


@role_required("student")
def student_account_settings(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    student = get_student_by_user(portal_user)
    context = {
        "role_label": ROLE_CONFIG["student"]["label"],
        "page_title": "账号设置",
        "page_description": "统一账号体系下，学生可以在这里查看并修改自己的登录账号。",
        "student_display_name": student.display_name,
        "current_username": portal_user.username,
        "proposed_username": portal_user.username,
        **build_shell_identity_context(request),
    }

    if request.GET.get("updated") == "1":
        context["success_message"] = "登录账号已更新，后续请使用新账号登录。"

    if request.method == "POST":
        next_username = request.POST.get("new_username", "").strip()
        current_password = request.POST.get("current_password", "")
        context["proposed_username"] = next_username

        if not next_username:
            context["error_message"] = "请输入新的登录账号。"
        elif len(next_username) > 64:
            context["error_message"] = "登录账号长度不能超过 64 个字符。"
        elif any(char.isspace() for char in next_username):
            context["error_message"] = "登录账号不能包含空格。"
        elif next_username == portal_user.username:
            context["error_message"] = "新登录账号与当前账号相同。"
        elif not current_password:
            context["error_message"] = "请输入当前密码以确认修改。"
        elif not portal_user.check_password(current_password):
            context["error_message"] = "当前密码不正确。"
        elif PortalUser.objects.filter(username=next_username).exclude(id=portal_user.id).exists():
            context["error_message"] = "该登录账号已被占用，请更换一个。"
        else:
            portal_user.username = next_username
            portal_user.save(update_fields=["username", "updated_at"])
            response = redirect(f"{reverse('student-account-settings')}?updated=1")
            set_auth_cookie(response, build_user_payload(portal_user))
            return response

    return render(request, "entry/student_account_settings.html", context)


@role_required("student")
def student_cpp_gesp4_array_2d(request: HttpRequest) -> HttpResponse:
    return _render_gesp4_topic_page(request, ARRAY_2D_CONTENT_SLUG)


def _render_gesp2_topic_page(request: HttpRequest, topic_slug: str) -> HttpResponse:
    role_config = ROLE_CONFIG["student"]
    portal_user = get_portal_user_from_request(request)
    student = get_student_by_user(portal_user)
    try:
        content = get_gesp2_knowledge_content(topic_slug)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该知识点") from exc

    if not student_has_content_access(student, content.slug):
        return redirect("/student/cpp/gesp/gesp2")

    if topic_slug == ENUMERATION_METHOD_CONTENT_SLUG:
        topic_context = get_gesp2_enumeration_page_context(view_mode="student")
        return render(
            request,
            "entry/topics/gesp2_enumeration_page.html",
            {
                "role_label": role_config["label"],
                **build_shell_identity_context(request),
                **topic_context,
            },
        )

    if topic_slug == ASCII_CHAR_ENCODING_CONTENT_SLUG:
        topic_context = get_gesp2_ascii_char_encoding_page_context(view_mode="student")
        return render(
            request,
            "entry/topics/gesp2_ascii_char_encoding_page.html",
            {
                "role_label": role_config["label"],
                **build_shell_identity_context(request),
                **topic_context,
            },
        )

    page_shell = build_gesp2_reserved_topic_page(topic_slug)
    return render(
        request,
        "entry/student_portal_page.html",
        {
            "role_label": role_config["label"],
            "page_title": page_shell["page_title"],
            "page_description": page_shell["page_description"],
            "page_shell": page_shell,
            **build_shell_identity_context(request),
        },
    )


@role_required("teacher")
def teacher_cpp_gesp2_enumeration(request: HttpRequest) -> HttpResponse:
    topic_context = get_gesp2_enumeration_page_context(view_mode="teacher")
    topic_context["manual_override_feedback"] = build_manual_override_feedback(request)
    topic_context["manual_override_focus_question_id"] = topic_context["manual_override_feedback"]["question_id"]
    return render(
        request,
        "entry/topics/gesp2_enumeration_page.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **topic_context,
        },
    )


@role_required("teacher")
def teacher_question_manual_override(request: HttpRequest, question_id: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("仅支持 POST 上传。")

    next_path = get_safe_next_path(request, "teacher-cpp-gesp2-enumeration")
    return_anchor = (request.POST.get("return_anchor") or "").strip()
    redirect_params = {
        "manual_override_question": question_id,
    }

    try:
        question = update_question_manual_override(
            question_id=question_id,
            files_by_field={
                "question_image": request.FILES.get("question_image"),
                "code_image": request.FILES.get("code_image"),
                "options_image": request.FILES.get("options_image"),
            },
            review_note=request.POST.get("review_note", ""),
            is_reviewed=request.POST.get("is_reviewed") == "on",
        )
    except Question.DoesNotExist as exc:
        raise Http404("未找到该题目。") from exc
    except ValidationError as exc:
        redirect_params["manual_override_status"] = "error"
        redirect_params["manual_override_message"] = "；".join(exc.messages)
        return redirect(build_redirect_with_query(next_path, params=redirect_params, anchor=return_anchor))

    redirect_params["manual_override_status"] = "saved"
    redirect_params["manual_override_message"] = f"已保存题目「{question.title}」的人工修正截图。"
    return redirect(build_redirect_with_query(next_path, params=redirect_params, anchor=return_anchor))


@role_required("teacher")
def teacher_cpp_gesp2_ascii_char_encoding(request: HttpRequest) -> HttpResponse:
    topic_context = get_gesp2_ascii_char_encoding_page_context(view_mode="teacher")
    return render(
        request,
        "entry/topics/gesp2_ascii_char_encoding_page.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **topic_context,
        },
    )


@role_required("teacher")
def teacher_cpp_gesp4_array_2d(request: HttpRequest) -> HttpResponse:
    topic_context = get_gesp4_array_2d_page_context(request.GET.get("lecture"))
    topic_context["topic_note"] = "当前教师页直连 GESP4 二维数组真实教学页，题目数据采用数据库优先、静态兜底。"
    topic_context["breadcrumb_items"] = [
        {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
        {"label": "C++", "href": reverse("teacher-course-detail", args=["cpp"])},
        {"label": "GESP", "href": reverse("teacher-course-category-detail", args=["cpp", "gesp"])},
        {"label": "GESP4", "href": reverse("teacher-course-level-detail", args=["cpp", "gesp", "GESP4"])},
        {"label": "二维数组专题"},
    ]
    return render(
        request,
        "entry/topics/gesp4_array_2d_page.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **topic_context,
        },
    )


def _render_teacher_gesp4_static_topic_page(request: HttpRequest, *, topic_slug: str, topic_title: str) -> HttpResponse:
    topic_context = get_generic_gesp4_topic_page_context(
        topic_slug,
        lecture_id=request.GET.get("lecture"),
        breadcrumb_items=[
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": "C++", "href": reverse("teacher-course-detail", args=["cpp"])},
            {"label": "GESP", "href": reverse("teacher-course-category-detail", args=["cpp", "gesp"])},
            {"label": "GESP4", "href": reverse("teacher-course-level-detail", args=["cpp", "gesp", "GESP4"])},
            {"label": topic_title},
        ],
        topic_note=f"当前教师页直连 {topic_title} 真实教学页结构，先用静态专题数据承接讲次导读与教学说明。",
    )
    return render(
        request,
        "entry/topics/gesp4_array_2d_page.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **topic_context,
        },
    )


@role_required("teacher")
def teacher_cpp_gesp4_binary_search(request: HttpRequest) -> HttpResponse:
    return _render_teacher_gesp4_static_topic_page(request, topic_slug=BINARY_SEARCH_CONTENT_SLUG, topic_title="二分查找专题")


@role_required("teacher")
def teacher_cpp_gesp4_sorting(request: HttpRequest) -> HttpResponse:
    return _render_teacher_gesp4_static_topic_page(request, topic_slug=SORTING_CONTENT_SLUG, topic_title="排序专题")


@role_required("teacher")
def teacher_cpp_gesp4_strings(request: HttpRequest) -> HttpResponse:
    return _render_teacher_gesp4_static_topic_page(request, topic_slug=STRINGS_CONTENT_SLUG, topic_title="字符串专题")


def _render_gesp4_topic_page(request: HttpRequest, topic_slug: str) -> HttpResponse:
    role_config = ROLE_CONFIG["student"]
    portal_user = get_portal_user_from_request(request)
    student = get_student_by_user(portal_user)
    try:
        content = get_gesp4_topic_content(topic_slug)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该专题") from exc

    if not student_has_content_access(student, content.slug):
        return redirect(f"/student/cpp/gesp/gesp4?locked_content={topic_slug}")

    if topic_slug == ARRAY_2D_CONTENT_SLUG:
        topic_context = get_gesp4_array_2d_student_page_context()
        return render(
            request,
            "entry/topics/gesp4_array_2d_student_page.html",
            {
                "role_label": role_config["label"],
                **build_shell_identity_context(request),
                **topic_context,
            },
        )

    page_shell = build_gesp4_reserved_topic_page(topic_slug)
    return render(
        request,
        "entry/student_portal_page.html",
        {
            "role_label": role_config["label"],
            "page_title": page_shell["page_title"],
            "page_description": page_shell["page_description"],
            "page_shell": page_shell,
            **build_shell_identity_context(request),
        },
    )


@role_required("student")
def student_cpp_gesp4_topic(request: HttpRequest, topic_slug: str) -> HttpResponse:
    return _render_gesp4_topic_page(request, topic_slug)


@role_required("student")
def student_cpp_gesp2_topic(request: HttpRequest, topic_slug: str) -> HttpResponse:
    return _render_gesp2_topic_page(request, topic_slug)


@role_required("parent")
def parent_student_profile(request: HttpRequest) -> HttpResponse:
    return render_role_page(request, "parent", build_parent_page_shell(get_portal_user_from_request(request)))


@role_required("parent")
def parent_homework_list(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    context = build_parent_homework_list_context(portal_user)
    return render_shell_page(request, "parent", "entry/student_homework_list.html", context)


@role_required("parent")
def parent_homework_detail(request: HttpRequest, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_parent_homework_detail_context(portal_user, assignment_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该作业记录") from exc
    return render_shell_page(request, "parent", "entry/student_homework_detail.html", context)


@role_required("parent")
def parent_homework_summary_detail(request: HttpRequest, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_parent_homework_summary_detail_context(portal_user, assignment_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该本周总结") from exc
    return render_shell_page(request, "parent", "entry/homework_summary_detail.html", context)


@role_required("parent")
def parent_homework_submission_detail(request: HttpRequest, assignment_id: int, submission_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_parent_homework_submission_detail_context(portal_user, assignment_id, submission_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该提交记录") from exc
    return render_shell_page(request, "parent", "entry/homework_submission_detail.html", context)


@role_required("parent")
def parent_homework_print(request: HttpRequest, assignment_id: int, submission_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_parent_homework_print_context(
            portal_user,
            assignment_id,
            submission_id=submission_id,
            wrong_only=False,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该打印结果") from exc
    return render(request, "entry/student_homework_print.html", context)


@role_required("parent")
def parent_homework_print_wrong(request: HttpRequest, assignment_id: int, submission_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_parent_homework_print_context(
            portal_user,
            assignment_id,
            submission_id=submission_id,
            wrong_only=True,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该错题打印页") from exc
    return render(request, "entry/student_homework_print.html", context)


@role_required("parent")
def parent_homework_print_blank(request: HttpRequest, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_parent_homework_print_context(
            portal_user,
            assignment_id,
            wrong_only=False,
            blank_only=True,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该空白练习卷") from exc
    return render(request, "entry/student_homework_print.html", context)


@role_required("teacher")
def teacher_students(request: HttpRequest) -> HttpResponse:
    active_tab = request.GET.get("tab", "students")
    return render_role_page(
        request,
        "teacher",
        build_teacher_page_shell(get_portal_user_from_request(request), active_tab=active_tab),
    )


@role_required("teacher")
def teacher_homework_stats(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    context = build_teacher_homework_stats_context(
        portal_user,
        period=request.GET.get("period", "week"),
    )
    return render(
        request,
        "entry/teacher_homework_stats.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_homework_batch_create(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    selected_course_slug = (request.GET.get("course") or "").strip().lower()
    selected_course = None
    if selected_course_slug:
        selected_course = Course.objects.filter(slug=selected_course_slug).order_by("id").first()
        if selected_course is None:
            raise Http404("未找到该课程")

    success_message = ""
    if request.GET.get("op") == "created":
        created_count = normalize_positive_int(request.GET.get("count"), default=0, minimum=0)
        if created_count:
            if request.GET.get("with_summary") == "1":
                success_message = f"批量布置完成：已为 {created_count} 名学生创建作业，并关联 1 篇课后总结。"
            else:
                success_message = f"批量布置完成：已为 {created_count} 名学生创建作业。"

    form_values: dict[str, object] | None = None
    error_message = ""

    if request.method == "POST":
        selected_student_ids = normalize_positive_int_list(request.POST.getlist("student_ids"))
        selected_import_job_id = normalize_positive_int(request.POST.get("import_job_id"), default=0, minimum=1)
        due_date_raw = request.POST.get("due_date", "").strip()
        assignment_requirement = request.POST.get("assignment_requirement", "").strip()
        summary_title = request.POST.get("summary_title", "").strip()
        summary_html_text = request.POST.get("summary_html", "").strip()
        form_values = {
            "student_ids": selected_student_ids,
            "import_job_id": selected_import_job_id,
            "assignment_requirement": assignment_requirement,
            "due_date": due_date_raw,
            "summary_title": summary_title,
            "summary_html": summary_html_text,
        }

        if not selected_student_ids:
            error_message = "请至少选择 1 名学生。"
        elif not selected_import_job_id:
            error_message = "请先选择 1 条 HomeworkImportJob 题目记录。"
        else:
            try:
                due_date_value = date.fromisoformat(due_date_raw)
            except ValueError:
                due_date_value = None
                error_message = "请选择有效的截止日期。"

            summary_html_value = ""
            if not error_message:
                uploaded_summary_file = request.FILES.get("summary_html_file")
                try:
                    summary_html_value = (
                        decode_uploaded_summary_html(uploaded_summary_file)
                        if uploaded_summary_file
                        else summary_html_text
                    ).strip()
                except ValidationError as exc:
                    error_message = "；".join(exc.messages) if exc.messages else str(exc)
                else:
                    if summary_title and not summary_html_value:
                        error_message = "已填写课后总结标题，但还没有上传或粘贴 HTML 内容。"

            visible_import_job = None
            if due_date_value is not None:
                visible_import_job = (
                    get_visible_homework_import_jobs(
                        portal_user,
                        course_id=selected_course.id if selected_course is not None else None,
                    )
                    .filter(id=selected_import_job_id)
                    .first()
                )
                if visible_import_job is None:
                    error_message = "当前老师不能使用这条 HomeworkImportJob 题目记录。"

            if not error_message and visible_import_job is not None:
                allowed_students = list(
                    Student.objects.select_related("user", "parent_user", "teacher_user")
                    .filter(
                        id__in=selected_student_ids,
                        teacher_user=portal_user,
                    )
                    .order_by("id")
                )
                allowed_student_ids = {student.id for student in allowed_students}
                invalid_student_ids = [
                    student_id for student_id in selected_student_ids if student_id not in allowed_student_ids
                ]
                if invalid_student_ids:
                    invalid_students = list(
                        Student.objects.filter(id__in=invalid_student_ids).order_by("id")
                    )
                    invalid_names = "、".join(student.display_name for student in invalid_students)
                    error_message = f"所选学生里存在不属于你名下的记录：{invalid_names or '未知学生'}。"
                else:
                    allowed_student_map = {student.id: student for student in allowed_students}
                    selected_students = [
                        allowed_student_map[student_id]
                        for student_id in selected_student_ids
                        if student_id in allowed_student_map
                    ]
                    assignment_title = (
                        visible_import_job.assignment.title.strip()
                        or visible_import_job.assignment.content.title.strip()
                        or visible_import_job.source_filename.strip()
                    )
                    created_summary = None
                    final_summary_title = ""
                    if summary_html_value:
                        final_summary_title = summary_title or build_default_batch_homework_summary_title(
                            course_label=selected_course.title if selected_course is not None else "批量作业",
                            anchor_date=timezone.localdate(),
                        )
                    try:
                        with transaction.atomic():
                            if summary_html_value:
                                created_summary = HomeworkSummary.objects.create(
                                    title=final_summary_title,
                                    summary_html=summary_html_value,
                                    created_by=portal_user,
                                )
                            for student in selected_students:
                                ensure_homework_content_access(
                                    student,
                                    visible_import_job.assignment.content,
                                    portal_user,
                                )
                                assignment = HomeworkAssignment.objects.create(
                                    teacher=portal_user,
                                    student=student,
                                    content=visible_import_job.assignment.content,
                                    title=assignment_title,
                                    description=assignment_requirement,
                                    due_date=due_date_value,
                                    status=HomeworkAssignment.STATUS_ASSIGNED,
                                    summary=created_summary,
                                    source_import_job=visible_import_job,
                                    assigned_at=timezone.now(),
                                    is_active=True,
                                )
                    except ValidationError as exc:
                        error_message = "；".join(exc.messages) if exc.messages else str(exc)
                    else:
                        redirect_params: dict[str, object] = {
                            "op": "created",
                            "count": len(selected_students),
                        }
                        if created_summary is not None:
                            redirect_params["with_summary"] = 1
                        if selected_course_slug:
                            redirect_params["course"] = selected_course_slug
                        return redirect(
                            build_redirect_with_query(
                                reverse("teacher-homework-batch-create"),
                                params=redirect_params,
                            )
                        )

    try:
        context = build_teacher_homework_batch_create_context(
            portal_user,
            selected_course_slug=selected_course_slug,
            form_values=form_values,
            error_message=error_message,
            success_message=success_message,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该课程") from exc

    return render(
        request,
        "entry/teacher_homework_batch_create.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_homework_import_job_preview(request: HttpRequest, import_job_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    import_job = get_visible_homework_import_jobs(portal_user).filter(id=import_job_id).first()
    if import_job is None:
        raise Http404("未找到该题目记录")
    return JsonResponse(build_homework_import_job_preview_payload(import_job))


@role_required("teacher")
def teacher_assignment_new(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    preset_student_id = normalize_positive_int(request.GET.get("student_id"), default=0, minimum=1) or None
    try:
        context = build_teacher_assignment_form_context(
            portal_user,
            action_label="新增 assignment",
            submit_label="加入我名下",
            student_id=preset_student_id,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该学生") from exc

    if request.method == "POST":
        form_data = {
            "student_id": request.POST.get("student_id", "").strip(),
            "scope_key": request.POST.get("scope_key", "").strip(),
        }
        context = build_teacher_assignment_form_context(
            portal_user,
            action_label="新增 assignment",
            submit_label="加入我名下",
            student_id=preset_student_id,
            form_data=form_data,
        )
        student_id = normalize_positive_int(form_data["student_id"], default=0, minimum=1)
        scope_key = str(form_data["scope_key"])
        course_id, level_code = parse_assignment_scope_key(scope_key)
        if not context["available_scopes"]:
            context["error_message"] = "当前老师还没有可用的课程 / 级别范围，暂时不能新增 assignment。"
        elif student_id not in context["student_option_ids"]:
            context["error_message"] = "请选择有效的学生。"
        elif scope_key not in context["available_scope_keys"] or not course_id or not level_code:
            context["error_message"] = "请选择有效的课程 / 级别范围。"
        else:
            student = Student.objects.get(id=student_id)
            existing_assignment = (
                TeacherStudentAssignment.objects.filter(
                    teacher=portal_user,
                    student=student,
                    course_id=course_id,
                    level_code=level_code,
                )
                .order_by("id")
                .first()
            )
            if existing_assignment:
                if existing_assignment.is_active:
                    context["error_message"] = "该学生在这个课程 / 级别下已经在你名下，无需重复新增。"
                else:
                    existing_assignment.is_active = True
                    existing_assignment.save(update_fields=["is_active", "updated_at"])
                    return redirect(f"{reverse('teacher-student-assignments', args=[student.id])}?op=restored")
            else:
                TeacherStudentAssignment.objects.create(
                    teacher=portal_user,
                    student=student,
                    course=Course.objects.get(id=course_id),
                    level_code=level_code,
                    is_active=True,
                )
                return redirect(f"{reverse('teacher-student-assignments', args=[student.id])}?op=created")

    return render(
        request,
        "entry/teacher_assignment_form.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_student_assignments(request: HttpRequest, student_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_teacher_student_assignment_list_context(portal_user, student_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该学生") from exc

    op = request.GET.get("op", "").strip()
    if op == "created":
        context["success_message"] = "assignment 已创建。"
    elif op == "restored":
        context["success_message"] = "原有 inactive assignment 已恢复为生效中。"
    elif op == "updated":
        context["success_message"] = "assignment 已更新。"
    elif op == "removed":
        context["success_message"] = "assignment 已移除，当前只做软移除。"

    return render(
        request,
        "entry/teacher_student_assignments.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_student_assignment_edit(request: HttpRequest, student_id: int, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    assignment = (
        TeacherStudentAssignment.objects.select_related("course", "student")
        .filter(id=assignment_id, teacher=portal_user, student_id=student_id, is_active=True)
        .first()
    )
    if not assignment:
        raise Http404("未找到该 assignment")

    context = build_teacher_assignment_form_context(
        portal_user,
        action_label="修改 assignment",
        submit_label="保存修改",
        assignment=assignment,
    )

    if request.method == "POST":
        form_data = {
            "student_id": str(student_id),
            "scope_key": request.POST.get("scope_key", "").strip(),
        }
        context = build_teacher_assignment_form_context(
            portal_user,
            action_label="修改 assignment",
            submit_label="保存修改",
            assignment=assignment,
            form_data=form_data,
        )
        scope_key = str(form_data["scope_key"])
        course_id, level_code = parse_assignment_scope_key(scope_key)
        if scope_key not in context["available_scope_keys"] or not course_id or not level_code:
            context["error_message"] = "请选择有效的课程 / 级别范围。"
        else:
            duplicate = (
                TeacherStudentAssignment.objects.filter(
                    teacher=portal_user,
                    student=assignment.student,
                    course_id=course_id,
                    level_code=level_code,
                )
                .exclude(id=assignment.id)
                .order_by("id")
                .first()
            )
            if duplicate and duplicate.is_active:
                context["error_message"] = "目标课程 / 级别的 assignment 已经存在且生效中。"
            elif duplicate and not duplicate.is_active:
                duplicate.is_active = True
                duplicate.save(update_fields=["is_active", "updated_at"])
                assignment.is_active = False
                assignment.save(update_fields=["is_active", "updated_at"])
                return redirect(f"{reverse('teacher-student-assignments', args=[student_id])}?op=updated")
            else:
                assignment.course_id = course_id
                assignment.level_code = level_code
                assignment.save(update_fields=["course", "level_code", "updated_at"])
                return redirect(f"{reverse('teacher-student-assignments', args=[student_id])}?op=updated")

    return render(
        request,
        "entry/teacher_assignment_form.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_student_assignment_remove(request: HttpRequest, student_id: int, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_teacher_assignment_remove_context(portal_user, student_id, assignment_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该 assignment") from exc

    if request.method == "POST":
        assignment = context["assignment"]
        assignment.is_active = False
        assignment.save(update_fields=["is_active", "updated_at"])
        return redirect(f"{reverse('teacher-student-assignments', args=[student_id])}?op=removed")

    return render(
        request,
        "entry/teacher_assignment_remove_confirm.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_detail(request: HttpRequest, course_slug: str) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_teacher_course_detail_context(portal_user, course_slug)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该课程") from exc

    return render(
        request,
        "entry/teacher_course_categories.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_export(request: HttpRequest, course_slug: str) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        payload = build_teacher_course_structure_export_payload(portal_user, course_slug)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该课程") from exc
    filename = f"teacher-course-{course_slug}-{timezone.localtime():%Y%m%d-%H%M%S}.json"
    return build_json_download_response(payload=payload, filename=filename)


@role_required("teacher")
def teacher_course_category_export(request: HttpRequest, course_slug: str, category_slug: str) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        payload = build_teacher_course_structure_export_payload(
            portal_user,
            course_slug,
            category_slug=category_slug,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该课程分类") from exc
    filename = f"teacher-course-{course_slug}-category-{category_slug}-{timezone.localtime():%Y%m%d-%H%M%S}.json"
    return build_json_download_response(payload=payload, filename=filename)


@role_required("teacher")
def teacher_course_level_export(
    request: HttpRequest,
    course_slug: str,
    category_slug: str,
    level_code: str,
) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        payload = build_teacher_course_structure_export_payload(
            portal_user,
            course_slug,
            category_slug=category_slug,
            level_code=level_code,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该 Level") from exc
    filename = f"teacher-course-{course_slug}-level-{level_code.lower()}-{timezone.localtime():%Y%m%d-%H%M%S}.json"
    return build_json_download_response(payload=payload, filename=filename)


@role_required("teacher")
def teacher_course_students_detail(request: HttpRequest, course_slug: str) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    search_query = request.GET.get("q", "").strip()
    page = normalize_grid_page(request.GET.get("page"))
    page_size = normalize_grid_page_size(request.GET.get("page_size"))

    def build_context() -> dict:
        return build_teacher_course_students_detail_context(
            portal_user,
            course_slug,
            search_query=search_query,
            page=page,
            page_size=page_size,
        )

    try:
        context = build_context()
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该课程") from exc

    student_import_modal_should_open = request.GET.get("open_import") == "1"
    student_import_result: dict[str, object] | None = None
    student_import_error_message = ""
    can_import_students = teacher_can_import_students(portal_user) and context["course"].slug == "cpp"
    if student_import_modal_should_open and not can_import_students:
        student_import_modal_should_open = False

    if request.method == "POST" and (request.POST.get("form_action") or "").strip() == "import_students_csv":
        if not teacher_can_import_students(portal_user):
            return HttpResponseForbidden("只有 teacher001 可以导入学生。")

        student_import_modal_should_open = True
        if context["course"].slug != "cpp":
            student_import_error_message = "当前仅支持在 C++ 课程下导入学生。"
        else:
            uploaded_file = request.FILES.get("student_import_file") or request.FILES.get("student_csv_file")
            if uploaded_file is None:
                student_import_error_message = "请先选择一个 CSV / XLSX 文件再提交。"
            else:
                try:
                    rows = parse_student_import_file(uploaded_file)
                except ValidationError as exc:
                    student_import_error_message = str(exc)
                else:
                    student_import_result = import_students_from_rows(
                        teacher_user=portal_user,
                        course=context["course"],
                        rows=rows,
                    )

        context = build_context()
        can_import_students = teacher_can_import_students(portal_user) and context["course"].slug == "cpp"
        if student_import_result is not None:
            success_count = int(student_import_result["success_count"])
            failure_count = int(student_import_result["failure_count"])
            if success_count and failure_count:
                context["success_message"] = f"CSV 导入完成：成功 {success_count} 行，失败 {failure_count} 行。"
            elif success_count:
                context["success_message"] = f"CSV 导入完成：成功 {success_count} 行。"
            elif failure_count and not student_import_error_message:
                student_import_error_message = "CSV 导入失败：没有成功导入任何学生。"
        if student_import_error_message:
            context["error_message"] = student_import_error_message

    context["can_import_students"] = can_import_students
    context["student_import_modal_should_open"] = student_import_modal_should_open
    context["student_import_result"] = student_import_result
    context["student_import_error_message"] = student_import_error_message

    return render(
        request,
        "entry/teacher_course_students_detail.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_student_pool(request: HttpRequest, course_slug: str) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    selected_level_code = (request.POST.get("level_code") or request.GET.get("level_code") or "").strip().upper()
    try:
        context = build_teacher_course_student_pool_context(
            portal_user,
            course_slug,
            selected_level_code=selected_level_code,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该课程") from exc

    if request.GET.get("saved") == "1":
        created_count = normalize_positive_int(request.GET.get("created"), default=0, minimum=0)
        restored_count = normalize_positive_int(request.GET.get("restored"), default=0, minimum=0)
        skipped_count = normalize_positive_int(request.GET.get("skipped"), default=0, minimum=0)
        context["success_message"] = (
            f"学生池保存完成：新增 {created_count} 人，恢复 {restored_count} 人，跳过 {skipped_count} 人。"
        )
    elif request.GET.get("single_student_saved") == "1":
        context["success_message"] = "单个学生添加成功，已加入当前课程。"

    if request.method == "POST":
        form_action = (request.POST.get("form_action") or "bulk_add_students").strip()
        course = context["course"]

        if form_action == "create_single_student":
            form_values = build_teacher_course_student_pool_single_student_form_values(
                context,
                {
                    "student_name": request.POST.get("new_student_name"),
                    "parent_phone": request.POST.get("new_parent_phone"),
                    "course_id": request.POST.get("new_course_id"),
                    "permission_level_code": request.POST.get("new_permission_level_code"),
                    "primary_level_name": request.POST.get("new_primary_level_name"),
                },
            )
            context["single_student_form_values"] = form_values
            context["single_student_modal_should_open"] = True

            student_name = form_values["student_name"]
            parent_phone = normalize_phone(form_values["parent_phone"])
            selected_course_id = normalize_positive_int(form_values["course_id"], default=0, minimum=1)
            normalized_permission_level_code = (
                normalize_assignment_level(course.slug, form_values["permission_level_code"]) or ""
            )
            primary_level_name = form_values["primary_level_name"]
            context["single_student_form_values"]["parent_phone"] = parent_phone or form_values["parent_phone"]
            context["single_student_form_values"]["permission_level_code"] = (
                normalized_permission_level_code or form_values["permission_level_code"]
            )
            context["single_student_form_values"]["primary_level_name"] = primary_level_name

            if not student_name or not parent_phone or not selected_course_id or not normalized_permission_level_code or not primary_level_name:
                context["single_student_error_message"] = "请完整填写学生姓名、家长电话、当前课程、权限等级和等级名称。"
            elif selected_course_id != course.id:
                context["single_student_error_message"] = "请选择当前页面对应的课程后再提交。"
            elif normalized_permission_level_code not in context["single_student_permission_level_codes"]:
                context["single_student_error_message"] = "请选择有效的权限等级后再提交。"
            elif primary_level_name not in context["single_student_level_name_options"]:
                context["single_student_error_message"] = "请选择有效的等级名称后再提交。"
            else:
                try:
                    with transaction.atomic():
                        create_or_update_student_with_parent_and_assignment(
                            teacher_user=portal_user,
                            course=course,
                            student_name=student_name,
                            parent_phone=parent_phone,
                            primary_track_name=infer_student_primary_track_name(
                                course=course,
                                primary_level_name=primary_level_name,
                            ),
                            primary_level_name=primary_level_name,
                            level_code=normalized_permission_level_code,
                            default_password=DEFAULT_IMPORTED_ACCOUNT_PASSWORD,
                            allow_existing_student=False,
                        )
                except StudentImportError as exc:
                    context["single_student_error_message"] = str(exc)
                else:
                    redirect_url = build_redirect_with_query(
                        reverse("teacher-course-student-pool", args=[course_slug]),
                        params={
                            "level_code": context["selected_level_code"],
                            "single_student_saved": 1,
                        },
                    )
                    return redirect(redirect_url)

            if context["single_student_error_message"]:
                context["error_message"] = context["single_student_error_message"]
        else:
            selected_student_ids = {int(value) for value in request.POST.getlist("student_ids") if value.isdigit()}
            pool_student_ids = context["pool_student_ids"]
            level_code = selected_level_code

            if not context["available_level_codes"]:
                context["error_message"] = "当前课程方向还没有可用级别，暂时不能从学生池批量加入。"
            elif level_code not in context["available_level_codes"]:
                context["error_message"] = "请选择有效的级别后再保存。"
            else:
                created_count = 0
                restored_count = 0
                skipped_count = 0
                valid_student_ids = sorted(selected_student_ids & pool_student_ids)

                for student_id in valid_student_ids:
                    assignment = (
                        TeacherStudentAssignment.objects.filter(
                            teacher=portal_user,
                            student_id=student_id,
                            course_id=course.id,
                            level_code=level_code,
                        )
                        .order_by("id")
                        .first()
                    )
                    if assignment:
                        if assignment.is_active:
                            skipped_count += 1
                        else:
                            assignment.is_active = True
                            assignment.save(update_fields=["is_active", "updated_at"])
                            restored_count += 1
                    else:
                        TeacherStudentAssignment.objects.create(
                            teacher=portal_user,
                            student_id=student_id,
                            course=course,
                            level_code=level_code,
                            is_active=True,
                        )
                        created_count += 1

                query = urlencode(
                    {
                        "saved": 1,
                        "level_code": level_code,
                        "created": created_count,
                        "restored": restored_count,
                        "skipped": skipped_count,
                    }
                )
                return redirect(f"{reverse('teacher-course-student-pool', args=[course_slug])}?{query}")

    return render(
        request,
        "entry/teacher_course_student_pool.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_category_detail(request: HttpRequest, course_slug: str, category_slug: str) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_teacher_course_category_detail_context(portal_user, course_slug, category_slug)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该课程分类") from exc

    return render(
        request,
        "entry/teacher_course_category_levels.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_level_detail(request: HttpRequest, course_slug: str, category_slug: str, level_code: str) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    search_query = request.GET.get("q", "").strip()
    page = normalize_grid_page(request.GET.get("page"))
    page_size = normalize_grid_page_size(request.GET.get("page_size"))
    try:
        context = build_teacher_course_level_detail_context(
            portal_user,
            course_slug,
            category_slug,
            level_code,
            search_query=search_query,
            page=page,
            page_size=page_size,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该 Level") from exc

    op = request.GET.get("op", "").strip()
    if op == "created":
        context["success_message"] = "知识点已创建。"
    elif op == "updated":
        context["success_message"] = "知识点已更新。"
    elif op == "deleted":
        context["success_message"] = "知识点已停用，不会再出现在当前 grid 中。"

    return render(
        request,
        "entry/teacher_course_level_knowledge_points.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_knowledge_point_permissions(
    request: HttpRequest,
    course_slug: str,
    category_slug: str,
    level_code: str,
    content_slug: str,
) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    search_query = (request.POST.get("q") or request.GET.get("q") or "").strip()
    try:
        context = build_teacher_course_content_access_context(
            portal_user,
            course_slug,
            category_slug,
            level_code,
            content_slug,
            search_query=search_query,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该知识点") from exc

    if request.GET.get("saved") == "1":
        context["success_message"] = f"学生权限已保存：当前已开通 {context['open_count']} / {context['total_count']} 人。"

    if request.method == "POST":
        selected_student_ids = {int(value) for value in request.POST.getlist("student_ids") if value.isdigit()}
        managed_student_ids = {row["student_id"] for row in context["student_rows"]}
        content = context["content"]
        for student_id in managed_student_ids:
            next_state = student_id in selected_student_ids
            set_student_content_visibility(
                context["student_map"][student_id],
                content,
                is_visible=next_state,
                granted_by=portal_user,
            )
        query_string = urlencode({"saved": 1, "q": search_query}) if search_query else "saved=1"
        return redirect(f"{reverse('teacher-course-knowledge-point-permissions', args=[course_slug, category_slug, level_code, content_slug])}?{query_string}")

    return render(
        request,
        "entry/teacher_course_knowledge_point_permissions.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_knowledge_point_teaching_page(
    request: HttpRequest,
    course_slug: str,
    category_slug: str,
    level_code: str,
    content_slug: str,
) -> HttpResponse:
    if course_slug == "cpp" and category_slug == "gesp" and level_code == "GESP2":
        if content_slug == ENUMERATION_METHOD_CONTENT_SLUG:
            return redirect(reverse("teacher-cpp-gesp2-enumeration"))
        if content_slug == ASCII_CHAR_ENCODING_CONTENT_SLUG:
            return redirect(reverse("teacher-cpp-gesp2-ascii-char-encoding"))
    if course_slug == "cpp" and category_slug == "gesp" and level_code == "GESP4" and content_slug == ARRAY_2D_CONTENT_SLUG:
        return redirect(reverse("teacher-cpp-gesp4-array-2d"))
    if course_slug == "cpp" and category_slug == "gesp" and level_code == "GESP4":
        if content_slug == BINARY_SEARCH_CONTENT_SLUG:
            return redirect(reverse("teacher-cpp-gesp4-binary-search"))
        if content_slug == SORTING_CONTENT_SLUG:
            return redirect(reverse("teacher-cpp-gesp4-sorting"))
        if content_slug == STRINGS_CONTENT_SLUG:
            return redirect(reverse("teacher-cpp-gesp4-strings"))

    portal_user = get_portal_user_from_request(request)
    try:
        context = build_teacher_course_workflow_placeholder_context(
            portal_user,
            course_slug,
            category_slug,
            level_code,
            action_label="Teaching Page 占位",
            content_slug=content_slug,
            placeholder_message="当前知识点暂未接入真实教学页。后续接入后，这里会直接跳转到教师版 Teaching Page。",
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该知识点") from exc

    return render(
        request,
        "entry/teacher_course_workflow_placeholder.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_knowledge_point_new(request: HttpRequest, course_slug: str, category_slug: str, level_code: str) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    form_data = None
    error_message = ""
    try:
        context = build_knowledge_point_form_context(
            portal_user,
            course_slug,
            category_slug,
            level_code,
            action_label="新增知识点",
            submit_label="创建知识点",
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该 Level") from exc

    if request.method == "POST":
        form_data = {
            "title": request.POST.get("title", "").strip(),
            "slug": request.POST.get("slug", "").strip(),
            "route_path": request.POST.get("route_path", "").strip(),
            "summary": request.POST.get("summary", "").strip(),
            "has_real_content": request.POST.get("has_real_content") == "on",
            "sort_order": request.POST.get("sort_order", "").strip(),
            "level_id": request.POST.get("level_id", "").strip(),
        }
        try:
            context = build_knowledge_point_form_context(
                portal_user,
                course_slug,
                category_slug,
                level_code,
                action_label="新增知识点",
                submit_label="创建知识点",
                form_data=form_data,
            )
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该 Level") from exc

        title = str(form_data["title"])
        slug = str(form_data["slug"])
        selected_level = context["selected_level"]
        route_path = str(form_data["route_path"]) or build_knowledge_point_default_route_path(course_slug, category_slug, selected_level.code, slug)
        sort_order = normalize_positive_int(form_data["sort_order"], default=0, minimum=0)
        if not title:
            error_message = "请输入知识点名称。"
        elif not slug:
            error_message = "请输入 slug。"
        elif normalize_positive_int(form_data["level_id"], default=0, minimum=1) not in context["available_level_ids"]:
            error_message = "请选择有效的 Level。"
        elif CourseContent.objects.filter(slug=slug).exists():
            error_message = "该 slug 已存在，请更换。"
        elif CourseContent.objects.filter(route_path=route_path).exists():
            error_message = "该 route_path 已存在，请更换。"
        else:
            existing_type = (
                CourseContent.objects.filter(course=context["course"])
                .exclude(content_type="")
                .order_by("id")
                .values_list("content_type", flat=True)
                .first()
            ) or f"{context['course'].title}{context['category'].title}"
            next_sort_order = sort_order or (
                (CourseContent.objects.filter(level=selected_level).aggregate(max_sort=Max("sort_order"))["max_sort"] or 0) + 1
            )
            CourseContent.objects.create(
                course=context["course"],
                level=selected_level,
                content_type=existing_type,
                slug=slug,
                title=title,
                phase=selected_level.code,
                permission_code=infer_content_permission_code(
                    context["course"].slug,
                    level_code=selected_level.code,
                    phase=selected_level.code,
                ),
                sort_order=next_sort_order,
                route_path=route_path,
                summary=str(form_data["summary"]),
                has_real_content=bool(form_data["has_real_content"]),
                is_active=True,
            )
            return redirect(f"{reverse('teacher-course-level-detail', args=[course_slug, category_slug, selected_level.code])}?op=created")

        context["error_message"] = error_message
        context["form_values"]["route_path"] = route_path

    return render(
        request,
        "entry/teacher_course_knowledge_point_form.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_knowledge_point_edit(
    request: HttpRequest,
    course_slug: str,
    category_slug: str,
    level_code: str,
    content_slug: str,
) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        scope = get_teacher_course_scope(portal_user, course_slug)
        category = get_teacher_course_category(scope["course"], category_slug)
        level = get_teacher_course_level(category, level_code)
        content = get_teacher_course_content(scope["course"], level, content_slug)
        context = build_knowledge_point_form_context(
            portal_user,
            course_slug,
            category_slug,
            level_code,
            action_label="修改知识点",
            submit_label="保存修改",
            content=content,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该知识点") from exc

    if request.method == "POST":
        form_data = {
            "title": request.POST.get("title", "").strip(),
            "slug": request.POST.get("slug", "").strip(),
            "route_path": request.POST.get("route_path", "").strip(),
            "summary": request.POST.get("summary", "").strip(),
            "has_real_content": request.POST.get("has_real_content") == "on",
            "sort_order": request.POST.get("sort_order", "").strip(),
            "level_id": request.POST.get("level_id", "").strip(),
        }
        context = build_knowledge_point_form_context(
            portal_user,
            course_slug,
            category_slug,
            level_code,
            action_label="修改知识点",
            submit_label="保存修改",
            content=content,
            form_data=form_data,
        )
        title = str(form_data["title"])
        slug = str(form_data["slug"])
        selected_level = context["selected_level"]
        route_path = str(form_data["route_path"]) or build_knowledge_point_default_route_path(course_slug, category_slug, selected_level.code, slug)
        sort_order = normalize_positive_int(form_data["sort_order"], default=content.sort_order, minimum=0)
        if not title:
            context["error_message"] = "请输入知识点名称。"
        elif not slug:
            context["error_message"] = "请输入 slug。"
        elif normalize_positive_int(form_data["level_id"], default=0, minimum=1) not in context["available_level_ids"]:
            context["error_message"] = "请选择有效的 Level。"
        elif CourseContent.objects.filter(slug=slug).exclude(id=content.id).exists():
            context["error_message"] = "该 slug 已存在，请更换。"
        elif CourseContent.objects.filter(route_path=route_path).exclude(id=content.id).exists():
            context["error_message"] = "该 route_path 已存在，请更换。"
        else:
            content.title = title
            content.slug = slug
            content.route_path = route_path
            content.summary = str(form_data["summary"])
            content.has_real_content = bool(form_data["has_real_content"])
            content.phase = selected_level.code
            content.level = selected_level
            content.permission_code = infer_content_permission_code(
                context["course"].slug,
                level_code=selected_level.code,
                phase=selected_level.code,
            )
            content.sort_order = sort_order
            content.save(
                update_fields=[
                    "title",
                    "slug",
                    "route_path",
                    "summary",
                    "has_real_content",
                    "phase",
                    "level",
                    "permission_code",
                    "sort_order",
                ]
            )
            return redirect(f"{reverse('teacher-course-level-detail', args=[course_slug, category_slug, selected_level.code])}?op=updated")
        context["form_values"]["route_path"] = route_path

    return render(
        request,
        "entry/teacher_course_knowledge_point_form.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_course_knowledge_point_delete(
    request: HttpRequest,
    course_slug: str,
    category_slug: str,
    level_code: str,
    content_slug: str,
) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_knowledge_point_delete_context(portal_user, course_slug, category_slug, level_code, content_slug)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该知识点") from exc

    if request.method == "POST":
        content = context["content"]
        content.is_active = False
        content.save(update_fields=["is_active"])
        return redirect(f"{reverse('teacher-course-level-detail', args=[course_slug, category_slug, level_code])}?op=deleted")

    return render(
        request,
        "entry/teacher_course_knowledge_point_delete_confirm.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("teacher")
def teacher_student_detail(request: HttpRequest, student_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)

    homework_success_map = {
        "created": "作业已布置，学生端现在可以在“我的作业”里看到，并已同步补开对应知识点权限。",
        "reviewed": "评语已保存，学生端会同步显示最新内容。",
        "cancelled": "作业已取消，记录会继续保留在当前列表里。",
        "summary_created": "本周总结已创建，并已绑定到选定日期范围内的作业。",
    }

    def render_detail(
        *,
        homework_form_values: dict[str, object] | None = None,
        homework_error_message: str = "",
        homework_summary_form_values: dict[str, object] | None = None,
        homework_summary_error_message: str = "",
    ) -> HttpResponse:
        success_message = homework_success_map.get((request.GET.get("homework_op") or "").strip(), "")
        if (request.GET.get("homework_op") or "").strip() == "summary_created":
            bound_count = normalize_positive_int(request.GET.get("summary_bound"), default=0, minimum=0)
            if bound_count:
                success_message = f"本周总结已创建，并绑定 {bound_count} 条当周作业。"
        content_restriction_success_message = ""
        try:
            detail_context = build_teacher_student_detail_context(
                portal_user,
                student_id,
                homework_form_values=homework_form_values,
                homework_error_message=homework_error_message,
                homework_success_message=success_message,
                homework_summary_form_values=homework_summary_form_values,
                homework_summary_error_message=homework_summary_error_message,
            )
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该学生") from exc
        if request.GET.get("content_restriction_saved") == "1":
            content_restriction_success_message = (
                f"当前已限制 {detail_context['content_restriction_restricted_count']} / "
                f"{detail_context['content_restriction_total_count']} 个默认可见内容。"
            )
        return render(
            request,
            "entry/teacher_student_detail.html",
            {
                "role_label": ROLE_CONFIG["teacher"]["label"],
                "content_restriction_success_message": content_restriction_success_message,
                **build_shell_identity_context(request),
                **detail_context,
            },
        )

    if request.method == "POST":
        try:
            context = build_teacher_student_detail_context(portal_user, student_id)
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该学生") from exc
        action = request.POST.get("form_action", "").strip()
        student = context["student"]

        if action == "save_topic_access":
            selected_slugs = set(request.POST.getlist("topic_slugs"))
            for item in context["topic_access_items"]:
                try:
                    content = get_gesp4_topic_content(item["slug"])
                except ObjectDoesNotExist as exc:
                    raise Http404("未找到该专题") from exc
                next_state = item["slug"] in selected_slugs
                set_student_content_visibility(
                    student,
                    content,
                    is_visible=next_state,
                    granted_by=portal_user,
                )

        elif action == "save_content_restrictions":
            restricted_content_ids = {int(value) for value in request.POST.getlist("restricted_content_ids") if value.isdigit()}
            managed_content_ids = {item["id"] for item in context["content_restriction_items"]}
            managed_contents = {
                content.id: content
                for content in CourseContent.objects.select_related("course", "level").filter(id__in=managed_content_ids)
            }
            for content_id, content in managed_contents.items():
                set_student_content_visibility(
                    student,
                    content,
                    is_visible=content_id not in restricted_content_ids,
                    granted_by=portal_user,
                )
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-student-detail", args=[student_id]),
                    params={"content_restriction_saved": 1},
                    anchor="content-restrictions",
                )
            )

        elif action == "add_evaluation":
            evaluation_text = request.POST.get("evaluation_text", "").strip()
            if evaluation_text:
                TeacherEvaluation.objects.create(
                    student=student,
                    teacher=portal_user,
                    evaluation_text=evaluation_text,
                )

        elif action == "add_reward":
            reward_text = request.POST.get("reward_text", "").strip()
            if reward_text:
                RewardRecord.objects.create(
                    student=student,
                    teacher=portal_user,
                    reward_text=reward_text,
                )

        elif action == "add_lesson_hours":
            delta_raw = request.POST.get("delta_hours", "").strip()
            note = request.POST.get("lesson_hour_note", "").strip()
            try:
                delta_hours = int(delta_raw)
            except ValueError:
                delta_hours = 0

            if delta_hours:
                LessonHourLedger.objects.create(
                    student=student,
                    teacher=portal_user,
                    delta_hours=delta_hours,
                    note=note,
                )

        elif action == "create_homework":
            available_contents = get_teacher_student_homework_contents(portal_user, student)
            content_map = {content.id: content for content in available_contents}
            selected_content_id = normalize_positive_int(request.POST.get("content_id"), default=0, minimum=1)
            title = request.POST.get("title", "").strip()
            description = request.POST.get("description", "").strip()
            due_date_raw = request.POST.get("due_date", "").strip()
            form_values = {
                "content_id": selected_content_id,
                "title": title,
                "description": description,
                "due_date": due_date_raw,
            }
            content = content_map.get(selected_content_id)
            if not content:
                return render_detail(
                    homework_form_values=form_values,
                    homework_error_message="请选择当前教师负责范围内的知识点作为作业目标。",
                )
            try:
                due_date_value = date.fromisoformat(due_date_raw)
            except ValueError:
                return render_detail(
                    homework_form_values=form_values,
                    homework_error_message="请选择有效的截止日期。",
                )
            final_title = title or content.title
            ensure_homework_content_access(student, content, portal_user)
            HomeworkAssignment.objects.create(
                teacher=portal_user,
                student=student,
                content=content,
                title=final_title,
                description=description,
                due_date=due_date_value,
                status=HomeworkAssignment.STATUS_ASSIGNED,
                assigned_at=timezone.now(),
                is_active=True,
            )
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-student-detail", args=[student_id]),
                    params={"homework_op": "created"},
                    anchor="homework-panel",
                )
            )

        elif action == "create_homework_summary":
            title = request.POST.get("summary_title", "").strip()
            start_date_raw = request.POST.get("summary_start_date", "").strip()
            end_date_raw = request.POST.get("summary_end_date", "").strip()
            summary_html_input = request.POST.get("summary_html", "").strip()
            form_values = {
                "title": title,
                "start_date": start_date_raw,
                "end_date": end_date_raw,
                "summary_html": summary_html_input,
            }
            start_date_value = parse_iso_date(start_date_raw)
            end_date_value = parse_iso_date(end_date_raw)
            if not start_date_value or not end_date_value:
                return render_detail(
                    homework_summary_form_values=form_values,
                    homework_summary_error_message="请选择有效的起止日期范围。",
                )
            if start_date_value > end_date_value:
                return render_detail(
                    homework_summary_form_values=form_values,
                    homework_summary_error_message="开始日期不能晚于结束日期。",
                )
            uploaded_html_file = request.FILES.get("summary_html_file")
            try:
                summary_html = (
                    decode_uploaded_summary_html(uploaded_html_file)
                    if uploaded_html_file
                    else summary_html_input
                ).strip()
            except ValidationError as exc:
                return render_detail(
                    homework_summary_form_values=form_values,
                    homework_summary_error_message=str(exc),
                )
            if not summary_html:
                return render_detail(
                    homework_summary_form_values=form_values,
                    homework_summary_error_message="请上传 HTML 文件，或直接填写总结 HTML 正文。",
                )
            matched_assignments = list(
                filter_homework_assignments_by_assigned_date(
                    list(get_teacher_student_homework_queryset(portal_user, student)),
                    start_date=start_date_value,
                    end_date=end_date_value,
                )
            )
            if not matched_assignments:
                return render_detail(
                    homework_summary_form_values=form_values,
                    homework_summary_error_message="当前日期范围内没有可绑定的作业，请调整日期范围后重试。",
                )
            final_title = title or build_default_homework_summary_title(
                student,
                start_date=start_date_value,
                end_date=end_date_value,
            )
            summary = HomeworkSummary.objects.create(
                title=final_title,
                summary_html=summary_html,
                created_by=portal_user,
            )
            HomeworkAssignment.objects.filter(
                id__in=[assignment.id for assignment in matched_assignments]
            ).update(summary=summary, updated_at=timezone.now())
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-student-detail", args=[student_id]),
                    params={"homework_op": "summary_created", "summary_bound": len(matched_assignments)},
                    anchor="homework-panel",
                )
            )

        elif action == "review_homework":
            homework_id = normalize_positive_int(request.POST.get("homework_id"), default=0, minimum=1)
            teacher_comment = request.POST.get("teacher_comment", "").strip()
            assignment = (
                HomeworkAssignment.objects.filter(
                    id=homework_id,
                    teacher=portal_user,
                    student=student,
                    is_active=True,
                )
                .select_related("student")
                .first()
            )
            if not assignment:
                raise Http404("未找到该作业")
            previous_status = assignment.status
            previous_comment = assignment.teacher_comment
            previous_reviewed_at = assignment.reviewed_at
            assignment.mark_reviewed(teacher_comment=teacher_comment)
            update_fields = []
            if assignment.teacher_comment != previous_comment:
                update_fields.append("teacher_comment")
            if assignment.status != previous_status:
                update_fields.append("status")
            if assignment.reviewed_at != previous_reviewed_at:
                update_fields.append("reviewed_at")
            if update_fields:
                update_fields.append("updated_at")
                assignment.save(update_fields=update_fields)
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-student-detail", args=[student_id]),
                    params={"homework_op": "reviewed"},
                    anchor=f"homework-{assignment.id}",
                )
            )

        elif action == "cancel_homework":
            homework_id = normalize_positive_int(request.POST.get("homework_id"), default=0, minimum=1)
            assignment = (
                HomeworkAssignment.objects.filter(
                    id=homework_id,
                    teacher=portal_user,
                    student=student,
                    is_active=True,
                )
                .select_related("student")
                .first()
            )
            if not assignment:
                raise Http404("未找到该作业")
            if assignment.cancel():
                assignment.save(update_fields=["status", "updated_at"])
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-student-detail", args=[student_id]),
                    params={"homework_op": "cancelled"},
                    anchor=f"homework-{assignment.id}",
                )
            )

        return redirect("teacher-student-detail", student_id=student_id)

    return render_detail()


@role_required("teacher")
def teacher_homework_builder(request: HttpRequest, student_id: int, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)

    success_map = {
        "parsed": "源文件已上传并完成候选题识别，请先确认题目再写入正式作业。",
    }

    def get_success_message() -> str:
        op = (request.GET.get("op") or "").strip()
        if op == "confirmed":
            confirmed_count = normalize_positive_int(request.GET.get("confirmed_count"), default=0, minimum=0)
            if confirmed_count > 0:
                return f"确认成功，已写入 {confirmed_count} 道正式题目。学生端现在会进入在线作答模式。"
            return "候选题已确认并写入正式作业题目，学生端现在会进入在线作答模式。"
        return success_map.get(op, "")

    def render_builder(*, upload_error_message: str = "", upload_success_message: str = "") -> HttpResponse:
        success_message = upload_success_message or get_success_message()
        try:
            builder_context = build_teacher_homework_builder_context(
                portal_user,
                student_id,
                assignment_id,
                upload_error_message=upload_error_message,
                upload_success_message=success_message,
            )
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该作业") from exc
        return render(
            request,
            "entry/teacher_homework_question_builder.html",
            {
                "role_label": ROLE_CONFIG["teacher"]["label"],
                **build_shell_identity_context(request),
                **builder_context,
            },
        )

    def get_assignment() -> HomeworkAssignment:
        assignment = (
            HomeworkAssignment.objects.select_related("teacher", "student", "content", "content__course", "content__level", "summary")
            .filter(id=assignment_id, teacher=portal_user, student_id=student_id, is_active=True)
            .first()
        )
        if not assignment:
            raise Http404("未找到该作业")
        return assignment

    if request.method == "POST":
        action = request.POST.get("form_action", "").strip()
        assignment = get_assignment()

        if action == "upload_choice_file":
            source_file = request.FILES.get("source_file")
            if not source_file:
                return render_builder(upload_error_message="请先选择一个文件再上传。")
            source_type = detect_homework_source_type(source_file.name)
            if not source_type:
                return render_builder(upload_error_message="当前只支持 pdf / image / html / txt / docx / xlsx 文件。")
            source_sha256 = compute_uploaded_file_sha256(source_file)
            with transaction.atomic():
                locked_assignment = (
                    HomeworkAssignment.objects.select_for_update()
                    .get(id=assignment.id, teacher=portal_user, student_id=student_id, is_active=True)
                )
                pending_import_job = (
                    HomeworkImportJob.objects.select_for_update()
                    .filter(
                        assignment=locked_assignment,
                        is_active=True,
                        parse_status__in=[HomeworkImportJob.STATUS_UPLOADED, HomeworkImportJob.STATUS_PARSING],
                    )
                    .order_by("-created_at", "-id")
                    .first()
                )
                if pending_import_job:
                    return render_builder(upload_error_message="当前作业已有导入任务正在上传或识别中，请等待完成后再试。")
                import_job = HomeworkImportJob.objects.create(
                    teacher=portal_user,
                    assignment=locked_assignment,
                    source_file=source_file,
                    source_filename=source_file.name,
                    source_sha256=source_sha256,
                    source_type=source_type,
                    parse_status=HomeworkImportJob.STATUS_UPLOADED,
                    is_active=True,
                )
            try:
                parse_homework_import_job(import_job)
            except Exception as exc:
                logger.exception(
                    "homework import parse crashed import_job=%s assignment=%s source_type=%s",
                    import_job.id,
                    assignment.id,
                    source_type,
                )
                import_job.parse_status = HomeworkImportJob.STATUS_FAILED
                import_job.candidates_json = []
                import_job.parse_notes = "\n".join(
                    [
                        "页面提示：文件解析时发生后端异常，未生成候选题，请联系管理员查看日志。",
                        f"失败步骤：导入解析",
                        f"失败原因：{type(exc).__name__}: {exc}",
                    ]
                )
                import_job.save(update_fields=["parse_status", "candidates_json", "parse_notes", "updated_at"])
            if import_job.parse_status == HomeworkImportJob.STATUS_FAILED:
                return render_builder(
                    upload_error_message=extract_import_job_user_facing_message(
                        import_job,
                        default="文件解析失败，请检查源文件内容。",
                    )
                )
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-homework-builder", args=[student_id, assignment_id]),
                    params={"op": "parsed"},
                    anchor="candidate-editor",
                )
            )

        if action == "confirm_import_job":
            import_job_id = normalize_positive_int(request.POST.get("import_job_id"), default=0, minimum=1)
            import_job = (
                assignment.import_jobs.filter(id=import_job_id, teacher=portal_user, is_active=True)
                .first()
            )
            if not import_job:
                raise Http404("未找到该导入任务")
            candidate_count = normalize_positive_int(request.POST.get("candidate_count"), default=0, minimum=0)
            if candidate_count <= 0:
                logger.warning(
                    "homework import confirm rejected: empty payload import_job=%s assignment=%s payload_count=0 included_count=0",
                    import_job.id,
                    assignment.id,
                )
                return render_builder(upload_error_message="确认失败：候选题提交数据为空，请刷新页面后重试。")
            payloads = []
            has_candidate_fields = False
            for index in range(candidate_count):
                included_field_name = f"candidate_{index}_included"
                has_candidate_fields = has_candidate_fields or any(
                    field_name in request.POST
                    for field_name in [
                        included_field_name,
                        f"candidate_{index}_stem",
                        f"candidate_{index}_option_A",
                        f"candidate_{index}_option_B",
                        f"candidate_{index}_option_C",
                        f"candidate_{index}_option_D",
                        f"candidate_{index}_correct_answer",
                        f"candidate_{index}_analysis",
                        f"candidate_{index}_notes",
                    ]
                )
                payloads.append(
                    {
                        "included": "1" in request.POST.getlist(included_field_name),
                        "stem": request.POST.get(f"candidate_{index}_stem", ""),
                        "options": {
                            "A": request.POST.get(f"candidate_{index}_option_A", ""),
                            "B": request.POST.get(f"candidate_{index}_option_B", ""),
                            "C": request.POST.get(f"candidate_{index}_option_C", ""),
                            "D": request.POST.get(f"candidate_{index}_option_D", ""),
                        },
                        "correct_answer": request.POST.get(f"candidate_{index}_correct_answer", ""),
                        "analysis": request.POST.get(f"candidate_{index}_analysis", ""),
                        "notes": request.POST.get(f"candidate_{index}_notes", ""),
                    }
                )
            if not has_candidate_fields or not payloads:
                logger.warning(
                    "homework import confirm rejected: candidate fields missing import_job=%s assignment=%s payload_count=%s included_count=0",
                    import_job.id,
                    assignment.id,
                    len(payloads),
                )
                return render_builder(upload_error_message="确认失败：候选题提交数据为空，请刷新页面后重试。")
            included_count = sum(1 for payload in payloads if payload["included"])
            logger.info(
                "homework import confirm attempt import_job=%s assignment=%s payload_count=%s included_count=%s",
                import_job.id,
                assignment.id,
                len(payloads),
                included_count,
            )
            try:
                created_questions = confirm_homework_import_job(import_job, payloads, operator=portal_user)
            except HomeworkImportParseError as exc:
                logger.warning(
                    "homework import confirm failed import_job=%s assignment=%s payload_count=%s included_count=%s written_count=0 error=%s",
                    import_job.id,
                    assignment.id,
                    len(payloads),
                    included_count,
                    exc,
                )
                import_job.candidates_json = encode_sql_ascii_json_text(payloads)
                import_job.parse_status = HomeworkImportJob.STATUS_PARSED
                import_job.save(update_fields=["candidates_json", "parse_status", "updated_at"])
                return render_builder(upload_error_message=f"确认失败：{exc}")
            logger.info(
                "homework import confirm succeeded import_job=%s assignment=%s payload_count=%s included_count=%s written_count=%s",
                import_job.id,
                assignment.id,
                len(payloads),
                included_count,
                len(created_questions),
            )
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-homework-builder", args=[student_id, assignment_id]),
                    params={"op": "confirmed", "confirmed_count": len(created_questions)},
                    anchor="candidate-editor",
                )
            )

    return render_builder()


@role_required("principal")
def principal_dashboard(request: HttpRequest) -> HttpResponse:
    return render_role_page(request, "principal", build_principal_page_shell())
