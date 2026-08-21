"""Enterprise WeChat delivery contract tests."""

from __future__ import annotations

import importlib

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def webhook_module(monkeypatch):
    monkeypatch.setenv(
        "ALERT_WEBHOOK_URL",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only",
    )
    return importlib.import_module("monitoring.wechat_webhook.server")


def test_wechat_response_requires_business_success(webhook_module):
    response = httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
    assert webhook_module._require_wechat_success(response)["errcode"] == 0


@pytest.mark.parametrize(
    "response,match",
    [
        (httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid webhook"}), "93000"),
        (httpx.Response(200, json={"errmsg": "missing code"}), "None"),
        (httpx.Response(200, json=[]), "non-object"),
        (httpx.Response(200, text="not-json"), "invalid JSON"),
    ],
)
def test_wechat_response_rejects_business_and_protocol_errors(webhook_module, response, match):
    with pytest.raises(webhook_module.WeChatResponseError, match=match):
        webhook_module._require_wechat_success(response)


def test_webhook_returns_502_when_wechat_rejects_message(monkeypatch, webhook_module):
    upstream = httpx.Response(
        200,
        request=httpx.Request("POST", "https://qyapi.weixin.qq.com/webhook"),
        json={"errcode": 93000, "errmsg": "invalid webhook"},
    )

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return upstream

    monkeypatch.setattr(webhook_module.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "RagServiceDown", "severity": "critical"},
                "annotations": {"summary": "test"},
            }
        ],
    }

    response = TestClient(webhook_module.app).post("/webhook", json=payload)

    assert response.status_code == 502
    assert response.json()["detail"] == "upstream rejected message"


def test_webhook_rejects_non_object_payload(webhook_module):
    response = TestClient(webhook_module.app).post("/webhook", json=[])
    assert response.status_code == 400
