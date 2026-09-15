"""
Infrastructure connectivity checks.
Each test skips when its service isn't configured/reachable, so the unit
suite stays green on machines (and CI runners) without Redis or Supabase.
"""

import pytest
import redis

from config import settings, supabase


def test_redis_connection():
    """Verify that Redis is running and reachable."""
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    try:
        assert client.ping() is True
    except (redis.ConnectionError, redis.TimeoutError):
        pytest.skip(f"Redis not reachable at {settings.REDIS_URL}")


def test_supabase_connection():
    """Verify that Supabase can be contacted if credentials are provided."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        pytest.skip("Supabase environment variables are missing; skipping connectivity test.")

    res = supabase.table("customers").select("id").limit(1).execute()
    assert hasattr(res, "data")
