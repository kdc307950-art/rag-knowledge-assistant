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

from enterprise_rag.services.diagnostics_service import build_diagnostics

from .api import chat, diagnostics, kb, upload
from .auth import require_access

logger = logging.getLogger(__name__)


async def _log_startup_diagnostics() -> None:
    try:
        report = await run_in_threadpool(build_diagnostics)
        checks = report["checks"]
        logger.info(
            "[自检] vector=%s manifest=%s embedding=%s reranker=%s llm=%s",
            checks["vector_store"]["status"],
            checks["manifest"]["status"],
            checks["embedding"]["state"],
            checks["reranker"]["state"],
            checks["llm"]["state"],
        )
    except Exception:
        logger.exception("启动自检失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    expose_headers=["X-Session-Id"],
)

api_dependencies = [Depends(require_access)]
app.include_router(chat.router, prefix="/api", dependencies=api_dependencies)
app.include_router(upload.router, prefix="/api", dependencies=api_dependencies)
app.include_router(kb.router, prefix="/api", dependencies=api_dependencies)
app.include_router(diagnostics.router, prefix="/api", dependencies=api_dependencies)
