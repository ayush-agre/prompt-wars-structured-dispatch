# Supabase database migration

`schema.sql` expects the Supabase `vector` extension to be installed already.
It resolves the extension schema instead of assuming `public` or `extensions`,
creates a 768-dimension HNSW cosine index, and restricts retrieval to the
authenticated user's `owner_id` through both the RPC predicate and table RLS.

Apply it only through a Supabase migration or a PostgreSQL session with
`ON_ERROR_STOP=1`. Validate it in a disposable project before production:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/schema.sql
```

The migration intentionally does not grant the match RPC or table privileges to
`service_role`. If a trusted backend needs cross-user administration, implement a
separate audited administrative path with explicit tenant parameters rather than
weakening the owner-scoped policies.
