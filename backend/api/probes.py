"""Unauthenticated liveness/readiness probes for local process supervision."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from enterprise_rag.storage import vector_store
from enterprise_rag.storage.kb_manifest import get_manifest
from enterprise_rag.storage.embedding import get_model_readiness
from enterprise_rag.rag.reranker import get_reranker_readiness

from ..observability.metrics import set_kb_snapshot

router = APIRouter()


def _readiness() -> dict[str, object]:
    healthy, status = vector_store.get_vector_store_health()
    if not healthy:
        set_kb_snapshot(generation=None, documents=0, chunks=0, healthy=False)
        return {"ready": False, "status": status}
    try:
        embedding_ready, embedding_detail = get_model_readiness()
        reranker_ready, reranker_detail = get_reranker_readiness()
        if not embedding_ready or not reranker_ready:
            return {
                "ready": False,
                "status": "model_unavailable",
                "embedding": {"ready": embedding_ready, "detail": embedding_detail},
                "reranker": {"ready": reranker_ready, "detail": reranker_detail},
            }
        snapshot = get_manifest().snapshot()
        document_count = vector_store.get_document_count()
        chunk_count = vector_store.get_doc_count()
        set_kb_snapshot(
            generation=getattr(snapshot, "generation", None),
            documents=document_count,
            chunks=chunk_count,
            healthy=True,
        )
        if status == "更新中":
            return {"ready": True, "status": "updating"}
        # A brand-new empty store is valid. Once initialized, counts must agree;
        # an update window is deliberately reported as ready/degraded.
        consistent = (
            not snapshot.initialized and document_count == 0 and chunk_count == 0
        ) or (
            snapshot.initialized
            and len(snapshot.active_revisions) == document_count
            and sum(snapshot.chunk_counts.values()) == chunk_count
        )
        if not consistent:
            return {"ready": False, "status": "manifest_mismatch"}
        return {
            "ready": True,
            "status": "updating" if status == "更新中" else "ready",
            "embedding": {"ready": True, "detail": embedding_detail},
            "reranker": {"ready": True, "detail": reranker_detail},
        }
    except Exception:
        set_kb_snapshot(generation=None, documents=0, chunks=0, healthy=False)
        return {"ready": False, "status": "dependency_error"}


@router.get("/live")
async def live() -> dict[str, bool]:
    return {"ok": True}


@router.get("/ready")
async def ready():
    result = await run_in_threadpool(_readiness)
    return JSONResponse(result, status_code=200 if result["ready"] else 503)
