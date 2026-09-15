"""Router tests for customers, orders and segments (Supabase is faked)."""

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from main import app
from tests.fakes import ORG_HEADERS, ORG_ID, TIMESTAMP, fake_db, fake_query

client = TestClient(app)

HIGH_VALUE_SPEC = {"operator": "AND", "conditions": [{"field": "monetary", "op": "gte", "value": 5000}]}


def customer_row(**overrides) -> dict:
    return {
        "id": str(uuid4()),
        "first_name": "Aarav",
        "last_name": "Patel",
        "phone": "+919000000001",
        "email": "aarav@example.com",
        "city": "Mumbai",
        "channel_pref": "whatsapp",
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
        **overrides,
    }


def segment_row(**overrides) -> dict:
    return {
        "id": str(uuid4()),
        "name": "High value",
        "description": "Spent > 5000",
        "filter_spec": HIGH_VALUE_SPEC,
        "nl_query": None,
        "customer_count": 5,
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
        **overrides,
    }


# ── Customers ────────────────────────────────────────────────────────────────

def test_list_customers_flattens_score_and_scopes_to_org():
    score = {"recency_days": 10, "frequency": 3, "monetary": 4500.0, "rfm_score": 85.0, "churn_risk": "low"}
    customers = fake_query(([customer_row(score=[score])], 1))
    with patch("routers.customers.supabase", fake_db(customers=customers)):
        resp = client.get("/api/v1/customers?page=1&limit=10", headers=ORG_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["data"][0]["score"]["churn_risk"] == "low"
    customers.eq.assert_any_call("org_id", ORG_ID)


def test_list_customers_strips_filter_syntax_from_search():
    customers = fake_query(([], 0))
    with patch("routers.customers.supabase", fake_db(customers=customers)):
        resp = client.get("/api/v1/customers?search=a,org_id.neq.x", headers=ORG_HEADERS)

    assert resp.status_code == 200
    or_filter = customers.or_.call_args.args[0]
    assert "a org_id.neq.x" in or_filter
    assert or_filter.count(",") == 3  # only the four intended clauses


def test_get_customer_not_found():
    with patch("routers.customers.supabase", fake_db(customers=fake_query([]))):
        resp = client.get(f"/api/v1/customers/{uuid4()}", headers=ORG_HEADERS)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Customer not found"


def test_create_customer_stamps_org_and_triggers_scoring():
    new_row = customer_row(first_name="Neha", phone="+919876543211", channel_pref="sms", email=None)
    customers = fake_query([], [new_row])  # phone uniqueness check, then insert
    with patch("routers.customers.supabase", fake_db(customers=customers)), \
         patch("tasks.score_customers.score_single_customer.delay") as score, \
         patch("services.customer_embedder.safe_embed_async") as embed:
        resp = client.post(
            "/api/v1/customers",
            json={"first_name": "Neha", "phone": "+919876543211", "channel_pref": "sms"},
            headers=ORG_HEADERS,
        )

    assert resp.status_code == 201
    assert resp.json()["id"] == new_row["id"]
    assert customers.insert.call_args.args[0]["org_id"] == ORG_ID
    score.assert_called_once_with(new_row["id"])
    embed.assert_called_once_with([new_row["id"]])


def test_create_customer_rejects_duplicate_phone():
    customers = fake_query([{"id": str(uuid4())}])
    with patch("routers.customers.supabase", fake_db(customers=customers)):
        resp = client.post(
            "/api/v1/customers",
            json={"first_name": "Neha", "phone": "+919876543211"},
            headers=ORG_HEADERS,
        )

    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]
    customers.insert.assert_not_called()


def test_create_customer_requires_an_organization():
    resp = client.post("/api/v1/customers", json={"first_name": "Neha"})
    assert resp.status_code == 400


# ── Orders ───────────────────────────────────────────────────────────────────

def test_create_order_customer_not_found():
    order = {
        "customer_id": str(uuid4()),
        "order_date": "2026-06-12T12:00:00Z",
        "amount": 2500.0,
        "category": "haircare",
        "product_name": "Argan Oil Shampoo",
    }
    with patch("routers.orders.supabase", fake_db(customers=fake_query([]))):
        resp = client.post("/api/v1/orders", json=order, headers=ORG_HEADERS)

    assert resp.status_code == 400
    assert "does not exist" in resp.json()["detail"]


# ── Segments ─────────────────────────────────────────────────────────────────

def test_list_segments():
    with patch("routers.segments.supabase", fake_db(segments=fake_query([segment_row()]))):
        resp = client.get("/api/v1/segments", headers=ORG_HEADERS)

    assert resp.status_code == 200
    assert [s["name"] for s in resp.json()] == ["High value"]


def test_create_segment_counts_matching_customers():
    segments = fake_query([segment_row()])
    with patch("services.segment_executor.supabase", fake_db(customers=fake_query(([], 5)))), \
         patch("routers.segments.supabase", fake_db(segments=segments)):
        resp = client.post(
            "/api/v1/segments",
            json={"name": "High value", "description": "Spent > 5000", "filter_spec": HIGH_VALUE_SPEC},
            headers=ORG_HEADERS,
        )

    assert resp.status_code == 201
    assert resp.json()["customer_count"] == 5
    assert segments.insert.call_args.args[0]["customer_count"] == 5


def test_nl_to_segment_returns_count_and_preview():
    matches = [
        {"first_name": "Priya", "last_name": "Sharma", "score": []},
        {"first_name": "Rahul", "last_name": "Mehta", "score": []},
    ]
    with patch("services.ai_engine.nl_to_segment", return_value=HIGH_VALUE_SPEC), \
         patch("services.segment_executor.supabase", fake_db(customers=fake_query((matches, 2)))):
        resp = client.post(
            "/api/v1/segments/nl2segment",
            json={"query": "customers who spent over 5000 rupees"},
            headers=ORG_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_count"] == 2
    assert body["preview"] == ["Priya Sharma", "Rahul Mehta"]
