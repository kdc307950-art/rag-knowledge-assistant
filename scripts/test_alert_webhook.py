"""验证企业微信告警适配器的 firing 和 recovery 通知。

用法：
  # 使用默认地址（本地 docker compose monitoring profile）
  python scripts/test_alert_webhook.py

  # 指定地址
  python scripts/test_alert_webhook.py http://localhost:5001/webhook

期望结果：企业微信机器人收到两条消息——一条红色告警、一条绿色恢复。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5001/webhook"
NOW = datetime.now(timezone.utc).isoformat()


def _post(label: str, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            print(f"  [{label}] HTTP {resp.status} → {body}")
            if not body.get("sent"):
                print(f"  ⚠  sent=false: {body.get('reason', '—')} （ALERT_WEBHOOK_URL 未配置？）")
    except urllib.error.HTTPError as exc:
        print(f"  [{label}] HTTP {exc.code}: {exc.read().decode()[:200]}")
        sys.exit(1)
    except Exception as exc:
        print(f"  [{label}] 连接失败: {exc}")
        sys.exit(1)


_BASE_ALERT = {
    "status": "firing",
    "labels": {
        "alertname": "RagServiceDown",
        "severity": "critical",
        "job": "rag",
        "instance": "backend:8000",
    },
    "annotations": {
        "summary": "RAG 服务不可达（测试）",
        "description": "这是一条由 test_alert_webhook.py 发送的测试告警，不代表真实故障。",
    },
    "startsAt": NOW,
    "endsAt": "0001-01-01T00:00:00Z",
    "generatorURL": "http://prometheus:9090",
    "fingerprint": "test-fingerprint-000",
}

FIRING_PAYLOAD = {
    "version": "4",
    "groupKey": "test-group",
    "status": "firing",
    "receiver": "wechat",
    "groupLabels": {"alertname": "RagServiceDown"},
    "commonLabels": _BASE_ALERT["labels"],
    "commonAnnotations": _BASE_ALERT["annotations"],
    "externalURL": "http://alertmanager:9093",
    "alerts": [_BASE_ALERT],
}

RECOVERY_PAYLOAD = {
    **FIRING_PAYLOAD,
    "status": "resolved",
    "alerts": [
        {
            **_BASE_ALERT,
            "status": "resolved",
            "endsAt": NOW,
        }
    ],
}

print(f"=== 告警 Webhook 测试 ===")
print(f"目标: {URL}\n")

print("1/2  发送 FIRING 告警 ...")
_post("firing", FIRING_PAYLOAD)

print("\n2/2  发送 RECOVERY 恢复通知 ...")
_post("recovery", RECOVERY_PAYLOAD)

print("\n✓ 完成。请确认企业微信收到红色告警和绿色恢复两条消息。")
