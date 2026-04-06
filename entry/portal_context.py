from __future__ import annotations

from copy import deepcopy

from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from .models import CourseContent, PortalUser, Student, StudentContentAccess
from .shell_content import ROLE_SHELL_CONTENT
from .student_portal_content import STUDENT_PORTAL_CONTENT


ARRAY_2D_CONTENT_SLUG = "array-2d"
ARRAY_2D_ROUTE_PATH = "/student/cpp/gesp/gesp4/array-2d"


def format_datetime(value) -> str:
    if not value:
        return "暂无记录"
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")


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
        .filter(teacher_user=portal_user)
        .order_by("id")
    )


def get_array_2d_content() -> CourseContent:
    return CourseContent.objects.select_related("course").get(slug=ARRAY_2D_CONTENT_SLUG)


def get_student_content_access(student: Student, content: CourseContent) -> StudentContentAccess:
    access, _ = StudentContentAccess.objects.get_or_create(student=student, content=content)
    return access


def student_has_content_access(student: Student, content_slug: str = ARRAY_2D_CONTENT_SLUG) -> bool:
    return StudentContentAccess.objects.filter(
        student=student,
        content__slug=content_slug,
        is_open=True,
    ).exists()


def build_student_portal_page(
    page_key: str,
    portal_user: PortalUser,
    *,
    entry_message: str | None = None,
) -> dict:
    page_shell = deepcopy(STUDENT_PORTAL_CONTENT[page_key])

    if page_key != "cpp_gesp4":
        return page_shell

    student = get_student_by_user(portal_user)
    access = get_student_content_access(student, get_array_2d_content())
    is_open = access.is_open

    for card in page_shell["portal_cards"]:
        if card["slug"] != ARRAY_2D_CONTENT_SLUG:
            continue

        if is_open:
            card.update(
                {
                    "meta": "首个真实内容入口",
                    "subtitle": "当前可进入专题首页",
                    "note": f"已由{access.granted_by.full_name if access.granted_by else '教师'}开放，可进入专题首页与讲次内容。",
                    "state": "open",
                    "status_text": "已开放",
                    "action_label": "进入二维数组专题",
                    "action_href": ARRAY_2D_ROUTE_PATH,
                }
            )
        else:
            card.update(
                {
                    "meta": "等待教师开放",
                    "subtitle": "当前不可进入",
                    "note": "教师开放后才能进入专题首页；直接访问真实路由也会被后端拦截。",
                    "state": "locked",
                    "status_text": "未开放",
                    "featured": True,
                }
            )
            card.pop("action_label", None)
            card.pop("action_href", None)

    page_shell["summary_cards"] = [
        {"label": "已开放", "value": "1 个" if is_open else "0 个"},
        {"label": "待开放", "value": "5 个" if is_open else "6 个"},
        {"label": "当前阶段", "value": "GESP4"},
    ]
    page_shell["entry_hint"] = entry_message or (
        "二维数组专题已开放，可以进入专题首页继续学习。"
        if is_open
        else "二维数组专题尚未开放，教师开放后才可进入。"
    )
    return page_shell


def build_parent_page_shell(portal_user: PortalUser) -> dict:
    page_shell = deepcopy(ROLE_SHELL_CONTENT["parent"])
    student = get_parent_student(portal_user)
    if not student:
        page_shell["summary_cards"] = [
            {"label": "关联孩子", "value": "0 位", "hint": "当前账号还未关联学生"},
            {"label": "已开放内容", "value": "0 项", "hint": "暂无可查看内容"},
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
        page_shell["profile_highlight_label"] = "当前开放内容"
        page_shell["profile_highlight_value"] = "暂无"
        page_shell["content_items"] = []
        return page_shell

    accesses = list(
        student.content_accesses.select_related("content", "granted_by").order_by("content__id")
    )
    open_accesses = [access for access in accesses if access.is_open]
    latest_open = next((access for access in accesses if access.is_open and access.granted_at), None)

    page_shell["summary_cards"] = [
        {"label": "关联孩子", "value": "1 位", "hint": "当前家长账号已绑定学生"},
        {"label": "已开放内容", "value": f"{len(open_accesses)} 项", "hint": "当前可进入的学习内容"},
        {
            "label": "最近开放",
            "value": format_datetime(latest_open.granted_at) if latest_open else "暂无",
            "hint": "教师最近一次开放记录",
        },
    ]
    page_shell["student_profile"] = {
        "name": student.display_name,
        "summary": f"在读学员 · {student.current_program or '课程待分配'}",
        "campus": student.campus or "校区待补充",
        "grade": student.grade or "年级待补充",
        "mentor": student.teacher_user.full_name if student.teacher_user else "教师待分配",
        "recent_lesson": "二维数组专题首页已接入系统内容层" if open_accesses else "等待教师开放专题内容",
        "avatar": student.display_name[:1] if student.display_name else "学",
    }
    page_shell["profile_items"] = [
        {"label": "当前方向", "value": student.current_program or "待分配"},
        {"label": "阶段标签", "value": student.phase_label or "待设置"},
        {"label": "学生账号", "value": student.user.username},
        {"label": "专题状态", "value": "已开放" if open_accesses else "未开放"},
    ]
    page_shell["profile_highlight_label"] = "当前开放内容"
    page_shell["profile_highlight_value"] = f"{len(open_accesses)} 项"
    page_shell["content_items"] = [
        {
            "title": access.content.title,
            "subtitle": "C++ > GESP > GESP4",
            "description": access.content.summary or "GESP4 真实专题内容入口。",
            "state": "open" if access.is_open else "locked",
            "status_text": "已开放" if access.is_open else "未开放",
            "route_path": access.content.route_path if access.is_open else "",
            "hint": (
                f"开放时间：{format_datetime(access.granted_at)}"
                if access.is_open
                else "当前尚未开放，教师开放后可进入。"
            ),
        }
        for access in accesses
    ]
    return page_shell


def build_teacher_page_shell(portal_user: PortalUser) -> dict:
    page_shell = deepcopy(ROLE_SHELL_CONTENT["teacher"])
    content = get_array_2d_content()
    students = list(
        get_teacher_students(portal_user).prefetch_related("content_accesses__content", "content_accesses__granted_by")
    )

    student_rows = []
    open_count = 0
    for student in students:
        access = next(
            (
                item
                for item in student.content_accesses.all()
                if item.content_id == content.id
            ),
            None,
        )
        if access is None:
            access = StudentContentAccess(student=student, content=content, is_open=False)

        if access.is_open:
            open_count += 1
            status = "已开放"
            note = f"开放时间：{format_datetime(access.granted_at)}"
            state = "open"
        else:
            status = "未开放"
            note = "当前等待教师执行开放动作。"
            state = "locked"

        student_rows.append(
            {
                "name": student.display_name,
                "grade": student.grade,
                "course": student.current_program or "C++ > GESP > GESP4",
                "status": status,
                "state": state,
                "note": note,
                "action_href": reverse("teacher-student-detail", args=[student.id]),
                "action_label": "查看并操作",
            }
        )

    page_shell["summary_cards"] = [
        {"label": "负责学生", "value": f"{len(students)} 人", "hint": "当前教师名下学生数"},
        {"label": "已开放专题", "value": f"{open_count} 人", "hint": "二维数组专题已开放学生数"},
        {
            "label": "待处理",
            "value": f"{max(len(students) - open_count, 0)} 人",
            "hint": "仍未开放二维数组专题的学生",
        },
    ]
    page_shell["students"] = student_rows
    return page_shell


def build_teacher_student_detail_context(portal_user: PortalUser, student_id: int) -> dict:
    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id, teacher_user=portal_user)
        .get()
    )
    content = get_array_2d_content()
    access = get_student_content_access(student, content)
    is_open = access.is_open

    return {
        "student": student,
        "content": content,
        "access": access,
        "page_title": f"{student.display_name} · 二维数组专题开放控制",
        "page_description": "当前页只承接最小内容开放闭环：查看该学生状态，并对二维数组专题执行开放或关闭动作。",
        "summary_cards": [
            {"label": "当前状态", "value": "已开放" if is_open else "未开放", "hint": "学生是否可以进入真实专题页"},
            {
                "label": "学生账号",
                "value": student.user.username,
                "hint": student.current_program or "当前方向待补充",
            },
            {
                "label": "最近动作",
                "value": format_datetime(access.granted_at) if is_open else "暂无开放记录",
                "hint": "关闭后会清空最近开放时间",
            },
        ],
        "breadcrumbs": [
            {"label": "教师学生列表", "href": reverse("teacher-students")},
            {"label": student.display_name},
        ],
        "student_info_items": [
            {"label": "学生姓名", "value": student.display_name},
            {"label": "年级", "value": student.grade or "待补充"},
            {"label": "家长账号", "value": student.parent_user.username if student.parent_user else "未关联"},
            {"label": "当前方向", "value": student.current_program or "待补充"},
        ],
        "toggle_label": "关闭二维数组专题" if is_open else "开放二维数组专题",
        "toggle_help": (
            "开放后学生端入口会变为可进入状态，并允许访问真实专题内容路由。"
            if not is_open
            else "关闭后学生端入口会重新锁定，直接访问真实专题页也会被后端拦截。"
        ),
        "access_state": "open" if is_open else "locked",
        "support_items": [
            {"title": "当前动作范围", "description": "这次只控制 C++ > GESP > GESP4 > 二维数组专题。"},
            {"title": "学生端生效方式", "description": "目录入口会同步显示开放状态，真实内容路由也会做后端校验。"},
            {"title": "家长与校长可见", "description": "开放结果会同步展示在家长页和校长概览页。"},
        ],
    }


def build_principal_page_shell() -> dict:
    page_shell = deepcopy(ROLE_SHELL_CONTENT["principal"])
    student_count = Student.objects.count()
    teacher_count = PortalUser.objects.filter(role=PortalUser.ROLE_TEACHER, is_active=True).count()
    open_access_count = StudentContentAccess.objects.filter(is_open=True).count()
    recent_accesses = list(
        StudentContentAccess.objects.select_related("student", "content", "granted_by")
        .filter(is_open=True)
        .order_by("-granted_at", "-updated_at")[:5]
    )

    page_shell["summary_cards"] = [
        {"label": "学生数量", "value": str(student_count), "hint": "当前最小学生档案数"},
        {"label": "教师数量", "value": str(teacher_count), "hint": "当前教师账号数"},
        {"label": "已开放内容", "value": str(open_access_count), "hint": "学生内容开放记录中的开放项"},
    ]
    page_shell["overview_cards"] = [
        {"title": "学生数量", "value": str(student_count), "description": "当前已建学生业务档案数量。"},
        {"title": "教师数量", "value": str(teacher_count), "description": "当前可登录教师账号数量。"},
        {"title": "已开放内容", "value": str(open_access_count), "description": "已被教师开放给学生的内容数量。"},
        {
            "title": "覆盖情况",
            "value": f"{open_access_count}/{student_count}" if student_count else "0/0",
            "description": "以当前学生为基数的二维数组专题开放覆盖情况。",
        },
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
    return page_shell
