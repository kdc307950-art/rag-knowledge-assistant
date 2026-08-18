"""上传与任务状态路由：复用 DocumentService 公开接口。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from enterprise_rag.services.document_service import DocumentService
from enterprise_rag.utils.logger import log_audit_event

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
async def upload(files: list[UploadFile] = File(...)):
    """暂存并提交后台入库任务；返回 task_id 供轮询。"""
    adapted: list[_UploadFileAdapter] = []
    for file in files:
        if file.size == 0:
            continue
        await file.seek(0)
        adapted.append(_UploadFileAdapter(file))
    if not adapted:
        raise HTTPException(status_code=400, detail="未收到有效文件内容")
    result = await run_in_threadpool(DocumentService().process_uploads, adapted)
    if not result.get("success"):
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
