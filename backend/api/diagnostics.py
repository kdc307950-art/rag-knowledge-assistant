"""Aggregated, read-only system diagnostics."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from enterprise_rag.services.diagnostics_service import build_diagnostics
from enterprise_rag.storage.acl import user_is_admin


router = APIRouter()


@router.get("/diagnostics")
async def diagnostics(request: Request):
    if not user_is_admin(getattr(request.state, "current_user", None)):
        raise HTTPException(status_code=403, detail="仅 admin 可访问全局诊断")
    return await run_in_threadpool(build_diagnostics)
