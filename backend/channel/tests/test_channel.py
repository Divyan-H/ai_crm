"""Tests for the channel gateway: HTTP API, sender guards, and receipt reporting."""

from unittest.mock import patch

from fastapi.testclient import TestClient

import senders
from main import app
from tasks.deliver import deliver_message

client = TestClient(app)


def send_payload(**overrides) -> dict:
    return {
        "communication_id": "c-1",
        "campaign_id": "camp-1",
        "customer_id": "cust-1",
        "channel": "email",
        "recipient_email": "priya@example.com",
        "subject": "Hello",
        "message": "Hi Priya",
        "idempotency_key": "camp-1_cust-1_attempt_1",
        **overrides,
    }


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "channel"}


def test_send_accepts_and_enqueues_delivery():
    with patch("routers.send.deliver_message.delay") as delay:
        resp = client.post("/send", json=send_payload())

    assert resp.status_code == 202
    assert resp.json() == {"accepted": True, "communication_id": "c-1"}
    assert delay.call_args.args[0]["recipient_email"] == "priya@example.com"


def test_email_requires_smtp_configuration():
    with patch.object(senders.settings, "SMTP_HOST", None):
        ok, reason = senders.send_email("priya@example.com", "Hello", "<p>Hi</p>")

    assert not ok
    assert "not configured" in reason


def test_twilio_requires_credentials():
    with patch.object(senders.settings, "TWILIO_ACCOUNT_SID", None):
        ok, reason = senders.send_twilio("sms", "+919876543210", "Hi")

    assert not ok
    assert "not configured" in reason


def test_unsupported_channel_reports_failed_receipt():
    with patch("tasks.deliver.httpx.post") as post:
        deliver_message.run(send_payload(channel="fax"))

    receipt = post.call_args.kwargs["json"]
    assert receipt["status"] == "failed"
    assert receipt["idempotency_key"] == "camp-1_cust-1_attempt_1_failed"
    assert "unsupported channel" in receipt["failure_reason"]
