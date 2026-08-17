"""APP_PASSWORD 鉴权：配置口令后所有 API 需 X-API-Key 头。"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from enterprise_rag.config import APP_PASSWORD


async def require_access(x_api_key: str | None = Header(default=None)) -> None:
    if not APP_PASSWORD:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, APP_PASSWORD):
        raise HTTPException(status_code=401, detail="访问口令错误")
