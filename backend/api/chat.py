"""问答 SSE 路由：复用 RagService 公开入口，统一 JSON 编码，支持断连停止。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import threading
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from enterprise_rag.agent.memory import build_history
from enterprise_rag.llm.client import needs_history_rewrite
from enterprise_rag.services.chat_service import ChatService
from enterprise_rag.services.rag_service import RagService, _general_failure_details

from ..schemas import ChatRequest, DraftRequest, GeneralRequest
from ..session import SessionOwnershipError, append_message, get_messages, get_or_create_session_id
from ..sse import sse
from ..observability.context import get_request_telemetry, mark_sse_terminal
from enterprise_rag.services.quality_service import DuplicateRunError, get_quality_service

logger = logging.getLogger(__name__)
router = APIRouter()

_GREETING_REPLY = (
    "您好！我是企业知识库助手。您可以直接提问公司制度、流程或业务相关问题。"
)

# 只向客户端透传 UI 需要的元数据键，避免泄露内部字段。
_META_KEYS = (
    "sources",
    "source_refs",
    "citation_validation",
    "thought",
    "is_reject",
    "is_kb_busy",
    "fallback_allowed",
    "draft_allowed",
    "from_cache",
    "is_interrupted",
    "is_empty",
    "action_failed",
    "error_code",
    "is_kb_stale",
    "query",
    "retrieval_query",
)

_DISCONNECT_POLL_SECONDS = 0.2
_ERROR_MESSAGES = {
    "authentication": "模型服务鉴权失败，请检查 API Key 配置后重试。",
    "rate_limit": "模型服务当前请求过多或额度不足，请稍后重试。",
    "model_not_found": "模型配置不可用，请检查模型名称后重试。",
    "empty_response": "模型未返回有效内容，请重新提问。",
    "retrieval_error": "知识库检索服务暂时不可用，请稍后重试。",
    "document_governance_unresolved": "文档生效关系尚未确认，普通问答暂不可用。",
    "generation_error": "回答生成未正常完成，请稍后重试。",
}


@dataclass
class _StreamOutcome:
    terminal: str | None = None


def _should_persist_history(outcome: _StreamOutcome, content: str) -> bool:
    return outcome.terminal == "done" and bool(content)


def _select_history(query: str, messages: list[dict]) -> str:
    """与 ChatService 相同的历史选择语义：仅指代型追问携带历史。"""
    recent = build_history(messages, max_entries=3, max_length_per_msg=300) or ""
    return recent if needs_history_rewrite(recent, query) else ""


def _meta_payload(rag: RagService) -> dict:
    meta = dict(getattr(rag, "_last_meta", {}) or {})
    return {key: meta.get(key) for key in _META_KEYS}


def _terminal_event(payload: dict) -> tuple[str, dict]:
    """将业务层失败元数据转换为唯一的 SSE 失败终态。"""
    failed = bool(
        payload.get("action_failed")
        or payload.get("is_interrupted")
        or payload.get("is_empty")
    )
    if not failed:
        return "done", payload
    code = str(payload.get("error_code") or "generation_error")
    return "error", {
        "code": code,
        "message": _ERROR_MESSAGES.get(code, _ERROR_MESSAGES["generation_error"]),
        "partial": bool(payload.get("is_interrupted")),
    }


def _make_producer(iterator_factory, on_done, on_error=None, on_interrupted=None):
    """构建线程内同步迭代生成器的生产者。

    设计要点：
    - 生成迭代在线程池中执行，避免阻塞事件循环；
    - 客户端断开时由 stop_event 通知生产者退出；
    - 生成器在生产者线程内 close（同线程 next 后 close 是安全的），
      触发 rag_service 的中断保留逻辑；
    - 所有数据统一走 JSON 编码（sse 函数负责）。
    """

    def _produce(emit, stop_event: threading.Event) -> None:
        gen = None
        try:
            gen = iterator_factory()
            for chunk in gen:
                if stop_event.is_set():
                    mark_sse_terminal("interrupted")
                    if on_interrupted is not None:
                        on_interrupted()
                    break
                emit("token", chunk)
            if not stop_event.is_set():
                kind, payload = _terminal_event(on_done())
                if kind == "done":
                    mark_sse_terminal("ok")
                else:
                    mark_sse_terminal("error", payload.get("code"))
                emit(kind, payload)
        except Exception as exc:
            if not stop_event.is_set():
                logger.error("SSE 流式生成失败: %s", exc, exc_info=True)
                message, code = _general_failure_details(exc)
                if on_error is not None:
                    try:
                        on_error(code)
                    except Exception:
                        logger.exception("记录 SSE 失败 run 时发生异常")
                mark_sse_terminal("error", code)
                emit("error", {"code": code, "message": message, "partial": False})
            else:
                if on_interrupted is not None:
                    try:
                        on_interrupted()
                    except Exception:
                        logger.exception("记录 SSE 中断 run 时发生异常")
                mark_sse_terminal("interrupted")
        finally:
            if gen is not None:
                # 生成器才有 close（触发中断保留逻辑）；list_iterator 等普通迭代器没有。
                close = getattr(gen, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        logger.debug("关闭 SSE 业务生成器失败", exc_info=True)
            emit("__end__", None)

    return _produce


async def _run_sse(
    producer,
    collected: list[str] | None,
    request: Request,
    outcome: _StreamOutcome | None = None,
):
    """消费生产者事件并转为 SSE；客户端断开时尽快通知生产者停止。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()
    accept_events = threading.Event()
    accept_events.set()
    outcome = outcome or _StreamOutcome()

    def emit(kind: str, payload) -> None:
        if not accept_events.is_set():
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))
        except RuntimeError:
            # 事件循环已关闭时丢弃迟到事件；工作线程会自行结束。
            return

    task = loop.create_task(run_in_threadpool(producer, emit, stop_event))

    def consume_task_result(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("SSE 生产者线程异常退出", exc_info=True)

    task.add_done_callback(consume_task_result)
    try:
        while True:
            if await request.is_disconnected():
                outcome.terminal = "disconnected"
                mark_sse_terminal("interrupted")
                stop_event.set()
                break
            try:
                kind, payload = await asyncio.wait_for(
                    queue.get(), timeout=_DISCONNECT_POLL_SECONDS
                )
            except asyncio.TimeoutError:
                continue
            if kind == "__end__":
                outcome.terminal = outcome.terminal or "aborted"
                if outcome.terminal != "done":
                    mark_sse_terminal("interrupted")
                break
            if kind == "error":
                outcome.terminal = "error"
                mark_sse_terminal("error", payload.get("code"))
                yield sse("error", payload)
                break
            if kind == "token":
                if collected is not None:
                    collected.append(payload)
                yield sse("token", payload)
            elif kind == "done":
                outcome.terminal = "done"
                mark_sse_terminal("ok")
                yield sse("done", payload)
                break
            else:
                yield sse(kind, payload)
    except asyncio.CancelledError:
        outcome.terminal = "disconnected"
        mark_sse_terminal("interrupted")
        raise
    finally:
        stop_event.set()
        accept_events.clear()


@router.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    x_session_id: str | None = Header(default=None),
    x_message_id: str | None = Header(default=None),
) -> StreamingResponse:
    """严格知识库问答：SSE 输出 token… → done / error；断开即停止生成。"""
    if x_message_id:
        try:
            message_id = str(UUID(x_message_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="X-Message-Id 必须是 UUID") from exc
    else:
        message_id = str(uuid4())
    rag = RagService()
    try:
        session_id = get_or_create_session_id(
            x_session_id or req.session_id,
            principal_id=str((getattr(request.state, "current_user", None) or {}).get("id") or "local"),
        )
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    messages = get_messages(session_id)
    history = _select_history(req.query, messages)
    collected: list[str] = []
    generated: list[str] = []
    outcome = _StreamOutcome()
    current_user = getattr(request.state, "current_user", None) or {}
    try:
        quality = get_quality_service()
    except ValueError:
        logger.exception("质量反馈配置无效，继续提供问答但禁用反馈")
        quality = None
    telemetry = get_request_telemetry()
    run_id = telemetry.request_id if telemetry is not None else uuid4().hex
    principal_id = str(current_user.get("id") or "local")
    if quality is not None:
        try:
            quality.reserve_run(
                run_id=run_id,
                message_id=message_id,
                principal_id=principal_id,
                session_id=session_id,
                action="chat",
                query=req.query,
            )
        except DuplicateRunError as exc:
            raise HTTPException(status_code=409, detail="X-Message-Id 已被使用") from exc
        except Exception:
            logger.exception("初始化质量 run 失败，继续提供问答但禁用反馈")
            quality = None

    is_greeting = ChatService._is_greeting(req.query)

    def tracked_iterator(source):
        for chunk in source:
            generated.append(str(chunk))
            yield chunk

    def iterator_factory():
        source = (
            iter([_GREETING_REPLY])
            if is_greeting
            else rag.answer_stream(
                req.query,
                history,
                messages,
                access_context=current_user,
            )
        )
        return tracked_iterator(source)

    def finalize_quality(
        terminal: str,
        *,
        error_code: str | None = None,
        feedback_eligible: bool = False,
        sources: list[str] | None = None,
    ) -> bool:
        """Commit a reserved run before exposing its terminal client state."""
        if quality is None:
            return False
        try:
            return quality.finalize_run(
                run_id=run_id,
                terminal=terminal,
                answer="".join(generated),
                sources=sources,
                error_code=error_code,
                feedback_eligible=feedback_eligible,
            )
        except Exception:
            logger.exception("完成质量 run 落库失败")
            return False

    def on_done():
        metadata = (
            {"sources": [], "is_kb": False}
            if is_greeting
            else {**_meta_payload(rag), "is_kb": True}
        )
        terminal, payload = _terminal_event(metadata)
        sources = metadata.get("sources")
        valid_sources = sources if isinstance(sources, list) else []
        eligible = terminal == "done" and bool(generated) and not is_greeting
        persisted = finalize_quality(
            terminal,
            error_code=payload.get("code") if terminal == "error" else None,
            feedback_eligible=eligible,
            sources=valid_sources,
        )
        payload["message_id"] = message_id
        payload["feedback_eligible"] = bool(eligible and persisted)
        return payload

    def on_error(code: str) -> None:
        finalize_quality("error", error_code=code)

    def on_interrupted() -> None:
        finalize_quality("interrupted", error_code="interrupted")

    producer = _make_producer(
        iterator_factory,
        on_done,
        on_error=on_error,
        on_interrupted=on_interrupted,
    )

    async def event_stream():
        async for chunk in _run_sse(producer, collected, request, outcome):
            yield chunk

    async def wrapped():
        async for chunk in event_stream():
            yield chunk
        full = "".join(collected)
        if _should_persist_history(outcome, full):
            append_message(session_id, "user", req.query)
            append_message(session_id, "assistant", full)

    return StreamingResponse(
        wrapped(),
        media_type="text/event-stream",
        headers={
            "X-Session-Id": session_id,
            "X-Message-Id": message_id,
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
        lambda: rag.draft_stream(
            req.query, req.retrieval_query,
            access_context=getattr(request.state, "current_user", None),
        ),
        lambda: {**_meta_payload(rag), "is_kb": True, "is_draft": True},
    )
    return StreamingResponse(
        _run_sse(producer, None, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
