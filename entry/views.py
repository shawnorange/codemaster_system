from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from .auth import (
    DEFAULT_TEST_PASSWORD,
    ROLE_CONFIG,
    authenticate_credentials,
    clear_auth_cookie,
    get_authenticated_user,
    list_test_accounts,
    role_required,
    set_auth_cookie,
)
from .models import PortalUser
from .portal_context import (
    ARRAY_2D_CONTENT_SLUG,
    build_parent_page_shell,
    build_principal_page_shell,
    build_student_portal_page,
    build_teacher_page_shell,
    build_teacher_student_detail_context,
    get_array_2d_content,
    get_student_by_user,
    get_student_content_access,
)
from .topic_content.gesp4_array_2d.context import get_topic_page_context


def login_page(request: HttpRequest) -> HttpResponse:
    current_user = get_authenticated_user(request)
    if current_user:
        return redirect(current_user["landing_url"])

    context = {
        "default_test_password": DEFAULT_TEST_PASSWORD,
        "test_accounts": list_test_accounts(),
    }

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        context["username"] = username

        user = authenticate_credentials(username, password)
        if user:
            response = redirect(user["landing_url"])
            set_auth_cookie(response, user)
            return response

        context["error_message"] = "账号或密码错误，请使用预置测试账号登录。"

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
    user = request.codemaster_user
    return render(
        request,
        "entry/role_page.html",
        {
            "role_key": role_key,
            "role_label": role_config["label"],
            "page_title": role_config["page_title"],
            "page_description": role_config["page_description"],
            "page_shell": page_shell,
            "username": user["username"],
        },
    )


def render_student_portal_page(request: HttpRequest, page_key: str) -> HttpResponse:
    role_config = ROLE_CONFIG["student"]
    user = request.codemaster_user
    portal_user = get_portal_user_from_request(request)
    entry_message = None
    if page_key == "cpp_gesp4" and request.GET.get("locked_content") == ARRAY_2D_CONTENT_SLUG:
        entry_message = "二维数组专题当前未开放，教师开放后才能进入真实内容。"

    page_shell = build_student_portal_page(page_key, portal_user, entry_message=entry_message)
    return render(
        request,
        "entry/student_portal_page.html",
        {
            "role_label": role_config["label"],
            "page_title": page_shell["page_title"],
            "page_description": page_shell["page_description"],
            "page_shell": page_shell,
            "username": user["username"],
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
def student_cpp_gesp4(request: HttpRequest) -> HttpResponse:
    return render_student_portal_page(request, "cpp_gesp4")


@role_required("student")
def student_cpp_gesp4_array_2d(request: HttpRequest) -> HttpResponse:
    role_config = ROLE_CONFIG["student"]
    user = request.codemaster_user
    portal_user = get_portal_user_from_request(request)
    student = get_student_by_user(portal_user)
    access = get_student_content_access(student, get_array_2d_content())
    if not access.is_open:
        return redirect(f"/student/cpp/gesp/gesp4?locked_content={ARRAY_2D_CONTENT_SLUG}")

    topic_context = get_topic_page_context(request.GET.get("lecture"))
    return render(
        request,
        "entry/topics/gesp4_array_2d_page.html",
        {
            "role_label": role_config["label"],
            "username": user["username"],
            **topic_context,
        },
    )


@role_required("parent")
def parent_student_profile(request: HttpRequest) -> HttpResponse:
    return render_role_page(request, "parent", build_parent_page_shell(get_portal_user_from_request(request)))


@role_required("teacher")
def teacher_students(request: HttpRequest) -> HttpResponse:
    return render_role_page(request, "teacher", build_teacher_page_shell(get_portal_user_from_request(request)))


@role_required("teacher")
def teacher_student_detail(request: HttpRequest, student_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_teacher_student_detail_context(portal_user, student_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该学生") from exc

    if request.method == "POST":
        access = context["access"]
        next_state = not access.is_open
        access.set_open_state(is_open=next_state, granted_by=portal_user if next_state else None)
        access.save(update_fields=["is_open", "granted_by", "granted_at", "updated_at"])

        student = context["student"]
        student.phase_label = "二维数组专题已开放" if next_state else "二维数组专题待开放"
        student.save(update_fields=["phase_label"])
        return redirect("teacher-student-detail", student_id=student_id)

    return render(
        request,
        "entry/teacher_student_detail.html",
        {
            "role_label": ROLE_CONFIG["teacher"]["label"],
            "username": request.codemaster_user["username"],
            **context,
        },
    )


@role_required("principal")
def principal_dashboard(request: HttpRequest) -> HttpResponse:
    return render_role_page(request, "principal", build_principal_page_shell())
