import json
from urllib.parse import urlencode

from django.db.models import Max
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .auth import (
    ROLE_CONFIG,
    authenticate_credentials,
    build_user_payload,
    clear_auth_cookie,
    get_authenticated_user,
    role_required,
    set_auth_cookie,
)
from .gesp2_catalog import ASCII_CHAR_ENCODING_CONTENT_SLUG, ENUMERATION_METHOD_CONTENT_SLUG
from .gesp4_catalog import ARRAY_2D_CONTENT_SLUG
from .gesp4_catalog import BINARY_SEARCH_CONTENT_SLUG, SORTING_CONTENT_SLUG, STRINGS_CONTENT_SLUG
from .models import Course, CourseContent, LessonHourLedger, PortalUser, RewardRecord, Student, TeacherEvaluation, TeacherStudentAssignment
from .portal_context import (
    build_gesp2_reserved_topic_page,
    build_gesp4_reserved_topic_page,
    build_teacher_assignment_form_context,
    build_teacher_assignment_remove_context,
    build_teacher_course_category_detail_context,
    build_teacher_course_content_access_context,
    build_teacher_course_student_pool_context,
    build_teacher_course_students_detail_context,
    build_teacher_course_structure_export_payload,
    build_parent_page_shell,
    build_principal_page_shell,
    build_student_portal_page,
    build_teacher_course_detail_context,
    build_teacher_course_level_detail_context,
    build_teacher_course_workflow_placeholder_context,
    build_teacher_page_shell,
    build_teacher_student_assignment_list_context,
    build_teacher_student_detail_context,
    get_gesp2_knowledge_content,
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
    get_student_content_access,
)
from .topic_content.gesp2_enumeration.context import get_topic_page_context as get_gesp2_enumeration_page_context
from .topic_content.gesp2_ascii_char_encoding.context import (
    get_topic_page_context as get_gesp2_ascii_char_encoding_page_context,
)
from .topic_content.gesp4_array_2d.context import get_topic_page_context
from .topic_content.gesp4_shared.context import get_topic_page_context as get_generic_gesp4_topic_page_context


def build_shell_identity_context(request: HttpRequest) -> dict[str, str]:
    user = request.codemaster_user
    return {
        "viewer_display_name": user.get("full_name") or user["username"],
        "viewer_username": user["username"],
        "account_settings_href": reverse("student-account-settings") if user["role"] == "student" else "",
    }


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


def build_json_download_response(*, payload: dict, filename: str) -> HttpResponse:
    response = HttpResponse(
        json.dumps(payload, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


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
    try:
        content = get_gesp2_knowledge_content(topic_slug)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该知识点") from exc

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
    topic_context = get_topic_page_context(request.GET.get("lecture"))
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

    access = get_student_content_access(student, content)
    if not access.is_open:
        return redirect(f"/student/cpp/gesp/gesp4?locked_content={topic_slug}")

    if topic_slug == ARRAY_2D_CONTENT_SLUG:
        topic_context = get_topic_page_context(request.GET.get("lecture"))
        return render(
            request,
            "entry/topics/gesp4_array_2d_page.html",
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


@role_required("teacher")
def teacher_students(request: HttpRequest) -> HttpResponse:
    active_tab = request.GET.get("tab", "students")
    return render_role_page(
        request,
        "teacher",
        build_teacher_page_shell(get_portal_user_from_request(request), active_tab=active_tab),
    )


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
    try:
        context = build_teacher_course_students_detail_context(
            portal_user,
            course_slug,
            search_query=search_query,
            page=page,
            page_size=page_size,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该课程") from exc

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

    if request.method == "POST":
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
            course = context["course"]

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
            access = get_student_content_access(context["student_map"][student_id], content)
            next_state = student_id in selected_student_ids
            if access.is_open != next_state:
                access.set_open_state(is_open=next_state, granted_by=portal_user if next_state else None)
                access.save(update_fields=["is_open", "granted_by", "granted_at", "updated_at"])
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
            content.sort_order = sort_order
            content.save(update_fields=["title", "slug", "route_path", "summary", "has_real_content", "phase", "level", "sort_order"])
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
    try:
        context = build_teacher_student_detail_context(portal_user, student_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该学生") from exc

    if request.method == "POST":
        action = request.POST.get("form_action", "").strip()
        student = context["student"]

        if action == "save_topic_access":
            selected_slugs = set(request.POST.getlist("topic_slugs"))
            for item in context["topic_access_items"]:
                try:
                    content = get_gesp4_topic_content(item["slug"])
                except ObjectDoesNotExist as exc:
                    raise Http404("未找到该专题") from exc
                access = get_student_content_access(student, content)
                next_state = item["slug"] in selected_slugs
                if access.is_open != next_state:
                    access.set_open_state(is_open=next_state, granted_by=portal_user if next_state else None)
                    access.save(update_fields=["is_open", "granted_by", "granted_at", "updated_at"])

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

        return redirect("teacher-student-detail", student_id=student_id)

    return render(
        request,
        "entry/teacher_student_detail.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            **build_shell_identity_context(request),
            **context,
        },
    )


@role_required("principal")
def principal_dashboard(request: HttpRequest) -> HttpResponse:
    return render_role_page(request, "principal", build_principal_page_shell())
