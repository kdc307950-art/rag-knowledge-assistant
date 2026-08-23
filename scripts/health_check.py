"""External, dependency-free health check for the single-process deployment.

This script intentionally runs outside FastAPI. A dead process must still be
observable, so the task scheduler can invoke it every few minutes without
depending on an in-process background thread.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.config import (  # noqa: E402
    BACKUP_DIR,
    HEALTH_SLOW_LLM_MS,
    HEALTH_SLOW_MIN_COUNT,
    HEALTH_SLOW_REQUEST_MS,
    LOG_DIR,
    RUNTIME_DATA_DIR,
)


UTC = timezone.utc
DEFAULT_WINDOW_MINUTES = 15
DEFAULT_BACKUP_MAX_AGE_DAYS = 7
DEFAULT_MIN_FREE_GB = 1.0
DEFAULT_ERROR_RATE = 0.05
DEFAULT_MIN_REQUESTS = 10
DEFAULT_AUTH_FAILURE_MIN = 2
DEFAULT_SLOW_REQUEST_MS = HEALTH_SLOW_REQUEST_MS
DEFAULT_SLOW_LLM_MS = HEALTH_SLOW_LLM_MS
DEFAULT_SLOW_MIN_COUNT = HEALTH_SLOW_MIN_COUNT
COOLDOWN_SECONDS = 30 * 60


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _read_jsonl(path: Path, max_bytes: int = 2 * 1024 * 1024) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - max_bytes))
            raw = handle.read()
    except OSError:
        return []
    if len(raw) and raw[:1] not in {b"{", b"["}:
        raw = raw[raw.find(b"\n") + 1 :]
    records: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def check_ready(
    url: str,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout: float = 5.0,
) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})

    def failure(http_status: int, payload: Any = None) -> dict[str, Any]:
        # Keep the stable ``http_503`` detail used by existing automation, but
        # retain the probe's machine-readable reason for actionable alerts.
        result: dict[str, Any] = {
            "name": "ready",
            "status": "critical",
            "detail": f"http_{http_status}",
        }
        if isinstance(payload, dict):
            probe_status = payload.get("status")
            probe_detail = payload.get("detail")
            if isinstance(probe_status, str) and probe_status:
                result["ready_status"] = probe_status
            if isinstance(probe_detail, str) and probe_detail:
                result["ready_detail"] = probe_detail
        return result

    try:
        with opener(request, timeout=timeout) as response:
            status_value = getattr(response, "status", None)
            if status_value is None:
                status_value = response.getcode()
            status = int(status_value)
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        payload = None
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (AttributeError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        return failure(exc.code, payload)
    except Exception as exc:
        return {"name": "ready", "status": "critical", "detail": f"network_error:{type(exc).__name__}"}
    if status != 200:
        return failure(status, payload)
    if not isinstance(payload, dict) or payload.get("ready") is not True:
        result = {"name": "ready", "status": "critical", "detail": "not_ready"}
        if isinstance(payload, dict) and isinstance(payload.get("status"), str):
            result["ready_status"] = payload["status"]
        return result
    return {"name": "ready", "status": "ok", "detail": str(payload.get("status", "ready"))}


def latest_verified_backup(backup_dir: Path) -> tuple[datetime | None, str | None]:
    ledger = backup_dir / "verification_ledger.jsonl"
    candidates: list[tuple[datetime, str]] = []
    for record in _read_jsonl(ledger):
        if record.get("ok") is not True:
            continue
        timestamp = _parse_time(record.get("timestamp"))
        name = record.get("backup")
        if timestamp is None or not isinstance(name, str):
            continue
        path = backup_dir / name
        try:
            if path.is_dir() and path.resolve().parent == backup_dir.resolve():
                candidates.append((timestamp, name))
        except OSError:
            continue
    return max(candidates, default=(None, None))


def check_backup_freshness(
    backup_dir: Path,
    *,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_BACKUP_MAX_AGE_DAYS,
) -> dict[str, Any]:
    timestamp, name = latest_verified_backup(backup_dir)
    if timestamp is None:
        return {"name": "backup", "status": "critical", "detail": "no_verified_backup"}
    age = (now or _now()) - timestamp
    if age > timedelta(days=max_age_days):
        return {"name": "backup", "status": "critical", "detail": "stale", "backup": name, "age_seconds": int(age.total_seconds())}
    return {"name": "backup", "status": "ok", "detail": "fresh", "backup": name, "age_seconds": int(age.total_seconds())}


def check_disk(
    data_dir: Path,
    *,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    usage: Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes]], Any] = shutil.disk_usage,
) -> dict[str, Any]:
    try:
        free = int(usage(data_dir).free)
    except OSError as exc:
        return {"name": "disk", "status": "critical", "detail": type(exc).__name__}
    threshold = int(min_free_gb * 1024**3)
    if free < threshold:
        return {"name": "disk", "status": "critical", "detail": "low_free_space", "free_bytes": free}
    return {"name": "disk", "status": "ok", "detail": "enough_free_space", "free_bytes": free}


def access_error_rate(
    log_dir: Path,
    *,
    now: datetime | None = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    min_requests: int = DEFAULT_MIN_REQUESTS,
    threshold: float = DEFAULT_ERROR_RATE,
) -> dict[str, Any]:
    cutoff = (now or _now()) - timedelta(minutes=window_minutes)
    records: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("access.log*")):
        records.extend(_read_jsonl(path))
    recent = [record for record in records if (_parse_time(record.get("timestamp")) or datetime.min.replace(tzinfo=UTC)) >= cutoff]
    recent = [
        record
        for record in recent
        if record.get("route") != "/metrics"
        and str(record.get("outcome", "")).lower() != "interrupted"
    ]
    total = len(recent)
    errors = 0
    for record in recent:
        try:
            status_code = int(record.get("status_code", 200) or 200)
        except (TypeError, ValueError):
            status_code = 200
        if str(record.get("outcome", "")).lower() == "error" or status_code >= 500:
            errors += 1
    rate = errors / total if total else 0.0
    if total < min_requests:
        return {"name": "error_rate", "status": "ok", "detail": "insufficient_sample", "requests": total, "errors": errors, "rate": rate}
    # The contract is error rate < threshold. Equality is therefore already a
    # breach; keep a zero-error window healthy even when threshold is set to 0.
    status = "critical" if errors > 0 and rate >= threshold else "ok"
    return {"name": "error_rate", "status": status, "detail": "threshold_exceeded" if status == "critical" else "within_threshold", "requests": total, "errors": errors, "rate": rate}


def _duration_ms(record: dict[str, Any], name: str) -> float | None:
    """Read a finite millisecond value from the current or legacy log shape."""

    phases = record.get("phases")
    value = phases.get(name) if isinstance(phases, dict) else record.get(name)
    if value is None and name == "total_ms":
        # Older access records only exposed duration_ms at the top level.
        value = record.get("duration_ms")
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 and parsed != float("inf") and parsed != float("-inf") else None


def check_slow_requests(
    log_dir: Path,
    *,
    now: datetime | None = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    request_threshold_ms: float = DEFAULT_SLOW_REQUEST_MS,
    llm_threshold_ms: float = DEFAULT_SLOW_LLM_MS,
    min_count: int = DEFAULT_SLOW_MIN_COUNT,
) -> dict[str, Any]:
    """Detect repeated slow completed requests without treating SSE lifetime as latency.

    A normal request is slow when its total duration exceeds ``request_threshold_ms``;
    an SSE request is evaluated by its ``llm_ms`` stage because ``total_ms`` is the
    client-held stream lifetime. Interrupted streams are excluded from both counts.
    """

    cutoff = (now or _now()) - timedelta(minutes=max(0, int(window_minutes)))
    request_threshold = max(0.0, float(request_threshold_ms))
    llm_threshold = max(0.0, float(llm_threshold_ms))
    threshold_count = max(1, int(min_count))
    total = 0
    normal_total = 0
    sse_total = 0
    slow = 0
    slow_requests = 0
    slow_sse = 0
    slow_llm = 0

    records: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("access.log*")):
        records.extend(_read_jsonl(path))
    for record in records:
        timestamp = _parse_time(record.get("timestamp"))
        if timestamp is None or timestamp < cutoff:
            continue
        if record.get("route") == "/metrics":
            continue
        outcome = str(record.get("outcome", "")).strip().lower()
        if outcome == "interrupted":
            continue

        route_type_value = record.get("route_type")
        if route_type_value is None and record.get("sse_terminal") is not None:
            # Keep compatibility with pre-route_type access records that still
            # carry the middleware's SSE terminal marker.
            route_type_value = "sse"
        route_type = str(route_type_value or "request").strip().lower()
        if route_type not in {"request", "sse"}:
            route_type = "request"
        total_ms = _duration_ms(record, "total_ms")
        llm_ms = _duration_ms(record, "llm_ms")
        total += 1
        if route_type == "sse":
            sse_total += 1
            # Never use the full SSE connection lifetime as a slow-request signal.
            is_slow = llm_ms is not None and llm_ms >= llm_threshold
            if is_slow:
                slow += 1
                slow_sse += 1
                slow_llm += 1
            continue

        normal_total += 1
        slow_total = total_ms is not None and total_ms >= request_threshold
        slow_stage = llm_ms is not None and llm_ms >= llm_threshold
        if slow_total or slow_stage:
            slow += 1
            if slow_total:
                slow_requests += 1
            if slow_stage:
                slow_llm += 1

    status = "critical" if slow >= threshold_count else "ok"
    if status == "critical":
        detail = "threshold_exceeded"
    elif total < threshold_count:
        detail = "insufficient_sample"
    else:
        detail = "within_threshold"
    return {
        "name": "slow_requests",
        "status": status,
        "detail": detail,
        "window_minutes": max(0, int(window_minutes)),
        "requests": total,
        "normal_requests": normal_total,
        "sse_requests": sse_total,
        "slow": slow,
        "slow_requests": slow_requests,
        "slow_sse": slow_sse,
        "slow_llm": slow_llm,
        "request_threshold_ms": request_threshold,
        "llm_threshold_ms": llm_threshold,
        "min_count": threshold_count,
    }


def model_auth_failure_count(
    log_dir: Path,
    *,
    now: datetime | None = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> int:
    """Count recent model-auth failures without reading raw request content."""

    cutoff = (now or _now()) - timedelta(minutes=window_minutes)
    count = 0
    markers = (
        "authentication_error",
        "invalid_api_key",
        "unauthorized",
        "鉴权",
        "api key",
        "api_key",
        "欠费",
        "额度不足",
        "insufficient_quota",
        "quota_exceeded",
        "arrears",
    )
    seen_request_ids: set[str] = set()
    seen_legacy_events: set[tuple[str, str]] = set()
    for path in sorted(log_dir.glob("error.log*")):
        for record in _read_jsonl(path):
            timestamp = _parse_time(record.get("timestamp"))
            if timestamp is None or timestamp < cutoff:
                continue
            code = str(record.get("error_code", "")).strip().lower()
            message = str(record.get("message", "")).lower()
            matched_marker = next((marker for marker in markers if marker in message), None)
            if code != "authentication" and matched_marker is None:
                continue
            request_id = record.get("request_id")
            if isinstance(request_id, str) and request_id:
                # A single request can produce several wrapped log records
                # (_call_llm -> stream adapter -> service -> SSE producer).
                if request_id in seen_request_ids:
                    continue
                seen_request_ids.add(request_id)
                count += 1
                continue
            # Older logs predate request IDs. The JSON formatter has one-second
            # timestamp precision, so collapse same-second duplicates while
            # preserving failures from separate seconds.
            event_key = (str(record.get("timestamp", "")), code or matched_marker or "authentication")
            if event_key in seen_legacy_events:
                continue
            seen_legacy_events.add(event_key)
            count += 1
    return count


def check_model_auth_failures(
    log_dir: Path,
    *,
    now: datetime | None = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    min_failures: int = DEFAULT_AUTH_FAILURE_MIN,
) -> dict[str, Any]:
    failures = model_auth_failure_count(log_dir, now=now, window_minutes=window_minutes)
    threshold = max(1, int(min_failures))
    if failures < threshold:
        return {
            "name": "model_auth",
            "status": "ok",
            "detail": "insufficient_sample",
            "failures": failures,
            "threshold": threshold,
        }
    return {
        "name": "model_auth",
        "status": "critical",
        "detail": "persistent_authentication_failures",
        "failures": failures,
        "threshold": threshold,
    }


def run_checks(
    *,
    ready_url: str,
    backup_dir: Path = BACKUP_DIR,
    data_dir: Path = RUNTIME_DATA_DIR,
    log_dir: Path = LOG_DIR,
    now: datetime | None = None,
    opener: Callable[..., Any] = urlopen,
    backup_max_age_days: int = DEFAULT_BACKUP_MAX_AGE_DAYS,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    error_rate_threshold: float = DEFAULT_ERROR_RATE,
    error_min_requests: int = DEFAULT_MIN_REQUESTS,
    auth_failure_min: int = DEFAULT_AUTH_FAILURE_MIN,
    slow_request_ms: float = DEFAULT_SLOW_REQUEST_MS,
    slow_llm_ms: float = DEFAULT_SLOW_LLM_MS,
    slow_min_count: int = DEFAULT_SLOW_MIN_COUNT,
) -> list[dict[str, Any]]:
    return [
        check_ready(ready_url, opener=opener),
        check_backup_freshness(backup_dir, now=now, max_age_days=backup_max_age_days),
        check_disk(data_dir, min_free_gb=min_free_gb),
        access_error_rate(
            log_dir,
            now=now,
            threshold=error_rate_threshold,
            min_requests=error_min_requests,
        ),
        check_model_auth_failures(
            log_dir,
            now=now,
            min_failures=auth_failure_min,
        ),
        check_slow_requests(
            log_dir,
            now=now,
            request_threshold_ms=slow_request_ms,
            llm_threshold_ms=slow_llm_ms,
            min_count=slow_min_count,
        ),
    ]


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _alert_id(name: str, first_active_at: str) -> str:
    """Return an id stable for one incident, even as check details change."""

    seed = f"rag-health|{name}|{first_active_at}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:16]


def _read_alert_state(path: Path) -> dict[str, Any]:
    """Read the current alert state, tolerating the pre-per-check schema."""

    if not path.is_file():
        return {"checks": {}}
    state: dict[str, Any] | None = None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            state = parsed
    except (OSError, json.JSONDecodeError):
        state = None
    if state is None:
        records = _read_jsonl(path, max_bytes=256 * 1024)
        state = records[-1] if records and isinstance(records[-1], dict) else None
    if state is None:
        return {"checks": {}}
    checks = state.get("checks")
    if isinstance(checks, dict):
        return {"checks": checks}

    # Migrate the old shared timestamp in memory. It is intentionally not
    # treated as a notification for checks that were not active in that run.
    previous_active = state.get("active")
    previous_time = state.get("updated_at")
    migrated: dict[str, Any] = {}
    if isinstance(previous_active, list) and isinstance(previous_time, str):
        for name in previous_active:
            if isinstance(name, str):
                migrated[name] = {
                    "first_active_at": previous_time,
                    "last_notified_at": previous_time,
                    "alert_id": _alert_id(name, previous_time),
                }
    return {"checks": migrated}


def _append_alert(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _send_webhook(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    opener: Callable[..., Any] = urlopen,
    attempts: int = 2,
) -> None:
    """Best-effort delivery; create a fresh Request for every retry."""

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    for _attempt in range(max(1, attempts)):
        request = Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with opener(request, timeout=5):
                return
        except Exception:
            continue


def notify(
    checks: list[dict[str, Any]],
    *,
    state_path: Path,
    alerts_path: Path,
    webhook_url: str = "",
    now: datetime | None = None,
    opener: Callable[..., Any] = urlopen,
) -> None:
    now = now or _now()
    previous = _read_alert_state(state_path)
    previous_checks = previous.get("checks", {})
    active = {check["name"]: check for check in checks if check.get("status") == "critical"}
    next_checks: dict[str, Any] = {}
    emitted: list[dict[str, Any]] = []

    for name, check in active.items():
        prior = previous_checks.get(name) if isinstance(previous_checks, dict) else None
        if not isinstance(prior, dict):
            first_active_at = now.isoformat()
            alert_id = _alert_id(name, first_active_at)
            last_notified_at = None
        else:
            first_active_at = str(prior.get("first_active_at") or now.isoformat())
            alert_id = str(prior.get("alert_id") or _alert_id(name, first_active_at))
            last_notified_at = _parse_time(prior.get("last_notified_at"))

        should_notify = (
            last_notified_at is None
            or (now - last_notified_at).total_seconds() >= COOLDOWN_SECONDS
        )
        if should_notify:
            event = {
                "timestamp": now.isoformat(),
                "event": "alert",
                "check": name,
                "alert_id": alert_id,
                "detail": check.get("detail"),
            }
            _append_alert(alerts_path, event)
            emitted.append({**event, "check_result": check})
            last_notified_at = now
        next_checks[name] = {
            "first_active_at": first_active_at,
            "last_notified_at": last_notified_at.isoformat() if last_notified_at else None,
            "alert_id": alert_id,
        }

    recovered: list[dict[str, Any]] = []
    for name, prior in previous_checks.items() if isinstance(previous_checks, dict) else []:
        if name in active or not isinstance(prior, dict):
            continue
        event = {
            "timestamp": now.isoformat(),
            "event": "recovery",
            "check": name,
            "alert_id": prior.get("alert_id"),
        }
        _append_alert(alerts_path, event)
        recovered.append(event)

    # State is a single JSON document written atomically. Each check has its
    # own cooldown timestamp, so a noisy disk check cannot suppress a new
    # readiness incident in the same scheduler run.
    _atomic_write(state_path, {"updated_at": now.isoformat(), "checks": next_checks})

    if webhook_url and (emitted or recovered):
        webhook_payload = {
            "timestamp": now.isoformat(),
            "alerts": emitted,
            "recoveries": recovered,
        }
        _send_webhook(webhook_url, webhook_payload, opener=opener)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("RAG_READY_URL", "http://127.0.0.1:8000/api/ready"))
    parser.add_argument("--state", type=Path, default=Path(os.getenv("RAG_ALERT_STATE", str(LOG_DIR / "alerts_state.json"))))
    parser.add_argument("--alerts", type=Path, default=Path(os.getenv("RAG_ALERT_LOG", str(LOG_DIR / "alerts.log"))))
    args = parser.parse_args(argv)
    checks = run_checks(
        ready_url=args.url,
        backup_max_age_days=max(1, int(os.getenv("HEALTH_BACKUP_MAX_AGE_DAYS", DEFAULT_BACKUP_MAX_AGE_DAYS))),
        min_free_gb=max(0.0, float(os.getenv("HEALTH_MIN_FREE_GB", DEFAULT_MIN_FREE_GB))),
        error_rate_threshold=max(0.0, float(os.getenv("HEALTH_ERROR_RATE_THRESHOLD", DEFAULT_ERROR_RATE))),
        error_min_requests=max(1, int(os.getenv("HEALTH_ERROR_MIN_REQUESTS", DEFAULT_MIN_REQUESTS))),
        auth_failure_min=max(1, int(os.getenv("HEALTH_AUTH_FAILURE_MIN", DEFAULT_AUTH_FAILURE_MIN))),
        slow_request_ms=max(0.0, float(os.getenv("HEALTH_SLOW_REQUEST_MS", DEFAULT_SLOW_REQUEST_MS))),
        slow_llm_ms=max(0.0, float(os.getenv("HEALTH_SLOW_LLM_MS", DEFAULT_SLOW_LLM_MS))),
        slow_min_count=max(1, int(os.getenv("HEALTH_SLOW_MIN_COUNT", DEFAULT_SLOW_MIN_COUNT))),
    )
    notify(checks, state_path=args.state, alerts_path=args.alerts, webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""))
    print(json.dumps({"ok": all(item.get("status") != "critical" for item in checks), "checks": checks}, ensure_ascii=False))
    return 0 if all(item.get("status") != "critical" for item in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
