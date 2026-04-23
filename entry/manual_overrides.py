from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.utils import timezone

from .models import Question


MANUAL_OVERRIDE_IMAGE_FIELDS = (
    "question_image",
    "code_image",
    "options_image",
)
DEFAULT_MANUAL_OVERRIDE = {
    "question_image": "",
    "code_image": "",
    "options_image": "",
    "is_reviewed": False,
    "review_note": "",
    "updated_at": "",
}
SLOT_TO_FILENAME = {
    "question_image": "question",
    "code_image": "code",
    "options_image": "options",
}
IMAGE_EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def normalize_manual_override_payload(value: Any) -> dict[str, Any]:
    manual_override = deepcopy(value) if isinstance(value, dict) else {}
    normalized = deepcopy(DEFAULT_MANUAL_OVERRIDE)
    normalized.update(manual_override)
    normalized["is_reviewed"] = bool(normalized.get("is_reviewed"))
    normalized["review_note"] = str(normalized.get("review_note") or "").strip()
    normalized["updated_at"] = str(normalized.get("updated_at") or "").strip()
    for field_name in MANUAL_OVERRIDE_IMAGE_FIELDS:
        normalized[field_name] = str(normalized.get(field_name) or "").strip().replace("\\", "/")
    return normalized


def build_media_url(relative_path: str) -> str:
    normalized_path = str(relative_path or "").strip().lstrip("/")
    if not normalized_path:
        return ""
    return f"{settings.MEDIA_URL.rstrip('/')}/{normalized_path}"


def build_manual_override_view_data(value: Any) -> dict[str, Any]:
    manual_override = normalize_manual_override_payload(value)
    view_data = deepcopy(manual_override)
    for field_name in MANUAL_OVERRIDE_IMAGE_FIELDS:
        view_data[f"{field_name}_url"] = build_media_url(manual_override[field_name])
    view_data["has_any_image"] = any(manual_override.get(field_name) for field_name in MANUAL_OVERRIDE_IMAGE_FIELDS)
    view_data["image_fields"] = list(MANUAL_OVERRIDE_IMAGE_FIELDS)
    return view_data


def _guess_image_extension(uploaded_file: Any) -> str:
    suffix = Path(str(getattr(uploaded_file, "name", "") or "")).suffix.lower()
    if suffix in ALLOWED_IMAGE_EXTENSIONS:
        return ".jpg" if suffix == ".jpeg" else suffix
    content_type = str(getattr(uploaded_file, "content_type", "") or "").lower()
    if content_type in IMAGE_EXTENSION_BY_CONTENT_TYPE:
        return IMAGE_EXTENSION_BY_CONTENT_TYPE[content_type]
    raise ValidationError("只支持 JPG、PNG、WEBP 或 GIF 图片上传。")


def validate_manual_override_uploads(files_by_field: dict[str, Any]) -> dict[str, Any]:
    validated_files: dict[str, Any] = {}
    for field_name, uploaded_file in files_by_field.items():
        if field_name not in MANUAL_OVERRIDE_IMAGE_FIELDS or uploaded_file is None:
            continue
        content_type = str(getattr(uploaded_file, "content_type", "") or "").lower()
        if content_type and not content_type.startswith("image/"):
            raise ValidationError(f"{field_name} 不是图片文件。")
        validated_files[field_name] = uploaded_file
    return validated_files


def build_manual_override_storage_dir(question: Question) -> Path:
    return (
        Path("question_assets")
        / "manual_overrides"
        / question.level_code
        / question.content_slug
        / f"q_{question.id}"
    )


def _remove_existing_slot_files(storage: FileSystemStorage, *, question: Question, slot: str) -> None:
    base_dir = Path(storage.location) / build_manual_override_storage_dir(question)
    if not base_dir.exists():
        return
    for candidate in base_dir.glob(f"{SLOT_TO_FILENAME[slot]}.*"):
        relative_candidate = candidate.relative_to(storage.location).as_posix()
        if storage.exists(relative_candidate):
            storage.delete(relative_candidate)


def save_manual_override_image(*, question: Question, slot: str, uploaded_file: Any) -> str:
    if slot not in SLOT_TO_FILENAME:
        raise ValidationError("不支持的截图类型。")

    storage = FileSystemStorage(location=settings.MEDIA_ROOT, base_url=settings.MEDIA_URL)
    extension = _guess_image_extension(uploaded_file)
    relative_dir = build_manual_override_storage_dir(question)
    relative_path = (relative_dir / f"{SLOT_TO_FILENAME[slot]}{extension}").as_posix()

    _remove_existing_slot_files(storage, question=question, slot=slot)
    absolute_dir = Path(storage.location) / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)

    with storage.open(relative_path, "wb") as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)

    return relative_path


def update_question_manual_override(
    *,
    question_id: int,
    files_by_field: dict[str, Any],
    review_note: str,
    is_reviewed: bool,
) -> Question:
    validated_files = validate_manual_override_uploads(files_by_field)

    with transaction.atomic():
        question = Question.objects.select_for_update().get(pk=question_id)
        payload = dict(question.payload or {})
        manual_override = normalize_manual_override_payload(payload.get("manual_override"))

        changed = False
        for field_name, uploaded_file in validated_files.items():
            relative_path = save_manual_override_image(question=question, slot=field_name, uploaded_file=uploaded_file)
            if manual_override.get(field_name) != relative_path:
                manual_override[field_name] = relative_path
                changed = True

        normalized_note = str(review_note or "").strip()
        if manual_override.get("review_note") != normalized_note:
            manual_override["review_note"] = normalized_note
            changed = True
        if bool(manual_override.get("is_reviewed")) != bool(is_reviewed):
            manual_override["is_reviewed"] = bool(is_reviewed)
            changed = True

        if changed:
            manual_override["updated_at"] = timezone.localtime().isoformat(timespec="seconds")
            payload["manual_override"] = manual_override
            question.payload = payload
            question.save(update_fields=["payload"])

        return question
