from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from .auth import (
    ROLE_CONFIG,
    authenticate_credentials,
    build_user_payload,
    clear_auth_cookie,
    get_authenticated_user,
    role_required,
    set_auth_cookie,
)
from .gesp2_catalog import ENUMERATION_METHOD_CONTENT_SLUG
from .gesp4_catalog import ARRAY_2D_CONTENT_SLUG
from .models import LessonHourLedger, PortalUser, RewardRecord, TeacherEvaluation
from .portal_context import (
    build_gesp2_reserved_topic_page,
    build_gesp4_reserved_topic_page,
    build_parent_page_shell,
    build_principal_page_shell,
    build_student_portal_page,
    build_teacher_course_detail_context,
    build_teacher_page_shell,
    build_teacher_student_detail_context,
    get_gesp2_knowledge_content,
    get_gesp4_topic_access_items,
    get_gesp4_topic_content,
    get_student_by_user,
    get_student_content_access,
)
from .topic_content.gesp2_enumeration.context import get_topic_page_context as get_gesp2_enumeration_page_context
from .topic_content.gesp4_array_2d.context import get_topic_page_context


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
def teacher_course_detail(request: HttpRequest, course_slug: str) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    selected_topic_slug = request.GET.get("topic", "").strip() or None
    try:
        context = build_teacher_course_detail_context(portal_user, course_slug, selected_topic_slug=selected_topic_slug)
    except KeyError as exc:
        raise Http404("未找到该课程") from exc

    if request.method == "POST":
        action = request.POST.get("form_action", "").strip()
        if action == "save_course_topic_access":
            topic_slug = request.POST.get("topic_slug", "").strip()
            if course_slug != "cpp":
                raise Http404("当前课程暂不支持专题权限批量分配")
            try:
                content = get_gesp4_topic_content(topic_slug)
            except ObjectDoesNotExist as exc:
                raise Http404("未找到该专题") from exc

            selected_student_ids = {int(value) for value in request.POST.getlist("student_ids") if value.isdigit()}
            managed_student_ids = {row["student_id"] for row in context["topic_assignment_rows"]}
            for student_id in managed_student_ids:
                student = context["student_map"][student_id]
                access = get_student_content_access(student, content)
                next_state = student_id in selected_student_ids
                if access.is_open != next_state:
                    access.set_open_state(is_open=next_state, granted_by=portal_user if next_state else None)
                    access.save(update_fields=["is_open", "granted_by", "granted_at", "updated_at"])

            return redirect(f"{reverse('teacher-course-detail', args=[course_slug])}?topic={topic_slug}")

    return render(
        request,
        "entry/teacher_course_detail.html",
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
