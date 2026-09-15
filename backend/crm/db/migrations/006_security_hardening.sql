-- ============================================================
-- Migration 006 — Security hardening
-- Paste into the Supabase SQL Editor and Run. Idempotent.
--
-- The backend connects with the service_role key, which bypasses RLS, so
-- enabling RLS (with no policies) changes nothing for the API while blocking
-- direct reads/writes through Supabase's public anon key.
-- ============================================================

-- 1. Row Level Security on every application table.
ALTER TABLE organizations     ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers         ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders            ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_scores   ENABLE ROW LEVEL SECURITY;
ALTER TABLE segments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns         ENABLE ROW LEVEL SECURITY;
ALTER TABLE communications    ENABLE ROW LEVEL SECURITY;
ALTER TABLE imports           ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_feedback ENABLE ROW LEVEL SECURITY;

-- 2. Drop the unused dynamic-SQL RPC from early versions of schema.sql
--    (SECURITY DEFINER + a raw WHERE clause = SQL injection surface).
DROP FUNCTION IF EXISTS execute_segment_filter(TEXT, TEXT[]);

-- 3. RPCs are backend-only.
REVOKE EXECUTE ON FUNCTION increment_campaign_counter(UUID, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION match_customers(TEXT, UUID, INT)       FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION similar_customers(UUID, UUID, INT)     FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION increment_campaign_counter(UUID, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION match_customers(TEXT, UUID, INT)       TO service_role;
GRANT EXECUTE ON FUNCTION similar_customers(UUID, UUID, INT)     TO service_role;
