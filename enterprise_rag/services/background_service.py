"""持久化、带数据校验的页面后台存储."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import threading
import uuid

from PIL import Image, UnidentifiedImageError

from ..config import RUNTIME_DATA_DIR

DATA_DIR = RUNTIME_DATA_DIR
BACKGROUND_DIR = DATA_DIR / "backgrounds"
BACKGROUND_CONFIG = DATA_DIR / "background.json"
MAX_BACKGROUND_SIZE_BYTES = 5 * 1024 * 1024
MAX_BACKGROUND_WIDTH = 4096
MAX_BACKGROUND_HEIGHT = 4096
MAX_BACKGROUND_PIXELS = 16_000_000

_BACKGROUND_LOCK = threading.RLock()
_IMAGE_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


class BackgroundImageError(ValueError):
    """上传的背景图片无效时抛出此异常."""


def _ensure_data_dir() -> None:
    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)


def _detect_image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _validate_image(data: bytes, extension: str) -> str:
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            if (
                width > MAX_BACKGROUND_WIDTH
                or height > MAX_BACKGROUND_HEIGHT
                or width * height > MAX_BACKGROUND_PIXELS
            ):
                raise BackgroundImageError(
                    "背景图片分辨率过大，请使用不超过 4096 x 4096 像素的图片"
                )
            image.verify()
            actual_type = (image.format or "").lower()
    except (UnidentifiedImageError, OSError) as exc:
        raise BackgroundImageError("图片文件已损坏或不是有效图片") from exc

    if actual_type == "jpeg":
        actual_type = "jpg"
    normalized_extension = "jpg" if extension == "jpeg" else extension
    if actual_type != normalized_extension:
        raise BackgroundImageError("图片内容与扩展名不匹配")
    return actual_type


def _read_config() -> dict:
    if not BACKGROUND_CONFIG.exists():
        return {}
    try:
        config = json.loads(BACKGROUND_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return config if isinstance(config, dict) else {}


def _write_config(config: dict) -> None:
    temp_path = BACKGROUND_CONFIG.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(config, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(temp_path, BACKGROUND_CONFIG)


def _safe_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    candidate = Path(filename).name
    if candidate != filename or not candidate.startswith("background_"):
        return None
    return candidate


def save_background(uploaded_file) -> str:
    """校验并保存上传图片，返回其存储文件名."""
    filename = getattr(uploaded_file, "name", "")
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in _IMAGE_TYPES:
        raise BackgroundImageError("仅支持 PNG、JPG、JPEG 或 WEBP 图片")

    data = bytes(uploaded_file.getvalue())
    if not data:
        raise BackgroundImageError("图片文件为空")
    if len(data) > MAX_BACKGROUND_SIZE_BYTES:
        raise BackgroundImageError("背景图片不能超过 5MB")

    actual_type = _validate_image(data, extension)

    with _BACKGROUND_LOCK:
        _ensure_data_dir()
        previous_name = _safe_filename(_read_config().get("filename"))
        stored_name = f"background_{uuid.uuid4().hex}.{actual_type}"
        target_path = BACKGROUND_DIR / stored_name
        temp_path = BACKGROUND_DIR / f".{stored_name}.tmp"
        temp_path.write_bytes(data)
        os.replace(temp_path, target_path)
        _write_config({"filename": stored_name, "mime_type": _IMAGE_TYPES[extension]})

        if previous_name and previous_name != stored_name:
            previous_path = BACKGROUND_DIR / previous_name
            try:
                previous_path.unlink(missing_ok=True)
            except OSError:
                # 新的背景文件已提交，需清理旧文件
                # 出现故障时，上传界面不应显示上传失败。
                pass
        return stored_name


def get_current_background_name() -> str | None:
    with _BACKGROUND_LOCK:
        filename = _safe_filename(_read_config().get("filename"))
        if not filename or not (BACKGROUND_DIR / filename).is_file():
            return None
        return filename


def get_background_data_url() -> str | None:
    """返回适用于CSS注入的安全数据URL，默认情况下返回`None`."""
    with _BACKGROUND_LOCK:
        config = _read_config()
        filename = _safe_filename(config.get("filename"))
        if not filename:
            return None
        path = BACKGROUND_DIR / filename
        if not path.is_file():
            return None
        data = path.read_bytes()
        detected_type = _detect_image_type(data)
        if not detected_type:
            return None
        mime_type = _IMAGE_TYPES[detected_type]
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"


def clear_background() -> None:
    """恢复默认背景并移除当前已上传文件."""
    with _BACKGROUND_LOCK:
        filename = _safe_filename(_read_config().get("filename"))
        try:
            BACKGROUND_CONFIG.unlink(missing_ok=True)
        except OSError:
            pass
        if filename:
            try:
                (BACKGROUND_DIR / filename).unlink(missing_ok=True)
            except OSError:
                pass
