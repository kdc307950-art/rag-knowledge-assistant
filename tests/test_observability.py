"""API-level observability contracts."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_live_and_metrics_are_available_without_app_password(monkeypatch):
    from backend import main
    from backend.api import metrics as metrics_api
    from backend.observability.metrics import reset_metrics

    reset_metrics()
    monkeypatch.setattr(metrics_api, "METRICS_TOKEN", "test-metrics-token")
    monkeypatch.setattr(main, "build_diagnostics", lambda: {"checks": {}})
    with TestClient(main.app) as client:
        live = client.get("/api/live")
        metrics = client.get("/metrics", headers={"X-Metrics-Token": "test-metrics-token"})

    assert live.status_code == 200
    assert live.json() == {"ok": True}
    assert live.headers.get("X-Request-Id")
    assert metrics.status_code == 200
    assert "rag_requests_total" in metrics.text


def test_ready_returns_503_when_dependency_unready(monkeypatch):
    from backend import main
    from backend.api import probes

    monkeypatch.setattr(probes, "_readiness", lambda: {"ready": False, "status": "broken"})
    with TestClient(main.app) as client:
        response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["ready"] is False


def test_ready_reports_missing_model_as_unready(monkeypatch):
    from backend import main
    from backend.api import probes

    monkeypatch.setattr(probes.vector_store, "get_vector_store_health", lambda: (True, "就绪"))
    monkeypatch.setattr(probes, "get_model_readiness", lambda: (False, "离线模式缺少嵌入模型"))
    monkeypatch.setattr(probes, "get_reranker_readiness", lambda: (True, "本地缓存可用"))
    with TestClient(main.app) as client:
        response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "model_unavailable"
    assert response.json()["embedding"]["ready"] is False


def test_auth_failure_is_audited_and_counted(monkeypatch):
    from backend import auth, main
    from backend.observability import metrics as observability_metrics

    events: list[dict] = []
    monkeypatch.setattr(auth, "APP_PASSWORD", "secret")
    monkeypatch.setattr(auth, "log_audit_event", lambda event, **payload: events.append({"event": event, **payload}))
    observability_metrics.reset_metrics()

    with TestClient(main.app) as client:
        response = client.get("/api/health", headers={"X-API-Key": "wrong"})

    assert response.status_code == 401
    assert events and events[-1]["event"] == "auth_failure"
    assert events[-1]["route"] == "/api/health"
    assert "rag_auth_failures_total" in observability_metrics.render_metrics()


def test_empty_knowledge_base_is_ready_in_diagnostics(monkeypatch):
    from enterprise_rag.services import diagnostics_service

    monkeypatch.setattr(diagnostics_service, "get_model_readiness", lambda: (True, "已加载"))
    monkeypatch.setattr(diagnostics_service, "get_model_status", lambda: "已加载")
    monkeypatch.setattr(diagnostics_service, "get_reranker_readiness", lambda: (True, "已加载"))
    monkeypatch.setattr(diagnostics_service.vector_store, "get_vector_store_health", lambda: (True, "就绪"))
    monkeypatch.setattr(diagnostics_service.vector_store, "get_document_count", lambda: 0)
    monkeypatch.setattr(diagnostics_service.vector_store, "get_doc_count", lambda: 0)
    monkeypatch.setattr(diagnostics_service, "API_KEY", "configured")
    monkeypatch.setattr(diagnostics_service, "recent_error_summary", lambda: {"count": 0, "recent": [], "truncated": False})

    class Snapshot:
        generation = 0
        initialized = False
        active_revisions = {}
        chunk_counts = {}

    class Manifest:
        def snapshot(self):
            return Snapshot()

    monkeypatch.setattr(diagnostics_service, "get_manifest", lambda: Manifest())
    report = diagnostics_service.build_diagnostics()
    assert report["checks"]["manifest"]["ok"] is True
