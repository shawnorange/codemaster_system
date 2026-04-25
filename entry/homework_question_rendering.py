from __future__ import annotations

from typing import Any

from .homework_option_formatting import build_homework_content_display, format_homework_option_display
from .homework_online import decode_sql_ascii_json_text
from .models import HomeworkQuestion, HomeworkSubmission, HomeworkSubmissionAnswer


QUESTION_STATE_ANSWERING = "answering"
QUESTION_STATE_SUBMITTED = "submitted"
QUESTION_STATE_REVIEWED = "reviewed"
QUESTION_STATE_PRINT = "print"
QUESTION_STATE_PRINT_BLANK = "print_blank"

QUESTION_STATE_LABELS = {
    QUESTION_STATE_ANSWERING: "作答中",
    QUESTION_STATE_SUBMITTED: "已提交",
    QUESTION_STATE_REVIEWED: "已复核",
    QUESTION_STATE_PRINT: "打印视图",
    QUESTION_STATE_PRINT_BLANK: "空白练习卷",
}
QUESTION_BODY_TEMPLATE_MAP = {
    HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE: "entry/includes/homework_questions/question_single_choice.html",
}
QUESTION_FALLBACK_TEMPLATE = "entry/includes/homework_questions/question_unsupported.html"
QUESTION_TYPE_LABELS = {
    HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE: "单选题",
}


def get_question_render_state(
    *,
    submission_status: str = "",
    is_print: bool = False,
    is_blank_print: bool = False,
) -> str:
    if is_blank_print:
        return QUESTION_STATE_PRINT_BLANK
    if is_print:
        return QUESTION_STATE_PRINT
    if submission_status == HomeworkSubmission.STATUS_REVIEWED:
        return QUESTION_STATE_REVIEWED
    if submission_status in {
        HomeworkSubmission.STATUS_SUBMITTED,
        HomeworkSubmission.STATUS_AUTO_CHECKED,
    }:
        return QUESTION_STATE_SUBMITTED
    return QUESTION_STATE_ANSWERING


def build_homework_question_view_models(
    questions: list[HomeworkQuestion],
    *,
    state: str,
    answer_map: dict[int, HomeworkSubmissionAnswer] | None = None,
) -> list[dict[str, Any]]:
    answer_map = answer_map or {}
    return [
        build_homework_question_view_model(
            question,
            state=state,
            answer=answer_map.get(question.id),
        )
        for question in questions
    ]


def build_homework_question_view_model(
    question: HomeworkQuestion,
    *,
    state: str,
    answer: HomeworkSubmissionAnswer | None = None,
) -> dict[str, Any]:
    question_type = str(question.question_type or HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE).strip()
    question_type_label = QUESTION_TYPE_LABELS.get(question_type, question_type or "题目")
    selected_answer = str(answer.selected_answer or "").strip().upper() if answer else ""
    correct_answer = str(answer.correct_answer_snapshot or question.correct_answer or "").strip().upper() if answer else str(question.correct_answer or "").strip().upper()
    analysis = str(answer.analysis_snapshot or question.analysis or "当前老师没有补充解析。").strip() if answer else str(question.analysis or "当前老师没有补充解析。").strip()
    stem_display = build_homework_content_display(question.stem)
    analysis_display = build_homework_content_display(analysis)
    show_feedback = state not in {QUESTION_STATE_ANSWERING, QUESTION_STATE_PRINT_BLANK}
    is_correct = bool(show_feedback and selected_answer and selected_answer == correct_answer)
    is_wrong = bool(show_feedback and not is_correct)

    result_text, badge_tone, feedback_tone = _build_question_result_state(
        state=state,
        show_feedback=show_feedback,
        question_type_label=question_type_label,
        is_correct=is_correct,
        selected_answer=selected_answer,
    )
    decoded_options = decode_sql_ascii_json_text(question.options_json)
    options = _build_question_option_view_models(
        decoded_options if isinstance(decoded_options, dict) else {},
        selected_answer=selected_answer,
        correct_answer=correct_answer,
        show_feedback=show_feedback,
    )

    return {
        "id": question.id,
        "dom_id": f"homework-question-{question.id}",
        "question_type": question_type,
        "question_type_label": question_type_label,
        "question_no": question.question_no,
        "stem": str(question.stem or "").strip(),
        "stem_blocks": stem_display["blocks"],
        "stem_is_code_like": stem_display["is_code_content"],
        "state": state,
        "state_label": QUESTION_STATE_LABELS.get(state, state),
        "body_template": QUESTION_BODY_TEMPLATE_MAP.get(question_type, QUESTION_FALLBACK_TEMPLATE),
        "renderer_key": question_type,
        "input_name": f"question_{question.id}",
        "show_selector": state == QUESTION_STATE_ANSWERING,
        "show_feedback": show_feedback,
        "show_analysis": show_feedback,
        "show_answer_summary": show_feedback,
        "is_print": state in {QUESTION_STATE_PRINT, QUESTION_STATE_PRINT_BLANK},
        "is_blank_print": state == QUESTION_STATE_PRINT_BLANK,
        "options": options,
        "student_answer": selected_answer,
        "student_answer_text": selected_answer or "未作答",
        "correct_answer": correct_answer,
        "correct_answer_text": correct_answer or "暂无",
        "analysis": analysis,
        "analysis_blocks": analysis_display["blocks"],
        "feedback_items": [
            {
                "label": "你的答案",
                "value": selected_answer or "未作答",
                "blocks": [{"kind": "text", "text": selected_answer or "未作答"}],
            },
            {
                "label": "正确答案",
                "value": correct_answer or "暂无",
                "blocks": [{"kind": "text", "text": correct_answer or "暂无"}],
            },
            {"label": "解析", "value": analysis, "blocks": analysis_display["blocks"]},
        ],
        "result_text": result_text,
        "badge_text": result_text,
        "badge_tone": badge_tone,
        "feedback_tone": feedback_tone,
        "is_correct": is_correct,
        "is_wrong": is_wrong,
        "result_section_text": "错题" if is_wrong else "结果",
    }


def _build_question_option_view_models(
    options: dict[str, object],
    *,
    selected_answer: str,
    correct_answer: str,
    show_feedback: bool,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for key in ["A", "B", "C", "D"]:
        raw_text = str(options.get(key) or "").strip()
        if not raw_text:
            continue

        formatted = format_homework_option_display(raw_text)
        is_selected = key == selected_answer and bool(selected_answer)
        is_correct_answer = key == correct_answer and bool(correct_answer)
        is_wrong_selected = bool(show_feedback and is_selected and selected_answer != correct_answer)
        style_tone = "default"
        status_label = ""
        status_tone = ""

        if show_feedback:
            if is_wrong_selected:
                style_tone = "wrong"
                status_label = "你的错误选择"
                status_tone = "wrong"
            elif is_correct_answer:
                style_tone = "correct"
                status_label = "你的答案" if is_selected else "正确答案"
                status_tone = "correct" if not is_selected else "selected"
            elif is_selected:
                style_tone = "selected"
                status_label = "你的答案"
                status_tone = "selected"
        elif is_selected:
            style_tone = "selected"

        items.append(
            {
                "key": key,
                "text": formatted["text"],
                "rendered_text": formatted["display_text"],
                "is_code_like": formatted["is_code_option"],
                "is_selected": is_selected,
                "is_correct_answer": is_correct_answer,
                "is_wrong_selected": is_wrong_selected,
                "style_tone": style_tone,
                "status_label": status_label,
                "status_tone": status_tone,
            }
        )

    return items


def _build_question_result_state(
    *,
    state: str,
    show_feedback: bool,
    question_type_label: str,
    is_correct: bool,
    selected_answer: str,
) -> tuple[str, str, str]:
    if state == QUESTION_STATE_PRINT_BLANK:
        return "空白练习", "answering", "neutral"
    if not show_feedback:
        return question_type_label, "answering", "neutral"
    if is_correct:
        return "回答正确", "correct", "correct"
    if not selected_answer:
        return "未作答", "wrong", "wrong"
    return "回答错误", "wrong", "wrong"
