"""验证结构化日志、脱敏规则和历史事故台账。"""

from __future__ import annotations

import json
import logging

from enterprise_rag.utils.logger import setup_logger


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
