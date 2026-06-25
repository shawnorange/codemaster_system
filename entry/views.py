import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import uuid
import unicodedata
import threading
from io import BytesIO
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import Count, F, Max, Q
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import escape
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt

from .account_identity import normalize_phone
from .auth import (
    ROLE_CONFIG,
    api_role_required,
    authenticate_portal_user,
    authenticate_credentials,
    build_auth_token,
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
from .homework_completion_stats import resolve_homework_completion_period_dates
from .exam_online import (
    ExamError,
    activate_exam_access_code,
    create_free_exam_practice_session,
    create_exam_practice_session,
    create_exam_for_students,
    create_or_get_exam_session_by_access_code,
    ensure_exam_run_for_paper,
    generate_unique_exam_access_code,
    get_exam_session_questions,
    grade_exam_session,
    normalize_exam_answer,
    normalize_exam_options,
    record_exam_proctor_event,
    recalculate_exam_scores_for_paper,
    start_exam_session,
)
from .exam_analysis import (
    _request_qwen_analysis,
    _strip_json_code_fence,
    generate_ai_analysis_for_bank_question,
    identify_bank_question_knowledge_points,
    sync_legacy_question_analysis,
)
from .exam_paper_import import (
    ExamPaperImportConfirmError,
    ExamPaperImportError,
    build_default_question_analysis_md,
    clean_imported_markdown,
    combine_raw_ocr_page_markdown,
    confirm_exam_question_bank_import_job,
    detect_exam_import_source_type,
    format_exam_markdown_for_teacher_edit,
    get_supported_exam_import_extensions,
    is_scratch_docx_import_job,
    is_raw_ocr_page_question,
    materialize_raw_ocr_paper_questions,
    restore_exam_markdown_code_fences_from_original,
    separate_programming_reference_solutions_for_paper,
    split_ocr_markdown_into_question_blocks,
)
from .homework_online import (
    HomeworkImportParseError,
    QUESTION_SOURCE_KNOWLEDGE_MARKER,
    compute_uploaded_file_sha256,
    confirm_homework_import_job,
    confirm_question_source_import_job,
    decode_sql_ascii_json_text,
    detect_homework_source_type,
    encode_sql_ascii_json_text,
    grade_homework_submission,
    parse_question_source_knowledge_snapshot,
)
from .manual_overrides import update_question_manual_override
from .models import (
    Course,
    CourseCategory,
    CourseContent,
    CourseLevel,
    ExamPaper,
    ExamQuestion,
    ExamQuestionAnalysisBlock,
    ExamQuestionAnalysisSuggestion,
    ExamQuestionBankAsset,
    ExamQuestionBankImportJob,
    ExamQuestionBankItem,
    ExamKnowledgePointMap,
    ExamQuestionBankOption,
    ExamQuestionBankPaper,
    ExamQuestionBankQuestion,
    ExamSession,
    ExamSubmissionAnswer,
    HomeworkAssignment,
    HomeworkImportJob,
    HomeworkQuestion,
    HomeworkSubmission,
    HomeworkSummary,
    LessonHourLedger,
    PortalUser,
    ProgrammingSubmission,
    Question,
    RewardRecord,
    Student,
    StudentSiteMessage,
    Teacher,
    TeacherEvaluation,
    TeacherStudentAssignment,
)
from .portal_context import (
    build_gesp2_reserved_topic_page,
    build_gesp4_reserved_topic_page,
    build_teacher_homework_assignment_submission_detail_context,
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
    build_student_exam_detail_context,
    build_student_exam_list_context,
    build_student_exam_print_context,
    build_student_exam_record_detail_context,
    build_student_free_practice_context,
    build_free_practice_question_rows_for_filters,
    build_student_site_message_context,
    build_student_practice_page_shell,
    build_teacher_assignment_form_context,
    build_teacher_assignment_remove_context,
    build_teacher_course_category_detail_context,
    build_teacher_course_content_access_context,
    build_teacher_course_student_pool_context,
    build_teacher_course_students_detail_context,
    build_teacher_course_structure_export_payload,
    build_teacher_homework_batch_create_context,
    build_teacher_homework_submission_answer_detail_context,
    build_teacher_homework_submission_detail_context,
    build_teacher_homework_student_period_assignment_detail_context,
    build_parent_page_shell,
    build_principal_page_shell,
    build_student_portal_page,
    build_student_homework_print_context,
    build_teacher_course_detail_context,
    build_teacher_course_level_detail_context,
    build_teacher_course_workflow_placeholder_context,
    build_teacher_exam_page_context,
    build_teacher_exam_detail_context,
    format_knowledge_map_subject_label,
    serialize_available_exam_bank_paper,
    build_teacher_homework_stats_context,
    build_teacher_homework_builder_context,
    build_teacher_page_shell,
    build_teacher_question_source_import_context,
    build_teacher_student_assignment_list_context,
    build_teacher_student_detail_context,
    build_teacher_student_exam_detail_context,
    build_default_batch_homework_summary_title,
    build_default_homework_summary_title,
    ensure_homework_content_access,
    filter_homework_assignments_by_assigned_date,
    get_gesp2_knowledge_content,
    get_teacher_student_homework_queryset,
    get_teacher_batch_homework_contents,
    get_teacher_course_category,
    get_teacher_course_content,
    get_teacher_course_level,
    get_teacher_course_levels,
    get_teacher_question_source_level_options,
    get_teacher_course_scope,
    get_gesp4_topic_access_items,
    get_gesp4_topic_content,
    normalize_grid_page,
    normalize_grid_page_size,
    infer_exam_bank_paper_subject,
    infer_exam_paper_subject_title,
    render_exam_markdown_for_display,
    teacher_can_operate_exam_subject,
    get_student_by_user,
    set_student_content_visibility,
    student_has_content_access,
    get_teacher_student_homework_contents,
    normalize_knowledge_map_subject,
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
from .student_learning_api import (
    build_student_learning_overview,
    build_student_week_lesson_feedback_payload,
    get_student_week_lesson_feedback_assignments,
    normalize_student_learning_anchor_date,
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
OPEN_HOMEWORK_IMPORT_STATUSES = [
    HomeworkImportJob.STATUS_UPLOADED,
    HomeworkImportJob.STATUS_PARSING,
    HomeworkImportJob.STATUS_PARSED,
]


def expire_stale_homework_import_jobs(base_queryset) -> int:
    stale_minutes = max(int(getattr(settings, "HOMEWORK_IMPORT_STALE_MINUTES", 30)), 1)
    cutoff = timezone.now() - timedelta(minutes=stale_minutes)
    stale_jobs = list(
        base_queryset.filter(
            parse_status__in=[HomeworkImportJob.STATUS_UPLOADED, HomeworkImportJob.STATUS_PARSING],
            updated_at__lt=cutoff,
        )
    )
    if not stale_jobs:
        return 0

    stale_note = f"导入任务超过 {stale_minutes} 分钟仍未完成，已自动标记失败，请重新上传。"
    for import_job in stale_jobs:
        import_job.parse_status = HomeworkImportJob.STATUS_FAILED
        import_job.candidates_json = []
        import_job.parse_notes = "\n".join(
            item for item in [import_job.parse_notes.strip(), stale_note] if item
        )
        import_job.save(update_fields=["parse_status", "candidates_json", "parse_notes", "updated_at"])
    return len(stale_jobs)


WECHAT_MINIAPP_ACCESS_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
WECHAT_MINIAPP_GET_PHONE_URL = "https://api.weixin.qq.com/wxa/business/getuserphonenumber"
WECHAT_MINIAPP_REQUEST_TIMEOUT_SECONDS = 10


class MiniappPhoneLoginError(Exception):
    def __init__(self, *, error_code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


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


def _build_miniapp_login_success_response(portal_user: PortalUser) -> JsonResponse:
    user = build_user_payload(portal_user)
    token = build_auth_token(user)
    response = JsonResponse(
        {
            "token": token,
            "user": user,
        }
    )
    set_auth_cookie(response, user)
    return response


def _fetch_wechat_miniapp_access_token() -> str:
    appid = str(getattr(settings, "WECHAT_MINIAPP_APPID", "") or "").strip()
    secret = str(getattr(settings, "WECHAT_MINIAPP_SECRET", "") or "").strip()
    if not appid or not secret:
        raise MiniappPhoneLoginError(
            error_code="wechat_config_missing",
            message="WECHAT_MINIAPP_APPID 或 WECHAT_MINIAPP_SECRET 未配置。",
            status=500,
        )

    try:
        response = requests.get(
            WECHAT_MINIAPP_ACCESS_TOKEN_URL,
            params={
                "grant_type": "client_credential",
                "appid": appid,
                "secret": secret,
            },
            timeout=WECHAT_MINIAPP_REQUEST_TIMEOUT_SECONDS,
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MiniappPhoneLoginError(
            error_code="wechat_api_error",
            message="微信 access_token 接口调用失败。",
            status=502,
        ) from exc

    access_token = str(payload.get("access_token") or "").strip()
    if access_token:
        return access_token

    raise MiniappPhoneLoginError(
        error_code="wechat_api_error",
        message="微信 access_token 获取失败。",
        status=502,
    )


def _fetch_wechat_miniapp_phone_number(*, code: str) -> str:
    access_token = _fetch_wechat_miniapp_access_token()

    try:
        response = requests.post(
            WECHAT_MINIAPP_GET_PHONE_URL,
            params={"access_token": access_token},
            json={"code": code},
            timeout=WECHAT_MINIAPP_REQUEST_TIMEOUT_SECONDS,
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MiniappPhoneLoginError(
            error_code="wechat_api_error",
            message="微信手机号接口调用失败。",
            status=502,
        ) from exc

    if int(payload.get("errcode") or 0) != 0:
        raise MiniappPhoneLoginError(
            error_code="invalid_phone_code",
            message="微信手机号 code 无效。",
            status=400,
        )

    phone_info = payload.get("phone_info")
    if not isinstance(phone_info, dict):
        raise MiniappPhoneLoginError(
            error_code="invalid_phone_code",
            message="微信手机号 code 无效。",
            status=400,
        )

    normalized_phone = normalize_phone(
        str(phone_info.get("purePhoneNumber") or phone_info.get("phoneNumber") or "")
    )
    if not normalized_phone:
        raise MiniappPhoneLoginError(
            error_code="invalid_phone_code",
            message="微信手机号 code 无效。",
            status=400,
        )
    return normalized_phone


def _resolve_parent_portal_user_by_phone(*, phone: str) -> PortalUser:
    normalized_phone = normalize_phone(phone)
    matched_parents = [
        portal_user
        for portal_user in PortalUser.objects.filter(
            role=PortalUser.ROLE_PARENT,
            is_active=True,
        ).order_by("id")
        if normalize_phone(portal_user.phone) == normalized_phone
    ]
    if not matched_parents:
        raise MiniappPhoneLoginError(
            error_code="phone_not_bound",
            message="手机号未绑定家长账号。",
            status=403,
        )
    if len(matched_parents) > 1:
        raise MiniappPhoneLoginError(
            error_code="phone_conflict",
            message="手机号绑定了多个家长账号。",
            status=409,
        )
    return matched_parents[0]


@csrf_exempt
def api_miniapp_login(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"error": "仅支持 POST 请求。"}, status=405)

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "请求体必须为 JSON。"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "请求体必须为 JSON 对象。"}, status=400)

    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password:
        return JsonResponse({"error": "username 和 password 不能为空。"}, status=400)

    portal_user = authenticate_portal_user(username, password)
    if portal_user is None:
        return JsonResponse({"error": "账号或密码错误。"}, status=401)

    if portal_user.role not in {PortalUser.ROLE_PARENT, PortalUser.ROLE_PRINCIPAL}:
        return JsonResponse({"error": "仅支持家长或校长账号登录。"}, status=403)

    return _build_miniapp_login_success_response(portal_user)


@csrf_exempt
def api_miniapp_login_by_phone(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"error": "仅支持 POST 请求。"}, status=405)

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "请求体必须为 JSON。"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "请求体必须为 JSON 对象。"}, status=400)

    code = str(payload.get("code") or "").strip()
    if not code:
        return JsonResponse({"error": "code 不能为空。", "error_code": "missing_code"}, status=400)

    try:
        phone = _fetch_wechat_miniapp_phone_number(code=code)
        portal_user = _resolve_parent_portal_user_by_phone(phone=phone)
    except MiniappPhoneLoginError as exc:
        return JsonResponse(
            {"error": exc.message, "error_code": exc.error_code},
            status=exc.status,
        )

    return _build_miniapp_login_success_response(portal_user)


def get_portal_user_from_request(request: HttpRequest) -> PortalUser:
    return PortalUser.objects.get(
        username=request.codemaster_user["username"],
        role=request.codemaster_user["role"],
        is_active=True,
    )


def build_knowledge_point_default_route_path(course_slug: str, category_slug: str, level_code: str, slug: str) -> str:
    return f"/student/{course_slug}/{category_slug}/{level_code.lower()}/{slug}"


def build_auto_course_content_slug(course: Course, level: CourseLevel, title: str) -> str:
    base_slug = slugify(title)
    if not base_slug:
        base_slug = f"{course.slug}-{level.code.lower()}-knowledge-point"
    candidate_slug = base_slug
    suffix = 2
    while CourseContent.objects.filter(slug=candidate_slug).exists():
        candidate_slug = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate_slug


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


def normalize_preserved_multiline_text(value: object) -> str:
    normalized = str(value or "")
    return normalized.replace("\r\n", "\n").replace("\r", "\n")


def parse_iso_date(value: object) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_homework_due_datetime_input(value: object) -> datetime | None:
    parsed_date = parse_iso_date(value)
    if parsed_date is None:
        return None
    return HomeworkAssignment.build_due_datetime_for_date(parsed_date)


def parse_datetime_local_input(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def save_exam_question_image(uploaded_file: UploadedFile, *, image_scope: str, slot_index: int) -> str:
    content_type = str(getattr(uploaded_file, "content_type", "") or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise ValidationError("考试题图片只支持图片文件。")
    suffix = Path(str(uploaded_file.name or "")).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        suffix = ".png"
    relative_path = (
        f"exam_question_images/{image_scope}/"
        f"question_{slot_index}_{uuid.uuid4().hex}{suffix}"
    )
    return default_storage.save(relative_path, uploaded_file)


def collect_exam_question_payloads(request: HttpRequest, *, image_scope: str, max_slots: int = 5) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for index in range(1, max_slots + 1):
        stem = normalize_preserved_multiline_text(request.POST.get(f"exam_question_{index}_stem", "")).strip()
        options = {
            key: request.POST.get(f"exam_question_{index}_option_{key.lower()}", "").strip()
            for key in ["A", "B", "C", "D"]
        }
        correct_answer = request.POST.get(f"exam_question_{index}_correct_answer", "").strip().upper()
        analysis = normalize_preserved_multiline_text(request.POST.get(f"exam_question_{index}_analysis", "")).strip()
        wrong_point_label = request.POST.get(f"exam_question_{index}_wrong_point_label", "").strip()
        score = request.POST.get(f"exam_question_{index}_score", "").strip() or "1"
        uploaded_image = request.FILES.get(f"exam_question_{index}_image")
        has_text_payload = bool(stem or any(options.values()) or correct_answer or analysis or wrong_point_label)
        if not has_text_payload and not uploaded_image:
            continue
        image_path = ""
        if uploaded_image:
            image_path = save_exam_question_image(uploaded_image, image_scope=image_scope, slot_index=index)
        payloads.append(
            {
                "stem": stem,
                "options": options,
                "correct_answer": correct_answer,
                "analysis": analysis,
                "wrong_point_label": wrong_point_label,
                "score": score,
                "image_path": image_path,
            }
        )
    return payloads


def select_primary_feedback_assignment(
    assignments: list[HomeworkAssignment],
) -> HomeworkAssignment | None:
    if not assignments:
        return None
    return sorted(
        assignments,
        key=lambda assignment: (
            1 if assignment.source_import_job_id else 0,
            assignment.due_date.isoformat() if assignment.due_date else "",
            assignment.assigned_at.isoformat() if assignment.assigned_at is not None else "",
            int(assignment.id or 0),
        ),
        reverse=True,
    )[0]


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


def infer_exam_pdf_metadata_from_filename(filename: str, *, level_code: str = "", course_title: str = "") -> dict[str, str]:
    stem = Path(filename or "").stem.strip()
    compact_stem = re.sub(r"\s+", "", stem)
    year_match = re.search(r"(20\d{2})\s*年", stem) or re.search(r"(20\d{2})", stem)
    month_match = re.search(r"年\s*(1[0-2]|0?[1-9])\s*月", compact_stem)
    if month_match is None:
        month_match = re.search(r"20\d{2}[_\-.年]*(1[0-2]|0?[1-9])", compact_stem)

    year = year_match.group(1) if year_match else ""
    month = str(int(month_match.group(1))) if month_match else ""
    normalized_level = str(level_code or "").strip().upper()
    normalized_course = str(course_title or "").strip().lower()
    lower_stem = compact_stem.lower()

    token = ""
    if "csp-j" in lower_stem or "cspj" in lower_stem or normalized_level == "CSP-J":
        token = "csp_j"
    elif "csp-s" in lower_stem or "csps" in lower_stem or normalized_level == "CSP-S":
        token = "csp_s"
    else:
        chinese_level_match = re.search(r"(\d+)\s*级", compact_stem)
        gesp_match = re.match(r"^GESP\s*(\d+)$", normalized_level)
        if ("c++" in lower_stem or "cpp" in lower_stem or normalized_course in {"c++", "cpp"}) and (
            chinese_level_match or gesp_match
        ):
            token = f"c_{(chinese_level_match or gesp_match).group(1)}"
        elif gesp_match:
            token = f"gesp_{gesp_match.group(1)}"

    if not token:
        token = re.sub(r"[^a-z0-9]+", "_", lower_stem).strip("_")
    source_pdf_id = "_".join(part for part in [year, month, token] if part)
    return {
        "year": year,
        "month": month,
        "source_pdf_id": source_pdf_id,
        "title": stem,
    }


def build_media_relative_url(relative_path: object) -> str:
    relative_path_text = str(relative_path or "").strip()
    if not relative_path_text:
        return ""
    try:
        return default_storage.url(relative_path_text)
    except Exception:
        media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
        return media_url.rstrip("/") + "/" + relative_path_text.lstrip("/")


def read_media_text_file(relative_path: object) -> str:
    relative_path_text = str(relative_path or "").strip()
    if not relative_path_text:
        return ""
    media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
    file_path = (media_root / relative_path_text).resolve(strict=False)
    try:
        file_path.relative_to(media_root)
    except ValueError:
        return ""
    if not file_path.exists() or not file_path.is_file():
        return ""
    return file_path.read_text(encoding="utf-8", errors="replace")


def build_exam_import_job_preview_rows(import_job: ExamQuestionBankImportJob) -> list[dict[str, object]]:
    rendered_pages = import_job.rendered_pages_json if isinstance(import_job.rendered_pages_json, list) else []
    raw_ocr_pages = import_job.raw_ocr_json if isinstance(import_job.raw_ocr_json, list) else []
    rendered_by_page_no: dict[int, dict] = {}
    raw_by_page_no: dict[int, dict] = {}

    for page in rendered_pages:
        if not isinstance(page, dict):
            continue
        page_no = normalize_positive_int(page.get("page_no"), default=0, minimum=1)
        if page_no:
            rendered_by_page_no[page_no] = page
    for page in raw_ocr_pages:
        if not isinstance(page, dict):
            continue
        page_no = normalize_positive_int(page.get("page_no"), default=0, minimum=1)
        if page_no:
            raw_by_page_no[page_no] = page

    page_numbers = sorted(set(rendered_by_page_no) | set(raw_by_page_no))
    if not page_numbers and import_job.page_count:
        page_numbers = list(range(1, import_job.page_count + 1))

    rows: list[dict[str, object]] = []
    for page_no in page_numbers:
        rendered_page = rendered_by_page_no.get(page_no, {})
        raw_page = raw_by_page_no.get(page_no, {})
        image_relative_path = str(rendered_page.get("relative_path") or "")
        markdown_relative_path = str(raw_page.get("markdown_relative_path") or "")
        raw_markdown_text = read_media_text_file(markdown_relative_path)
        markdown_text = clean_imported_markdown(raw_markdown_text) if raw_markdown_text else ""
        rows.append(
            {
                "page_no": page_no,
                "image_url": build_media_relative_url(image_relative_path),
                "image_relative_path": image_relative_path,
                "image_size_text": (
                    f"{rendered_page.get('width')} x {rendered_page.get('height')}"
                    if rendered_page.get("width") and rendered_page.get("height")
                    else ""
                ),
                "markdown_text": markdown_text,
                "markdown_relative_path": markdown_relative_path,
                "response_relative_path": str(raw_page.get("response_relative_path") or ""),
                "char_count": int(raw_page.get("char_count") or len(raw_markdown_text or "")),
            }
        )
    return rows


def get_scratch_docx_preview_section_label(question_type: object) -> str:
    normalized = str(question_type or "")
    if normalized == ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE:
        return "二、判断题"
    if normalized == ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING:
        return "三、编程题"
    return "一、单选题"


SCRATCH_DOCX_INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)]\(([^)]+)\)")


def render_scratch_docx_preview_markdown(value: object) -> str:
    markdown_text = str(value or "")
    if not SCRATCH_DOCX_INLINE_IMAGE_RE.search(markdown_text):
        return render_exam_markdown_for_display(markdown_text)

    parts: list[str] = []
    offset = 0
    for match in SCRATCH_DOCX_INLINE_IMAGE_RE.finditer(markdown_text):
        text_part = markdown_text[offset:match.start()]
        if text_part.strip():
            parts.append(str(render_exam_markdown_for_display(text_part)))
        image_path = str(match.group(2) or "").strip().strip('"').strip("'")
        image_url = image_path if image_path.startswith("/media/") else build_media_relative_url(image_path)
        image_alt = str(match.group(1) or "题目图片").strip() or "题目图片"
        parts.append(
            '<figure class="scratch-docx-inline-image">'
            f'<img src="{escape(image_url)}" alt="{escape(image_alt)}">'
            "</figure>"
        )
        offset = match.end()

    trailing_text = markdown_text[offset:]
    if trailing_text.strip():
        parts.append(str(render_exam_markdown_for_display(trailing_text)))
    return mark_safe("".join(parts))


def build_scratch_docx_import_question_preview(import_job: ExamQuestionBankImportJob) -> list[dict[str, object]]:
    if not is_scratch_docx_import_job(import_job):
        return []
    preview_rows = build_exam_import_job_preview_rows(import_job)
    combined_markdown = "\n\n".join(str(row.get("markdown_text") or "").strip() for row in preview_rows if str(row.get("markdown_text") or "").strip())
    if not combined_markdown:
        return []
    parsed_questions = split_ocr_markdown_into_question_blocks(combined_markdown, scratch_docx_mode=True)
    section_order = [
        ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
        ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
        ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
    ]
    sections_by_type: dict[str, dict[str, object]] = {}
    for question_type in section_order:
        sections_by_type[question_type] = {
            "question_type": question_type,
            "label": get_scratch_docx_preview_section_label(question_type),
            "question_count": 0,
            "total_score": "",
            "questions": [],
        }

    for parsed_question in parsed_questions:
        question_type = str(parsed_question.get("question_type") or ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN)
        section = sections_by_type.setdefault(
            question_type,
            {
                "question_type": question_type,
                "label": get_scratch_docx_preview_section_label(question_type),
                "question_count": 0,
                "total_score": "",
                "questions": [],
            },
        )
        answer_json = parsed_question.get("answer_json") if isinstance(parsed_question.get("answer_json"), dict) else {}
        answer_text = str(answer_json.get("correct_answer") or answer_json.get("answer") or "").strip()
        options = parsed_question.get("options") if isinstance(parsed_question.get("options"), dict) else {}
        stem_md = str(parsed_question.get("stem_md") or "")
        analysis_md = str(parsed_question.get("analysis_md") or "")
        question_preview = {
            "question_no": int(parsed_question.get("question_no") or 0),
            "anchor": f"scratch-docx-q-{int(parsed_question.get('question_no') or 0)}",
            "question_type": question_type,
            "question_type_text": get_exam_bank_question_type_review_label(question_type),
            "score": str(parsed_question.get("score") or ""),
            "answer_text": answer_text,
            "stem_html": render_scratch_docx_preview_markdown(stem_md),
            "analysis_html": render_scratch_docx_preview_markdown(analysis_md or "当前没有解析。"),
            "has_analysis": bool(analysis_md.strip()),
            "options": [
                {
                    "key": key,
                    "text_html": render_scratch_docx_preview_markdown(str(options.get(key) or "")) if str(options.get(key) or "").strip() else "",
                }
                for key in EXAM_IMPORT_MANUAL_CHOICE_KEYS
            ],
        }
        section["questions"].append(question_preview)
        section["question_count"] = int(section["question_count"] or 0) + 1
        if parsed_question.get("section_total_score"):
            section["total_score"] = str(parsed_question.get("section_total_score") or "")

    return [
        section
        for section in sections_by_type.values()
        if section["questions"]
    ]


EXAM_IMPORT_MANUAL_CHOICE_KEYS = ("A", "B", "C", "D")
PDF_CROP_DEMO_STATE_DIR = "exam_assets/demo_sessions"
PDF_CROP_DEMO_SUBJECT_CHOICES = ("cpp",)
PDF_CROP_DEMO_EXAM_TYPE_CHOICES = ("gesp1", "gesp2", "gesp3", "gesp4", "csp_j", "csp_s")
PDF_CROP_DEMO_QUESTION_TYPE_CHOICES = ("single_choice", "judgment", "programming")
PDF_CROP_DEMO_EXPECTED_QUESTION_COUNT = 27


def infer_pdf_crop_demo_metadata_from_filename(filename: str) -> dict[str, str]:
    stem = Path(filename or "").stem.strip()
    compact = re.sub(r"\s+", "", stem)
    lower_compact = compact.lower()
    year_match = re.search(r"(20\d{2})", compact)
    month_match = re.search(r"20\d{2}[_\-.年]*(1[0-2]|0?[1-9])", compact)
    if month_match is None:
        month_match = re.search(r"年\s*(1[0-2]|0?[1-9])\s*月", compact)

    subject = "cpp" if any(token in lower_compact for token in ("c++", "cpp", "c语言")) else "cpp"
    exam_type = ""
    if "csp-j" in lower_compact or "cspj" in lower_compact:
        exam_type = "csp_j"
    elif "csp-s" in lower_compact or "csps" in lower_compact:
        exam_type = "csp_s"
    else:
        gesp_match = re.search(r"gesp\s*([1-8])", lower_compact)
        chinese_level_match = re.search(r"([1-8])\s*级", compact)
        level_match = gesp_match or chinese_level_match
        if level_match:
            exam_type = f"gesp{level_match.group(1)}"

    return {
        "subject": subject,
        "exam_type": exam_type,
        "year": year_match.group(1) if year_match else "",
        "month": f"{int(month_match.group(1)):02d}" if month_match else "",
    }


def normalize_pdf_crop_demo_month(value: object) -> str:
    month = normalize_positive_int(value, default=0, minimum=1)
    if month < 1 or month > 12:
        return ""
    return f"{month:02d}"


def normalize_pdf_crop_demo_scale(value: object) -> int:
    scale = normalize_positive_int(value, default=2, minimum=2)
    return 3 if scale == 3 else 2


def get_pdf_crop_demo_state_path(session_id: str) -> str:
    safe_session_id = re.sub(r"[^0-9A-Za-z_-]", "", str(session_id or ""))
    return f"{PDF_CROP_DEMO_STATE_DIR}/{safe_session_id}.json"


def get_pdf_crop_demo_state(session_id: str) -> dict[str, object] | None:
    state_path = get_pdf_crop_demo_state_path(session_id)
    if not default_storage.exists(state_path):
        return None
    try:
        with default_storage.open(state_path, "r") as state_file:
            state = json.load(state_file)
    except Exception:
        return None
    return state if isinstance(state, dict) else None


def save_pdf_crop_demo_state(session_id: str, state: dict[str, object]) -> None:
    state_path = get_pdf_crop_demo_state_path(session_id)
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    if default_storage.exists(state_path):
        default_storage.delete(state_path)
    default_storage.save(state_path, ContentFile(payload.encode("utf-8")))


def get_pdf_crop_demo_question_type(question_no: int) -> str:
    if 1 <= question_no <= 15:
        return "single_choice"
    if 16 <= question_no <= 25:
        return "judgment"
    return "programming"


def get_pdf_crop_demo_section_no(question_no: int) -> int:
    if 1 <= question_no <= 15:
        return 1
    if 16 <= question_no <= 25:
        return 2
    return 3


def get_pdf_crop_demo_default_score(question_no: int) -> str:
    return "25" if question_no >= 26 else "2"


def get_csp_j_round1_question_score(question_no: int, question_type: object) -> str:
    if 1 <= question_no <= 15:
        return "2"
    if str(question_type or "") == "judgment":
        return "1.5"
    return "3"


def map_course_title_to_pdf_crop_demo_subject(course_title: object) -> str:
    normalized = str(course_title or "").strip().lower()
    if normalized in {"c++", "cpp"} or "c++" in normalized or "cpp" in normalized:
        return "cpp"
    return ""


def map_level_code_to_pdf_crop_demo_exam_type(level_code: object) -> str:
    value = str(level_code or "").strip().lower().replace("-", "_")
    if value.startswith("gesp") and value[4:].isdigit():
        exam_type = f"gesp{int(value[4:])}"
    elif value in {"csp_j", "cspj"}:
        exam_type = "csp_j"
    elif value in {"csp_s", "csps"}:
        exam_type = "csp_s"
    else:
        exam_type = ""
    return exam_type if exam_type in PDF_CROP_DEMO_EXAM_TYPE_CHOICES else ""


def should_use_csp_j_round1_crop_mode(*, subject: str, exam_type: str, title: object, filename: object) -> bool:
    text = unicodedata.normalize("NFKC", f"{title or ''} {filename or ''}")
    compact = re.sub(r"\s+", "", text).lower()
    round1_markers = (
        "初赛",
        "第一轮",
        "round1",
        "csp-j1",
        "csp_j1",
        "cspj1",
    )
    return subject == "cpp" and exam_type == "csp_j" and any(marker in compact for marker in round1_markers)


def extract_pdf_demo_text_lines(document: object) -> list[dict[str, object]]:
    lines: list[dict[str, object]] = []
    for page_index in range(document.page_count):
        page = document.load_page(page_index)
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                text = "".join(str(span.get("text") or "") for span in line.get("spans", [])).strip()
                if not text:
                    continue
                bbox = line.get("bbox") or [0, 0, 0, 0]
                lines.append(
                    {
                        "page_no": page_index + 1,
                        "text": text,
                        "x0": float(bbox[0]),
                        "y0": float(bbox[1]),
                        "x1": float(bbox[2]),
                        "y1": float(bbox[3]),
                    }
                )
    lines.sort(key=lambda item: (int(item["page_no"]), float(item["y0"]), float(item["x0"])))
    return lines


def build_pdf_demo_page_metrics(document: object, pages: list[dict[str, object]], lines: list[dict[str, object]]) -> dict[int, dict[str, float]]:
    line_map: dict[int, list[dict[str, object]]] = {}
    for line in lines:
        line_map.setdefault(int(line["page_no"]), []).append(line)
    page_by_no = {int(page["page_no"]): page for page in pages}
    metrics: dict[int, dict[str, float]] = {}
    for page_index in range(document.page_count):
        page_no = page_index + 1
        page = document.load_page(page_index)
        rendered_page = page_by_no.get(page_no, {})
        footer_lines = [
            line for line in line_map.get(page_no, [])
            if re.search(r"第\s*\d+\s*页\s*/\s*共\s*\d+\s*页", str(line.get("text") or ""))
        ]
        if footer_lines:
            bottom_pt = min(float(line["y0"]) for line in footer_lines) - 4
        else:
            bottom_pt = float(page.rect.height) - 22
        metrics[page_no] = {
            "width_pt": float(page.rect.width),
            "height_pt": float(page.rect.height),
            "width_px": float(rendered_page.get("width") or 0),
            "height_px": float(rendered_page.get("height") or 0),
            "top_pt": 28.0,
            "bottom_pt": max(60.0, bottom_pt),
        }
    return metrics


def parse_pdf_demo_local_question_no(text: object) -> int:
    raw_text = str(text or "").strip()
    if not raw_text:
        return 0
    normalized_text = unicodedata.normalize("NFKC", raw_text)
    compact = re.sub(r"\s+", "", normalized_text)
    if any(token in compact for token in ("题号", "答案")):
        return 0

    digit_question_match = re.search(r"第\s*(\d{1,2})\s*题", normalized_text)
    if digit_question_match:
        return int(digit_question_match.group(1))

    chinese_number_map = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "十一": 11,
        "十二": 12,
        "十三": 13,
        "十四": 14,
        "十五": 15,
    }
    chinese_question_match = re.search(r"第\s*([一二三四五六七八九十]{1,3})\s*题", normalized_text)
    if chinese_question_match:
        return chinese_number_map.get(chinese_question_match.group(1), 0)

    numbered_question_match = re.match(r"^\s*(\d{1,2})\s*[.、]\s*(?!\d)", normalized_text)
    if numbered_question_match:
        return int(numbered_question_match.group(1))
    return 0


def detect_pdf_demo_anchors(lines: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[int, str]]:
    anchors: list[dict[str, object]] = []
    boundaries: list[dict[str, object]] = []
    answer_sections: dict[str, list[dict[str, object]]] = {"single_choice": [], "judgment": []}
    current_section = ""
    first_question_seen: dict[str, bool] = {"single_choice": False, "judgment": False}

    for line in lines:
        text = str(line.get("text") or "").strip()
        section_changed = False
        if "单选题" in text:
            current_section = "single_choice"
            section_changed = True
        elif "判断题" in text:
            current_section = "judgment"
            section_changed = True
        elif "编程题" in text and "每题" in text:
            current_section = "programming"
            section_changed = True
        if section_changed and current_section in {"single_choice", "judgment", "programming"}:
            boundaries.append({**line, "boundary_type": current_section})
        if current_section == "programming" and ("参考程序" in text or "参考代码" in text):
            boundaries.append({**line, "boundary_type": f"{current_section}_reference"})

        if current_section in answer_sections and not first_question_seen[current_section]:
            answer_sections[current_section].append(line)

        local_question_no = parse_pdf_demo_local_question_no(text)
        if local_question_no and current_section in {"single_choice", "judgment"}:
            local_no = local_question_no
            if current_section == "single_choice" and 1 <= local_no <= 15:
                question_no = local_no
            elif current_section == "judgment" and 1 <= local_no <= 10:
                question_no = 15 + local_no
            else:
                question_no = 0
            if question_no:
                first_question_seen[current_section] = True
                anchors.append({**line, "question_no": question_no, "question_type": get_pdf_crop_demo_question_type(question_no)})
            continue

        normalized_programming_text = text.replace("．", ".")
        programming_match = (
            re.search(r"(?:^|\s)3\s*\.\s*([12])\s*编程题(?:\s*([12]))?", normalized_programming_text)
            or re.search(r"(?:^|\s)编程题\s*([12])(?:\s|$)", normalized_programming_text)
            or (
                re.search(r"(?:^|\s)3\s*\.\s*([12])\s*(?:\.\s*\d+)?\s*(?:题目描述|试题名称)", normalized_programming_text)
                if current_section == "programming"
                else None
            )
        )
        if programming_match:
            explicit_local_no = programming_match.group(2) if (programming_match.lastindex or 0) >= 2 else ""
            local_no = int(explicit_local_no or programming_match.group(1))
            question_no = 25 + local_no
            anchors.append({**line, "question_no": question_no, "question_type": "programming"})

    anchors_by_no: dict[int, dict[str, object]] = {}
    for anchor in anchors:
        question_no = int(anchor["question_no"])
        anchors_by_no.setdefault(question_no, anchor)
    ordered_anchors = sorted(anchors_by_no.values(), key=lambda item: int(item["question_no"]))
    answers = parse_pdf_demo_answers(answer_sections)
    return ordered_anchors, boundaries, answers


def parse_pdf_demo_answers(answer_sections: dict[str, list[dict[str, object]]]) -> dict[int, str]:
    answers: dict[int, str] = {}

    def extract_choice_answer_tokens(text: str) -> list[str]:
        normalized_text = unicodedata.normalize("NFKC", str(text or "")).upper()
        return re.findall(r"[A-D]", normalized_text)

    def extract_judgment_answer_tokens(text: str) -> list[str]:
        normalized_text = unicodedata.normalize("NFKC", str(text or "")).upper()
        raw_tokens = re.findall(r"正确|错误|√|×|✓|✕|(?<![A-Z0-9_])X(?![A-Z0-9_])|对|错", normalized_text)
        normalized_tokens = []
        for token in raw_tokens:
            if token in {"√", "✓", "正确", "对"}:
                normalized_tokens.append("√")
            elif token in {"×", "✕", "X", "错误", "错"}:
                normalized_tokens.append("×")
        return normalized_tokens

    def extract_answer_tokens(text: str, *, answer_kind: str) -> list[str]:
        if answer_kind == "judgment":
            return extract_judgment_answer_tokens(text)
        return extract_choice_answer_tokens(text)

    def extract_question_numbers(text: str, *, max_count: int) -> list[int]:
        normalized_text = unicodedata.normalize("NFKC", str(text or ""))
        numbers = []
        for raw_number in re.findall(r"\d{1,2}", normalized_text):
            number = int(raw_number)
            if 1 <= number <= max_count and number not in numbers:
                numbers.append(number)
        return numbers

    def parse_section_answer_map(section_lines: list[dict[str, object]], *, max_count: int, answer_kind: str) -> dict[int, str]:
        rows = [
            {
                **line,
                "text": str(line.get("text") or "").strip(),
                "x0": float(line.get("x0") or 0),
                "y0": float(line.get("y0") or 0),
                "y1": float(line.get("y1") or line.get("y0") or 0),
            }
            for line in section_lines
            if str(line.get("text") or "").strip()
        ]
        texts = [str(row["text"]) for row in rows]
        question_numbers: list[int] = []
        answer_tokens: list[str] = []
        question_row_seen = False
        answer_line_seen = False

        def row_center_y(row: dict[str, object]) -> float:
            return (float(row.get("y0") or 0) + float(row.get("y1") or row.get("y0") or 0)) / 2

        def is_same_text_row(row: dict[str, object], target_row: dict[str, object], *, tolerance: float) -> bool:
            return abs(row_center_y(row) - row_center_y(target_row)) <= tolerance

        def collect_by_geometry(label: str) -> list[str]:
            label_rows = [row for row in rows if label in re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(row["text"])))]
            if not label_rows:
                return []
            line_heights = [max(1.0, float(row["y1"]) - float(row["y0"])) for row in rows if float(row["y1"]) >= float(row["y0"])]
            tolerance = max(3.0, (sorted(line_heights)[len(line_heights) // 2] if line_heights else 8.0) * 0.8)
            tokens: list[str] = []
            for label_row in label_rows:
                same_row = sorted(
                    [row for row in rows if is_same_text_row(row, label_row, tolerance=tolerance)],
                    key=lambda row: float(row.get("x0") or 0),
                )
                for row in same_row:
                    text = str(row["text"])
                    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
                    if re.search(r"第\s*\d{1,2}\s*题", text):
                        continue
                    if label == "题号":
                        tokens.extend(str(number) for number in extract_question_numbers(text, max_count=max_count))
                    elif "答案" in compact:
                        after_answer = re.split(r"答案\s*[:：|]?", text, maxsplit=1)
                        tokens.extend(extract_answer_tokens(after_answer[1] if len(after_answer) > 1 else "", answer_kind=answer_kind))
                    else:
                        tokens.extend(extract_answer_tokens(text, answer_kind=answer_kind))
                if tokens:
                    return tokens
            return tokens

        geometry_question_numbers = [int(token) for token in collect_by_geometry("题号") if str(token).isdigit()]
        geometry_answer_tokens = collect_by_geometry("答案")
        if geometry_answer_tokens:
            mapped_answers: dict[int, str] = {}
            if geometry_question_numbers and len(geometry_question_numbers) == len(geometry_answer_tokens):
                for question_no, token in zip(geometry_question_numbers, geometry_answer_tokens, strict=False):
                    mapped_answers[question_no] = token
                return mapped_answers
            for index, token in enumerate(geometry_answer_tokens[:max_count], start=1):
                mapped_answers[index] = token
            return mapped_answers

        for text in texts:
            normalized_text = unicodedata.normalize("NFKC", text)
            compact = re.sub(r"\s+", "", normalized_text)
            if re.search(r"第\s*\d{1,2}\s*题", text):
                continue
            if "题号" in compact:
                question_row_seen = True
                question_numbers.extend(
                    number for number in extract_question_numbers(normalized_text, max_count=max_count) if number not in question_numbers
                )
                continue
            if "答案" in compact:
                answer_line_seen = True
                question_row_seen = False
                after_answer = re.split(r"答案\s*[:：|]?", text, maxsplit=1)
                answer_text = after_answer[1] if len(after_answer) > 1 else text
                answer_tokens.extend(extract_answer_tokens(answer_text, answer_kind=answer_kind))
                continue
            if question_row_seen and not answer_line_seen:
                question_numbers.extend(
                    number for number in extract_question_numbers(normalized_text, max_count=max_count) if number not in question_numbers
                )
                continue
            if answer_line_seen:
                # Some PDFs expose table cells as separate text lines. Once the
                # answer row has started, keep consuming answer-like tokens until
                # the first question anchor closes the answer section.
                answer_tokens.extend(extract_answer_tokens(normalized_text, answer_kind=answer_kind))

        if not answer_tokens:
            section_text = " ".join(texts)
            answer_match = re.search(r"答案\s*[:：|]?\s*(.+)", section_text)
            if answer_match:
                answer_tokens = extract_answer_tokens(answer_match.group(1), answer_kind=answer_kind)

        mapped_answers: dict[int, str] = {}
        if question_numbers and len(question_numbers) == len(answer_tokens):
            for question_no, token in zip(question_numbers, answer_tokens, strict=False):
                mapped_answers[question_no] = token
            return mapped_answers

        for index, token in enumerate(answer_tokens[:max_count], start=1):
            mapped_answers[index] = token
        return mapped_answers

    for local_no, token in parse_section_answer_map(answer_sections.get("single_choice", []), max_count=15, answer_kind="choice").items():
        answers[local_no] = token
    for local_no, token in parse_section_answer_map(answer_sections.get("judgment", []), max_count=10, answer_kind="judgment").items():
        answers[15 + local_no] = token
    return answers


def infer_pdf_demo_visual_judgment_answers(
    *,
    pages: list[dict[str, object]],
    lines: list[dict[str, object]],
    page_metrics: dict[int, dict[str, float]],
) -> dict[int, str]:
    from PIL import Image

    judgment_heading: dict[str, object] | None = None
    first_judgment_question: dict[str, object] | None = None
    for line in lines:
        text = str(line.get("text") or "").strip()
        if judgment_heading is None and "判断题" in text:
            judgment_heading = line
            continue
        if judgment_heading is not None and re.search(r"第\s*1\s*题", text):
            if (
                int(line.get("page_no") or 0) > int(judgment_heading.get("page_no") or 0)
                or float(line.get("y0") or 0) > float(judgment_heading.get("y0") or 0)
            ):
                first_judgment_question = line
                break
    if not judgment_heading or not first_judgment_question:
        return {}

    page_no = int(first_judgment_question.get("page_no") or judgment_heading.get("page_no") or 0)
    if page_no != int(judgment_heading.get("page_no") or 0):
        return {}
    page = next((item for item in pages if isinstance(item, dict) and int(item.get("page_no") or 0) == page_no), None)
    metric = page_metrics.get(page_no)
    if not page or not metric or not metric.get("width_px") or not metric.get("height_px"):
        return {}
    image_path = str(page.get("image_path") or "").strip()
    if not image_path or not default_storage.exists(image_path):
        return {}

    scale_x = float(metric["width_px"]) / max(1.0, float(metric["width_pt"]))
    scale_y = float(metric["height_px"]) / max(1.0, float(metric["height_pt"]))
    x1 = max(0, int(float(metric["width_px"]) * 0.12))
    x2 = min(int(metric["width_px"]), int(float(metric["width_px"]) * 0.92))
    y1 = max(0, int((float(judgment_heading.get("y1") or judgment_heading.get("y0") or 0) + 2) * scale_y))
    y2 = min(int(metric["height_px"]), int((float(first_judgment_question.get("y0") or 0) - 2) * scale_y))
    if y2 <= y1 + 8:
        return {}

    try:
        with default_storage.open(image_path, "rb") as image_file:
            image = Image.open(image_file).convert("RGB")
            red_points: list[tuple[int, int]] = []
            for y in range(y1, y2):
                for x in range(x1, x2):
                    red, green, blue = image.getpixel((x, y))
                    if red > 175 and green < 165 and blue < 165 and red > green * 1.25 and red > blue * 1.25:
                        red_points.append((x, y))
    except Exception:
        return {}
    if not red_points:
        return {}

    x_values = sorted({x for x, _ in red_points})
    x_groups: list[list[int]] = []
    max_gap = max(4, int(5 * scale_x))
    for x in x_values:
        if not x_groups or x > x_groups[-1][1] + max_gap:
            x_groups.append([x, x])
        else:
            x_groups[-1][1] = x

    components: list[dict[str, int]] = []
    for group_x1, group_x2 in x_groups:
        points = [(x, y) for x, y in red_points if group_x1 <= x <= group_x2]
        if not points:
            continue
        component = {
            "x1": min(x for x, _ in points),
            "y1": min(y for _, y in points),
            "x2": max(x for x, _ in points),
            "y2": max(y for _, y in points),
            "count": len(points),
        }
        width = component["x2"] - component["x1"] + 1
        height = component["y2"] - component["y1"] + 1
        if component["count"] >= 5 and width >= 3 and height >= 3:
            components.append(component)

    if len(components) < 10:
        return {}
    components = sorted(components, key=lambda item: (item["x1"], -item["count"]))[:10]
    heights = sorted(component["y2"] - component["y1"] + 1 for component in components)
    median_height = heights[len(heights) // 2] if heights else 0
    answers: dict[int, str] = {}
    for index, component in enumerate(components, start=16):
        width = component["x2"] - component["x1"] + 1
        height = component["y2"] - component["y1"] + 1
        answers[index] = "×" if median_height and height <= median_height * 0.75 and width <= median_height * 0.9 else "√"
    return answers


def build_pdf_demo_source_regions(
    *,
    anchors: list[dict[str, object]],
    boundaries: list[dict[str, object]],
    page_metrics: dict[int, dict[str, float]],
) -> dict[int, list[dict[str, object]]]:
    cut_points = sorted(
        [*anchors, *boundaries],
        key=lambda item: (int(item["page_no"]), float(item["y0"]), float(item["x0"])),
    )
    regions_by_question: dict[int, list[dict[str, object]]] = {}
    max_page_no = max(page_metrics) if page_metrics else 0

    for anchor in anchors:
        question_no = int(anchor["question_no"])
        anchor_position = (int(anchor["page_no"]), float(anchor["y0"]), float(anchor["x0"]))
        next_cut = next(
            (
                item for item in cut_points
                if (int(item["page_no"]), float(item["y0"]), float(item["x0"])) > anchor_position
            ),
            None,
        )
        start_page = int(anchor["page_no"])
        end_page = int(next_cut["page_no"]) if next_cut else max_page_no
        question_regions: list[dict[str, object]] = []
        for page_no in range(start_page, end_page + 1):
            metric = page_metrics.get(page_no)
            if not metric or not metric["width_px"] or not metric["height_px"]:
                continue
            y1_pt = max(metric["top_pt"], float(anchor["y0"]) - 6) if page_no == start_page else metric["top_pt"]
            if next_cut and page_no == int(next_cut["page_no"]):
                y2_pt = min(metric["bottom_pt"], float(next_cut["y0"]) - 6)
            else:
                y2_pt = metric["bottom_pt"]
            if y2_pt <= y1_pt + 8:
                continue
            scale_x = metric["width_px"] / metric["width_pt"]
            scale_y = metric["height_px"] / metric["height_pt"]
            x1_px = int(24 * scale_x)
            x2_px = int((metric["width_pt"] - 24) * scale_x)
            y1_px = int(y1_pt * scale_y)
            y2_px = int(y2_pt * scale_y)
            question_regions.append(
                {
                    "page_no": page_no,
                    "bbox": [
                        max(0, min(int(metric["width_px"]), x1_px)),
                        max(0, min(int(metric["height_px"]), y1_px)),
                        max(0, min(int(metric["width_px"]), x2_px)),
                        max(0, min(int(metric["height_px"]), y2_px)),
                    ],
                }
            )
        regions_by_question[question_no] = question_regions
    return regions_by_question


def build_pdf_demo_fallback_regions(pages: list[dict[str, object]]) -> dict[int, list[dict[str, object]]]:
    if not pages:
        return {}
    regions: dict[int, list[dict[str, object]]] = {}
    questions_per_page = max(1, (PDF_CROP_DEMO_EXPECTED_QUESTION_COUNT + len(pages) - 1) // len(pages))
    question_no = 1
    for page in pages:
        if question_no > PDF_CROP_DEMO_EXPECTED_QUESTION_COUNT:
            break
        width = int(page.get("width") or 0)
        height = int(page.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        top = max(40, int(height * 0.04))
        bottom = max(top + 100, int(height * 0.96))
        page_question_count = min(questions_per_page, PDF_CROP_DEMO_EXPECTED_QUESTION_COUNT - question_no + 1)
        block = (bottom - top) / max(page_question_count, 1)
        for index in range(page_question_count):
            y1 = int(top + block * index)
            y2 = int(min(bottom, y1 + block * 0.92))
            regions[question_no] = [
                {
                    "page_no": int(page["page_no"]),
                    "bbox": [int(width * 0.04), y1, int(width * 0.96), y2],
                }
            ]
            question_no += 1
    return regions


def build_pdf_demo_section_fallback_regions(
    *,
    boundaries: list[dict[str, object]],
    page_metrics: dict[int, dict[str, float]],
    answers: dict[int, str],
) -> dict[int, list[dict[str, object]]]:
    section_specs = [
        ("single_choice", 1, 15),
        ("judgment", 16, 25),
        ("programming", 26, 27),
    ]
    boundaries_by_type: dict[str, list[dict[str, object]]] = {}
    for boundary in boundaries:
        boundary_type = str(boundary.get("boundary_type") or "")
        boundaries_by_type.setdefault(boundary_type, []).append(boundary)

    section_boundaries: list[dict[str, object]] = []
    for boundary_type, _, _ in section_specs:
        candidates = boundaries_by_type.get(boundary_type) or []
        if candidates:
            section_boundaries.append(min(candidates, key=lambda item: (int(item["page_no"]), float(item["y0"]), float(item["x0"]))))
    if len(section_boundaries) < 2:
        return {}
    section_boundaries.sort(key=lambda item: (int(item["page_no"]), float(item["y0"]), float(item["x0"])))

    regions: dict[int, list[dict[str, object]]] = {}
    for section_type, start_question_no, end_question_no in section_specs:
        start_candidates = boundaries_by_type.get(section_type) or []
        if not start_candidates:
            continue
        start_boundary = min(start_candidates, key=lambda item: (int(item["page_no"]), float(item["y0"]), float(item["x0"])))
        next_section = next(
            (
                boundary
                for boundary in section_boundaries
                if (int(boundary["page_no"]), float(boundary["y0"]), float(boundary["x0"]))
                > (int(start_boundary["page_no"]), float(start_boundary["y0"]), float(start_boundary["x0"]))
            ),
            None,
        )
        reference_boundary = next(
            (
                boundary
                for boundary in sorted(
                    boundaries_by_type.get(f"{section_type}_reference") or [],
                    key=lambda item: (int(item["page_no"]), float(item["y0"]), float(item["x0"])),
                )
                if (int(boundary["page_no"]), float(boundary["y0"]), float(boundary["x0"]))
                > (int(start_boundary["page_no"]), float(start_boundary["y0"]), float(start_boundary["x0"]))
            ),
            None,
        )
        end_boundary = min(
            [boundary for boundary in (next_section, reference_boundary) if boundary],
            key=lambda item: (int(item["page_no"]), float(item["y0"]), float(item["x0"])),
            default=None,
        )
        if not end_boundary and section_type != "programming":
            continue

        question_numbers = list(range(start_question_no, end_question_no + 1))
        if section_type == "single_choice" and not any(question_no in answers for question_no in question_numbers):
            continue
        if section_type == "judgment" and not any(question_no in answers for question_no in question_numbers):
            continue

        spans: list[dict[str, float]] = []
        start_page = int(start_boundary["page_no"])
        end_page = int(end_boundary["page_no"]) if end_boundary else max(page_metrics)
        for page_no in range(start_page, end_page + 1):
            metric = page_metrics.get(page_no)
            if not metric or not metric["width_px"] or not metric["height_px"]:
                continue
            top_pt = metric["top_pt"]
            bottom_pt = metric["bottom_pt"]
            if page_no == start_page:
                heading_padding = 44.0 if section_type in {"single_choice", "judgment"} else 8.0
                top_pt = max(top_pt, float(start_boundary.get("y1") or start_boundary.get("y0") or 0) + heading_padding)
            if end_boundary and page_no == int(end_boundary["page_no"]):
                bottom_pt = min(bottom_pt, float(end_boundary.get("y0") or 0) - 6)
            if bottom_pt <= top_pt + 8:
                continue
            spans.append({"page_no": float(page_no), "top_pt": top_pt, "bottom_pt": bottom_pt, "height_pt": bottom_pt - top_pt})
        total_height = sum(span["height_pt"] for span in spans)
        if total_height <= 0:
            continue

        block_height = total_height / len(question_numbers)
        for index, question_no in enumerate(question_numbers):
            segment_start = block_height * index
            segment_end = block_height * (index + 0.92)
            consumed = 0.0
            question_regions: list[dict[str, object]] = []
            for span in spans:
                span_start = consumed
                span_end = consumed + span["height_pt"]
                overlap_start = max(segment_start, span_start)
                overlap_end = min(segment_end, span_end)
                consumed = span_end
                if overlap_end <= overlap_start + 4:
                    continue
                page_no = int(span["page_no"])
                metric = page_metrics.get(page_no)
                if not metric:
                    continue
                local_y1_pt = span["top_pt"] + (overlap_start - span_start)
                local_y2_pt = span["top_pt"] + (overlap_end - span_start)
                scale_x = metric["width_px"] / metric["width_pt"]
                scale_y = metric["height_px"] / metric["height_pt"]
                question_regions.append(
                    {
                        "page_no": page_no,
                        "bbox": [
                            max(0, min(int(metric["width_px"]), int(24 * scale_x))),
                            max(0, min(int(metric["height_px"]), int(local_y1_pt * scale_y))),
                            max(0, min(int(metric["width_px"]), int((metric["width_pt"] - 24) * scale_x))),
                            max(0, min(int(metric["height_px"]), int(local_y2_pt * scale_y))),
                        ],
                    }
                )
            if question_regions:
                regions[question_no] = question_regions
    return regions


def is_pdf_demo_csp_j_material_anchor(text: object) -> bool:
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    return bool(re.match(r"^[（(]\s*\d{1,2}\s*[）)]", normalized))


def detect_csp_j_round1_anchors(lines: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    question_anchors: list[dict[str, object]] = []
    material_anchors: list[dict[str, object]] = []
    boundaries: list[dict[str, object]] = []
    current_section = ""
    material_group_no = 0

    for line in lines:
        text = str(line.get("text") or "").strip()
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
        if "单项选择题" in compact or "单项选择" in compact:
            current_section = "single_choice"
            boundaries.append({**line, "boundary_type": current_section})
            continue
        if "阅读程序" in compact:
            current_section = "reading"
            boundaries.append({**line, "boundary_type": current_section})
            continue
        if "完善程序" in compact:
            current_section = "completion"
            boundaries.append({**line, "boundary_type": current_section})
            continue
        if current_section in {"reading", "completion"} and is_pdf_demo_csp_j_material_anchor(text):
            material_group_no += 1
            material_anchors.append(
                {
                    **line,
                    "material_group_no": material_group_no,
                    "section_type": current_section,
                }
            )
            continue

        question_no = parse_pdf_demo_local_question_no(text)
        if not question_no:
            continue
        if current_section == "single_choice" and 1 <= question_no <= 15:
            section_no = 1
        elif current_section == "reading" and question_no >= 16:
            section_no = 2
        elif current_section == "completion" and question_no >= 16:
            section_no = 3
        else:
            continue
        question_anchors.append(
            {
                **line,
                "question_no": question_no,
                "section_no": section_no,
                "section_type": current_section,
            }
        )

    anchors_by_no: dict[int, dict[str, object]] = {}
    for anchor in question_anchors:
        anchors_by_no.setdefault(int(anchor["question_no"]), anchor)
    ordered_questions = sorted(anchors_by_no.values(), key=lambda item: int(item["question_no"]))
    material_anchors.sort(key=lambda item: (int(item["page_no"]), float(item["y0"]), float(item["x0"])))
    boundaries.sort(key=lambda item: (int(item["page_no"]), float(item["y0"]), float(item["x0"])))
    return ordered_questions, material_anchors, boundaries


def pdf_demo_position(item: dict[str, object]) -> tuple[int, float, float]:
    return int(item.get("page_no") or 0), float(item.get("y0") or 0), float(item.get("x0") or 0)


def convert_pdf_demo_pt_span_to_regions(
    *,
    start_item: dict[str, object],
    end_item: dict[str, object] | None,
    page_metrics: dict[int, dict[str, float]],
    top_padding: float = 4.0,
    bottom_padding: float = 6.0,
) -> list[dict[str, object]]:
    if not page_metrics:
        return []
    start_page = int(start_item.get("page_no") or 0)
    end_page = int(end_item.get("page_no") or max(page_metrics)) if end_item else max(page_metrics)
    regions: list[dict[str, object]] = []
    for page_no in range(start_page, end_page + 1):
        metric = page_metrics.get(page_no)
        if not metric or not metric["width_px"] or not metric["height_px"]:
            continue
        y1_pt = metric["top_pt"]
        y2_pt = metric["bottom_pt"]
        if page_no == start_page:
            y1_pt = max(metric["top_pt"], float(start_item.get("y0") or 0) - top_padding)
        if end_item and page_no == int(end_item.get("page_no") or 0):
            y2_pt = min(metric["bottom_pt"], float(end_item.get("y0") or 0) - bottom_padding)
        if y2_pt <= y1_pt + 8:
            continue
        scale_x = metric["width_px"] / metric["width_pt"]
        scale_y = metric["height_px"] / metric["height_pt"]
        regions.append(
            {
                "page_no": page_no,
                "bbox": [
                    max(0, min(int(metric["width_px"]), int(24 * scale_x))),
                    max(0, min(int(metric["height_px"]), int(y1_pt * scale_y))),
                    max(0, min(int(metric["width_px"]), int((metric["width_pt"] - 24) * scale_x))),
                    max(0, min(int(metric["height_px"]), int(y2_pt * scale_y))),
                ],
            }
        )
    return regions


def infer_csp_j_round1_question_type(
    *,
    question_anchor: dict[str, object],
    next_cut: dict[str, object] | None,
    lines: list[dict[str, object]],
) -> str:
    section_type = str(question_anchor.get("section_type") or "")
    if section_type in {"single_choice", "completion"}:
        return "single_choice"
    start_position = pdf_demo_position(question_anchor)
    end_position = pdf_demo_position(next_cut) if next_cut else (10**6, 10**6, 10**6)
    section_lines = [
        line
        for line in lines
        if start_position < pdf_demo_position(line) < end_position
    ]
    for line in section_lines:
        text = unicodedata.normalize("NFKC", str(line.get("text") or "")).strip()
        if re.match(r"^[A-D][.．、]", text):
            return "single_choice"
    return "judgment"


def build_csp_j_round1_auto_crops(
    *,
    state: dict[str, object],
    document: object,
    pages: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    lines = extract_pdf_demo_text_lines(document)
    page_metrics = build_pdf_demo_page_metrics(document, pages, lines)
    question_anchors, material_anchors, boundaries = detect_csp_j_round1_anchors(lines)
    if not question_anchors:
        return [], {
            "crop_mode": "csp_j_round1",
            "anchor_count": 0,
            "material_count": 0,
            "crop_count": 0,
            "fallback_used": True,
            "requires_manual_crop": True,
        }

    cuts = sorted([*question_anchors, *material_anchors, *boundaries], key=pdf_demo_position)
    material_by_group: dict[int, dict[str, object]] = {}
    for index, material_anchor in enumerate(material_anchors):
        group_no = int(material_anchor.get("material_group_no") or 0)
        next_question = next((anchor for anchor in question_anchors if pdf_demo_position(anchor) > pdf_demo_position(material_anchor)), None)
        next_material = next((anchor for anchor in material_anchors if pdf_demo_position(anchor) > pdf_demo_position(material_anchor)), None)
        end_item = min(
            [item for item in (next_question, next_material) if item],
            key=pdf_demo_position,
            default=None,
        )
        regions = convert_pdf_demo_pt_span_to_regions(start_item=material_anchor, end_item=end_item, page_metrics=page_metrics)
        records: list[dict[str, object]] = []
        for part_index, region in enumerate(regions, start=1):
            bbox = region.get("bbox") if isinstance(region, dict) else None
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            try:
                crop_result = crop_pdf_demo_material_image(
                    state=state,
                    page_no=int(region.get("page_no") or 0),
                    crop_box=[float(value) for value in bbox],
                    material_group_no=group_no,
                    part_no=part_index,
                )
            except ValidationError:
                continue
            records.append(
                {
                    "material_group_no": group_no,
                    "part_no": part_index,
                    "page_no": crop_result["page_no"],
                    "crop_box": crop_result["crop_box"],
                    "image_path": crop_result["image_path"],
                    "image_url": crop_result["image_url"],
                }
            )
        material_by_group[group_no] = {
            "material_group_no": group_no,
            "section_type": str(material_anchor.get("section_type") or ""),
            "records": records,
            "anchor": material_anchor,
        }

    crops: list[dict[str, object]] = []
    for question_anchor in question_anchors:
        question_no = int(question_anchor["question_no"])
        next_cut = next((item for item in cuts if pdf_demo_position(item) > pdf_demo_position(question_anchor)), None)
        regions = convert_pdf_demo_pt_span_to_regions(start_item=question_anchor, end_item=next_cut, page_metrics=page_metrics)
        question_type = infer_csp_j_round1_question_type(question_anchor=question_anchor, next_cut=next_cut, lines=lines)
        material_anchor = next(
            (
                anchor for anchor in reversed(material_anchors)
                if pdf_demo_position(anchor) < pdf_demo_position(question_anchor)
                and str(anchor.get("section_type") or "") == str(question_anchor.get("section_type") or "")
            ),
            None,
        )
        material_group_no = int(material_anchor.get("material_group_no") or 0) if material_anchor else 0
        material_records = material_by_group.get(material_group_no, {}).get("records") if material_group_no else []
        material_records = material_records if isinstance(material_records, list) else []
        material_image_paths = [str(record.get("image_path") or "") for record in material_records if isinstance(record, dict) and record.get("image_path")]
        material_image_urls = [str(record.get("image_url") or "") for record in material_records if isinstance(record, dict) and record.get("image_url")]
        for part_index, region in enumerate(regions, start=1):
            bbox = region.get("bbox") if isinstance(region, dict) else None
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            try:
                crop_result = crop_pdf_demo_question_image(
                    state=state,
                    page_no=int(region.get("page_no") or 0),
                    crop_box=[float(value) for value in bbox],
                    section_no=int(question_anchor.get("section_no") or 1),
                    question_no=question_no,
                    part_no=part_index,
                )
            except ValidationError:
                continue
            crops.append(
                {
                    "subject": state.get("subject"),
                    "exam_type": state.get("exam_type"),
                    "year": state.get("year"),
                    "month": state.get("month"),
                    "section_no": int(question_anchor.get("section_no") or 1),
                    "question_no": question_no,
                    "part_no": part_index,
                    "question_type": question_type,
                    "answer": "",
                    "score": get_csp_j_round1_question_score(question_no, question_type),
                    "page_no": crop_result["page_no"],
                    "crop_box": crop_result["crop_box"],
                    "image_path": crop_result["image_path"],
                    "image_url": crop_result["image_url"],
                    "display_mode": "grouped_material" if material_group_no else "single",
                    "material_group_no": material_group_no,
                    "material_image_paths": material_image_paths,
                    "material_image_urls": material_image_urls,
                    "needs_review": False,
                }
            )
    summary = {
        "crop_mode": "csp_j_round1",
        "anchor_count": len(question_anchors),
        "material_count": len(material_by_group),
        "crop_count": len(crops),
        "fallback_used": False,
        "requires_manual_crop": False,
    }
    return crops, summary


def build_pdf_demo_auto_crops(
    *,
    state: dict[str, object],
    document: object,
    pages: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if str(state.get("crop_mode") or "") == "csp_j_round1":
        return build_csp_j_round1_auto_crops(state=state, document=document, pages=pages)

    lines = extract_pdf_demo_text_lines(document)
    page_metrics = build_pdf_demo_page_metrics(document, pages, lines)
    anchors, boundaries, answers = detect_pdf_demo_anchors(lines)
    visual_judgment_answers = infer_pdf_demo_visual_judgment_answers(pages=pages, lines=lines, page_metrics=page_metrics)
    for question_no, answer in visual_judgment_answers.items():
        answers.setdefault(question_no, answer)
    fallback_used = not anchors or len(anchors) < max(3, PDF_CROP_DEMO_EXPECTED_QUESTION_COUNT // 2)
    regions_by_question = (
        build_pdf_demo_section_fallback_regions(boundaries=boundaries, page_metrics=page_metrics, answers=answers)
        if fallback_used
        else build_pdf_demo_source_regions(anchors=anchors, boundaries=boundaries, page_metrics=page_metrics)
    )
    crops: list[dict[str, object]] = []
    for question_no in sorted(regions_by_question):
        question_type = get_pdf_crop_demo_question_type(question_no)
        for part_index, region in enumerate(regions_by_question[question_no], start=1):
            bbox = region.get("bbox") if isinstance(region, dict) else None
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            try:
                crop_result = crop_pdf_demo_question_image(
                    state=state,
                    page_no=int(region.get("page_no") or 0),
                    crop_box=[float(value) for value in bbox],
                    section_no=get_pdf_crop_demo_section_no(question_no),
                    question_no=question_no,
                    part_no=part_index,
                )
            except ValidationError:
                continue
            crops.append(
                {
                    "subject": state.get("subject"),
                    "exam_type": state.get("exam_type"),
                    "year": state.get("year"),
                    "month": state.get("month"),
                    "section_no": get_pdf_crop_demo_section_no(question_no),
                    "question_no": question_no,
                    "part_no": part_index,
                    "question_type": question_type,
                    "answer": answers.get(question_no, ""),
                    "score": get_pdf_crop_demo_default_score(question_no),
                    "page_no": crop_result["page_no"],
                    "crop_box": crop_result["crop_box"],
                    "image_path": crop_result["image_path"],
                    "image_url": crop_result["image_url"],
                    "needs_review": fallback_used,
                }
            )
    summary = {
        "anchor_count": len(anchors),
        "boundary_count": len(boundaries),
        "answer_count": len(answers),
        "visual_judgment_answer_count": len(visual_judgment_answers),
        "crop_count": len(crops),
        "fallback_used": fallback_used,
        "requires_manual_crop": fallback_used and not crops,
    }
    return crops, summary


def render_pdf_crop_demo_pages(
    *,
    uploaded_file: UploadedFile,
    session_id: str,
    scale: int,
    subject: str,
    exam_type: str,
    year: str,
    month: str,
    crop_mode: str = "",
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise ValidationError("PDF 截图切题 Demo 需要安装 PyMuPDF：pip install PyMuPDF Pillow。") from exc

    try:
        pdf_bytes = uploaded_file.read()
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValidationError("PDF 渲染失败，请确认上传的是有效 PDF 文件。") from exc

    pages: list[dict[str, object]] = []
    try:
        if document.page_count <= 0:
            raise ValidationError("PDF 中没有可渲染页面。")
        matrix = fitz.Matrix(scale, scale)
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            relative_path = f"exam_assets/demo_pages/{session_id}/page_{index + 1:03d}.png"
            if default_storage.exists(relative_path):
                default_storage.delete(relative_path)
            saved_path = default_storage.save(relative_path, ContentFile(pixmap.tobytes("png")))
            pages.append(
                {
                    "page_no": index + 1,
                    "image_path": saved_path,
                    "image_url": build_media_relative_url(saved_path),
                    "width": int(pixmap.width),
                    "height": int(pixmap.height),
                }
            )
        state_stub = {
            "subject": subject,
            "exam_type": exam_type,
            "year": year,
            "month": month,
            "pages": pages,
            "crop_mode": crop_mode,
        }
        crops, summary = build_pdf_demo_auto_crops(state=state_stub, document=document, pages=pages)
    finally:
        document.close()
    return pages, crops, summary


def create_pdf_crop_demo_session_from_upload(
    *,
    uploaded_file: UploadedFile,
    subject: str,
    exam_type: str,
    year: int,
    month: str,
    scale: int,
    crop_mode: str = "",
) -> str:
    session_id = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    pages, crops, auto_summary = render_pdf_crop_demo_pages(
        uploaded_file=uploaded_file,
        session_id=session_id,
        scale=scale,
        subject=subject,
        exam_type=exam_type,
        year=str(year),
        month=month,
        crop_mode=crop_mode,
    )
    state = {
        "session_id": session_id,
        "subject": subject,
        "exam_type": exam_type,
        "year": str(year),
        "month": month,
        "scale": scale,
        "crop_mode": crop_mode,
        "source_filename": uploaded_file.name,
        "pages": pages,
        "crops": crops,
        "auto_summary": auto_summary,
        "created_at": timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_pdf_crop_demo_state(session_id, state)
    return session_id


def parse_pdf_crop_demo_number(value: object, *, field_label: str) -> float:
    try:
        return float(str(value or "").strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_label} 坐标无效，请重新框选。") from exc


def crop_pdf_demo_question_image(
    *,
    state: dict[str, object],
    page_no: int,
    crop_box: list[float],
    section_no: int,
    question_no: int,
    part_no: int,
) -> dict[str, object]:
    from PIL import Image

    pages = state.get("pages") if isinstance(state.get("pages"), list) else []
    page = next((item for item in pages if isinstance(item, dict) and int(item.get("page_no") or 0) == page_no), None)
    if not page:
        raise ValidationError("未找到当前 PDF 页面，请重新上传 PDF。")
    image_path = str(page.get("image_path") or "").strip()
    if not image_path:
        raise ValidationError("当前页面图路径为空，请重新上传 PDF。")

    subject = str(state.get("subject") or "").strip()
    exam_type = str(state.get("exam_type") or "").strip()
    year = str(state.get("year") or "").strip()
    month = str(state.get("month") or "").strip()
    output_dir = f"exam_assets/demo_questions/{subject}/{exam_type}/{year}_{month}"
    output_name = f"{subject}_{exam_type}_{year}_{month}_s{section_no:02d}_q{question_no:03d}_p{part_no:02d}.png"
    output_path = f"{output_dir}/{output_name}"

    try:
        with default_storage.open(image_path, "rb") as image_file:
            image = Image.open(image_file)
            image.load()
    except Exception as exc:
        raise ValidationError("读取 PDF 页面图失败，请重新上传 PDF。") from exc

    width, height = image.size
    x1, y1, x2, y2 = crop_box
    left = max(0, min(width, int(round(min(x1, x2)))))
    right = max(0, min(width, int(round(max(x1, x2)))))
    top = max(0, min(height, int(round(min(y1, y2)))))
    bottom = max(0, min(height, int(round(max(y1, y2)))))
    if right - left < 8 or bottom - top < 8:
        raise ValidationError("框选区域太小，请重新框选题目区域。")

    cropped = image.crop((left, top, right, bottom))
    buffer = BytesIO()
    cropped.save(buffer, format="PNG")
    if default_storage.exists(output_path):
        default_storage.delete(output_path)
    saved_path = default_storage.save(output_path, ContentFile(buffer.getvalue()))
    return {
        "page_no": page_no,
        "crop_box": [left, top, right, bottom],
        "image_path": saved_path,
        "image_url": build_media_relative_url(saved_path),
    }


def crop_pdf_demo_material_image(
    *,
    state: dict[str, object],
    page_no: int,
    crop_box: list[float],
    material_group_no: int,
    part_no: int,
) -> dict[str, object]:
    from PIL import Image

    pages = state.get("pages") if isinstance(state.get("pages"), list) else []
    page = next((item for item in pages if isinstance(item, dict) and int(item.get("page_no") or 0) == page_no), None)
    if not page:
        raise ValidationError("未找到当前 PDF 页面，请重新上传 PDF。")
    image_path = str(page.get("image_path") or "").strip()
    if not image_path:
        raise ValidationError("当前页面图路径为空，请重新上传 PDF。")

    subject = str(state.get("subject") or "").strip()
    exam_type = str(state.get("exam_type") or "").strip()
    year = str(state.get("year") or "").strip()
    month = str(state.get("month") or "").strip()
    output_dir = f"exam_assets/demo_questions/{subject}/{exam_type}/{year}_{month}"
    output_name = f"{subject}_{exam_type}_{year}_{month}_g{material_group_no:03d}_material_p{part_no:02d}.png"
    output_path = f"{output_dir}/{output_name}"

    try:
        with default_storage.open(image_path, "rb") as image_file:
            image = Image.open(image_file)
            image.load()
    except Exception as exc:
        raise ValidationError("读取 PDF 页面图失败，请重新上传 PDF。") from exc

    width, height = image.size
    x1, y1, x2, y2 = crop_box
    left = max(0, min(width, int(round(min(x1, x2)))))
    right = max(0, min(width, int(round(max(x1, x2)))))
    top = max(0, min(height, int(round(min(y1, y2)))))
    bottom = max(0, min(height, int(round(max(y1, y2)))))
    if right - left < 8 or bottom - top < 8:
        raise ValidationError("公共材料区域太小，请重新框选。")

    cropped = image.crop((left, top, right, bottom))
    buffer = BytesIO()
    cropped.save(buffer, format="PNG")
    if default_storage.exists(output_path):
        default_storage.delete(output_path)
    saved_path = default_storage.save(output_path, ContentFile(buffer.getvalue()))
    return {
        "page_no": page_no,
        "crop_box": [left, top, right, bottom],
        "image_path": saved_path,
        "image_url": build_media_relative_url(saved_path),
    }


def build_pdf_demo_crop_question_groups(crops: list[object]) -> list[dict[str, object]]:
    groups_by_question: dict[int, dict[str, object]] = {}
    for crop in crops:
        if not isinstance(crop, dict):
            continue
        question_no = normalize_positive_int(crop.get("question_no"), default=0, minimum=1)
        if not question_no:
            continue
        group = groups_by_question.setdefault(
            question_no,
            {
                "section_no": normalize_positive_int(crop.get("section_no"), default=get_pdf_crop_demo_section_no(question_no), minimum=1),
                "question_no": question_no,
                "question_type": str(crop.get("question_type") or get_pdf_crop_demo_question_type(question_no)),
                "answer": str(crop.get("answer") or ""),
                "score": str(crop.get("score") or get_pdf_crop_demo_default_score(question_no)),
                "display_mode": str(crop.get("display_mode") or ""),
                "material_group_no": normalize_positive_int(crop.get("material_group_no"), default=0, minimum=1),
                "material_image_paths": crop.get("material_image_paths") if isinstance(crop.get("material_image_paths"), list) else [],
                "material_image_urls": crop.get("material_image_urls") if isinstance(crop.get("material_image_urls"), list) else [],
                "records": [],
                "next_part_no": 1,
            },
        )
        if not group.get("material_image_paths") and isinstance(crop.get("material_image_paths"), list):
            group["material_image_paths"] = crop.get("material_image_paths")
        if not group.get("material_image_urls") and isinstance(crop.get("material_image_urls"), list):
            group["material_image_urls"] = crop.get("material_image_urls")
        group_records = group["records"] if isinstance(group["records"], list) else []
        group_records.append(crop)
        group["records"] = group_records
        part_no = normalize_positive_int(crop.get("part_no"), default=0, minimum=1)
        group["next_part_no"] = max(int(group.get("next_part_no") or 1), part_no + 1)

    groups = sorted(groups_by_question.values(), key=lambda item: (int(item["section_no"]), int(item["question_no"])))
    for group in groups:
        records = group["records"] if isinstance(group["records"], list) else []
        records.sort(key=lambda item: normalize_positive_int(item.get("part_no") if isinstance(item, dict) else 0, default=1, minimum=1))
    return groups


def normalize_pdf_demo_quick_answer(*, question_no: int, answer: object) -> str:
    value = unicodedata.normalize("NFKC", str(answer or "")).strip().upper()
    if not value:
        return ""
    if 1 <= question_no <= 15:
        return value if value in {"A", "B", "C", "D"} else value
    if 16 <= question_no <= 25:
        if value in {"√", "✓", "对", "正确", "TRUE", "T"}:
            return "√"
        if value in {"×", "✕", "X", "错", "错误", "FALSE", "F"}:
            return "×"
    return value


def build_pdf_demo_quick_answer_cells(crops: list[object]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    answers_by_question: dict[int, str] = {}
    for crop in crops:
        if not isinstance(crop, dict):
            continue
        question_no = normalize_positive_int(crop.get("question_no"), default=0, minimum=1)
        if not question_no or question_no in answers_by_question:
            continue
        answers_by_question[question_no] = str(crop.get("answer") or "")
    choice_cells = [
        {
            "question_no": question_no,
            "label": str(question_no),
            "field_name": f"quick_answer_{question_no}",
            "value": answers_by_question.get(question_no, ""),
        }
        for question_no in range(1, 16)
    ]
    judgment_cells = [
        {
            "question_no": question_no,
            "label": str(question_no - 15),
            "field_name": f"quick_answer_{question_no}",
            "value": answers_by_question.get(question_no, ""),
        }
        for question_no in range(16, 26)
    ]
    return choice_cells, judgment_cells


def map_pdf_crop_demo_exam_type_to_level(exam_type: object) -> str:
    value = str(exam_type or "").strip().lower()
    if value.startswith("gesp") and value[4:].isdigit():
        return f"GESP{int(value[4:])}"
    if value == "csp_j":
        return "CSP-J"
    if value == "csp_s":
        return "CSP-S"
    return value.upper() or "GESP1"


def map_pdf_crop_demo_question_type_to_bank(question_type: object) -> str:
    value = str(question_type or "").strip()
    if value == "judgment":
        return ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE
    if value == "programming":
        return ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING
    return ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE


def get_pdf_demo_image_dimensions(relative_path: object) -> tuple[int | None, int | None]:
    from PIL import Image

    path = str(relative_path or "").strip()
    if not path:
        return None, None
    try:
        with default_storage.open(path, "rb") as image_file:
            image = Image.open(image_file)
            image.load()
            return int(image.width), int(image.height)
    except Exception:
        return None, None


@transaction.atomic
def confirm_pdf_crop_demo_to_bank_paper(*, state: dict[str, object], teacher: PortalUser) -> ExamQuestionBankPaper:
    crops = state.get("crops") if isinstance(state.get("crops"), list) else []
    groups = build_pdf_demo_crop_question_groups(crops)
    if not groups:
        raise ValidationError("当前预览没有可确认的题目截图。")

    subject = str(state.get("subject") or "cpp").strip()
    exam_type = str(state.get("exam_type") or "gesp1").strip()
    year = normalize_positive_int(state.get("year"), default=0, minimum=2000)
    month = normalize_positive_int(state.get("month"), default=0, minimum=1)
    if not year or month < 1 or month > 12:
        raise ValidationError("试卷年份或月份无效，请重新上传。")

    level = map_pdf_crop_demo_exam_type_to_level(exam_type)
    session_id = str(state.get("session_id") or uuid.uuid4().hex[:12]).strip()
    source_pdf_id = f"pdf_crop_{subject}_{exam_type}_{year}_{month:02d}_{session_id}"
    title = f"{level} {year}年{month:02d}月截图试卷"
    source_filename = str(state.get("source_filename") or "").strip()

    paper, _ = ExamQuestionBankPaper.objects.update_or_create(
        source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
        source_pdf_id=source_pdf_id,
        defaults={
            "level": level,
            "year": year,
            "month": month,
            "source_file": source_filename,
            "title": title,
            "import_batch_uid": session_id,
            "is_active": True,
        },
    )

    for group in groups:
        question_no = int(group["question_no"])
        question_type = map_pdf_crop_demo_question_type_to_bank(group.get("question_type"))
        answer = str(group.get("answer") or "").strip()
        score = str(group.get("score") or get_pdf_crop_demo_default_score(question_no)).strip()
        records = [record for record in group.get("records", []) if isinstance(record, dict) and record.get("image_path")]
        material_image_paths = [
            str(path or "").strip()
            for path in (group.get("material_image_paths") if isinstance(group.get("material_image_paths"), list) else [])
            if str(path or "").strip()
        ]
        question_image_paths = [str(record.get("image_path") or "").strip() for record in records if str(record.get("image_path") or "").strip()]
        image_paths = [*material_image_paths, *question_image_paths]
        question_uid = f"{source_pdf_id}_q{question_no:03d}"
        bank_question, _ = ExamQuestionBankQuestion.objects.update_or_create(
            paper=paper,
            question_no=question_no,
            defaults={
                "question_uid": question_uid,
                "question_type": question_type,
                "stem_md": f"第 {question_no} 题（见截图）",
                "answer_json": {"correct_answer": answer} if answer else {},
                "analysis_md": "",
                "programming_json": {},
                "full_json": {
                    "source": "pdf_crop_demo",
                    "subject": subject,
                    "exam_type": exam_type,
                    "session_id": session_id,
                    "score": score,
                    "image_paths": image_paths,
                    "question_image_paths": question_image_paths,
                    "material_group_no": int(group.get("material_group_no") or 0),
                    "material_image_paths": material_image_paths,
                    "display_mode": str(group.get("display_mode") or ""),
                    "crop_records": records,
                    "confirmed_by": teacher.username,
                },
            },
        )
        if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE:
            for index, option_key in enumerate(("A", "B", "C", "D"), start=1):
                ExamQuestionBankOption.objects.update_or_create(
                    question=bank_question,
                    option_key=option_key,
                    defaults={"option_text_md": option_key, "sort_order": index},
                )
        elif question_type == ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE:
            ExamQuestionBankOption.objects.update_or_create(
                question=bank_question,
                option_key="A",
                defaults={"option_text_md": "正确", "sort_order": 1},
            )
            ExamQuestionBankOption.objects.update_or_create(
                question=bank_question,
                option_key="B",
                defaults={"option_text_md": "错误", "sort_order": 2},
            )

        existing_paths = set()
        for material_index, relative_path in enumerate(material_image_paths, start=1):
            if not relative_path:
                continue
            existing_paths.add(relative_path)
            width, height = get_pdf_demo_image_dimensions(relative_path)
            ExamQuestionBankAsset.objects.update_or_create(
                question=bank_question,
                asset_role="content",
                relative_path=relative_path,
                defaults={
                    "asset_uid": f"{question_uid}_material_p{material_index:02d}",
                    "asset_type": "material_crop",
                    "public_url": "",
                    "alt": f"第 {question_no} 题公共材料 {material_index}",
                    "width": width,
                    "height": height,
                },
            )
        for record in records:
            relative_path = str(record.get("image_path") or "").strip()
            if not relative_path:
                continue
            existing_paths.add(relative_path)
            part_no = normalize_positive_int(record.get("part_no"), default=1, minimum=1)
            width, height = get_pdf_demo_image_dimensions(relative_path)
            ExamQuestionBankAsset.objects.update_or_create(
                question=bank_question,
                asset_role="content",
                relative_path=relative_path,
                defaults={
                    "asset_uid": f"{question_uid}_p{part_no:02d}",
                    "asset_type": "question_crop",
                    "public_url": "",
                    "alt": f"第 {question_no} 题截图 {part_no}",
                    "width": width,
                    "height": height,
                },
            )
        bank_question.assets.filter(asset_role="content").exclude(relative_path__in=existing_paths).delete()

    paper.questions.exclude(question_no__in=[int(group["question_no"]) for group in groups]).delete()
    return paper


@role_required("teacher")
def teacher_exam_pdf_crop_demo_upload(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect(f"{reverse('teacher-exam-paper-new')}#exam-paper-upload")

    scale = normalize_pdf_crop_demo_scale(request.POST.get("demo_scale"))
    pdf_file = request.FILES.get("demo_pdf")
    inferred_metadata = infer_pdf_crop_demo_metadata_from_filename(pdf_file.name if pdf_file else "")
    subject = str(request.POST.get("demo_subject") or inferred_metadata.get("subject") or "").strip()
    exam_type = str(request.POST.get("demo_exam_type") or inferred_metadata.get("exam_type") or "").strip()
    year = normalize_positive_int(request.POST.get("demo_year") or inferred_metadata.get("year"), default=0, minimum=2000)
    month = normalize_pdf_crop_demo_month(request.POST.get("demo_month") or inferred_metadata.get("month"))

    def redirect_with_error(message: str) -> HttpResponse:
        return redirect(
            build_redirect_with_query(
                reverse("teacher-exam-paper-new"),
                params={"op": "pdf_crop_demo_error", "message": message},
                anchor="exam-paper-upload",
            )
        )

    if subject not in PDF_CROP_DEMO_SUBJECT_CHOICES:
        return redirect_with_error("PDF 截图切题 Demo 当前只支持 cpp。")
    if exam_type not in PDF_CROP_DEMO_EXAM_TYPE_CHOICES:
        return redirect_with_error("请选择有效的考试类型。")
    if not year:
        return redirect_with_error("请填写有效年份。")
    if not month:
        return redirect_with_error("请填写有效月份。")
    if not pdf_file:
        return redirect_with_error("请先选择 PDF 文件。")
    if not str(pdf_file.name or "").lower().endswith(".pdf"):
        return redirect_with_error("PDF 截图切题 Demo 只接受 PDF 文件。")

    try:
        session_id = create_pdf_crop_demo_session_from_upload(
            uploaded_file=pdf_file,
            subject=subject,
            exam_type=exam_type,
            year=year,
            month=month,
            scale=scale,
        )
    except ValidationError as exc:
        message = "；".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return redirect_with_error(message or "PDF 渲染失败。")

    return redirect(reverse("teacher-exam-pdf-crop-demo", args=[session_id]))


@role_required("teacher")
def teacher_exam_pdf_crop_demo(request: HttpRequest, session_id: str) -> HttpResponse:
    state = get_pdf_crop_demo_state(session_id)
    if state is None:
        raise Http404("未找到 PDF 截图切题 Demo 会话")

    error_message = ""
    success_message = ""
    if request.method == "POST":
        form_action = str(request.POST.get("form_action") or "add_pdf_crop_demo_part").strip()
        if form_action == "update_pdf_crop_demo_answers":
            crops = state.get("crops") if isinstance(state.get("crops"), list) else []
            updated_questions: set[int] = set()
            for record in crops:
                if not isinstance(record, dict):
                    continue
                question_no = normalize_positive_int(record.get("question_no"), default=0, minimum=1)
                if not question_no:
                    continue
                answer_key = f"answer_{question_no}"
                score_key = f"score_{question_no}"
                question_type_key = f"question_type_{question_no}"
                answer = str(request.POST.get(answer_key) if answer_key in request.POST else record.get("answer") or "").strip()
                score = str(request.POST.get(score_key) if score_key in request.POST else record.get("score") or "").strip()
                question_type = str(
                    request.POST.get(question_type_key) if question_type_key in request.POST else record.get("question_type") or ""
                ).strip()
                if question_type and question_type not in PDF_CROP_DEMO_QUESTION_TYPE_CHOICES:
                    error_message = "请选择有效题型。"
                    break
                record["answer"] = answer
                record["score"] = score
                if question_type:
                    record["question_type"] = question_type
                updated_questions.add(question_no)
            if not error_message:
                state["crops"] = crops
                save_pdf_crop_demo_state(session_id, state)
                success_message = f"已更新 {len(updated_questions)} 道题的答案/题型/分值。"
        elif form_action == "bulk_update_pdf_crop_demo_answers":
            crops = state.get("crops") if isinstance(state.get("crops"), list) else []
            quick_answers: dict[int, str] = {}
            for question_no in range(1, 26):
                field_name = f"quick_answer_{question_no}"
                if field_name not in request.POST:
                    continue
                answer = normalize_pdf_demo_quick_answer(question_no=question_no, answer=request.POST.get(field_name))
                if answer:
                    quick_answers[question_no] = answer
            updated_questions: set[int] = set()
            for record in crops:
                if not isinstance(record, dict):
                    continue
                question_no = normalize_positive_int(record.get("question_no"), default=0, minimum=1)
                if question_no in quick_answers:
                    record["answer"] = quick_answers[question_no]
                    updated_questions.add(question_no)
            state["crops"] = crops
            save_pdf_crop_demo_state(session_id, state)
            success_message = f"已快速填入 {len(updated_questions)} 道客观题答案。"
        elif form_action == "confirm_pdf_crop_demo_to_bank":
            try:
                paper = confirm_pdf_crop_demo_to_bank_paper(
                    state=state,
                    teacher=get_portal_user_from_request(request),
                )
            except ValidationError as exc:
                error_message = "；".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            else:
                state["confirmed_bank_paper_id"] = paper.id
                state["confirmed_at"] = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S")
                save_pdf_crop_demo_state(session_id, state)
                return redirect(
                    build_redirect_with_query(
                        reverse("teacher-exam-paper-new"),
                        params={"op": "pdf_crop_demo_confirmed", "paper_id": paper.id},
                        anchor="confirmed-bank-papers",
                    )
                )
        else:
            page_no = normalize_positive_int(request.POST.get("page_no"), default=0, minimum=1)
            section_no = normalize_positive_int(request.POST.get("section_no"), default=1, minimum=1)
            question_no = normalize_positive_int(request.POST.get("question_no"), default=1, minimum=1)
            part_no = normalize_positive_int(request.POST.get("part_no"), default=1, minimum=1)
            question_type = str(request.POST.get("question_type") or "").strip()
            answer = str(request.POST.get("answer") or "").strip()
            score = str(request.POST.get("score") or "").strip()
            try:
                if question_type not in PDF_CROP_DEMO_QUESTION_TYPE_CHOICES:
                    raise ValidationError("请选择有效题型。")
                crop_box = [
                    parse_pdf_crop_demo_number(request.POST.get("x1"), field_label="x1"),
                    parse_pdf_crop_demo_number(request.POST.get("y1"), field_label="y1"),
                    parse_pdf_crop_demo_number(request.POST.get("x2"), field_label="x2"),
                    parse_pdf_crop_demo_number(request.POST.get("y2"), field_label="y2"),
                ]
                crop_result = crop_pdf_demo_question_image(
                    state=state,
                    page_no=page_no,
                    crop_box=crop_box,
                    section_no=section_no,
                    question_no=question_no,
                    part_no=part_no,
                )
            except ValidationError as exc:
                error_message = "；".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            else:
                record = {
                    "subject": state.get("subject"),
                    "exam_type": state.get("exam_type"),
                    "year": state.get("year"),
                    "month": state.get("month"),
                    "section_no": section_no,
                    "question_no": question_no,
                    "part_no": part_no,
                    "question_type": question_type,
                    "answer": answer,
                    "score": score,
                    "page_no": crop_result["page_no"],
                    "crop_box": crop_result["crop_box"],
                    "image_path": crop_result["image_path"],
                    "image_url": crop_result["image_url"],
                }
                crops = state.get("crops") if isinstance(state.get("crops"), list) else []
                replaced_existing = False
                for index, existing_record in enumerate(crops):
                    if not isinstance(existing_record, dict):
                        continue
                    if (
                        normalize_positive_int(existing_record.get("question_no"), default=0, minimum=1) == question_no
                        and normalize_positive_int(existing_record.get("part_no"), default=0, minimum=1) == part_no
                    ):
                        crops[index] = record
                        replaced_existing = True
                        break
                if not replaced_existing:
                    crops.append(record)
                state["crops"] = crops
                save_pdf_crop_demo_state(session_id, state)
                action_text = "已替换" if replaced_existing else "已生成"
                success_message = f"{action_text}第 {section_no} 大题第 {question_no} 题第 {part_no} 张截图。"

    pages = state.get("pages") if isinstance(state.get("pages"), list) else []
    crops = state.get("crops") if isinstance(state.get("crops"), list) else []
    quick_choice_answer_cells, quick_judgment_answer_cells = build_pdf_demo_quick_answer_cells(crops)
    context = {
        "page_title": "PDF 截图切题 Demo",
        "page_description": "手动框选 PDF 页面区域，生成题目截图并预览考试展示效果。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "新增试卷", "href": reverse("teacher-exam-paper-new")},
            {"label": "PDF 截图切题 Demo"},
        ],
        "session_id": session_id,
        "demo_state": state,
        "pages": pages,
        "crops": crops,
        "crop_question_groups": build_pdf_demo_crop_question_groups(crops),
        "quick_choice_answer_cells": quick_choice_answer_cells,
        "quick_judgment_answer_cells": quick_judgment_answer_cells,
        "question_type_options": PDF_CROP_DEMO_QUESTION_TYPE_CHOICES,
        "error_message": error_message,
        "success_message": success_message,
        "back_href": f"{reverse('teacher-exam-paper-new')}#exam-paper-upload",
    }
    return render_shell_page(request, "teacher", "entry/teacher_exam_pdf_crop_demo.html", context)


def is_manual_choice_review_import(import_job: ExamQuestionBankImportJob) -> bool:
    source_type = detect_exam_import_source_type(import_job.source_filename or import_job.source_pdf.name)
    return source_type == "image"


def collect_manual_choice_review_payloads(request: HttpRequest, *, required: bool) -> dict[int, dict[str, object]]:
    page_numbers = normalize_positive_int_list(request.POST.getlist("manual_choice_page_no"))
    if required and not page_numbers:
        raise ExamPaperImportConfirmError("图片题请先进入预览页，补充正确答案后再确认入库。")
    payloads: dict[int, dict[str, object]] = {}
    missing_answer_pages: list[int] = []
    for page_no in page_numbers:
        correct_answer = str(request.POST.get(f"manual_choice_correct_answer_{page_no}") or "").strip().upper()
        analysis_md = normalize_preserved_multiline_text(
            request.POST.get(f"manual_choice_analysis_{page_no}") or ""
        ).strip()
        options = {
            key: str(request.POST.get(f"manual_choice_option_{page_no}_{key.lower()}") or "").strip()
            or f"选项 {key}（见题图）"
            for key in EXAM_IMPORT_MANUAL_CHOICE_KEYS
        }
        if correct_answer and correct_answer not in EXAM_IMPORT_MANUAL_CHOICE_KEYS:
            missing_answer_pages.append(page_no)
            continue
        if required and not correct_answer:
            missing_answer_pages.append(page_no)
        payloads[page_no] = {
            "correct_answer": correct_answer,
            "analysis_md": analysis_md,
            "options": options,
        }
    if missing_answer_pages:
        joined_pages = "、".join(str(page_no) for page_no in missing_answer_pages)
        raise ExamPaperImportConfirmError(f"图片题第 {joined_pages} 页请先选择正确答案。")
    return payloads


def serialize_exam_import_job_row(item: ExamQuestionBankImportJob) -> dict[str, object]:
    can_confirm = item.status == ExamQuestionBankImportJob.STATUS_OCR_DONE
    is_imported = item.status == ExamQuestionBankImportJob.STATUS_IMPORTED
    source_type = detect_exam_import_source_type(item.source_filename or item.source_pdf.name)
    is_text_source_import = bool(source_type and source_type not in {"pdf", "image"})
    status_text = item.get_status_display()
    if is_text_source_import and item.status == ExamQuestionBankImportJob.STATUS_OCR_DONE:
        status_text = "文本转换完成"
    elif is_text_source_import and item.status == ExamQuestionBankImportJob.STATUS_RENDERING:
        status_text = "文本转换中"
    return {
        "id": item.id,
        "title": item.title,
        "course_title": item.course.title if item.course_id and item.course else "未绑定学科",
        "level_code": item.level_code,
        "source_filename": item.source_filename,
        "source_pdf_id": item.source_pdf_id,
        "source_type": source_type,
        "is_text_source_import": is_text_source_import,
        "status_text": status_text,
        "status": item.status,
        "status_notes": item.status_notes,
        "error_message": item.error_message,
        "page_count": item.page_count,
        "rendered_page_count": item.rendered_page_count,
        "ocr_page_count": item.ocr_page_count,
        "workspace_relative_path": item.workspace_relative_path,
        "created_at": timezone.localtime(item.created_at).strftime("%Y-%m-%d %H:%M"),
        "updated_at": timezone.localtime(item.updated_at).strftime("%Y-%m-%d %H:%M:%S"),
        "preview_href": reverse("teacher-exam-paper-import-job-detail", args=[item.id]),
        "confirm_action": reverse("teacher-exam-paper-new"),
        "can_confirm": can_confirm,
        "confirm_label": "已确认" if is_imported else "确认",
        "confirm_disabled_reason": ""
        if can_confirm
        else "已入库" if is_imported else "处理完成后可确认入库",
        "status_tone": (
            "success"
            if item.status in {ExamQuestionBankImportJob.STATUS_OCR_DONE, ExamQuestionBankImportJob.STATUS_IMPORTED}
            else "danger"
            if item.status == ExamQuestionBankImportJob.STATUS_FAILED
            else "trial"
        ),
    }


def get_teacher_exam_import_job_rows(portal_user: PortalUser, *, limit: int = 10) -> list[dict[str, object]]:
    recent_import_jobs = list(
        ExamQuestionBankImportJob.objects.select_related("course")
        .filter(teacher=portal_user, is_active=True)
        .order_by("-created_at", "-id")[:limit]
    )
    return [serialize_exam_import_job_row(item) for item in recent_import_jobs]


def serialize_confirmed_exam_bank_paper_row(
    paper: ExamQuestionBankPaper,
    *,
    published_bank_paper_ids: set[int] | None = None,
) -> dict[str, object]:
    subject_title = infer_exam_bank_paper_subject(paper)
    source_text = dict(ExamQuestionBankPaper.SOURCE_CHOICES).get(paper.source, paper.source or "系统")
    question_count = int(getattr(paper, "question_count", 0) or 0)
    knowledge_done_count = 0
    knowledge_questions = list(paper.questions.all())
    knowledge_total_count = len(knowledge_questions) or question_count
    knowledge_running_count = 0
    knowledge_failed_count = 0
    for question in knowledge_questions:
        full_json = question.full_json if isinstance(question.full_json, dict) else {}
        if str(full_json.get("knowledge_level_1") or "").strip() and str(full_json.get("knowledge_level_2") or "").strip():
            knowledge_done_count += 1
        knowledge_status = str(full_json.get("knowledge_status") or "").strip()
        if knowledge_status in {"pending", "running"}:
            knowledge_running_count += 1
        elif knowledge_status == "failed":
            knowledge_failed_count += 1
    if knowledge_total_count <= 0:
        knowledge_status_text = "无题目"
        knowledge_status_tone = "trial"
    elif knowledge_running_count > 0:
        knowledge_status_text = f"识别中 {knowledge_done_count}/{knowledge_total_count}"
        knowledge_status_tone = "trial"
    elif knowledge_done_count <= 0:
        knowledge_status_text = "识别失败" if knowledge_failed_count else "未识别"
        knowledge_status_tone = "trial"
    elif knowledge_done_count >= knowledge_total_count:
        knowledge_status_text = "识别完成"
        knowledge_status_tone = "open"
    else:
        knowledge_status_text = f"部分识别 {knowledge_done_count}/{knowledge_total_count}"
        knowledge_status_tone = "trial"
    year_month_text = f"{paper.year}年{paper.month}月" if paper.year and paper.month else "未设置"
    created_at_text = timezone.localtime(paper.created_at).strftime("%Y-%m-%d %H:%M") if paper.created_at else ""
    updated_at_text = timezone.localtime(paper.updated_at).strftime("%Y-%m-%d %H:%M") if paper.updated_at else ""
    has_exam_management_record = (
        paper.id in published_bank_paper_ids
        if published_bank_paper_ids is not None
        else exam_bank_paper_has_exam_management_record(paper.id)
    )
    return {
        "id": paper.id,
        "title": paper.title,
        "subject_title": subject_title,
        "level_text": paper.level or "未分级",
        "year_month_text": year_month_text,
        "source_text": source_text,
        "source_pdf_id": paper.source_pdf_id,
        "source_file": paper.source_file,
        "question_count": question_count,
        "created_at_text": created_at_text,
        "updated_at_text": updated_at_text,
        "edit_href": reverse("teacher-exam-bank-paper-edit", args=[paper.id]),
        "knowledge_status_text": knowledge_status_text,
        "knowledge_status_tone": knowledge_status_tone,
        "knowledge_total_count": knowledge_total_count,
        "knowledge_done_count": knowledge_done_count,
        "knowledge_running_count": knowledge_running_count,
        "knowledge_failed_count": knowledge_failed_count,
        "can_generate_knowledge": knowledge_running_count == 0 and knowledge_done_count < knowledge_total_count,
        "publish_href": f"{reverse('teacher-exams')}#available-exam-papers",
        "has_exam_management_record": has_exam_management_record,
        "delete_label": "删除" if has_exam_management_record else "硬删除",
        "delete_confirm_message": (
            "这张试卷已经发布过，删除后会进入已删除试卷，可恢复。是否继续？"
            if has_exam_management_record
            else "这张试卷还没有发布过，将硬删除题库快照和对应识别任务，之后可重新上传识别。是否继续？"
        ),
        "search_text": " ".join(
            [
                paper.title,
                subject_title,
                paper.level or "",
                year_month_text,
                source_text,
                paper.source_file or "",
                paper.source_pdf_id or "",
            ]
        ).lower(),
    }


def get_confirmed_exam_bank_paper_rows(*, limit: int = 100) -> list[dict[str, object]]:
    published_bank_paper_ids = get_exam_bank_paper_ids_with_exam_management_records()
    papers = list(
        ExamQuestionBankPaper.objects.filter(is_active=True)
        .annotate(question_count=Count("questions"))
        .order_by("-updated_at", "-created_at", "-id")[:limit]
    )
    return [
        serialize_confirmed_exam_bank_paper_row(paper, published_bank_paper_ids=published_bank_paper_ids)
        for paper in papers
    ]


def get_deleted_exam_bank_paper_rows(*, limit: int = 100) -> list[dict[str, object]]:
    published_bank_paper_ids = get_exam_bank_paper_ids_with_exam_management_records()
    papers = list(
        ExamQuestionBankPaper.objects.filter(is_active=False, id__in=published_bank_paper_ids)
        .annotate(question_count=Count("questions"))
        .order_by("-updated_at", "-created_at", "-id")[:limit]
    )
    return [
        serialize_confirmed_exam_bank_paper_row(paper, published_bank_paper_ids=published_bank_paper_ids)
        for paper in papers
    ]


def get_exam_bank_question_type_review_label(question_type: object) -> str:
    normalized_type = str(question_type or "")
    if normalized_type == ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN:
        return "待复核题"
    return dict(ExamQuestionBankQuestion.QUESTION_TYPE_CHOICES).get(normalized_type, normalized_type)


def serialize_exam_bank_paper_question_for_review(
    question: ExamQuestionBankQuestion,
    *,
    include_assets: bool = True,
) -> dict[str, object]:
    options = list(question.options.all().order_by("sort_order", "option_key"))
    assets = list(question.assets.filter(asset_role="content").order_by("id")) if include_assets else []
    answer_json = question.answer_json if isinstance(question.answer_json, dict) else {}
    answer_text = (
        answer_json.get("correct_answer")
        or answer_json.get("answer")
        or answer_json.get("value")
        or ""
    )
    if not answer_text and answer_json and set(answer_json) - {"source", "needs_teacher_review"}:
        answer_text = json.dumps(answer_json, ensure_ascii=False)
    option_map = {option.option_key.upper(): option.option_text_md for option in options}
    full_json = question.full_json if isinstance(question.full_json, dict) else {}
    return {
        "id": question.id,
        "question_uid": question.question_uid,
        "question_no": question.question_no,
        "question_type": question.question_type,
        "question_type_text": get_exam_bank_question_type_review_label(question.question_type),
        "stem_md": question.stem_md,
        "stem_md_for_edit": format_exam_markdown_for_teacher_edit(question.stem_md),
        "stem_html": render_exam_markdown_for_display(question.stem_md),
        "answer_text": str(answer_text or ""),
        "analysis_md": question.analysis_md,
        "analysis_html": render_exam_markdown_for_display(question.analysis_md or "当前没有解析。"),
        "has_analysis": bool(str(question.analysis_md or "").strip()),
        "knowledge_level_1": str(full_json.get("knowledge_level_1") or ""),
        "knowledge_level_2": str(full_json.get("knowledge_level_2") or ""),
        "knowledge_level_3": str(full_json.get("knowledge_level_3") or ""),
        "can_generate_analysis": question.question_type in {
            ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
            ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
        },
        "programming_json_text": json.dumps(question.programming_json, ensure_ascii=False, indent=2)
        if isinstance(question.programming_json, (dict, list))
        else str(question.programming_json or ""),
        "full_json_text": json.dumps(question.full_json, ensure_ascii=False, indent=2)
        if isinstance(question.full_json, (dict, list))
        else str(question.full_json or ""),
        "options": [
            {
                "key": key,
                "text": option_map.get(key, ""),
                "text_html": render_exam_markdown_for_display(option_map.get(key, "")),
            }
            for key in ["A", "B", "C", "D"]
        ],
        "assets": [
            {
                "relative_path": asset.relative_path,
                "url": build_media_relative_url(asset.relative_path),
                "alt": asset.alt or f"第 {question.question_no} 题图片",
                "asset_type": asset.asset_type,
            }
            for asset in assets
        ],
    }


def serialize_parsed_ocr_question_for_review(
    *,
    source_question: ExamQuestionBankQuestion,
    parsed_question: dict[str, object],
) -> dict[str, object]:
    answer_json = parsed_question.get("answer_json") if isinstance(parsed_question.get("answer_json"), dict) else {}
    answer_text = str(answer_json.get("correct_answer") or answer_json.get("answer") or "")
    option_map = parsed_question.get("options") if isinstance(parsed_question.get("options"), dict) else {}
    question_no = normalize_positive_int(parsed_question.get("question_no"), default=source_question.question_no, minimum=1)
    stem_md = str(parsed_question.get("stem_md") or "")
    question_type = str(parsed_question.get("question_type") or source_question.question_type)
    return {
        "id": f"parsed-{source_question.id}-{question_no}",
        "question_uid": f"{source_question.question_uid}-q-{question_no:03d}",
        "question_no": question_no,
        "question_type": question_type,
        "question_type_text": get_exam_bank_question_type_review_label(question_type),
        "stem_md": stem_md,
        "stem_md_for_edit": format_exam_markdown_for_teacher_edit(stem_md),
        "stem_html": render_exam_markdown_for_display(stem_md),
        "answer_text": answer_text,
        "analysis_md": str(parsed_question.get("analysis_md") or ""),
        "analysis_html": render_exam_markdown_for_display(str(parsed_question.get("analysis_md") or "当前没有解析。")),
        "has_analysis": bool(str(parsed_question.get("analysis_md") or "").strip()),
        "can_generate_analysis": False,
        "programming_json_text": "",
        "full_json_text": "",
        "options": [
            {
                "key": key,
                "text": str(option_map.get(key) or ""),
                "text_html": render_exam_markdown_for_display(str(option_map.get(key) or "")),
            }
            for key in ["A", "B", "C", "D"]
        ],
        "assets": [],
    }


def build_exam_bank_paper_preview_question_rows(questions: list[ExamQuestionBankQuestion]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    used_question_numbers: set[int] = set()
    raw_page_questions = [question for question in questions if is_raw_ocr_page_question(question)]
    if raw_page_questions:
        parsed_questions = split_ocr_markdown_into_question_blocks(combine_raw_ocr_page_markdown(raw_page_questions))
        source_question = raw_page_questions[0]
        for parsed_question in parsed_questions:
            question_no = normalize_positive_int(parsed_question.get("question_no"), default=0, minimum=1)
            if not question_no or question_no in used_question_numbers:
                continue
            rows.append(
                serialize_parsed_ocr_question_for_review(
                    source_question=source_question,
                    parsed_question=parsed_question,
                )
            )
            used_question_numbers.add(question_no)
    for question in questions:
        if question.id in {raw_question.id for raw_question in raw_page_questions} and used_question_numbers:
            continue
        if (
            question.question_type == ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN
            and not question.options.exists()
        ):
            parsed_questions = split_ocr_markdown_into_question_blocks(question.stem_md)
            if parsed_questions:
                for parsed_question in parsed_questions:
                    question_no = normalize_positive_int(parsed_question.get("question_no"), default=0, minimum=1)
                    if not question_no or question_no in used_question_numbers:
                        continue
                    rows.append(
                        serialize_parsed_ocr_question_for_review(
                            source_question=question,
                            parsed_question=parsed_question,
                        )
                    )
                    used_question_numbers.add(question_no)
                continue
        if question.question_no not in used_question_numbers:
            rows.append(serialize_exam_bank_paper_question_for_review(question, include_assets=question.question_type != ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN))
            used_question_numbers.add(question.question_no)
    rows.sort(key=lambda row: (int(row["question_no"]), str(row["question_uid"])))
    return rows


def get_exam_bank_paper_edit_knowledge_rows(paper: ExamQuestionBankPaper) -> list[dict[str, str]]:
    subject = normalize_exam_knowledge_subject(infer_exam_bank_paper_subject(paper))
    category_code = normalize_exam_knowledge_category_code(paper.level)
    if not subject or not category_code:
        return []
    queryset = ExamKnowledgePointMap.objects.filter(
        subject=subject,
        category_code__iexact=category_code,
        is_active=True,
    )
    if not queryset.exists():
        queryset = ExamKnowledgePointMap.objects.filter(
            subject=subject,
            course_level_code__iexact=category_code,
            is_active=True,
        )
    queryset = (
        queryset
        .exclude(level_1="")
        .exclude(level_2="")
        .order_by("category_code", "sort_order", "level_1", "level_2", "level_3", "id")
        .values("level_1", "level_2", "level_3")
    )
    seen_paths: set[tuple[str, str, str]] = set()
    rows: list[dict[str, str]] = []
    for item in queryset:
        level_1 = str(item.get("level_1") or "").strip()
        level_2 = str(item.get("level_2") or "").strip()
        level_3 = str(item.get("level_3") or "").strip()
        path_key = (level_1, level_2, level_3)
        if not level_1 or not level_2 or path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        rows.append({"level_1": level_1, "level_2": level_2, "level_3": level_3})
    return rows


def resolve_exam_bank_paper_knowledge_scope(paper: ExamQuestionBankPaper) -> tuple[str, str, str]:
    subject = normalize_exam_knowledge_subject(infer_exam_bank_paper_subject(paper))
    category_code = normalize_exam_knowledge_category_code(paper.level)
    if not subject or not category_code:
        return "", "", ""
    course_level_code = derive_exam_knowledge_course_level_code(category_code) or category_code
    if ExamKnowledgePointMap.objects.filter(
        subject=subject,
        category_code__iexact=category_code,
        is_active=True,
    ).exists():
        return subject, category_code, course_level_code
    existing_category_code = (
        ExamKnowledgePointMap.objects.filter(
            subject=subject,
            course_level_code__iexact=category_code,
            is_active=True,
        )
        .exclude(category_code="")
        .order_by("category_code")
        .values_list("category_code", flat=True)
        .first()
    )
    return subject, normalize_exam_knowledge_category_code(existing_category_code or category_code), course_level_code


def build_missing_knowledge_question_rows(question_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    missing_rows: list[dict[str, object]] = []
    for question in question_rows:
        missing_parts: list[str] = []
        if not str(question.get("knowledge_level_1") or "").strip():
            missing_parts.append("一级知识目录")
        if not str(question.get("knowledge_level_2") or "").strip():
            missing_parts.append("二级知识目录")
        if missing_parts:
            stem_preview = re.sub(r"\s+", " ", str(question.get("stem_md") or "")).strip()
            missing_rows.append(
                {
                    "id": question.get("id"),
                    "question_no": question.get("question_no"),
                    "stem_preview": stem_preview[:96],
                    "missing_text": "、".join(missing_parts),
                }
            )
    return missing_rows


def ensure_exam_bank_paper_knowledge_map_path(
    *,
    paper: ExamQuestionBankPaper,
    portal_user: PortalUser,
    level_1: str,
    level_2: str,
    level_3: str = "",
) -> None:
    normalized_level_1 = str(level_1 or "").strip()[:128]
    normalized_level_2 = str(level_2 or "").strip()[:128]
    normalized_level_3 = str(level_3 or "").strip()[:255]
    if not normalized_level_1 or not normalized_level_2:
        return
    subject, category_code, course_level_code = resolve_exam_bank_paper_knowledge_scope(paper)
    if not subject or not category_code:
        return
    existing = ExamKnowledgePointMap.objects.filter(
        subject=subject,
        category_code__iexact=category_code,
        level_1=normalized_level_1,
        level_2=normalized_level_2,
        level_3=normalized_level_3,
    ).first()
    if existing is not None:
        update_fields = []
        if not existing.is_active:
            existing.is_active = True
            update_fields.append("is_active")
        if existing.course_level_code != course_level_code:
            existing.course_level_code = course_level_code
            update_fields.append("course_level_code")
        if existing.uploaded_by_id != portal_user.id:
            existing.uploaded_by = portal_user
            update_fields.append("uploaded_by")
        if update_fields:
            existing.save(update_fields=[*update_fields, "updated_at"])
        return
    next_sort_order = (
        ExamKnowledgePointMap.objects.filter(subject=subject, category_code__iexact=category_code)
        .aggregate(max_order=Max("sort_order"))
        .get("max_order")
        or 0
    ) + 1
    ExamKnowledgePointMap.objects.create(
        subject=subject,
        course_level_code=course_level_code,
        category_code=category_code,
        level_1=normalized_level_1,
        level_2=normalized_level_2,
        level_3=normalized_level_3,
        source_path=f"teacher_exam_bank_paper_edit:{paper.id}",
        uploaded_by=portal_user,
        sort_order=next_sort_order,
        is_active=True,
    )


def get_exam_bank_paper_review_context(
    portal_user: PortalUser,
    paper_id: int,
    *,
    mode: str,
    error_message: str = "",
    success_message: str = "",
) -> dict[str, object]:
    paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).get()
    is_edit = mode == "edit"
    if is_edit:
        materialize_raw_ocr_paper_questions(paper)
    separate_programming_reference_solutions_for_paper(paper)
    questions = list(
        paper.questions.prefetch_related("options", "assets").order_by("question_no", "id")
    )
    question_rows = (
        [serialize_exam_bank_paper_question_for_review(question, include_assets=question.question_type != ExamQuestionBankQuestion.QUESTION_TYPE_RAW_MARKDOWN) for question in questions]
        if is_edit
        else build_exam_bank_paper_preview_question_rows(questions)
    )
    knowledge_subject = normalize_exam_knowledge_subject(infer_exam_bank_paper_subject(paper))
    knowledge_category_code = normalize_exam_knowledge_category_code(paper.level)
    return {
        "page_title": f"{'编辑' if is_edit else '预览'}试卷 · {paper.title}",
        "page_description": "发布前先检查整张试卷的题面、选项、答案、解析和题图。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "考试管理", "href": reverse("teacher-exams")},
            {"label": "试卷管理", "href": f"{reverse('teacher-exams')}#available-exam-papers"},
            {"label": "编辑试卷" if is_edit else "预览试卷"},
        ],
        "paper": paper,
        "question_rows": question_rows,
        "question_type_choices": ExamQuestionBankQuestion.QUESTION_TYPE_CHOICES,
        "option_keys": ["A", "B", "C", "D"],
        "mode": mode,
        "is_edit": is_edit,
        "error_message": error_message,
        "success_message": success_message,
        "knowledge_subject": knowledge_subject,
        "knowledge_category_code": knowledge_category_code,
        "knowledge_map_rows": get_exam_bank_paper_edit_knowledge_rows(paper) if is_edit else [],
        "missing_knowledge_questions": build_missing_knowledge_question_rows(question_rows) if is_edit else [],
        "preview_href": reverse("teacher-exam-bank-paper-preview", args=[paper.id]),
        "edit_href": reverse("teacher-exam-bank-paper-edit", args=[paper.id]),
        "back_href": f"{reverse('teacher-exams')}#available-exam-papers",
    }


def update_exam_bank_paper_from_request(
    paper: ExamQuestionBankPaper,
    request: HttpRequest,
    portal_user: PortalUser,
) -> None:
    question_ids = normalize_positive_int_list(request.POST.getlist("question_ids"))
    new_question_keys: list[str] = []
    seen_new_question_keys: set[str] = set()
    for raw_key in request.POST.getlist("new_question_keys"):
        key = re.sub(r"[^0-9A-Za-z_-]", "", str(raw_key or "").strip())
        if key and key not in seen_new_question_keys:
            new_question_keys.append(key)
            seen_new_question_keys.add(key)
    if not question_ids and not new_question_keys:
        raise ValidationError("当前试卷没有可保存的题目。")
    if question_ids:
        valid_question_ids = set(paper.questions.filter(id__in=question_ids).values_list("id", flat=True))
        if valid_question_ids != set(question_ids):
            raise ValidationError("提交的题目数据不属于当前试卷，请刷新后重试。")

    option_keys = ["A", "B", "C", "D"]
    type_values = {choice[0] for choice in ExamQuestionBankQuestion.QUESTION_TYPE_CHOICES}
    with transaction.atomic():
        locked_paper = ExamQuestionBankPaper.objects.select_for_update().get(id=paper.id, is_active=True)
        locked_paper.title = str(request.POST.get("paper_title") or "").strip() or locked_paper.title
        locked_paper.level = str(request.POST.get("paper_level") or "").strip() or locked_paper.level
        locked_paper.save(update_fields=["title", "level", "updated_at"])

        questions = {
            question.id: question
            for question in ExamQuestionBankQuestion.objects.select_for_update().filter(paper=locked_paper, id__in=question_ids)
        }
        for question_id in question_ids:
            question = questions[question_id]
            question_type = str(request.POST.get(f"question_{question_id}_type") or question.question_type).strip()
            if question_type not in type_values:
                raise ValidationError(f"第 {question.question_no} 题题型不合法。")
            stem_md = normalize_preserved_multiline_text(request.POST.get(f"question_{question_id}_stem_md") or "").strip()
            stem_md = restore_exam_markdown_code_fences_from_original(stem_md, question.stem_md)
            if not stem_md:
                raise ValidationError(f"第 {question.question_no} 题题面不能为空。")
            answer_text = str(request.POST.get(f"question_{question_id}_answer") or "").strip()
            existing_answer = question.answer_json if isinstance(question.answer_json, dict) else {}
            answer_json = dict(existing_answer)
            if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE:
                answer_json["correct_answer"] = answer_text.upper()
            else:
                answer_json["answer"] = answer_text
            question.question_type = question_type
            question.stem_md = stem_md
            question.answer_json = answer_json
            question.analysis_md = normalize_preserved_multiline_text(
                request.POST.get(f"question_{question_id}_analysis") or ""
            ).strip()
            full_json = dict(question.full_json) if isinstance(question.full_json, dict) else {}
            knowledge_level_1 = str(request.POST.get(f"question_{question_id}_knowledge_level_1") or "").strip()
            knowledge_level_2 = str(request.POST.get(f"question_{question_id}_knowledge_level_2") or "").strip()
            knowledge_level_3 = str(request.POST.get(f"question_{question_id}_knowledge_level_3") or "").strip()
            full_json["knowledge_level_1"] = knowledge_level_1
            full_json["knowledge_level_2"] = knowledge_level_2
            full_json["knowledge_level_3"] = knowledge_level_3
            question.full_json = full_json
            question.save(update_fields=["question_type", "stem_md", "answer_json", "analysis_md", "full_json", "updated_at"])
            ensure_exam_bank_paper_knowledge_map_path(
                paper=locked_paper,
                portal_user=portal_user,
                level_1=knowledge_level_1,
                level_2=knowledge_level_2,
                level_3=knowledge_level_3,
            )

            for sort_order, option_key in enumerate(option_keys, start=1):
                option_text = normalize_preserved_multiline_text(
                    request.POST.get(f"question_{question_id}_option_{option_key.lower()}") or ""
                ).strip()
                if option_text:
                    ExamQuestionBankOption.objects.update_or_create(
                        question=question,
                        option_key=option_key,
                        defaults={
                            "option_text_md": option_text,
                            "sort_order": sort_order,
                        },
                    )
                else:
                    ExamQuestionBankOption.objects.filter(question=question, option_key=option_key).delete()

        next_question_no = (locked_paper.questions.aggregate(max_no=Max("question_no")).get("max_no") or 0) + 1
        created_count = 0
        for key in new_question_keys:
            question_type = str(
                request.POST.get(f"new_question_{key}_type")
                or ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE
            ).strip()
            if question_type not in type_values:
                raise ValidationError("新增题目题型不合法。")
            stem_md = normalize_preserved_multiline_text(
                request.POST.get(f"new_question_{key}_stem_md") or ""
            ).strip()
            answer_text = str(request.POST.get(f"new_question_{key}_answer") or "").strip()
            analysis_md = normalize_preserved_multiline_text(
                request.POST.get(f"new_question_{key}_analysis") or ""
            ).strip()
            knowledge_level_1 = str(request.POST.get(f"new_question_{key}_knowledge_level_1") or "").strip()
            knowledge_level_2 = str(request.POST.get(f"new_question_{key}_knowledge_level_2") or "").strip()
            knowledge_level_3 = str(request.POST.get(f"new_question_{key}_knowledge_level_3") or "").strip()
            options: dict[str, str] = {
                option_key: normalize_preserved_multiline_text(
                    request.POST.get(f"new_question_{key}_option_{option_key.lower()}") or ""
                ).strip()
                for option_key in option_keys
            }
            has_any_value = bool(
                stem_md
                or answer_text
                or analysis_md
                or knowledge_level_1
                or knowledge_level_2
                or knowledge_level_3
                or any(options.values())
            )
            if not has_any_value:
                continue
            if not stem_md:
                raise ValidationError("新增题目的题干 Markdown 不能为空。")
            answer_json = (
                {"correct_answer": answer_text.upper()}
                if question_type == ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE
                else {"answer": answer_text}
            )
            analysis_md = build_default_question_analysis_md(
                question_type=question_type,
                stem_md=stem_md,
                answer_json=answer_json,
                options=options,
                existing_analysis_md=analysis_md,
            )
            question_uid_prefix = re.sub(r"\s+", "_", str(locked_paper.source_pdf_id or locked_paper.id).strip())[:96]
            new_question = ExamQuestionBankQuestion.objects.create(
                paper=locked_paper,
                question_uid=f"{question_uid_prefix or locked_paper.id}-manual-{uuid.uuid4().hex[:12]}",
                question_no=next_question_no,
                question_type=question_type,
                stem_md=stem_md,
                answer_json=answer_json,
                analysis_md=analysis_md,
                programming_json={},
                full_json={
                    "source": "teacher_manual_edit",
                    "knowledge_level_1": knowledge_level_1,
                    "knowledge_level_2": knowledge_level_2,
                    "knowledge_level_3": knowledge_level_3,
                },
            )
            ensure_exam_bank_paper_knowledge_map_path(
                paper=locked_paper,
                portal_user=portal_user,
                level_1=knowledge_level_1,
                level_2=knowledge_level_2,
                level_3=knowledge_level_3,
            )
            for sort_order, option_key in enumerate(option_keys, start=1):
                option_text = options.get(option_key, "")
                if not option_text:
                    continue
                ExamQuestionBankOption.objects.create(
                    question=new_question,
                    option_key=option_key,
                    option_text_md=option_text,
                    sort_order=sort_order,
                )
            next_question_no += 1
            created_count += 1

        if not question_ids and created_count == 0:
            raise ValidationError("当前试卷没有可保存的题目。")


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
def student_home(request: HttpRequest) -> HttpResponse:
    return render_shell_page(
        request,
        "student",
        "entry/student_home.html",
        {"active_nav": "home"},
    )


@role_required("student")
def student_profile(request: HttpRequest) -> HttpResponse:
    # 占位数据写在模板里，待接真实学员资料（等级/星数/统计/徽章）。
    return render_shell_page(
        request,
        "student",
        "entry/student_profile_redesign.html",
        {"active_nav": "profile"},
    )


@role_required("student")
def student_wrongbook(request: HttpRequest) -> HttpResponse:
    # 占位数据写在模板里，待接真实错题数据（薄弱点聚合 + 待重做错题）。
    return render_shell_page(
        request,
        "student",
        "entry/student_wrongbook_redesign.html",
        {"active_nav": "profile"},
    )


@role_required("student")
def student_coursemap(request: HttpRequest) -> HttpResponse:
    # 占位数据写在模板里，待接真实课程进度（逐课通关状态 + 概览数字）。
    return render_shell_page(
        request,
        "student",
        "entry/student_coursemap_redesign.html",
        {"active_nav": "course"},
    )


@role_required("student")
def student_course_page(request: HttpRequest) -> HttpResponse:
    # 课程主页（重设计）。占位数据写在模板里；不影响现有 student_courses（通用 portal page）。
    return render_shell_page(
        request,
        "student",
        "entry/student_course_redesign.html",
        {"active_nav": "course"},
    )


@role_required("student")
def student_result_demo(request: HttpRequest) -> HttpResponse:
    # 批改/成绩详情 · 占位 UI 版。真实数据（student-homework-detail / student-exam-record-detail）待接。
    return render_shell_page(
        request,
        "student",
        "entry/student_result_redesign.html",
        {"active_nav": "homework"},
    )


@role_required("student")
def student_parent_report_demo(request: HttpRequest) -> HttpResponse:
    # 家长学习报告 · 占位 UI 版。真实数据（时长/掌握度/动态）待接。
    # 家长视角，导航不高亮（active_nav 不设）。
    return render_shell_page(
        request,
        "student",
        "entry/student_parent_report_redesign.html",
        {},
    )


@role_required("student")
def student_answer(request: HttpRequest) -> HttpResponse:
    ctx = "exam" if request.GET.get("ctx") == "exam" else "hw"
    if ctx == "exam":
        context = {
            "active_nav": "exam",
            "back_href": reverse("student-exam-list"),
            "answer_title": "GESP 二级 · 模拟测",
            "answer_meta": "共 3 题 · 限时计时中",
        }
    else:
        context = {
            "active_nav": "homework",
            "back_href": reverse("student-homework-list"),
            "answer_title": "第5课 · 循环练习",
            "answer_meta": "共 3 题 · 预计 25 分钟",
        }
    return render_shell_page(request, "student", "entry/student_answer.html", context)


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
def student_free_practice(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    student = get_student_by_user(portal_user)
    level_code = (request.POST.get("level_code") if request.method == "POST" else request.GET.get("level_code")) or ""
    knowledge_query = (request.POST.get("knowledge_query") if request.method == "POST" else request.GET.get("knowledge_query")) or ""
    knowledge_level_2 = (request.POST.get("knowledge_level_2") if request.method == "POST" else request.GET.get("knowledge_level_2")) or ""

    def render_free_practice(
        *,
        error_message: str = "",
        limit_warning: str = "",
        selected_question_ids: list[object] | None = None,
    ) -> HttpResponse:
        context = build_student_free_practice_context(
            portal_user,
            level_code=level_code,
            knowledge_query=knowledge_query,
            knowledge_level_2=knowledge_level_2,
            selected_question_ids=selected_question_ids,
            error_message=error_message,
            limit_warning=limit_warning,
        )
        return render_shell_page(request, "student", "entry/student_free_practice.html", context)

    if request.method == "POST":
        action = request.POST.get("form_action", "").strip()
        if action != "start_free_practice":
            return render_free_practice(error_message="请选择有效的自由练习操作。")
        selected_question_tokens = []
        seen_question_tokens = set()
        for raw_token in request.POST.getlist("question_ids"):
            token = str(raw_token or "").strip()
            if not token or token in seen_question_tokens:
                continue
            if token.isdigit():
                normalized_token = str(normalize_positive_int(token, default=0, minimum=1))
                if normalized_token != "0" and normalized_token not in seen_question_tokens:
                    selected_question_tokens.append(normalized_token)
                    seen_question_tokens.add(normalized_token)
                continue
            bank_match = re.fullmatch(r"bank:(\d+)", token)
            if bank_match:
                normalized_id = normalize_positive_int(bank_match.group(1), default=0, minimum=1)
                normalized_token = f"bank:{normalized_id}"
                if normalized_id and normalized_token not in seen_question_tokens:
                    selected_question_tokens.append(normalized_token)
                    seen_question_tokens.add(normalized_token)
                continue
            homework_match = re.fullmatch(r"homework:(\d+)", token)
            if homework_match:
                normalized_id = normalize_positive_int(homework_match.group(1), default=0, minimum=1)
                normalized_token = f"homework:{normalized_id}"
                if normalized_id and normalized_token not in seen_question_tokens:
                    selected_question_tokens.append(normalized_token)
                    seen_question_tokens.add(normalized_token)
        if not selected_question_tokens:
            return render_free_practice(error_message="请至少选择一道题。")
        if len(selected_question_tokens) > 20:
            return render_free_practice(
                limit_warning="练习不在多，而在精。",
                selected_question_ids=selected_question_tokens[:20],
            )
        ordered_question_refs = []
        exam_question_ids = []
        bank_question_ids = []
        homework_question_ids = []
        for token in selected_question_tokens:
            if token.isdigit():
                question_id = normalize_positive_int(token, default=0, minimum=1)
                if question_id:
                    ordered_question_refs.append(("exam", question_id))
                    exam_question_ids.append(question_id)
                continue
            bank_match = re.fullmatch(r"bank:(\d+)", token)
            if bank_match:
                bank_question_id = normalize_positive_int(bank_match.group(1), default=0, minimum=1)
                if bank_question_id:
                    ordered_question_refs.append(("bank", bank_question_id))
                    bank_question_ids.append(bank_question_id)
                continue
            homework_match = re.fullmatch(r"homework:(\d+)", token)
            if homework_match:
                homework_question_id = normalize_positive_int(homework_match.group(1), default=0, minimum=1)
                if homework_question_id:
                    ordered_question_refs.append(("homework", homework_question_id))
                    homework_question_ids.append(homework_question_id)
        questions_by_id = {
            question.id: question
            for question in ExamQuestion.objects.select_related("paper", "paper__teacher", "paper__course")
            .prefetch_related("analysis_blocks")
            .filter(id__in=exam_question_ids, is_active=True, paper__is_active=True)
        }
        bank_questions_by_id = materialize_free_practice_bank_questions(bank_question_ids)
        homework_questions_by_id = materialize_free_practice_homework_questions(homework_question_ids)
        source_questions = []
        for ref_type, question_id in ordered_question_refs:
            if ref_type == "exam" and question_id in questions_by_id:
                source_questions.append(questions_by_id[question_id])
            if ref_type == "bank" and question_id in bank_questions_by_id:
                source_questions.append(bank_questions_by_id[question_id])
            if ref_type == "homework" and question_id in homework_questions_by_id:
                source_questions.append(homework_questions_by_id[question_id])
        if not source_questions:
            return render_free_practice(error_message="没有找到可练习的题目。", selected_question_ids=selected_question_tokens)
        try:
            practice_session = create_free_exam_practice_session(
                student=student,
                source_questions=source_questions,
                level_code=level_code,
                knowledge_query=" / ".join(part for part in [knowledge_query.strip(), knowledge_level_2.strip()] if part),
            )
        except ExamError as exc:
            return render_free_practice(error_message=str(exc), selected_question_ids=selected_question_tokens)
        return redirect(
            build_redirect_with_query(
                reverse("student-exam-detail", args=[practice_session.id]),
                params={"op": "practice_started"},
            )
        )

    return render_free_practice()


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
    items = context.get("homework_items", [])
    todo_count = sum(1 for i in items if not i["is_completed"] and not i["is_cancelled"])
    submitted_count = sum(1 for i in items if i["is_completed"] and not i["is_reviewed"])
    graded_count = sum(1 for i in items if i["is_reviewed"])
    context.update(
        {
            "active_nav": "homework",
            "nav_homework_badge": todo_count,
            "hw_todo_count": todo_count,
            "hw_submitted_count": submitted_count,
            "hw_graded_count": graded_count,
        }
    )
    return render_shell_page(request, "student", "entry/student_homework_list_redesign.html", context)


@role_required("student")
def student_exam_list(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)

    def render_exam_list(*, error_message: str = "", success_message: str = "") -> HttpResponse:
        context = build_student_exam_list_context(
            portal_user,
            error_message=error_message,
            success_message=success_message,
        )
        items = context.get("exam_items", [])
        assigned = sum(1 for i in items if not i["is_finished"] and not i["is_in_progress"])
        context.update(
            {
                "active_nav": "exam",
                "nav_exam_badge": assigned,
                "exam_assigned_count": assigned,
                "exam_progress_count": sum(1 for i in items if i["is_in_progress"]),
                "exam_finished_count": sum(1 for i in items if i["is_finished"]),
            }
        )
        return render_shell_page(request, "student", "entry/student_exam_list_redesign.html", context)

    if request.method == "POST":
        action = (request.POST.get("form_action") or "").strip()
        if action != "enter_exam_access_code":
            return render_exam_list(error_message="请选择有效的考试操作。")
        student = get_student_by_user(portal_user)
        try:
            session = create_or_get_exam_session_by_access_code(
                student=student,
                access_code=request.POST.get("exam_access_code", ""),
            )
        except ExamError as exc:
            return render_exam_list(error_message=str(exc))
        return redirect(
            build_redirect_with_query(
                reverse("student-exam-detail", args=[session.id]),
                params={"op": "code_accepted"},
            )
        )

    return render_exam_list()


@role_required("student")
def student_site_messages(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    context = build_student_site_message_context(portal_user)
    return render_shell_page(request, "student", "entry/student_site_messages.html", context)


@role_required("student")
def student_site_message_open(request: HttpRequest, message_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    student = get_student_by_user(portal_user)
    try:
        message = StudentSiteMessage.objects.get(id=message_id, student=student)
    except StudentSiteMessage.DoesNotExist as exc:
        raise Http404("未找到该消息") from exc
    if not message.is_read:
        message.is_read = True
        message.read_at = timezone.now()
        message.save(update_fields=["is_read", "read_at", "updated_at"])
    target_href = str(message.target_href or "").strip()
    if target_href:
        return redirect(target_href)
    return redirect(reverse("student-site-messages"))


@role_required("student")
def student_exam_record_detail(request: HttpRequest, paper_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_student_exam_record_detail_context(portal_user, paper_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该考试记录") from exc
    return render_shell_page(request, "student", "entry/student_exam_record_detail.html", context)


@role_required("student")
def student_exam_detail(request: HttpRequest, session_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    student = get_student_by_user(portal_user)

    def render_exam(
        *,
        error_message: str = "",
        selected_answer_overrides: dict[int, str] | None = None,
        explanation_overrides: dict[int, str] | None = None,
        focus_question_id: int | None = None,
    ) -> HttpResponse:
        try:
            context = build_student_exam_detail_context(
                portal_user,
                session_id,
                selected_answer_overrides=selected_answer_overrides,
                explanation_overrides=explanation_overrides,
            )
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该考试") from exc
        if error_message:
            context["error_message"] = error_message
        if focus_question_id:
            context["focus_question_id"] = focus_question_id
        if request.GET.get("op") == "submitted":
            session_status = str((context.get("session") or {}).get("status") or "")
            context["success_message"] = (
                "考试已提交，编程题等待老师查看。"
                if session_status == ExamSession.STATUS_SUBMITTED
                else "考试已提交并自动判分。"
            )
        elif request.GET.get("op") == "practice_started":
            context["success_message"] = "练习场次已创建，可以开始作答。"
        elif request.GET.get("op") == "code_accepted":
            context["success_message"] = "口令校验成功，可以开始考试。"
        elif request.GET.get("op") == "analysis_suggestion_submitted":
            context["success_message"] = "你的解析挑战已提交，老师采纳后会展示给同学们。"
        return render_shell_page(request, "student", "entry/student_exam_detail.html", context)

    try:
        session = ExamSession.objects.select_related("paper").get(id=session_id, student=student, is_active=True)
    except ExamSession.DoesNotExist as exc:
        raise Http404("未找到该考试") from exc

    if request.method == "POST":
        action = request.POST.get("form_action", "").strip()
        if action == "start_exam":
            try:
                started_session = start_exam_session(session, student=student)
            except ExamError as exc:
                return render_exam(error_message=str(exc))
            return redirect(
                build_redirect_with_query(
                    reverse("student-exam-detail", args=[started_session.id]),
                    params={"op": "started"},
                )
            )

        if action in {"start_full_practice", "start_wrong_practice"}:
            try:
                practice_session = create_exam_practice_session(
                    source_session=session,
                    student=student,
                    session_type=(
                        ExamSession.SESSION_TYPE_FULL_PRACTICE
                        if action == "start_full_practice"
                        else ExamSession.SESSION_TYPE_WRONG_PRACTICE
                    ),
                )
            except ExamError as exc:
                return render_exam(error_message=str(exc))
            return redirect(
                build_redirect_with_query(
                    reverse("student-exam-detail", args=[practice_session.id]),
                    params={"op": "practice_started"},
                )
            )

        if action.startswith("submit_analysis_suggestion"):
            if session.status not in {ExamSession.STATUS_SUBMITTED, ExamSession.STATUS_AUTO_CHECKED}:
                return render_exam(error_message="交卷后才能提交解析挑战。")
            _action_name, _separator, action_question_id = action.partition(":")
            question_id = normalize_positive_int(action_question_id, default=0, minimum=1)
            suggestion_text = str(request.POST.get(f"analysis_suggestion_{question_id}") or "").strip()
            if len("".join(suggestion_text.split())) < 5:
                return render_exam(error_message="解析挑战内容太短，请至少写出一句完整说明。", focus_question_id=question_id)
            questions_by_id = {question.id: question for question in get_exam_session_questions(session)}
            question = questions_by_id.get(question_id)
            if not question:
                return render_exam(error_message="未找到这道题，无法提交解析挑战。")
            ExamQuestionAnalysisSuggestion.objects.create(
                question=question,
                student=student,
                session=session,
                content_md=suggestion_text,
            )
            return redirect(
                build_redirect_with_query(
                    reverse("student-exam-detail", args=[session.id]),
                    params={"op": "analysis_suggestion_submitted"},
                    anchor=f"student-exam-question-{question.id}",
                )
            )

        if action != "submit_exam":
            return render_exam(error_message="请选择有效的考试操作。")

        selected_answers = {}
        explanation_texts = {}
        programming_submissions = {}
        for key, value in request.POST.items():
            if key.startswith("question_"):
                question_id = normalize_positive_int(key.split("_", 1)[1], default=0, minimum=1)
                if question_id:
                    selected_answers[question_id] = str(value).strip().upper()
            elif key.startswith("explanation_"):
                question_id = normalize_positive_int(key.split("_", 1)[1], default=0, minimum=1)
                if question_id:
                    explanation_texts[question_id] = str(value).strip()
            elif key.startswith("programming_submission_"):
                question_id = normalize_positive_int(key.rsplit("_", 1)[1], default=0, minimum=1)
                if question_id:
                    programming_submissions[question_id] = str(value).strip()
        try:
            graded_session = grade_exam_session(
                session,
                student=student,
                selected_answers=selected_answers,
                explanation_texts=explanation_texts,
                programming_submissions=programming_submissions,
            )
        except ExamError as exc:
            explanation_overrides = {**explanation_texts, **programming_submissions}
            return render_exam(
                error_message=str(exc),
                selected_answer_overrides=selected_answers,
                explanation_overrides=explanation_overrides,
                focus_question_id=getattr(exc, "target_question_id", None),
            )
        return redirect(
            build_redirect_with_query(
                reverse("student-exam-detail", args=[graded_session.id]),
                params={"op": "submitted"},
            )
        )
    return render_exam()


@role_required("student")
def student_exam_print(request: HttpRequest, session_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_student_exam_print_context(
            portal_user,
            session_id,
            variant=str(request.GET.get("variant") or "blank_full"),
            hide_important_marks=str(request.GET.get("hide_important") or "").strip() == "1",
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该考试打印页") from exc
    return render(request, "entry/student_exam_print.html", context)


def get_scratch_programming_config(question: ExamQuestion) -> dict[str, object]:
    snapshot = question.source_snapshot_json if isinstance(question.source_snapshot_json, dict) else {}
    programming_config = snapshot.get("programming_json") if isinstance(snapshot.get("programming_json"), dict) else {}
    return programming_config


def get_scratch_editor_url_for_question(question: ExamQuestion) -> str:
    programming_config = get_scratch_programming_config(question)
    return str(
        programming_config.get("editor_url") or getattr(settings, "DASHIMA_SCRATCH_EDITOR_URL", "")
    ).strip()


def save_programming_uploaded_file(uploaded_file: UploadedFile, *, submission_id: int, role: str) -> str:
    suffix = Path(str(uploaded_file.name or "")).suffix.lower()
    if role == "project" and suffix != ".sb3":
        suffix = ".sb3"
    if role == "screenshot" and suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".png"
    relative_path = f"programming_submissions/{submission_id}/{role}_{uuid.uuid4().hex}{suffix}"
    return default_storage.save(relative_path, uploaded_file)


def normalize_programming_block_count(value: object) -> int | None:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def get_programming_submission_project_file_path(submission: ProgrammingSubmission, *, prefer_submitted: bool = False) -> str:
    artifacts = submission.artifacts_json if isinstance(submission.artifacts_json, dict) else {}
    if prefer_submitted:
        submitted_path = str(artifacts.get("submitted_project_file_path") or "").strip()
        if submitted_path:
            return submitted_path
    return str(submission.project_file_path or artifacts.get("project_file_path") or "").strip()


def build_programming_project_file_url(*, request: HttpRequest, submission: ProgrammingSubmission) -> str:
    project_file_path = get_programming_submission_project_file_path(submission, prefer_submitted=True)
    if not project_file_path:
        return ""
    filename = Path(project_file_path).name
    return request.build_absolute_uri(
        build_redirect_with_query(
            reverse("api-programming-submission-project-file", args=[submission.id]),
            params={"filename": filename},
        )
    )


def build_programming_submission_editor_url(
    *,
    request: HttpRequest,
    submission: ProgrammingSubmission,
    question: ExamQuestion,
) -> str:
    editor_url = get_scratch_editor_url_for_question(question)
    programming_config = get_scratch_programming_config(question)
    return_url = request.build_absolute_uri(
        f"{reverse('student-exam-detail', args=[submission.exam_session_id])}#student-exam-question-{question.id}"
    )
    callback_url = request.build_absolute_uri(
        reverse("api-programming-submission-artifacts", args=[submission.id])
    )
    params = {
        "source": "codemaster",
        "platform": submission.platform,
        "submission_id": submission.id,
        "token": submission.launch_token,
        "session_id": submission.exam_session_id,
        "paper_id": submission.exam_session.paper_id if submission.exam_session_id else "",
        "question_id": question.id,
        "question_no": question.question_no,
        "callback_url": callback_url,
        "return_url": return_url,
    }
    project_url = ""
    if get_programming_submission_project_file_path(submission):
        project_url = build_programming_project_file_url(request=request, submission=submission)
        params["project_file_url"] = project_url
    elif submission.project_url:
        project_url = submission.project_url
    elif programming_config.get("template_project"):
        project_url = str(programming_config.get("template_project"))
    if project_url:
        params["project_url"] = project_url
    if programming_config.get("template_project"):
        params["template_project"] = str(programming_config.get("template_project"))
    return build_redirect_with_query(editor_url, params=params)


def build_programming_submission_review_editor_url(
    *,
    request: HttpRequest,
    submission: ProgrammingSubmission,
) -> str:
    question = submission.exam_question
    editor_url = submission.editor_url or (get_scratch_editor_url_for_question(question) if question else "")
    project_url = ""
    if get_programming_submission_project_file_path(submission, prefer_submitted=True):
        project_url = build_programming_project_file_url(request=request, submission=submission)
    elif submission.project_url:
        project_url = submission.project_url
    if not editor_url or not project_url:
        return ""

    session = submission.exam_session
    return_url = ""
    if session:
        return_url = request.build_absolute_uri(
            reverse("teacher-student-exam-detail", args=[submission.student_id, session.id])
        )
    params = {
        "source": "codemaster",
        "mode": "teacher_review",
        "platform": submission.platform,
        "submission_id": submission.id,
        "student_id": submission.student_id,
        "student_name": submission.student.display_name if submission.student_id else "",
        "project_title": submission.project_title or "Scratch 作品",
        "project_url": project_url,
        "project_file_url": project_url,
        "readonly": "1",
        "return_url": return_url,
    }
    if session:
        params["session_id"] = session.id
        params["paper_id"] = session.paper_id
    if question:
        params["question_id"] = question.id
        params["question_no"] = question.question_no
    return build_redirect_with_query(editor_url, params=params)


@role_required("student")
def student_programming_scratch_launch(request: HttpRequest, session_id: int, question_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    student = get_student_by_user(portal_user)
    session = (
        ExamSession.objects.select_related("paper", "student")
        .filter(id=session_id, student=student, is_active=True, paper__is_active=True)
        .get()
    )
    question = (
        ExamQuestion.objects.select_related("paper")
        .filter(id=question_id, paper=session.paper, question_type=ExamQuestion.QUESTION_TYPE_PROGRAMMING, is_active=True)
        .get()
    )
    editor_url = get_scratch_editor_url_for_question(question)
    if not editor_url:
        return redirect(
            build_redirect_with_query(
                reverse("student-exam-detail", args=[session.id]),
                params={"op": "scratch_editor_missing"},
                anchor=f"student-exam-question-{question.id}",
            )
        )

    submission, _created = ProgrammingSubmission.objects.update_or_create(
        student=student,
        exam_session=session,
        exam_question=question,
        platform=ProgrammingSubmission.PLATFORM_SCRATCH,
        defaults={
            "editor_url": editor_url,
        },
    )
    return redirect(
        build_programming_submission_editor_url(
            request=request,
            submission=submission,
            question=question,
        )
    )


@role_required("teacher")
def teacher_programming_scratch_review_launch(request: HttpRequest, submission_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    submission = (
        ProgrammingSubmission.objects.select_related(
            "student",
            "exam_session",
            "exam_session__paper",
            "exam_question",
        )
        .filter(id=submission_id, platform=ProgrammingSubmission.PLATFORM_SCRATCH)
        .first()
    )
    if not submission or not submission.exam_session or not submission.exam_question:
        raise Http404("未找到该 Scratch 作品")
    if submission.exam_session.paper.teacher_id != portal_user.id:
        return HttpResponseForbidden("无权查看该 Scratch 作品。")

    editor_url = build_programming_submission_review_editor_url(request=request, submission=submission)
    if not editor_url:
        return redirect(
            build_redirect_with_query(
                reverse("teacher-student-exam-detail", args=[submission.student_id, submission.exam_session_id]),
                params={"op": "scratch_review_missing"},
                anchor=f"teacher-exam-question-{submission.exam_question_id}",
            )
        )
    return redirect(editor_url)


def build_programming_project_file_response_headers(response: HttpResponse) -> HttpResponse:
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    response["Cross-Origin-Resource-Policy"] = "cross-origin"
    return response


@csrf_exempt
def api_programming_submission_project_file(request: HttpRequest, submission_id: int) -> HttpResponse:
    if request.method == "OPTIONS":
        return build_programming_project_file_response_headers(HttpResponse(status=204))
    if request.method not in {"GET", "HEAD"}:
        return build_programming_project_file_response_headers(
            JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)
        )

    submission = ProgrammingSubmission.objects.filter(id=submission_id).first()
    if not submission:
        raise Http404("未找到该 Scratch 作品文件")
    artifacts = submission.artifacts_json if isinstance(submission.artifacts_json, dict) else {}
    candidate_paths = [
        str(artifacts.get("submitted_project_file_path") or "").strip(),
        str(submission.project_file_path or "").strip(),
        str(artifacts.get("project_file_path") or "").strip(),
    ]
    candidate_paths = [path for path in candidate_paths if path]
    if not candidate_paths:
        raise Http404("未找到该 Scratch 作品文件")
    requested_filename = str(request.GET.get("filename") or "").strip()
    project_file_path = ""
    if requested_filename:
        for candidate_path in candidate_paths:
            if Path(candidate_path).name == requested_filename:
                project_file_path = candidate_path
                break
    else:
        project_file_path = candidate_paths[0]
    if not project_file_path:
        raise Http404("未找到该 Scratch 作品文件")
    if not default_storage.exists(project_file_path):
        raise Http404("未找到该 Scratch 作品文件")

    actual_filename = Path(project_file_path).name
    response = FileResponse(
        default_storage.open(project_file_path, "rb"),
        as_attachment=False,
        filename=actual_filename,
        content_type="application/octet-stream",
    )
    return build_programming_project_file_response_headers(response)


def build_programming_cors_response(payload: dict[str, object], *, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    return response


def get_programming_submission_token(request: HttpRequest, payload: dict[str, object] | None = None) -> str:
    authorization = str(request.headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    if payload and payload.get("token"):
        return str(payload.get("token") or "").strip()
    return str(request.POST.get("token") or request.GET.get("token") or "").strip()


def load_programming_submission_payload(request: HttpRequest) -> dict[str, object]:
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
    return {key: value for key, value in request.POST.items()}


def parse_programming_json_payload(value: object) -> dict[str, object] | list[object] | None:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


@csrf_exempt
def api_programming_submission_artifacts(request: HttpRequest, submission_id: int) -> JsonResponse:
    if request.method == "OPTIONS":
        return build_programming_cors_response({"ok": True})
    if request.method != "POST":
        return build_programming_cors_response({"ok": False, "error": "method_not_allowed"}, status=405)

    payload = load_programming_submission_payload(request)
    token = get_programming_submission_token(request, payload)
    try:
        launch_token = uuid.UUID(str(token))
    except (TypeError, ValueError):
        return build_programming_cors_response({"ok": False, "error": "invalid_submission_token"}, status=403)
    submission = (
        ProgrammingSubmission.objects.select_related("student", "exam_session", "exam_question")
        .filter(id=submission_id, launch_token=launch_token)
        .first()
    )
    if not submission:
        return build_programming_cors_response({"ok": False, "error": "invalid_submission_token"}, status=403)

    update_fields = ["updated_at"]
    artifacts = dict(submission.artifacts_json) if isinstance(submission.artifacts_json, dict) else {}
    previous_project_file_path = str(submission.project_file_path or artifacts.get("project_file_path") or "").strip()
    previous_block_count = normalize_programming_block_count(artifacts.get("block_count"))
    artifacts_payload = parse_programming_json_payload(payload.get("artifacts"))
    incoming_block_count = None
    if isinstance(artifacts_payload, dict):
        incoming_block_count = normalize_programming_block_count(artifacts_payload.get("block_count"))
        artifacts.update(artifacts_payload)
    project_file = request.FILES.get("project_file") or request.FILES.get("sb3")
    if project_file:
        uploaded_project_file_path = save_programming_uploaded_file(project_file, submission_id=submission.id, role="project")
        artifacts["last_uploaded_project_file_path"] = uploaded_project_file_path
        should_replace_project_file = True
        if (
            previous_project_file_path
            and previous_block_count is not None
            and incoming_block_count is not None
            and incoming_block_count < previous_block_count
        ):
            should_replace_project_file = False
        if should_replace_project_file:
            submission.project_file_path = uploaded_project_file_path
            artifacts["project_file_path"] = submission.project_file_path
            update_fields.append("project_file_path")
        else:
            artifacts["project_file_path"] = previous_project_file_path
            artifacts["ignored_project_file_path"] = uploaded_project_file_path
            if previous_block_count is not None:
                artifacts["block_count"] = previous_block_count
    screenshot = request.FILES.get("screenshot")
    if screenshot:
        submission.screenshot_path = save_programming_uploaded_file(screenshot, submission_id=submission.id, role="screenshot")
        artifacts["screenshot_path"] = submission.screenshot_path
        update_fields.append("screenshot_path")

    project_title = str(payload.get("project_title") or "").strip()
    if project_title:
        submission.project_title = project_title[:255]
        update_fields.append("project_title")
    project_url = str(payload.get("project_url") or "").strip()
    if project_url:
        submission.project_url = project_url[:500]
        update_fields.append("project_url")
    learning_events_payload = parse_programming_json_payload(payload.get("learning_events"))
    if isinstance(learning_events_payload, (dict, list)):
        submission.learning_events_json = learning_events_payload or {}
        update_fields.append("learning_events_json")
    ai_summary_payload = parse_programming_json_payload(payload.get("ai_summary"))
    if isinstance(ai_summary_payload, (dict, list)):
        submission.ai_summary_json = ai_summary_payload or {}
        update_fields.append("ai_summary_json")

    submission.artifacts_json = artifacts
    submission.last_saved_at = timezone.now()
    update_fields.extend(["artifacts_json", "last_saved_at"])
    if str(payload.get("status") or "").strip() == ProgrammingSubmission.STATUS_SUBMITTED:
        submission.status = ProgrammingSubmission.STATUS_SUBMITTED
        submission.submitted_at = timezone.now()
        submitted_project_file_path = get_programming_submission_project_file_path(submission)
        if submitted_project_file_path:
            artifacts["submitted_project_file_path"] = submitted_project_file_path
            submitted_block_count = normalize_programming_block_count(artifacts.get("block_count"))
            if submitted_block_count is not None:
                artifacts["submitted_block_count"] = submitted_block_count
        update_fields.extend(["status", "submitted_at"])
    elif submission.status == ProgrammingSubmission.STATUS_DRAFT:
        update_fields.append("status")

    submission.save(update_fields=sorted(set(update_fields)))
    return build_programming_cors_response(
        {
            "ok": True,
            "submission_id": submission.id,
            "status": submission.status,
            "project_file_path": submission.project_file_path,
            "screenshot_path": submission.screenshot_path,
            "updated_at": submission.updated_at.isoformat(),
        }
    )


@role_required("student")
def api_student_exam_proctor_event(request: HttpRequest, session_id: int) -> JsonResponse:
    portal_user = get_portal_user_from_request(request)
    student = get_student_by_user(portal_user)
    if request.method != "POST":
        return JsonResponse({"error": "只支持 POST。"}, status=405)
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    try:
        session = ExamSession.objects.select_related("paper").get(id=session_id, student=student, is_active=True)
        event = record_exam_proctor_event(
            session,
            student=student,
            event_type=str(payload.get("event_type") or ""),
            metadata={"source": "browser_exam_page"},
        )
        session.refresh_from_db(fields=["switch_count", "status"])
    except (ExamSession.DoesNotExist, ExamError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"event_id": event.id, "switch_count": session.switch_count, "status": session.status})


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


def load_exam_question_bank_import_records(uploaded_file: UploadedFile) -> list[dict]:
    try:
        raw_data = json.loads(uploaded_file.read().decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValidationError("题库 JSON 文件必须使用 UTF-8 编码。") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"题库 JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列。") from exc

    if isinstance(raw_data, list):
        records = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("questions"), list):
        records = raw_data["questions"]
    else:
        raise ValidationError("JSON 顶层必须是题目数组，或包含 questions 数组的对象。")

    normalized_records = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValidationError(f"第 {index} 条题目不是对象。")
        normalized_records.append(record)
    return normalized_records


def import_exam_question_bank_items_from_json(
    *,
    teacher: PortalUser,
    course_id: int,
    uploaded_file: UploadedFile,
) -> int:
    teacher_course_ids = {
        assignment.course_id
        for assignment in TeacherStudentAssignment.objects.filter(teacher=teacher, is_active=True)
    }
    if course_id not in teacher_course_ids:
        raise ValidationError("请选择当前老师负责范围内的课程。")

    course = Course.objects.filter(id=course_id).first()
    if course is None:
        raise ValidationError("请选择有效课程。")

    content_by_id = {
        content.id: content
        for content in CourseContent.objects.filter(course_id=course_id, is_active=True)
    }
    records = load_exam_question_bank_import_records(uploaded_file)
    if not records:
        raise ValidationError("题库 JSON 里没有题目。")

    created_count = 0
    with transaction.atomic():
        for index, record in enumerate(records, start=1):
            question_type = str(record.get("question_type") or ExamQuestionBankItem.QUESTION_TYPE_SINGLE_CHOICE).strip()
            if question_type != ExamQuestionBankItem.QUESTION_TYPE_SINGLE_CHOICE:
                raise ValidationError(f"第 {index} 题暂只支持 single_choice。")

            stem = str(record.get("stem") or record.get("title") or "").strip()
            options = normalize_exam_options(record.get("options") or record.get("options_json"))
            correct_answer = normalize_exam_answer(record.get("correct_answer") or record.get("answer"))
            if not stem:
                raise ValidationError(f"第 {index} 题题干不能为空。")
            if any(not options[key] for key in ["A", "B", "C", "D"]):
                raise ValidationError(f"第 {index} 题 A/B/C/D 选项必须填写完整。")
            if not correct_answer:
                raise ValidationError(f"第 {index} 题正确答案必须是 A/B/C/D。")

            content_id = normalize_positive_int(record.get("content_id"), default=0, minimum=1)
            content = content_by_id.get(content_id) if content_id else None
            knowledge_point = str(
                record.get("knowledge_point")
                or (content.title if content else "")
                or record.get("wrong_point_label")
                or "未归类"
            ).strip()
            level_code = str(record.get("level_code") or (content.level.code if content and content.level_id else "")).strip()
            source_snapshot = record.get("source_snapshot_json")

            item = ExamQuestionBankItem(
                course=course,
                content=content,
                level_code=level_code,
                knowledge_point=knowledge_point,
                source=ExamQuestionBankItem.SOURCE_IMPORT,
                source_label=str(record.get("source_label") or uploaded_file.name).strip(),
                source_url=str(record.get("source_url") or "").strip(),
                question_type=ExamQuestionBankItem.QUESTION_TYPE_SINGLE_CHOICE,
                stem=stem,
                options_json=options,
                correct_answer=correct_answer,
                analysis=str(record.get("analysis") or "").strip(),
                score=record.get("score") or 1,
                image_path=str(record.get("image_path") or "").strip(),
                source_snapshot_json=source_snapshot if isinstance(source_snapshot, dict) else {},
                created_by=teacher,
                is_active=True,
            )
            item.full_clean()
            item.save()
            created_count += 1
    return created_count


def build_exam_bank_paper_publish_marker(bank_paper_id: int) -> str:
    return f"exam_question_bank_paper_id={bank_paper_id}"


def get_exam_bank_paper_id_from_exam_description(description: str) -> int | None:
    marker_match = re.search(r"(?:^|\n)exam_question_bank_paper_id=(\d+)(?:\n|$)", str(description or ""))
    if not marker_match:
        return None
    return normalize_positive_int(marker_match.group(1), default=0, minimum=1) or None


def exam_bank_paper_has_exam_management_record(bank_paper_id: int) -> bool:
    marker = build_exam_bank_paper_publish_marker(bank_paper_id)
    return ExamPaper.objects.filter(description__contains=marker, is_active=True).exists()


def get_exam_bank_paper_ids_with_exam_management_records(teacher: PortalUser | None = None) -> set[int]:
    bank_paper_ids: set[int] = set()
    queryset = ExamPaper.objects.filter(is_active=True).exclude(description="")
    if teacher is not None:
        queryset = queryset.filter(teacher=teacher)
    descriptions = queryset.values_list("description", flat=True)
    for description in descriptions:
        bank_paper_id = get_exam_bank_paper_id_from_exam_description(str(description or ""))
        if bank_paper_id:
            bank_paper_ids.add(bank_paper_id)
    return bank_paper_ids


def delete_exam_bank_paper_by_usage(bank_paper_id: int) -> dict[str, object]:
    with transaction.atomic():
        bank_paper = (
            ExamQuestionBankPaper.objects.select_for_update()
            .filter(id=bank_paper_id)
            .first()
        )
        if bank_paper is None:
            return {"deleted_count": 0, "mode": "missing"}
        if exam_bank_paper_has_exam_management_record(bank_paper.id):
            if not bank_paper.is_active:
                return {"deleted_count": 0, "mode": "soft"}
            bank_paper.is_active = False
            bank_paper.save(update_fields=["is_active", "updated_at"])
            return {"deleted_count": 1, "mode": "soft"}

        source_pdf_id = bank_paper.source_pdf_id
        bank_paper.delete()
        if source_pdf_id:
            ExamQuestionBankImportJob.objects.filter(source_pdf_id=source_pdf_id).update(
                is_active=False,
                updated_at=timezone.now(),
            )
        return {"deleted_count": 1, "mode": "hard"}


def ensure_teacher_can_operate_exam_bank_paper(portal_user: PortalUser, bank_paper: ExamQuestionBankPaper) -> None:
    subject_title = infer_exam_bank_paper_subject(bank_paper)
    if not teacher_can_operate_exam_subject(portal_user, subject_title):
        raise ExamError("这张试卷不属于当前老师负责的学科，不能操作。")


def ensure_teacher_can_operate_exam_paper(portal_user: PortalUser, paper: ExamPaper) -> None:
    subject_title = infer_exam_paper_subject_title(paper)
    if not teacher_can_operate_exam_subject(portal_user, subject_title):
        raise ExamError("这场考试不属于当前老师负责的学科，不能操作。")


def get_bank_question_exam_answer(question: ExamQuestionBankQuestion) -> str:
    answer_json = question.answer_json if isinstance(question.answer_json, dict) else {}
    raw_answer = (
        answer_json.get("correct_answer")
        or answer_json.get("answer")
        or answer_json.get("value")
        or ""
    )
    if question.question_type == ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE:
        normalized = str(raw_answer or "").strip().lower()
        if normalized in {"a", "t", "true", "1", "yes", "y", "正确", "对", "是", "√", "✓"}:
            return "A"
        if normalized in {"b", "f", "false", "0", "no", "n", "错误", "错", "否", "×", "✕", "x"}:
            return "B"
    return normalize_exam_answer(raw_answer)


def get_bank_question_exam_options(question: ExamQuestionBankQuestion) -> dict[str, str]:
    option_rows = list(question.options.all().order_by("sort_order", "option_key"))
    options = {
        str(option.option_key or "").strip().upper(): str(option.option_text_md or "").strip()
        for option in option_rows
        if str(option.option_key or "").strip()
    }
    if question.question_type == ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE:
        options.setdefault("A", "正确")
        options.setdefault("B", "错误")
    return {key: value for key, value in options.items() if key in {"A", "B", "C", "D"} and value}


def clean_bank_question_stem_for_exam(
    question: ExamQuestionBankQuestion,
    bank_paper: ExamQuestionBankPaper,
    *,
    remove_visual_placeholders: bool = False,
) -> str:
    title = str(bank_paper.title or "").strip()
    stem_lines = str(question.stem_md or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned_lines = []
    for line in stem_lines:
        stripped = line.strip()
        if title and stripped == title:
            continue
        if remove_visual_placeholders and stripped in {"[流程图见图]", "【流程图见图】", "流程图见图"}:
            continue
        if title and stripped.startswith(title):
            stripped = stripped.removeprefix(title).strip(" ：:-")
            if not stripped:
                continue
            line = stripped
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def ensure_flowchart_content_asset_for_bank_question(
    *,
    bank_paper: ExamQuestionBankPaper,
    bank_question: ExamQuestionBankQuestion,
) -> None:
    stem_text = str(bank_question.stem_md or "")
    if "流程图" not in stem_text and "[流程图见图]" not in stem_text:
        return
    if bank_question.assets.filter(asset_role="content").exists():
        return

    full_json = bank_question.full_json if isinstance(bank_question.full_json, dict) else {}
    page_no = normalize_positive_int(full_json.get("page_no"), default=1, minimum=1)
    import_job = (
        ExamQuestionBankImportJob.objects.filter(source_pdf_id=bank_paper.source_pdf_id, workspace_relative_path__gt="")
        .order_by("-id")
        .first()
    )
    if not import_job:
        return
    source_relative_path = f"{import_job.workspace_relative_path}/page_images/page_{page_no:03d}.png"
    if not default_storage.exists(source_relative_path):
        return

    from PIL import Image

    with default_storage.open(source_relative_path, "rb") as source_file:
        image = Image.open(source_file).convert("RGB")
        width, height = image.size
        # Current PDF imports do not yet persist Hermes source_regions. For flowchart
        # placeholder questions, crop the question-body visual band from the page image
        # instead of attaching the full OCR page screenshot.
        if bank_question.question_no == 1:
            crop_box = (
                int(width * 0.16),
                int(height * 0.345),
                int(width * 0.90),
                int(height * 0.51),
            )
        else:
            crop_box = (
                int(width * 0.12),
                int(height * 0.20),
                int(width * 0.92),
                int(height * 0.62),
            )
        cropped = image.crop(crop_box)

    asset_uid = f"{bank_paper.source_pdf_id}_q{bank_question.question_no:03d}_flowchart_01"
    relative_path = f"question_bank/content/{bank_paper.level}/{bank_paper.source_pdf_id}/{asset_uid}.png"
    if not default_storage.exists(relative_path):
        from io import BytesIO

        buffer = BytesIO()
        cropped.save(buffer, format="PNG")
        default_storage.save(relative_path, ContentFile(buffer.getvalue()))

    ExamQuestionBankAsset.objects.update_or_create(
        question=bank_question,
        asset_uid=asset_uid,
        defaults={
            "asset_role": "content",
            "asset_type": "flowchart",
            "relative_path": relative_path,
            "public_url": "",
            "alt": f"第 {bank_question.question_no} 题流程图",
            "width": cropped.size[0],
            "height": cropped.size[1],
        },
    )


def get_exam_knowledge_mapping_path(bank_paper: ExamQuestionBankPaper) -> Path:
    level_code = re.sub(r"[^0-9A-Za-z_-]", "", str(bank_paper.level or "").strip().upper())
    if not level_code:
        raise ExamPaperImportError("这张试卷没有所属级别，无法匹配知识对照表。")
    return Path(settings.BASE_DIR) / "knowledge_mappings" / "cpp" / f"{level_code}知识对照.md"


def normalize_exam_knowledge_subject(subject_title: str) -> str:
    normalized = normalize_knowledge_map_subject(subject_title)
    return normalized or re.sub(r"[^0-9a-zA-Z_-]", "", str(subject_title or "").strip().lower())


def normalize_exam_knowledge_category_code(value: object) -> str:
    code = str(value or "").strip().upper().replace(" ", "")
    if code in {"CSPJ", "CSP-J"}:
        return "CSP-J"
    if code in {"CSPS", "CSP-S"}:
        return "CSP-S"
    match = re.fullmatch(r"GESP([1-8])", code)
    if match:
        return f"GESP{match.group(1)}"
    return code[:32]


SCRATCH_LEVEL_LABEL_MAP = {
    "一级": "S1",
    "1级": "S1",
    "S1": "S1",
    "二级": "S2",
    "2级": "S2",
    "S2": "S2",
    "三级": "S3",
    "3级": "S3",
    "S3": "S3",
    "四级": "S4",
    "4级": "S4",
    "S4": "S4",
}

TEACHER_MANUAL_SUBJECT_COURSE_SCAFFOLDS = {
    "scratch": {
        "slug": "scratch",
        "title": "Scratch",
        "summary": "图形化编程与创意表达主线。",
        "category": {
            "slug": "grade-exam",
            "title": "图形化等级考试",
            "summary": "承接图形化编程一级至四级学生池和练习范围。",
        },
        "levels": [
            ("S1", "图形化编程一级", "图形化编程一级"),
            ("S2", "图形化编程二级", "图形化编程二级"),
            ("S3", "图形化编程三级", "图形化编程三级"),
            ("S4", "图形化编程四级", "图形化编程四级"),
        ],
    },
}


def build_manual_subject_course_slug(subject_name: object) -> str:
    raw_subject = str(subject_name or "").strip()
    subject_key = normalize_knowledge_map_subject(raw_subject)
    if subject_key == "drone":
        subject_key = "uav"
    base_slug = subject_key if re.fullmatch(r"[a-z0-9_-]+", subject_key or "") else ""
    if not base_slug:
        base_slug = slugify(raw_subject)
    digest = hashlib.sha1(raw_subject.encode("utf-8")).hexdigest()[:8]
    if not base_slug:
        base_slug = f"manual-{digest}"
    if len(base_slug) > 40:
        base_slug = f"{base_slug[:31].rstrip('-_')}-{digest}"
    if not Course.objects.filter(slug=base_slug).exclude(title__iexact=raw_subject).exists():
        return base_slug
    return f"{base_slug[:31].rstrip('-_')}-{digest}"


def ensure_course_default_student_pool_level(course: Course, *, category_title: str = "通用分组") -> None:
    category, _ = CourseCategory.objects.get_or_create(
        course=course,
        slug="general",
        defaults={
            "title": category_title,
            "summary": "用于学生池新增学生的默认分组。",
            "sort_order": 1,
            "is_active": True,
        },
    )
    CourseLevel.objects.get_or_create(
        category=category,
        code="DEFAULT",
        defaults={
            "title": "默认级别",
            "summary": "用于未细分级别的新学科学生关系。",
            "sort_order": 1,
            "is_active": True,
        },
    )


def ensure_teacher_manual_subject_course(subject_name: object) -> Course | None:
    raw_subject = str(subject_name or "").strip()
    if not raw_subject:
        return None
    subject_key = normalize_knowledge_map_subject(subject_name)
    if subject_key == "drone":
        subject_key = "uav"

    scaffold = TEACHER_MANUAL_SUBJECT_COURSE_SCAFFOLDS.get(subject_key)
    if scaffold is None:
        course = (
            Course.objects.filter(Q(slug__iexact=subject_key) | Q(title__iexact=raw_subject))
            .order_by("id")
            .first()
        )
        if course is None:
            course = Course.objects.create(
                slug=build_manual_subject_course_slug(raw_subject),
                title=raw_subject,
                summary=f"{raw_subject} 课程方向。",
            )
        ensure_course_default_student_pool_level(course)
        return course

    course = (
        Course.objects.filter(Q(slug__iexact=scaffold["slug"]) | Q(title__iexact=scaffold["title"]))
        .order_by("id")
        .first()
    )
    if course is None:
        course = Course.objects.create(
            slug=scaffold["slug"],
            title=scaffold["title"],
            summary=scaffold["summary"],
        )

    category_data = scaffold["category"]
    category = (
        CourseCategory.objects.filter(course=course, slug=category_data["slug"])
        .order_by("id")
        .first()
    )
    if category is None:
        category = CourseCategory.objects.create(
            course=course,
            slug=category_data["slug"],
            title=category_data["title"],
            summary=category_data["summary"],
            sort_order=1,
            is_active=True,
        )
    for sort_order, (level_code, level_title, level_summary) in enumerate(scaffold["levels"], start=1):
        CourseLevel.objects.get_or_create(
            category=category,
            code=level_code,
            defaults={
                "title": level_title,
                "summary": level_summary,
                "sort_order": sort_order,
                "is_active": True,
            },
        )
    return course


def resolve_teacher_courses_from_subjects(
    selected_course_ids: list[int],
    manual_subject_parts: list[str],
) -> list[Course]:
    courses_by_id = {
        course.id: course
        for course in Course.objects.filter(id__in=selected_course_ids).order_by("id")
    }
    courses = [courses_by_id[course_id] for course_id in selected_course_ids if course_id in courses_by_id]
    seen_course_ids = {course.id for course in courses}
    for subject_part in manual_subject_parts:
        course = ensure_teacher_manual_subject_course(subject_part)
        if course is not None and course.id not in seen_course_ids:
            courses.append(course)
            seen_course_ids.add(course.id)
    return courses


def infer_scratch_course_level_code_from_markdown_heading(line: object) -> str:
    text = re.sub(r"^[#\s]+", "", str(line or "").strip())
    match = re.search(r"^(.+?)[（(]\s*([^）)]+)\s*[）)]", text, flags=re.IGNORECASE)
    if not match:
        return ""
    heading_title = match.group(1).strip()
    if "图形化" not in heading_title and "scratch" not in heading_title.lower():
        return ""
    return SCRATCH_LEVEL_LABEL_MAP.get(match.group(2).strip().upper(), SCRATCH_LEVEL_LABEL_MAP.get(match.group(2).strip(), ""))


def infer_exam_knowledge_category_code_from_markdown(markdown_text: object) -> str:
    for raw_line in str(markdown_text or "").splitlines():
        text = re.sub(r"^[#\s]+", "", raw_line.strip())
        if not text:
            continue
        match = re.search(r"^(.+?)[（(]\s*(一级|二级|三级|四级|[1-4]级|S[1-4])\s*[）)]", text, flags=re.IGNORECASE)
        if match:
            category_code = normalize_exam_knowledge_category_code(match.group(1))
            if category_code:
                return category_code
    return ""


def infer_exam_knowledge_category_code_from_filename(filename: object) -> str:
    raw_stem = Path(str(filename or "")).stem.strip()
    stem = raw_stem.upper().replace(" ", "")
    csp_match = re.search(r"CSP[-_]?([JS])", stem)
    if csp_match:
        return f"CSP-{csp_match.group(1)}"
    gesp_match = re.search(r"GESP[-_]?([1-8])", stem)
    if gesp_match:
        return f"GESP{gesp_match.group(1)}"
    cleaned = re.sub(r"^\d{4,8}", "", raw_stem).strip()
    cleaned = re.sub(r"(?i)S\s*[1-4]\s*[-_~至到]\s*S\s*[1-4]", "", cleaned)
    cleaned = re.sub(r"(?i)\bS\s*[1-4]\b", "", cleaned)
    cleaned = re.sub(r"(知识点|知识对照|知识体系|知识目录|对照|导入|表格|markdown|md)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[-_（）()\s]+$", "", cleaned).strip()
    cleaned = re.sub(r"^[-_（）()\s]+", "", cleaned).strip()
    if cleaned and re.search(r"[\u4e00-\u9fffA-Za-z]", cleaned):
        return normalize_exam_knowledge_category_code(cleaned)
    return ""


def derive_exam_knowledge_course_level_code(category_code: object) -> str:
    category_code = normalize_exam_knowledge_category_code(category_code)
    if category_code in {"S1", "S2", "S3", "S4"}:
        return category_code
    match = re.fullmatch(r"GESP([1-8])", category_code)
    if match:
        level_number = int(match.group(1))
        return "C1" if level_number <= 4 else "C2"
    if category_code == "CSP-J":
        return "C3"
    if category_code == "CSP-S":
        return "C4"
    return ""


def resolve_course_for_knowledge_subject(portal_user: PortalUser, subject: str, course_slug: str = "") -> Course | None:
    normalized_subject = normalize_knowledge_map_subject(subject)
    if course_slug:
        try:
            return get_teacher_course_scope(portal_user, course_slug)["course"]
        except ObjectDoesNotExist:
            return None
    candidate_queryset = Course.objects.all()
    if normalized_subject == "cpp":
        candidate_queryset = candidate_queryset.filter(Q(slug__iexact="cpp") | Q(title__iexact="C++") | Q(title__iexact="cpp"))
    else:
        candidate_queryset = candidate_queryset.filter(Q(slug__iexact=normalized_subject) | Q(title__iexact=normalized_subject))
    for course in candidate_queryset.order_by("id"):
        if teacher_can_import_students(portal_user):
            return course
        if TeacherStudentAssignment.objects.filter(teacher=portal_user, course=course, is_active=True).exists():
            return course
    return None


def build_knowledge_path_title(level_1: str, level_2: str, level_3: str = "") -> str:
    return " / ".join(part for part in [level_1, level_2, level_3] if str(part or "").strip())


def ensure_course_content_for_knowledge_path(
    *,
    portal_user: PortalUser,
    subject: str,
    category_code: str,
    level_1: str,
    level_2: str = "",
    level_3: str = "",
    course_slug: str = "",
) -> CourseContent:
    normalized_subject = normalize_knowledge_map_subject(subject or course_slug or "cpp")
    normalized_category_code = str(category_code or "").strip().upper()
    normalized_level_1 = str(level_1 or "").strip()
    normalized_level_2 = str(level_2 or "").strip()
    normalized_level_3 = str(level_3 or "").strip()
    if not normalized_subject:
        raise ValidationError("请选择学科。")
    if not normalized_category_code:
        raise ValidationError("请选择级别。")
    if not normalized_level_1:
        raise ValidationError("请选择或填写一级知识点。")

    title = build_knowledge_path_title(normalized_level_1, normalized_level_2, normalized_level_3)
    if not title:
        raise ValidationError("请先选择有效的知识点路径。")

    selected_course = resolve_course_for_knowledge_subject(
        portal_user,
        normalized_subject,
        course_slug,
    )
    if selected_course is None:
        raise ValidationError("当前课程不存在，或你没有该课程权限。")

    available_level_options = get_teacher_question_source_level_options(portal_user, selected_course.slug)
    matched_level_option = next(
        (
            item
            for item in available_level_options
            if str(item.get("code") or "").strip().upper() == normalized_category_code
        ),
        None,
    )
    if matched_level_option is None:
        raise ValidationError("请选择当前老师负责范围内的有效级别。")

    selected_level = (
        CourseLevel.objects.select_related("category", "category__course")
        .filter(id=int(matched_level_option["id"]), is_active=True, category__course=selected_course)
        .first()
    )
    if selected_level is None:
        raise ValidationError("请选择当前老师负责范围内的有效级别。")

    existing_type = (
        CourseContent.objects.filter(course=selected_course)
        .exclude(content_type="")
        .order_by("id")
        .values_list("content_type", flat=True)
        .first()
    ) or f"{selected_course.title}{selected_level.category.title}"
    with transaction.atomic():
        ExamKnowledgePointMap.objects.update_or_create(
            subject=normalized_subject,
            category_code=normalized_category_code,
            level_1=normalized_level_1,
            level_2=normalized_level_2,
            level_3=normalized_level_3,
            defaults={"is_active": True},
        )
        existing_content = CourseContent.objects.filter(
            course=selected_course,
            level=selected_level,
            title__iexact=title,
            is_active=True,
        ).first()
        if existing_content is not None:
            return existing_content
        generated_slug = build_auto_course_content_slug(selected_course, selected_level, title)
        route_path = build_knowledge_point_default_route_path(
            selected_course.slug,
            selected_level.category.slug,
            selected_level.code,
            generated_slug,
        )
        next_sort_order = (
            (CourseContent.objects.filter(level=selected_level).aggregate(max_sort=Max("sort_order"))["max_sort"] or 0)
            + 1
        )
        return CourseContent.objects.create(
            course=selected_course,
            level=selected_level,
            content_type=existing_type,
            slug=generated_slug,
            title=title,
            phase=selected_level.code,
            permission_code=infer_content_permission_code(
                selected_course.slug,
                level_code=selected_level.code,
                phase=selected_level.code,
            ),
            sort_order=next_sort_order,
            route_path=route_path,
            summary="",
            has_real_content=False,
            is_active=True,
        )


def parse_exam_knowledge_markdown_table_rows(markdown_text: str) -> list[tuple[str, str, str]]:
    return [
        (entry["level_1"], entry["level_2"], entry["level_3"])
        for entry in parse_exam_knowledge_markdown_table_entries(markdown_text)
    ]


def parse_exam_knowledge_markdown_table_entries(markdown_text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    in_table = False
    current_course_level_code = ""
    for raw_line in str(markdown_text or "").splitlines():
        line = raw_line.strip()
        heading_course_level_code = infer_scratch_course_level_code_from_markdown_heading(line)
        if heading_course_level_code:
            current_course_level_code = heading_course_level_code
        if not line.startswith("|") or not line.endswith("|"):
            if in_table:
                in_table = False
            continue
        cells = [cell.strip().strip("*") for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        first_cell = cells[0].strip()
        if first_cell == "一级目录" or set(first_cell.replace(":", "").replace("-", "")) <= {" "}:
            in_table = True
            continue
        if all(set(cell.replace(":", "").replace("-", "")) <= {" "} for cell in cells[:3]):
            in_table = True
            continue
        in_table = True
        level_1 = cells[0].strip()
        level_2 = cells[1].strip()
        level_3 = cells[2].strip()
        if level_1 and level_2:
            row = {
                "course_level_code": current_course_level_code,
                "level_1": level_1[:128],
                "level_2": level_2[:128],
                "level_3": level_3[:255],
            }
            entries.append(row)
    return entries


def import_exam_knowledge_markdown(
    *,
    portal_user: PortalUser,
    subject: str,
    category_code: str,
    uploaded_file: UploadedFile,
) -> int:
    subject = normalize_exam_knowledge_subject(subject)
    if not subject:
        raise ValidationError("请选择科目。")
    if not uploaded_file:
        raise ValidationError("请先选择 md 文件。")
    if not str(uploaded_file.name or "").lower().endswith(".md"):
        raise ValidationError("当前只支持上传 .md 文件。")
    raw_bytes = uploaded_file.read()
    try:
        markdown_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        markdown_text = raw_bytes.decode("utf-8-sig")
    category_code = (
        normalize_exam_knowledge_category_code(category_code)
        or infer_exam_knowledge_category_code_from_filename(uploaded_file.name)
        or infer_exam_knowledge_category_code_from_markdown(markdown_text)
    )
    if not category_code:
        raise ValidationError("无法识别考试名称，请选择已有考试名称，或使用类似 GESP2知识点.md、CSP-J知识点.md、图形化编程S1-S4知识对照.md 的文件名。")
    default_course_level_code = derive_exam_knowledge_course_level_code(category_code)
    rows = parse_exam_knowledge_markdown_table_entries(markdown_text)
    if not rows:
        raise ValidationError("没有在 md 文件中识别到知识点表格。")
    existing_max_order = (
        ExamKnowledgePointMap.objects.filter(subject=subject, category_code=category_code)
        .aggregate(max_order=Max("sort_order"))
        .get("max_order")
        or 0
    )
    source_path = f"uploaded:{uploaded_file.name}"[:255]
    imported_count = 0
    with transaction.atomic():
        for index, row in enumerate(rows, start=1):
            course_level_code = row["course_level_code"] or default_course_level_code
            ExamKnowledgePointMap.objects.update_or_create(
                subject=subject,
                category_code=category_code,
                level_1=row["level_1"],
                level_2=row["level_2"],
                level_3=row["level_3"],
                defaults={
                    "course_level_code": course_level_code,
                    "source_path": source_path,
                    "sort_order": existing_max_order + index,
                    "is_active": True,
                    "uploaded_by": portal_user,
                },
            )
            imported_count += 1
    return imported_count


def build_exam_knowledge_mapping_markdown_from_rows(rows: list[ExamKnowledgePointMap]) -> str:
    lines = [
        "| 一级目录 | 二级目录 | 三级训练点 / 典型考法 |",
        "|---|---|---|",
    ]
    for row in rows:
        lines.append(f"| {row.level_1} | {row.level_2} | {row.level_3} |")
    return "\n".join(lines)


def search_exam_knowledge_point_maps(
    *,
    subject: str = "",
    category_code: str = "",
    query: str = "",
    limit: int = 50,
):
    queryset = ExamKnowledgePointMap.objects.filter(is_active=True)
    normalized_subject = normalize_exam_knowledge_subject(subject)
    normalized_category = re.sub(r"[^0-9A-Za-z_-]", "", str(category_code or "").strip().upper())
    normalized_query = str(query or "").strip()
    if normalized_subject:
        queryset = queryset.filter(subject=normalized_subject)
    if normalized_category:
        queryset = queryset.filter(category_code__iexact=normalized_category)
    if normalized_query:
        queryset = queryset.filter(
            Q(level_1__icontains=normalized_query)
            | Q(level_2__icontains=normalized_query)
            | Q(level_3__icontains=normalized_query)
        )
    return queryset.order_by("subject", "category_code", "sort_order", "id")[: max(1, min(int(limit or 50), 200))]


def load_exam_knowledge_mapping_markdown(bank_paper: ExamQuestionBankPaper) -> str:
    subject = normalize_exam_knowledge_subject(infer_exam_bank_paper_subject(bank_paper))
    category_code = normalize_exam_knowledge_category_code(bank_paper.level)
    if subject and category_code:
        rows = list(
            ExamKnowledgePointMap.objects.filter(
                subject=subject,
                category_code__iexact=category_code,
                is_active=True,
            ).order_by("sort_order", "id")
        )
        if rows:
            return build_exam_knowledge_mapping_markdown_from_rows(rows)
        rows = list(
            ExamKnowledgePointMap.objects.filter(
                subject=subject,
                course_level_code__iexact=category_code,
                is_active=True,
            ).order_by("category_code", "sort_order", "id")
        )
        if rows:
            return build_exam_knowledge_mapping_markdown_from_rows(rows)
    mapping_path = get_exam_knowledge_mapping_path(bank_paper)
    if not mapping_path.is_file():
        if subject and subject != "cpp":
            level_label = category_code or str(bank_paper.level or "").strip() or "当前级别"
            raise ExamPaperImportError(f"未找到 {infer_exam_bank_paper_subject(bank_paper)} {level_label} 对应的知识对照表，请先在知识点管理中导入。")
        raise ExamPaperImportError(f"未找到 {bank_paper.level} 对应的知识对照表：{mapping_path.relative_to(settings.BASE_DIR)}")
    return mapping_path.read_text(encoding="utf-8").strip()


def update_bank_question_knowledge_points(
    bank_question: ExamQuestionBankQuestion,
    *,
    level_1: str,
    level_2: str,
    level_3: str = "",
    source: str = "qwen",
) -> None:
    full_json = dict(bank_question.full_json) if isinstance(bank_question.full_json, dict) else {}
    full_json["knowledge_level_1"] = str(level_1 or "").strip()
    full_json["knowledge_level_2"] = str(level_2 or "").strip()
    full_json["knowledge_level_3"] = str(level_3 or "").strip()
    full_json["knowledge_source"] = source
    full_json["knowledge_status"] = "done"
    full_json["knowledge_error"] = ""
    bank_question.full_json = full_json
    bank_question.save(update_fields=["full_json", "updated_at"])


def build_bank_question_knowledge_label(bank_question: ExamQuestionBankQuestion, fallback: str = "") -> str:
    full_json = bank_question.full_json if isinstance(bank_question.full_json, dict) else {}
    parts = [
        str(full_json.get("knowledge_level_1") or "").strip(),
        str(full_json.get("knowledge_level_2") or "").strip(),
        str(full_json.get("knowledge_level_3") or "").strip(),
    ]
    label = " / ".join(part for part in parts if part)
    return label or fallback


EXAM_BANK_PAPER_KNOWLEDGE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="exam-bank-knowledge")
EXAM_BANK_PAPER_KNOWLEDGE_LOCK = threading.Lock()
EXAM_BANK_PAPER_KNOWLEDGE_FUTURES: dict[int, object] = {}


def get_exam_bank_paper_knowledge_questions(bank_paper: ExamQuestionBankPaper):
    return bank_paper.questions.filter(
        question_type__in=[
            ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
            ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
        ]
    ).order_by("question_no", "id")


def bank_question_has_knowledge_points(bank_question: ExamQuestionBankQuestion) -> bool:
    full_json = bank_question.full_json if isinstance(bank_question.full_json, dict) else {}
    return bool(
        str(full_json.get("knowledge_level_1") or "").strip()
        and str(full_json.get("knowledge_level_2") or "").strip()
    )


def set_bank_question_knowledge_status(
    bank_question: ExamQuestionBankQuestion,
    status: str,
    *,
    error_message: str = "",
) -> None:
    full_json = dict(bank_question.full_json) if isinstance(bank_question.full_json, dict) else {}
    full_json["knowledge_status"] = status
    full_json["knowledge_error"] = str(error_message or "").strip()[:1000]
    bank_question.full_json = full_json
    bank_question.save(update_fields=["full_json", "updated_at"])


def mark_bank_paper_knowledge_generation_pending(bank_paper: ExamQuestionBankPaper) -> int:
    questions = list(get_exam_bank_paper_knowledge_questions(bank_paper))
    for question in questions:
        if bank_question_has_knowledge_points(question):
            continue
        set_bank_question_knowledge_status(question, "pending")
    return len(questions)


def generate_bank_paper_knowledge_points(bank_paper_id: int) -> int:
    bank_paper = ExamQuestionBankPaper.objects.prefetch_related("questions__options", "questions__assets").get(
        id=bank_paper_id,
        is_active=True,
    )
    mapping_markdown = load_exam_knowledge_mapping_markdown(bank_paper)
    supported_types = {
        ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
        ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
        ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
    }
    updated_count = 0
    for bank_question in bank_paper.questions.filter(question_type__in=supported_types).order_by("question_no", "id"):
        result = identify_bank_question_knowledge_points(
            bank_question=bank_question,
            mapping_markdown=mapping_markdown,
        )
        update_bank_question_knowledge_points(
            bank_question,
            level_1=result["level_1"],
            level_2=result["level_2"],
            level_3=result["level_3"],
            source="qwen",
        )
        updated_count += 1
    sync_exam_questions_for_bank_paper_usage(bank_paper)
    return updated_count


def run_exam_bank_paper_knowledge_generation(bank_paper_id: int) -> None:
    if threading.current_thread() is not threading.main_thread():
        close_old_connections()
    error_messages: list[str] = []
    try:
        bank_paper = ExamQuestionBankPaper.objects.get(id=bank_paper_id, is_active=True)
        mapping_markdown = load_exam_knowledge_mapping_markdown(bank_paper)
        questions = list(get_exam_bank_paper_knowledge_questions(bank_paper).prefetch_related("options", "assets"))
        target_questions = [question for question in questions if not bank_question_has_knowledge_points(question)]
        if not target_questions:
            sync_exam_questions_for_bank_paper_usage(bank_paper)
            return

        worker_count = max(1, min(int(getattr(settings, "EXAM_AI_KNOWLEDGE_QUESTION_CONCURRENCY", 2)), len(target_questions), 4))

        def identify_one(question_id: int) -> tuple[int, str, str]:
            should_manage_db_connection = threading.current_thread() is not threading.main_thread()
            if should_manage_db_connection:
                close_old_connections()
            try:
                question = (
                    ExamQuestionBankQuestion.objects.select_related("paper")
                    .prefetch_related("options", "assets")
                    .get(id=question_id, paper_id=bank_paper_id)
                )
                if bank_question_has_knowledge_points(question):
                    return question_id, "skipped", ""
                set_bank_question_knowledge_status(question, "running")
                result = identify_bank_question_knowledge_points(
                    bank_question=question,
                    mapping_markdown=mapping_markdown,
                )
                update_bank_question_knowledge_points(
                    question,
                    level_1=result["level_1"],
                    level_2=result["level_2"],
                    level_3=result["level_3"],
                    source="qwen",
                )
                return question_id, "done", ""
            except Exception as exc:  # noqa: BLE001 - background status must capture Qwen errors.
                try:
                    failed_question = ExamQuestionBankQuestion.objects.get(id=question_id, paper_id=bank_paper_id)
                    set_bank_question_knowledge_status(failed_question, "failed", error_message=str(exc))
                except Exception:
                    logger.exception("failed to update knowledge status question_id=%s", question_id)
                return question_id, "failed", str(exc)
            finally:
                if should_manage_db_connection:
                    close_old_connections()

        if worker_count == 1:
            question_results = [identify_one(question.id) for question in target_questions]
        else:
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix=f"exam-knowledge-q-{bank_paper_id}") as executor:
                question_results = [future.result() for future in as_completed([executor.submit(identify_one, question.id) for question in target_questions])]
        for _question_id, result, error_message in question_results:
            if result == "failed" and error_message:
                error_messages.append(error_message)
        sync_exam_questions_for_bank_paper_usage(ExamQuestionBankPaper.objects.get(id=bank_paper_id))
    except Exception:
        logger.exception("exam bank paper knowledge generation failed bank_paper_id=%s", bank_paper_id)
        try:
            bank_paper = ExamQuestionBankPaper.objects.get(id=bank_paper_id)
            for question in get_exam_bank_paper_knowledge_questions(bank_paper):
                if not bank_question_has_knowledge_points(question):
                    set_bank_question_knowledge_status(question, "failed", error_message="后台知识点识别任务异常，请稍后重试。")
        except Exception:
            logger.exception("failed to update exam bank paper knowledge status bank_paper_id=%s", bank_paper_id)
    finally:
        with EXAM_BANK_PAPER_KNOWLEDGE_LOCK:
            EXAM_BANK_PAPER_KNOWLEDGE_FUTURES.pop(bank_paper_id, None)
        if threading.current_thread() is not threading.main_thread():
            close_old_connections()


def start_exam_bank_paper_knowledge_generation(bank_paper_id: int) -> bool:
    with transaction.atomic():
        bank_paper = ExamQuestionBankPaper.objects.select_for_update().get(id=bank_paper_id, is_active=True)
        questions = list(get_exam_bank_paper_knowledge_questions(bank_paper))
        total_count = len(questions)
        done_count = sum(1 for question in questions if bank_question_has_knowledge_points(question))
        running_count = sum(
            1
            for question in questions
            if str((question.full_json if isinstance(question.full_json, dict) else {}).get("knowledge_status") or "") in {"pending", "running"}
        )
        can_start = total_count > done_count and running_count == 0
        if not can_start:
            return False
        if any(
            str((question.full_json if isinstance(question.full_json, dict) else {}).get("knowledge_status") or "") in {"pending", "running"}
            for question in questions
        ):
            return False
        load_exam_knowledge_mapping_markdown(bank_paper)
        mark_bank_paper_knowledge_generation_pending(bank_paper)
    with EXAM_BANK_PAPER_KNOWLEDGE_LOCK:
        future = EXAM_BANK_PAPER_KNOWLEDGE_EXECUTOR.submit(run_exam_bank_paper_knowledge_generation, bank_paper_id)
        EXAM_BANK_PAPER_KNOWLEDGE_FUTURES[bank_paper_id] = future
    return True


def sync_exam_questions_from_bank_paper(*, exam_paper: ExamPaper, bank_paper: ExamQuestionBankPaper) -> int:
    supported_types = {
        ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
        ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
        ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
    }
    bank_questions = list(
        bank_paper.questions.prefetch_related("options", "assets")
        .filter(question_type__in=supported_types)
        .order_by("question_no", "id")
    )
    synced_count = 0
    for bank_question in bank_questions:
        ensure_flowchart_content_asset_for_bank_question(bank_paper=bank_paper, bank_question=bank_question)
        options = get_bank_question_exam_options(bank_question)
        correct_answer = get_bank_question_exam_answer(bank_question)
        is_choice_like = bank_question.question_type in {
            ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
        }
        full_json = bank_question.full_json if isinstance(bank_question.full_json, dict) else {}
        is_pdf_crop_demo_question = full_json.get("source") == "pdf_crop_demo"
        if is_choice_like and not is_pdf_crop_demo_question and (not options or correct_answer not in options):
            continue
        content_assets = [
            asset
            for asset in bank_question.assets.filter(asset_role="content").order_by("id")
            if asset.asset_role == "content" and asset.relative_path
        ]
        first_asset = content_assets[0] if content_assets else None
        image_paths = [asset.relative_path for asset in content_assets if asset.relative_path]
        material_image_paths = [
            str(path or "").strip()
            for path in (full_json.get("material_image_paths") if isinstance(full_json.get("material_image_paths"), list) else [])
            if str(path or "").strip()
        ]
        question_image_paths = [
            str(path or "").strip()
            for path in (full_json.get("question_image_paths") if isinstance(full_json.get("question_image_paths"), list) else [])
            if str(path or "").strip()
        ]
        bank_score = str(full_json.get("score") or "").strip()
        default_non_choice_score = "25.00" if is_pdf_crop_demo_question else "0.00"
        knowledge_label = build_bank_question_knowledge_label(bank_question, fallback=bank_paper.level)
        stem = clean_bank_question_stem_for_exam(
            bank_question,
            bank_paper,
            remove_visual_placeholders=bool(first_asset),
        )
        question, _created = ExamQuestion.objects.update_or_create(
            paper=exam_paper,
            question_no=bank_question.question_no,
            defaults={
                "question_type": bank_question.question_type,
                "stem": stem,
                "options_json": options,
                "correct_answer": correct_answer,
                "analysis": bank_question.analysis_md,
                "score": bank_score or ("2.00" if is_choice_like else default_non_choice_score),
                "wrong_point_label": knowledge_label,
                "image_path": first_asset.relative_path if first_asset else "",
                "source_snapshot_json": {
                    "created_from": "exam_question_bank_paper",
                    "bank_paper_id": bank_paper.id,
                    "bank_question_id": bank_question.id,
                    "question_uid": bank_question.question_uid,
                    "source_pdf_id": bank_paper.source_pdf_id,
                    "level_code": bank_paper.level,
                    "knowledge_point": knowledge_label,
                    "knowledge_level_1": str(full_json.get("knowledge_level_1") or "").strip(),
                    "knowledge_level_2": str(full_json.get("knowledge_level_2") or "").strip(),
                    "knowledge_level_3": str(full_json.get("knowledge_level_3") or "").strip(),
                    "question_type": bank_question.question_type,
                    "programming_json": bank_question.programming_json if isinstance(bank_question.programming_json, dict) else {},
                    "image_paths": image_paths,
                    "material_image_paths": material_image_paths,
                    "question_image_paths": question_image_paths,
                    "display_mode": str(full_json.get("display_mode") or ""),
                    "material_group_no": int(full_json.get("material_group_no") or 0),
                },
                "is_active": True,
            },
        )
        if bank_question.analysis_md.strip():
            ExamQuestionAnalysisBlock.objects.update_or_create(
                question=question,
                source_type=ExamQuestionAnalysisBlock.SOURCE_TEACHER,
                sort_order=0,
                defaults={
                    "content_md": bank_question.analysis_md.strip(),
                    "is_visible": True,
                },
            )
        sync_legacy_question_analysis(question)
        synced_count += 1
    return synced_count


EXAM_BANK_PAPER_ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="exam-bank-analysis")
EXAM_BANK_PAPER_ANALYSIS_LOCK = threading.Lock()
EXAM_BANK_PAPER_ANALYSIS_FUTURES: dict[int, object] = {}


def get_exam_bank_paper_analysis_questions(bank_paper: ExamQuestionBankPaper):
    return bank_paper.questions.filter(
        question_type__in=[
            ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
            ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
        ]
    ).order_by("question_no", "id")


def refresh_exam_bank_paper_analysis_status(bank_paper: ExamQuestionBankPaper, *, error_message: str = "") -> ExamQuestionBankPaper:
    questions = list(get_exam_bank_paper_analysis_questions(bank_paper).only("id", "analysis_md"))
    total_count = len(questions)
    done_count = sum(1 for question in questions if str(question.analysis_md or "").strip())
    failed_count = max(total_count - done_count, 0)
    if total_count and done_count >= total_count:
        status = ExamQuestionBankPaper.ANALYSIS_STATUS_COMPLETED
        failed_count = 0
    elif done_count > 0:
        status = ExamQuestionBankPaper.ANALYSIS_STATUS_PARTIAL
    elif error_message:
        status = ExamQuestionBankPaper.ANALYSIS_STATUS_FAILED
    else:
        status = ExamQuestionBankPaper.ANALYSIS_STATUS_NOT_STARTED
        failed_count = 0
    bank_paper.analysis_generation_status = status
    bank_paper.analysis_generation_total_count = total_count
    bank_paper.analysis_generation_done_count = done_count
    bank_paper.analysis_generation_failed_count = failed_count
    bank_paper.analysis_generation_error = error_message[:1000]
    bank_paper.analysis_generation_completed_at = timezone.now() if status != ExamQuestionBankPaper.ANALYSIS_STATUS_RUNNING else None
    bank_paper.save(
        update_fields=[
            "analysis_generation_status",
            "analysis_generation_total_count",
            "analysis_generation_done_count",
            "analysis_generation_failed_count",
            "analysis_generation_error",
            "analysis_generation_completed_at",
            "updated_at",
        ]
    )
    return bank_paper


def sync_exam_questions_for_bank_paper_usage(bank_paper: ExamQuestionBankPaper) -> int:
    marker = build_exam_bank_paper_publish_marker(bank_paper.id)
    synced_count = 0
    for exam_paper in ExamPaper.objects.filter(is_active=True, description__contains=marker).order_by("id"):
        synced_count += sync_exam_questions_from_bank_paper(exam_paper=exam_paper, bank_paper=bank_paper)
    return synced_count


def build_free_practice_bank_paper_source_marker(bank_paper_id: int) -> str:
    return f"free_practice_exam_question_bank_paper_id={bank_paper_id}"


def resolve_free_practice_source_owner() -> PortalUser:
    owner = (
        PortalUser.objects.filter(role=PortalUser.ROLE_PRINCIPAL, is_active=True).order_by("id").first()
        or PortalUser.objects.filter(role=PortalUser.ROLE_TEACHER, is_active=True).order_by("id").first()
        or PortalUser.objects.filter(is_active=True).order_by("id").first()
    )
    if owner is None:
        raise ExamError("当前没有可用教师账号，暂时不能生成自由练习。")
    return owner


def get_or_create_free_practice_source_exam_paper(bank_paper: ExamQuestionBankPaper) -> ExamPaper:
    marker = build_free_practice_bank_paper_source_marker(bank_paper.id)
    existing_paper = (
        ExamPaper.objects.select_related("course", "teacher")
        .filter(is_active=True, description__contains=marker)
        .order_by("-created_at", "-id")
        .first()
    )
    if existing_paper:
        return existing_paper

    subject_title = infer_exam_bank_paper_subject(bank_paper)
    subject_key = normalize_knowledge_map_subject(subject_title)
    course = next(
        (
            candidate
            for candidate in Course.objects.order_by("id")
            if normalize_knowledge_map_subject(candidate.title) == subject_key
            or normalize_knowledge_map_subject(candidate.slug) == subject_key
        ),
        None,
    )
    owner = resolve_free_practice_source_owner()
    description = "\n".join(
        [
            f"来源题库试卷：{bank_paper.title}",
            marker,
            f"source_pdf_id={bank_paper.source_pdf_id}",
            f"level={bank_paper.level}",
            "用途：自由练习题库源，不代表已发布考试。",
        ]
    )
    return ExamPaper.objects.create(
        teacher=owner,
        course=course,
        title=f"题库自由练习源 · {bank_paper.title}",
        description=description,
        mode=ExamPaper.MODE_DEADLINE,
        duration_minutes=60,
        proctoring_enabled=False,
        status=ExamPaper.STATUS_DRAFT,
        is_active=True,
    )


def build_free_practice_homework_source_marker() -> str:
    return "free_practice_homework_question_source=1"


def get_or_create_free_practice_homework_source_exam_paper() -> ExamPaper:
    marker = build_free_practice_homework_source_marker()
    existing_paper = (
        ExamPaper.objects.select_related("course", "teacher")
        .filter(is_active=True, description__contains=marker)
        .order_by("-created_at", "-id")
        .first()
    )
    if existing_paper:
        return existing_paper
    owner = resolve_free_practice_source_owner()
    course = Course.objects.filter(title__iexact="C++").order_by("id").first()
    return ExamPaper.objects.create(
        teacher=owner,
        course=course,
        title="作业题源自由练习源",
        description="\n".join(
            [
                marker,
                "用途：自由练习公共作业题源，不代表已发布考试。",
            ]
        ),
        mode=ExamPaper.MODE_DEADLINE,
        duration_minutes=60,
        proctoring_enabled=False,
        status=ExamPaper.STATUS_DRAFT,
        is_active=True,
    )


def materialize_free_practice_homework_questions(homework_question_ids: list[int]) -> dict[int, ExamQuestion]:
    if not homework_question_ids:
        return {}
    homework_questions = list(
        HomeworkQuestion.objects.select_related(
            "import_job",
            "import_job__content",
            "import_job__content__course",
            "import_job__content__level",
        )
        .filter(
            id__in=homework_question_ids,
            is_active=True,
            assignment__isnull=True,
            import_job__is_active=True,
            import_job__assignment__isnull=True,
            import_job__parse_status=HomeworkImportJob.STATUS_CONFIRMED,
        )
        .order_by("id")
    )
    if not homework_questions:
        return {}
    exam_paper = get_or_create_free_practice_homework_source_exam_paper()
    materialized: dict[int, ExamQuestion] = {}
    existing_questions = ExamQuestion.objects.filter(paper=exam_paper, is_active=True)
    for question in existing_questions:
        snapshot = question.source_snapshot_json if isinstance(question.source_snapshot_json, dict) else {}
        homework_question_id = normalize_positive_int(snapshot.get("homework_question_id"), default=0, minimum=1)
        if homework_question_id:
            materialized[homework_question_id] = question

    next_question_no = (ExamQuestion.objects.filter(paper=exam_paper).aggregate(max_no=Max("question_no"))["max_no"] or 0) + 1
    for homework_question in homework_questions:
        if homework_question.id in materialized:
            continue
        snapshot = decode_sql_ascii_json_text(homework_question.source_snapshot_json)
        if not isinstance(snapshot, dict):
            snapshot = {}
        options = decode_sql_ascii_json_text(homework_question.options_json)
        if not isinstance(options, dict):
            options = {}
        source_snapshot = {
            **snapshot,
            "homework_question_id": homework_question.id,
            "homework_import_job_id": homework_question.import_job_id or 0,
            "free_practice_homework_source": True,
        }
        materialized[homework_question.id] = ExamQuestion.objects.create(
            paper=exam_paper,
            question_no=next_question_no,
            question_type=ExamQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem=homework_question.stem,
            options_json=options,
            correct_answer=str(homework_question.correct_answer or "").strip().upper()[:1],
            analysis=str(homework_question.analysis or "").strip(),
            score=1,
            wrong_point_label=str(source_snapshot.get("knowledge_level_1") or "公共作业题源").strip()[:128],
            source_snapshot_json=source_snapshot,
            is_active=True,
        )
        next_question_no += 1
    return materialized


def materialize_free_practice_bank_questions(bank_question_ids: list[int]) -> dict[int, ExamQuestion]:
    if not bank_question_ids:
        return {}

    bank_questions = list(
        ExamQuestionBankQuestion.objects.select_related("paper")
        .prefetch_related("options", "assets")
        .filter(id__in=bank_question_ids, paper__is_active=True)
        .order_by("paper_id", "question_no", "id")
    )
    bank_questions_by_paper: dict[int, list[ExamQuestionBankQuestion]] = {}
    for bank_question in bank_questions:
        bank_questions_by_paper.setdefault(bank_question.paper_id, []).append(bank_question)

    materialized_questions: dict[int, ExamQuestion] = {}
    for paper_id, paper_questions in bank_questions_by_paper.items():
        bank_paper = paper_questions[0].paper
        materialize_raw_ocr_paper_questions(bank_paper)
        separate_programming_reference_solutions_for_paper(bank_paper)
        exam_paper = get_or_create_free_practice_source_exam_paper(bank_paper)
        sync_exam_questions_from_bank_paper(exam_paper=exam_paper, bank_paper=bank_paper)
        synced_questions = ExamQuestion.objects.select_related("paper", "paper__teacher", "paper__course").filter(
            paper=exam_paper,
            is_active=True,
        )
        for question in synced_questions:
            snapshot = question.source_snapshot_json if isinstance(question.source_snapshot_json, dict) else {}
            bank_question_id = normalize_positive_int(snapshot.get("bank_question_id"), default=0, minimum=1)
            if bank_question_id:
                materialized_questions[bank_question_id] = question
    return materialized_questions


def run_exam_bank_paper_analysis_generation(bank_paper_id: int) -> None:
    if threading.current_thread() is not threading.main_thread():
        close_old_connections()
    error_messages: list[str] = []
    try:
        bank_paper = ExamQuestionBankPaper.objects.get(id=bank_paper_id, is_active=True)
        questions = list(get_exam_bank_paper_analysis_questions(bank_paper).prefetch_related("options", "assets"))
        total_count = len(questions)
        bank_paper.analysis_generation_total_count = total_count
        bank_paper.analysis_generation_done_count = sum(1 for question in questions if str(question.analysis_md or "").strip())
        bank_paper.analysis_generation_failed_count = 0
        bank_paper.save(
            update_fields=[
                "analysis_generation_total_count",
                "analysis_generation_done_count",
                "analysis_generation_failed_count",
                "updated_at",
            ]
        )
        missing_questions = [question for question in questions if not str(question.analysis_md or "").strip()]
        if not missing_questions:
            refresh_exam_bank_paper_analysis_status(bank_paper)
            sync_exam_questions_for_bank_paper_usage(bank_paper)
            return

        worker_count = max(1, min(int(getattr(settings, "EXAM_AI_ANALYSIS_QUESTION_CONCURRENCY", 2)), len(missing_questions), 4))

        def generate_one(question_id: int) -> tuple[int, str, str]:
            should_manage_db_connection = threading.current_thread() is not threading.main_thread()
            if should_manage_db_connection:
                close_old_connections()
            try:
                question = (
                    ExamQuestionBankQuestion.objects.select_related("paper")
                    .prefetch_related("options", "assets")
                    .get(id=question_id, paper_id=bank_paper_id)
                )
                if str(question.analysis_md or "").strip():
                    return question_id, "skipped", ""
                analysis_md = generate_ai_analysis_for_bank_question(question).strip()
                if not analysis_md:
                    return question_id, "failed", "Qwen 返回空解析。"
                question.analysis_md = analysis_md
                question.save(update_fields=["analysis_md", "updated_at"])
                return question_id, "done", ""
            except Exception as exc:  # noqa: BLE001 - background status must capture Qwen errors.
                return question_id, "failed", str(exc)
            finally:
                if should_manage_db_connection:
                    close_old_connections()

        if worker_count == 1:
            question_results = [generate_one(question.id) for question in missing_questions]
        else:
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix=f"exam-analysis-q-{bank_paper_id}") as executor:
                question_results = [future.result() for future in as_completed([executor.submit(generate_one, question.id) for question in missing_questions])]
        for _question_id, result, error_message in question_results:
            if result == "failed" and error_message:
                error_messages.append(error_message)
            latest_paper = ExamQuestionBankPaper.objects.get(id=bank_paper_id)
            latest_questions = list(get_exam_bank_paper_analysis_questions(latest_paper).only("id", "analysis_md"))
            done_count = sum(1 for question in latest_questions if str(question.analysis_md or "").strip())
            latest_paper.analysis_generation_done_count = done_count
            latest_paper.analysis_generation_failed_count = len(error_messages)
            latest_paper.analysis_generation_error = "；".join(error_messages[-3:])[:1000]
            latest_paper.save(
                update_fields=[
                    "analysis_generation_done_count",
                    "analysis_generation_failed_count",
                    "analysis_generation_error",
                    "updated_at",
                ]
            )

        bank_paper = ExamQuestionBankPaper.objects.get(id=bank_paper_id)
        refresh_exam_bank_paper_analysis_status(bank_paper, error_message="；".join(error_messages[-3:]))
        sync_exam_questions_for_bank_paper_usage(bank_paper)
    except Exception:
        logger.exception("exam bank paper analysis generation failed bank_paper_id=%s", bank_paper_id)
        try:
            bank_paper = ExamQuestionBankPaper.objects.get(id=bank_paper_id)
            refresh_exam_bank_paper_analysis_status(bank_paper, error_message="后台解析任务异常，请稍后重试。")
        except Exception:
            logger.exception("failed to update exam bank paper analysis status bank_paper_id=%s", bank_paper_id)
    finally:
        with EXAM_BANK_PAPER_ANALYSIS_LOCK:
            EXAM_BANK_PAPER_ANALYSIS_FUTURES.pop(bank_paper_id, None)
        if threading.current_thread() is not threading.main_thread():
            close_old_connections()


def start_exam_bank_paper_analysis_generation(bank_paper_id: int) -> bool:
    with transaction.atomic():
        bank_paper = ExamQuestionBankPaper.objects.select_for_update().get(id=bank_paper_id, is_active=True)
        if bank_paper.analysis_generation_status not in {
            ExamQuestionBankPaper.ANALYSIS_STATUS_NOT_STARTED,
            ExamQuestionBankPaper.ANALYSIS_STATUS_FAILED,
        }:
            return False
        bank_paper.analysis_generation_status = ExamQuestionBankPaper.ANALYSIS_STATUS_RUNNING
        bank_paper.analysis_generation_started_at = timezone.now()
        bank_paper.analysis_generation_completed_at = None
        bank_paper.analysis_generation_error = ""
        bank_paper.save(
            update_fields=[
                "analysis_generation_status",
                "analysis_generation_started_at",
                "analysis_generation_completed_at",
                "analysis_generation_error",
                "updated_at",
            ]
        )
    with EXAM_BANK_PAPER_ANALYSIS_LOCK:
        future = EXAM_BANK_PAPER_ANALYSIS_EXECUTOR.submit(run_exam_bank_paper_analysis_generation, bank_paper_id)
        EXAM_BANK_PAPER_ANALYSIS_FUTURES[bank_paper_id] = future
    return True


def generate_single_bank_question_analysis(bank_paper: ExamQuestionBankPaper, question_id: int) -> ExamQuestionBankQuestion:
    question = (
        ExamQuestionBankQuestion.objects.select_related("paper")
        .prefetch_related("options", "assets")
        .get(id=question_id, paper=bank_paper)
    )
    if question.question_type not in {
        ExamQuestionBankQuestion.QUESTION_TYPE_SINGLE_CHOICE,
        ExamQuestionBankQuestion.QUESTION_TYPE_TRUE_FALSE,
        ExamQuestionBankQuestion.QUESTION_TYPE_PROGRAMMING,
    }:
        raise ValidationError("当前题型暂不支持单独生成解析。")
    analysis_md = generate_ai_analysis_for_bank_question(question).strip()
    if not analysis_md:
        raise ValidationError("Qwen 返回空解析。")
    question.analysis_md = analysis_md
    question.save(update_fields=["analysis_md", "updated_at"])
    refresh_exam_bank_paper_analysis_status(bank_paper)
    sync_exam_questions_for_bank_paper_usage(bank_paper)
    return question


def sync_exam_questions_from_linked_bank_paper(exam_paper: ExamPaper) -> int:
    bank_paper_id = get_exam_bank_paper_id_from_exam_description(exam_paper.description)
    if not bank_paper_id:
        return 0
    bank_paper = ExamQuestionBankPaper.objects.filter(id=bank_paper_id, is_active=True).first()
    if not bank_paper:
        return 0
    materialize_raw_ocr_paper_questions(bank_paper)
    separate_programming_reference_solutions_for_paper(bank_paper)
    return sync_exam_questions_from_bank_paper(exam_paper=exam_paper, bank_paper=bank_paper)


def parse_exam_management_schedule_from_request(request: HttpRequest) -> dict[str, object]:
    schedule_mode = str(request.POST.get("exam_schedule_mode") or "").strip()
    if schedule_mode not in {"scheduled", "countdown"}:
        raise ExamError("请选择考试模式。")

    if schedule_mode == "scheduled":
        start_at = parse_datetime_local_input(request.POST.get("exam_schedule_start_at"))
        end_at = parse_datetime_local_input(request.POST.get("exam_schedule_end_at"))
        if start_at is None or end_at is None:
            raise ExamError("请设置考试开始时间和结束时间。")
        if end_at <= start_at:
            raise ExamError("考试结束时间必须晚于开始时间。")
        if end_at <= timezone.now():
            raise ExamError("考试结束时间必须晚于当前时间。")
        duration_seconds = max((end_at - start_at).total_seconds(), 60)
        duration_minutes = max(int((duration_seconds + 59) // 60), 1)
        return {
            "schedule_mode": schedule_mode,
            "paper_mode": ExamPaper.MODE_TIMED,
            "start_at": start_at,
            "end_at": end_at,
            "duration_minutes": duration_minutes,
            "start_immediately": False,
        }

    duration_minutes = normalize_positive_int(
        request.POST.get("exam_schedule_duration_minutes"),
        default=0,
        minimum=1,
    )
    if duration_minutes <= 0:
        raise ExamError("请填写考试持续分钟数。")
    now = timezone.now()
    return {
        "schedule_mode": schedule_mode,
        "paper_mode": ExamPaper.MODE_DEADLINE,
        "start_at": None,
        "end_at": now + timedelta(minutes=duration_minutes),
        "duration_minutes": duration_minutes,
        "start_immediately": True,
    }


def apply_exam_management_schedule(
    paper: ExamPaper,
    *,
    schedule: dict[str, object],
) -> ExamPaper:
    start_immediately = bool(schedule.get("start_immediately"))
    paper.mode = str(schedule["paper_mode"])
    paper.start_at = schedule["start_at"]  # type: ignore[assignment]
    paper.end_at = schedule["end_at"]  # type: ignore[assignment]
    paper.duration_minutes = int(schedule["duration_minutes"])
    update_fields = ["mode", "start_at", "end_at", "duration_minutes", "status", "updated_at"]
    if start_immediately:
        paper.status = ExamPaper.STATUS_PUBLISHED
        if not paper.access_code:
            paper.access_code = generate_unique_exam_access_code()
            paper.access_code_generated_at = timezone.now()
            update_fields.extend(["access_code", "access_code_generated_at"])
    elif paper.status != ExamPaper.STATUS_PUBLISHED:
        paper.status = ExamPaper.STATUS_DRAFT
    paper.save(update_fields=update_fields)
    if start_immediately:
        ensure_exam_run_for_paper(paper=paper, teacher=paper.teacher)
    return paper


def create_or_update_exam_management_from_bank_paper(
    *,
    teacher: PortalUser,
    bank_paper: ExamQuestionBankPaper,
    schedule: dict[str, object],
) -> tuple[ExamPaper, bool]:
    ensure_teacher_can_operate_exam_bank_paper(teacher, bank_paper)
    materialize_raw_ocr_paper_questions(bank_paper)
    separate_programming_reference_solutions_for_paper(bank_paper)
    marker = build_exam_bank_paper_publish_marker(bank_paper.id)
    existing_paper = (
        ExamPaper.objects.select_related("course")
        .filter(teacher=teacher, is_active=True, description__contains=marker)
        .order_by("-created_at", "-id")
        .first()
    )
    if existing_paper:
        return existing_paper, False

    subject_title = infer_exam_bank_paper_subject(bank_paper)
    course = Course.objects.filter(title__iexact=subject_title).order_by("id").first()
    description = "\n".join(
        [
            f"来源题库试卷：{bank_paper.title}",
            marker,
            f"source_pdf_id={bank_paper.source_pdf_id}",
            f"level={bank_paper.level}",
            "来源：考试题库快照发布。",
        ]
    )
    exam_paper = ExamPaper.objects.create(
        teacher=teacher,
        course=course,
        title=bank_paper.title,
        description=description,
        mode=str(schedule["paper_mode"]),
        duration_minutes=int(schedule["duration_minutes"]),
        start_at=schedule["start_at"],
        end_at=schedule["end_at"],
        proctoring_enabled=False,
        status=ExamPaper.STATUS_PUBLISHED if schedule.get("start_immediately") else ExamPaper.STATUS_DRAFT,
        is_active=True,
    )
    if schedule.get("start_immediately"):
        exam_paper.access_code = generate_unique_exam_access_code()
        exam_paper.access_code_generated_at = timezone.now()
        exam_paper.save(update_fields=["access_code", "access_code_generated_at", "updated_at"])
        ensure_exam_run_for_paper(paper=exam_paper, teacher=teacher)
    sync_exam_questions_from_bank_paper(exam_paper=exam_paper, bank_paper=bank_paper)
    return exam_paper, True


@role_required("teacher")
def teacher_students(request: HttpRequest) -> HttpResponse:
    active_tab = request.GET.get("tab", "students")
    portal_user = get_portal_user_from_request(request)
    if portal_user.role == PortalUser.ROLE_PRINCIPAL:
        return redirect(f"{reverse('principal-dashboard')}?tab={active_tab}")
    return render_role_page(
        request,
        "teacher",
        build_teacher_page_shell(portal_user, active_tab=active_tab),
    )


@role_required("teacher")
def teacher_exams(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)

    def render_exam_page(*, form_values: dict[str, object] | None = None, error_message: str = "") -> HttpResponse:
        success_message = ""
        op = (request.GET.get("op") or "").strip()
        if op == "created":
            created_count = normalize_positive_int(request.GET.get("count"), default=0, minimum=0)
            success_message = f"考试已发布，已生成 {created_count} 名学生的考试入口。"
        elif op == "bank_imported":
            imported_count = normalize_positive_int(request.GET.get("count"), default=0, minimum=0)
            success_message = f"题库导入成功，已写入 {imported_count} 道考试题。"
        elif op == "access_code_generated":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = (
                ExamPaper.objects.filter(id=paper_id, teacher=portal_user, is_active=True)
                .only("title", "access_code")
                .first()
            )
            if paper and paper.access_code:
                success_message = f"{paper.title} 已开始考试，口令：{paper.access_code}。"
            else:
                success_message = "考试口令已生成。"
        elif op == "exam_schedule_updated":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamPaper.objects.filter(id=paper_id, teacher=portal_user, is_active=True).only("title").first()
            success_message = f"{paper.title} 的考试时间已更新。" if paper else "考试时间已更新。"
        elif op == "deleted":
            success_message = "考试已删除。"
        elif op == "paper_confirmed":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).first()
            success_message = f"{paper.title} 已进入可用试卷列表。" if paper else "试卷已进入可用试卷列表。"
        elif op == "pdf_crop_demo_confirmed":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).first()
            success_message = f"{paper.title} 已由截图预览确认，进入可发布试卷列表。" if paper else "截图试卷已进入可发布试卷列表。"
        elif op == "bank_paper_deleted":
            delete_mode = str(request.GET.get("mode") or "").strip()
            success_message = "未发布试卷已硬删除。" if delete_mode == "hard" else "可用试卷已删除。"
        elif op == "bank_paper_analysis_started":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).only("title").first()
            success_message = f"{paper.title} 已开始后台生成 AI 解析，可继续操作其他试卷。" if paper else "已开始后台生成 AI 解析。"
        elif op == "bank_paper_analysis_running":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).only("title").first()
            success_message = f"{paper.title} 正在生成 AI 解析，请稍后刷新查看状态。" if paper else "这张试卷正在生成 AI 解析。"
        elif op == "bank_paper_analysis_unavailable":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).only("title").first()
            paper_title = paper.title if paper else "这张试卷"
            success_message = f"{paper_title} 当前解析状态不可重复生成；只有未生成或解析失败时才允许点击增加解析。"
        elif op == "bank_paper_knowledge_generated":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).only("title").first()
            paper_title = paper.title if paper else "这张试卷"
            success_message = f"{paper_title} 已开始后台识别知识点，可离开页面，完成后状态会自动更新。"
        elif op == "bank_paper_knowledge_running":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).only("title").first()
            success_message = f"{paper.title} 正在识别知识点，请稍后查看状态。" if paper else "这张试卷正在识别知识点。"
        elif op == "bank_paper_knowledge_unavailable":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).only("title").first()
            paper_title = paper.title if paper else "这张试卷"
            success_message = f"{paper_title} 当前知识点识别状态不可重复识别；只有未识别或识别失败时才允许点击知识点识别。"
        elif op == "knowledge_uploaded":
            imported_count = normalize_positive_int(request.GET.get("count"), default=0, minimum=0)
            success_message = f"知识点 md 文件已识别，已写入 {imported_count} 条目录。"
        elif op == "knowledge_deleted":
            success_message = "知识点目录已删除。"
        elif op == "knowledge_updated":
            success_message = "知识点目录已更新。"
        elif op == "knowledge_created":
            success_message = "知识点已新增。"
        elif op in {"bank_paper_added", "bank_paper_exists"}:
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamPaper.objects.filter(id=paper_id, teacher=portal_user, is_active=True).first()
            if paper and op == "bank_paper_added":
                success_message = f"{paper.title} 已加入考试管理列表。"
            elif paper:
                success_message = f"{paper.title} 已在考试管理列表中，未重复发布。"
            else:
                success_message = "试卷已加入考试管理列表。"
        elif op == "bank_paper_started":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamPaper.objects.filter(id=paper_id, teacher=portal_user, is_active=True).only("title", "access_code").first()
            success_message = f"{paper.title} 已发布并开始考试，口令：{paper.access_code}。" if paper else "考试已发布并开始。"
        elif op == "pdf_crop_demo_error":
            error_message = str(request.GET.get("message") or "PDF 截图切题 Demo 处理失败。").strip()
        context = build_teacher_exam_page_context(
            portal_user,
            form_values=form_values,
            error_message=error_message,
            success_message=success_message,
        )
        return render_shell_page(request, "teacher", "entry/teacher_exams.html", context)

    if request.method == "POST":
        action = (request.POST.get("form_action") or "").strip()
        if action == "start_exam":
            paper_id = normalize_positive_int(request.POST.get("paper_id"), default=0, minimum=1)
            try:
                paper = ExamPaper.objects.get(id=paper_id, teacher=portal_user, is_active=True)
                ensure_teacher_can_operate_exam_paper(portal_user, paper)
                sync_exam_questions_from_linked_bank_paper(paper)
                activated_paper = activate_exam_access_code(paper=paper, teacher=portal_user)
            except (ExamPaper.DoesNotExist, ExamError) as exc:
                return render_exam_page(error_message=str(exc) if str(exc) else "未找到可开始的考试。")
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={"op": "access_code_generated", "paper_id": activated_paper.id},
                )
            )

        if action == "delete_exam":
            paper_id = normalize_positive_int(request.POST.get("paper_id"), default=0, minimum=1)
            try:
                paper = ExamPaper.objects.get(id=paper_id, teacher=portal_user, is_active=True)
                ensure_teacher_can_operate_exam_paper(portal_user, paper)
            except ExamPaper.DoesNotExist:
                return render_exam_page(error_message="未找到可删除的考试。")
            except ExamError as exc:
                return render_exam_page(error_message=str(exc))
            if paper.sessions.filter(is_active=True, status=ExamSession.STATUS_IN_PROGRESS).exists():
                return render_exam_page(error_message="当前正在考试中，不允许删除。")
            paper.is_active = False
            paper.access_code = ""
            paper.save(update_fields=["is_active", "access_code", "updated_at"])
            return redirect(build_redirect_with_query(reverse("teacher-exams"), params={"op": "deleted"}))

        if action == "delete_bank_paper":
            bank_paper_id = normalize_positive_int(request.POST.get("bank_paper_id"), default=0, minimum=1)
            bank_paper = ExamQuestionBankPaper.objects.filter(id=bank_paper_id).first()
            if bank_paper is None:
                return render_exam_page(error_message="未找到可删除的试卷。")
            try:
                ensure_teacher_can_operate_exam_bank_paper(portal_user, bank_paper)
            except ExamError as exc:
                return render_exam_page(error_message=str(exc))
            delete_result = delete_exam_bank_paper_by_usage(bank_paper_id)
            if not delete_result["deleted_count"]:
                return render_exam_page(error_message="未找到可删除的试卷。")
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={"op": "bank_paper_deleted", "mode": delete_result["mode"]},
                    anchor="available-exam-papers",
                )
                )

        if action == "generate_bank_paper_analysis":
            bank_paper_id = normalize_positive_int(request.POST.get("bank_paper_id"), default=0, minimum=1)
            try:
                bank_paper = ExamQuestionBankPaper.objects.get(id=bank_paper_id, is_active=True)
                ensure_teacher_can_operate_exam_bank_paper(portal_user, bank_paper)
                started = start_exam_bank_paper_analysis_generation(bank_paper_id)
            except ExamQuestionBankPaper.DoesNotExist:
                return render_exam_page(error_message="未找到这张可用试卷。")
            except ExamError as exc:
                return render_exam_page(error_message=str(exc))
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={
                        "op": "bank_paper_analysis_started" if started else "bank_paper_analysis_unavailable",
                        "paper_id": bank_paper_id,
                    },
                    anchor="available-exam-papers",
                )
            )

        if action == "generate_bank_paper_knowledge":
            bank_paper_id = normalize_positive_int(request.POST.get("bank_paper_id"), default=0, minimum=1)
            try:
                bank_paper = ExamQuestionBankPaper.objects.get(id=bank_paper_id, is_active=True)
                ensure_teacher_can_operate_exam_bank_paper(portal_user, bank_paper)
                started = start_exam_bank_paper_knowledge_generation(bank_paper_id)
            except ExamQuestionBankPaper.DoesNotExist:
                return render_exam_page(error_message="未找到这张可用试卷。")
            except ExamError as exc:
                return render_exam_page(error_message=str(exc))
            except ExamPaperImportError as exc:
                return render_exam_page(error_message=str(exc))
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={
                        "op": "bank_paper_knowledge_generated" if started else "bank_paper_knowledge_unavailable",
                        "paper_id": bank_paper_id,
                    },
                    anchor="available-exam-papers",
                )
            )

        if action == "upload_exam_knowledge_md":
            try:
                imported_count = import_exam_knowledge_markdown(
                    portal_user=portal_user,
                    subject=request.POST.get("knowledge_subject") or "cpp",
                    category_code=request.POST.get("knowledge_category_code") or "",
                    uploaded_file=request.FILES.get("knowledge_md_file"),
                )
            except ValidationError as exc:
                return render_exam_page(error_message="；".join(exc.messages) if exc.messages else str(exc))
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={"op": "knowledge_uploaded", "count": imported_count},
                    anchor="exam-knowledge-management",
                )
            )

        if action == "create_exam_knowledge_point":
            subject = normalize_exam_knowledge_subject(request.POST.get("knowledge_subject") or "")
            course_level_code = str(request.POST.get("knowledge_course_level_code") or "").strip().upper()
            selected_category_code = str(request.POST.get("knowledge_category_code") or "").strip()
            manual_category_code = str(request.POST.get("knowledge_category_manual") or "").strip()
            category_code = normalize_exam_knowledge_category_code(manual_category_code or selected_category_code)
            level_1 = str(request.POST.get("knowledge_level_1") or "").strip()[:128]
            level_2 = str(request.POST.get("knowledge_level_2") or "").strip()[:128]
            level_3 = str(request.POST.get("knowledge_level_3") or "").strip()[:255]
            if not subject:
                return render_exam_page(error_message="请选择科目。")
            if not teacher_can_operate_exam_subject(portal_user, subject):
                return render_exam_page(error_message="只能给当前老师负责的科目新增知识点。")
            if not course_level_code:
                return render_exam_page(error_message="请选择级别。")
            if not category_code:
                return render_exam_page(error_message="请选择或填写考试名称。")
            if not level_1 or not level_2:
                return render_exam_page(error_message="一级、二级知识点不能为空。")
            existing_max_order = (
                ExamKnowledgePointMap.objects.filter(subject=subject, category_code=category_code)
                .aggregate(max_order=Max("sort_order"))
                .get("max_order")
                or 0
            )
            ExamKnowledgePointMap.objects.update_or_create(
                subject=subject,
                category_code=category_code,
                level_1=level_1,
                level_2=level_2,
                level_3=level_3,
                defaults={
                    "course_level_code": course_level_code,
                    "source_path": "teacher_exam_knowledge_manual",
                    "uploaded_by": portal_user,
                    "sort_order": existing_max_order + 1,
                    "is_active": True,
                },
            )
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={"op": "knowledge_created"},
                    anchor="exam-knowledge-management",
                )
            )

        if action == "delete_exam_knowledge_group":
            knowledge_map_id = normalize_positive_int(request.POST.get("knowledge_map_id"), default=0, minimum=1)
            try:
                root_map = ExamKnowledgePointMap.objects.get(id=knowledge_map_id, is_active=True)
            except ExamKnowledgePointMap.DoesNotExist:
                return render_exam_page(error_message="未找到可删除的知识点目录。")
            ExamKnowledgePointMap.objects.filter(
                subject=root_map.subject,
                course_level_code=root_map.course_level_code,
                category_code=root_map.category_code,
                level_1=root_map.level_1,
                is_active=True,
            ).update(is_active=False)
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={"op": "knowledge_deleted"},
                    anchor="exam-knowledge-management",
                )
            )

        if action == "edit_exam_knowledge_group":
            knowledge_map_id = normalize_positive_int(request.POST.get("knowledge_map_id"), default=0, minimum=1)
            try:
                root_map = ExamKnowledgePointMap.objects.get(id=knowledge_map_id, is_active=True)
            except ExamKnowledgePointMap.DoesNotExist:
                return render_exam_page(error_message="未找到可编辑的知识点目录。")
            new_subject = normalize_exam_knowledge_subject(request.POST.get("knowledge_subject") or root_map.subject)
            new_category_code = normalize_exam_knowledge_category_code(request.POST.get("knowledge_category_code") or root_map.category_code)
            new_course_level_code = (
                derive_exam_knowledge_course_level_code(new_category_code)
                or root_map.course_level_code
            )
            new_level_1 = str(request.POST.get("knowledge_level_1") or "").strip()[:128]
            if not new_subject:
                return render_exam_page(error_message="请选择科目。")
            if not new_category_code:
                return render_exam_page(error_message="请选择考试名称。")
            if not new_level_1:
                return render_exam_page(error_message="一级知识目录不能为空。")
            group_rows = list(
                ExamKnowledgePointMap.objects.filter(
                    subject=root_map.subject,
                    course_level_code=root_map.course_level_code,
                    category_code=root_map.category_code,
                    level_1=root_map.level_1,
                    is_active=True,
                ).order_by("sort_order", "id")
            )
            with transaction.atomic():
                for row in group_rows:
                    duplicate = (
                        ExamKnowledgePointMap.objects.filter(
                            subject=new_subject,
                            category_code=new_category_code,
                            level_1=new_level_1,
                            level_2=row.level_2,
                            level_3=row.level_3,
                        )
                        .exclude(id=row.id)
                        .first()
                    )
                    if duplicate:
                        duplicate.course_level_code = new_course_level_code
                        duplicate.source_path = row.source_path
                        duplicate.sort_order = row.sort_order
                        duplicate.is_active = True
                        duplicate.uploaded_by = portal_user
                        duplicate.save(update_fields=["course_level_code", "source_path", "sort_order", "is_active", "uploaded_by", "updated_at"])
                        row.is_active = False
                        row.save(update_fields=["is_active", "updated_at"])
                        continue
                    row.subject = new_subject
                    row.category_code = new_category_code
                    row.course_level_code = new_course_level_code
                    row.level_1 = new_level_1
                    row.uploaded_by = portal_user
                    row.save(update_fields=["subject", "category_code", "course_level_code", "level_1", "uploaded_by", "updated_at"])
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={"op": "knowledge_updated"},
                    anchor="exam-knowledge-management",
                )
            )

        if action == "create_exam_from_bank_paper":
            bank_paper_id = normalize_positive_int(request.POST.get("bank_paper_id"), default=0, minimum=1)
            try:
                bank_paper = ExamQuestionBankPaper.objects.get(id=bank_paper_id, is_active=True)
                schedule = parse_exam_management_schedule_from_request(request)
                exam_paper, created = create_or_update_exam_management_from_bank_paper(
                    teacher=portal_user,
                    bank_paper=bank_paper,
                    schedule=schedule,
                )
            except ExamQuestionBankPaper.DoesNotExist:
                return render_exam_page(error_message="未找到这张可用试卷。")
            except ExamError as exc:
                return render_exam_page(error_message=str(exc))
            op = "bank_paper_started" if created and schedule.get("start_immediately") else ("bank_paper_added" if created else "bank_paper_exists")
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={"op": op, "paper_id": exam_paper.id},
                    anchor="teacher-exam-management",
                )
            )

        if action == "update_exam_schedule":
            paper_id = normalize_positive_int(request.POST.get("paper_id"), default=0, minimum=1)
            try:
                paper = ExamPaper.objects.get(id=paper_id, teacher=portal_user, is_active=True)
                ensure_teacher_can_operate_exam_paper(portal_user, paper)
                schedule = parse_exam_management_schedule_from_request(request)
                apply_exam_management_schedule(paper, schedule=schedule)
            except ExamPaper.DoesNotExist:
                return render_exam_page(error_message="未找到可编辑的考试。")
            except ExamError as exc:
                return render_exam_page(error_message=str(exc))
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={"op": "exam_schedule_updated", "paper_id": paper.id},
                    anchor="teacher-exam-management",
                )
            )

        if action == "import_question_bank":
            selected_course_id = normalize_positive_int(request.POST.get("exam_bank_course_id"), default=0, minimum=1)
            source_file = request.FILES.get("exam_bank_json_file")
            if not source_file:
                return render_exam_page(error_message="请先选择题库 JSON 文件。")
            if not source_file.name.lower().endswith(".json"):
                return render_exam_page(error_message="当前导入入口只接受 .json 文件。")
            try:
                imported_count = import_exam_question_bank_items_from_json(
                    teacher=portal_user,
                    course_id=selected_course_id,
                    uploaded_file=source_file,
                )
            except ValidationError as exc:
                return render_exam_page(error_message="；".join(exc.messages) if exc.messages else str(exc))
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exams"),
                    params={"op": "bank_imported", "count": imported_count},
                )
            )

        selected_course_id = normalize_positive_int(request.POST.get("exam_course_id"), default=0, minimum=1)
        selected_student_ids = normalize_positive_int_list(request.POST.getlist("student_ids"))
        selected_question_ids = normalize_positive_int_list(request.POST.getlist("question_bank_item_ids"))
        mode = request.POST.get("exam_mode", ExamPaper.MODE_TIMED).strip()
        title = request.POST.get("exam_title", "").strip()
        description = normalize_preserved_multiline_text(request.POST.get("exam_description", "")).strip()
        duration_minutes = normalize_positive_int(request.POST.get("exam_duration_minutes"), default=45, minimum=1)
        start_at_raw = request.POST.get("exam_start_at", "").strip()
        end_at_raw = request.POST.get("exam_end_at", "").strip()
        start_at = parse_datetime_local_input(start_at_raw)
        end_at = parse_datetime_local_input(end_at_raw)
        proctoring_enabled = request.POST.get("exam_proctoring_enabled") == "on"
        form_values = {
            "course_id": selected_course_id,
            "student_ids": selected_student_ids,
            "question_bank_item_ids": selected_question_ids,
            "mode": mode,
            "title": title,
            "description": description,
            "duration_minutes": duration_minutes,
            "start_at": start_at_raw,
            "end_at": end_at_raw,
            "proctoring_enabled": proctoring_enabled,
        }
        try:
            paper = create_exam_for_students(
                teacher=portal_user,
                student_ids=selected_student_ids,
                course_id=selected_course_id,
                title=title,
                description=description,
                mode=mode,
                start_at=start_at,
                end_at=end_at,
                duration_minutes=duration_minutes,
                proctoring_enabled=proctoring_enabled,
                question_bank_item_ids=selected_question_ids,
            )
        except (ExamError, ValidationError) as exc:
            return render_exam_page(form_values=form_values, error_message=str(exc))
        return redirect(
            build_redirect_with_query(
                reverse("teacher-exams"),
                params={"op": "created", "count": paper.sessions.filter(is_active=True).count()},
            )
        )

    return render_exam_page()


@role_required("teacher")
def teacher_exam_knowledge_detail(request: HttpRequest, map_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    root_map = ExamKnowledgePointMap.objects.filter(id=map_id, is_active=True).first()
    if not root_map:
        raise Http404("未找到知识点目录。")

    def group_queryset():
        return ExamKnowledgePointMap.objects.filter(
            subject=root_map.subject,
            course_level_code=root_map.course_level_code,
            category_code=root_map.category_code,
            level_1=root_map.level_1,
            is_active=True,
        ).order_by("sort_order", "level_1", "level_2", "level_3", "id")

    def redirect_self(op: str = "") -> HttpResponse:
        params = {"op": op} if op else {}
        return redirect(build_redirect_with_query(reverse("teacher-exam-knowledge-detail", args=[root_map.id]), params=params))

    error_message = ""
    if request.method == "POST":
        action = (request.POST.get("form_action") or "").strip()
        if action == "delete_exam_knowledge_row":
            row_id = normalize_positive_int(request.POST.get("knowledge_row_id"), default=0, minimum=1)
            row = group_queryset().filter(id=row_id).first()
            if not row:
                error_message = "未找到可删除的知识点。"
            else:
                row.is_active = False
                row.save(update_fields=["is_active", "updated_at"])
                return redirect_self("deleted")
        elif action in {"add_exam_knowledge_row", "edit_exam_knowledge_row"}:
            row_id = normalize_positive_int(request.POST.get("knowledge_row_id"), default=0, minimum=1)
            level_1 = str(request.POST.get("level_1") or "").strip()[:128]
            level_2 = str(request.POST.get("level_2") or "").strip()[:128]
            level_3 = str(request.POST.get("level_3") or "").strip()[:255]
            if not level_1 or not level_2:
                error_message = "一级目录和二级目录不能为空。"
            elif action == "edit_exam_knowledge_row":
                row = group_queryset().filter(id=row_id).first()
                if not row:
                    error_message = "未找到可编辑的知识点。"
                else:
                    duplicate = (
                        ExamKnowledgePointMap.objects.filter(
                            subject=root_map.subject,
                            category_code=root_map.category_code,
                            level_1=level_1,
                            level_2=level_2,
                            level_3=level_3,
                        )
                        .exclude(id=row.id)
                        .first()
                    )
                    if duplicate:
                        duplicate.course_level_code = root_map.course_level_code
                        duplicate.is_active = True
                        duplicate.uploaded_by = portal_user
                        duplicate.save(update_fields=["course_level_code", "is_active", "uploaded_by", "updated_at"])
                        row.is_active = False
                        row.save(update_fields=["is_active", "updated_at"])
                    else:
                        row.level_1 = level_1
                        row.level_2 = level_2
                        row.level_3 = level_3
                        row.uploaded_by = portal_user
                        row.save(update_fields=["level_1", "level_2", "level_3", "uploaded_by", "updated_at"])
                    return redirect_self("updated")
            else:
                max_order = (
                    ExamKnowledgePointMap.objects.filter(subject=root_map.subject, category_code=root_map.category_code)
                    .aggregate(max_order=Max("sort_order"))
                    .get("max_order")
                    or 0
                )
                created_row, _ = ExamKnowledgePointMap.objects.update_or_create(
                    subject=root_map.subject,
                    category_code=root_map.category_code,
                    level_1=level_1,
                    level_2=level_2,
                    level_3=level_3,
                    defaults={
                        "course_level_code": root_map.course_level_code,
                        "source_path": "manual",
                        "sort_order": max_order + 1,
                        "is_active": True,
                        "uploaded_by": portal_user,
                    },
                )
                return redirect(build_redirect_with_query(reverse("teacher-exam-knowledge-detail", args=[created_row.id]), params={"op": "created"}))

    success_message = ""
    op = (request.GET.get("op") or "").strip()
    if op == "created":
        success_message = "知识点目录已新增。"
    elif op == "updated":
        success_message = "知识点目录已更新。"
    elif op == "deleted":
        success_message = "知识点目录已删除。"

    rows = []
    for item in group_queryset():
        rows.append(
            {
                "id": item.id,
                "category_code": item.category_code,
                "level_1": item.level_1,
                "level_2": item.level_2,
                "level_3": item.level_3,
                "operator_name": (item.uploaded_by.full_name or item.uploaded_by.username) if item.uploaded_by_id and item.uploaded_by else "未记录",
                "updated_at_text": timezone.localtime(item.updated_at).strftime("%Y-%m-%d %H:%M") if item.updated_at else "",
                "search_text": " ".join([item.category_code, item.level_1, item.level_2, item.level_3]),
            }
        )

    subject_title = format_knowledge_map_subject_label(root_map.subject)
    context = {
        "page_title": "知识点详情",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "考试管理", "href": f"{reverse('teacher-exams')}#exam-knowledge-management"},
            {"label": "知识点详情"},
        ],
        "back_href": f"{reverse('teacher-exams')}#exam-knowledge-management",
        "subject_title": subject_title,
        "course_level_text": root_map.course_level_code or "未设置",
        "category_code": root_map.category_code,
        "level_1": root_map.level_1,
        "knowledge_detail_rows": rows,
        "success_message": success_message,
        "error_message": error_message,
        "default_form_values": {
            "level_1": root_map.level_1,
            "level_2": "",
            "level_3": "",
        },
        "empty_message": "当前一级目录下还没有二级、三级知识点。",
    }
    return render_shell_page(request, "teacher", "entry/teacher_exam_knowledge_detail.html", context)


@role_required("teacher")
def teacher_exam_paper_new(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    upload_error_message = ""
    upload_success_message = ""
    uploaded_pdf_name = ""
    uploaded_pdf_size = 0
    uploaded_pdf_metadata = {"year": "", "month": "", "source_pdf_id": "", "title": ""}
    selected_course_id = normalize_positive_int(request.POST.get("course_id"), default=0, minimum=1)
    selected_level_code = str(request.POST.get("level_code") or "").strip()
    courses = list(Course.objects.order_by("title", "id").values("id", "title"))
    if not selected_course_id and request.method != "POST" and courses:
        teacher_default_course = next(
            (
                course
                for course in courses
                if portal_user.role != PortalUser.ROLE_PRINCIPAL
                and teacher_can_operate_exam_subject(portal_user, course["title"])
            ),
            None,
        )
        selected_course_id = int((teacher_default_course or courses[0])["id"])
    feedback_job_id = normalize_positive_int(request.GET.get("job_id"), default=0, minimum=1)
    feedback_op = str(request.GET.get("op") or "").strip()
    if feedback_op == "deleted":
        deleted_count = normalize_positive_int(request.GET.get("count"), default=0, minimum=0)
        upload_success_message = f"已删除 {deleted_count} 条识别任务。"
    elif feedback_op == "bank_paper_deleted":
        deleted_count = normalize_positive_int(request.GET.get("count"), default=0, minimum=0)
        delete_mode = str(request.GET.get("mode") or "").strip()
        if delete_mode == "hard":
            upload_success_message = f"已硬删除 {deleted_count} 张未发布试卷，可重新上传识别。"
        else:
            upload_success_message = f"已删除 {deleted_count} 张已发布试卷，可在“已删除试卷”中恢复。"
    elif feedback_op == "bank_paper_restored":
        restored_count = normalize_positive_int(request.GET.get("count"), default=0, minimum=0)
        upload_success_message = f"已恢复 {restored_count} 张试卷。"
    elif feedback_op == "restore_available":
        upload_success_message = "这份文件对应的试卷已入库但当前处于已删除状态，请在下方“已删除试卷”中点击恢复。"
    elif feedback_op == "pdf_crop_demo_confirmed":
        paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
        paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).first()
        upload_success_message = f"{paper.title} 已确认入库，已进入已入库试卷列表。" if paper else "截图试卷已确认入库。"
    elif feedback_op == "pdf_crop_demo_error":
        upload_error_message = str(request.GET.get("message") or "PDF 截图切题处理失败。").strip()
    elif feedback_job_id and feedback_op in {"queued", "existing"}:
        feedback_job = (
            ExamQuestionBankImportJob.objects.filter(teacher=portal_user, is_active=True, id=feedback_job_id)
            .select_related("course")
            .first()
        )
        if feedback_job is not None:
            if feedback_op == "existing":
                upload_success_message = f"已存在相同文件的识别任务 #{feedback_job.id}，没有重复创建记录。"
            else:
                upload_success_message = f"已创建考试文件导入任务 #{feedback_job.id}，后台会按文件类型自动处理。"

    if request.method == "POST":
        action = str(request.POST.get("form_action") or "").strip()
        if action == "delete_bank_paper":
            bank_paper_id = normalize_positive_int(request.POST.get("bank_paper_id"), default=0, minimum=1)
            delete_result = delete_exam_bank_paper_by_usage(bank_paper_id)
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exam-paper-new"),
                    params={
                        "op": "bank_paper_deleted",
                        "count": delete_result["deleted_count"],
                        "mode": delete_result["mode"],
                    },
                    anchor="confirmed-bank-papers",
                )
            )
        if action == "restore_bank_paper":
            bank_paper_id = normalize_positive_int(request.POST.get("bank_paper_id"), default=0, minimum=1)
            restored_count = 0
            if exam_bank_paper_has_exam_management_record(bank_paper_id):
                restored_count = ExamQuestionBankPaper.objects.filter(id=bank_paper_id, is_active=False).update(
                    is_active=True,
                    updated_at=timezone.now(),
                )
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exam-paper-new"),
                    params={"op": "bank_paper_restored", "count": restored_count},
                    anchor="confirmed-bank-papers",
                )
            )
        if action == "delete_import_job":
            import_job_id = normalize_positive_int(request.POST.get("import_job_id"), default=0, minimum=1)
            deleted_count = ExamQuestionBankImportJob.objects.filter(
                id=import_job_id,
                teacher=portal_user,
                is_active=True,
            ).update(is_active=False, updated_at=timezone.now())
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exam-paper-new"),
                    params={"op": "deleted", "count": deleted_count},
                    anchor="recent-import-jobs",
                )
            )
        if action == "bulk_delete_import_jobs":
            import_job_ids = normalize_positive_int_list(request.POST.getlist("import_job_ids"))
            deleted_count = 0
            if import_job_ids:
                deleted_count = ExamQuestionBankImportJob.objects.filter(
                    id__in=import_job_ids,
                    teacher=portal_user,
                    is_active=True,
                ).update(is_active=False, updated_at=timezone.now())
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exam-paper-new"),
                    params={"op": "deleted", "count": deleted_count},
                    anchor="recent-import-jobs",
                )
            )
        if action == "confirm_import_job":
            import_job_id = normalize_positive_int(request.POST.get("import_job_id"), default=0, minimum=1)
            import_job = (
                ExamQuestionBankImportJob.objects.select_related("course")
                .filter(id=import_job_id, teacher=portal_user, is_active=True)
                .first()
            )
            if import_job is None:
                upload_error_message = "未找到可确认的识别任务。"
            else:
                try:
                    manual_choice_payloads = collect_manual_choice_review_payloads(
                        request,
                        required=is_manual_choice_review_import(import_job),
                    )
                    paper, _summary = confirm_exam_question_bank_import_job(
                        import_job,
                        manual_choice_payloads=manual_choice_payloads,
                    )
                except (ExamPaperImportConfirmError, ExamQuestionBankPaper.DoesNotExist) as exc:
                    upload_error_message = str(exc) or "确认入库失败。"
                else:
                    return redirect(
                        build_redirect_with_query(
                            reverse("teacher-exams"),
                            params={"op": "paper_confirmed", "paper_id": paper.id},
                            anchor="available-exam-papers",
                        )
                    )

        source_file = None if action else request.FILES.get("source_pdf")
        if source_file is None:
            if not action:
                upload_error_message = "请先选择一份 PDF 文件。"
        else:
            uploaded_pdf_name = source_file.name
            uploaded_pdf_size = int(getattr(source_file, "size", 0) or 0)
            selected_course_title = (
                Course.objects.filter(id=selected_course_id).values_list("title", flat=True).first() or ""
            )
            uploaded_pdf_metadata = infer_exam_pdf_metadata_from_filename(
                source_file.name,
                level_code=selected_level_code,
                course_title=selected_course_title,
            )
            source_type = detect_exam_import_source_type(source_file.name)
            if not source_type:
                upload_error_message = "当前新增试卷入口支持 pdf / png / jpg / webp / txt / md / json / html / docx 文件。"
            else:
                use_qwen_ocr = request.POST.get("use_qwen_ocr") == "on"
                selected_course = Course.objects.filter(id=selected_course_id).first()
                if selected_course is None:
                    upload_error_message = "请选择所属学科。"
                elif not selected_level_code:
                    upload_error_message = "请选择所属级别/类别。"
                else:
                    if source_type == "pdf" and not use_qwen_ocr:
                        today = timezone.localdate()
                        subject = map_course_title_to_pdf_crop_demo_subject(selected_course.title)
                        exam_type = map_level_code_to_pdf_crop_demo_exam_type(selected_level_code)
                        metadata_year = normalize_positive_int(uploaded_pdf_metadata.get("year"), default=today.year, minimum=2000)
                        metadata_month = normalize_pdf_crop_demo_month(uploaded_pdf_metadata.get("month") or today.month)
                        restore_source_pdf_id = str(uploaded_pdf_metadata.get("source_pdf_id") or "").strip()
                        if restore_source_pdf_id:
                            inactive_bank_paper = ExamQuestionBankPaper.objects.filter(
                                source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
                                source_pdf_id=restore_source_pdf_id,
                                is_active=False,
                            ).first()
                            if inactive_bank_paper is not None and exam_bank_paper_has_exam_management_record(inactive_bank_paper.id):
                                return redirect(
                                    build_redirect_with_query(
                                        reverse("teacher-exam-paper-new"),
                                        params={"op": "restore_available", "paper_id": inactive_bank_paper.id},
                                        anchor="deleted-bank-papers",
                                    )
                                )
                        if subject not in PDF_CROP_DEMO_SUBJECT_CHOICES:
                            upload_error_message = "截图切题当前只支持 C++ 学科；其他学科请勾选 Qwen OCR 识别。"
                        elif not exam_type:
                            upload_error_message = "截图切题当前支持 C++ 的 GESP1-4 / CSP-J / CSP-S；其他级别请勾选 Qwen OCR 识别。"
                        elif not metadata_month:
                            upload_error_message = "无法识别有效月份，请调整文件名后重新上传。"
                        else:
                            try:
                                upload_title = str(request.POST.get("title") or uploaded_pdf_metadata["title"] or source_file.name).strip()
                                crop_mode = (
                                    "csp_j_round1"
                                    if should_use_csp_j_round1_crop_mode(
                                        subject=subject,
                                        exam_type=exam_type,
                                        title=upload_title,
                                        filename=source_file.name,
                                    )
                                    else ""
                                )
                                session_id = create_pdf_crop_demo_session_from_upload(
                                    uploaded_file=source_file,
                                    subject=subject,
                                    exam_type=exam_type,
                                    year=metadata_year,
                                    month=metadata_month,
                                    scale=normalize_pdf_crop_demo_scale(request.POST.get("demo_scale")),
                                    crop_mode=crop_mode,
                                )
                            except ValidationError as exc:
                                upload_error_message = "；".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                            else:
                                return redirect(reverse("teacher-exam-pdf-crop-demo", args=[session_id]))
                    if upload_error_message:
                        pass
                    else:
                        source_sha256 = compute_uploaded_file_sha256(source_file)
                        today = timezone.localdate()
                        file_stem_slug = slugify(Path(source_file.name).stem) or re.sub(
                            r"[^0-9a-zA-Z_]+",
                            "_",
                            Path(source_file.name).stem,
                        ).strip("_").lower()
                        metadata_has_date = bool(uploaded_pdf_metadata["year"] and uploaded_pdf_metadata["month"])
                        uploaded_pdf_metadata["year"] = uploaded_pdf_metadata["year"] or str(today.year)
                        uploaded_pdf_metadata["month"] = uploaded_pdf_metadata["month"] or str(today.month)
                        if not metadata_has_date:
                            uploaded_pdf_metadata["source_pdf_id"] = ""
                        uploaded_pdf_metadata["source_pdf_id"] = uploaded_pdf_metadata["source_pdf_id"] or "_".join(
                            [
                                uploaded_pdf_metadata["year"],
                                uploaded_pdf_metadata["month"],
                                file_stem_slug[:64] or source_sha256[:12],
                            ]
                        )
                        reusable_statuses = [
                            ExamQuestionBankImportJob.STATUS_UPLOADED,
                            ExamQuestionBankImportJob.STATUS_RENDERING,
                            ExamQuestionBankImportJob.STATUS_OCR_RUNNING,
                            ExamQuestionBankImportJob.STATUS_OCR_DONE,
                            ExamQuestionBankImportJob.STATUS_IMPORTED,
                        ]
                        existing_job = (
                            ExamQuestionBankImportJob.objects.filter(
                                teacher=portal_user,
                                course=selected_course,
                                level_code__iexact=selected_level_code,
                                source_pdf_id=uploaded_pdf_metadata["source_pdf_id"],
                                source_sha256=source_sha256,
                                is_active=True,
                                status__in=reusable_statuses,
                            )
                            .order_by("-created_at", "-id")
                            .first()
                        )
                        if existing_job is not None:
                            inactive_bank_paper = ExamQuestionBankPaper.objects.filter(
                                source=ExamQuestionBankPaper.SOURCE_LOCAL_OCR,
                                source_pdf_id=uploaded_pdf_metadata["source_pdf_id"],
                                is_active=False,
                            ).first()
                            if inactive_bank_paper is not None and exam_bank_paper_has_exam_management_record(inactive_bank_paper.id):
                                return redirect(
                                    build_redirect_with_query(
                                        reverse("teacher-exam-paper-new"),
                                        params={"op": "restore_available", "paper_id": inactive_bank_paper.id},
                                        anchor="deleted-bank-papers",
                                    )
                                )
                            if inactive_bank_paper is not None:
                                ExamQuestionBankImportJob.objects.filter(id=existing_job.id).update(
                                    is_active=False,
                                    updated_at=timezone.now(),
                                )
                            else:
                                return redirect(
                                    build_redirect_with_query(
                                        reverse("teacher-exam-paper-new"),
                                        params={"op": "existing", "job_id": existing_job.id},
                                        anchor="recent-import-jobs",
                                    )
                                )
                        import_job = ExamQuestionBankImportJob.objects.create(
                            teacher=portal_user,
                            course=selected_course,
                            level_code=selected_level_code,
                            title=str(request.POST.get("title") or uploaded_pdf_metadata["title"] or source_file.name).strip(),
                            year=int(uploaded_pdf_metadata["year"]),
                            month=int(uploaded_pdf_metadata["month"]),
                            source_pdf_id=uploaded_pdf_metadata["source_pdf_id"],
                            source_pdf=source_file,
                            source_filename=source_file.name,
                            source_sha256=source_sha256,
                            status=ExamQuestionBankImportJob.STATUS_UPLOADED,
                            status_notes="文件已上传，等待后台处理。",
                            qwen_model=str(getattr(settings, "QWEN_OCR_MODEL", "") or getattr(settings, "HOMEWORK_LLM_MODEL", "") or ""),
                            qwen_base_url=str(getattr(settings, "QWEN_BASE_URL", "") or getattr(settings, "HOMEWORK_LLM_API_URL", "") or ""),
                        )
                        route_note = "文本类文件将直接本地转换为 Markdown。" if source_type not in {"pdf", "image"} else "后台将渲染截图并调用 Qwen OCR。"
                        import_job.status_notes = f"文件已上传，等待后台处理。{route_note}"
                        import_job.save(update_fields=["status_notes", "updated_at"])
                        return redirect(
                            build_redirect_with_query(
                                reverse("teacher-exam-paper-new"),
                                params={"op": "queued", "job_id": import_job.id},
                                anchor="recent-import-jobs",
                            )
                        )

    levels = list(
        CourseLevel.objects.select_related("category", "category__course")
        .filter(is_active=True, category__is_active=True)
        .order_by("category__course__title", "category__sort_order", "sort_order", "id")
    )
    level_options = [
        {
            "id": item.id,
            "label": item.code,
            "value": item.code,
            "course_id": item.category.course_id,
        }
        for item in levels
    ]
    level_options_by_course_id: dict[str, list[dict[str, str]]] = {
        str(item["id"]): [] for item in courses
    }
    for item in level_options:
        course_id = str(item["course_id"])
        course_levels = level_options_by_course_id.setdefault(course_id, [])
        if not any(existing["value"].upper() == str(item["value"]).upper() for existing in course_levels):
            course_levels.append({"label": str(item["label"]), "value": str(item["value"])})

    for course in courses:
        if str(course["title"]).strip().lower() != "c++":
            continue
        course_id = str(course["id"])
        course_levels = level_options_by_course_id.setdefault(course_id, [])
        existing_level_values = {item["value"].upper() for item in course_levels}
        for fallback_level in ["CSP-J", "CSP-S"]:
            if fallback_level not in existing_level_values:
                course_levels.append({"label": fallback_level, "value": fallback_level})
    qwen_key_configured = bool(
        str(getattr(settings, "QWEN_API_KEY", "") or "").strip()
        or str(getattr(settings, "DASHSCOPE_API_KEY", "") or "").strip()
    )
    qwen_base_url = str(
        getattr(settings, "QWEN_BASE_URL", "")
        or getattr(settings, "HOMEWORK_LLM_API_URL", "")
        or ""
    ).strip()
    qwen_model = str(
        getattr(settings, "QWEN_OCR_MODEL", "")
        or getattr(settings, "HOMEWORK_LLM_MODEL", "")
        or ""
    ).strip()
    recent_import_job_rows = get_teacher_exam_import_job_rows(portal_user)
    confirmed_bank_paper_rows = get_confirmed_exam_bank_paper_rows()
    deleted_bank_paper_rows = get_deleted_exam_bank_paper_rows()

    context = {
        "page_title": "新增试卷",
        "page_description": "上传 PDF 后生成考试题库试卷快照。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "考试管理", "href": reverse("teacher-exams")},
            {"label": "新增试卷"},
        ],
        "course_options": courses,
        "level_options_by_course_id": level_options_by_course_id,
        "selected_course_id": selected_course_id,
        "selected_level_code": selected_level_code,
        "upload_error_message": upload_error_message,
        "upload_success_message": upload_success_message,
        "uploaded_pdf_name": uploaded_pdf_name,
        "uploaded_pdf_size": uploaded_pdf_size,
        "uploaded_pdf_metadata": uploaded_pdf_metadata,
        "exam_import_accept": ",".join(sorted(get_supported_exam_import_extensions())),
        "recent_import_job_rows": recent_import_job_rows,
        "confirmed_bank_paper_rows": confirmed_bank_paper_rows,
        "deleted_bank_paper_rows": deleted_bank_paper_rows,
        "import_job_status_url": reverse("teacher-exam-paper-import-jobs-status"),
        "qwen_check_items": [
            {
                "label": "Qwen API Key",
                "value": "已配置" if qwen_key_configured else "未配置",
                "tone": "success" if qwen_key_configured else "warning",
            },
            {
                "label": "Qwen Base URL",
                "value": qwen_base_url or "未配置",
                "tone": "success" if qwen_base_url else "warning",
            },
            {
                "label": "OCR Model",
                "value": qwen_model or "未配置",
                "tone": "success" if qwen_model else "warning",
            },
            {
                "label": "PDF Renderer",
                "value": "当前项目已有 pypdfium2；Hermes 建议 PyMuPDF 300 DPI",
                "tone": "warning",
            },
        ],
        "back_href": reverse("teacher-exams"),
    }
    return render_shell_page(request, "teacher", "entry/teacher_exam_paper_new.html", context)


@role_required("teacher")
def teacher_exam_paper_import_jobs_status(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    return JsonResponse({"jobs": get_teacher_exam_import_job_rows(portal_user)})


@role_required("teacher")
def teacher_exam_bank_paper_status(request: HttpRequest) -> JsonResponse:
    portal_user = get_portal_user_from_request(request)
    current_teacher_name = portal_user.full_name or portal_user.username
    published_bank_paper_ids = get_exam_bank_paper_ids_with_exam_management_records()
    current_teacher_published_bank_paper_ids = get_exam_bank_paper_ids_with_exam_management_records(portal_user)
    papers = list(
        ExamQuestionBankPaper.objects.filter(is_active=True)
        .prefetch_related("questions")
        .order_by("-year", "-month", "level", "source_pdf_id", "id")
    )
    rows = [
        serialize_available_exam_bank_paper(
            paper,
            publisher_name=current_teacher_name,
            published_bank_paper_ids=published_bank_paper_ids,
        )
        for paper in papers
    ]
    for row in rows:
        subject_allowed = teacher_can_operate_exam_subject(portal_user, row.get("subject_title"))
        subject_restricted_reason = "" if subject_allowed else "这张试卷不属于当前老师负责的学科，不能操作。"
        row["can_operate_subject"] = subject_allowed
        row["subject_operation_disabled_reason"] = subject_restricted_reason
        row["can_generate_analysis"] = bool(row.get("can_generate_analysis")) and subject_allowed
        if subject_restricted_reason:
            row["analysis_action_disabled_reason"] = subject_restricted_reason
        row["can_generate_knowledge"] = bool(row.get("can_generate_knowledge")) and subject_allowed
        if subject_restricted_reason:
            row["knowledge_action_disabled_reason"] = subject_restricted_reason
        row["can_publish_to_exam_management"] = (
            subject_allowed
            and int(row["id"]) not in current_teacher_published_bank_paper_ids
        )
        if not subject_allowed:
            row["publish_disabled_reason"] = subject_restricted_reason
            row["delete_disabled_reason"] = subject_restricted_reason
            row["edit_disabled_reason"] = subject_restricted_reason
        elif int(row["id"]) in current_teacher_published_bank_paper_ids:
            row["publish_disabled_reason"] = "已在试卷管理中，删除后可再次发布"
        else:
            row["publish_disabled_reason"] = ""
            row["delete_disabled_reason"] = ""
            row["edit_disabled_reason"] = ""
    return JsonResponse({"rows": rows})


@role_required("teacher")
def teacher_exam_paper_import_job_detail(request: HttpRequest, import_job_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    import_job = (
        ExamQuestionBankImportJob.objects.select_related("course")
        .filter(id=import_job_id, teacher=portal_user, is_active=True)
        .first()
    )
    if import_job is None:
        raise Http404("未找到该识别任务")

    preview_rows = build_exam_import_job_preview_rows(import_job)
    manual_choice_review_enabled = is_manual_choice_review_import(import_job)
    can_confirm = import_job.status == ExamQuestionBankImportJob.STATUS_OCR_DONE
    is_imported = import_job.status == ExamQuestionBankImportJob.STATUS_IMPORTED
    scratch_docx_preview_sections = build_scratch_docx_import_question_preview(import_job)
    source_type = detect_exam_import_source_type(import_job.source_filename or import_job.source_pdf.name)
    is_text_source_import = bool(source_type and source_type not in {"pdf", "image"})
    status_text = import_job.get_status_display()
    if is_text_source_import and import_job.status == ExamQuestionBankImportJob.STATUS_OCR_DONE:
        status_text = "文本转换完成"
    elif is_text_source_import and import_job.status == ExamQuestionBankImportJob.STATUS_RENDERING:
        status_text = "文本转换中"
    context = {
        "page_title": f"识别预览 #{import_job.id}",
        "page_description": "先核对文本转换结果，确认后再进入结构化解析和试卷入库。" if is_text_source_import else "先核对 Qwen OCR 的逐页识别结果，确认后再进入结构化解析和试卷入库。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "考试管理", "href": reverse("teacher-exams")},
            {"label": "新增试卷", "href": reverse("teacher-exam-paper-new")},
            {"label": f"识别预览 #{import_job.id}"},
        ],
        "import_job": import_job,
        "is_text_source_import": is_text_source_import,
        "course_title": import_job.course.title if import_job.course_id and import_job.course else "未绑定学科",
        "status_text": status_text,
        "created_at_text": timezone.localtime(import_job.created_at).strftime("%Y-%m-%d %H:%M"),
        "updated_at_text": timezone.localtime(import_job.updated_at).strftime("%Y-%m-%d %H:%M"),
        "preview_rows": preview_rows,
        "scratch_docx_preview_sections": scratch_docx_preview_sections,
        "has_scratch_docx_preview": bool(scratch_docx_preview_sections),
        "manual_choice_review_enabled": manual_choice_review_enabled,
        "manual_choice_keys": EXAM_IMPORT_MANUAL_CHOICE_KEYS,
        "confirm_action": reverse("teacher-exam-paper-new"),
        "can_confirm": can_confirm,
        "confirm_button_label": "已确认入库" if is_imported else "确认入库",
        "confirm_disabled_reason": ""
        if can_confirm
        else "当前任务已入库。" if is_imported else "处理完成后才能确认入库。",
        "back_href": reverse("teacher-exam-paper-new"),
    }
    return render_shell_page(request, "teacher", "entry/teacher_exam_paper_import_job_detail.html", context)


@role_required("teacher")
def teacher_exam_bank_paper_preview(request: HttpRequest, paper_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).get()
    except ExamQuestionBankPaper.DoesNotExist as exc:
        raise Http404("未找到该可用试卷") from exc
    try:
        ensure_teacher_can_operate_exam_bank_paper(portal_user, paper)
    except ExamError as exc:
        return HttpResponseForbidden(str(exc))
    if request.method == "POST":
        action = str(request.POST.get("form_action") or "").strip()
        if action != "generate_bank_question_analysis":
            return redirect(reverse("teacher-exam-bank-paper-preview", args=[paper_id]))
        question_id = normalize_positive_int(request.POST.get("question_id"), default=0, minimum=1)
        try:
            question = generate_single_bank_question_analysis(paper, question_id)
        except (ExamQuestionBankQuestion.DoesNotExist, ValidationError) as exc:
            message = "；".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            return_mode = "edit" if str(request.POST.get("return_to") or "").strip() == "edit" else "preview"
            context = get_exam_bank_paper_review_context(
                portal_user,
                paper_id,
                mode=return_mode,
                error_message=message or "单题解析生成失败。",
            )
            template_name = "entry/teacher_exam_bank_paper_edit.html" if return_mode == "edit" else "entry/teacher_exam_bank_paper_preview.html"
            return render_shell_page(request, "teacher", template_name, context)
        return_mode = "edit" if str(request.POST.get("return_to") or "").strip() == "edit" else "preview"
        target_route = "teacher-exam-bank-paper-edit" if return_mode == "edit" else "teacher-exam-bank-paper-preview"
        return redirect(
            build_redirect_with_query(
                reverse(target_route, args=[paper_id]),
                params={"op": "question_analysis_generated", "question": question.question_no},
                anchor=f"bank-question-{question.id}",
            )
        )
    op = (request.GET.get("op") or "").strip()
    success_message = ""
    if op == "updated":
        success_message = "试卷内容已保存。"
    elif op == "question_analysis_generated":
        success_message = f"已生成第 {request.GET.get('question') or ''} 题解析。"
    context = get_exam_bank_paper_review_context(
        portal_user,
        paper_id,
        mode="preview",
        success_message=success_message,
    )
    return render_shell_page(request, "teacher", "entry/teacher_exam_bank_paper_preview.html", context)


@role_required("teacher")
def teacher_exam_bank_paper_edit(request: HttpRequest, paper_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).get()
    except ExamQuestionBankPaper.DoesNotExist as exc:
        raise Http404("未找到该可用试卷") from exc
    try:
        ensure_teacher_can_operate_exam_bank_paper(portal_user, paper)
    except ExamError as exc:
        return HttpResponseForbidden(str(exc))

    if request.method == "POST":
        try:
            update_exam_bank_paper_from_request(paper, request, portal_user)
        except ValidationError as exc:
            message = "；".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            context = get_exam_bank_paper_review_context(portal_user, paper_id, mode="edit", error_message=message)
            return render_shell_page(request, "teacher", "entry/teacher_exam_bank_paper_edit.html", context)
        return redirect(
            build_redirect_with_query(
                reverse("teacher-exam-bank-paper-preview", args=[paper_id]),
                params={"op": "updated"},
            )
        )

    op = (request.GET.get("op") or "").strip()
    success_message = ""
    if op == "question_analysis_generated":
        success_message = f"已生成第 {request.GET.get('question') or ''} 题解析。"
    elif op == "updated":
        success_message = "试卷内容已保存。"
    context = get_exam_bank_paper_review_context(portal_user, paper_id, mode="edit", success_message=success_message)
    return render_shell_page(request, "teacher", "entry/teacher_exam_bank_paper_edit.html", context)


@role_required("teacher")
def teacher_exam_detail(request: HttpRequest, paper_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    if request.method == "POST":
        action = str(request.POST.get("form_action") or "").strip()
        if action not in {
            "mark_exam_question_important",
            "update_exam_question_answer",
            "update_exam_question_analysis",
            "accept_exam_analysis_suggestion",
            "reject_exam_analysis_suggestion",
        }:
            return redirect(reverse("teacher-exam-detail", args=[paper_id]))
        stats_exam_run_id = normalize_positive_int(request.POST.get("stats_exam_run_id"), default=0, minimum=0)
        stats_student_id = normalize_positive_int(request.POST.get("stats_student_id"), default=0, minimum=0)
        question_id = normalize_positive_int(request.POST.get("question_id"), default=0, minimum=1)
        try:
            question_queryset = ExamQuestion.objects.select_related("paper").filter(
                id=question_id,
                paper_id=paper_id,
                paper__is_active=True,
                is_active=True,
            )
            if portal_user.role != PortalUser.ROLE_PRINCIPAL:
                question_queryset = question_queryset.filter(paper__teacher=portal_user)
            question = question_queryset.get()
        except ExamQuestion.DoesNotExist as exc:
            raise Http404("未找到该考试题目") from exc
        redirect_params = {"question": question.question_no}
        if stats_exam_run_id:
            redirect_params["stats_exam_run_id"] = stats_exam_run_id
        if stats_student_id:
            redirect_params["stats_student_id"] = stats_student_id
        if action == "update_exam_question_answer":
            new_answer = normalize_exam_answer(request.POST.get("correct_answer"))
            options = question.options_json if isinstance(question.options_json, dict) else {}
            if not new_answer or new_answer not in options:
                redirect_params["op"] = "answer_update_failed"
                return redirect(
                    build_redirect_with_query(
                        reverse("teacher-exam-detail", args=[paper_id]),
                        params=redirect_params,
                        anchor=f"exam-question-{question.id}",
                    )
                )
            question.correct_answer = new_answer
            question.save(update_fields=["correct_answer", "updated_at"])
            updated_sessions = recalculate_exam_scores_for_paper(question.paper)
            redirect_params.update({"op": "answer_updated", "sessions": updated_sessions})
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exam-detail", args=[paper_id]),
                    params=redirect_params,
                    anchor=f"exam-question-{question.id}",
                )
            )
        if action == "update_exam_question_analysis":
            analysis = str(request.POST.get("analysis") or "").strip()
            ExamQuestionAnalysisBlock.objects.update_or_create(
                question=question,
                source_type=ExamQuestionAnalysisBlock.SOURCE_TEACHER,
                sort_order=0,
                defaults={
                    "content_md": analysis,
                    "created_by": portal_user,
                    "is_visible": bool(analysis),
                },
            )
            analysis = sync_legacy_question_analysis(question)
            ExamSubmissionAnswer.objects.filter(question=question).update(
                analysis_snapshot=analysis,
                updated_at=timezone.now(),
            )
            redirect_params["op"] = "analysis_updated"
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exam-detail", args=[paper_id]),
                    params=redirect_params,
                    anchor=f"exam-question-{question.id}",
                )
            )
        if action in {"accept_exam_analysis_suggestion", "reject_exam_analysis_suggestion"}:
            suggestion_id = normalize_positive_int(request.POST.get("suggestion_id"), default=0, minimum=1)
            try:
                suggestion = (
                    ExamQuestionAnalysisSuggestion.objects.select_related("student", "question")
                    .filter(
                        id=suggestion_id,
                        question=question,
                        question__paper_id=paper_id,
                        status=ExamQuestionAnalysisSuggestion.STATUS_PENDING,
                    )
                    .get()
                )
            except ExamQuestionAnalysisSuggestion.DoesNotExist as exc:
                raise Http404("未找到该解析建议") from exc
            if action == "reject_exam_analysis_suggestion":
                suggestion.status = ExamQuestionAnalysisSuggestion.STATUS_REJECTED
                suggestion.reviewed_by = portal_user
                suggestion.reviewed_at = timezone.now()
                suggestion.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
                create_student_analysis_suggestion_message(suggestion)
                redirect_params["op"] = "analysis_suggestion_rejected"
                return redirect(
                    build_redirect_with_query(
                        reverse("teacher-exam-detail", args=[paper_id]),
                        params=redirect_params,
                        anchor=f"exam-question-{question.id}",
                    )
                )
            accepted_content = str(request.POST.get("accepted_analysis") or suggestion.content_md or "").strip()
            contributor_name = suggestion.student.display_name
            contributor_heading = f"本解析由 {contributor_name} 同学提供"
            accepted_body = re.sub(r"^\s*-{3,}\s*", "", accepted_content).strip()
            accepted_body = re.sub(
                rf"^\s*(\*\*)?本解析由\s+{re.escape(contributor_name)}\s+同学提供。?(\*\*)?\s*",
                "",
                accepted_body,
            ).strip()
            accepted_content = f"---\n\n{contributor_heading}\n\n{accepted_body or suggestion.content_md.strip()}"
            max_sort_order = (
                ExamQuestionAnalysisBlock.objects.filter(question=question).aggregate(Max("sort_order")).get("sort_order__max")
                or 10
            )
            block = ExamQuestionAnalysisBlock.objects.create(
                question=question,
                source_type=ExamQuestionAnalysisBlock.SOURCE_STUDENT,
                content_md=accepted_content,
                contributors_json=[{"student_id": suggestion.student_id, "name": contributor_name}],
                created_by=portal_user,
                is_visible=True,
                sort_order=int(max_sort_order) + 10,
            )
            suggestion.status = ExamQuestionAnalysisSuggestion.STATUS_ACCEPTED
            suggestion.accepted_block = block
            suggestion.reviewed_by = portal_user
            suggestion.reviewed_at = timezone.now()
            suggestion.save(update_fields=["status", "accepted_block", "reviewed_by", "reviewed_at", "updated_at"])
            create_student_analysis_suggestion_message(suggestion)
            analysis = sync_legacy_question_analysis(question)
            ExamSubmissionAnswer.objects.filter(question=question).update(
                analysis_snapshot=analysis,
                updated_at=timezone.now(),
            )
            redirect_params["op"] = "analysis_suggestion_accepted"
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-exam-detail", args=[paper_id]),
                    params=redirect_params,
                    anchor=f"exam-question-{question.id}",
                )
            )
        important_note = str(request.POST.get("important_note") or "").strip()
        question.is_important = True
        question.important_note = important_note
        question.important_marked_by = portal_user
        question.important_marked_at = timezone.now()
        question.save(update_fields=["is_important", "important_note", "important_marked_by", "important_marked_at", "updated_at"])
        return redirect(
            build_redirect_with_query(
                reverse("teacher-exam-detail", args=[paper_id]),
                params={**redirect_params, "op": "important_marked"},
                anchor=f"exam-question-{question.id}",
            )
        )
    try:
        context = build_teacher_exam_detail_context(
            portal_user,
            paper_id,
            selected_exam_run_id=normalize_positive_int(request.GET.get("stats_exam_run_id"), default=0, minimum=0),
            selected_student_id=normalize_positive_int(request.GET.get("stats_student_id"), default=0, minimum=0),
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该考试") from exc
    if request.GET.get("op") == "important_marked":
        context["success_message"] = "已标记重点题。"
    elif request.GET.get("op") == "answer_updated":
        context["success_message"] = f"已修改第 {request.GET.get('question') or ''} 题标准答案，并同步重算 {request.GET.get('sessions') or '0'} 条提交记录。"
    elif request.GET.get("op") == "analysis_updated":
        context["success_message"] = f"已更新第 {request.GET.get('question') or ''} 题解析，学生端结果页会同步显示。"
    elif request.GET.get("op") == "analysis_suggestion_accepted":
        context["success_message"] = f"已采纳第 {request.GET.get('question') or ''} 题的学生解析，学生端结果页会同步显示。"
    elif request.GET.get("op") == "analysis_suggestion_rejected":
        context["success_message"] = f"已忽略第 {request.GET.get('question') or ''} 题的学生解析建议。"
    elif request.GET.get("op") == "answer_update_failed":
        context["error_message"] = f"第 {request.GET.get('question') or ''} 题标准答案无效，请选择当前题目已有选项。"
    return render_shell_page(request, "teacher", "entry/teacher_exam_detail.html", context)


def create_student_analysis_suggestion_message(suggestion: ExamQuestionAnalysisSuggestion) -> StudentSiteMessage | None:
    if not suggestion.session_id:
        return None
    question = suggestion.question
    paper = question.paper
    status_text = dict(ExamQuestionAnalysisSuggestion.STATUS_CHOICES).get(suggestion.status, "已处理")
    target_href = f"{reverse('student-exam-detail', args=[suggestion.session_id])}#student-analysis-suggestion-{suggestion.id}"
    message, _created = StudentSiteMessage.objects.update_or_create(
        source_suggestion=suggestion,
        defaults={
            "student": suggestion.student,
            "title": f"解析挑战{status_text}",
            "body": f"{paper.title} 第 {question.question_no} 题的解析挑战已{status_text}。",
            "target_href": target_href,
            "is_read": False,
            "read_at": None,
        },
    )
    return message


@role_required("teacher")
def teacher_homework_stats(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    raw_anchor_date = str(request.GET.get("anchor_date") or "").strip()
    if raw_anchor_date:
        try:
            anchor_date = normalize_student_learning_anchor_date(raw_anchor_date)
        except ValueError:
            anchor_date = timezone.localdate()
    else:
        anchor_date = None
    context = build_teacher_homework_stats_context(
        portal_user,
        period=request.GET.get("period", "week"),
        anchor_date=anchor_date,
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


@api_role_required("teacher")
def teacher_homework_stats_lesson_feedback(request: HttpRequest) -> JsonResponse:
    selected_period = request.GET.get("period") or "week"
    if selected_period != "week":
        return JsonResponse({"error": "教师评价仅支持 week 周期。"}, status=400)

    try:
        anchor_date = normalize_student_learning_anchor_date(request.GET.get("anchor_date"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    portal_user = get_portal_user_from_request(request)
    student_id = normalize_positive_int(request.GET.get("student_id"), default=0, minimum=1)
    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id, teacher_user=portal_user)
        .first()
    )
    if student is None:
        return JsonResponse({"error": "未找到该学生"}, status=404)

    payload = build_student_week_lesson_feedback_payload(
        student=student,
        anchor_date=anchor_date,
        include_teacher_fields=True,
        teacher=portal_user,
    )
    return JsonResponse(payload)


@api_role_required("teacher")
def teacher_homework_stats_lesson_feedback_save(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"error": "仅支持 POST 提交。"}, status=405)

    selected_period = request.POST.get("period") or "week"
    if selected_period != "week":
        return JsonResponse({"error": "教师评价仅支持 week 周期。"}, status=400)

    try:
        anchor_date = normalize_student_learning_anchor_date(request.POST.get("anchor_date"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    portal_user = get_portal_user_from_request(request)
    student_id = normalize_positive_int(request.POST.get("student_id"), default=0, minimum=1)
    assignment_id = normalize_positive_int(request.POST.get("assignment_id"), default=0, minimum=1)
    if not student_id or not assignment_id:
        return JsonResponse({"error": "请选择有效的学生和作业。"}, status=400)

    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id, teacher_user=portal_user)
        .first()
    )
    if student is None:
        return JsonResponse({"error": "未找到该学生"}, status=404)

    candidate_assignments = get_student_week_lesson_feedback_assignments(
        student=student,
        anchor_date=anchor_date,
        teacher=portal_user,
    )
    assignment = next(
        (item for item in candidate_assignments if int(item.id) == assignment_id),
        None,
    )
    if assignment is None:
        return JsonResponse({"error": "未找到可编辑的课堂评价"}, status=404)

    assignment.highlights = normalize_preserved_multiline_text(request.POST.get("highlights", "")).strip()
    assignment.areas_for_growth = normalize_preserved_multiline_text(request.POST.get("areas_for_growth", "")).strip()
    assignment.save(update_fields=["highlights", "areas_for_growth", "updated_at"])

    payload = build_student_week_lesson_feedback_payload(
        student=student,
        anchor_date=anchor_date,
        include_teacher_fields=True,
        teacher=portal_user,
    )
    return JsonResponse(
        {
            "message": "保存成功",
            **payload,
        }
    )


@role_required("teacher")
def teacher_homework_submission_detail(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    raw_anchor_date = str(request.GET.get("anchor_date") or "").strip()
    if raw_anchor_date:
        try:
            anchor_date = normalize_student_learning_anchor_date(raw_anchor_date)
        except ValueError:
            anchor_date = timezone.localdate()
    else:
        anchor_date = None
    student_id = normalize_positive_int(request.GET.get("student_id"), default=0, minimum=1)
    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id, teacher_user=portal_user)
        .first()
    )
    if student is None:
        raise Http404("未找到该学生")

    context = build_teacher_homework_submission_detail_context(
        portal_user,
        student=student,
        anchor_date=anchor_date,
    )
    return render_shell_page(request, "teacher", "entry/teacher_homework_submission_detail.html", context)


@role_required("teacher")
def teacher_homework_student_period_assignment_detail(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    raw_anchor_date = str(request.GET.get("anchor_date") or "").strip()
    if raw_anchor_date:
        try:
            anchor_date = normalize_student_learning_anchor_date(raw_anchor_date)
        except ValueError:
            anchor_date = timezone.localdate()
    else:
        anchor_date = None
    student_id = normalize_positive_int(request.GET.get("student_id"), default=0, minimum=1)
    selected_period = request.GET.get("period") or "month"
    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id, teacher_user=portal_user)
        .first()
    )
    if student is None:
        raise Http404("未找到该学生")

    context = build_teacher_homework_student_period_assignment_detail_context(
        portal_user,
        student=student,
        period=selected_period,
        anchor_date=anchor_date,
    )
    return render_shell_page(request, "teacher", "entry/teacher_homework_student_period_assignment_detail.html", context)


@role_required("teacher")
def teacher_homework_assignment_submission_detail(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    raw_anchor_date = str(request.GET.get("anchor_date") or "").strip()
    if raw_anchor_date:
        try:
            anchor_date = normalize_student_learning_anchor_date(raw_anchor_date)
        except ValueError:
            anchor_date = timezone.localdate()
    else:
        anchor_date = None
    student_id = normalize_positive_int(request.GET.get("student_id"), default=0, minimum=1)
    assignment_id = normalize_positive_int(request.GET.get("assignment_id"), default=0, minimum=1)
    selected_period = request.GET.get("period") or "month"
    student = (
        Student.objects.select_related("user", "parent_user", "teacher_user")
        .filter(id=student_id, teacher_user=portal_user)
        .first()
    )
    if student is None:
        raise Http404("未找到该学生")

    assignment = (
        HomeworkAssignment.objects.select_related("student", "source_import_job")
        .filter(
            id=assignment_id,
            teacher=portal_user,
            student=student,
            is_active=True,
        )
        .first()
    )
    if assignment is None:
        raise Http404("未找到该作业")

    context = build_teacher_homework_assignment_submission_detail_context(
        portal_user,
        student=student,
        assignment=assignment,
        period=selected_period,
        anchor_date=anchor_date,
    )
    return render_shell_page(request, "teacher", "entry/teacher_homework_assignment_submission_detail.html", context)


@role_required("teacher")
def teacher_homework_submission_answer_detail(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    raw_anchor_date = str(request.GET.get("anchor_date") or "").strip()
    if raw_anchor_date:
        try:
            anchor_date = normalize_student_learning_anchor_date(raw_anchor_date)
        except ValueError:
            anchor_date = timezone.localdate()
    else:
        anchor_date = None
    submission_id = normalize_positive_int(request.GET.get("submission_id"), default=0, minimum=1)
    selected_period = request.GET.get("period") or "month"
    submission = (
        HomeworkSubmission.objects.select_related(
            "student",
            "assignment",
            "assignment__source_import_job",
        )
        .filter(
            id=submission_id,
            student__teacher_user=portal_user,
            student_id=F("assignment__student_id"),
            assignment__teacher=portal_user,
            assignment__is_active=True,
            is_active=True,
        )
        .first()
    )
    if submission is None:
        raise Http404("未找到该提交记录")

    context = build_teacher_homework_submission_answer_detail_context(
        submission=submission,
        period=selected_period,
        anchor_date=anchor_date,
    )
    return render_shell_page(request, "teacher", "entry/teacher_homework_submission_answer_detail.html", context)


@role_required("teacher")
def teacher_homework_batch_create(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    selected_course_slug = (request.GET.get("course") or "").strip().lower()
    requested_import_job_id = normalize_positive_int(request.GET.get("import_job_id"), default=0, minimum=1)
    selected_course = None
    if selected_course_slug:
        selected_course = Course.objects.filter(slug=selected_course_slug).order_by("id").first()
        if selected_course is None:
            raise Http404("未找到该课程")

    success_message = ""
    batch_result: dict[str, object] | None = None
    if request.GET.get("op") == "created":
        created_count = normalize_positive_int(request.GET.get("count"), default=0, minimum=0)
        if created_count:
            created_mode = (request.GET.get("mode") or "with_source").strip()
            if created_mode == "requirement_only":
                success_message = f"已为 {created_count} 名学生布置要求型作业。"
            else:
                success_message = f"已为 {created_count} 名学生布置作业。"
            if request.GET.get("with_summary") == "1":
                success_message += " 已关联 1 篇课后总结。"
            batch_result = {
                "status": "success",
                "title": "批量布置成功",
                "message": success_message,
                "count": created_count,
            }

    form_values: dict[str, object] | None = {
        "import_job_id": requested_import_job_id,
        "target_subject": request.GET.get("target_subject", "cpp"),
        "target_category_code": request.GET.get("target_category_code", ""),
        "target_level_1": request.GET.get("target_level_1", ""),
        "target_level_2": request.GET.get("target_level_2", ""),
        "target_level_3": request.GET.get("target_level_3", ""),
        "free_question_ids": normalize_positive_int_list(request.GET.getlist("free_question_ids")),
    }
    error_message = ""

    if request.method == "POST":
        selected_student_ids = normalize_positive_int_list(request.POST.getlist("student_ids"))
        selected_import_job_ids = normalize_positive_int_list(request.POST.getlist("import_job_id"))
        selected_import_job_id = selected_import_job_ids[0] if len(selected_import_job_ids) == 1 else 0
        selected_content_id = normalize_positive_int(request.POST.get("content_id"), default=0, minimum=1)
        free_question_ids = normalize_positive_int_list(request.POST.getlist("free_question_ids"))
        target_subject = normalize_knowledge_map_subject(request.POST.get("target_subject") or "cpp")
        target_category_code = (request.POST.get("target_category_code") or "").strip().upper()
        target_level_1 = (request.POST.get("target_level_1") or "").strip()
        target_level_2 = (request.POST.get("target_level_2") or "").strip()
        target_level_3 = (request.POST.get("target_level_3") or "").strip()
        due_date_raw = request.POST.get("due_date", "").strip()
        assignment_requirement = normalize_preserved_multiline_text(
            request.POST.get("assignment_requirement", "")
        )
        assignment_requirement_text = assignment_requirement.strip()
        summary_title = request.POST.get("summary_title", "").strip()
        summary_html_text = request.POST.get("summary_html", "").strip()
        summary_highlights = normalize_preserved_multiline_text(request.POST.get("summary_highlights", "")).strip()
        summary_areas_for_growth = normalize_preserved_multiline_text(request.POST.get("summary_areas_for_growth", "")).strip()
        form_values = {
            "student_ids": selected_student_ids,
            "import_job_id": selected_import_job_id,
            "content_id": selected_content_id,
            "free_question_ids": free_question_ids,
            "target_subject": target_subject,
            "target_category_code": target_category_code,
            "target_level_1": target_level_1,
            "target_level_2": target_level_2,
            "target_level_3": target_level_3,
            "assignment_requirement": assignment_requirement,
            "due_date": due_date_raw,
            "summary_title": summary_title,
            "summary_html": summary_html_text,
            "summary_highlights": summary_highlights,
            "summary_areas_for_growth": summary_areas_for_growth,
        }

        if not selected_student_ids:
            error_message = "请选择学生"
        elif len(selected_import_job_ids) > 1:
            error_message = "一次只能选择1条题源"
        elif not selected_import_job_id and not free_question_ids and not assignment_requirement_text:
            error_message = "请选择题源或填写作业要求"
        else:
            due_date_value = parse_homework_due_datetime_input(due_date_raw)
            if due_date_value is None:
                error_message = "请选择有效的截止日期。"

            summary_html_value = ""
            summary_content_present = False
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
                    summary_content_present = bool(summary_html_value)
                    if summary_title and not summary_content_present:
                        error_message = "已填写课后总结标题，但还没有上传或粘贴 HTML 内容。"

            visible_import_job = None
            source_content = None
            assignment_title = ""
            creation_mode = "with_source"
            if due_date_value is not None:
                if selected_import_job_id:
                    visible_import_job = (
                        get_visible_homework_import_jobs(
                            portal_user,
                            course_id=selected_course.id if selected_course is not None else None,
                        )
                        .filter(id=selected_import_job_id)
                        .first()
                    )
                    if visible_import_job is None:
                        error_message = "题源不存在或无权访问"
                    else:
                        source_content = (
                            visible_import_job.assignment.content
                            if visible_import_job.assignment_id and visible_import_job.assignment
                            else visible_import_job.content
                        )
                        if source_content is None:
                            knowledge_snapshot = parse_question_source_knowledge_snapshot(visible_import_job.parse_notes)
                            if not knowledge_snapshot.get("level_code") or not knowledge_snapshot.get("knowledge_level_1"):
                                source_question = (
                                    visible_import_job.questions.filter(is_active=True)
                                    .order_by("question_no", "id")
                                    .first()
                                )
                                question_snapshot = (
                                    decode_sql_ascii_json_text(source_question.source_snapshot_json)
                                    if source_question is not None
                                    else {}
                                )
                                if isinstance(question_snapshot, dict):
                                    knowledge_snapshot = {
                                        **knowledge_snapshot,
                                        "knowledge_subject": knowledge_snapshot.get("knowledge_subject")
                                        or str(question_snapshot.get("knowledge_subject") or "").strip(),
                                        "level_code": knowledge_snapshot.get("level_code")
                                        or str(question_snapshot.get("level_code") or "").strip().upper(),
                                        "knowledge_level_1": knowledge_snapshot.get("knowledge_level_1")
                                        or str(question_snapshot.get("knowledge_level_1") or "").strip(),
                                        "knowledge_level_2": knowledge_snapshot.get("knowledge_level_2")
                                        or str(question_snapshot.get("knowledge_level_2") or "").strip(),
                                        "knowledge_level_3": knowledge_snapshot.get("knowledge_level_3")
                                        or str(question_snapshot.get("knowledge_level_3") or "").strip(),
                                    }
                            import_subject = (
                                knowledge_snapshot.get("knowledge_subject")
                                or target_subject
                                or (selected_course.slug if selected_course is not None else selected_course_slug)
                                or "cpp"
                            )
                            import_category_code = knowledge_snapshot.get("level_code") or target_category_code
                            import_level_1 = knowledge_snapshot.get("knowledge_level_1") or target_level_1
                            import_level_2 = knowledge_snapshot.get("knowledge_level_2") or target_level_2
                            import_level_3 = knowledge_snapshot.get("knowledge_level_3") or target_level_3
                            try:
                                source_content = ensure_course_content_for_knowledge_path(
                                    portal_user=portal_user,
                                    subject=import_subject,
                                    category_code=import_category_code,
                                    level_1=import_level_1,
                                    level_2=import_level_2,
                                    level_3=import_level_3,
                                    course_slug=selected_course.slug if selected_course is not None else selected_course_slug,
                                )
                            except ValidationError as exc:
                                error_message = "；".join(exc.messages) if exc.messages else str(exc)
                            else:
                                visible_import_job.content = source_content
                                visible_import_job.save(update_fields=["content", "updated_at"])
                        if not error_message and source_content is not None:
                            assignment_title = (
                                visible_import_job.assignment.title.strip()
                                if visible_import_job.assignment_id and visible_import_job.assignment
                                else ""
                            )
                            if not assignment_title:
                                assignment_title = source_content.title.strip()
                            assignment_title = assignment_title or visible_import_job.source_filename.strip()
                elif free_question_ids:
                    available_contents = get_teacher_batch_homework_contents(
                        portal_user,
                        course_slug=selected_course_slug,
                    )
                    content_map = {content.id: content for content in available_contents}
                    source_content = content_map.get(selected_content_id)
                    if source_content is None:
                        error_message = "请选择当前老师负责范围内的知识点作为作业目标。"
                    else:
                        assignment_title = build_knowledge_path_title(target_level_1, target_level_2, target_level_3) or source_content.title.strip() or "自由选题作业"
                        try:
                            visible_import_job = create_homework_import_job_from_exam_questions(
                                teacher=portal_user,
                                content=source_content,
                                question_ids=free_question_ids[:20],
                                title=assignment_title,
                            )
                        except ValidationError as exc:
                            error_message = "；".join(exc.messages) if exc.messages else str(exc)
                else:
                    creation_mode = "requirement_only"
                    available_contents = get_teacher_batch_homework_contents(
                        portal_user,
                        course_slug=selected_course_slug,
                    )
                    content_map = {content.id: content for content in available_contents}
                    source_content = content_map.get(selected_content_id)
                    if source_content is None:
                        error_message = "请选择当前老师负责范围内的知识点作为作业目标。"
                    else:
                        assignment_title = source_content.title.strip() or "课后作业要求"

            if not error_message and source_content is not None:
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
                    if not error_message:
                        created_summary = None
                        final_summary_title = ""
                        if summary_content_present:
                            final_summary_title = summary_title or build_default_batch_homework_summary_title(
                                course_label=selected_course.title if selected_course is not None else "批量作业",
                                anchor_date=timezone.localdate(),
                            )
                        try:
                            with transaction.atomic():
                                if summary_content_present:
                                    created_summary = HomeworkSummary.objects.create(
                                        title=final_summary_title,
                                        summary_html=summary_html_value,
                                        created_by=portal_user,
                                    )
                                for student in selected_students:
                                    ensure_homework_content_access(
                                        student,
                                        source_content,
                                        portal_user,
                                    )
                                    HomeworkAssignment.objects.create(
                                        teacher=portal_user,
                                        student=student,
                                        content=source_content,
                                        title=assignment_title,
                                        description=assignment_requirement,
                                        due_date=due_date_value,
                                        status=HomeworkAssignment.STATUS_ASSIGNED,
                                        highlights=summary_highlights,
                                        areas_for_growth=summary_areas_for_growth,
                                        summary=created_summary,
                                        source_import_job=visible_import_job if creation_mode == "with_source" else None,
                                        assigned_at=timezone.now(),
                                        is_active=True,
                                    )
                        except ValidationError as exc:
                            error_message = "；".join(exc.messages) if exc.messages else str(exc)
                        else:
                            redirect_params: dict[str, object] = {
                                "op": "created",
                                "count": len(selected_students),
                                "mode": creation_mode,
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
        if error_message:
            batch_result = {
                "status": "error",
                "title": "批量布置失败",
                "message": error_message,
            }

    try:
        context = build_teacher_homework_batch_create_context(
            portal_user,
            selected_course_slug=selected_course_slug,
            form_values=form_values,
            error_message=error_message,
            success_message=success_message,
            batch_result=batch_result,
        )
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该课程") from exc

    batch_title_icon_relative_path = "entry/images/batch-homework-title.png"
    batch_title_icon_disk_path = Path(settings.BASE_DIR) / "entry" / "static" / "entry" / "images" / "batch-homework-title.png"
    context["batch_title_icon_href"] = static(batch_title_icon_relative_path) if batch_title_icon_disk_path.exists() else ""
    context["batch_title_icon_relative_path"] = batch_title_icon_relative_path

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


HOMEWORK_IMPORT_JOB_RECOGNITION_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="homework-import-recognition")
HOMEWORK_IMPORT_JOB_RECOGNITION_LOCK = threading.Lock()
HOMEWORK_IMPORT_JOB_RECOGNITION_FUTURES: dict[tuple[str, int], object] = {}


def get_homework_question_options_text(question: HomeworkQuestion) -> str:
    options = decode_sql_ascii_json_text(question.options_json)
    if not isinstance(options, dict):
        return "无"
    lines = [
        f"{key}. {str(options.get(key) or '').strip()}"
        for key in ["A", "B", "C", "D"]
        if str(options.get(key) or "").strip()
    ]
    return "\n".join(lines) or "无"


def get_homework_question_snapshot(question: HomeworkQuestion) -> dict[str, object]:
    snapshot = decode_sql_ascii_json_text(question.source_snapshot_json)
    return snapshot if isinstance(snapshot, dict) else {}


def save_homework_question_snapshot(question: HomeworkQuestion, snapshot: dict[str, object]) -> None:
    question.source_snapshot_json = encode_sql_ascii_json_text(snapshot)
    question.save(update_fields=["source_snapshot_json", "updated_at"])


def set_homework_question_recognition_status(
    question: HomeworkQuestion,
    *,
    kind: str,
    status: str,
    error_message: str = "",
) -> None:
    snapshot = get_homework_question_snapshot(question)
    snapshot[f"{kind}_status"] = status
    snapshot[f"{kind}_error"] = str(error_message or "").strip()[:1000]
    save_homework_question_snapshot(question, snapshot)


def build_homework_question_analysis_prompt(question: HomeworkQuestion) -> str:
    return "\n".join(
        [
            "你是少儿编程老师。请为下面选择题生成适合学生复盘的解析。",
            "要求：使用 Markdown；步骤清晰；如果涉及代码，用 fenced code block；不要输出题目无关内容。",
            "",
            f"题号：第 {question.question_no} 题",
            f"题干：{question.stem}",
            "选项：",
            get_homework_question_options_text(question),
            f"正确答案：{question.correct_answer or '未填写'}",
            "已有解析：",
            str(question.analysis or "").strip() or "无",
        ]
    )


def build_homework_question_knowledge_prompt(question: HomeworkQuestion, mapping_markdown: str) -> str:
    return "\n".join(
        [
            "你是少儿编程考试知识点标注助手。请严格根据下面的知识对照表，为题目选择最匹配的知识点。",
            "只输出 JSON，不要输出 Markdown、解释或多余文字。",
            'JSON 格式必须是：{"level_1":"一级目录","level_2":"二级目录","level_3":"三级训练点或空字符串"}',
            "level_1 和 level_2 必须来自知识对照表中的同一行；level_3 可为空。",
            "",
            "知识对照表：",
            mapping_markdown,
            "",
            f"题号：第 {question.question_no} 题",
            f"题干：{question.stem}",
            "选项：",
            get_homework_question_options_text(question),
            f"正确答案：{question.correct_answer or '未填写'}",
            "已有解析：",
            str(question.analysis or "").strip() or "无",
        ]
    )


def get_homework_import_job_mapping_markdown(import_job: HomeworkImportJob) -> str:
    content = import_job.content or (import_job.assignment.content if import_job.assignment_id and import_job.assignment else None)
    if content is not None and content.course_id is not None:
        subject = normalize_exam_knowledge_subject(content.course.title)
        category_code = str(content.level.code if content.level_id and content.level else content.phase or "").strip().upper()
    else:
        knowledge_snapshot = parse_question_source_knowledge_snapshot(import_job.parse_notes)
        subject = normalize_exam_knowledge_subject(knowledge_snapshot.get("knowledge_subject") or "cpp")
        category_code = str(knowledge_snapshot.get("level_code") or "").strip().upper()
    if not subject or not category_code:
        raise ValidationError("当前题源没有可用的学科和级别，无法匹配知识点映射表。")
    rows = list(
        ExamKnowledgePointMap.objects.filter(
            subject=subject,
            category_code=category_code,
            is_active=True,
        ).order_by("sort_order", "id")
    )
    if not rows:
        raise ValidationError("当前学科和级别没有可用知识点映射。")
    return build_exam_knowledge_mapping_markdown_from_rows(rows)


def run_homework_import_job_analysis_generation(import_job_id: int) -> None:
    close_old_connections()
    try:
        import_job = HomeworkImportJob.objects.get(id=import_job_id, is_active=True)
        questions = list(import_job.questions.filter(is_active=True).order_by("question_no", "id"))
        worker_count = max(1, min(int(getattr(settings, "HOMEWORK_IMPORT_JOB_RECOGNITION_CONCURRENCY", 2)), len(questions), 4))

        def process_question(question_id: int) -> None:
            question = HomeworkQuestion.objects.get(id=question_id, is_active=True)
            set_homework_question_recognition_status(question, kind="analysis", status="running")
            try:
                analysis = _request_qwen_analysis(prompt=build_homework_question_analysis_prompt(question), image_paths=[], append_challenge=False)
            except Exception as exc:
                set_homework_question_recognition_status(question, kind="analysis", status="failed", error_message=str(exc))
                raise
            question.analysis = analysis.strip()
            snapshot = get_homework_question_snapshot(question)
            snapshot["analysis_status"] = "done"
            snapshot["analysis_error"] = ""
            question.source_snapshot_json = encode_sql_ascii_json_text(snapshot)
            question.save(update_fields=["analysis", "source_snapshot_json", "updated_at"])

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix=f"homework-analysis-{import_job_id}") as executor:
            for future in as_completed([executor.submit(process_question, question.id) for question in questions]):
                future.result()
    finally:
        with HOMEWORK_IMPORT_JOB_RECOGNITION_LOCK:
            HOMEWORK_IMPORT_JOB_RECOGNITION_FUTURES.pop(("analysis", import_job_id), None)
        close_old_connections()


def run_homework_import_job_knowledge_generation(import_job_id: int) -> None:
    close_old_connections()
    try:
        import_job = HomeworkImportJob.objects.select_related(
            "content",
            "content__course",
            "content__level",
            "assignment",
            "assignment__content",
            "assignment__content__course",
            "assignment__content__level",
        ).get(id=import_job_id, is_active=True)
        mapping_markdown = get_homework_import_job_mapping_markdown(import_job)
        questions = list(import_job.questions.filter(is_active=True).order_by("question_no", "id"))
        worker_count = max(1, min(int(getattr(settings, "HOMEWORK_IMPORT_JOB_RECOGNITION_CONCURRENCY", 2)), len(questions), 4))

        def process_question(question_id: int) -> None:
            question = HomeworkQuestion.objects.get(id=question_id, is_active=True)
            set_homework_question_recognition_status(question, kind="knowledge", status="running")
            try:
                raw = _request_qwen_analysis(
                    prompt=build_homework_question_knowledge_prompt(question, mapping_markdown),
                    image_paths=[],
                    append_challenge=False,
                )
                parsed = json.loads(_strip_json_code_fence(raw))
                if not isinstance(parsed, dict):
                    raise ValidationError("Qwen 知识点识别返回格式不正确。")
            except Exception as exc:
                set_homework_question_recognition_status(question, kind="knowledge", status="failed", error_message=str(exc))
                raise
            snapshot = get_homework_question_snapshot(question)
            snapshot["knowledge_level_1"] = str(parsed.get("level_1") or "").strip()
            snapshot["knowledge_level_2"] = str(parsed.get("level_2") or "").strip()
            snapshot["knowledge_level_3"] = str(parsed.get("level_3") or "").strip()
            snapshot["knowledge_status"] = "done"
            snapshot["knowledge_error"] = ""
            save_homework_question_snapshot(question, snapshot)

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix=f"homework-knowledge-{import_job_id}") as executor:
            for future in as_completed([executor.submit(process_question, question.id) for question in questions]):
                future.result()
    finally:
        with HOMEWORK_IMPORT_JOB_RECOGNITION_LOCK:
            HOMEWORK_IMPORT_JOB_RECOGNITION_FUTURES.pop(("knowledge", import_job_id), None)
        close_old_connections()


def start_homework_import_job_recognition(import_job_id: int, kind: str) -> bool:
    with HOMEWORK_IMPORT_JOB_RECOGNITION_LOCK:
        key = (kind, import_job_id)
        existing = HOMEWORK_IMPORT_JOB_RECOGNITION_FUTURES.get(key)
        if existing is not None and not existing.done():
            return False
        target = run_homework_import_job_analysis_generation if kind == "analysis" else run_homework_import_job_knowledge_generation
        future = HOMEWORK_IMPORT_JOB_RECOGNITION_EXECUTOR.submit(target, import_job_id)
        HOMEWORK_IMPORT_JOB_RECOGNITION_FUTURES[key] = future
    return True


@role_required("teacher")
def teacher_homework_import_job_recognition(request: HttpRequest, import_job_id: int, kind: str) -> HttpResponse:
    if request.method != "POST":
        return JsonResponse({"error": "仅支持 POST。"}, status=405)
    if kind not in {"analysis", "knowledge"}:
        return JsonResponse({"error": "识别类型无效。"}, status=400)
    portal_user = get_portal_user_from_request(request)
    import_job = get_visible_homework_import_jobs(portal_user).filter(id=import_job_id).first()
    if import_job is None:
        raise Http404("未找到该题目记录")
    questions = list(import_job.questions.filter(is_active=True).order_by("question_no", "id"))
    if not questions:
        return JsonResponse({"error": "当前题源没有可识别题目。"}, status=400)
    status_key = f"{kind}_status"
    for question in questions:
        set_homework_question_recognition_status(question, kind=kind, status="pending")
    started = start_homework_import_job_recognition(import_job.id, kind)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "started": started})
    return redirect(request.headers.get("Referer") or reverse("teacher-homework-batch-create"))


def create_homework_import_job_from_exam_questions(
    *,
    teacher: PortalUser,
    content: CourseContent,
    question_ids: list[int],
    title: str,
) -> HomeworkImportJob:
    questions = list(
        ExamQuestion.objects.select_related("paper")
        .filter(id__in=question_ids, is_active=True, paper__is_active=True)
        .order_by("id")
    )
    if not questions:
        raise ValidationError("请选择可用的自由选题题目。")
    question_order = {question_id: index for index, question_id in enumerate(question_ids)}
    questions.sort(key=lambda item: question_order.get(item.id, len(question_order)))
    source_text = "\n".join(f"{index}. {question.stem}" for index, question in enumerate(questions, start=1))
    source_bytes = source_text.encode("utf-8")
    source_filename = f"自由选题-{timezone.localtime().strftime('%Y%m%d%H%M%S')}.txt"
    import_job = HomeworkImportJob.objects.create(
        teacher=teacher,
        assignment=None,
        content=content,
        source_file=ContentFile(source_bytes, name=source_filename),
        source_filename=source_filename,
        source_sha256=compute_uploaded_file_sha256(ContentFile(source_bytes, name=source_filename)),
        source_type=HomeworkImportJob.SOURCE_TYPE_TEXT,
        parse_status=HomeworkImportJob.STATUS_CONFIRMED,
        candidates_json=[],
        parse_notes=f"{title or content.title}：老师从自由选题生成。",
        confirmed_at=timezone.now(),
        is_active=True,
    )
    for index, question in enumerate(questions, start=1):
        HomeworkQuestion.objects.create(
            assignment=None,
            import_job=import_job,
            question_no=index,
            question_type=HomeworkQuestion.QUESTION_TYPE_SINGLE_CHOICE,
            stem=question.stem,
            options_json=question.options_json if isinstance(question.options_json, dict) else {},
            correct_answer=str(question.correct_answer or "").strip().upper(),
            analysis=str(question.analysis or "").strip(),
            source_snapshot_json={
                **(question.source_snapshot_json if isinstance(question.source_snapshot_json, dict) else {}),
                "free_practice_source_question_id": question.id,
                "source_exam_paper_id": question.paper_id,
            },
            is_active=True,
        )
    return import_job


@role_required("teacher")
def teacher_question_source_create_content(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return JsonResponse({"error": "仅支持 POST 提交。"}, status=405)

    portal_user = get_portal_user_from_request(request)
    course_slug = (request.POST.get("course") or request.GET.get("course") or "").strip().lower()
    subject = normalize_knowledge_map_subject(request.POST.get("subject") or request.POST.get("knowledge_subject") or "")
    category_code = (request.POST.get("category_code") or request.POST.get("knowledge_category_code") or "").strip().upper()
    level_1 = (request.POST.get("level_1") or request.POST.get("knowledge_level_1") or "").strip()
    level_2 = (request.POST.get("level_2") or request.POST.get("knowledge_level_2") or "").strip()
    level_3 = (request.POST.get("level_3") or request.POST.get("knowledge_level_3") or "").strip()
    is_mapping_create = bool(subject or category_code or level_1 or level_2 or level_3)
    title = (request.POST.get("title") or "").strip()
    if is_mapping_create and not title:
        title = build_knowledge_path_title(level_1, level_2, level_3)
    level_id = normalize_positive_int(request.POST.get("level_id"), default=0, minimum=1)

    if is_mapping_create:
        if not subject:
            return JsonResponse({"error": "请选择学科。"}, status=400)
        if not category_code:
            return JsonResponse({"error": "请选择级别。"}, status=400)
        if not level_1:
            return JsonResponse({"error": "请输入或选择一级目录。"}, status=400)
        if not level_2:
            return JsonResponse({"error": "请输入或选择二级目录。"}, status=400)
    if not title:
        return JsonResponse({"error": "请输入知识点名称。"}, status=400)

    selected_course = resolve_course_for_knowledge_subject(portal_user, subject or course_slug or "cpp", course_slug)
    if selected_course is None:
        return JsonResponse({"error": "当前课程不存在，或你没有该课程权限。"}, status=400)
    course_slug = selected_course.slug

    available_level_options = get_teacher_question_source_level_options(portal_user, course_slug)
    available_level_ids = {item["id"] for item in available_level_options}
    if not level_id and category_code:
        matched_level = next((item for item in available_level_options if str(item.get("code") or "").upper() == category_code), None)
        if matched_level:
            level_id = int(matched_level["id"])
    if level_id not in available_level_ids:
        return JsonResponse({"error": "请选择有效的 Level。"}, status=400)

    selected_level = (
        CourseLevel.objects.select_related("category", "category__course")
        .filter(id=level_id, is_active=True, category__course=selected_course)
        .first()
    )
    if selected_level is None:
        return JsonResponse({"error": "请选择有效的 Level。"}, status=400)

    duplicate_exists = CourseContent.objects.filter(
        course=selected_course,
        level=selected_level,
        title__iexact=title,
        is_active=True,
    ).first()
    if duplicate_exists and not is_mapping_create:
        return JsonResponse({"error": "该知识点已存在"}, status=400)

    generated_slug = build_auto_course_content_slug(selected_course, selected_level, title)
    route_path = build_knowledge_point_default_route_path(
        selected_course.slug,
        selected_level.category.slug,
        selected_level.code,
        generated_slug,
    )
    existing_type = (
        CourseContent.objects.filter(course=selected_course)
        .exclude(content_type="")
        .order_by("id")
        .values_list("content_type", flat=True)
        .first()
    ) or f"{selected_course.title}{selected_level.category.title}"
    next_sort_order = (
        (CourseContent.objects.filter(level=selected_level).aggregate(max_sort=Max("sort_order"))["max_sort"] or 0)
        + 1
    )

    with transaction.atomic():
        if is_mapping_create:
            ExamKnowledgePointMap.objects.update_or_create(
                subject=subject,
                category_code=category_code,
                level_1=level_1,
                level_2=level_2,
                level_3=level_3,
                defaults={"is_active": True},
            )
        created_content = duplicate_exists
        if created_content is None:
            created_content = CourseContent.objects.create(
                course=selected_course,
                level=selected_level,
                content_type=existing_type,
                slug=generated_slug,
                title=title,
                phase=selected_level.code,
                permission_code=infer_content_permission_code(
                    selected_course.slug,
                    level_code=selected_level.code,
                    phase=selected_level.code,
                ),
                sort_order=next_sort_order,
                route_path=route_path,
                summary="",
                has_real_content=False,
                is_active=True,
            )

    payload = {
        "id": created_content.id,
        "value": created_content.id,
        "slug": created_content.slug,
        "title": created_content.title,
        "phase": created_content.phase,
        "level_id": selected_level.id,
        "level_label": selected_level.title,
        "category_title": selected_level.category.title,
        "label": f"{selected_course.title} / {selected_level.title} / {created_content.title}",
        "knowledge_map": {
            "subject": subject,
            "category_code": category_code,
            "level_1": level_1,
            "level_2": level_2,
            "level_3": level_3,
        } if is_mapping_create else None,
    }
    return JsonResponse(payload, status=200 if duplicate_exists else 201)


@role_required("teacher")
def teacher_question_source_import(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    selected_course_slug = (request.GET.get("course") or "").strip().lower()
    requested_content_id = normalize_positive_int(request.GET.get("content_id"), default=0, minimum=1)
    requested_import_job_id = normalize_positive_int(request.GET.get("import_job_id"), default=0, minimum=1)

    success_map = {
        "blocked": "当前知识点已有导入任务正在解析或待确认，请先处理当前任务。",
        "queued": "源文件已上传，候选题识别已进入后台队列。请稍后刷新查看解析结果。",
        "parsed": "源文件已上传并完成候选题识别，请先确认题目再写入公共题池。",
    }

    def get_success_message() -> str:
        op = (request.GET.get("op") or "").strip()
        if op == "confirmed":
            confirmed_count = normalize_positive_int(request.GET.get("confirmed_count"), default=0, minimum=0)
            if confirmed_count > 0:
                return f"确认成功，已写入 {confirmed_count} 道公共题池题目。"
            return "候选题已确认并写入公共题池。"
        return success_map.get(op, "")

    def render_import_page(*, upload_error_message: str = "", upload_success_message: str = "", selected_content_id: int = 0) -> HttpResponse:
        success_message = upload_success_message or get_success_message()
        try:
            context = build_teacher_question_source_import_context(
                portal_user,
                selected_course_slug=selected_course_slug,
                selected_content_id=selected_content_id or requested_content_id,
                selected_import_job_id=requested_import_job_id,
                upload_error_message=upload_error_message,
                upload_success_message=success_message,
            )
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该课程") from exc
        return render(
            request,
            "entry/teacher_question_source_import.html",
            {
                "role_label": ROLE_CONFIG["teacher"]["label"],
                **build_shell_identity_context(request),
                **context,
            },
        )

    if request.method == "POST":
        action = request.POST.get("form_action", "").strip()
        selected_content_id = normalize_positive_int(request.POST.get("content_id"), default=0, minimum=1)
        target_subject = normalize_exam_knowledge_subject(request.POST.get("target_subject") or selected_course_slug or "cpp")
        target_category_code = (request.POST.get("target_category_code") or "").strip().upper()
        target_level_1 = (request.POST.get("target_level_1") or "").strip()
        target_level_2 = (request.POST.get("target_level_2") or "").strip()
        target_level_3 = (request.POST.get("target_level_3") or "").strip()
        try:
            context = build_teacher_question_source_import_context(
                portal_user,
                selected_course_slug=selected_course_slug,
                selected_content_id=selected_content_id,
            )
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该课程") from exc
        if not selected_content_id:
            target_title = build_knowledge_path_title(target_level_1, target_level_2, target_level_3)
            if target_category_code and target_title:
                matched_content_option = next(
                    (
                        item
                        for item in context["content_options"]
                        if str(item.get("title") or "").strip() == target_title
                        and str(item.get("phase") or item.get("level_label") or "").strip().upper() == target_category_code
                    ),
                    None,
                )
                if matched_content_option:
                    selected_content_id = int(matched_content_option["id"])
                    context = build_teacher_question_source_import_context(
                        portal_user,
                        selected_course_slug=selected_course_slug,
                        selected_content_id=selected_content_id,
                    )
        content_option_ids = {item["id"] for item in context["content_options"]}
        selected_content_option = context["selected_content_option"]
        selected_content = (
            CourseContent.objects.select_related("course", "level")
            .filter(id=selected_content_id)
            .first()
            if selected_content_id in content_option_ids
            else None
        )

        if action == "upload_choice_file":
            if not target_category_code:
                return render_import_page(upload_error_message="请先选择级别。", selected_content_id=selected_content_id)
            if not target_level_1:
                return render_import_page(upload_error_message="请先选择一级知识点。", selected_content_id=selected_content_id)
            source_file = request.FILES.get("source_file")
            if not source_file:
                return render_import_page(upload_error_message="请先选择一个文件再上传。", selected_content_id=selected_content_id)
            source_type = detect_homework_source_type(source_file.name)
            if not source_type:
                return render_import_page(upload_error_message="当前只支持 pdf / image / html / txt / docx / xlsx 文件。", selected_content_id=selected_content_id)
            source_sha256 = compute_uploaded_file_sha256(source_file)
            with transaction.atomic():
                import_scope_queryset = (
                    HomeworkImportJob.objects.select_for_update()
                    .filter(
                        teacher=portal_user,
                        assignment__isnull=True,
                        content=selected_content,
                        is_active=True,
                    )
                )
                expire_stale_homework_import_jobs(import_scope_queryset)
                open_import_job = (
                    import_scope_queryset
                    .filter(parse_status__in=OPEN_HOMEWORK_IMPORT_STATUSES)
                    .order_by("-created_at", "-id")
                    .first()
                )
                if open_import_job:
                    redirect_params = {
                        "op": "blocked",
                        "content_id": selected_content_id,
                        "import_job_id": open_import_job.id,
                    }
                    if selected_course_slug:
                        redirect_params["course"] = selected_course_slug
                    return redirect(
                        build_redirect_with_query(
                            reverse("teacher-question-source-import"),
                            params=redirect_params,
                            anchor="candidate-editor",
                        )
                    )
                import_job = HomeworkImportJob.objects.create(
                    teacher=portal_user,
                    assignment=None,
                    content=selected_content,
                    source_file=source_file,
                    source_filename=source_file.name,
                    source_sha256=source_sha256,
                    source_type=source_type,
                    parse_status=HomeworkImportJob.STATUS_UPLOADED,
                    parse_notes=(
                        QUESTION_SOURCE_KNOWLEDGE_MARKER
                        + json.dumps(
                            {
                                "subject": target_subject,
                                "category_code": target_category_code,
                                "level_1": target_level_1,
                                "level_2": target_level_2,
                                "level_3": target_level_3,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                    is_active=True,
                )
            redirect_params = {"op": "queued", "content_id": selected_content_id}
            if selected_course_slug:
                redirect_params["course"] = selected_course_slug
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-question-source-import"),
                    params=redirect_params,
                    anchor="candidate-editor",
                )
            )

        if action == "confirm_import_job":
            import_job_id = normalize_positive_int(request.POST.get("import_job_id"), default=0, minimum=1)
            import_job = (
                HomeworkImportJob.objects.filter(
                    id=import_job_id,
                    teacher=portal_user,
                    assignment__isnull=True,
                    is_active=True,
                )
                .first()
            )
            if import_job is None:
                raise Http404("未找到该公共题池导入任务")
            candidate_count = normalize_positive_int(request.POST.get("candidate_count"), default=0, minimum=0)
            if candidate_count <= 0:
                return render_import_page(upload_error_message="确认失败：候选题提交数据为空，请刷新页面后重试。", selected_content_id=selected_content_id or import_job.content_id)
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
                if (
                    not payloads[-1]["included"]
                    and not payloads[-1]["stem"].strip()
                    and not any(value.strip() for value in payloads[-1]["options"].values())
                    and not payloads[-1]["correct_answer"].strip()
                    and not payloads[-1]["analysis"].strip()
                ):
                    payloads.pop()
            if not has_candidate_fields or not payloads:
                return render_import_page(upload_error_message="确认失败：候选题提交数据为空，请刷新页面后重试。", selected_content_id=selected_content_id or import_job.content_id)
            try:
                created_questions = confirm_question_source_import_job(import_job, payloads, operator=portal_user)
            except HomeworkImportParseError as exc:
                import_job.candidates_json = encode_sql_ascii_json_text(payloads)
                import_job.parse_status = HomeworkImportJob.STATUS_PARSED
                import_job.save(update_fields=["candidates_json", "parse_status", "updated_at"])
                return render_import_page(upload_error_message=f"确认失败：{exc}", selected_content_id=selected_content_id or import_job.content_id)
            redirect_params = {
                "op": "confirmed",
                "confirmed_count": len(created_questions),
                "content_id": import_job.content_id or selected_content_id,
            }
            if selected_course_slug:
                redirect_params["course"] = selected_course_slug
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-question-source-import"),
                    params=redirect_params,
                    anchor="candidate-editor",
                )
            )

    return render_import_page(selected_content_id=requested_content_id)


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
    can_import_students = teacher_can_import_students(portal_user)
    if student_import_modal_should_open and not can_import_students:
        student_import_modal_should_open = False

    if request.method == "POST" and (request.POST.get("form_action") or "").strip() == "import_students_csv":
        if not teacher_can_import_students(portal_user):
            return HttpResponseForbidden("当前账号不能导入学生。")

        student_import_modal_should_open = True
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
        can_import_students = teacher_can_import_students(portal_user)
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
    exam_success_map = {
        "created": "考试已创建，学生端现在可以在“我的考试”里看到。",
    }

    def render_detail(
        *,
        homework_form_values: dict[str, object] | None = None,
        homework_error_message: str = "",
        homework_summary_form_values: dict[str, object] | None = None,
        homework_summary_error_message: str = "",
        exam_form_values: dict[str, object] | None = None,
        exam_error_message: str = "",
    ) -> HttpResponse:
        success_message = homework_success_map.get((request.GET.get("homework_op") or "").strip(), "")
        exam_success_message = exam_success_map.get((request.GET.get("exam_op") or "").strip(), "")
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
                exam_form_values=exam_form_values,
                exam_error_message=exam_error_message,
                exam_success_message=exam_success_message,
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
            description = normalize_preserved_multiline_text(request.POST.get("description", ""))
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
            due_date_value = parse_homework_due_datetime_input(due_date_raw)
            if due_date_value is None:
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
            summary_highlights = normalize_preserved_multiline_text(request.POST.get("summary_highlights", "")).strip()
            summary_areas_for_growth = normalize_preserved_multiline_text(request.POST.get("summary_areas_for_growth", "")).strip()
            form_values = {
                "title": title,
                "start_date": start_date_raw,
                "end_date": end_date_raw,
                "summary_html": summary_html_input,
                "highlights": summary_highlights,
                "areas_for_growth": summary_areas_for_growth,
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
            if not (summary_html or summary_highlights or summary_areas_for_growth):
                return render_detail(
                    homework_summary_form_values=form_values,
                    homework_summary_error_message="请上传 HTML 文件、填写总结 HTML，或补充亮点表现 / 待提升点。",
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
            summary = None
            if summary_html:
                summary = HomeworkSummary.objects.create(
                    title=final_title,
                    summary_html=summary_html,
                    created_by=portal_user,
                )
                HomeworkAssignment.objects.filter(
                    id__in=[assignment.id for assignment in matched_assignments]
                ).update(summary=summary, updated_at=timezone.now())
            primary_feedback_assignment = select_primary_feedback_assignment(matched_assignments)
            if primary_feedback_assignment is not None and (summary_highlights or summary_areas_for_growth):
                primary_feedback_assignment.highlights = summary_highlights
                primary_feedback_assignment.areas_for_growth = summary_areas_for_growth
                primary_feedback_assignment.save(update_fields=["highlights", "areas_for_growth", "updated_at"])
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
            highlights = normalize_preserved_multiline_text(request.POST.get("highlights", "")).strip()
            areas_for_growth = normalize_preserved_multiline_text(request.POST.get("areas_for_growth", "")).strip()
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
            previous_highlights = assignment.highlights
            previous_areas_for_growth = assignment.areas_for_growth
            assignment.mark_reviewed(teacher_comment=teacher_comment)
            assignment.highlights = highlights
            assignment.areas_for_growth = areas_for_growth
            update_fields = []
            if assignment.teacher_comment != previous_comment:
                update_fields.append("teacher_comment")
            if assignment.highlights != previous_highlights:
                update_fields.append("highlights")
            if assignment.areas_for_growth != previous_areas_for_growth:
                update_fields.append("areas_for_growth")
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
def teacher_student_exam_detail(request: HttpRequest, student_id: int, session_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = build_teacher_student_exam_detail_context(portal_user, student_id, session_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该考试记录") from exc
    return render_shell_page(request, "teacher", "entry/teacher_student_exam_detail.html", context)


@role_required("teacher")
def teacher_homework_builder(request: HttpRequest, student_id: int, assignment_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    requested_import_job_id = normalize_positive_int(request.GET.get("import_job_id"), default=0, minimum=1)

    success_map = {
        "blocked": "当前作业已有导入任务正在解析或待确认，请先处理当前任务。",
        "queued": "源文件已上传，候选题识别已进入后台队列。请稍后刷新查看解析结果。",
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
                selected_import_job_id=requested_import_job_id,
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
                import_scope_queryset = (
                    HomeworkImportJob.objects.select_for_update()
                    .filter(
                        assignment=locked_assignment,
                        is_active=True,
                    )
                )
                expire_stale_homework_import_jobs(import_scope_queryset)
                open_import_job = (
                    import_scope_queryset
                    .filter(parse_status__in=OPEN_HOMEWORK_IMPORT_STATUSES)
                    .order_by("-created_at", "-id")
                    .first()
                )
                if open_import_job:
                    return redirect(
                        build_redirect_with_query(
                            reverse("teacher-homework-builder", args=[student_id, assignment_id]),
                            params={"op": "blocked", "import_job_id": open_import_job.id},
                            anchor="candidate-editor",
                        )
                    )
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
            return redirect(
                build_redirect_with_query(
                    reverse("teacher-homework-builder", args=[student_id, assignment_id]),
                    params={"op": "queued"},
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
                if (
                    not payloads[-1]["included"]
                    and not payloads[-1]["stem"].strip()
                    and not any(value.strip() for value in payloads[-1]["options"].values())
                    and not payloads[-1]["correct_answer"].strip()
                    and not payloads[-1]["analysis"].strip()
                ):
                    payloads.pop()
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
    active_tab = request.GET.get("tab", "students")
    page_shell = build_teacher_page_shell(get_portal_user_from_request(request), active_tab=active_tab)
    page_shell["teacher_form_values"] = {
        "name": "",
        "username": "",
        "course_ids": [],
        "subject_manual": "",
        "phone": "",
    }
    page_shell["teacher_course_options"] = [
        {"value": str(course.id), "label": course.title}
        for course in Course.objects.order_by("id")
    ]
    page_shell["teacher_form_error"] = ""
    success_messages = {
        "1": "教师账号已新增，默认密码为 123456。",
        "updated": "教师信息已更新。",
        "deleted": "教师已标记为离职。",
        "restored": "教师已恢复为在职。",
    }
    page_shell["teacher_form_success"] = success_messages.get(str(request.GET.get("teacher_saved") or request.GET.get("teacher_created") or ""), "")

    if request.method == "POST":
        form_action = str(request.POST.get("form_action") or "").strip()
        if form_action not in {"create_teacher", "update_teacher", "delete_teacher", "restore_teacher"}:
            return redirect(f"{reverse('principal-dashboard')}?tab=teachers")

        page_shell["active_tab"] = "teachers"

        if form_action in {"delete_teacher", "restore_teacher"}:
            teacher_id = normalize_positive_int(request.POST.get("teacher_id"), default=0, minimum=1)
            try:
                teacher_profile = Teacher.objects.select_related("user").get(
                    id=teacher_id,
                    user__role=PortalUser.ROLE_TEACHER,
                )
            except Teacher.DoesNotExist as exc:
                raise Http404("未找到该教师") from exc
            is_active = form_action == "restore_teacher"
            teacher_profile.is_active = is_active
            teacher_profile.save(update_fields=["is_active", "updated_at"])
            teacher_profile.user.is_active = is_active
            teacher_profile.user.save(update_fields=["is_active", "updated_at"])
            result_key = "restored" if is_active else "deleted"
            return redirect(f"{reverse('principal-dashboard')}?tab=teachers&teacher_saved={result_key}")

        teacher_name = str(request.POST.get("teacher_name") or "").strip()
        teacher_username = str(request.POST.get("teacher_username") or "").strip()
        raw_teacher_course_ids = request.POST.getlist("teacher_course_ids")
        if not raw_teacher_course_ids and request.POST.get("teacher_course_id"):
            raw_teacher_course_ids = [request.POST.get("teacher_course_id") or ""]
        teacher_course_ids = []
        for raw_course_id in raw_teacher_course_ids:
            course_id = normalize_positive_int(raw_course_id, default=0, minimum=1)
            if course_id and course_id not in teacher_course_ids:
                teacher_course_ids.append(course_id)
        teacher_phone = normalize_phone(request.POST.get("teacher_phone"))
        manual_subject_parts = [
            part.strip()
            for part in re.split(r"[、,，;；\n]+", str(request.POST.get("teacher_subject_manual") or ""))
            if part.strip()
        ]
        courses = resolve_teacher_courses_from_subjects(teacher_course_ids, manual_subject_parts)
        subject_parts = []
        seen_subject_keys = set()
        for part in [*(course.title for course in courses), *manual_subject_parts]:
            subject_key = normalize_knowledge_map_subject(part) or str(part).strip().lower()
            if part and subject_key not in seen_subject_keys:
                subject_parts.append(part)
                seen_subject_keys.add(subject_key)
        teacher_subject = "、".join(subject_parts)
        resolved_teacher_username = teacher_username or teacher_name
        page_shell["teacher_form_values"] = {
            "name": teacher_name,
            "username": teacher_username,
            "course_ids": [str(course_id) for course_id in teacher_course_ids],
            "subject_manual": "、".join(manual_subject_parts),
            "phone": teacher_phone,
        }

        if not teacher_name or not subject_parts or not teacher_phone:
            page_shell["teacher_form_error"] = "请完整填写教师姓名、所教授学科和手机号。"
        elif form_action == "create_teacher" and PortalUser.objects.filter(username=resolved_teacher_username).exists():
            page_shell["teacher_form_error"] = "当前用户名已被注册，请重新选择用户名。"
        elif form_action == "create_teacher" and PortalUser.objects.filter(phone=teacher_phone).exists():
            page_shell["teacher_form_error"] = "该手机号已存在账号，请换一个手机号或先核对已有账号。"
        elif form_action == "update_teacher":
            teacher_id = normalize_positive_int(request.POST.get("teacher_id"), default=0, minimum=1)
            try:
                teacher_profile = Teacher.objects.select_related("user").get(
                    id=teacher_id,
                    user__role=PortalUser.ROLE_TEACHER,
                )
            except Teacher.DoesNotExist as exc:
                raise Http404("未找到该教师") from exc
            duplicate_user = (
                PortalUser.objects.filter(phone=teacher_phone)
                .exclude(id=teacher_profile.user_id)
                .exists()
            )
            if duplicate_user:
                page_shell["teacher_form_error"] = "该手机号已存在账号，请换一个手机号或先核对已有账号。"
            else:
                with transaction.atomic():
                    teacher_profile.display_name = teacher_name
                    teacher_profile.phone = teacher_phone
                    teacher_profile.subject = teacher_subject
                    teacher_profile.save(update_fields=["display_name", "phone", "subject", "updated_at"])
                    teacher_profile.user.full_name = teacher_name
                    teacher_profile.user.phone = teacher_phone
                    teacher_profile.user.save(update_fields=["full_name", "phone", "updated_at"])
                    teacher_profile.courses.set(courses)
                return redirect(f"{reverse('principal-dashboard')}?tab=teachers&teacher_saved=updated")
        else:
            with transaction.atomic():
                teacher_user = PortalUser(
                    username=resolved_teacher_username,
                    role=PortalUser.ROLE_TEACHER,
                    full_name=teacher_name,
                    phone=teacher_phone,
                    is_active=True,
                )
                teacher_user.set_password(DEFAULT_IMPORTED_ACCOUNT_PASSWORD)
                teacher_user.save()
                teacher_profile = Teacher.objects.create(
                    user=teacher_user,
                    display_name=teacher_name,
                    phone=teacher_phone,
                    subject=teacher_subject,
                    is_active=True,
                )
                teacher_profile.courses.set(courses)
            return redirect(f"{reverse('principal-dashboard')}?tab=teachers&teacher_saved=1")

    return render_role_page(request, "principal", page_shell)


@api_role_required("principal")
def api_principal_get_students_info(request: HttpRequest) -> JsonResponse:
    try:
        anchor_date = normalize_student_learning_anchor_date(request.GET.get("anchor_date"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    students = Student.objects.select_related("teacher_user").all()
    payload = build_student_learning_overview(
        students=students,
        anchor_date=anchor_date,
        include_teacher_fields=True,
    )
    return JsonResponse(payload)


@api_role_required("parent")
def api_parent_get_my_child(request: HttpRequest) -> JsonResponse:
    try:
        anchor_date = normalize_student_learning_anchor_date(request.GET.get("anchor_date"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    portal_user = get_portal_user_from_request(request)
    children = Student.objects.select_related("teacher_user").filter(parent_user=portal_user)
    payload = build_student_learning_overview(
        students=children,
        anchor_date=anchor_date,
        include_teacher_fields=False,
        include_oj_weekly_stats=True,
    )
    return JsonResponse(
        {
            "anchor_date": payload["anchor_date"],
            "children": payload["students"],
        }
    )
