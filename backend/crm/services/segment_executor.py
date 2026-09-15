"""
Segment Executor — runs a filter_spec against Supabase.

Two execution strategies:
  1. PostgREST native filters (fast path, for flat AND-only specs)
  2. Client-side evaluation over a paginated fetch (for nested AND/OR specs)

execute_filter_spec returns (customer_count, preview_names);
get_segment_customer_ids returns every matching customer id.
"""

from __future__ import annotations

import logging
from typing import Any

from config import supabase

logger = logging.getLogger(__name__)

# ── Field mapping ────────────────────────────────────────────────────────────
# Score fields live on customer_scores, customer fields on customers.

SCORE_FIELDS = {"monetary", "recency_days", "frequency", "churn_risk", "top_category", "rfm_score"}
CUSTOMER_FIELDS = {"city", "channel_pref"}

ALL_VALID_FIELDS = SCORE_FIELDS | CUSTOMER_FIELDS

# filter_spec operator -> PostgREST operator
OP_TO_POSTGREST: dict[str, str] = {
    "eq": "eq",
    "neq": "neq",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    "in": "in",
    "not_in": "not.in",
    "contains": "ilike",
}

VALID_OPS = set(OP_TO_POSTGREST)

# PostgREST caps responses at 1000 rows.
PAGE_SIZE = 1000


def _org(query, org_id: str | None):
    """Apply the tenant filter to a customers query (no-op when org_id is None)."""
    return query.eq("org_id", org_id) if org_id else query


def _preview_names(rows: list[dict]) -> list[str]:
    return [f"{row['first_name']} {row.get('last_name') or ''}".strip() for row in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Execution engine: PostgREST path (fast, for flat AND-only specs)
# ═══════════════════════════════════════════════════════════════════════════════

def _is_flat_and_spec(spec: dict) -> bool:
    """Check if this spec is a simple flat AND with no nesting."""
    if spec.get("operator", "AND") != "AND":
        return False
    for cond in spec.get("conditions", []):
        if "operator" in cond and "conditions" in cond:
            return False
    return True


def _apply_postgrest_filter(query, field: str, op: str, value: Any, table_ref: str | None):
    """Apply a single PostgREST filter to a query builder."""
    postgrest_op = OP_TO_POSTGREST.get(op)
    if postgrest_op is None:
        raise ValueError(f"Unsupported operator for PostgREST path: {op}")

    if op == "contains":
        value = f"*{value}*"

    if op in ("in", "not_in"):
        # PostgREST expects a tuple-like string for IN: (val1,val2,val3)
        value = "(" + ",".join(str(v) for v in value) + ")"

    column = f"{table_ref}.{field}" if table_ref else field
    return query.filter(column, postgrest_op, value)


def _spec_needs_score(spec: dict) -> bool:
    """True if any condition (recursively) filters on a customer_scores field."""
    for cond in spec.get("conditions", []):
        if "operator" in cond and "conditions" in cond:
            if _spec_needs_score(cond):
                return True
        elif cond.get("field") in SCORE_FIELDS:
            return True
    return False


def _execute_via_postgrest(spec: dict, org_id: str | None = None) -> tuple[int, list[str]]:
    """Execute a flat AND-only filter spec using PostgREST native filters."""
    # Only require a score (inner join) when the filter actually uses score
    # fields. A city/channel-only filter must still match brand-new customers
    # who have no orders yet (and therefore no customer_scores row).
    join = "!inner" if _spec_needs_score(spec) else ""
    query = supabase.table("customers").select(
        f"id, first_name, last_name, score:customer_scores{join}(*)",
        count="exact",
    )
    query = _org(query, org_id)

    for cond in spec.get("conditions", []):
        field = cond["field"]
        table_ref = "score" if field in SCORE_FIELDS else None
        query = _apply_postgrest_filter(query, field, cond["op"], cond["value"], table_ref)

    # Limit to first 5 for preview
    res = query.limit(5).execute()

    total = res.count if res.count is not None else len(res.data)
    return total, _preview_names(res.data or [])


# ═══════════════════════════════════════════════════════════════════════════════
# Execution engine: client-side path (for nested AND/OR specs)
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_flat_rows(org_id: str | None) -> list[dict]:
    """
    Fetch every customer in the org (paginated) with its score fields flattened
    onto the row. Suitable for datasets up to ~50k customers.
    """
    all_data: list[dict] = []
    offset = 0
    while True:
        res = (
            _org(
                supabase.table("customers")
                .select("id, first_name, last_name, city, channel_pref, score:customer_scores(*)"),
                org_id,
            )
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        if not res.data:
            break
        all_data.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    flat_rows = []
    for row in all_data:
        score = row.get("score")
        if isinstance(score, list):
            score = score[0] if score else {}
        elif not score:
            score = {}
        flat_rows.append({
            "id": row["id"],
            "first_name": row["first_name"],
            "last_name": row.get("last_name", ""),
            "city": row.get("city"),
            "channel_pref": row.get("channel_pref"),
            **{k: score.get(k) for k in SCORE_FIELDS},
        })
    return flat_rows


def _execute_client_side(spec: dict, org_id: str | None = None) -> tuple[int, list[str]]:
    """Evaluate a nested spec in memory against every customer in the org."""
    matched = [row for row in _fetch_flat_rows(org_id) if _evaluate_spec(spec, row)]
    return len(matched), _preview_names(matched[:5])


def _evaluate_spec(spec: dict, row: dict) -> bool:
    """Recursively evaluate a filter_spec against a single flattened row."""
    combinator = spec.get("operator", "AND")
    results = []

    for cond in spec.get("conditions", []):
        if "operator" in cond and "conditions" in cond:
            results.append(_evaluate_spec(cond, row))
            continue

        field = cond.get("field", "")
        op = cond.get("op", "eq")
        expected = cond.get("value")
        actual = row.get(field)

        if actual is None:
            results.append(False)
            continue

        results.append(_compare(actual, op, expected))

    if not results:
        return True

    if combinator == "AND":
        return all(results)
    elif combinator == "OR":
        return any(results)
    return False


def _compare(actual: Any, op: str, expected: Any) -> bool:
    """Compare a single value using the given operator."""
    try:
        if op == "eq":
            return actual == expected
        elif op == "neq":
            return actual != expected
        elif op == "gt":
            return float(actual) > float(expected)
        elif op == "gte":
            return float(actual) >= float(expected)
        elif op == "lt":
            return float(actual) < float(expected)
        elif op == "lte":
            return float(actual) <= float(expected)
        elif op == "in":
            return actual in expected
        elif op == "not_in":
            return actual not in expected
        elif op == "contains":
            return str(expected).lower() in str(actual).lower()
    except (TypeError, ValueError):
        return False
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def execute_filter_spec(spec: dict, org_id: str | None = None) -> tuple[int, list[str]]:
    """
    Run a filter_spec against Supabase and return (count, preview_names),
    scoped to org_id when provided.
    """
    if not spec or not spec.get("conditions"):
        # Empty spec matches everyone (in the org)
        res = _org(
            supabase.table("customers").select("id, first_name, last_name", count="exact"),
            org_id,
        ).limit(5).execute()
        total = res.count if res.count is not None else len(res.data)
        return total, _preview_names(res.data or [])

    # Validate all fields before execution
    _validate_spec_fields(spec)

    if _is_flat_and_spec(spec):
        logger.info("Executing segment via PostgREST native filters (fast path).")
        return _execute_via_postgrest(spec, org_id)
    logger.info("Executing segment client-side (nested spec).")
    return _execute_client_side(spec, org_id)


def _validate_spec_fields(spec: dict) -> None:
    """Recursively validate that all fields and operators in the spec are known."""
    for cond in spec.get("conditions", []):
        if "operator" in cond and "conditions" in cond:
            _validate_spec_fields(cond)
            continue
        field = cond.get("field", "")
        if field not in ALL_VALID_FIELDS:
            raise ValueError(f"Unknown filter field: '{field}'. Valid fields: {sorted(ALL_VALID_FIELDS)}")
        op = cond.get("op", "")
        if op not in VALID_OPS:
            raise ValueError(f"Unknown operator: '{op}'. Valid operators: {sorted(VALID_OPS)}")


def get_segment_customer_ids(spec: dict, org_id: str | None = None) -> list[str]:
    """
    Execute the filter spec and return the full list of matching customer IDs,
    scoped to org_id. Used by the campaign launcher to resolve segment → customers.
    """
    # Semantic segments carry an explicit id list (from RAG search).
    if spec and spec.get("static_ids"):
        ids = spec["static_ids"]
        if not org_id:
            return ids
        # Keep only ids that belong to this org
        kept: list[str] = []
        for i in range(0, len(ids), 200):
            chunk = ids[i : i + 200]
            res = supabase.table("customers").select("id").eq("org_id", org_id).in_("id", chunk).execute()
            kept.extend(r["id"] for r in (res.data or []))
        return kept

    if not spec or not spec.get("conditions"):
        # Empty spec: all customers (in the org)
        all_ids: list[str] = []
        offset = 0
        while True:
            res = (
                _org(supabase.table("customers").select("id"), org_id)
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
            )
            if not res.data:
                break
            all_ids.extend(row["id"] for row in res.data)
            if len(res.data) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return all_ids

    _validate_spec_fields(spec)

    if _is_flat_and_spec(spec):
        return _get_ids_postgrest(spec, org_id)
    return [row["id"] for row in _fetch_flat_rows(org_id) if _evaluate_spec(spec, row)]


def _get_ids_postgrest(spec: dict, org_id: str | None = None) -> list[str]:
    """Get all matching customer IDs via PostgREST for flat AND specs."""
    all_ids: list[str] = []
    offset = 0

    needs_score = _spec_needs_score(spec)
    select_str = "id, score:customer_scores!inner(customer_id)" if needs_score else "id"
    while True:
        query = _org(supabase.table("customers").select(select_str), org_id)
        for cond in spec.get("conditions", []):
            field = cond["field"]
            table_ref = "score" if field in SCORE_FIELDS else None
            query = _apply_postgrest_filter(query, field, cond["op"], cond["value"], table_ref)

        res = query.range(offset, offset + PAGE_SIZE - 1).execute()
        if not res.data:
            break
        all_ids.extend(row["id"] for row in res.data)
        if len(res.data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return all_ids
