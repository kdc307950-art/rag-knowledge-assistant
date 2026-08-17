"""FastAPI SSE 桥接的线程、终态和断连回归测试。"""

from __future__ import annotations

import asyncio
import json
import threading

from backend.api import chat


def _event(raw: str) -> tuple[str, object]:
    lines = raw.strip().splitlines()
    event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
    data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
    return event, json.loads(data)


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_business_failure_uses_error_terminal_event():
    kind, payload = chat._terminal_event(
        {
            "action_failed": True,
            "is_interrupted": True,
            "error_code": "rate_limit",
        }
    )

    assert kind == "error"
    assert payload["code"] == "rate_limit"
    assert payload["partial"] is True


def test_producer_emits_tokens_and_one_done_event():
    emitted: list[tuple[str, object]] = []
    producer = chat._make_producer(lambda: iter(["A", "B"]), lambda: {"sources": []})

    producer(lambda kind, payload: emitted.append((kind, payload)), threading.Event())

    assert emitted == [
        ("token", "A"),
        ("token", "B"),
        ("done", {"sources": []}),
        ("__end__", None),
    ]


def test_run_sse_collects_only_delivered_tokens():
    async def scenario():
        collected: list[str] = []
        outcome = chat._StreamOutcome()
        producer = chat._make_producer(
            lambda: iter(["第一段", "第二段"]), lambda: {"sources": []}
        )
        chunks = [
            chunk
            async for chunk in chat._run_sse(
                producer, collected, _ConnectedRequest(), outcome
            )
        ]
        return collected, outcome, [_event(chunk) for chunk in chunks]

    collected, outcome, events = asyncio.run(scenario())

    assert collected == ["第一段", "第二段"]
    assert outcome.terminal == "done"
    assert events[-1] == ("done", {"sources": []})


def test_run_sse_detects_disconnect_while_producer_is_idle(monkeypatch):
    monkeypatch.setattr(chat, "_DISCONNECT_POLL_SECONDS", 0.01)

    class DisconnectingRequest:
        def __init__(self):
            self.calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls >= 2

    stopped = threading.Event()

    def producer(_emit, stop_event):
        stop_event.wait(timeout=1)
        if stop_event.is_set():
            stopped.set()

    async def scenario():
        outcome = chat._StreamOutcome()
        chunks = [
            chunk
            async for chunk in chat._run_sse(
                producer, [], DisconnectingRequest(), outcome
            )
        ]
        await asyncio.sleep(0.02)
        return chunks, outcome

    chunks, outcome = asyncio.run(scenario())

    assert chunks == []
    assert outcome.terminal == "disconnected"
    assert stopped.wait(timeout=1)


def test_meta_payload_keeps_failure_flags():
    class Rag:
        _last_meta = {
            "sources": [],
            "action_failed": True,
            "is_interrupted": True,
            "is_empty": False,
            "error_code": "authentication",
        }

    payload = chat._meta_payload(Rag())

    assert payload["action_failed"] is True
    assert payload["is_interrupted"] is True
    assert payload["error_code"] == "authentication"


def test_meta_payload_keeps_canonical_draft_inputs():
    class Rag:
        _last_meta = {
            "query": "帮我写请假邮件",
            "retrieval_query": "请假制度",
        }

    payload = chat._meta_payload(Rag())

    assert payload["query"] == "帮我写请假邮件"
    assert payload["retrieval_query"] == "请假制度"


def test_only_successful_done_content_is_persisted():
    assert chat._should_persist_history(chat._StreamOutcome("done"), "完整回答")
    assert not chat._should_persist_history(chat._StreamOutcome("error"), "部分回答")
    assert not chat._should_persist_history(
        chat._StreamOutcome("disconnected"), "部分回答"
    )
    assert not chat._should_persist_history(chat._StreamOutcome("done"), "")
