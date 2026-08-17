"""统一管理模型、检索、分块、缓存、上传和运行目录配置。"""
import hashlib
import logging
import math
import os
from pathlib import Path


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    """读取有限浮点环境变量；格式错误时回退默认值并应用下限。"""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(minimum, value) if minimum is not None else value

# ===== Hugging Face 离线模式 =====
# 离线模式必须由部署环境显式启用。首次安装时默认允许下载模型；
# 生产离线环境则应预置模型后设置 HF_HUB_OFFLINE=1。
os.environ.setdefault("HF_HUB_OFFLINE", os.getenv("HF_HUB_OFFLINE", "0"))

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

# 配置优先级：进程环境 > 项目根目录 .env > 包内 .env。
# 显式部署参数不能被本地文件覆盖；根目录配置仍可覆盖包内示例值。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATHS = (Path(__file__).resolve().parent / ".env", PROJECT_ROOT / ".env")
ENV_SOURCE: Path | None = None
_ENV_SOURCES: dict[str, Path] = {}
_PROCESS_ENV_KEYS = set(os.environ)
for _env_path in ENV_PATHS:
    if _env_path.is_file():
        for _key, _value in dotenv_values(_env_path).items():
            if _key and _value is not None and _key not in _PROCESS_ENV_KEYS:
                os.environ[_key] = _value
                _ENV_SOURCES[_key] = _env_path

# 所有运行数据默认收口到项目根目录下的 data/，避免源码包目录、项目根目录
# 同时出现多份向量库。部署到只读目录或容器时可通过环境变量整体迁移。
RUNTIME_DATA_DIR = Path(
    os.getenv("RAG_DATA_DIR", str(PROJECT_ROOT / "data"))
).expanduser()
# 向量库与 manifest 必须共享同一运行数据根目录，否则可能把新 Chroma
# 与旧清单组合在一起。旧版 RAG_KB_DIR 只允许保持默认值；迁移整个运行
# 数据目录时统一设置 RAG_DATA_DIR。
KB_DATA_DIR = RUNTIME_DATA_DIR / "kb_data"
LOG_DIR = Path(os.getenv("RAG_LOG_DIR", str(RUNTIME_DATA_DIR / "logs"))).expanduser()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = max(1, int(_env_float("LOG_MAX_BYTES", 5 * 1024 * 1024, minimum=1024)))
LOG_BACKUP_COUNT = max(1, int(_env_float("LOG_BACKUP_COUNT", 7, minimum=1)))
_legacy_kb_dir = os.getenv("RAG_KB_DIR")
if _legacy_kb_dir and Path(_legacy_kb_dir).expanduser() != KB_DATA_DIR:
    raise ValueError(
        "RAG_KB_DIR 已停用，不能单独迁移向量库；请改用 RAG_DATA_DIR "
        "整体迁移 kb_data、kb_manifest.sqlite3 与其他运行数据。"
    )

# =========================
# 1. API 与模型配置
# =========================
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_MODEL = os.getenv("OPENAI_MODEL", "qwen3.7-max")
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
# DashScope 与 OpenAI 可能同时配置在同一台机器上。连接 DashScope 时优先使用
# 服务商专用变量，避免把全局 OPENAI_API_KEY 误发给 DashScope 并触发 401。
_USE_DASHSCOPE = "dashscope.aliyuncs.com" in BASE_URL.lower()
API_KEY_ENV_NAME = (
    "DASHSCOPE_API_KEY"
    if _USE_DASHSCOPE and os.getenv("DASHSCOPE_API_KEY")
    else "OPENAI_API_KEY"
)
API_KEY = os.getenv(API_KEY_ENV_NAME)
ENV_SOURCE = _ENV_SOURCES.get(API_KEY_ENV_NAME)
# 本地开发默认不要求口令；部署到局域网或公网前必须显式配置。
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

if API_KEY:
    logger.info(
        "LLM 配置已加载: key_name=%s key_source=%s key_fingerprint=%s model=%s",
        API_KEY_ENV_NAME,
        str(ENV_SOURCE) if ENV_SOURCE else "process_environment",
        hashlib.sha256(API_KEY.encode()).hexdigest()[:12],
        LLM_MODEL,
    )
else:
    logger.warning("未检测到模型服务 API Key，LLM 生成功能不可用")

# =========================
# 3. 上下文与历史长度限制
# =========================
MAX_CONTEXT_LENGTH = 8000

# =========================
# 4. Reranker（重排序）配置
# =========================
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
INITIAL_RETRIEVAL_K = 50
FINAL_TOP_K = 8

# =========================
# 5. 混合检索（Hybrid Search）配置
# =========================
USE_HYBRID_SEARCH = True
HYBRID_ALPHA = 0.5

# =========================
# 6. 父子分块（Parent-Child Chunking）配置
# =========================
PARENT_CHUNK_SIZE = 2000
PARENT_OVERLAP = 200
CHILD_CHUNK_SIZE = 500
CHILD_OVERLAP = 50

# =========================
# 7. 文件上传策略配置
# =========================
UPLOAD_TASK_WORKERS = max(
    1, int(_env_float("UPLOAD_TASK_WORKERS", 1, minimum=1))
)
UPLOAD_PARSE_WORKERS = max(
    1, int(_env_float("UPLOAD_PARSE_WORKERS", 2, minimum=1))
)
UPLOAD_QUEUE_CAPACITY = max(
    UPLOAD_TASK_WORKERS,
    int(_env_float("UPLOAD_QUEUE_CAPACITY", 4, minimum=1)),
)
UPLOAD_MAX_BATCH_MB = max(
    20, int(_env_float("UPLOAD_MAX_BATCH_MB", 200, minimum=20))
)
UPLOAD_STAGING_TTL_SECONDS = int(
    _env_float("UPLOAD_STAGING_TTL_SECONDS", 86_400, minimum=60)
)
VECTOR_WRITE_BATCH_SIZE = max(
    32, int(_env_float("VECTOR_WRITE_BATCH_SIZE", 512, minimum=32))
)
EMBEDDING_BATCH_SIZE = max(
    1, int(_env_float("EMBEDDING_BATCH_SIZE", 32, minimum=1))
)

# =========================
# 8. Reranker 分数阈值
# =========================
RERANK_SCORE_THRESHOLD = _env_float("RERANK_SCORE_THRESHOLD", 0.5, minimum=0.0)
ANSWER_CACHE_TTL_SECONDS = int(_env_float("ANSWER_CACHE_TTL_SECONDS", 86_400, minimum=1))

# 提示词、检索协议或模型行为变化时递增对应版本，确保旧缓存自动失效。
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "grounded-v3")
RETRIEVAL_VERSION = os.getenv("RETRIEVAL_VERSION", "hybrid-rerank-v2")
MODEL_VERSION = os.getenv("MODEL_VERSION", LLM_MODEL)
RERANK_BATCH_SIZE = max(1, int(_env_float("RERANK_BATCH_SIZE", 16, minimum=1)))
DRAFT_ENABLED = os.getenv("DRAFT_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
FALLBACK_ENABLED = os.getenv("FALLBACK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
