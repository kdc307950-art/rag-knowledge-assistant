"""企业微信机器人 Webhook 适配器。

接收 Alertmanager 标准 webhook payload，转换为企业微信机器人 Markdown 消息格式，
再发往 ALERT_WEBHOOK_URL。firing 和 resolved 两类事件均支持。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn


WECHAT_URL = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("wechat_webhook")

app = FastAPI(title="wechat-webhook", docs_url=None, redoc_url=None)

_SEVERITY_ICON = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
_BEIJING = timezone(timedelta(hours=8))


def _fmt_time(iso: str) -> str:
    if not iso or iso.startswith("0001"):
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_BEIJING)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso


def _render_alert(alert: dict[str, Any]) -> str:
    status = alert.get("status", "firing")
    labels = alert.get("labels", {})
    ann = alert.get("annotations", {})

    name = labels.get("alertname", "Unknown")
    severity = labels.get("severity", "warning")
    summary = ann.get("summary", name)
    description = ann.get("description", "")

    if status == "resolved":
        icon = "✅"
        badge = "已恢复"
    else:
        icon = _SEVERITY_ICON.get(severity, "⚠️")
        badge = "触发告警"

    lines = [f"{icon} **[{badge}] {name}**", f"> {summary}"]
    if description:
        lines.append(f"> {description}")
    lines.append(f"> 级别: `{severity}`")

    starts = _fmt_time(alert.get("startsAt", ""))
    if starts:
        lines.append(f"> 开始: {starts} (北京)")
    if status == "resolved":
        ends = _fmt_time(alert.get("endsAt", ""))
        if ends:
            lines.append(f"> 恢复: {ends} (北京)")

    return "\n".join(lines)


def _build_payload(body: dict[str, Any]) -> dict:
    status = body.get("status", "firing")
    alerts: list[dict] = body.get("alerts", [])

    header = "🔴 **RAG 告警通知**" if status != "resolved" else "✅ **RAG 恢复通知**"
    sections = [header, ""]
    for i, alert in enumerate(alerts):
        sections.append(_render_alert(alert))
        if i < len(alerts) - 1:
            sections.append("\n---")

    return {"msgtype": "markdown", "markdown": {"content": "\n".join(sections)}}


@app.post("/webhook")
async def receive(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    status = body.get("status", "unknown")
    n = len(body.get("alerts", []))
    logger.info("received status=%s alerts=%d", status, n)

    if not WECHAT_URL:
        logger.warning("ALERT_WEBHOOK_URL not configured, dropping notification")
        return JSONResponse({"sent": False, "reason": "no_url"})

    payload = _build_payload(body)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(WECHAT_URL, json=payload)
            resp.raise_for_status()
        logger.info("sent status=%s alerts=%d wechat_status=%d", status, n, resp.status_code)
        return JSONResponse({"sent": True, "status": status, "alerts": n})
    except httpx.HTTPStatusError as exc:
        logger.error("wechat returned %d: %s", exc.response.status_code, exc.response.text[:200])
        raise HTTPException(status_code=502, detail="upstream error") from exc
    except Exception as exc:
        logger.error("send failed: %s", exc)
        raise HTTPException(status_code=502, detail="send failed") from exc


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "webhook_configured": bool(WECHAT_URL)})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001, log_config=None)
