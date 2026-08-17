# 嵌入模型：加载与管理向量化模型（bge-small-zh）
import os
import threading

from ..config import MODEL_NAME
from ..utils.model_readiness import get_hf_model_readiness, resolve_hf_model_source

_model = None
_model_lock = threading.Lock()


def load_embedding_model():
    # Windows 下 scikit-learn 与 PyTorch 可能重复初始化 Intel OpenMP。
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    from sentence_transformers import SentenceTransformer

    model_source, local_only = resolve_hf_model_source(MODEL_NAME)
    return SentenceTransformer(model_source, local_files_only=local_only)

def get_embedding_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = load_embedding_model()
    return _model

def get_model_status():
    global _model
    return "已加载" if _model is not None else "待加载"


def get_model_readiness() -> tuple[bool, str]:
    """检查离线部署所需模型是否已在本地缓存，避免首次提问才报错。"""
    if _model is not None:
        return True, "已加载"
    return get_hf_model_readiness(
        MODEL_NAME,
        missing_message="离线模式缺少嵌入模型",
    )
