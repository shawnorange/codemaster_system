from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from entry.gesp4_catalog import ARRAY_2D_CONTENT_SLUG
from entry.question_fallbacks import QUESTION_TYPE_LABELS, build_array_2d_static_import_records

OPTION_LINE_PATTERN = re.compile(r"^[A-H][\.\uff0e、]\s*(.+)$")
LINE_NUMBER_PATTERN = re.compile(r"^\d+\s*$")
QUESTION_NO_PATTERN = re.compile(r"第\s*\d+\s*题")
CODE_HINT_PATTERN = re.compile(
    r"(#include|using namespace|std::|cout|cin|scanf|printf|return\b|for\s*\(|while\s*\(|if\s*\(|else\b|int\b|char\b|void\b|bool\b|string\b|const\b|;\s*$|\{|\}|<<|>>)"
)


ARRAY_2D_OVERVIEW = {
    "definition": "二维数组专题页不再按题目平铺，而是先按知识点组复盘，再在组内看主例题和同类练习。",
    "summary": "先稳住定义、坐标和按行理解，再过统计窗口、字符网格、局部判断和矩阵整体操作。",
    "learning_targets": [
        "先把合法定义、先行后列和双重循环遍历写稳。",
        "把一整行、按行存储、列数传参和线性映射真正看懂。",
        "把统计窗口、字符网格、局部 check 和矩阵整体操作串成同一类二维数组题。",
    ],
    "estimated_minutes": 18,
}

ARRAY_2D_KNOWLEDGE_POINT_RULES = [
    {
        "id": "definition-access",
        "title": "定义、坐标与基础遍历",
        "summary": "先把二维数组写对、找对、遍历对，避免后面所有题都从坐标层面开始出错。",
        "study_goal": "稳住合法定义、先行后列和双重循环的基本动作。",
        "focus_statement": "遇到“第几行第几列”先转成代码下标，再写访问表达式。",
        "pattern_statement": "声明写两层方括号，访问先行后列，遍历外层行内层列。",
        "practice_hint": "这一组适合先用客观题快速扫语法和坐标错误，再回到模板巩固输入输出。",
        "topic_ids": {"lecture-1-core-definition"},
        "lecture_ids": {"lecture-1"},
        "keywords": (
            "合法定义",
            "正确定义",
            "声明",
            "先行后列",
            "第 2 行第 3 列",
            "下标",
            "二维数组输入模板",
            "按行输出模板",
            "遍历",
        ),
    },
    {
        "id": "row-major-parameter",
        "title": "按行存储、整行视角与传参",
        "summary": "把二维数组从“很多格子”升级成“很多行组成的矩阵”，建立行偏移和列数意识。",
        "study_goal": "理解 a[i] 是一整行、二维数组按行连续存储、传参时列数不能丢。",
        "focus_statement": "算地址和偏移时，先跨过整行，再在行内定位到具体列。",
        "pattern_statement": "整行大小由列数决定，线性映射本质是 i * m + j。",
        "practice_hint": "这组题虽然问法分散，但本质都在训练“按行理解”而不是只盯单个元素。",
        "topic_ids": {"lecture-2-core-row"},
        "lecture_ids": {"lecture-2"},
        "keywords": (
            "按行存储",
            "一整行",
            "arr[1]",
            "arr[0]",
            "地址",
            "偏移",
            "线性",
            "传参",
            "列数",
            "(*arr)[4]",
        ),
    },
    {
        "id": "stats-window",
        "title": "整表统计与固定窗口",
        "summary": "把二维数组当成数值表处理，核心是稳定地写出求和、最值、计数和局部窗口统计。",
        "study_goal": "会把统计量放在正确的位置更新，并在进入局部窗口时控制好重置和边界。",
        "focus_statement": "先确定统计对象和更新时机，再决定变量是在整表级还是窗口级重置。",
        "pattern_statement": "整表题看访问顺序，窗口题看左上角范围和窗口内部循环。",
        "practice_hint": "这一组最怕变量没重置或窗口范围越界，复盘时先检查循环层级。",
        "topic_ids": {"lecture-3-core-basic"},
        "lecture_ids": {"lecture-3"},
        "keywords": (
            "求和",
            "最值",
            "计数",
            "固定窗口",
            "窗口",
            "统计",
            "sum",
            "max",
            "min",
            "cnt",
        ),
    },
    {
        "id": "char-grid",
        "title": "字符网格、地图与四方向",
        "summary": "题面看起来像地图和画布，但本质还是二维数组读入、四方向访问和区域裁剪。",
        "study_goal": "会把字符串行读成字符网格，并稳定处理上下左右和子矩形输出。",
        "focus_statement": "先把字符网格读完整，再做相邻格判断或局部裁剪。",
        "pattern_statement": "字符题仍然是二维数组题，只是单元格类型从整数换成字符。",
        "practice_hint": "复盘时重点盯边界和方向数组，字符网格最容易在下标和越界上失手。",
        "topic_ids": {"lecture-4-core-grid"},
        "lecture_ids": {"lecture-4"},
        "keywords": (
            "字符网格",
            "地图",
            "画布",
            "裁剪",
            "上、下、左、右",
            "上下左右",
            "邻居",
            "网格图",
            "字符串",
        ),
    },
    {
        "id": "local-check",
        "title": "局部判断、子矩形与模式匹配",
        "summary": "进入局部区域题之后，关键不只是枚举范围，更是把 check 函数和局部规则写稳定。",
        "study_goal": "会枚举子矩形左上角、完整检查局部区域，并处理八方向邻域判断。",
        "focus_statement": "先定合法左上角范围，再把局部判断交给独立的小循环或 check 函数。",
        "pattern_statement": "局部题通常是“枚举起点 + 局部检查 + 发现失败及时退出”。",
        "practice_hint": "这一组容易漏方向、漏边界、漏局部重置，复盘时按“起点范围 -> check 逻辑”顺序检查。",
        "topic_ids": {"lecture-5-core-local"},
        "lecture_ids": {"lecture-5"},
        "keywords": (
            "八方向",
            "子矩形",
            "左上角",
            "check",
            "局部",
            "模式",
            "邻域",
            "4×4",
            "最大矩形",
            "平衡子矩形",
        ),
    },
    {
        "id": "matrix-operation",
        "title": "矩阵整体操作与二维到一维映射",
        "summary": "最后一组强调矩阵是整体对象，不只会 a[i][j]，还要会整体传参、翻转、转置和连续空间映射。",
        "study_goal": "把二维数组从“很多格子”提升成“可整体操作的矩阵对象”。",
        "focus_statement": "看到矩阵整体题，先判断是在交换行列、做映射，还是把二维结构作为参数传递。",
        "pattern_statement": "矩阵整体操作仍然依赖行列关系，只是关注点从单点访问变成整行整列或整体变换。",
        "practice_hint": "这组题适合拿来收口，重点检查自己是否仍然只会单点访问而不会整体思考。",
        "topic_ids": {"lecture-6-core-matrix"},
        "lecture_ids": {"lecture-6"},
        "keywords": (
            "矩阵",
            "转置",
            "翻转",
            "交换",
            "参数",
            "连续空间",
            "模拟二维数组",
            "i * m + j",
            "整体",
        ),
    },
]


def build_student_review_data(
    site_data: dict[str, Any],
    db_questions: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    question_records = (
        sorted(db_questions, key=lambda item: (item.get("sort_order", 0), item.get("id") or 0))
        if db_questions
        else build_array_2d_static_import_records(site_data)
    )
    question_source = "db" if db_questions else "static"
    normalized_questions = [_normalize_question_record(record) for record in question_records]
    sections = _build_sections(normalized_questions)
    navigation = _build_navigation(sections)
    overview = _build_overview(site_data, sections)

    return (
        {
            "content_slug": ARRAY_2D_CONTENT_SLUG,
            "topic_title": site_data.get("meta", {}).get("title") or "GESP4 二维数组专题",
            "overview": overview,
            "sections": sections,
            "navigation": navigation,
            "default_section_key": "overview",
        },
        question_source,
    )


def _normalize_question_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    statement = _normalize_lines(payload.get("statement"))
    raw_question_text = str(payload.get("raw_question_text") or payload.get("question_text") or "").strip()
    if not statement:
        statement = _split_blocks(raw_question_text)

    display_content = _extract_question_display_content(
        raw_question_text,
        fallback_statement=statement,
    )
    prompt_text = "\n".join(display_content["theme_stem_lines"]).strip()
    inline_code = display_content["inline_code"]

    analysis_lines = _normalize_lines(payload.get("answer_analysis"))
    if not analysis_lines:
        analysis_lines = _split_blocks(payload.get("analysis_text"))

    answer_text = str(payload.get("answer_text") or "").strip()
    if not answer_text:
        answer_text = "题库暂未补充明确答案文本。"

    classification_reason = str(payload.get("classification_reason") or "").strip()
    pitfall = str(payload.get("pitfall") or "").strip()
    study_guide = payload.get("study_guide")
    study_guide_items = _normalize_study_guide(study_guide)

    question_id = str(
        payload.get("question_id")
        or record.get("code")
        or record.get("id")
        or "array-2d-question"
    )
    question_type = str(record.get("question_type") or "").strip()

    return {
        "id": question_id,
        "dom_id": _build_dom_id(question_id),
        "code": record.get("code") or "",
        "content_slug": record.get("content_slug") or ARRAY_2D_CONTENT_SLUG,
        "level_code": record.get("level_code") or "GESP4",
        "question_type": question_type,
        "question_type_label": QUESTION_TYPE_LABELS.get(question_type, question_type or "题目"),
        "source": _format_source_label(
            payload.get("paper_label"),
            record.get("source_year"),
            record.get("source_month"),
        ),
        "question_no": _format_question_no(
            payload.get("question_number"),
            record.get("source_question_no"),
        ),
        "title": str(record.get("title") or payload.get("summary") or "未命名题目").strip(),
        "summary": str(payload.get("summary") or (statement[0] if statement else "")).strip(),
        "statement": statement,
        "theme_stem_lines": display_content["theme_stem_lines"],
        "option_lines": display_content["option_lines"],
        "inline_code": inline_code,
        "should_render_prompt": not should_hide_prompt(prompt_text, inline_code),
        "answer_text": answer_text,
        "analysis_lines": analysis_lines,
        "classification_reason": classification_reason,
        "pitfall": pitfall,
        "study_guide_items": study_guide_items,
        "reference_code": str(payload.get("reference_code") or "").strip(),
        "lecture_id": str(payload.get("lecture_id") or "").strip(),
        "topic_id": str(payload.get("topic_id") or "").strip(),
        "sort_order": int(record.get("sort_order") or 0),
    }


def _build_sections(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped_questions: dict[str, list[dict[str, Any]]] = {
        rule["id"]: []
        for rule in ARRAY_2D_KNOWLEDGE_POINT_RULES
    }

    for question in questions:
        rule = _classify_question(question)
        grouped_questions[rule["id"]].append(question)

    sections: list[dict[str, Any]] = []
    for rule in ARRAY_2D_KNOWLEDGE_POINT_RULES:
        matched_questions = grouped_questions[rule["id"]]
        if not matched_questions:
            continue

        section_index = len(sections) + 1
        ordered_questions = sorted(
            matched_questions,
            key=lambda item: (item.get("sort_order", 0), item.get("question_no") or "", item.get("title") or ""),
        )
        lead_question = _pick_lead_question(ordered_questions)
        practice_questions = [
            deepcopy(question)
            for question in ordered_questions
            if question["id"] != lead_question["id"]
        ]
        lead_question = deepcopy(lead_question)

        sections.append(
            {
                "key": rule["id"],
                "title": rule["title"],
                "index": section_index,
                "section_label": f"第 {section_index} 组",
                "summary": rule["summary"],
                "study_goal": rule["study_goal"],
                "focus_statement": rule["focus_statement"],
                "pattern_statement": rule["pattern_statement"],
                "practice_hint": rule["practice_hint"],
                "question_count": len(ordered_questions),
                "practice_count": len(practice_questions),
                "question_types": _summarize_question_types(ordered_questions),
                "lead_question": lead_question,
                "practice_questions": practice_questions,
                "prev_section_key": None,
                "prev_section_title": "",
                "next_section_key": None,
                "next_section_title": "",
            }
        )

    sequence = [{"key": "overview", "title": "专题导览"}] + [
        {"key": section["key"], "title": section["title"]}
        for section in sections
    ]
    for index, section in enumerate(sections, start=1):
        previous_item = sequence[index - 1]
        next_item = sequence[index + 1] if index + 1 < len(sequence) else None
        section["prev_section_key"] = previous_item["key"]
        section["prev_section_title"] = previous_item["title"]
        section["next_section_key"] = next_item["key"] if next_item else None
        section["next_section_title"] = next_item["title"] if next_item else ""

    return sections


def _build_navigation(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    navigation = [
        {
            "key": "overview",
            "title": "专题导览",
            "subtitle": "先看分组，再顺着知识点往下学",
            "badge": f"{len(sections)} 组",
        }
    ]
    navigation.extend(
        {
            "key": section["key"],
            "title": section["title"],
            "subtitle": section["study_goal"],
            "badge": f"{section['question_count']} 题",
        }
        for section in sections
    )
    return navigation


def _build_overview(site_data: dict[str, Any], sections: list[dict[str, Any]]) -> dict[str, Any]:
    total_questions = sum(section["question_count"] for section in sections)
    first_section_key = sections[0]["key"] if sections else None
    first_section_title = sections[0]["title"] if sections else ""

    return {
        "title": site_data.get("meta", {}).get("title") or "GESP4 二维数组专题",
        "definition": ARRAY_2D_OVERVIEW["definition"],
        "summary": ARRAY_2D_OVERVIEW["summary"],
        "learning_targets": list(ARRAY_2D_OVERVIEW["learning_targets"]),
        "review_sequence": [section["title"] for section in sections],
        "estimated_minutes": ARRAY_2D_OVERVIEW["estimated_minutes"],
        "section_count": len(sections),
        "question_count": total_questions,
        "first_section_key": first_section_key,
        "prev_section_key": None,
        "next_section_key": first_section_key,
        "next_section_title": first_section_title,
    }


def _classify_question(question: dict[str, Any]) -> dict[str, Any]:
    combined_text = " ".join(
        part
        for part in [
            question.get("title"),
            question.get("summary"),
            question.get("classification_reason"),
            question.get("pitfall"),
            "\n".join(question.get("statement", [])),
            "\n".join(
                item.get("text", "")
                for item in question.get("study_guide_items", [])
            ),
            question.get("reference_code"),
        ]
        if part
    ).lower()

    best_rule = ARRAY_2D_KNOWLEDGE_POINT_RULES[0]
    best_score = -1
    for rule in ARRAY_2D_KNOWLEDGE_POINT_RULES:
        score = 0
        if question.get("topic_id") and question["topic_id"] in rule["topic_ids"]:
            score += 10
        if question.get("lecture_id") and question["lecture_id"] in rule["lecture_ids"]:
            score += 5
        for keyword in rule["keywords"]:
            if keyword and keyword.lower() in combined_text:
                score += 1
        if score > best_score:
            best_rule = rule
            best_score = score

    return best_rule


def _pick_lead_question(questions: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        questions,
        key=lambda item: (
            -_question_richness(item),
            item.get("sort_order", 0),
            item.get("title") or "",
        ),
    )[0]


def _question_richness(question: dict[str, Any]) -> int:
    score = 0
    if question.get("question_type") == "programming":
        score += 3
    if question.get("reference_code"):
        score += 2
    if question.get("study_guide_items"):
        score += 1
    if question.get("analysis_lines"):
        score += 1
    return score


def _summarize_question_types(questions: list[dict[str, Any]]) -> str:
    ordered_types: list[str] = []
    for question in questions:
        label = question.get("question_type_label") or "题目"
        if label not in ordered_types:
            ordered_types.append(label)
    return " / ".join(ordered_types)


def _normalize_lines(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _split_blocks(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split("\n\n") if part.strip()]


def _normalize_study_guide(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        return []

    label_map = {
        "target": "训练目标",
        "why2d": "为什么归到二维数组",
        "method": "解题抓手",
        "steps": "解题步骤",
        "pitfall": "易错提醒",
        "mistake": "易错提醒",
        "checklist": "检查清单",
        "complexity": "复杂度观察",
        "note": "补充说明",
    }

    items: list[dict[str, str]] = []
    for key, raw_value in value.items():
        text = ""
        if isinstance(raw_value, list):
            text = "；".join(str(item).strip() for item in raw_value if str(item).strip())
        else:
            text = str(raw_value or "").strip()
        if not text:
            continue
        items.append(
            {
                "label": label_map.get(key, key),
                "text": text,
            }
        )
    return items


def _extract_question_display_content(
    raw_question_text: str,
    *,
    fallback_statement: list[str],
) -> dict[str, Any]:
    lines = [
        line.rstrip()
        for line in str(raw_question_text or "").splitlines()
        if line.strip()
    ]
    if not lines:
        return {
            "theme_stem_lines": fallback_statement or [],
            "option_lines": [],
            "inline_code": "",
        }

    theme_stem_lines: list[str] = []
    option_lines: list[str] = []
    code_lines: list[str] = []
    saw_line_numbers = False
    collecting_code = False

    for line in lines:
        stripped = line.strip()
        option_match = OPTION_LINE_PATTERN.match(stripped)
        if option_match:
            option_lines.append(option_match.group(1).strip())
            collecting_code = False
            saw_line_numbers = False
            continue

        if LINE_NUMBER_PATTERN.match(stripped):
            saw_line_numbers = True
            continue

        looks_like_code = _looks_like_code_line(stripped)
        if not option_lines and (looks_like_code or collecting_code or saw_line_numbers):
            if looks_like_code or collecting_code or saw_line_numbers:
                code_lines.append(stripped)
                collecting_code = True
                saw_line_numbers = False
                continue

        saw_line_numbers = False
        collecting_code = False
        theme_stem_lines.append(stripped)

    cleaned_theme_stem_lines = theme_stem_lines or fallback_statement or []
    return {
        "theme_stem_lines": cleaned_theme_stem_lines,
        "option_lines": option_lines,
        "inline_code": "\n".join(code_lines).strip(),
    }


def extract_question_no(text: Any) -> str | None:
    matched = QUESTION_NO_PATTERN.search(str(text or ""))
    if not matched:
        return None
    digits = re.search(r"\d+", matched.group(0))
    return digits.group(0) if digits else None


def should_hide_prompt(prompt_text: Any, inline_code_text: Any) -> bool:
    if not prompt_text or not inline_code_text:
        return False

    prompt_question_no = extract_question_no(prompt_text)
    inline_code_question_no = extract_question_no(inline_code_text)
    if not prompt_question_no or not inline_code_question_no:
        return False
    return prompt_question_no == inline_code_question_no


def _looks_like_code_line(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if CODE_HINT_PATTERN.search(text):
        return True
    if text.startswith(("//", "/*", "*")):
        return True
    if "=" in text and not text.endswith("。"):
        return True
    return False


def _format_source_label(paper_label: Any, source_year: Any, source_month: Any) -> str:
    explicit_label = str(paper_label or "").strip()
    if explicit_label:
        return explicit_label
    if source_year and source_month:
        return f"{source_year} 年 {int(source_month):02d} 月"
    if source_year:
        return f"{source_year} 年"
    return "题库题目"


def _format_question_no(question_number: Any, source_question_no: Any) -> str:
    raw_number = question_number or source_question_no
    if raw_number in (None, ""):
        return "未标注题号"
    return f"第 {raw_number} 题"


def _build_dom_id(question_id: str) -> str:
    sanitized = [
        character if character.isalnum() else "-"
        for character in str(question_id)
    ]
    return "".join(sanitized).strip("-") or "array-2d-question"
