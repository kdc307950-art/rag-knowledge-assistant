"""Hugging Face 模型本地可用性检查。"""

from __future__ import annotations

import os
from pathlib import Path


_TRUE_VALUES = {"1", "true", "yes", "on"}


def resolve_hf_model_source(
    model_reference: str | os.PathLike[str],
) -> tuple[str, bool]:
    """优先返回可用的本地模型路径，并标记是否应禁止联网查找。"""
    reference = str(model_reference)
    try:
        model_path = Path(model_reference).expanduser()
        if model_path.is_dir() and (model_path / "config.json").is_file():
            return str(model_path), True
    except (OSError, TypeError, ValueError):
        pass

    try:
        from huggingface_hub import try_to_load_from_cache

        cached_config = try_to_load_from_cache(reference, "config.json")
    except Exception:
        cached_config = None
    if isinstance(cached_config, (str, os.PathLike)):
        config_path = Path(cached_config)
        if config_path.is_file():
            return str(config_path.parent), True

    offline_mode = os.getenv("HF_HUB_OFFLINE", "0").strip().lower() in _TRUE_VALUES
    return reference, offline_mode


def get_hf_model_readiness(
    model_reference: str | os.PathLike[str],
    *,
    missing_message: str,
) -> tuple[bool, str]:
    """判断模型可从本地目录、Hub 缓存或在线下载中取得。"""
    try:
        model_path = Path(model_reference).expanduser()
        if model_path.is_dir() and (model_path / "config.json").is_file():
            return True, "待加载（本地目录可用）"
    except (OSError, TypeError, ValueError):
        pass

    offline_mode = os.getenv("HF_HUB_OFFLINE", "0").strip().lower() in _TRUE_VALUES
    if not offline_mode:
        return True, "待加载（首次使用时下载）"

    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache(str(model_reference), "config.json")
    except Exception:
        cached = None

    # Hub 命中时返回文件路径；真值哨兵 `_CACHED_NO_EXIST` 不是路径，必须视为缺失。
    if isinstance(cached, (str, os.PathLike)):
        return True, "待加载（本地缓存可用）"
    return False, missing_message
