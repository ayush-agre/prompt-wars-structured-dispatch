"""Offline contract checks for the Supabase pgvector migration."""

from __future__ import annotations

from pathlib import Path

SCHEMA = (Path(__file__).parents[2] / "database" / "schema.sql").read_text(
    encoding="utf-8"
)


def test_schema_resolves_pgvector_and_builds_owner_scoped_vector_search() -> None:
    """Keep extension resolution, vector storage, index, and owner filtering intact."""
    assert "DO $migration$" in SCHEMA
    assert "e.extname = 'vector'" in SCHEMA
    assert "to_regprocedure" in SCHEMA
    assert "vector_norm" in SCHEMA
    assert "CREATE TABLE IF NOT EXISTS public.intake_records" in SCHEMA
    assert "owner_id            uuid NOT NULL" in SCHEMA
    assert "REFERENCES auth.users(id)" in SCHEMA
    assert "vector(768)" in SCHEMA
    assert "USING hnsw" in SCHEMA
    assert "vector_cosine_ops" in SCHEMA
    assert "CREATE OR REPLACE FUNCTION public.match_intake_records" in SCHEMA
    assert "SECURITY INVOKER" in SCHEMA
    assert "SET search_path = pg_catalog" in SCHEMA
    assert "SET row_security = on" in SCHEMA
    assert "r.owner_id = (SELECT auth.uid())" in SCHEMA


def test_schema_enforces_rls_validation_and_least_privilege_rpc_grants() -> None:
    """Reject accidental anonymous/service RPC access and unbounded searches."""
    assert "ENABLE ROW LEVEL SECURITY" in SCHEMA
    assert "FORCE ROW LEVEL SECURITY" in SCHEMA
    assert "TO authenticated" in SCHEMA
    assert "auth.uid()) = owner_id" in SCHEMA
    assert "match_count > 100" in SCHEMA
    assert "match_threshold NOT BETWEEN -1.0 AND 1.0" in SCHEMA
    assert "REVOKE ALL ON FUNCTION" in SCHEMA
    assert "FROM PUBLIC, anon, authenticated, service_role" in SCHEMA
    assert "GRANT EXECUTE ON FUNCTION" in SCHEMA
    assert "TO authenticated" in SCHEMA
    assert "GRANT EXECUTE ON FUNCTION public.match_intake_records" in SCHEMA
    assert "REVOKE ALL ON TABLE public.intake_records FROM PUBLIC, anon" in SCHEMA
    assert "TO service_role" not in SCHEMA
