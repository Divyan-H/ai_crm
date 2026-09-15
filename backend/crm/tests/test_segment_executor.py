"""Tests for the segment executor's filter_spec validation and in-memory evaluation."""

import pytest

from services.segment_executor import _evaluate_spec, _validate_spec_fields

CUSTOMER = {
    "monetary": 6000.0,
    "recency_days": 75,
    "frequency": 2,
    "churn_risk": "high",
    "top_category": "Skincare",
    "city": "Mumbai",
    "channel_pref": "whatsapp",
    "rfm_score": 42.0,
}


def spec(operator: str, *conditions) -> dict:
    return {"operator": operator, "conditions": list(conditions)}


def cond(field: str, op: str, value) -> dict:
    return {"field": field, "op": op, "value": value}


def test_simple_gte_matches():
    assert _evaluate_spec(spec("AND", cond("monetary", "gte", 5000)), CUSTOMER)


def test_and_requires_every_condition():
    assert not _evaluate_spec(
        spec("AND", cond("monetary", "gte", 5000), cond("recency_days", "lt", 30)),
        CUSTOMER,
    )


def test_or_requires_any_condition():
    assert _evaluate_spec(
        spec("OR", cond("city", "eq", "Delhi"), cond("churn_risk", "in", ["high", "critical"])),
        CUSTOMER,
    )


def test_nested_groups():
    nested = spec(
        "AND",
        cond("channel_pref", "eq", "whatsapp"),
        spec("OR", cond("frequency", "gte", 5), cond("monetary", "gt", 5000)),
    )
    assert _evaluate_spec(nested, CUSTOMER)


def test_not_in_and_contains():
    assert _evaluate_spec(spec("AND", cond("churn_risk", "not_in", ["low"])), CUSTOMER)
    assert _evaluate_spec(spec("AND", cond("top_category", "contains", "skin")), CUSTOMER)


def test_missing_value_never_matches():
    assert not _evaluate_spec(spec("AND", cond("monetary", "gte", 0)), {**CUSTOMER, "monetary": None})


def test_empty_spec_matches_everyone():
    assert _evaluate_spec(spec("AND"), CUSTOMER)


def test_unknown_field_raises():
    with pytest.raises(ValueError, match="Unknown filter field"):
        _validate_spec_fields(spec("AND", cond("nonexistent", "eq", 1)))


def test_unknown_operator_raises():
    with pytest.raises(ValueError, match="Unknown operator"):
        _validate_spec_fields(spec("AND", spec("OR", cond("city", "like", "Mum"))))
