"""Local user store and token contract tests."""

from __future__ import annotations

import pytest


def test_scrypt_passwords_round_trip_and_do_not_store_plaintext():
    from enterprise_rag.auth.users import hash_password, verify_password

    encoded = hash_password("correct horse")
    assert encoded.startswith("scrypt$")
    assert "correct horse" not in encoded
    assert verify_password("correct horse", encoded)
    assert not verify_password("wrong", encoded)


def test_token_expiry_and_revocation(tmp_path):
    from enterprise_rag.auth.users import TokenManager, UserStore

    store = UserStore(tmp_path / "auth.sqlite3")
    user = store.create_user("alice", "secret", department="hr")
    manager = TokenManager(secret="test-secret", ttl_seconds=300)
    token = manager.issue(user)
    claims = manager.verify(token, store=store)
    assert claims["sub"] == user["id"]
    store.revoke_token(claims["jti"], claims["exp"])
    with pytest.raises(ValueError):
        manager.verify(token, store=store)
