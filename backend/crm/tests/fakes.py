"""Test doubles for the supabase-py client."""

from __future__ import annotations

from unittest.mock import MagicMock

ORG_ID = "11111111-1111-1111-1111-111111111111"
ORG_HEADERS = {"X-Org-Id": ORG_ID}
TIMESTAMP = "2026-06-12T00:00:00Z"

_BUILDER_METHODS = (
    "select", "insert", "update", "upsert", "delete",
    "eq", "neq", "in_", "is_", "or_", "filter", "order", "range", "limit",
)


def fake_query(*results):
    """
    A chainable stand-in for a PostgREST query builder.

    Every builder method returns the same object, and each successive
    execute() returns the next result. A result is a list of rows or a
    (rows, count) tuple.
    """
    query = MagicMock()
    for name in _BUILDER_METHODS:
        getattr(query, name).return_value = query
    query.execute.side_effect = [
        MagicMock(data=r[0], count=r[1]) if isinstance(r, tuple) else MagicMock(data=r, count=None)
        for r in results
    ]
    return query


def fake_db(**tables):
    """A stand-in supabase client whose table(name) returns the matching fake query."""
    db = MagicMock()
    db.table.side_effect = lambda name: tables[name]
    return db
