"""Reviewed feedback export never becomes an automatic holdout dataset."""

from __future__ import annotations

import json

from cryptography.fernet import Fernet
import pytest


def test_export_writes_manual_dev_candidates_only(monkeypatch, tmp_path):
    from enterprise_rag.services.quality_service import QualityService
    from scripts import export_feedback_cases

    service = QualityService(tmp_path / "quality.sqlite3", Fernet.generate_key().decode("ascii"))
    service.reserve_run(
        run_id="run-1",
        message_id="message-1",
        principal_id="alice",
        session_id="session-1",
        query="报销期限是多久？",
    )
    service.finalize_run(
        run_id="run-1",
        terminal="done",
        answer="以报销制度为准。",
        sources=["报销制度.md"],
        feedback_eligible=True,
    )
    feedback = service.submit_feedback(
        message_id="message-1", principal_id="alice", verdict="down", reason="wrong_source"
    )
    assert service.review_feedback(
        feedback.feedback_id or 0,
        reviewer_id="admin",
        adjudication_result="answer_failure",
        accepted=True,
    )
    dev_root = (tmp_path / "eval" / "dev").resolve()
    holdout_root = (tmp_path / "eval" / "holdout").resolve()
    monkeypatch.setattr(export_feedback_cases, "DEV_ROOT", dev_root)
    monkeypatch.setattr(export_feedback_cases, "HOLDOUT_ROOT", holdout_root)
    monkeypatch.setattr(export_feedback_cases, "get_quality_service", lambda: service)

    target = dev_root / "feedback.jsonl"
    assert export_feedback_cases.export_cases(target) == 1
    record = json.loads(target.read_text(encoding="utf-8"))
    assert record["manual_label_required"] is True
    assert record["query"] == "报销期限是多久？"
    assert "principal_id" not in record
    assert "session_id" not in record
    with pytest.raises(ValueError, match="不能由反馈自动写入"):
        export_feedback_cases.export_cases(holdout_root / "feedback.jsonl")
    with pytest.raises(ValueError, match="输出必须位于"):
        export_feedback_cases.export_cases(tmp_path / "outside.jsonl")
