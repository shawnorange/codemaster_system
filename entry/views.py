import logging
import json
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Max
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
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
    create_exam_practice_session,
    create_exam_for_students,
    create_or_get_exam_session_by_access_code,
    generate_unique_exam_access_code,
    grade_exam_session,
    normalize_exam_answer,
    normalize_exam_options,
    record_exam_proctor_event,
    start_exam_session,
)
from .exam_paper_import import (
    ExamPaperImportConfirmError,
    build_default_question_analysis_md,
    clean_imported_markdown,
    combine_raw_ocr_page_markdown,
    confirm_exam_question_bank_import_job,
    detect_exam_import_source_type,
    format_exam_markdown_for_teacher_edit,
    get_supported_exam_import_extensions,
    is_raw_ocr_page_question,
    materialize_raw_ocr_paper_questions,
    restore_exam_markdown_code_fences_from_original,
    separate_programming_reference_solutions_for_paper,
    split_ocr_markdown_into_question_blocks,
)
from .homework_online import (
    HomeworkImportParseError,
    compute_uploaded_file_sha256,
    confirm_homework_import_job,
    confirm_question_source_import_job,
    detect_homework_source_type,
    encode_sql_ascii_json_text,
    grade_homework_submission,
)
from .manual_overrides import update_question_manual_override
from .models import (
    Course,
    CourseContent,
    CourseLevel,
    ExamPaper,
    ExamQuestion,
    ExamQuestionBankAsset,
    ExamQuestionBankImportJob,
    ExamQuestionBankItem,
    ExamQuestionBankOption,
    ExamQuestionBankPaper,
    ExamQuestionBankQuestion,
    ExamSession,
    HomeworkAssignment,
    HomeworkImportJob,
    HomeworkSubmission,
    HomeworkSummary,
    LessonHourLedger,
    PortalUser,
    Question,
    RewardRecord,
    Student,
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
    render_exam_markdown_for_display,
    get_student_by_user,
    set_student_content_visibility,
    student_has_content_access,
    get_teacher_student_homework_contents,
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


EXAM_IMPORT_MANUAL_CHOICE_KEYS = ("A", "B", "C", "D")


def is_manual_choice_review_import(import_job: ExamQuestionBankImportJob) -> bool:
    source_type = detect_exam_import_source_type(import_job.source_filename or import_job.source_pdf.name)
    course_title = import_job.course.title if import_job.course_id and import_job.course else ""
    return source_type == "image" or str(course_title or "").strip().lower() == "scratch"


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
    return {
        "id": item.id,
        "title": item.title,
        "course_title": item.course.title if item.course_id and item.course else "未绑定学科",
        "level_code": item.level_code,
        "source_filename": item.source_filename,
        "source_pdf_id": item.source_pdf_id,
        "status_text": item.get_status_display(),
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
        else "已入库" if is_imported else "OCR 完成后可确认入库",
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
        "preview_href": reverse("teacher-exam-bank-paper-preview", args=[paper.id]),
        "edit_href": reverse("teacher-exam-bank-paper-edit", args=[paper.id]),
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
        "programming_json_text": "",
        "full_json_text": "",
        "options": [
            {
                "key": key,
                "text": str(option_map.get(key) or ""),
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
    return {
        "page_title": f"{'编辑' if is_edit else '预览'}试卷 · {paper.title}",
        "page_description": "发布前先检查整张试卷的题面、选项、答案、解析和题图。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "考试管理", "href": reverse("teacher-exams")},
            {"label": "发布考试", "href": f"{reverse('teacher-exams')}#available-exam-papers"},
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
        "preview_href": reverse("teacher-exam-bank-paper-preview", args=[paper.id]),
        "edit_href": reverse("teacher-exam-bank-paper-edit", args=[paper.id]),
        "back_href": f"{reverse('teacher-exams')}#available-exam-papers",
    }


def update_exam_bank_paper_from_request(paper: ExamQuestionBankPaper, request: HttpRequest) -> None:
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
            question.save(update_fields=["question_type", "stem_md", "answer_json", "analysis_md", "updated_at"])

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
            options: dict[str, str] = {
                option_key: normalize_preserved_multiline_text(
                    request.POST.get(f"new_question_{key}_option_{option_key.lower()}") or ""
                ).strip()
                for option_key in option_keys
            }
            has_any_value = bool(stem_md or answer_text or analysis_md or any(options.values()))
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
                full_json={"source": "teacher_manual_edit"},
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
    return render_shell_page(request, "student", "entry/student_homework_list.html", context)


@role_required("student")
def student_exam_list(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)

    def render_exam_list(*, error_message: str = "", success_message: str = "") -> HttpResponse:
        context = build_student_exam_list_context(
            portal_user,
            error_message=error_message,
            success_message=success_message,
        )
        return render_shell_page(request, "student", "entry/student_exam_list.html", context)

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
            context["success_message"] = "考试已提交并自动判分。"
        elif request.GET.get("op") == "practice_started":
            context["success_message"] = "练习场次已创建，可以开始作答。"
        elif request.GET.get("op") == "code_accepted":
            context["success_message"] = "口令校验成功，可以开始考试。"
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

        if action != "submit_exam":
            return render_exam(error_message="请选择有效的考试操作。")

        selected_answers = {}
        explanation_texts = {}
        for key, value in request.POST.items():
            if key.startswith("question_"):
                question_id = normalize_positive_int(key.split("_", 1)[1], default=0, minimum=1)
                if question_id:
                    selected_answers[question_id] = str(value).strip().upper()
            elif key.startswith("explanation_"):
                question_id = normalize_positive_int(key.split("_", 1)[1], default=0, minimum=1)
                if question_id:
                    explanation_texts[question_id] = str(value).strip()
        try:
            graded_session = grade_exam_session(
                session,
                student=student,
                selected_answers=selected_answers,
                explanation_texts=explanation_texts,
            )
        except ExamError as exc:
            return render_exam(
                error_message=str(exc),
                selected_answer_overrides=selected_answers,
                explanation_overrides=explanation_texts,
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
        session.refresh_from_db(fields=["switch_count"])
    except (ExamSession.DoesNotExist, ExamError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"event_id": event.id, "switch_count": session.switch_count})


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
    return ExamPaper.objects.filter(description__contains=marker).exists()


def get_exam_bank_paper_ids_with_exam_management_records() -> set[int]:
    bank_paper_ids: set[int] = set()
    descriptions = ExamPaper.objects.exclude(description="").values_list("description", flat=True)
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
        if is_choice_like and (not options or correct_answer not in options):
            continue
        first_asset = next(
            (
                asset
                for asset in bank_question.assets.filter(asset_role="content").order_by("id")
                if asset.asset_role == "content" and asset.relative_path
            ),
            None,
        )
        stem = clean_bank_question_stem_for_exam(
            bank_question,
            bank_paper,
            remove_visual_placeholders=bool(first_asset),
        )
        ExamQuestion.objects.update_or_create(
            paper=exam_paper,
            question_no=bank_question.question_no,
            defaults={
                "question_type": bank_question.question_type,
                "stem": stem,
                "options_json": options,
                "correct_answer": correct_answer,
                "analysis": bank_question.analysis_md,
                "score": "2.00" if is_choice_like else "0.00",
                "wrong_point_label": bank_paper.level,
                "image_path": first_asset.relative_path if first_asset else "",
                "source_snapshot_json": {
                    "created_from": "exam_question_bank_paper",
                    "bank_paper_id": bank_paper.id,
                    "bank_question_id": bank_question.id,
                    "question_uid": bank_question.question_uid,
                    "source_pdf_id": bank_paper.source_pdf_id,
                    "level_code": bank_paper.level,
                    "knowledge_point": bank_paper.title,
                    "question_type": bank_question.question_type,
                },
                "is_active": True,
            },
        )
        synced_count += 1
    return synced_count


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
    return paper


def create_or_update_exam_management_from_bank_paper(
    *,
    teacher: PortalUser,
    bank_paper: ExamQuestionBankPaper,
    schedule: dict[str, object],
) -> tuple[ExamPaper, bool]:
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
        apply_exam_management_schedule(existing_paper, schedule=schedule)
        sync_exam_questions_from_bank_paper(exam_paper=existing_paper, bank_paper=bank_paper)
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
    sync_exam_questions_from_bank_paper(exam_paper=exam_paper, bank_paper=bank_paper)
    return exam_paper, True


@role_required("teacher")
def teacher_students(request: HttpRequest) -> HttpResponse:
    active_tab = request.GET.get("tab", "students")
    return render_role_page(
        request,
        "teacher",
        build_teacher_page_shell(get_portal_user_from_request(request), active_tab=active_tab),
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
        elif op == "bank_paper_deleted":
            delete_mode = str(request.GET.get("mode") or "").strip()
            success_message = "未发布试卷已硬删除。" if delete_mode == "hard" else "可用试卷已删除。"
        elif op in {"bank_paper_added", "bank_paper_exists"}:
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamPaper.objects.filter(id=paper_id, teacher=portal_user, is_active=True).first()
            if paper and op == "bank_paper_added":
                success_message = f"{paper.title} 已加入考试管理列表。"
            elif paper:
                success_message = f"{paper.title} 已在考试管理列表中，发布设置已更新。"
            else:
                success_message = "试卷已加入考试管理列表。"
        elif op == "bank_paper_started":
            paper_id = normalize_positive_int(request.GET.get("paper_id"), default=0, minimum=1)
            paper = ExamPaper.objects.filter(id=paper_id, teacher=portal_user, is_active=True).only("title", "access_code").first()
            success_message = f"{paper.title} 已发布并开始考试，口令：{paper.access_code}。" if paper else "考试已发布并开始。"
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
            except ExamPaper.DoesNotExist:
                return render_exam_page(error_message="未找到可删除的考试。")
            if paper.sessions.filter(is_active=True, status=ExamSession.STATUS_IN_PROGRESS).exists():
                return render_exam_page(error_message="当前正在考试中，不允许删除。")
            paper.is_active = False
            paper.access_code = ""
            paper.save(update_fields=["is_active", "access_code", "updated_at"])
            return redirect(build_redirect_with_query(reverse("teacher-exams"), params={"op": "deleted"}))

        if action == "delete_bank_paper":
            bank_paper_id = normalize_positive_int(request.POST.get("bank_paper_id"), default=0, minimum=1)
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
            op = "bank_paper_started" if schedule.get("start_immediately") else ("bank_paper_added" if created else "bank_paper_exists")
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
def teacher_exam_paper_new(request: HttpRequest) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    upload_error_message = ""
    upload_success_message = ""
    uploaded_pdf_name = ""
    uploaded_pdf_size = 0
    uploaded_pdf_metadata = {"year": "", "month": "", "source_pdf_id": "", "title": ""}
    selected_course_id = normalize_positive_int(request.POST.get("course_id"), default=0, minimum=1)
    selected_level_code = str(request.POST.get("level_code") or "").strip()
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
                selected_course = Course.objects.filter(id=selected_course_id).first()
                if selected_course is None:
                    upload_error_message = "请选择所属学科。"
                elif not selected_level_code:
                    upload_error_message = "请选择所属级别/类别。"
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

    courses = list(Course.objects.order_by("title", "id").values("id", "title"))
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
    context = {
        "page_title": f"识别预览 #{import_job.id}",
        "page_description": "先核对 Qwen OCR 的逐页识别结果，确认后再进入结构化解析和试卷入库。",
        "breadcrumbs": [
            {"label": "教师工作台", "href": f"{reverse('teacher-students')}?tab=students"},
            {"label": "考试管理", "href": reverse("teacher-exams")},
            {"label": "新增试卷", "href": reverse("teacher-exam-paper-new")},
            {"label": f"识别预览 #{import_job.id}"},
        ],
        "import_job": import_job,
        "course_title": import_job.course.title if import_job.course_id and import_job.course else "未绑定学科",
        "status_text": import_job.get_status_display(),
        "created_at_text": timezone.localtime(import_job.created_at).strftime("%Y-%m-%d %H:%M"),
        "updated_at_text": timezone.localtime(import_job.updated_at).strftime("%Y-%m-%d %H:%M"),
        "preview_rows": preview_rows,
        "manual_choice_review_enabled": manual_choice_review_enabled,
        "manual_choice_keys": EXAM_IMPORT_MANUAL_CHOICE_KEYS,
        "confirm_action": reverse("teacher-exam-paper-new"),
        "can_confirm": can_confirm,
        "confirm_button_label": "已确认入库" if is_imported else "确认入库",
        "confirm_disabled_reason": ""
        if can_confirm
        else "当前任务已入库。" if is_imported else "OCR 全部完成后才能确认入库。",
        "back_href": reverse("teacher-exam-paper-new"),
    }
    return render_shell_page(request, "teacher", "entry/teacher_exam_paper_import_job_detail.html", context)


@role_required("teacher")
def teacher_exam_bank_paper_preview(request: HttpRequest, paper_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        context = get_exam_bank_paper_review_context(
            portal_user,
            paper_id,
            mode="preview",
            success_message="试卷内容已保存。" if (request.GET.get("op") or "").strip() == "updated" else "",
        )
    except ExamQuestionBankPaper.DoesNotExist as exc:
        raise Http404("未找到该可用试卷") from exc
    return render_shell_page(request, "teacher", "entry/teacher_exam_bank_paper_preview.html", context)


@role_required("teacher")
def teacher_exam_bank_paper_edit(request: HttpRequest, paper_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    try:
        paper = ExamQuestionBankPaper.objects.filter(id=paper_id, is_active=True).get()
    except ExamQuestionBankPaper.DoesNotExist as exc:
        raise Http404("未找到该可用试卷") from exc

    if request.method == "POST":
        try:
            update_exam_bank_paper_from_request(paper, request)
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

    context = get_exam_bank_paper_review_context(portal_user, paper_id, mode="edit")
    return render_shell_page(request, "teacher", "entry/teacher_exam_bank_paper_edit.html", context)


@role_required("teacher")
def teacher_exam_detail(request: HttpRequest, paper_id: int) -> HttpResponse:
    portal_user = get_portal_user_from_request(request)
    if request.method == "POST":
        action = str(request.POST.get("form_action") or "").strip()
        if action != "mark_exam_question_important":
            return redirect(reverse("teacher-exam-detail", args=[paper_id]))
        question_id = normalize_positive_int(request.POST.get("question_id"), default=0, minimum=1)
        important_note = str(request.POST.get("important_note") or "").strip()
        try:
            question = (
                ExamQuestion.objects.select_related("paper")
                .filter(id=question_id, paper_id=paper_id, paper__teacher=portal_user, paper__is_active=True, is_active=True)
                .get()
            )
        except ExamQuestion.DoesNotExist as exc:
            raise Http404("未找到该考试题目") from exc
        question.is_important = True
        question.important_note = important_note
        question.important_marked_by = portal_user
        question.important_marked_at = timezone.now()
        question.save(update_fields=["is_important", "important_note", "important_marked_by", "important_marked_at", "updated_at"])
        return redirect(
            build_redirect_with_query(
                reverse("teacher-exam-detail", args=[paper_id]),
                params={"op": "important_marked", "question": question.question_no},
                anchor=f"exam-question-{question.id}",
            )
        )
    try:
        context = build_teacher_exam_detail_context(portal_user, paper_id)
    except ObjectDoesNotExist as exc:
        raise Http404("未找到该考试") from exc
    if request.GET.get("op") == "important_marked":
        context["success_message"] = "已标记重点题。"
    return render_shell_page(request, "teacher", "entry/teacher_exam_detail.html", context)


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

    form_values: dict[str, object] | None = (
        {"import_job_id": requested_import_job_id}
        if requested_import_job_id
        else None
    )
    error_message = ""

    if request.method == "POST":
        selected_student_ids = normalize_positive_int_list(request.POST.getlist("student_ids"))
        selected_import_job_ids = normalize_positive_int_list(request.POST.getlist("import_job_id"))
        selected_import_job_id = selected_import_job_ids[0] if len(selected_import_job_ids) == 1 else 0
        selected_content_id = normalize_positive_int(request.POST.get("content_id"), default=0, minimum=1)
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
        elif not selected_import_job_id and not assignment_requirement_text:
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
                            error_message = "当前题目记录没有绑定有效知识点，暂时不能用于批量布置作业。"
                        else:
                            assignment_title = (
                                visible_import_job.assignment.title.strip()
                                if visible_import_job.assignment_id and visible_import_job.assignment
                                else ""
                            )
                            if not assignment_title:
                                assignment_title = source_content.title.strip()
                            assignment_title = assignment_title or visible_import_job.source_filename.strip()
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


@role_required("teacher")
def teacher_question_source_create_content(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return JsonResponse({"error": "仅支持 POST 提交。"}, status=405)

    portal_user = get_portal_user_from_request(request)
    course_slug = (request.POST.get("course") or request.GET.get("course") or "").strip().lower()
    title = (request.POST.get("title") or "").strip()
    level_id = normalize_positive_int(request.POST.get("level_id"), default=0, minimum=1)

    if not course_slug:
        return JsonResponse({"error": "请选择有效课程。"}, status=400)
    if not title:
        return JsonResponse({"error": "请输入知识点名称。"}, status=400)

    try:
        scope = get_teacher_course_scope(portal_user, course_slug)
    except ObjectDoesNotExist:
        return JsonResponse({"error": "当前课程不存在，或你没有该课程权限。"}, status=400)

    available_level_options = get_teacher_question_source_level_options(portal_user, course_slug)
    available_level_ids = {item["id"] for item in available_level_options}
    if level_id not in available_level_ids:
        return JsonResponse({"error": "请选择有效的 Level。"}, status=400)

    selected_level = (
        CourseLevel.objects.select_related("category", "category__course")
        .filter(id=level_id, is_active=True, category__course=scope["course"])
        .first()
    )
    if selected_level is None:
        return JsonResponse({"error": "请选择有效的 Level。"}, status=400)

    duplicate_exists = CourseContent.objects.filter(
        course=scope["course"],
        level=selected_level,
        title__iexact=title,
        is_active=True,
    ).exists()
    if duplicate_exists:
        return JsonResponse({"error": "该知识点已存在"}, status=400)

    generated_slug = build_auto_course_content_slug(scope["course"], selected_level, title)
    route_path = build_knowledge_point_default_route_path(
        scope["course"].slug,
        selected_level.category.slug,
        selected_level.code,
        generated_slug,
    )
    existing_type = (
        CourseContent.objects.filter(course=scope["course"])
        .exclude(content_type="")
        .order_by("id")
        .values_list("content_type", flat=True)
        .first()
    ) or f"{scope['course'].title}{selected_level.category.title}"
    next_sort_order = (
        (CourseContent.objects.filter(level=selected_level).aggregate(max_sort=Max("sort_order"))["max_sort"] or 0)
        + 1
    )

    with transaction.atomic():
        created_content = CourseContent.objects.create(
            course=scope["course"],
            level=selected_level,
            content_type=existing_type,
            slug=generated_slug,
            title=title,
            phase=selected_level.code,
            permission_code=infer_content_permission_code(
                scope["course"].slug,
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
        "label": f"{scope['course'].title} / {selected_level.title} / {created_content.title}",
    }
    return JsonResponse(payload, status=201)


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
        try:
            context = build_teacher_question_source_import_context(
                portal_user,
                selected_course_slug=selected_course_slug,
                selected_content_id=selected_content_id,
            )
        except ObjectDoesNotExist as exc:
            raise Http404("未找到该课程") from exc
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
            if selected_content is None:
                return render_import_page(upload_error_message="请先选择一个有效知识点。", selected_content_id=selected_content_id)
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
    can_import_students = teacher_can_import_students(portal_user) and context["course"].slug == "cpp"
    if student_import_modal_should_open and not can_import_students:
        student_import_modal_should_open = False

    if request.method == "POST" and (request.POST.get("form_action") or "").strip() == "import_students_csv":
        if not teacher_can_import_students(portal_user):
            return HttpResponseForbidden("只有 teacher001 可以导入学生。")

        student_import_modal_should_open = True
        if context["course"].slug != "cpp":
            student_import_error_message = "当前仅支持在 C++ 课程下导入学生。"
        else:
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
        can_import_students = teacher_can_import_students(portal_user) and context["course"].slug == "cpp"
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
            assignment.mark_reviewed(teacher_comment=teacher_comment)
            update_fields = []
            if assignment.teacher_comment != previous_comment:
                update_fields.append("teacher_comment")
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
    return render_role_page(request, "principal", build_principal_page_shell())


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
