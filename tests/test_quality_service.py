"""Unit coverage for encrypted, single-tenant feedback persistence."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from enterprise_rag.services.quality_service import (
    DuplicateRunError,
    FeedbackConflictError,
    FeedbackForbiddenError,
    FeedbackIneligibleError,
    FeedbackRateLimitError,
    QualityService,
    utc_ms,
)


def _service(tmp_path, **kwargs) -> QualityService:
    return QualityService(
        tmp_path / "quality.sqlite3",
        Fernet.generate_key().decode("ascii"),
        **kwargs,
    )


def test_quality_store_rejects_invalid_fernet_key(tmp_path):
    with pytest.raises(ValueError, match="Fernet key"):
        QualityService(tmp_path / "quality.sqlite3", "not-a-fernet-key")


def _eligible_run(service: QualityService, *, run_id="run-1", message_id="message-1", principal_id="alice"):
    service.reserve_run(
        run_id=run_id,
        message_id=message_id,
        principal_id=principal_id,
        session_id="session-1",
        query="员工的保密义务是什么？",
    )
    assert service.finalize_run(
        run_id=run_id,
        terminal="done",
        answer="员工应遵守保密制度。",
        sources=["员工手册.md | 保密"],
        feedback_eligible=True,
    )


def test_quality_store_uses_one_sqlite_database_with_encrypted_content(tmp_path):
    service = _service(tmp_path)
    _eligible_run(service)

    with service._connect() as connection:  # pragma is enforced for every service connection.
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    raw = service.db_path.read_bytes()
    assert "员工的保密义务是什么？".encode() not in raw
    assert "员工应遵守保密制度。".encode() not in raw

    run = service.get_run_by_message("message-1", include_content=True)
    assert run is not None
    assert run["query"] == "员工的保密义务是什么？"
    assert run["answer"] == "员工应遵守保密制度。"
    assert run["feedback_eligible"] is True


def test_runs_are_single_finalization_and_message_ids_are_unique(tmp_path):
    service = _service(tmp_path)
    _eligible_run(service)
    assert not service.finalize_run(run_id="run-1", terminal="done", answer="重复终态")
    with pytest.raises(DuplicateRunError):
        service.reserve_run(
            run_id="run-2",
            message_id="message-1",
            principal_id="alice",
            session_id="session-2",
            query="重复请求",
        )


def test_feedback_enforces_owner_idempotency_conflict_and_rate_limit(tmp_path):
    service = _service(tmp_path, feedback_limit_per_hour=1)
    _eligible_run(service)
    _eligible_run(service, run_id="run-2", message_id="message-2")

    created = service.submit_feedback(
        message_id="message-1", principal_id="alice", verdict="up", reason=None
    )
    assert created.status == "created"
    assert service.submit_feedback(
        message_id="message-1", principal_id="alice", verdict="up", reason=None
    ).status == "idempotent"
    with pytest.raises(FeedbackConflictError):
        service.submit_feedback(
            message_id="message-1", principal_id="alice", verdict="down", reason="other"
        )
    with pytest.raises(FeedbackForbiddenError):
        service.submit_feedback(
            message_id="message-1", principal_id="bob", verdict="up", reason=None
        )
    with pytest.raises(FeedbackRateLimitError):
        service.submit_feedback(
            message_id="message-2", principal_id="alice", verdict="up", reason=None
        )


def test_ineligible_runs_cannot_receive_feedback_and_admin_review_is_auditable(tmp_path):
    service = _service(tmp_path)
    service.reserve_run(
        run_id="failed",
        message_id="failed-message",
        principal_id="alice",
        session_id="session-1",
        query="未完成问题",
    )
    assert service.finalize_run(run_id="failed", terminal="error", error_code="rate_limit")
    with pytest.raises(FeedbackIneligibleError):
        service.submit_feedback(
            message_id="failed-message", principal_id="alice", verdict="up", reason=None
        )

    _eligible_run(service)
    feedback = service.submit_feedback(
        message_id="message-1", principal_id="alice", verdict="down", reason="wrong_source"
    )
    rows = service.list_feedback_for_review()
    assert rows[0]["id"] == feedback.feedback_id
    assert rows[0]["query"] == "员工的保密义务是什么？"
    assert service.review_feedback(
        feedback.feedback_id or 0,
        reviewer_id="admin",
        adjudication_result="answer_failure",
        accepted=True,
    )
    assert service.list_feedback_for_review(status="pending") == []
    assert service.list_feedback_for_review(status="accepted")[0]["reviewer_id"] == "admin"
    exported = service.export_accepted_feedback()
    assert exported[0]["query"] == "员工的保密义务是什么？"
    assert "principal_id" not in exported[0]
    assert "session_id" not in exported[0]


def test_retention_purge_removes_feedback_before_runs(tmp_path):
    service = _service(tmp_path, retention_days=1)
    _eligible_run(service)
    service.submit_feedback(message_id="message-1", principal_id="alice", verdict="up", reason=None)
    result = service.purge_expired(now_ms=utc_ms() + 2 * 86400000)
    assert result == {"feedback": 1, "runs": 1}
