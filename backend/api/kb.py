"""知识库管理路由：文档列表/删除/清空/统计/健康。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
import hashlib

from enterprise_rag.storage import vector_store
from enterprise_rag.utils.logger import log_audit_event

router = APIRouter()


@router.get("/kb/documents")
async def list_documents():
    return {"documents": vector_store.list_documents()}


@router.delete("/kb/documents/{source:path}")
async def delete_document(source: str):
    if not vector_store.delete_document(source):
        raise HTTPException(status_code=404, detail="文档不存在或知识库正在更新")
    log_audit_event(
        "document_deleted",
        source_digest=hashlib.sha256(source.encode("utf-8")).hexdigest()[:12],
    )
    return {"ok": True}


@router.delete("/kb/clear")
async def clear_documents():
    if not vector_store.clear_all_documents():
        raise HTTPException(status_code=409, detail="知识库正在更新，请稍后重试")
    log_audit_event("knowledge_base_cleared")
    return {"ok": True}


@router.get("/kb/stats")
async def kb_stats():
    healthy, status = vector_store.get_vector_store_health()
    if not healthy:
        return {
            "healthy": False,
            "vector_status": status,
            "doc_count": 0,
            "chunk_count": 0,
            "generation": None,
        }
    generation = None
    try:
        from enterprise_rag.storage.kb_manifest import get_manifest

        generation = get_manifest().snapshot().generation
    except Exception:
        generation = None
    return {
        "healthy": True,
        "vector_status": status,
        "doc_count": vector_store.get_document_count(),
        "chunk_count": vector_store.get_doc_count(),
        "generation": generation,
    }


@router.get("/health")
async def health():
    healthy, status = vector_store.get_vector_store_health()
    return {"ok": healthy, "vector_status": status}
