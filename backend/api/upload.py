"""上传与任务状态路由：复用 DocumentService 公开接口。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from enterprise_rag.services.document_service import DocumentService
from enterprise_rag.utils.logger import log_audit_event
from backend.observability.metrics import mark_kb_busy
from enterprise_rag.storage.acl import ACLMetadataError, normalize_acl_metadata, user_can_manage_documents

logger = logging.getLogger(__name__)
router = APIRouter()
_TASK_HISTORY_LIMIT = 20


class _UploadFileAdapter:
    """直接读取 UploadFile 的临时文件，避免把整批上传复制到内存。"""

    def __init__(self, upload: UploadFile):
        self.name = upload.filename or "unnamed"
        self.size = int(upload.size or 0)
        self._file = upload.file

    def read(self, *args, **kwargs):
        return self._file.read(*args, **kwargs)


@router.post("/upload")
async def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    classification: str | None = Form(default=None),
    department: str | None = Form(default=None),
    visibility: str | None = Form(default=None),
):
    """暂存并提交后台入库任务；返回 task_id 供轮询。"""
    adapted: list[_UploadFileAdapter] = []
    for file in files:
        if file.size == 0:
            continue
        await file.seek(0)
        adapted.append(_UploadFileAdapter(file))
    if not adapted:
        raise HTTPException(status_code=400, detail="未收到有效文件内容")
    user = getattr(request.state, "current_user", None)
    if not user_can_manage_documents(user):
        raise HTTPException(status_code=403, detail="当前账号没有上传文档权限")
    try:
        metadata = normalize_acl_metadata(
            {
                "classification": classification,
                "department": department,
                "visibility": visibility,
            },
            owner_id=str((user or {}).get("id") or ""),
        )
    except ACLMetadataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = await run_in_threadpool(DocumentService().process_uploads, adapted, metadata)
    if not result.get("success"):
        if result.get("busy"):
            mark_kb_busy("uploading")
        log_audit_event(
            "upload_rejected",
            file_count=len(adapted),
            total_bytes=sum(int(file.size or 0) for file in adapted),
            reason="busy" if result.get("busy") else "validation",
        )
        raise HTTPException(
            status_code=429 if result.get("busy") else 400,
            detail=result.get("error", "上传失败"),
        )
    task_ids = result.get("async_tasks") or []
    log_audit_event(
        "upload_queued",
        file_count=len(adapted),
        total_bytes=sum(int(file.size or 0) for file in adapted),
        task_count=len(task_ids),
    )
    return {"success": True, "task_id": task_ids[0] if task_ids else None, **result}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """查询单个后台入库任务快照。"""
    service = DocumentService()
    # React 轮询是当前唯一常规入口；在读取快照时顺便
    # 淘汰旧终态，避免进程级任务字典无限增长。
    service.clear_finished_tasks(
        keep_last=_TASK_HISTORY_LIMIT,
        preserve_task_id=task_id,
    )
    tasks = service.get_upload_tasks()
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return dict(task)
