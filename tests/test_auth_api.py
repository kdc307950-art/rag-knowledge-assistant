"""Optional users-mode API tests; legacy mode remains unchanged by default."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_users_login_me_logout_flow(monkeypatch, tmp_path):
    from backend import auth, main
    from backend.api import auth as auth_api
    from enterprise_rag.auth.users import TokenManager, UserStore

    store = UserStore(tmp_path / "auth.sqlite3")
    store.create_user("alice", "secret", display_name="Alice", department="hr")
    monkeypatch.setattr(auth, "AUTH_MODE", "users")
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_api, "AUTH_MODE", "users")
    monkeypatch.setattr(auth_api, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth_api, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth, "_USER_STORE", store)
    monkeypatch.setattr(
        auth,
        "_TOKEN_MANAGER",
        TokenManager(secret="test-secret", ttl_seconds=3600),
    )

    with TestClient(main.app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret"},
        )
        assert login.status_code == 200
        token = login.json()["token"]
        assert "rag_access=" in login.headers.get("set-cookie", "")
        cookie_me = client.get("/api/auth/me")
        assert cookie_me.status_code == 200
        assert cookie_me.json()["user"]["username"] == "alice"
        me = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 200
        assert me.json()["user"]["department"] == "hr"
        logout = client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert logout.status_code == 200
        assert (
            client.get(
                "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )
        assert client.get("/api/auth/me").status_code == 401


def test_users_login_is_rate_limited(monkeypatch, tmp_path):
    from backend import auth, main
    from backend.api import auth as auth_api
    from enterprise_rag.auth.users import TokenManager, UserStore

    store = UserStore(tmp_path / "auth.sqlite3")
    store.create_user("alice", "secret")
    monkeypatch.setattr(auth, "AUTH_MODE", "users")
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_api, "AUTH_MODE", "users")
    monkeypatch.setattr(auth_api, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth_api, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth, "_USER_STORE", store)
    monkeypatch.setattr(auth, "_TOKEN_MANAGER", TokenManager(secret="test-secret", ttl_seconds=3600))
    auth_api._LOGIN_FAILURES.clear()

    with TestClient(main.app) as client:
        for _ in range(5):
            assert client.post("/api/auth/login", json={"username": "alice", "password": "bad"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "alice", "password": "bad"}).status_code == 429


def test_users_login_sets_cookie_with_secure_cross_origin_policy(monkeypatch, tmp_path):
    from backend import auth, main
    from backend.api import auth as auth_api
    from enterprise_rag.auth.users import TokenManager, UserStore

    store = UserStore(tmp_path / "auth.sqlite3")
    store.create_user("alice", "secret")
    monkeypatch.setattr(auth, "AUTH_MODE", "users")
    monkeypatch.setattr(auth, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_api, "AUTH_MODE", "users")
    monkeypatch.setattr(auth_api, "DEPLOYMENT_MODE", "multi_user")
    monkeypatch.setattr(auth_api, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth, "_USER_STORE", store)
    monkeypatch.setattr(auth, "_TOKEN_MANAGER", TokenManager(secret="test-secret", ttl_seconds=3600))

    with TestClient(main.app) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret"},
            headers={"Origin": "http://localhost:5173"},
        )
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
