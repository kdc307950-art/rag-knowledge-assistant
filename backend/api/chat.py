"""问答 SSE 路由：复用 RagService 公开入口，统一 JSON 编码，支持断连停止。"""

from __future__ import annotations

import asyncio
import logging
import threading

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from enterprise_rag.agent.memory import build_history
from enterprise_rag.llm.client import needs_history_rewrite
from enterprise_rag.services.chat_service import ChatService
from enterprise_rag.services.rag_service import RagService, _general_failure_details

from ..schemas import ChatRequest, DraftRequest, GeneralRequest
from ..session import append_message, get_messages, get_or_create_session_id
from ..sse import sse

logger = logging.getLogger(__name__)
router = APIRouter()

_GREETING_REPLY = (
    "您好！我是企业知识库助手。您可以直接提问公司制度、流程或业务相关问题。"
)

# 只向客户端透传 UI 需要的元数据键，避免泄露内部字段。
_META_KEYS = (
    "sources",
    "thought",
    "is_reject",
    "is_kb_busy",
    "fallback_allowed",
    "draft_allowed",
    "from_cache",
)


def _select_history(query: str, messages: list[dict]) -> str:
    """与 ChatService 相同的历史选择语义：仅指代型追问携带历史。"""
    recent = build_history(messages, max_entries=3, max_length_per_msg=300) or ""
    return recent if needs_history_rewrite(recent, query) else ""


def _meta_payload(rag: RagService) -> dict:
    meta = dict(getattr(rag, "_last_meta", {}) or {})
    return {key: meta.get(key) for key in _META_KEYS}


def _make_producer(iterator_factory, on_done):
    """构建线程内同步迭代生成器的生产者。

    设计要点：
    - 生成迭代在线程池中执行，避免阻塞事件循环；
    - 客户端断开时由 stop_event 通知生产者退出；
    - 生成器在生产者线程内 close（同线程 next 后 close 是安全的），
      触发 rag_service 的中断保留逻辑；
    - 所有数据统一走 JSON 编码（sse 函数负责）。
    """

    def _produce(queue: asyncio.Queue, stop_event: threading.Event) -> None:
        gen = None
        try:
            gen = iterator_factory()
            for chunk in gen:
                if stop_event.is_set():
                    break
                queue.put_nowait(("token", chunk))
            if not stop_event.is_set():
                queue.put_nowait(("done", on_done()))
        except Exception as exc:
            logger.error("SSE 流式生成失败: %s", exc, exc_info=True)
            message, code = _general_failure_details(exc)
            queue.put_nowait(("error", {"code": code, "message": message}))
        finally:
            if gen is not None:
                # 生成器才有 close（触发中断保留逻辑）；list_iterator 等普通迭代器没有。
                close = getattr(gen, "close", None)
                if close is not None:
                    close()
            queue.put_nowait(("__end__", None))

    return _produce


async def _run_sse(producer, collected: list[str] | None, request: Request):
    """消费生产者事件并转为 SSE；客户端断开时通知生产者停止。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()
    task = loop.create_task(run_in_threadpool(producer, queue, stop_event))
    try:
        while True:
            if await request.is_disconnected():
                stop_event.set()
                break
            kind, payload = await queue.get()
            if kind == "__end__":
                break
            if kind == "error":
                yield sse("error", payload)
                break
            if kind == "token":
                if collected is not None:
                    collected.append(payload)
                yield sse("token", payload)
            elif kind == "done":
                yield sse("done", payload)
                break
            else:
                yield sse(kind, payload)
    finally:
        stop_event.set()
        if not task.done():
            task.cancel()


@router.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    x_session_id: str | None = Header(default=None),
) -> StreamingResponse:
    """严格知识库问答：SSE 输出 token… → done / error；断开即停止生成。"""
    rag = RagService()
    session_id = get_or_create_session_id(x_session_id or req.session_id)
    messages = get_messages(session_id)
    history = _select_history(req.query, messages)
    collected: list[str] = []

    def iterator_factory():
        if ChatService._is_greeting(req.query):
            return iter([_GREETING_REPLY])
        return rag.answer_stream(req.query, history, messages)

    def on_done():
        if ChatService._is_greeting(req.query):
            return {"sources": [], "is_kb": False}
        return {**_meta_payload(rag), "is_kb": True}

    producer = _make_producer(iterator_factory, on_done)

    async def event_stream():
        async for chunk in _run_sse(producer, collected, request):
            yield chunk

    async def wrapped():
        async for chunk in event_stream():
            yield chunk
        full = "".join(collected)
        if full:
            append_message(session_id, "user", req.query)
            append_message(session_id, "assistant", full)

    return StreamingResponse(
        wrapped(),
        media_type="text/event-stream",
        headers={
            "X-Session-Id": session_id,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/general")
async def chat_general(req: GeneralRequest, request: Request) -> StreamingResponse:
    """用户显式触发的通用办公回答（G2）。"""
    rag = RagService()

    producer = _make_producer(
        lambda: rag.general_stream(req.query),
        lambda: {**_meta_payload(rag), "is_kb": False, "is_general": True},
    )
    return StreamingResponse(
        _run_sse(producer, None, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/draft")
async def chat_draft(req: DraftRequest, request: Request) -> StreamingResponse:
    """基于当前有效资料的起草。"""
    rag = RagService()

    producer = _make_producer(
        lambda: rag.draft_stream(req.query, req.retrieval_query),
        lambda: {**_meta_payload(rag), "is_kb": True, "is_draft": True},
    )
    return StreamingResponse(
        _run_sse(producer, None, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
