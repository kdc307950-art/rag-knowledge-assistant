"""Deployment-mode and user-store readiness contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_dev_mode_is_ready_with_explicit_warning(monkeypatch):
    from enterprise_rag import config

    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "dev")
    monkeypatch.setattr(config, "AUTH_MODE", "legacy")
    result = config.auth_readiness()
    assert result["configured"] is True
    assert result["warning"] == "authentication_disabled"


def test_invalid_deployment_mode_is_not_ready(monkeypatch):
    from enterprise_rag import config

    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "legacy")
    result = config.auth_readiness()
    assert result["configured"] is False
    assert result["code"] == "invalid_deployment_mode"


def test_single_user_requires_password(monkeypatch):
    from enterprise_rag import config

    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "single_user")
    monkeypatch.setattr(config, "AUTH_MODE", "legacy")
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    result = config.auth_readiness()
    assert result["configured"] is False
    assert result["code"] == "auth_unconfigured"


def test_multi_user_requires_strict_mode_secret_and_admin(monkeypatch, tmp_path):
    from enterprise_rag import config
    from enterprise_rag.auth.users import UserStore

    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(config, "AUTH_MODE", "legacy")
    monkeypatch.setattr(config, "AUTH_SECRET", "test-secret")
    result = config.auth_readiness()
    assert result["code"] == "invalid_mode_combination"

    monkeypatch.setattr(config, "AUTH_MODE", "users")
    monkeypatch.setattr(config, "AUTH_SECRET", "")
    result = config.auth_readiness()
    assert result["code"] == "auth_unconfigured"

    path = tmp_path / "auth.sqlite3"
    monkeypatch.setattr(config, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(config, "AUTH_DB_PATH", path)
    result = config.auth_readiness()
    assert result["code"] == "auth_admin_missing"

    UserStore(path).create_user("admin", "secret", roles=["admin"])
    result = config.auth_readiness()
    assert result["configured"] is True
    assert result["admin_count"] == 1


def test_user_store_readiness_fails_closed_for_invalid_roles(tmp_path):
    from enterprise_rag.auth.users import UserStore

    store = UserStore(tmp_path / "auth.sqlite3")
    store.create_user("admin", "secret", roles=["admin"])
    with store._connect() as connection:
        connection.execute("UPDATE users SET roles_json = ?", ('["unknown"]',))
    health = store.readiness()
    assert health["readable"] is True
    assert health["writable"] is True
    assert health["roles_valid"] is False
    assert health["active_admin_count"] == 0


def test_ready_returns_auth_configuration_error(monkeypatch):
    from backend import main
    from backend.api import probes
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        probes,
        "auth_readiness",
        lambda: {
            "configured": False,
            "code": "auth_admin_missing",
            "mode": "multi_user",
        },
    )
    with TestClient(main.app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "auth_admin_missing"


def test_single_user_keeps_x_api_key_compatibility(monkeypatch):
    from backend import auth

    request = SimpleNamespace(
        state=SimpleNamespace(),
        scope={"route": None, "path": "/api/test"},
        client=SimpleNamespace(host="127.0.0.1"),
        method="GET",
    )
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "single_user")
    monkeypatch.setattr(auth, "AUTH_MODE", "legacy")
    monkeypatch.setattr(auth, "APP_PASSWORD", "a-long-enough-password")
    asyncio.run(auth.require_access(request, x_api_key="a-long-enough-password"))
    assert request.state.current_user["id"] == "service-admin"


def test_multi_user_never_uses_app_password_as_token_secret(monkeypatch):
    from backend import auth
    from backend.api import auth as auth_api
    from fastapi import HTTPException

    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth, "AUTH_MODE", "users")
    monkeypatch.setattr(auth, "AUTH_SECRET", "")
    monkeypatch.setattr(auth, "APP_PASSWORD", "legacy-password")
    monkeypatch.setattr(auth_api, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth_api, "AUTH_MODE", "users")
    monkeypatch.setattr(auth_api, "AUTH_SECRET", "")
    try:
        auth_api._ensure_users_mode()
    except HTTPException as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("multi_user must require AUTH_SECRET")
