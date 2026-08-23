"""上传与任务状态路由：复用 DocumentService 公开接口。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from enterprise_rag.services.document_service import DocumentService
from enterprise_rag.utils.logger import log_audit_event
from backend.observability.metrics import mark_kb_busy
from backend.observability.context import get_request_telemetry
from enterprise_rag.storage.acl import (
    ACLMetadataError,
    can_create_document,
    can_replace_document,
    normalize_acl_metadata,
)
from enterprise_rag.storage import vector_store
from enterprise_rag.storage.vector_store import SourceMetadataReadError

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
    if not user or not set(user.get("roles") or {}) & {"admin", "editor"}:
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
    if not can_create_document(user, metadata):
        raise HTTPException(status_code=403, detail="editor 只能创建本部门 private/department 文档")
    for file in adapted:
        try:
            existing = vector_store.get_source_metadata(file.name)
        except SourceMetadataReadError as exc:
            logger.warning("上传前无法读取既有文档元数据: %s", file.name)
            raise HTTPException(status_code=503, detail="知识库元数据暂不可用，请稍后重试") from exc
        # ``{}`` represents an existing legacy source without ACL metadata.
        # It is deliberately fail-closed, not a missing source an editor can
        # replace by using the same filename.
        if existing is not None and not can_replace_document(user, existing):
            raise HTTPException(status_code=403, detail=f"当前账号不能替换文档：{file.name}")
    task_context = {
        **metadata,
        "principal_id": str(user.get("id") or ""),
        "principal_roles": ",".join(sorted(str(role) for role in user.get("roles") or [])),
        "principal_department": str(user.get("department") or ""),
        "request_id": str(
            getattr(get_request_telemetry(), "request_id", "") or ""
        ),
    }
    result = await run_in_threadpool(DocumentService().process_uploads, adapted, task_context)
    if not result.get("success"):
        if result.get("busy"):
            mark_kb_busy("uploading")
        log_audit_event(
            "upload_rejected",
            file_count=len(adapted),
            total_bytes=sum(int(file.size or 0) for file in adapted),
            reason="busy" if result.get("busy") else "validation",
            actor_id=(user or {}).get("id"),
            actor_roles=(user or {}).get("roles", []),
            request_id=task_context["request_id"],
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
        actor_id=(user or {}).get("id"),
        actor_roles=(user or {}).get("roles", []),
        request_id=task_context["request_id"],
    )
    return {"success": True, "task_id": task_ids[0] if task_ids else None, **result}


@router.get("/tasks/{task_id}")
# 这里必须是裸 Request 而不是 Request | None：FastAPI 只对「注解恰好是 Request 类」
# 的形参做特殊处理，写成联合类型就会被当作普通请求体字段丢给 pydantic，
# 路由注册阶段直接抛 FastAPIError。默认值 None 是为了让测试能直接调用本函数，
# 函数体内已有 request is not None 分支，运行时安全。
async def get_task(task_id: str, request: Request = None):  # pyright: ignore[reportArgumentType]
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
    # Keep direct service-level callers/tests compatible; HTTP requests always
    # receive a Request from FastAPI and therefore use the authenticated user.
    user = getattr(request.state, "current_user", None) if request is not None else {
        "id": "local",
        "roles": ["admin"],
    }
    user = user or {}
    owner_id = str(task.get("principal_id") or "")
    if "admin" not in set(user.get("roles") or []) and owner_id != str(user.get("id") or ""):
        # Do not reveal whether another user's task exists.
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return dict(task)
