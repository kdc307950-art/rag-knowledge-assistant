"""验证结构化日志、脱敏规则和历史事故台账。"""

from __future__ import annotations

import json
import logging
import os
import time

from enterprise_rag.utils.logger import (
    _cleanup_old_log_files,
    log_access_event,
    log_audit_event,
    setup_logger,
)


def test_error_log_is_jsonl_and_redacts_secrets(monkeypatch, tmp_path):
    """错误日志应可逐行解析，并且不能保留模型服务密钥明文。"""
    secret = "test-api-key-should-not-appear"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)
    setup_logger(log_dir=tmp_path, level="INFO", reset=True)

    logging.getLogger("enterprise_rag.tests.logging").error(
        "模型请求失败: key=%s bearer %s",
        secret,
        "Bearer abcdefghijklmnopqrstuvwxyz",
    )
    for handler in logging.getLogger("enterprise_rag").handlers:
        handler.flush()

    records = [
        json.loads(line)
        for line in (tmp_path / "error.log").read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["level"] == "ERROR"
    assert secret not in records[-1]["message"]
    assert "***REDACTED***" in records[-1]["message"]


def test_request_id_is_injected_into_regular_logs(tmp_path):
    from backend.observability.context import (
        RequestTelemetry,
        bind_request_telemetry,
        reset_request_telemetry,
    )

    setup_logger(log_dir=tmp_path, level="INFO", reset=True)
    token = bind_request_telemetry(RequestTelemetry(request_id="request-42", method="GET"))
    try:
        logging.getLogger("enterprise_rag.tests.logging").error("request-scoped failure")
    finally:
        reset_request_telemetry(token)
    for handler in logging.getLogger("enterprise_rag").handlers:
        handler.flush()

    record = json.loads((tmp_path / "error.log").read_text(encoding="utf-8").splitlines()[-1])
    assert record["request_id"] == "request-42"


def test_historical_incidents_are_seeded_once(tmp_path):
    """首次初始化应写入已复盘事故，重复初始化不能追加重复记录。"""
    setup_logger(log_dir=tmp_path, level="INFO", reset=True)
    setup_logger(log_dir=tmp_path, level="INFO")

    incidents = [
        json.loads(line)
        for line in (tmp_path / "incident_history.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    incident_ids = [item["incident_id"] for item in incidents]
    assert "INC-2026-08-hnsw-recovery" in incident_ids
    assert len(incident_ids) == len(set(incident_ids))


def test_access_and_audit_logs_keep_structured_redacted_fields(tmp_path):
    setup_logger(log_dir=tmp_path, level="INFO", reset=True)
    log_access_event(
        request_id="request-1",
        route="/api/tasks/{task_id}",
        method="GET",
        status_code=200,
        duration_ms=12.5,
        phases={"retrieval": 0.1},
    )
    log_audit_event("document_deleted", source_digest="abc123")
    for logger_name in ("enterprise_rag.access", "enterprise_rag.audit"):
        for handler in logging.getLogger(logger_name).handlers:
            handler.flush()

    access = json.loads((tmp_path / "access.log").read_text(encoding="utf-8").splitlines()[-1])
    audit = json.loads((tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()[-1])
    assert access["route"] == "/api/tasks/{task_id}"
    assert access["phases"] == {"retrieval": "0.1"} or access["phases"] == {"retrieval": 0.1}
    assert "document_deleted" == audit["event"]


def test_setup_logger_repairs_missing_event_handlers(tmp_path):
    setup_logger(log_dir=tmp_path, level="INFO", reset=True)
    access_logger = logging.getLogger("enterprise_rag.access")
    for handler in list(access_logger.handlers):
        access_logger.removeHandler(handler)
        handler.close()

    setup_logger(log_dir=tmp_path, level="INFO")
    log_access_event(request_id="repaired", route="/api/live", status_code=200)
    for handler in access_logger.handlers:
        handler.flush()

    records = [
        json.loads(line)
        for line in (tmp_path / "access.log").read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["request_id"] == "repaired"


def test_log_retention_never_deletes_active_files(tmp_path):
    active = tmp_path / "app.log"
    rotated = tmp_path / "app.log.1"
    active.write_text("active\n", encoding="utf-8")
    rotated.write_text("old\n", encoding="utf-8")
    old = time.time() - 365 * 86400
    os.utime(active, (old, old))
    os.utime(rotated, (old, old))

    _cleanup_old_log_files(tmp_path)

    assert active.is_file()
    assert not rotated.exists()
