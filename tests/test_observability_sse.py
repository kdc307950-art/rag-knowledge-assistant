"""SSE business terminal events update shared observability telemetry."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import threading
from typing import Any

import pytest

from backend.api import chat
from backend.observability.middleware import (
    ObservabilityMiddleware,
    _phase_durations_ms,
    _sse_events,
    _with_request_id,
    route_label,
)
from backend.observability.metrics import render_metrics, reset_metrics


def test_producer_marks_successful_terminal(monkeypatch):
    terminals: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        chat,
        "mark_sse_terminal",
        lambda terminal, code=None: terminals.append((terminal, code)),
    )
    emitted: list[tuple[str, Any]] = []
    producer = chat._make_producer(lambda: iter(["A"]), lambda: {"sources": []})

    producer(lambda kind, payload: emitted.append((kind, payload)), threading.Event())

    assert emitted[0] == ("token", "A")
    assert emitted[1][0] == "done"
    assert ("ok", None) in terminals


def test_producer_marks_business_error_terminal(monkeypatch):
    terminals: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        chat,
        "mark_sse_terminal",
        lambda terminal, code=None: terminals.append((terminal, code)),
    )

    def failing_iterator():
        raise RuntimeError("upstream failed")
        yield "unreachable"

    emitted: list[tuple[str, Any]] = []
    producer = chat._make_producer(failing_iterator, lambda: {"sources": []})
    producer(lambda kind, payload: emitted.append((kind, payload)), threading.Event())

    assert emitted[0][0] == "error"
    assert emitted[0][1]["code"] == "generation_error"
    assert ("error", "generation_error") in terminals


def test_producer_marks_interruption_when_stop_requested(monkeypatch):
    terminals: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        chat,
        "mark_sse_terminal",
        lambda terminal, code=None: terminals.append((terminal, code)),
    )
    stop_event = threading.Event()
    stop_event.set()
    emitted: list[tuple[str, Any]] = []
    producer = chat._make_producer(lambda: iter(["late"]), lambda: {"sources": []})

    producer(lambda kind, payload: emitted.append((kind, payload)), stop_event)

    assert emitted == [("__end__", None)]
    assert ("interrupted", None) in terminals


def test_sse_parser_handles_frames_split_across_asgi_messages():
    events, buffer = _sse_events(
        {"body": b"event: token\ndata: \"a\"\n"},
        "",
    )
    assert events == []
    events, buffer = _sse_events(
        {"body": b"\nevent: done\ndata: {}\n\n"},
        buffer,
    )
    assert events == [("token", '\"a\"'), ("done", "{}")]
    assert buffer == ""


def test_route_label_never_keeps_task_or_source_identifiers():
    assert route_label({"path": "/api/tasks/secret?x=1"}) == "/api/tasks/{task_id}"
    assert route_label({"path": "/api/kb/documents/private.pdf"}) == "/api/kb/documents/{source}"


def test_route_label_keeps_fixed_chat_routes_without_router_metadata():
    assert route_label({"path": "/api/chat/general"}) == "/api/chat/general"
    assert route_label({"path": "/api/chat/draft"}) == "/api/chat/draft"


def test_request_id_is_server_owned_and_existing_values_are_removed():
    message = {
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"x-request-id", b"client-spoof"),
            (b"X-Request-ID", b"downstream-spoof"),
            (b"content-type", b"application/json"),
        ],
    }
    result = _with_request_id(message, "server-generated")
    ids = [value for name, value in result["headers"] if name.lower() == b"x-request-id"]
    assert ids == [b"server-generated"]


def test_access_phase_durations_use_explicit_milliseconds():
    assert _phase_durations_ms({"retrieval": 0.125, "rerank": 0.05}, 0.25) == {
        "retrieval_ms": 125.0,
        "rerank_ms": 50.0,
        "total_ms": 250.0,
    }


def test_cancelled_sse_is_interrupted_not_unhandled():
    reset_metrics()

    async def cancelled_app(_scope, _receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        raise asyncio.CancelledError

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        return None

    scope = {"type": "http", "method": "POST", "path": "/api/chat"}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ObservabilityMiddleware(cancelled_app)(scope, receive, send))

    rendered = render_metrics()
    assert 'rag_sse_duration_seconds_count{route="/api/chat",terminal="interrupted"} 1' in rendered
    assert 'outcome="interrupted"' in rendered
    assert 'rag_errors_total{code="unhandled"} 1' not in rendered


def test_sse_exception_after_headers_is_error_not_interrupted():
    reset_metrics()

    async def failing_app(_scope, _receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        raise RuntimeError("generation failed")

    async def receive():
        return {"type": "http.request"}

    async def send(_message):
        return None

    scope = {"type": "http", "method": "POST", "path": "/api/chat"}
    with pytest.raises(RuntimeError):
        asyncio.run(ObservabilityMiddleware(failing_app)(scope, receive, send))

    rendered = render_metrics()
    assert 'rag_sse_duration_seconds_count{route="/api/chat",terminal="error"} 1' in rendered
    assert 'outcome="error"' in rendered
    assert 'rag_errors_total{code="unhandled"} 1' in rendered


def test_non_sse_exception_after_headers_is_not_counted_as_success():
    reset_metrics()

    async def failing_app(_scope, _receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        raise RuntimeError("response failed")

    async def receive():
        return {"type": "http.request"}

    async def send(_message):
        return None

    scope = {"type": "http", "method": "GET", "path": "/api/health"}
    with pytest.raises(RuntimeError):
        asyncio.run(ObservabilityMiddleware(failing_app)(scope, receive, send))

    rendered = render_metrics()
    assert 'rag_requests_total{route="/api/health",method="GET",status="200",route_type="request",outcome="error"} 1' in rendered


def test_retrieval_and_rerank_stages_are_recorded_separately(monkeypatch):
    from enterprise_rag.rag import retriever

    stages: list[str] = []

    @contextmanager
    def stage(name: str):
        stages.append(name)
        yield

    class Reranker:
        def predict(self, pairs, batch_size):
            assert pairs == [["question", "document"]]
            return [0.9]

    monkeypatch.setattr(retriever, "timed_stage", stage)
    monkeypatch.setattr(
        retriever,
        "get_search_function",
        lambda: lambda _query, n_results: {
            "documents": [["document"]],
            "metadatas": [[{"source": "source.txt", "chapter": "chapter"}]],
            "distances": [[0.1]],
            "_kb_generation": 3,
        },
    )
    monkeypatch.setattr(retriever, "get_reranker", lambda: Reranker())

    result = retriever.retrieve_context(
        "question",
        return_raw=True,
        return_generation=True,
    )

    assert stages == ["retrieval", "rerank"]
    assert result[3] == 3
