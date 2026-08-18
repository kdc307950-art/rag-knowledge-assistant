"""知识库管理路由：文档列表/删除/清空/统计/健康。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
import hashlib

from enterprise_rag.storage import vector_store
from enterprise_rag.storage.acl import user_can_manage_documents
from enterprise_rag.utils.logger import log_audit_event
from backend.observability.metrics import mark_kb_busy

router = APIRouter()


@router.get("/kb/documents")
async def list_documents(request: Request):
    return {"documents": vector_store.list_documents(getattr(request.state, "current_user", None))}


@router.delete("/kb/documents/{source:path}")
async def delete_document(source: str, request: Request):
    if not user_can_manage_documents(getattr(request.state, "current_user", None)):
        raise HTTPException(status_code=403, detail="当前账号没有管理文档权限")
    if not vector_store.delete_document(source):
        if getattr(vector_store, "is_knowledge_base_busy", lambda: False)():
            mark_kb_busy("mutating")
        raise HTTPException(status_code=404, detail="文档不存在或知识库正在更新")
    log_audit_event(
        "document_deleted",
        source_digest=hashlib.sha256(source.encode("utf-8")).hexdigest()[:12],
    )
    return {"ok": True}


@router.delete("/kb/clear")
async def clear_documents(request: Request):
    if not user_can_manage_documents(getattr(request.state, "current_user", None)):
        raise HTTPException(status_code=403, detail="当前账号没有管理文档权限")
    if not vector_store.clear_all_documents():
        if getattr(vector_store, "is_knowledge_base_busy", lambda: False)():
            mark_kb_busy("mutating")
        raise HTTPException(status_code=409, detail="知识库正在更新，请稍后重试")
    log_audit_event("knowledge_base_cleared")
    return {"ok": True}


@router.get("/kb/stats")
async def kb_stats(request: Request):
    access_context = getattr(request.state, "current_user", None)
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
        "doc_count": vector_store.get_document_count(access_context),
        "chunk_count": vector_store.get_doc_count(access_context),
        "generation": generation,
    }


@router.get("/health")
async def health():
    healthy, status = vector_store.get_vector_store_health()
    return {"ok": healthy, "vector_status": status}
