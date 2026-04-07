from __future__ import annotations

from copy import deepcopy

from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from .gesp4_catalog import (
    ARRAY_2D_CONTENT_SLUG,
    GESP4_PHASE,
    GESP4_TOPIC_DEFINITIONS,
    GESP4_TOPIC_MAP,
    GESP4_TOPIC_SLUGS,
)
from .models import CourseContent, PortalUser, Student, StudentContentAccess
from .shell_content import ROLE_SHELL_CONTENT
from .student_portal_content import STUDENT_PORTAL_CONTENT


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


def get_student_content_access(student: Student, content: CourseContent) -> StudentContentAccess:
    access, _ = StudentContentAccess.objects.get_or_create(student=student, content=content)
    return access


def student_has_content_access(student: Student, content_slug: str) -> bool:
    return StudentContentAccess.objects.filter(
        student=student,
        content__slug=content_slug,
        is_open=True,
    ).exists()


def get_gesp4_topic_contents() -> list[CourseContent]:
    content_map = {
        content.slug: content
        for content in CourseContent.objects.select_related("course").filter(
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


def get_gesp4_topic_content(topic_slug: str) -> CourseContent:
    if topic_slug not in GESP4_TOPIC_MAP:
        raise CourseContent.DoesNotExist(f"Unknown GESP4 topic: {topic_slug}")
    return CourseContent.objects.select_related("course").get(
        course__slug="cpp",
        phase=GESP4_PHASE,
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


def _get_access_map(student: Student, contents: list[CourseContent]) -> dict[int, StudentContentAccess]:
    existing_accesses = {
        access.content_id: access
        for access in StudentContentAccess.objects.select_related("content", "granted_by").filter(
            student=student,
            content__in=contents,
        )
    }
    for content in contents:
        if content.id not in existing_accesses:
            existing_accesses[content.id] = get_student_content_access(student, content)
    return existing_accesses


def _build_topic_access_item(content: CourseContent, access: StudentContentAccess) -> dict:
    topic_definition = GESP4_TOPIC_MAP[content.slug]
    is_real_content = topic_definition["content_mode"] == "real"
    content_mode_text = "真实内容" if is_real_content else "内容预留"
    state = "open" if access.is_open else "locked"
    open_note = "已接入真实内容页。" if is_real_content else "当前先进入内容预留页。"
    if access.is_open:
        student_note = f"已开放，{open_note}"
        teacher_note = f"开放时间：{format_datetime(access.granted_at)}；{open_note}"
        parent_hint = f"开放时间：{format_datetime(access.granted_at)}"
        action_label = "进入专题" if is_real_content else "查看预留页"
        action_href = content.route_path
    else:
        student_note = f"教师开放后可进入；{open_note}"
        teacher_note = f"当前未开放；{open_note}"
        parent_hint = "当前尚未开放，教师开放后可进入。"
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
        "is_open": access.is_open,
        "state": state,
        "status_text": "已开放" if access.is_open else "未开放",
        "granted_at": access.granted_at,
        "granted_at_text": format_datetime(access.granted_at) if access.is_open else "暂无开放记录",
        "granted_by_name": access.granted_by.full_name if access.granted_by else "暂无记录",
        "student_note": student_note,
        "teacher_note": teacher_note,
        "parent_hint": parent_hint,
        "action_label": action_label,
        "action_href": action_href,
        "toggle_label": "关闭专题" if access.is_open else "开放专题",
        "toggle_help": (
            "关闭后学生端将重新锁定这个专题。"
            if access.is_open
            else "开放后学生端、家长端和校长端都会同步显示该专题状态。"
        ),
        "path_items": ["C++", "GESP", "GESP4", content.title],
    }


def get_gesp4_topic_access_items(student: Student) -> list[dict]:
    contents = get_gesp4_topic_contents()
    access_map = _get_access_map(student, contents)
    return [_build_topic_access_item(content, access_map[content.id]) for content in contents]


def build_student_portal_page(
    page_key: str,
    portal_user: PortalUser,
    *,
    locked_topic_slug: str | None = None,
) -> dict:
    page_shell = deepcopy(STUDENT_PORTAL_CONTENT[page_key])

    if page_key != "cpp_gesp4":
        return page_shell

    student = get_student_by_user(portal_user)
    topic_items = get_gesp4_topic_access_items(student)
    open_count = sum(1 for item in topic_items if item["is_open"])
    total_count = len(topic_items)

    page_shell["portal_cards"] = []
    for item in topic_items:
        card = {
            "slug": item["slug"],
            "title": item["title"],
            "meta": f"{item['order_label']} · {item['content_mode_text']}",
            "subtitle": (
                "真实内容已开放"
                if item["is_open"] and item["is_real_content"]
                else "内容预留已开放"
                if item["is_open"]
                else "真实内容待开放"
                if item["is_real_content"]
                else "内容预留待开放"
            ),
            "note": item["student_note"],
            "state": item["state"],
            "status_text": item["status_text"],
            "featured": item["is_real_content"],
        }
        if item["is_open"]:
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
        page_shell["entry_hint"] = "GESP4 目录已接入真实读库逻辑，教师开放后即可进入对应专题。"
    return page_shell


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
        return page_shell

    topic_items = get_gesp4_topic_access_items(student)
    open_items = [item for item in topic_items if item["is_open"]]
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
        "summary": f"在读学员 · {student.current_program or '课程待分配'}",
        "campus": student.campus or "校区待补充",
        "grade": student.grade or "年级待补充",
        "mentor": student.teacher_user.full_name if student.teacher_user else "教师待分配",
        "recent_lesson": summarize_open_topics(topic_items, limit=3),
        "avatar": student.display_name[:1] if student.display_name else "学",
    }
    page_shell["profile_items"] = [
        {"label": "当前方向", "value": student.current_program or "待分配"},
        {"label": "阶段标签", "value": build_phase_label(len(open_items))},
        {"label": "学生账号", "value": student.user.username},
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
    return page_shell


def build_teacher_page_shell(portal_user: PortalUser) -> dict:
    page_shell = deepcopy(ROLE_SHELL_CONTENT["teacher"])
    students = list(get_teacher_students(portal_user))

    student_rows = []
    total_open_records = 0
    for student in students:
        topic_items = get_gesp4_topic_access_items(student)
        open_items = [item for item in topic_items if item["is_open"]]
        total_open_records += len(open_items)

        student_rows.append(
            {
                "name": student.display_name,
                "grade": student.grade,
                "course": student.current_program or "C++ > GESP > GESP4",
                "status": f"{len(open_items)}/{len(topic_items)} 已开放" if open_items else "全部未开放",
                "state": "open" if open_items else "locked",
                "note": (
                    f"已开放：{summarize_open_topics(topic_items, limit=2)}"
                    if open_items
                    else "GESP4 目录下 6 个专题当前均未开放。"
                ),
                "action_href": reverse("teacher-student-detail", args=[student.id]),
                "action_label": "查看并操作",
            }
        )

    page_shell["summary_cards"] = [
        {"label": "负责学生", "value": f"{len(students)} 人", "hint": "当前教师名下学生数"},
        {"label": "已开放记录", "value": f"{total_open_records} 项", "hint": "GESP4 多专题的已开放记录总数"},
        {"label": "专题总数", "value": f"{len(GESP4_TOPIC_DEFINITIONS)} 个", "hint": "当前纳入统一开放框架的 GESP4 专题"},
    ]
    page_shell["students"] = student_rows
    return page_shell


def build_teacher_student_detail_context(portal_user: PortalUser, student_id: int) -> dict:
    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id, teacher_user=portal_user)
        .get()
    )
    topic_items = get_gesp4_topic_access_items(student)
    open_items = [item for item in topic_items if item["is_open"]]
    latest_open = max(
        (item for item in open_items if item["granted_at"]),
        key=lambda item: item["granted_at"],
        default=None,
    )

    return {
        "student": student,
        "page_title": f"{student.display_name} · GESP4 专题开放控制",
        "page_description": "当前页以 GESP4 多专题为最小通用开放框架。教师可以逐个专题执行开放或关闭操作。",
        "summary_cards": [
            {"label": "已开放专题", "value": f"{len(open_items)}/{len(topic_items)}", "hint": "该学生当前可进入的 GESP4 专题数量"},
            {"label": "真实内容页", "value": "1 个", "hint": "二维数组专题已接入真实内容页"},
            {"label": "最近开放", "value": latest_open["title"] if latest_open else "暂无", "hint": latest_open["granted_at_text"] if latest_open else "等待教师第一次开放"},
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
        "topic_access_items": topic_items,
        "support_items": [
            {"title": "当前动作范围", "description": "这次覆盖 GESP4 目录下的 6 个专题，而不再只控制二维数组专题。"},
            {"title": "学生端生效方式", "description": "学生端目录页会按真实开放状态显示，并对每个专题路由做后端拦截。"},
            {"title": "内容预留策略", "description": "二维数组专题是实时内容页，其余专题当前先进入统一预留页。"},
        ],
    }


def build_principal_page_shell() -> dict:
    page_shell = deepcopy(ROLE_SHELL_CONTENT["principal"])
    topic_contents = get_gesp4_topic_contents()
    student_count = Student.objects.count()
    teacher_count = PortalUser.objects.filter(role=PortalUser.ROLE_TEACHER, is_active=True).count()
    open_access_count = StudentContentAccess.objects.filter(
        content__slug__in=GESP4_TOPIC_SLUGS,
        is_open=True,
    ).count()
    recent_accesses = list(
        StudentContentAccess.objects.select_related("student", "content", "granted_by")
        .filter(content__slug__in=GESP4_TOPIC_SLUGS, is_open=True)
        .order_by("-granted_at", "-updated_at")[:8]
    )

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
    page_shell["recent_access_records"] = [
        {
            "student_name": access.student.display_name,
            "content_title": access.content.title,
            "granted_by": access.granted_by.full_name if access.granted_by else "系统",
            "granted_at": format_datetime(access.granted_at),
        }
        for access in recent_accesses
    ]
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
