"""验证企业微信告警适配器的 firing 和 recovery 通知。

这是手工运维脚本，不是 pytest 用例——文件名刻意不以 ``test_`` 开头，
否则 pytest 会在收集阶段就把它导入并真的发出网络请求。

用法：
  # 使用默认地址（本地 docker compose monitoring profile）
  python scripts/check_alert_webhook.py

  # 指定地址
  python scripts/check_alert_webhook.py http://localhost:5001/webhook

期望结果：企业微信机器人收到两条消息——一条红色告警、一条绿色恢复。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_URL = "http://localhost:5001/webhook"


def _post(url: str, label: str, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url,
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


def _payloads(now: str) -> tuple[dict, dict]:
    base_alert = {
        "status": "firing",
        "labels": {
            "alertname": "RagServiceDown",
            "severity": "critical",
            "job": "rag",
            "instance": "backend:8000",
        },
        "annotations": {
            "summary": "RAG 服务不可达（测试）",
            "description": "这是一条由 check_alert_webhook.py 发送的测试告警，不代表真实故障。",
        },
        "startsAt": now,
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "http://prometheus:9090",
        "fingerprint": "test-fingerprint-000",
    }
    firing = {
        "version": "4",
        "groupKey": "test-group",
        "status": "firing",
        "receiver": "wechat",
        "groupLabels": {"alertname": "RagServiceDown"},
        "commonLabels": base_alert["labels"],
        "commonAnnotations": base_alert["annotations"],
        "externalURL": "http://alertmanager:9093",
        "alerts": [base_alert],
    }
    recovery = {
        **firing,
        "status": "resolved",
        "alerts": [{**base_alert, "status": "resolved", "endsAt": now}],
    }
    return firing, recovery


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else DEFAULT_URL
    firing, recovery = _payloads(datetime.now(timezone.utc).isoformat())

    print("=== 告警 Webhook 测试 ===")
    print(f"目标: {url}\n")

    print("1/2  发送 FIRING 告警 ...")
    _post(url, "firing", firing)

    print("\n2/2  发送 RECOVERY 恢复通知 ...")
    _post(url, "recovery", recovery)

    print("\n✓ 完成。请确认企业微信收到红色告警和绿色恢复两条消息。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
