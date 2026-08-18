"""Pure tests for the external watchdog; no FastAPI process is required."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from urllib.error import HTTPError

from scripts import health_check


class _Response:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def getcode(self):
        return self.status


def test_check_ready_requires_200_and_ready_payload():
    assert health_check.check_ready(
        "http://unit.test/ready",
        opener=lambda *args, **kwargs: _Response(200, {"ready": True, "status": "ready"}),
    )["status"] == "ok"
    not_ready = health_check.check_ready(
        "http://unit.test/ready",
        opener=lambda *args, **kwargs: _Response(503, {"ready": False}),
    )
    assert not_ready["status"] == "critical"
    assert not_ready["detail"] == "http_503"


def test_check_ready_preserves_probe_reason_on_http_error():
    body = json.dumps(
        {"ready": False, "status": "manifest_mismatch", "detail": "counts differ"}
    ).encode("utf-8")

    def opener(*args, **kwargs):
        raise HTTPError(
            "http://unit.test/ready",
            503,
            "Service Unavailable",
            {},
            BytesIO(body),
        )

    result = health_check.check_ready("http://unit.test/ready", opener=opener)

    assert result["status"] == "critical"
    assert result["detail"] == "http_503"
    assert result["ready_status"] == "manifest_mismatch"
    assert result["ready_detail"] == "counts differ"


def test_backup_freshness_uses_verification_ledger(tmp_path):
    backup_dir = tmp_path / "backups"
    backup = backup_dir / "kb_20260817_120000"
    backup.mkdir(parents=True)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    (backup_dir / "verification_ledger.jsonl").write_text(
        json.dumps({"timestamp": now.isoformat(), "backup": backup.name, "ok": True}) + "\n",
        encoding="utf-8",
    )
    result = health_check.check_backup_freshness(backup_dir, now=now + timedelta(days=1))
    assert result["status"] == "ok"
    assert result["backup"] == backup.name


def test_access_error_rate_ignores_metrics_and_needs_minimum_sample(tmp_path):
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    records = [
        {"timestamp": now.isoformat(), "route": "/metrics", "status_code": 500, "outcome": "error"},
        *[
            {"timestamp": now.isoformat(), "route": "/api/chat", "status_code": 500, "outcome": "error"}
            for _ in range(10)
        ],
    ]
    (tmp_path / "access.log").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    result = health_check.access_error_rate(tmp_path, now=now, min_requests=10)
    assert result["status"] == "critical"
    assert result["requests"] == 10


def test_access_error_rate_treats_threshold_equality_as_breach(tmp_path):
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    records = [
        {
            "timestamp": now.isoformat(),
            "route": "/api/chat",
            "status_code": 500 if index == 0 else 200,
            "outcome": "error" if index == 0 else "ok",
        }
        for index in range(20)
    ]
    (tmp_path / "access.log").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    result = health_check.access_error_rate(
        tmp_path,
        now=now,
        min_requests=10,
        threshold=0.05,
    )

    assert result["rate"] == 0.05
    assert result["status"] == "critical"


def test_check_disk_uses_filesystem_free_bytes(tmp_path):
    result = health_check.check_disk(
        tmp_path,
        min_free_gb=1,
        usage=lambda _path: type("Usage", (), {"free": 2 * 1024**3})(),
    )
    assert result["status"] == "ok"


def test_model_auth_failure_check_requires_persistent_recent_failures(tmp_path):
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    records = [
        {
            "timestamp": (now - timedelta(minutes=2)).isoformat(),
            "level": "ERROR",
            "message": "LLM request failed: invalid_api_key",
        },
        {
            "timestamp": (now - timedelta(minutes=1)).isoformat(),
            "level": "ERROR",
            "message": "模型服务鉴权失败",
        },
        {
            "timestamp": (now - timedelta(hours=2)).isoformat(),
            "level": "ERROR",
            "message": "invalid_api_key",
        },
    ]
    (tmp_path / "error.log").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    result = health_check.check_model_auth_failures(tmp_path, now=now, min_failures=2)
    assert result["status"] == "critical"
    assert result["failures"] == 2


def test_model_auth_failure_check_detects_quota_exhaustion(tmp_path):
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    (tmp_path / "error.log").write_text(
        json.dumps(
            {
                "timestamp": now.isoformat(),
                "level": "ERROR",
                "message": "provider returned insufficient_quota",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = health_check.check_model_auth_failures(tmp_path, now=now, min_failures=1)
    assert result["status"] == "critical"


def test_model_auth_failure_count_deduplicates_wrapped_logs_by_request(tmp_path):
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    records = [
        {
            "timestamp": now.isoformat(),
            "request_id": "request-1",
            "message": message,
        }
        for message in ("invalid_api_key", "答案生成失败: invalid_api_key", "鉴权失败")
    ]
    records.append(
        {
            "timestamp": (now + timedelta(seconds=1)).isoformat(),
            "request_id": "request-2",
            "message": "invalid_api_key",
        }
    )
    (tmp_path / "error.log").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    assert health_check.model_auth_failure_count(tmp_path, now=now) == 2


def test_auth_audit_dedupe_is_scoped_by_client_class_and_route():
    from backend import auth

    with auth._AUTH_FAILURE_LOCK:
        auth._AUTH_FAILURE_LAST_LOGGED.clear()
    assert auth._should_audit_auth_failure("local", "/api/chat", now=100.0)
    assert not auth._should_audit_auth_failure("local", "/api/chat", now=110.0)
    assert auth._should_audit_auth_failure("lan", "/api/chat", now=110.0)
    assert auth._should_audit_auth_failure("local", "/api/health", now=110.0)


def test_notify_tracks_cooldown_per_check_and_records_recovery(tmp_path):
    state_path = tmp_path / "alerts_state.json"
    alerts_path = tmp_path / "alerts.log"
    first = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    health_check.notify(
        [
            {"name": "ready", "status": "critical", "detail": "down"},
            {"name": "disk", "status": "critical", "detail": "full"},
        ],
        state_path=state_path,
        alerts_path=alerts_path,
        now=first,
    )
    initial = [json.loads(line) for line in alerts_path.read_text(encoding="utf-8").splitlines()]
    assert [item["check"] for item in initial] == ["ready", "disk"]
    first_ids = {item["check"]: item["alert_id"] for item in initial}

    # Disk recovers while ready remains active. A new backup incident appears
    # within the same cooldown window and must not be suppressed by ready.
    health_check.notify(
        [
            {"name": "ready", "status": "critical", "detail": "still_down"},
            {"name": "backup", "status": "critical", "detail": "stale"},
        ],
        state_path=state_path,
        alerts_path=alerts_path,
        now=first + timedelta(minutes=5),
    )
    events = [json.loads(line) for line in alerts_path.read_text(encoding="utf-8").splitlines()]
    assert [(item["event"], item["check"]) for item in events] == [
        ("alert", "ready"),
        ("alert", "disk"),
        ("alert", "backup"),
        ("recovery", "disk"),
    ]
    assert events[0]["alert_id"] == first_ids["ready"]
    assert events[2]["alert_id"] != first_ids["ready"]

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(state["checks"]) == {"ready", "backup"}


def test_webhook_retry_builds_a_fresh_request_each_time(tmp_path):
    requests = []

    class _WebhookResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def opener(request, timeout):
        requests.append(request)
        if len(requests) == 1:
            raise OSError("temporary network failure")
        return _WebhookResponse()

    health_check._send_webhook(
        "http://unit.test/hook",
        {"alert_id": "abc"},
        opener=opener,
    )
    assert len(requests) == 2
    assert requests[0] is not requests[1]
    assert requests[0].data == requests[1].data
