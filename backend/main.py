"""FastAPI 应用入口：CORS + 鉴权 + 路由组装。

部署红线：单进程单 worker 启动（上传闸门、Chroma 写锁、BM25 均为单进程对象）。
    uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from enterprise_rag.config import (
    LOG_BACKUP_COUNT,
    LOG_DIR,
    LOG_LEVEL,
    LOG_MAX_BYTES,
)
from enterprise_rag import config as rag_config
from enterprise_rag.services.diagnostics_service import build_diagnostics
from enterprise_rag.utils.logger import setup_logger

from .api import auth as auth_api, chat, diagnostics, feedback, kb, metrics, probes, upload
from .auth import require_access
from .observability.middleware import ObservabilityMiddleware

logger = logging.getLogger(__name__)


async def _log_startup_diagnostics() -> None:
    try:
        report = await run_in_threadpool(build_diagnostics)
        checks = report["checks"]
        logger.info(
            "[自检] vector=%s manifest=%s embedding=%s reranker=%s llm=%s",
            checks.get("vector_store", {}).get("status", "unknown"),
            checks.get("manifest", {}).get("status", "unknown"),
            checks.get("embedding", {}).get("state", "unknown"),
            checks.get("reranker", {}).get("state", "unknown"),
            checks.get("llm", {}).get("state", "unknown"),
        )
    except Exception:
        logger.exception("启动自检失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uvicorn's default logger only writes to its own handlers. Install the
    # project JSON handlers before startup diagnostics and request handling so
    # access/error/audit extensions can rely on a configured package logger.
    setup_logger(
        log_dir=LOG_DIR,
        level=LOG_LEVEL,
        max_bytes=LOG_MAX_BYTES,
        backup_count=LOG_BACKUP_COUNT,
    )
    if rag_config.RAG_ENVIRONMENT == "production":
        auth = rag_config.auth_readiness()
        if not auth.get("configured"):
            raise RuntimeError(
                f"生产环境安全门禁未通过: {auth.get('code', 'unknown')}"
            )
    task = asyncio.create_task(_log_startup_diagnostics())
    app.state.startup_diagnostics_task = task
    try:
        yield
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="企业知识库助手 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
    expose_headers=["X-Session-Id", "X-Request-Id", "X-Message-Id"],
)
app.add_middleware(ObservabilityMiddleware)

api_dependencies = [Depends(require_access)]
# Probes and metrics deliberately sit outside the application API dependency:
# /api/live and /api/ready are consumed by an external watchdog, while
# /metrics has its own METRICS_TOKEN/loopback policy.
app.include_router(probes.router, prefix="/api")
app.include_router(metrics.router)
app.include_router(auth_api.router, prefix="/api")
app.include_router(chat.router, prefix="/api", dependencies=api_dependencies)
app.include_router(feedback.router, prefix="/api", dependencies=api_dependencies)
app.include_router(upload.router, prefix="/api", dependencies=api_dependencies)
app.include_router(kb.router, prefix="/api", dependencies=api_dependencies)
app.include_router(diagnostics.router, prefix="/api", dependencies=api_dependencies)
