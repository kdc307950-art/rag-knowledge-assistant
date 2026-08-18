"""Feedback HTTP authorization and SSE finalization contracts."""

from __future__ import annotations

from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


def _configure_users(monkeypatch, tmp_path):
    from backend import auth
    from backend.api import auth as auth_api
    from enterprise_rag.auth.users import TokenManager, UserStore

    store = UserStore(tmp_path / "auth.sqlite3")
    alice = store.create_user("alice", "secret", roles=["viewer"])
    bob = store.create_user("bob", "secret", roles=["viewer"])
    admin = store.create_user("admin", "secret", roles=["admin"])
    manager = TokenManager(secret="test-secret", ttl_seconds=3600)
    for module in (auth,):
        monkeypatch.setattr(module, "AUTH_MODE", "users")
        monkeypatch.setattr(module, "DEPLOYMENT_MODE", "multi_user")
        monkeypatch.setattr(module, "AUTH_SECRET", "test-secret")
        monkeypatch.setattr(module, "_USER_STORE", store)
        monkeypatch.setattr(module, "_TOKEN_MANAGER", manager)
    monkeypatch.setattr(auth_api, "AUTH_MODE", "users")
    monkeypatch.setattr(auth_api, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth_api, "AUTH_SECRET", "test-secret")
    return manager, {"alice": alice, "bob": bob, "admin": admin}


def _headers(manager, user):
    return {"Authorization": f"Bearer {manager.issue(user)}"}


def test_feedback_api_enforces_owner_idempotency_and_admin_review(monkeypatch, tmp_path):
    from backend import main
    from backend.api import feedback
    from enterprise_rag.services.quality_service import QualityService

    manager, users = _configure_users(monkeypatch, tmp_path)
    quality = QualityService(tmp_path / "quality.sqlite3", Fernet.generate_key().decode("ascii"))
    quality.reserve_run(
        run_id="run-1",
        message_id="message-1",
        principal_id=str(users["alice"]["id"]),
        session_id="session-1",
        query="员工可以远程办公吗？",
    )
    quality.finalize_run(run_id="run-1", terminal="done", answer="以制度为准。", feedback_eligible=True)
    monkeypatch.setattr(feedback, "get_quality_service", lambda: quality)

    with TestClient(main.app) as client:
        alice = _headers(manager, users["alice"])
        bob = _headers(manager, users["bob"])
        admin = _headers(manager, users["admin"])
        created = client.post("/api/feedback", headers=alice, json={"message_id": "message-1", "verdict": "up"})
        assert created.status_code == 200
        assert created.json()["status"] == "created"
        assert client.post("/api/feedback", headers=alice, json={"message_id": "message-1", "verdict": "up"}).json()["status"] == "idempotent"
        assert client.post("/api/feedback", headers=alice, json={"message_id": "message-1", "verdict": "down", "reason": "other"}).status_code == 409
        assert client.post("/api/feedback", headers=bob, json={"message_id": "message-1", "verdict": "up"}).status_code == 403
        assert client.get("/api/feedback/review", headers=alice).status_code == 403
        review = client.get("/api/feedback/review", headers=admin)
        assert review.status_code == 200
        feedback_id = review.json()["feedback"][0]["id"]
        assert client.patch(
            f"/api/feedback/{feedback_id}/review",
            headers=admin,
            json={"accepted": True, "adjudication_result": "answer_failure"},
        ).status_code == 200


def test_chat_persists_eligible_run_before_done_event(monkeypatch, tmp_path):
    from backend import auth, main
    from backend.api import chat
    from enterprise_rag.services.quality_service import QualityService

    quality = QualityService(tmp_path / "quality.sqlite3", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(chat, "get_quality_service", lambda: quality)
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "dev")

    class FakeRag:
        _last_meta = {"sources": ["员工手册.md | 第 1 章"]}

        def answer_stream(self, *_args, **_kwargs):
            yield "可远程办公。"

    monkeypatch.setattr(chat, "RagService", FakeRag)
    message_id = str(uuid4())
    with TestClient(main.app) as client:
        response = client.post(
            "/api/chat",
            headers={"X-Message-Id": message_id},
            json={"query": "可以远程办公吗？"},
        )
    assert response.status_code == 200
    assert response.headers["X-Message-Id"] == message_id
    assert '"feedback_eligible":true' in response.text
    run = quality.get_run_by_message(message_id, include_content=True)
    assert run is not None
    assert run["terminal"] == "done"
    assert run["feedback_eligible"] is True
    assert run["answer"] == "可远程办公。"
