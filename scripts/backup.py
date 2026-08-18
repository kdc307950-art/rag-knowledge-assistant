"""Create, verify, and restore offline runtime-data snapshots.

Chroma/HNSW does not provide an application-level hot snapshot contract here.
The CLI therefore requires an explicit confirmation that FastAPI and other
writers are stopped before create or restore operations.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from typing import Iterable
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.config import BACKUP_DIR, LOG_DIR, RUNTIME_DATA_DIR  # noqa: E402
from enterprise_rag.utils.logger import log_audit_event, setup_logger  # noqa: E402


FORMAT_VERSION = 1
MANIFEST_NAME = "backup_manifest.json"
VERIFICATION_LEDGER_NAME = "verification_ledger.jsonl"
REQUIRED_ENTRIES = ("kb_data", "kb_manifest.sqlite3")
SQLITE_PATHS = (
    "kb_manifest.sqlite3",
    "answer_cache.sqlite3",
    "kb_data/chroma.sqlite3",
)
REPLACE_RETRIES = 8
REPLACE_RETRY_DELAY_SECONDS = 0.25


class BackupError(RuntimeError):
    """Raised when a backup cannot be proven complete and restorable."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_quick_check(path: Path) -> str:
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise BackupError(f"Cannot open SQLite database {path}: {exc}") from exc
    try:
        try:
            row = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as exc:
            raise BackupError(f"SQLite quick_check failed for {path}: {exc}") from exc
        result = str(row[0]) if row else "missing result"
        if result.lower() != "ok":
            raise BackupError(f"SQLite quick_check failed for {path}: {result}")
        return result
    finally:
        connection.close()


def _check_required(data_dir: Path) -> None:
    missing = [name for name in REQUIRED_ENTRIES if not (data_dir / name).exists()]
    if missing:
        raise BackupError("Missing required runtime data: " + ", ".join(missing))


def _replace_with_retry(source: Path, target: Path) -> None:
    """Replace a directory while tolerating short Windows scanner locks."""

    last_error: OSError | None = None
    for attempt in range(REPLACE_RETRIES):
        try:
            source.replace(target)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < REPLACE_RETRIES:
                time.sleep(REPLACE_RETRY_DELAY_SECONDS)
    assert last_error is not None
    raise last_error


def _check_sqlite_files(data_dir: Path) -> dict[str, str]:
    results: dict[str, str] = {}
    for relative in SQLITE_PATHS:
        path = data_dir / Path(relative)
        if path.is_file():
            results[relative] = _sqlite_quick_check(path)
    return results


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    # Staged uploads are incomplete work, not committed runtime state.
    return {"upload_staging"} if "upload_staging" in names else set()


def _file_records(data_dir: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(item for item in data_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(data_dir).as_posix()
        records.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return records


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _append_log(backup_root: Path, payload: dict) -> None:
    backup_root.mkdir(parents=True, exist_ok=True)
    with (backup_root / "backup.log").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _record_verification(backup_dir: Path, result: dict[str, object], reason: str) -> None:
    """Record a successful hash/SQLite verification for external health checks."""
    root = backup_dir.parent
    _append_log(
        root,
        {
            "timestamp": _utc_now().isoformat(),
            "action": "verify",
            "reason": reason,
            "backup": backup_dir.name,
            "file_count": result.get("file_count"),
            "total_bytes": result.get("total_bytes"),
        },
    )
    # Keep a separate, append-only ledger so backup.log can remain a human-
    # readable activity log while health checks only parse verification facts.
    ledger = root / VERIFICATION_LEDGER_NAME
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": _utc_now().isoformat(),
                    "backup": backup_dir.name,
                    "ok": True,
                    "reason": reason,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )


def _next_backup_name(backup_root: Path, now: datetime) -> str:
    base = "kb_" + now.astimezone().strftime("%Y%m%d_%H%M%S")
    candidate = base
    suffix = 1
    while (backup_root / candidate).exists() or any(
        backup_root.glob(f".incomplete-{candidate}-*")
    ):
        candidate = f"{base}-{suffix:02d}"
        suffix += 1
    return candidate


def _remove_old_backups(backup_root: Path, keep: int) -> list[str]:
    if keep < 1:
        raise BackupError("Retention count must be at least 1")
    completed = sorted(
        path
        for path in backup_root.iterdir()
        if path.is_dir() and path.name.startswith("kb_") and (path / MANIFEST_NAME).is_file()
    )
    removed: list[str] = []
    for path in completed[:-keep]:
        if not _inside(path, backup_root):
            raise BackupError(f"Refusing to remove path outside backup root: {path}")
        shutil.rmtree(path)
        removed.append(path.name)
    return removed


def create_backup(
    *,
    data_dir: Path,
    backup_root: Path,
    keep: int = 7,
    confirm_stopped: bool = False,
    now: datetime | None = None,
) -> Path:
    if not confirm_stopped:
        raise BackupError("Stop FastAPI and other writers, then pass --confirm-stopped")
    data_dir = data_dir.expanduser().resolve()
    backup_root = backup_root.expanduser().resolve()
    if not data_dir.is_dir():
        raise BackupError(f"Runtime data directory does not exist: {data_dir}")
    if _inside(backup_root, data_dir) or _inside(data_dir, backup_root):
        raise BackupError("Backup root and runtime data directory must not contain each other")

    _check_required(data_dir)
    _check_sqlite_files(data_dir)
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = now or _utc_now()
    name = _next_backup_name(backup_root, timestamp)
    temporary = backup_root / f".incomplete-{name}-{uuid4().hex}"
    destination = backup_root / name

    try:
        copied_data = temporary / "data"
        shutil.copytree(data_dir, copied_data, ignore=_copy_ignore)
        _check_required(copied_data)
        sqlite_checks = _check_sqlite_files(copied_data)
        files = _file_records(copied_data)
        payload = {
            "format_version": FORMAT_VERSION,
            "created_at": timestamp.astimezone(timezone.utc).isoformat(),
            "source_data_dir": str(data_dir),
            "file_count": len(files),
            "total_bytes": sum(int(record["size"]) for record in files),
            "excluded": ["upload_staging"],
            "sqlite_checks": sqlite_checks,
            "files": files,
        }
        _write_json_atomic(temporary / MANIFEST_NAME, payload)
        temporary.replace(destination)
    except Exception:
        if temporary.exists() and _inside(temporary, backup_root):
            shutil.rmtree(temporary, ignore_errors=True)
        raise

    removed = _remove_old_backups(backup_root, keep)
    _record_verification(
        destination,
        {
            "file_count": len(files),
            "total_bytes": payload["total_bytes"],
        },
        "create",
    )
    _append_log(
        backup_root,
        {
            "timestamp": _utc_now().isoformat(),
            "action": "create",
            "backup": destination.name,
            "file_count": len(files),
            "total_bytes": payload["total_bytes"],
            "removed": removed,
        },
    )
    return destination


def _load_manifest(backup_dir: Path) -> dict:
    path = backup_dir / MANIFEST_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"Cannot read backup manifest: {path}") from exc
    if payload.get("format_version") != FORMAT_VERSION:
        raise BackupError("Unsupported backup manifest version")
    if not isinstance(payload.get("files"), list):
        raise BackupError("Backup manifest has no file inventory")
    return payload


def verify_backup(backup_dir: Path) -> dict[str, object]:
    backup_dir = backup_dir.expanduser().resolve()
    data_dir = backup_dir / "data"
    payload = _load_manifest(backup_dir)
    _check_required(data_dir)

    expected: dict[str, dict] = {}
    for record in payload["files"]:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise BackupError("Invalid file record in backup manifest")
        relative = Path(record["path"])
        target = (data_dir / relative).resolve()
        if relative.is_absolute() or not _inside(target, data_dir):
            raise BackupError(f"Unsafe file path in backup manifest: {relative}")
        expected[relative.as_posix()] = record

    actual_paths = {
        path.relative_to(data_dir).as_posix(): path
        for path in data_dir.rglob("*")
        if path.is_file()
    }
    if set(actual_paths) != set(expected):
        missing = sorted(set(expected) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(expected))
        raise BackupError(f"Backup inventory mismatch; missing={missing}, extra={extra}")

    for relative, path in actual_paths.items():
        record = expected[relative]
        if path.stat().st_size != int(record.get("size", -1)):
            raise BackupError(f"Size mismatch: {relative}")
        if _sha256(path) != record.get("sha256"):
            raise BackupError(f"SHA-256 mismatch: {relative}")

    sqlite_checks = _check_sqlite_files(data_dir)
    result = {
        "ok": True,
        "file_count": len(actual_paths),
        "total_bytes": sum(path.stat().st_size for path in actual_paths.values()),
        "sqlite_checks": sqlite_checks,
    }
    _record_verification(backup_dir, result, "verify")
    return result


def restore_backup(
    *,
    backup_dir: Path,
    data_dir: Path,
    confirm_stopped: bool = False,
    confirm_replace: bool = False,
    now: datetime | None = None,
) -> Path | None:
    if not confirm_stopped or not confirm_replace:
        raise BackupError(
            "Restore requires --confirm-stopped and --confirm-replace"
        )
    backup_dir = backup_dir.expanduser().resolve()
    data_dir = data_dir.expanduser().resolve()
    verify_backup(backup_dir)
    if _inside(backup_dir, data_dir) or _inside(data_dir, backup_dir):
        raise BackupError("Backup and restore target must not contain each other")

    data_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = data_dir.parent / f".{data_dir.name}.restore-{uuid4().hex}"
    rollback: Path | None = None
    try:
        shutil.copytree(backup_dir / "data", stage)
        if data_dir.exists():
            stamp = (now or _utc_now()).astimezone().strftime("%Y%m%d_%H%M%S")
            rollback = data_dir.parent / f"{data_dir.name}.pre-restore-{stamp}"
            if rollback.exists():
                raise BackupError(f"Rollback directory already exists: {rollback}")
            _replace_with_retry(data_dir, rollback)
        _replace_with_retry(stage, data_dir)
    except Exception:
        if stage.exists() and _inside(stage, data_dir.parent):
            shutil.rmtree(stage, ignore_errors=True)
        if rollback is not None and rollback.exists() and not data_dir.exists():
            _replace_with_retry(rollback, data_dir)
        raise
    return rollback


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create and verify an offline backup")
    create.add_argument("--data-dir", type=Path, default=RUNTIME_DATA_DIR)
    create.add_argument("--backup-dir", type=Path, default=BACKUP_DIR)
    create.add_argument("--keep", type=int, default=7)
    create.add_argument("--confirm-stopped", action="store_true")

    verify = subparsers.add_parser("verify", help="verify hashes and SQLite integrity")
    verify.add_argument("backup", type=Path)

    restore = subparsers.add_parser("restore", help="restore a verified offline backup")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--data-dir", type=Path, default=RUNTIME_DATA_DIR)
    restore.add_argument("--confirm-stopped", action="store_true")
    restore.add_argument("--confirm-replace", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    # ``LOG_DIR`` normally lives under ``RUNTIME_DATA_DIR``.  Do not open
    # those files before restore_backup renames the data directory on Windows;
    # an open handle makes the directory move fail with WinError 5.
    if args.command != "restore":
        setup_logger(log_dir=LOG_DIR)
    try:
        if args.command == "create":
            path = create_backup(
                data_dir=args.data_dir,
                backup_root=args.backup_dir,
                keep=args.keep,
                confirm_stopped=args.confirm_stopped,
            )
            log_audit_event("backup_created", backup=path.name)
            print(f"Backup created: {path}")
        elif args.command == "verify":
            result = verify_backup(args.backup)
            log_audit_event("backup_verified", backup=args.backup.name)
            print(json.dumps(result, ensure_ascii=False))
        else:
            rollback = restore_backup(
                backup_dir=args.backup,
                data_dir=args.data_dir,
                confirm_stopped=args.confirm_stopped,
                confirm_replace=args.confirm_replace,
            )
            # Re-open logging only after the restored data directory is in
            # place, then record the successful restore in the new log tree.
            setup_logger(log_dir=LOG_DIR)
            log_audit_event("backup_restored", backup=args.backup.name)
            print(f"Restore completed; rollback copy: {rollback or 'none'}")
    except (BackupError, OSError) as exc:
        print(f"Backup error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
