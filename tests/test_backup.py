"""Offline backup creation, verification, retention, and restore tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from scripts.backup import BackupError, create_backup, restore_backup, verify_backup


def _sqlite(path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE state(value TEXT NOT NULL)")
        connection.execute("INSERT INTO state(value) VALUES(?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _runtime_data(root, value: str = "original"):
    data = root / "data"
    _sqlite(data / "kb_manifest.sqlite3", value)
    _sqlite(data / "answer_cache.sqlite3", "cache")
    _sqlite(data / "kb_data" / "chroma.sqlite3", "chroma")
    (data / "kb_data" / "index.bin").write_bytes(b"hnsw-data")
    (data / "background.json").write_text('{"enabled":true}', encoding="utf-8")
    staging = data / "upload_staging"
    staging.mkdir()
    (staging / "partial.tmp").write_text("incomplete", encoding="utf-8")
    return data


def _read_value(path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute("SELECT value FROM state").fetchone()[0])
    finally:
        connection.close()


def test_create_and_verify_backup(tmp_path):
    data = _runtime_data(tmp_path)
    backup = create_backup(
        data_dir=data,
        backup_root=tmp_path / "backups",
        confirm_stopped=True,
    )

    result = verify_backup(backup)

    assert result["ok"] is True
    assert result["file_count"] == 5
    assert not (backup / "data" / "upload_staging").exists()


def test_backup_refuses_unconfirmed_live_copy(tmp_path):
    data = _runtime_data(tmp_path)

    with pytest.raises(BackupError, match="confirm-stopped"):
        create_backup(data_dir=data, backup_root=tmp_path / "backups")


def test_backup_reports_invalid_sqlite_cleanly(tmp_path):
    data = tmp_path / "data"
    (data / "kb_data").mkdir(parents=True)
    (data / "kb_manifest.sqlite3").write_text("not a database", encoding="utf-8")

    with pytest.raises(BackupError, match="quick_check failed"):
        create_backup(
            data_dir=data,
            backup_root=tmp_path / "backups",
            confirm_stopped=True,
        )


def test_verify_detects_tampering(tmp_path):
    data = _runtime_data(tmp_path)
    backup = create_backup(
        data_dir=data,
        backup_root=tmp_path / "backups",
        confirm_stopped=True,
    )
    (backup / "data" / "kb_data" / "index.bin").write_bytes(b"corrupt")

    with pytest.raises(BackupError, match="mismatch"):
        verify_backup(backup)


def test_retention_keeps_latest_completed_backups(tmp_path):
    data = _runtime_data(tmp_path)
    root = tmp_path / "backups"
    start = datetime(2026, 8, 17, tzinfo=timezone.utc)
    for offset in range(3):
        create_backup(
            data_dir=data,
            backup_root=root,
            keep=2,
            confirm_stopped=True,
            now=start + timedelta(seconds=offset),
        )

    completed = sorted(path.name for path in root.glob("kb_*") if path.is_dir())
    assert completed == ["kb_20260817_080001", "kb_20260817_080002"]


def test_restore_preserves_previous_data_as_rollback(tmp_path):
    data = _runtime_data(tmp_path, value="backup-value")
    backup = create_backup(
        data_dir=data,
        backup_root=tmp_path / "backups",
        confirm_stopped=True,
    )
    data.rename(tmp_path / "discarded-source")
    current = _runtime_data(tmp_path, value="current-value")

    rollback = restore_backup(
        backup_dir=backup,
        data_dir=current,
        confirm_stopped=True,
        confirm_replace=True,
        now=datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert _read_value(current / "kb_manifest.sqlite3") == "backup-value"
    assert rollback is not None
    assert _read_value(rollback / "kb_manifest.sqlite3") == "current-value"
