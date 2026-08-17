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

    report = diagnostics_service.build_diagnostics()

    assert report["ok"] is True
    assert report["checks"]["manifest"]["status"] == "一致"
    assert report["checks"]["embedding"]["state"] == "available"
    assert report["checks"]["llm"]["network_verified"] is False
    assert report["kb"] == {"document_count": 2, "chunk_count": 5, "generation": 7}


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
