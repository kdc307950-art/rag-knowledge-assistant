"""Protected Prometheus-compatible metrics endpoint."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.responses import PlainTextResponse

from enterprise_rag.config import METRICS_TOKEN

from ..observability.metrics import render_metrics

router = APIRouter()


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "").strip().lower()
    return host in {"127.0.0.1", "::1", "localhost"}


@router.get("/metrics")
async def metrics_endpoint(
    request: Request,
    x_metrics_token: str | None = Header(default=None),
):
    if METRICS_TOKEN:
        if not x_metrics_token or not hmac.compare_digest(x_metrics_token, METRICS_TOKEN):
            raise HTTPException(status_code=401, detail="metrics token required")
    elif not _is_loopback(request):
        raise HTTPException(status_code=403, detail="metrics endpoint is local-only")
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")
