"""Aggregated, read-only system diagnostics."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from enterprise_rag.services.diagnostics_service import build_diagnostics


router = APIRouter()


@router.get("/diagnostics")
async def diagnostics():
    return await run_in_threadpool(build_diagnostics)
