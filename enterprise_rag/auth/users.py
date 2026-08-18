"""SQLite-backed local users and signed bearer tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any


SCRYPT_PARAMS = {"n": 2**14, "r": 8, "p": 1}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password 不能为空")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT_PARAMS)
    return (
        f"scrypt${SCRYPT_PARAMS['n']}${SCRYPT_PARAMS['r']}${SCRYPT_PARAMS['p']}"
        f"${_b64(salt)}${_b64(digest)}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(_b64(digest), expected)
    except (TypeError, ValueError):
        return False


class UserStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    department TEXT NOT NULL,
                    roles_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    external_id TEXT,
                    created_at REAL NOT NULL,
                    last_login_at REAL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS revoked_tokens (
                    jti TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL
                )"""
            )

    def create_user(
        self,
        username: str,
        password: str,
        *,
        display_name: str | None = None,
        department: str = "general",
        roles: list[str] | None = None,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        username = username.strip()
        if not username or not password:
            raise ValueError("username/password 不能为空")
        normalized_roles = sorted(set(roles or ["viewer"]))
        if not set(normalized_roles).issubset({"admin", "editor", "viewer"}):
            raise ValueError("roles 含未知值")
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO users(
                    username,password_hash,display_name,department,roles_json,
                    external_id,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    username,
                    hash_password(password),
                    display_name or username,
                    department or "general",
                    json.dumps(normalized_roles),
                    external_id,
                    now,
                ),
            )
            user_id = int(cursor.lastrowid)
        return self.get_user(user_id) or {}

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id,username,display_name,department,roles_json,active,
                    external_id,created_at,last_login_at FROM users WHERE id=?""",
                (int(user_id),),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "display_name": row[2],
            "department": row[3],
            "roles": json.loads(row[4]),
            "active": bool(row[5]),
            "external_id": row[6],
            "created_at": row[7],
            "last_login_at": row[8],
        }

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,password_hash FROM users WHERE username=? AND active=1",
                (username.strip(),),
            ).fetchone()
            if not row or not verify_password(password, row[1]):
                return None
            connection.execute(
                "UPDATE users SET last_login_at=? WHERE id=?", (time.time(), row[0])
            )
        return self.get_user(int(row[0]))

    def revoke_token(self, jti: str, expires_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM revoked_tokens WHERE expires_at < ?", (time.time(),)
            )
            connection.execute(
                "INSERT OR REPLACE INTO revoked_tokens(jti,expires_at) VALUES(?,?)",
                (jti, float(expires_at)),
            )

    def is_revoked(self, jti: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM revoked_tokens WHERE jti=? AND expires_at >= ?",
                (jti, time.time()),
            ).fetchone()
        return row is not None

    def readiness(self) -> dict[str, Any]:
        """Perform a bounded local database health check without exposing users."""
        result: dict[str, Any] = {
            "readable": False,
            "writable": False,
            "quick_check": "error",
            "roles_valid": True,
            "active_admin_count": 0,
            "database": "error",
        }
        try:
            with self._connect() as connection:
                check = connection.execute("PRAGMA quick_check").fetchone()
                result["quick_check"] = str(check[0]).lower() if check else "error"
                result["readable"] = result["quick_check"] == "ok"
                rows = connection.execute(
                    "SELECT roles_json FROM users WHERE active=1"
                ).fetchall()
                admin_count = 0
                for (roles_json,) in rows:
                    try:
                        roles = json.loads(str(roles_json))
                    except json.JSONDecodeError:
                        result["roles_valid"] = False
                        continue
                    if not isinstance(roles, list) or not all(
                        isinstance(role, str) and role in {"admin", "editor", "viewer"}
                        for role in roles
                    ):
                        result["roles_valid"] = False
                        continue
                    if "admin" in roles:
                        admin_count += 1
                result["active_admin_count"] = admin_count

                # DDL and DML are rolled back, so this proves write access
                # without retaining a readiness marker in the user database.
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TEMP TABLE readiness_probe(value INTEGER)")
                connection.execute("INSERT INTO readiness_probe(value) VALUES(1)")
                connection.rollback()
                result["writable"] = True
                result["database"] = "ok"
        except (OSError, sqlite3.Error, ValueError):
            return result
        return result


class TokenManager:
    def __init__(self, *, secret: str, ttl_seconds: int):
        if not secret:
            raise ValueError("AUTH_SECRET 不能为空")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = int(ttl_seconds)

    def issue(self, user: dict[str, Any]) -> str:
        now = int(time.time())
        payload = {
            "sub": int(user["id"]),
            "iat": now,
            "exp": now + self.ttl_seconds,
            "jti": secrets.token_urlsafe(18),
        }
        body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = _b64(
            hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{body}.{signature}"

    def verify(self, token: str, *, store: UserStore) -> dict[str, Any]:
        try:
            body, signature = token.split(".", 1)
            expected = _b64(
                hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError("token signature invalid")
            payload = json.loads(_unb64(body))
            if int(payload["exp"]) <= int(time.time()) or store.is_revoked(str(payload["jti"])):
                raise ValueError("token expired or revoked")
            return payload
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid token") from exc
