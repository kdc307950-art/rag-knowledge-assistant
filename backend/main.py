"""FastAPI 应用入口：CORS + 鉴权 + 路由组装。

部署红线：单进程单 worker 启动（上传闸门、Chroma 写锁、BM25 均为单进程对象）。
    uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import chat, kb, upload
from .auth import require_access

app = FastAPI(title="企业知识库助手 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

api_dependencies = [Depends(require_access)]
app.include_router(chat.router, prefix="/api", dependencies=api_dependencies)
app.include_router(upload.router, prefix="/api", dependencies=api_dependencies)
app.include_router(kb.router, prefix="/api", dependencies=api_dependencies)
