"""Cheap, read-only diagnostics for local operation and UI status."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from ..config import API_KEY, LLM_MODEL, LOG_DIR, MODEL_NAME, RERANKER_MODEL
from ..rag.reranker import get_reranker_readiness
from ..storage import vector_store
from ..storage.embedding import get_model_readiness, get_model_status
from ..storage.kb_manifest import get_manifest
from ..utils.logger import _redact


_ERROR_TAIL_BYTES = 512 * 1024
_ERROR_MESSAGE_LIMIT = 300


def _state(ready: bool, detail: str) -> str:
    normalized = (detail or "").strip()
    if not ready:
        return "error"
    if normalized.startswith("已加载") or normalized == "就绪":
        return "loaded"
    return "available"


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def recent_error_summary(
    log_dir: Path = LOG_DIR,
    *,
    now: datetime | None = None,
    hours: int = 24,
    limit: int = 3,
) -> dict[str, Any]:
    path = Path(log_dir) / "error.log"
    if not path.is_file():
        return {"count": 0, "recent": [], "truncated": False}

    size = path.stat().st_size
    offset = max(0, size - _ERROR_TAIL_BYTES)
    with path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read()
    if offset:
        newline = raw.find(b"\n")
        raw = raw[newline + 1 :] if newline >= 0 else b""

    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(
        hours=max(1, hours)
    )
    matched: list[dict[str, str]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        timestamp = _parse_timestamp(payload.get("timestamp"))
        if timestamp is None or timestamp < cutoff:
            continue
        message = _redact(str(payload.get("message", ""))).strip()
        matched.append(
            {
                "timestamp": timestamp.isoformat(),
                "level": str(payload.get("level", "ERROR")),
                "logger": str(payload.get("logger", ""))[:120],
                "message": message[:_ERROR_MESSAGE_LIMIT],
            }
        )

    return {
        "count": len(matched),
        "recent": list(reversed(matched[-max(0, limit) :])),
        "truncated": offset > 0,
    }


def build_diagnostics() -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    embedding_ready, embedding_detail = get_model_readiness()
    reranker_ready, reranker_detail = get_reranker_readiness()

    vector_healthy = False
    vector_status = "连接异常"
    doc_count = 0
    chunk_count = 0
    generation: int | None = None
    try:
        vector_healthy, vector_status = vector_store.get_vector_store_health()
        if vector_healthy:
            doc_count = vector_store.get_document_count()
            chunk_count = vector_store.get_doc_count()
    except Exception:
        vector_healthy = False
        vector_status = "连接异常"

    manifest_check: dict[str, Any]
    try:
        snapshot = get_manifest().snapshot()
        generation = snapshot.generation
        declared_chunks = sum(snapshot.chunk_counts.values())
        manifest_ok = bool(
            vector_healthy
            and snapshot.initialized
            and len(snapshot.active_revisions) == doc_count
            and declared_chunks == chunk_count
        )
        manifest_check = {
            "ok": manifest_ok,
            "status": "一致" if manifest_ok else "不一致",
            "generation": generation,
            "initialized": snapshot.initialized,
            "source_count": len(snapshot.active_revisions),
            "declared_chunk_count": declared_chunks,
            "visible_chunk_count": chunk_count,
        }
    except Exception as exc:
        manifest_check = {
            "ok": False,
            "status": "检查失败",
            "generation": generation,
            "initialized": False,
            "source_count": 0,
            "declared_chunk_count": 0,
            "visible_chunk_count": 0,
            "detail": _redact(str(exc))[:200],
        }

    llm_configured = bool(API_KEY and LLM_MODEL)
    checks = {
        "vector_store": {"ok": vector_healthy, "status": vector_status},
        "manifest": manifest_check,
        "embedding": {
            "ok": embedding_ready,
            "state": _state(embedding_ready, get_model_status()),
            "detail": embedding_detail,
            "model": MODEL_NAME,
        },
        "reranker": {
            "ok": reranker_ready,
            "state": _state(reranker_ready, reranker_detail),
            "detail": reranker_detail,
            "model": RERANKER_MODEL,
        },
        "llm": {
            "ok": llm_configured,
            "state": "configured" if llm_configured else "missing",
            "model": LLM_MODEL,
            "network_verified": False,
        },
    }
    return {
        "ok": all(bool(check.get("ok")) for check in checks.values()),
        "checked_at": checked_at,
        "checks": checks,
        "kb": {
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "generation": generation,
        },
        "errors": recent_error_summary(),
    }
