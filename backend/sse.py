"""SSE 事件格式化工具。"""
from __future__ import annotations

import json


def sse(event: str, data) -> str:
    """把事件名与 JSON 数据序列化为一条 text/event-stream 消息。"""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"
