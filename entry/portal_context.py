from __future__ import annotations

from copy import deepcopy

from django.db.models import QuerySet, Sum
from django.urls import reverse
from django.utils import timezone

from .gesp2_catalog import (
    ENUMERATION_METHOD_CONTENT_SLUG,
    GESP2_KNOWLEDGE_DEFINITIONS,
    GESP2_KNOWLEDGE_MAP,
    GESP2_KNOWLEDGE_SLUGS,
    GESP2_PHASE,
)
from .gesp4_catalog import (
    ARRAY_2D_CONTENT_SLUG,
    GESP4_PHASE,
    GESP4_TOPIC_DEFINITIONS,
    GESP4_TOPIC_MAP,
    GESP4_TOPIC_SLUGS,
)
from .models import (
    CourseContent,
    LessonHourLedger,
    PortalUser,
    RewardRecord,
    Student,
    StudentContentAccess,
    TeacherEvaluation,
)
from .shell_content import ROLE_SHELL_CONTENT
from .student_portal_content import STUDENT_PORTAL_CONTENT
from .teacher_course_catalog import TEACHER_COURSE_DEFINITIONS, TEACHER_COURSE_MAP


def format_datetime(value) -> str:
    if not value:
        return "暂无记录"
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")


def shorten_text(text: str, *, limit: int = 48) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


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


def get_gesp2_knowledge_contents() -> list[CourseContent]:
    content_map = {
        content.slug: content
        for content in CourseContent.objects.select_related("course").filter(
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
    return CourseContent.objects.select_related("course").get(
        course__slug="cpp",
        phase=GESP4_PHASE,
        slug=topic_slug,
        is_active=True,
    )


def get_gesp2_knowledge_content(topic_slug: str) -> CourseContent:
    if topic_slug not in GESP2_KNOWLEDGE_MAP:
        raise CourseContent.DoesNotExist(f"Unknown GESP2 knowledge point: {topic_slug}")
    return CourseContent.objects.select_related("course").get(
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


def get_gesp2_knowledge_items() -> list[dict]:
    items = []
    for content in get_gesp2_knowledge_contents():
        definition = GESP2_KNOWLEDGE_MAP[content.slug]
        is_real_content = definition["content_mode"] == "real"
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
                    "首个真实教学页，已接入知识点说明、基础模板与真题区。"
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

    if page_key == "cpp_gesp":
        gesp2_contents = get_gesp2_knowledge_contents()
        gesp4_contents = get_gesp4_topic_contents()
        page_shell["summary_cards"] = [
            {"label": "已接入层级", "value": "2 个"},
            {"label": "首个真实内容", "value": "GESP2 · 枚举法"},
            {"label": "专题目录", "value": f"GESP4 · {len(gesp4_contents)} 个"},
        ]
        page_shell["entry_hint"] = "GESP2 已接入知识点目录和“枚举法”真实内容页；GESP4 继续保留多专题目录。"
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
                "note": f"当前共 {len(gesp2_contents)} 个知识点目录项，枚举法已接入真实教学页。",
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
        knowledge_items = get_gesp2_knowledge_items()
        real_count = sum(1 for item in knowledge_items if item["is_real_content"])
        reserved_count = len(knowledge_items) - real_count
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
        page_shell["entry_hint"] = "枚举法已作为 GESP2 首个真实知识点网页接入，其它知识点当前先进入统一预留页。"
        return page_shell

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
    page_shell["lesson_hour_summary"] = lesson_hour_summary
    page_shell["lesson_hour_records"] = [serialize_lesson_hour(record) for record in lesson_hour_records]
    return page_shell


def build_teacher_page_shell(portal_user: PortalUser, *, active_tab: str = "students") -> dict:
    page_shell = deepcopy(ROLE_SHELL_CONTENT["teacher"])
    students = list(get_teacher_students(portal_user))
    active_tab = active_tab if active_tab in {"students", "courses"} else "students"

    student_rows = []
    total_open_records = 0
    total_lesson_balance = 0
    for student in students:
        topic_items = get_gesp4_topic_access_items(student)
        open_items = [item for item in topic_items if item["is_open"]]
        lesson_hour_summary = build_lesson_hour_summary(student)
        total_open_records += len(open_items)
        total_lesson_balance += lesson_hour_summary["balance"]

        student_rows.append(
            {
                "name": student.display_name,
                "grade": student.grade,
                "parent_phone": student.parent_user.phone if student.parent_user and student.parent_user.phone else "未录入",
                "program": build_student_learning_path(student),
                "open_topics": f"{len(open_items)}/{len(topic_items)}",
                "remaining_hours": lesson_hour_summary["balance_text"],
                "status": f"{len(open_items)}/{len(topic_items)} 已开放" if open_items else "全部未开放",
                "state": "open" if open_items else "locked",
                "note": (
                    f"已开放：{summarize_open_topics(topic_items, limit=2)}"
                    if open_items
                    else "GESP4 目录下 6 个专题当前均未开放。"
                ),
                "action_href": reverse("teacher-student-detail", args=[student.id]),
                "action_label": "查看详情",
            }
        )

    course_rows = []
    for definition in TEACHER_COURSE_DEFINITIONS:
        student_count = _count_course_students(students, definition["title"])
        if definition["slug"] == "cpp":
            open_content_count = StudentContentAccess.objects.filter(
                student__teacher_user=portal_user,
                content__course__slug="cpp",
                is_open=True,
            ).count()
        else:
            open_content_count = 0
        course_rows.append(
            {
                "level": definition["level"],
                "title": definition["title"],
                "student_count": student_count,
                "open_content_count": open_content_count,
                "state": "open" if definition["slug"] == "cpp" or student_count else "locked",
                "note": definition["summary"],
                "action_href": reverse("teacher-course-detail", args=[definition["slug"]]),
                "action_label": "查看分类" if definition["slug"] == "cpp" else "查看课程",
            }
        )

    current_course_count = sum(1 for row in course_rows if row["student_count"] > 0)
    page_shell["summary_cards"] = [
        {"label": "负责学生", "value": f"{len(students)} 人", "hint": "当前教师名下学生数"},
        {"label": "当前课程", "value": f"{current_course_count} 门", "hint": "当前有学生在学的课程方向"},
        {"label": "已开放内容", "value": f"{total_open_records} 项", "hint": "GESP4 多专题的已开放记录总数"},
        {"label": "课时汇总", "value": format_delta_hours(total_lesson_balance), "hint": "当前负责学生的课时余额汇总"},
    ]
    page_shell["section_eyebrow"] = "Teacher Workbench"
    page_shell["section_title"] = "学生与课程工作入口"
    page_shell["section_description"] = "教师首页现在按“学生 / 课程”两条工作流组织。先从学生详情进入日常操作，再从课程页进入分类入口。"
    page_shell["tabs"] = [
        {
            "key": "students",
            "label": "学生",
            "href": f"{reverse('teacher-students')}?tab=students",
            "is_active": active_tab == "students",
        },
        {
            "key": "courses",
            "label": "课程",
            "href": f"{reverse('teacher-students')}?tab=courses",
            "is_active": active_tab == "courses",
        },
    ]
    page_shell["active_tab"] = active_tab
    page_shell["students"] = student_rows
    page_shell["courses"] = course_rows
    return page_shell


def build_teacher_course_detail_context(portal_user: PortalUser, course_slug: str, *, selected_topic_slug: str | None = None) -> dict:
    if course_slug not in TEACHER_COURSE_MAP:
        raise KeyError(course_slug)

    definition = TEACHER_COURSE_MAP[course_slug]
    students = list(get_teacher_students(portal_user))
    related_students = [student for student in students if get_student_primary_course(student) == definition["title"]]
    open_content_count = (
        StudentContentAccess.objects.filter(
            student__teacher_user=portal_user,
            content__course__slug=course_slug,
            is_open=True,
        ).count()
        if course_slug == "cpp"
        else 0
    )
    selected_topic = None
    topic_filter_items: list[dict] = []
    topic_assignment_rows: list[dict] = []
    topic_assignment_summary = None
    topic_assignment_action = ""
    teaching_page_links: list[dict] = []

    if course_slug == "cpp":
        teaching_page_links = [
            {
                "title": "GESP2 · 枚举法专题页",
                "description": "教师版保留 Knowledge Overview、Common Pitfalls、Scope Boundary、Coverage 与 Teaching Notes，适合直接备课和投屏讲解。",
                "href": reverse("teacher-cpp-gesp2-enumeration"),
                "status_text": "教师版教学页",
            }
        ]
        topic_contents = get_gesp4_topic_contents()
        topic_items = []
        for content in topic_contents:
            topic_definition = GESP4_TOPIC_MAP[content.slug]
            topic_items.append(
                {
                    "slug": content.slug,
                    "title": content.title,
                    "subtitle": topic_definition["subtitle"],
                    "content_mode_text": get_content_mode_text(content.slug),
                    "summary": content.summary or topic_definition["summary"],
                }
            )

        valid_slugs = {item["slug"] for item in topic_items}
        effective_topic_slug = selected_topic_slug if selected_topic_slug in valid_slugs else topic_items[0]["slug"]
        selected_topic = next(item for item in topic_items if item["slug"] == effective_topic_slug)
        selected_content = get_gesp4_topic_content(effective_topic_slug)

        access_map = {
            access.student_id: access
            for access in StudentContentAccess.objects.select_related("granted_by").filter(
                student__in=related_students,
                content=selected_content,
            )
        }
        for student in related_students:
            access = access_map.get(student.id)
            is_open = access.is_open if access else False
            topic_assignment_rows.append(
                {
                    "student_id": student.id,
                    "name": student.display_name,
                    "grade": student.grade or "待补充",
                    "stage": build_student_learning_path(student),
                    "is_open": is_open,
                    "status_text": "已开放" if is_open else "未开放",
                    "state": "open" if is_open else "locked",
                    "hint": (
                        f"开放时间：{format_datetime(access.granted_at)}"
                        if access and access.is_open
                        else "当前尚未开放该专题"
                    ),
                }
            )

        open_student_count = sum(1 for row in topic_assignment_rows if row["is_open"])
        topic_filter_items = [
            {
                **item,
                "href": f"{reverse('teacher-course-detail', args=[course_slug])}?topic={item['slug']}",
                "is_active": item["slug"] == effective_topic_slug,
            }
            for item in topic_items
        ]
        topic_assignment_summary = {
            "selected_title": selected_topic["title"],
            "selected_subtitle": selected_topic["subtitle"],
            "selected_mode_text": selected_topic["content_mode_text"],
            "student_count": len(topic_assignment_rows),
            "open_count": open_student_count,
            "locked_count": len(topic_assignment_rows) - open_student_count,
        }
        topic_assignment_action = reverse("teacher-course-detail", args=[course_slug])

    return {
        "course_slug": course_slug,
        "course_title": definition["title"],
        "page_title": f"{definition['title']} · 课程分类页",
        "page_description": definition["detail_description"],
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=courses"},
            {"label": definition["title"]},
        ],
        "summary_cards": [
            {"label": "级别", "value": definition["level"], "hint": "当前课程级别标签"},
            {"label": "负责学生", "value": f"{len(related_students)} 人", "hint": "当前教师名下该课程方向学生数"},
            {"label": "已开放内容", "value": str(open_content_count), "hint": "当前课程方向下的已开放内容记录数"},
        ],
        "category_items": definition["category_items"],
        "teaching_page_links": teaching_page_links,
        "selected_topic": selected_topic,
        "topic_filter_items": topic_filter_items,
        "topic_assignment_rows": topic_assignment_rows,
        "topic_assignment_summary": topic_assignment_summary,
        "topic_assignment_action": topic_assignment_action,
        "student_map": {student.id: student for student in related_students},
        "related_students": [
            {
                "name": student.display_name,
                "grade": student.grade or "待补充",
                "program": build_student_learning_path(student),
                "action_href": reverse("teacher-student-detail", args=[student.id]),
            }
            for student in related_students
        ],
        "support_items": [
            {"title": "当前作用", "description": "先承接教师端课程入口，让教师从课程视角进入分类页。"},
            {"title": "最小范围", "description": "这次只补课程维度的最小权限分配，不继续扩成复杂矩阵后台。"},
            {"title": "当前重点", "description": "C++ 课程已经可以进一步看到 GESP、CSP、机器人编程 3 个分类入口，并支持按专题给多个学生统一开关权限。"},
        ],
    }


def build_teacher_student_detail_context(portal_user: PortalUser, student_id: int) -> dict:
    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id, teacher_user=portal_user)
        .get()
    )
    topic_items = get_gesp4_topic_access_items(student)
    open_items = [item for item in topic_items if item["is_open"]]
    evaluation_records = list(student.teacher_evaluations.select_related("teacher")[:5])
    reward_records = list(student.reward_records.select_related("teacher")[:5])
    lesson_hour_records = list(student.lesson_hour_ledgers.select_related("teacher")[:5])
    evaluation_items = [serialize_evaluation(record) for record in evaluation_records]
    reward_items = [serialize_reward(record) for record in reward_records]
    lesson_hour_items = [serialize_lesson_hour(record) for record in lesson_hour_records]
    lesson_hour_summary = build_lesson_hour_summary(student)
    learning_path = build_student_learning_path(student)
    phase_text = build_phase_label(len(open_items))
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

    return {
        "student": student,
        "page_title": f"{student.display_name} · 教师工作台",
        "page_description": "当前页将学生概览、GESP4 专题开放管理、教学记录录入和最近记录整理在同一个最小教师工作台中。",
        "summary_cards": [
            {"label": "已开放专题", "value": f"{len(open_items)}/{len(topic_items)}", "hint": "该学生当前可进入的 GESP4 专题数量"},
            {"label": "教师评价", "value": f"{len(evaluation_records)} 条", "hint": "当前学生已有的评价记录数"},
            {"label": "课时余额", "value": lesson_hour_summary["balance_text"], "hint": lesson_hour_summary["latest_note"]},
            {"label": "最近开放", "value": latest_open["title"] if latest_open else "暂无", "hint": latest_open["granted_at_text"] if latest_open else "等待教师第一次开放"},
        ],
        "breadcrumbs": [
            {"label": "教师学生列表", "href": reverse("teacher-students")},
            {"label": student.display_name},
        ],
        "overview_intro": (
            f"当前学习线 {learning_path}，"
            f"已开放 {len(open_items)} 个专题，当前课时余额 {lesson_hour_summary['balance_text']}。"
        ),
        "overview_badges": [
            student.grade or "年级待补充",
            learning_path,
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
                "label": "当前学习线",
                "value": learning_path,
                "hint": phase_text,
            },
            {
                "label": "已开放专题",
                "value": f"{len(open_items)}/{len(topic_items)}",
                "hint": summarize_open_topics(topic_items, limit=2),
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
        "topic_access_summary_text": summarize_open_topics(topic_items, limit=3),
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
        "support_items": [
            {"title": "当前动作范围", "description": "这次覆盖 GESP4 目录下的 6 个专题，而不再只控制二维数组专题。"},
            {"title": "学生端生效方式", "description": "学生端目录页会按真实开放状态显示，并对每个专题路由做后端拦截。"},
            {"title": "内容预留策略", "description": "二维数组专题是实时内容页，其余专题当前先进入统一预留页。"},
            {"title": "教师记录闭环", "description": "评价、奖励和课时变动会同步展示到家长端和校长端。"},
        ],
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
