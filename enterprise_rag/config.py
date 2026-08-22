"""统一管理模型、检索、分块、缓存、上传和运行目录配置。"""
import hashlib
import logging
import math
import os
import secrets
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
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("OPENAI_MODEL", "deepseek-v4-flash")
# ``auto`` enables terminal stream usage on DashScope only. Custom
# OpenAI-compatible gateways must opt in explicitly because support for
# ``stream_options`` is not part of every compatibility layer.
LLM_STREAM_USAGE_MODE = os.getenv("LLM_STREAM_USAGE_MODE", "auto").strip().lower()
if LLM_STREAM_USAGE_MODE not in {"auto", "on", "true", "1", "off", "false", "0"}:
    LLM_STREAM_USAGE_MODE = "auto"
LLM_PRICE_TABLE_PATH = Path(
    os.getenv("LLM_PRICE_TABLE_PATH", str(PROJECT_ROOT / "config" / "llm_prices.json"))
).expanduser()
DOCUMENT_GOVERNANCE_PATH = Path(
    os.getenv(
        "RAG_DOCUMENT_GOVERNANCE_PATH",
        str(PROJECT_ROOT / "config" / "document_governance.json"),
    )
).expanduser()
LEGAL_TERMS_PATH = Path(
    os.getenv(
        "RAG_LEGAL_TERMS_PATH",
        str(PROJECT_ROOT / "config" / "legal_terms.json"),
    )
).expanduser()
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
# DashScope、DeepSeek 与 OpenAI 可能同时配置在同一台机器上。连接已识别的
# 服务商时优先使用专用变量，避免把其他服务的 OPENAI_API_KEY 误发出去。
_USE_DASHSCOPE = "dashscope.aliyuncs.com" in BASE_URL.lower()
_USE_DEEPSEEK = "api.deepseek.com" in BASE_URL.lower()
API_KEY_ENV_NAME = (
    "DASHSCOPE_API_KEY"
    if _USE_DASHSCOPE and os.getenv("DASHSCOPE_API_KEY")
    else "DEEPSEEK_API_KEY"
    if _USE_DEEPSEEK and os.getenv("DEEPSEEK_API_KEY")
    else "OPENAI_API_KEY"
)
API_KEY = os.getenv(API_KEY_ENV_NAME)
ENV_SOURCE = _ENV_SOURCES.get(API_KEY_ENV_NAME)
# 本地开发默认不要求口令；部署到局域网或公网前必须显式配置。
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
AUTH_MODE = os.getenv("AUTH_MODE", "legacy").strip().lower()
if AUTH_MODE not in {"legacy", "users"}:
    AUTH_MODE = "legacy"
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "dev").strip().lower()
# Shared answer caches are unsafe until their key includes the complete ACL
# visibility snapshot.  Multi-user therefore defaults to fail-closed caching.
ANSWER_CACHE_ENABLED = os.getenv("ANSWER_CACHE_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
AUTH_DB_PATH = Path(
    os.getenv("AUTH_DB_PATH", str(RUNTIME_DATA_DIR / "auth.sqlite3"))
).expanduser()
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
AUTH_TOKEN_TTL_SECONDS = max(
    300, int(_env_float("AUTH_TOKEN_TTL_SECONDS", 8 * 3600, minimum=300))
)
AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "rag_access")
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")
RAG_ENVIRONMENT = os.getenv("RAG_ENVIRONMENT", "development").strip().lower()

# Quality feedback capture is opt-in because it stores encrypted query/answer
# material for a bounded retention window.  The key must be supplied by the
# deployment environment and must not be generated at runtime.
QUALITY_CAPTURE_ENABLED = os.getenv("QUALITY_CAPTURE_ENABLED", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
QUALITY_DB_PATH = Path(
    os.getenv("QUALITY_DB_PATH", str(RUNTIME_DATA_DIR / "quality.sqlite3"))
).expanduser()
QUALITY_ENCRYPTION_KEY = os.getenv("QUALITY_ENCRYPTION_KEY", "").strip()
QUALITY_RETENTION_DAYS = max(
    1, int(_env_float("QUALITY_RETENTION_DAYS", 30, minimum=1))
)
QUALITY_MAX_QUERY_CHARS = max(
    256, int(_env_float("QUALITY_MAX_QUERY_CHARS", 4000, minimum=256))
)
QUALITY_MAX_ANSWER_CHARS = max(
    512, int(_env_float("QUALITY_MAX_ANSWER_CHARS", 12000, minimum=512))
)
QUALITY_FEEDBACK_LIMIT_PER_HOUR = max(
    1, int(_env_float("QUALITY_FEEDBACK_LIMIT_PER_HOUR", 30, minimum=1))
)


def _production_secret_error(secret: str) -> str | None:
    """Reject obvious low-entropy production secrets without pretending to measure entropy."""
    value = str(secret or "")
    if len(value.encode("utf-8")) < 32:
        return "AUTH_SECRET 必须至少 32 字节"
    lowered = value.strip().lower()
    if lowered in {"change-me", "changeme", "secret", "test-secret", "password"}:
        return "AUTH_SECRET 不能使用默认或测试口令"
    if len(set(value)) < 8:
        return "AUTH_SECRET 字符多样性不足"
    for width in range(1, min(8, len(value) // 2 + 1)):
        if len(value) % width == 0 and value == value[:width] * (len(value) // width):
            return "AUTH_SECRET 不能是重复模式"
    return None


def _require_runtime_data_path(name: str, path: Path) -> Path:
    """Keep stateful security data in the one backup/restore unit."""
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(RUNTIME_DATA_DIR.expanduser().resolve())
    except ValueError as exc:
        raise ValueError(
            f"{name} 必须位于 RAG_DATA_DIR 内；请迁移整个运行数据目录"
        ) from exc
    return resolved


AUTH_DB_PATH = _require_runtime_data_path("AUTH_DB_PATH", AUTH_DB_PATH)
QUALITY_DB_PATH = _require_runtime_data_path("QUALITY_DB_PATH", QUALITY_DB_PATH)


def production_security_error() -> str | None:
    """Return a blocking production-auth error, or ``None`` when ready."""
    if RAG_ENVIRONMENT != "production":
        return None
    if DEPLOYMENT_MODE != "multi_user":
        return "生产环境必须使用 DEPLOYMENT_MODE=multi_user"
    if AUTH_MODE != "users":
        return "生产环境必须使用 AUTH_MODE=users"
    if not AUTH_COOKIE_SECURE:
        return "AUTH_COOKIE_SECURE 必须为 1"
    if not PUBLIC_BASE_URL.lower().startswith("https://"):
        return "PUBLIC_BASE_URL 必须使用 HTTPS"
    if not ALERT_WEBHOOK_URL:
        return "生产环境必须配置 ALERT_WEBHOOK_URL（监控告警通知渠道）"
    return _production_secret_error(AUTH_SECRET)


def auth_readiness() -> dict:
    """Validate deployment/authentication mode without exposing secrets."""
    mode = DEPLOYMENT_MODE
    result = {
        "mode": mode,
        "configured": False,
        "code": "unknown",
        "detail": "",
        "database": "not_checked",
        "admin_count": 0,
    }
    production_error = production_security_error()
    if production_error:
        result.update(
            code="production_security_not_ready",
            detail=production_error,
        )
        return result
    if mode not in {"dev", "single_user", "multi_user"}:
        result.update(code="invalid_deployment_mode", detail="DEPLOYMENT_MODE 无效")
        return result
    if mode == "dev":
        if AUTH_MODE != "legacy":
            result.update(
                code="invalid_mode_combination",
                detail="dev 模式要求 AUTH_MODE=legacy",
            )
            return result
        result.update(
            configured=True,
            code="ok",
            detail="开发模式未启用用户鉴权",
            warning="authentication_disabled",
        )
        return result
    if mode == "single_user":
        if AUTH_MODE != "legacy":
            result.update(
                code="invalid_mode_combination",
                detail="single_user 模式要求 AUTH_MODE=legacy",
            )
            return result
        if not APP_PASSWORD.strip():
            result.update(code="auth_unconfigured", detail="single_user 模式缺少 APP_PASSWORD")
            return result
        result.update(
            configured=True,
            code="ok",
            detail="单用户口令鉴权已配置",
            warning=("weak_app_password" if len(APP_PASSWORD) < 12 else None),
        )
        return result

    if AUTH_MODE != "users":
        result.update(
            code="invalid_mode_combination",
            detail="multi_user 模式要求 AUTH_MODE=users",
        )
        return result
    if not AUTH_SECRET.strip():
        result.update(code="auth_unconfigured", detail="multi_user 模式缺少 AUTH_SECRET")
        return result
    try:
        from .auth.users import UserStore

        health = UserStore(AUTH_DB_PATH).readiness()
    except Exception as exc:
        result.update(code="auth_database_error", detail=str(exc)[:200])
        return result
    result.update(
        database=health.get("database", "error"),
        admin_count=health.get("active_admin_count", 0),
    )
    if not health.get("readable") or not health.get("writable"):
        result.update(code="auth_database_error", detail="用户数据库不可读写")
        return result
    if health.get("quick_check") != "ok":
        result.update(code="auth_database_corrupt", detail="用户数据库 quick_check 未通过")
        return result
    if not health.get("roles_valid", False):
        result.update(code="auth_database_error", detail="用户角色数据无效")
        return result
    if int(health.get("active_admin_count", 0)) < 1:
        result.update(code="auth_admin_missing", detail="用户库没有 active admin")
        return result
    result.update(configured=True, code="ok", detail="多用户 Bearer Token 与 ACL 已配置")
    return result
METRICS_TOKEN = os.getenv("METRICS_TOKEN", "")
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
BACKUP_DIR = Path(os.getenv("RAG_BACKUP_DIR", str(PROJECT_ROOT / "backups"))).expanduser()
LOG_RETENTION_DAYS = max(1, int(_env_float("LOG_RETENTION_DAYS", 30, minimum=1)))
ACCESS_LOG_RETENTION_DAYS = max(1, int(_env_float("ACCESS_LOG_RETENTION_DAYS", 7, minimum=1)))
AUDIT_LOG_RETENTION_DAYS = max(1, int(_env_float("AUDIT_LOG_RETENTION_DAYS", 90, minimum=1)))
# External health-check latency thresholds.  SSE total duration is deliberately
# not used by the watchdog; it measures the client-held stream lifetime.
HEALTH_SLOW_REQUEST_MS = _env_float("HEALTH_SLOW_REQUEST_MS", 60_000.0, minimum=0.0)
HEALTH_SLOW_LLM_MS = _env_float("HEALTH_SLOW_LLM_MS", 30_000.0, minimum=0.0)
HEALTH_SLOW_MIN_COUNT = max(1, int(_env_float("HEALTH_SLOW_MIN_COUNT", 3, minimum=1)))

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
