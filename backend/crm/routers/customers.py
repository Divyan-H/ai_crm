"""
CRM router — Customers.

Endpoints:
  GET    /customers          — paginated list with scores
  GET    /customers/{id}     — full profile + AI 360 summary
  POST   /customers          — create single customer
  DELETE /customers          — delete every customer in the org
  DELETE /customers/{id}     — delete one customer

Bulk file imports live in routers/imports.py (AI-assisted mapping).
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from config import redis_client, supabase
from deps import OrgContext, get_org
from models.customer import (
    CustomerCreate,
    CustomerDetailedRead,
    CustomerListResponse,
    CustomerRead,
)
from services.ai_engine import generate_customer_summary

logger = logging.getLogger(__name__)

router = APIRouter()

# Characters with syntactic meaning inside a PostgREST or=(...) filter string.
POSTGREST_RESERVED = re.compile(r"[,()]")


@router.get("/customers", response_model=CustomerListResponse)
async def list_customers(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query(None),
    churn_risk: str = Query(None),
    sort_by: str = Query("spend"),
    sort_dir: str = Query("desc"),
    org: OrgContext = Depends(get_org),
):
    """Return paginated customers joined with their RFM scores."""
    start = (page - 1) * limit
    end = start + limit - 1

    # Churn risk filtering requires an inner join to discard customers without scores.
    # Otherwise, perform a left join to include customers without order history yet.
    if churn_risk:
        select_str = "*, score:customer_scores!inner(*)"
    else:
        select_str = "*, score:customer_scores(*)"

    try:
        query = supabase.table("customers").select(select_str, count="exact")
        query = org.scope(query)

        # Strip reserved characters so user input can't inject extra filter clauses.
        search_clean = POSTGREST_RESERVED.sub(" ", search).strip() if search else ""
        if search_clean:
            query = query.or_(
                f"first_name.ilike.%{search_clean}%,"
                f"last_name.ilike.%{search_clean}%,"
                f"email.ilike.%{search_clean}%,"
                f"phone.ilike.%{search_clean}%"
            )

        if churn_risk:
            query = query.eq("score.churn_risk", churn_risk)

        # Apply sorting
        if sort_by == "spend":
            query = query.order("score(monetary)", desc=(sort_dir == "desc"))
        elif sort_by == "recency":
            query = query.order("score(recency_days)", desc=(sort_dir == "desc"))
        elif sort_by == "frequency":
            query = query.order("score(frequency)", desc=(sort_dir == "desc"))
        elif sort_by == "rfm_score":
            query = query.order("score(rfm_score)", desc=(sort_dir == "desc"))
        else:
            # Default sorting: creation date
            query = query.order("created_at", desc=(sort_dir == "desc"))

        query = query.range(start, end)
        res = query.execute()

        data = []
        for row in res.data:
            # PostgREST inner/left join yields list or None/dict
            score_val = row.get("score")
            if isinstance(score_val, list):
                row["score"] = score_val[0] if score_val else None
            elif not score_val:
                row["score"] = None
            data.append(row)

        total = res.count if res.count is not None else len(data)

        return {
            "data": data,
            "total": total,
            "page": page,
            "limit": limit,
        }

    except Exception as e:
        logger.error(f"Error listing customers: {e}")
        raise HTTPException(status_code=500, detail=f"Database query failed: {e}")


@router.get("/customers/{customer_id}", response_model=CustomerDetailedRead)
async def get_customer(customer_id: UUID, org: OrgContext = Depends(get_org)):
    """Full customer profile with AI 360 summary (cached 1 h in Redis)."""
    # 1. Fetch customer info + score (scoped to the caller's org)
    cust_res = org.scope(
        supabase.table("customers")
        .select("*, score:customer_scores(*)")
        .eq("id", str(customer_id))
    ).execute()
    if not cust_res.data:
        raise HTTPException(status_code=404, detail="Customer not found")

    customer = cust_res.data[0]
    score_val = customer.get("score")
    if isinstance(score_val, list):
        customer["score"] = score_val[0] if score_val else None
    elif not score_val:
        customer["score"] = None

    # 2. Fetch order history
    orders_res = (
        supabase.table("orders")
        .select("*")
        .eq("customer_id", str(customer_id))
        .order("order_date", desc=True)
        .execute()
    )
    customer["orders"] = orders_res.data or []

    # 3. Fetch campaigns this customer was part of
    comms_res = (
        supabase.table("communications")
        .select("*, campaign:campaigns(*)")
        .eq("customer_id", str(customer_id))
        .execute()
    )
    campaigns = []
    seen_campaign_ids = set()
    for comm in (comms_res.data or []):
        camp = comm.get("campaign")
        if isinstance(camp, list):
            camp = camp[0] if camp else None
        if camp and camp["id"] not in seen_campaign_ids:
            seen_campaign_ids.add(camp["id"])
            campaigns.append(camp)
    customer["campaigns"] = campaigns

    # 4. Handle AI 360 summary caching (cached 1 hour in Redis)
    cache_key = f"customer:summary:{customer_id}"
    ai_summary = None

    try:
        ai_summary = redis_client.get(cache_key)
    except Exception as redis_err:
        logger.warning(f"Redis cache read failed: {redis_err}")

    if not ai_summary:
        try:
            # Generate new summary using AI engine
            ai_summary = generate_customer_summary(customer, customer["orders"], campaigns)
            # Try caching in Redis for 1 hour (3600 seconds)
            try:
                redis_client.setex(cache_key, 3600, ai_summary)
            except Exception as redis_err:
                logger.warning(f"Redis cache write failed: {redis_err}")
        except Exception as ai_err:
            logger.error(f"AI summary generation failed: {ai_err}")
            ai_summary = "AI summary temporarily unavailable."

    customer["ai_summary"] = ai_summary
    return customer


@router.delete("/customers")
async def delete_all_customers(org: OrgContext = Depends(get_org)):
    """
    Remove ALL customers in the organization (and, via cascade, their orders,
    scores, and communications). Destructive — the UI guards this with a
    confirmation. Admin must act within a specific org.
    """
    org_id = org.require_org()
    try:
        res = supabase.table("customers").delete().eq("org_id", org_id).execute()
        return {"deleted_all": True, "count": len(res.data or [])}
    except Exception as e:
        logger.error(f"Error deleting all customers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/customers/{customer_id}")
async def delete_customer(customer_id: UUID, org: OrgContext = Depends(get_org)):
    """Delete a single customer (org-scoped). Cascades to their orders/scores/messages."""
    try:
        found = org.scope(
            supabase.table("customers").select("id").eq("id", str(customer_id))
        ).execute()
        if not found.data:
            raise HTTPException(status_code=404, detail="Customer not found.")
        org.scope(
            supabase.table("customers").delete().eq("id", str(customer_id))
        ).execute()
        return {"deleted": True, "id": str(customer_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting customer {customer_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/customers", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
async def create_customer(body: CustomerCreate, org: OrgContext = Depends(get_org)):
    """Create a single customer and trigger RFM scoring."""
    payload = org.stamp(body.model_dump(exclude_unset=True))

    try:
        # Check uniqueness constraints proactively to return clean 400s (within org)
        if payload.get("phone"):
            dup_phone = org.scope(
                supabase.table("customers").select("id").eq("phone", payload["phone"])
            ).execute()
            if dup_phone.data:
                raise HTTPException(
                    status_code=400,
                    detail=f"Customer with phone '{payload['phone']}' already exists.",
                )

        if payload.get("email"):
            dup_email = org.scope(
                supabase.table("customers").select("id").eq("email", payload["email"])
            ).execute()
            if dup_email.data:
                raise HTTPException(
                    status_code=400,
                    detail=f"Customer with email '{payload['email']}' already exists.",
                )

        # Insert customer
        res = supabase.table("customers").insert(payload).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Database insert returned empty result.")

        new_cust = res.data[0]

        # Trigger RFM scoring asynchronously
        from tasks.score_customers import score_single_customer
        score_single_customer.delay(new_cust["id"])

        # Build the semantic embedding in the background (non-blocking)
        from services.customer_embedder import safe_embed_async
        safe_embed_async([new_cust["id"]])

        return new_cust

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating customer: {e}")
        raise HTTPException(status_code=500, detail=str(e))
