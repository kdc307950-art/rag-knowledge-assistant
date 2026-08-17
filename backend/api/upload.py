"""上传与任务状态路由：复用 DocumentService 公开接口。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from enterprise_rag.services.document_service import DocumentService

logger = logging.getLogger(__name__)
router = APIRouter()


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
        raise HTTPException(
            status_code=429 if result.get("busy") else 400,
            detail=result.get("error", "上传失败"),
        )
    task_ids = result.get("async_tasks") or []
    return {"success": True, "task_id": task_ids[0] if task_ids else None, **result}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """查询单个后台入库任务快照。"""
    tasks = DocumentService().get_upload_tasks()
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return dict(task)
