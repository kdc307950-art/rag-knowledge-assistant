# syntax=docker/dockerfile:1
# 三阶段构建：前端构建 → Python 后端 → Nginx 前端服务
#
# 单 worker 硬约束：Chroma 写锁、上传闸门、BM25、会话存储均为进程内对象。
# 绝不在此文件或 compose 里设置 --workers > 1。

# ─── Stage 1: 前端构建 ───────────────────────────────────────────────────────
FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ─── Stage 2: Python 后端 ────────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS backend
WORKDIR /app

# 先复制依赖声明文件，充分利用层缓存；代码变更不会导致依赖重装
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 复制业务代码和配置（模型/数据不入镜像）
COPY backend/ ./backend/
COPY enterprise_rag/ ./enterprise_rag/
COPY config/ ./config/

# 数据目录占位；运行时由 compose 卷覆盖，不在镜像内存储任何状态
RUN mkdir -p /app/data/logs /app/backups

ENV RAG_DATA_DIR=/app/data \
    RAG_LOG_DIR=/app/data/logs \
    RAG_BACKUP_DIR=/app/backups \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/model_cache

EXPOSE 8000
# workers=1 写死，防止误扩容破坏进程内单例
CMD ["uv", "run", "uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# ─── Stage 3: Nginx 前端服务 ─────────────────────────────────────────────────
FROM nginx:1.27-alpine AS nginx
COPY --from=frontend-build /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80 443
