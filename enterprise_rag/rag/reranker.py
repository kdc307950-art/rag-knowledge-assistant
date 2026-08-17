# 重排序：交叉编码器对候选片段精排
import os
import threading

from enterprise_rag.config import RERANKER_MODEL
from enterprise_rag.utils.model_readiness import (
    get_hf_model_readiness,
    resolve_hf_model_source,
)

# 记录当前进程是否已经完成真实加载；仅检查本地缓存无法区分“可用”和“待加载”。
_RERANKER_LOADED = False
_reranker = None
_reranker_lock = threading.Lock()


def load_reranker():
    global _RERANKER_LOADED
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    from sentence_transformers import CrossEncoder

    model_source, local_only = resolve_hf_model_source(RERANKER_MODEL)
    model = CrossEncoder(model_source, max_length=512, local_files_only=local_only)
    _RERANKER_LOADED = True
    return model


def get_reranker():
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                _reranker = load_reranker()
    return _reranker


def get_reranker_readiness() -> tuple[bool, str]:
    """返回重排模型当前状态，区分已加载、待加载和不可用。"""
    if _RERANKER_LOADED:
        return True, f"已加载：{RERANKER_MODEL}"
    return get_hf_model_readiness(
        RERANKER_MODEL,
        missing_message="离线模式缺少重排模型",
    )
