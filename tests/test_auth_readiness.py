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


def test_production_multi_user_requires_secure_cookie_and_https(monkeypatch, tmp_path):
    from enterprise_rag import config
    from enterprise_rag.auth.users import UserStore

    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(config, "AUTH_MODE", "users")
    monkeypatch.setattr(config, "AUTH_SECRET", "A9f4xQ2mL7pR5vT8zK3nW6cH1sD0eY4u")
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://example.test")
    monkeypatch.setattr(config, "RAG_ENVIRONMENT", "production")
    path = tmp_path / "auth.sqlite3"
    monkeypatch.setattr(config, "AUTH_DB_PATH", path)
    UserStore(path).create_user("admin", "secret", roles=["admin"])
    result = config.auth_readiness()
    assert result["code"] == "production_security_not_ready"
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "https://hook.example.test/webhook")
    result = config.auth_readiness()
    assert result["configured"] is True


def test_production_rejects_repeated_auth_secret(monkeypatch):
    from enterprise_rag import config

    monkeypatch.setattr(config, "RAG_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(config, "AUTH_MODE", "users")
    monkeypatch.setattr(config, "AUTH_SECRET", "x" * 64)
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://example.test")

    result = config.auth_readiness()

    assert result["code"] == "production_security_not_ready"


def test_production_cannot_run_in_dev_mode(monkeypatch):
    from enterprise_rag import config

    monkeypatch.setattr(config, "RAG_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "dev")
    monkeypatch.setattr(config, "AUTH_MODE", "legacy")

    result = config.auth_readiness()

    assert result["code"] == "production_security_not_ready"


def test_production_gate_blocks_business_access(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from backend import auth
    from enterprise_rag import config

    monkeypatch.setattr(config, "RAG_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(config, "AUTH_MODE", "users")
    monkeypatch.setattr(config, "AUTH_SECRET", "x" * 64)
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://example.test")
    request = SimpleNamespace(
        state=SimpleNamespace(),
        scope={"route": None, "path": "/api/chat"},
        client=SimpleNamespace(host="127.0.0.1"),
        method="GET",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(auth.require_access(request))

    assert exc_info.value.status_code == 503


def test_production_lifespan_refuses_invalid_configuration(monkeypatch):
    import pytest
    from fastapi.testclient import TestClient

    from backend import main

    monkeypatch.setattr(main.rag_config, "RAG_ENVIRONMENT", "production")
    monkeypatch.setattr(
        main.rag_config,
        "auth_readiness",
        lambda: {"configured": False, "code": "production_security_not_ready"},
    )

    with pytest.raises(RuntimeError, match="生产环境安全门禁未通过"):
        with TestClient(main.app):
            pass


def test_production_cookie_post_rejects_wrong_origin(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from backend import auth
    from enterprise_rag import config
    from enterprise_rag.auth.users import TokenManager, UserStore

    store = UserStore(tmp_path / "auth.sqlite3")
    user = store.create_user("alice", "secret", roles=["viewer"])
    token = TokenManager(secret="A9f4xQ2mL7pR5vT8zK3nW6cH1sD0eY4u", ttl_seconds=3600).issue(user)
    monkeypatch.setattr(config, "RAG_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(config, "AUTH_MODE", "users")
    monkeypatch.setattr(config, "AUTH_SECRET", "A9f4xQ2mL7pR5vT8zK3nW6cH1sD0eY4u")
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://kb.example.test")
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "https://hook.example.test/webhook")
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth, "AUTH_MODE", "users")
    monkeypatch.setattr(auth, "AUTH_SECRET", "A9f4xQ2mL7pR5vT8zK3nW6cH1sD0eY4u")
    monkeypatch.setattr(auth, "_USER_STORE", store)
    monkeypatch.setattr(
        auth,
        "_TOKEN_MANAGER",
        TokenManager(secret="A9f4xQ2mL7pR5vT8zK3nW6cH1sD0eY4u", ttl_seconds=3600),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(),
        scope={"route": None, "path": "/api/auth/logout"},
        client=SimpleNamespace(host="127.0.0.1"),
        method="POST",
        headers={"origin": "https://untrusted.example.test"},
        cookies={config.AUTH_COOKIE_NAME: token},
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(auth.require_access(request))

    assert exc_info.value.status_code == 403


def test_production_cookie_post_allows_public_origin(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from backend import auth
    from enterprise_rag import config
    from enterprise_rag.auth.users import TokenManager, UserStore

    secret = "A9f4xQ2mL7pR5vT8zK3nW6cH1sD0eY4u"
    store = UserStore(tmp_path / "auth.sqlite3")
    user = store.create_user("alice", "secret", roles=["viewer"])
    manager = TokenManager(secret=secret, ttl_seconds=3600)
    monkeypatch.setattr(config, "RAG_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(config, "AUTH_MODE", "users")
    monkeypatch.setattr(config, "AUTH_SECRET", secret)
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://kb.example.test")
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "https://hook.example.test/webhook")
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth, "AUTH_MODE", "users")
    monkeypatch.setattr(auth, "AUTH_SECRET", secret)
    monkeypatch.setattr(auth, "_USER_STORE", store)
    monkeypatch.setattr(auth, "_TOKEN_MANAGER", manager)
    request = SimpleNamespace(
        state=SimpleNamespace(),
        scope={"route": None, "path": "/api/auth/logout"},
        client=SimpleNamespace(host="127.0.0.1"),
        method="POST",
        headers={"origin": "https://kb.example.test"},
        cookies={config.AUTH_COOKIE_NAME: manager.issue(user)},
    )

    asyncio.run(auth.require_access(request))

    assert request.state.current_user["id"] == user["id"]


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


def test_multi_user_api_key_fallback_is_rejected(monkeypatch):
    from backend import auth
    from fastapi import HTTPException

    request = SimpleNamespace(
        state=SimpleNamespace(), scope={"route": None, "path": "/api/test"},
        client=SimpleNamespace(host="127.0.0.1"), method="GET",
    )
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth, "AUTH_MODE", "users")
    monkeypatch.setattr(auth, "AUTH_SECRET", "long-test-secret")
    monkeypatch.setattr(auth, "APP_PASSWORD", "legacy-password")
    try:
        asyncio.run(auth.require_access(request, x_api_key="legacy-password"))
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("multi_user must reject APP_PASSWORD/X-API-Key fallback")
