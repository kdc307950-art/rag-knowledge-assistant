"""Read-only diagnostics and log-summary tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient

from enterprise_rag.services import diagnostics_service


def test_recent_error_summary_filters_old_entries_and_redacts(tmp_path, monkeypatch):
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    lines = [
        {
            "timestamp": (now - timedelta(hours=25)).isoformat(),
            "level": "ERROR",
            "logger": "old",
            "message": "old failure",
        },
        {
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "level": "ERROR",
            "logger": "new",
            "message": "request used sk-secret-value",
        },
    ]
    (tmp_path / "error.log").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )

    result = diagnostics_service.recent_error_summary(tmp_path, now=now)

    assert result["count"] == 1
    assert result["recent"][0]["message"] == "request used ***REDACTED***"


def test_build_diagnostics_does_not_load_models_or_call_llm(monkeypatch):
    monkeypatch.setattr(
        diagnostics_service, "get_model_readiness", lambda: (True, "本地缓存可用")
    )
    monkeypatch.setattr(diagnostics_service, "get_model_status", lambda: "待加载")
    monkeypatch.setattr(
        diagnostics_service, "get_reranker_readiness", lambda: (True, "本地缓存可用")
    )
    monkeypatch.setattr(
        diagnostics_service.vector_store,
        "get_vector_store_health",
        lambda: (True, "就绪"),
    )
    monkeypatch.setattr(
        diagnostics_service.vector_store, "get_document_count", lambda: 2
    )
    monkeypatch.setattr(diagnostics_service.vector_store, "get_doc_count", lambda: 5)

    class Snapshot:
        generation = 7
        initialized = True
        active_revisions = {"a.pdf": "r1", "b.pdf": "r2"}
        chunk_counts = {"a.pdf": 2, "b.pdf": 3}

    class Manifest:
        def snapshot(self):
            return Snapshot()

    monkeypatch.setattr(diagnostics_service, "get_manifest", lambda: Manifest())
    monkeypatch.setattr(
        diagnostics_service,
        "recent_error_summary",
        lambda: {"count": 0, "recent": [], "truncated": False},
    )
    monkeypatch.setattr(diagnostics_service, "API_KEY", "configured")
    monkeypatch.setattr(
        diagnostics_service,
        "llm_cost_snapshot",
        lambda: {"estimated": True, "totals": [{"currency": "CNY", "amount": 0.1}]},
    )
    monkeypatch.setattr(
        diagnostics_service,
        "price_table_status",
        lambda: {"configured": True, "entries": 1},
    )

    report = diagnostics_service.build_diagnostics()

    assert report["ok"] is True
    assert report["checks"]["manifest"]["status"] == "一致"
    assert report["checks"]["embedding"]["state"] == "available"
    assert report["checks"]["llm"]["network_verified"] is False
    assert report["kb"] == {"document_count": 2, "chunk_count": 5, "generation": 7}
    assert report["llm_cost"] == {
        "estimated": True,
        "totals": [{"currency": "CNY", "amount": 0.1}],
        "price_table": {"configured": True, "entries": 1},
    }


def test_manifest_count_mismatch_marks_report_unhealthy(monkeypatch):
    monkeypatch.setattr(diagnostics_service, "get_model_readiness", lambda: (True, "已加载"))
    monkeypatch.setattr(diagnostics_service, "get_model_status", lambda: "已加载")
    monkeypatch.setattr(
        diagnostics_service, "get_reranker_readiness", lambda: (True, "已加载")
    )
    monkeypatch.setattr(
        diagnostics_service.vector_store,
        "get_vector_store_health",
        lambda: (True, "就绪"),
    )
    monkeypatch.setattr(
        diagnostics_service.vector_store, "get_document_count", lambda: 1
    )
    monkeypatch.setattr(diagnostics_service.vector_store, "get_doc_count", lambda: 2)

    class Snapshot:
        generation = 3
        initialized = True
        active_revisions = {"a.pdf": "r1"}
        chunk_counts = {"a.pdf": 3}

    class Manifest:
        def snapshot(self):
            return Snapshot()

    monkeypatch.setattr(diagnostics_service, "get_manifest", lambda: Manifest())
    monkeypatch.setattr(
        diagnostics_service,
        "recent_error_summary",
        lambda: {"count": 0, "recent": [], "truncated": False},
    )
    monkeypatch.setattr(diagnostics_service, "API_KEY", "configured")

    report = diagnostics_service.build_diagnostics()

    assert report["ok"] is False
    assert report["checks"]["manifest"]["status"] == "不一致"


def test_manifest_failure_does_not_mask_vector_health(monkeypatch):
    monkeypatch.setattr(diagnostics_service, "get_model_readiness", lambda: (True, "已加载"))
    monkeypatch.setattr(diagnostics_service, "get_model_status", lambda: "已加载")
    monkeypatch.setattr(
        diagnostics_service, "get_reranker_readiness", lambda: (True, "已加载")
    )
    monkeypatch.setattr(
        diagnostics_service.vector_store,
        "get_vector_store_health",
        lambda: (True, "就绪"),
    )
    monkeypatch.setattr(
        diagnostics_service.vector_store, "get_document_count", lambda: 0
    )
    monkeypatch.setattr(diagnostics_service.vector_store, "get_doc_count", lambda: 0)
    monkeypatch.setattr(
        diagnostics_service, "get_manifest", lambda: (_ for _ in ()).throw(RuntimeError("broken"))
    )
    monkeypatch.setattr(
        diagnostics_service,
        "recent_error_summary",
        lambda: {"count": 0, "recent": [], "truncated": False},
    )
    monkeypatch.setattr(diagnostics_service, "API_KEY", "configured")

    report = diagnostics_service.build_diagnostics()

    assert report["checks"]["vector_store"] == {"ok": True, "status": "就绪"}
    assert report["checks"]["manifest"]["ok"] is False


def test_diagnostics_route_uses_api_authentication(monkeypatch):
    from backend import auth, main
    from backend.api import diagnostics as diagnostics_api

    report = {
        "ok": True,
        "checked_at": "2026-08-17T00:00:00+00:00",
        "checks": {},
        "kb": {"document_count": 0, "chunk_count": 0, "generation": 0},
        "errors": {"count": 0, "recent": [], "truncated": False},
    }
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "single_user")
    monkeypatch.setattr(auth, "AUTH_MODE", "legacy")
    monkeypatch.setattr(auth, "APP_PASSWORD", "test-key")
    monkeypatch.setattr(diagnostics_api, "build_diagnostics", lambda: report)
    monkeypatch.setattr(main, "build_diagnostics", lambda: report)

    with TestClient(main.app) as client:
        assert client.get("/api/diagnostics").status_code == 401
        response = client.get(
            "/api/diagnostics", headers={"X-API-Key": "test-key"}
        )

    assert response.status_code == 200
    assert response.json() == report


def test_diagnostics_route_enforces_multi_user_admin_role(monkeypatch, tmp_path):
    """The global diagnostics endpoint is authenticated and admin-only."""

    from backend import auth, main
    from backend.api import auth as auth_api
    from backend.api import diagnostics as diagnostics_api
    from enterprise_rag import config
    from enterprise_rag.auth.users import TokenManager, UserStore

    report = {
        "ok": True,
        "checked_at": "2026-08-19T00:00:00+00:00",
        "checks": {},
        "kb": {"document_count": 0, "chunk_count": 0, "generation": 0},
        "errors": {"count": 0, "recent": [], "truncated": False},
    }
    secret = "A9f4xQ2mL7pR5vT8zK3nW6cH1sD0eY4u"
    store = UserStore(tmp_path / "auth.sqlite3")
    viewer = store.create_user(
        "viewer", "secret", department="hr", roles=["viewer"]
    )
    editor = store.create_user(
        "editor", "secret", department="hr", roles=["editor"]
    )
    admin = store.create_user(
        "admin", "secret", department="general", roles=["admin"]
    )
    manager = TokenManager(secret=secret, ttl_seconds=3600)

    # Keep this test independent of the developer's process environment and of
    # the startup diagnostics implementation.
    monkeypatch.setattr(config, "RAG_ENVIRONMENT", "development")
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth, "AUTH_MODE", "users")
    monkeypatch.setattr(auth, "AUTH_SECRET", secret)
    monkeypatch.setattr(auth, "_USER_STORE", store)
    monkeypatch.setattr(auth, "_TOKEN_MANAGER", manager)
    monkeypatch.setattr(auth_api, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth_api, "AUTH_MODE", "users")
    monkeypatch.setattr(auth_api, "AUTH_SECRET", secret)
    monkeypatch.setattr(diagnostics_api, "build_diagnostics", lambda: report)
    monkeypatch.setattr(main, "build_diagnostics", lambda: report)

    def request_with(user):
        token = manager.issue(user)
        return {"Authorization": f"Bearer {token}"}

    with TestClient(main.app) as client:
        assert client.get("/api/diagnostics").status_code == 401
        assert client.get(
            "/api/diagnostics", headers=request_with(viewer)
        ).status_code == 403
        assert client.get(
            "/api/diagnostics", headers=request_with(editor)
        ).status_code == 403
        admin_response = client.get(
            "/api/diagnostics", headers=request_with(admin)
        )

    assert admin_response.status_code == 200
    assert admin_response.json() == report
