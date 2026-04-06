from django.http import HttpRequest, HttpResponse
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
from .shell_content import ROLE_SHELL_CONTENT
from .student_portal_content import STUDENT_PORTAL_CONTENT
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


def render_role_page(request: HttpRequest, role_key: str) -> HttpResponse:
    role_config = ROLE_CONFIG[role_key]
    user = request.codemaster_user
    page_shell = ROLE_SHELL_CONTENT[role_key]
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
    page_shell = STUDENT_PORTAL_CONTENT[page_key]
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
    return render_role_page(request, "parent")


@role_required("teacher")
def teacher_students(request: HttpRequest) -> HttpResponse:
    return render_role_page(request, "teacher")


@role_required("principal")
def principal_dashboard(request: HttpRequest) -> HttpResponse:
    return render_role_page(request, "principal")
