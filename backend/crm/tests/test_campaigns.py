"""Router tests for campaigns and delivery receipts (Supabase and Celery are faked)."""

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from main import app
from tests.fakes import ORG_HEADERS, ORG_ID, TIMESTAMP, fake_db, fake_query

client = TestClient(app)

RUNNING_TOTALS = {"status": "running", "total_sent": 10, "total_delivered": 5, "total_failed": 0}


def campaign_row(**overrides) -> dict:
    return {
        "id": str(uuid4()),
        "name": "Diwali Promo",
        "segment_id": str(uuid4()),
        "channel": "whatsapp",
        "message_template": "Hey {first_name}!",
        "personalized": True,
        "status": "draft",
        "created_by": "marketer",
        "total_sent": 0,
        "total_delivered": 0,
        "total_opened": 0,
        "total_clicked": 0,
        "total_failed": 0,
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
        **overrides,
    }


def communication_row(**overrides) -> dict:
    return {
        "id": str(uuid4()),
        "campaign_id": str(uuid4()),
        "customer_id": str(uuid4()),
        "channel": "email",
        "status": "sent",
        "delivered_at": None,
        "opened_at": None,
        "clicked_at": None,
        "failed_at": None,
        **overrides,
    }


def receipt(comm: dict, status: str, **extra) -> dict:
    return {
        "communication_id": comm["id"],
        "campaign_id": comm["campaign_id"],
        "idempotency_key": f"key_{status}",
        "status": status,
        "timestamp": "2026-06-12T12:00:00Z",
        **extra,
    }


# ── Campaigns ────────────────────────────────────────────────────────────────

def test_list_campaigns():
    campaigns = fake_query([campaign_row(name="Diwali campaign")])
    with patch("routers.campaigns.supabase", fake_db(campaigns=campaigns)):
        resp = client.get("/api/v1/campaigns", headers=ORG_HEADERS)

    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["Diwali campaign"]


def test_create_campaign_saves_draft_for_org():
    created = campaign_row()
    campaigns = fake_query([created])
    db = fake_db(segments=fake_query([{"id": created["segment_id"]}]), campaigns=campaigns)
    with patch("routers.campaigns.supabase", db):
        resp = client.post(
            "/api/v1/campaigns",
            json={
                "name": "Diwali Promo",
                "segment_id": created["segment_id"],
                "channel": "whatsapp",
                "message_template": "Hey {first_name}!",
            },
            headers=ORG_HEADERS,
        )

    assert resp.status_code == 201
    assert resp.json()["id"] == created["id"]
    payload = campaigns.insert.call_args.args[0]
    assert payload["org_id"] == ORG_ID
    assert payload["status"] == "draft"


def test_create_campaign_rejects_unknown_segment():
    campaigns = fake_query()
    db = fake_db(segments=fake_query([]), campaigns=campaigns)
    with patch("routers.campaigns.supabase", db):
        resp = client.post(
            "/api/v1/campaigns",
            json={
                "name": "Diwali Promo",
                "segment_id": str(uuid4()),
                "channel": "whatsapp",
                "message_template": "Hey {first_name}!",
            },
            headers=ORG_HEADERS,
        )

    assert resp.status_code == 400
    campaigns.insert.assert_not_called()


def test_launch_campaign_enqueues_task():
    camp = campaign_row()
    campaigns = fake_query([camp], [])  # lookup, then status update
    with patch("routers.campaigns.supabase", fake_db(campaigns=campaigns)), \
         patch("tasks.campaigns.launch_campaign_task.delay") as launch:
        resp = client.post(f"/api/v1/campaigns/{camp['id']}/launch", headers=ORG_HEADERS)

    assert resp.status_code == 202
    assert resp.json()["status"] == "running"
    launch.assert_called_once_with(camp["id"])


def test_launch_rejects_non_draft_campaign():
    camp = campaign_row(status="running")
    with patch("routers.campaigns.supabase", fake_db(campaigns=fake_query([camp]))), \
         patch("tasks.campaigns.launch_campaign_task.delay") as launch:
        resp = client.post(f"/api/v1/campaigns/{camp['id']}/launch", headers=ORG_HEADERS)

    assert resp.status_code == 400
    launch.assert_not_called()


# ── Receipts ─────────────────────────────────────────────────────────────────

def receipt_db(comm: dict):
    communications = fake_query([comm], [])  # lookup, then status update
    db = fake_db(communications=communications, campaigns=fake_query([RUNNING_TOTALS]))
    return db, communications


def test_delivered_receipt_updates_status_and_counter():
    comm = communication_row()
    db, communications = receipt_db(comm)
    with patch("services.delivery_events.supabase", db):
        resp = client.post("/api/v1/receipts", json=receipt(comm, "delivered"))

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert communications.update.call_args.args[0]["status"] == "delivered"
    db.rpc.assert_called_once_with(
        "increment_campaign_counter",
        {"camp_id": comm["campaign_id"], "counter_name": "total_delivered"},
    )


def test_failed_receipt_keeps_failure_reason():
    comm = communication_row()
    db, communications = receipt_db(comm)
    with patch("services.delivery_events.supabase", db):
        resp = client.post(
            "/api/v1/receipts",
            json=receipt(comm, "failed", failure_reason="smtp error: mailbox full"),
        )

    assert resp.status_code == 200
    update = communications.update.call_args.args[0]
    assert update["status"] == "failed"
    assert update["failure_reason"] == "smtp error: mailbox full"


def test_duplicate_receipt_is_ignored():
    comm = communication_row(status="delivered", delivered_at=TIMESTAMP)
    db = fake_db(communications=fake_query([comm]))
    with patch("services.delivery_events.supabase", db):
        resp = client.post("/api/v1/receipts", json=receipt(comm, "delivered"))

    assert resp.status_code == 200
    assert resp.json()["status"] == "duplicate"
    db.rpc.assert_not_called()


def test_receipt_for_unknown_communication():
    comm = communication_row()
    with patch("services.delivery_events.supabase", fake_db(communications=fake_query([]))):
        resp = client.post("/api/v1/receipts", json=receipt(comm, "delivered"))

    assert resp.status_code == 404
