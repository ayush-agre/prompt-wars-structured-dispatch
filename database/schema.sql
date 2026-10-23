-- Supabase/PostgreSQL migration for owner-scoped pgvector retrieval.
--
-- This migration deliberately requires the Supabase auth schema and an already
-- installed pgvector extension. The extension is operator-managed, so its
-- schema is resolved from pg_extension rather than guessed as public or
-- extensions. Run the file with ON_ERROR_STOP=1 in a disposable project before
-- applying it to production.

DO $migration$
DECLARE
    vector_schema name;
BEGIN
    SELECT n.nspname
      INTO vector_schema
      FROM pg_catalog.pg_extension AS e
      JOIN pg_catalog.pg_namespace AS n
        ON n.oid = e.extnamespace
     WHERE e.extname = 'vector';

    IF vector_schema IS NULL THEN
        RAISE EXCEPTION
            'The pgvector extension is required; install it before running this migration';
    END IF;

    IF pg_catalog.to_regclass('auth.users') IS NULL
       OR pg_catalog.to_regprocedure('auth.uid()') IS NULL THEN
        RAISE EXCEPTION
            'Supabase auth.users and auth.uid() are required by this migration';
    END IF;

    -- vector_norm() and vector_dims() are required to reject invalid stored
    -- and query vectors. Do not silently upgrade the extension during
    -- provisioning because that is an operator-controlled step.
    IF pg_catalog.to_regprocedure(
           pg_catalog.format(
               '%I.vector_norm(%I.vector)',
               vector_schema,
               vector_schema
           )
       ) IS NULL
       OR pg_catalog.to_regprocedure(
           pg_catalog.format(
               '%I.vector_dims(%I.vector)',
               vector_schema,
               vector_schema
           )
       ) IS NULL THEN
        RAISE EXCEPTION
            'pgvector with vector_norm() and vector_dims() is required; upgrade the vector extension before running this migration';
    END IF;

    -- Fail before creating an unusable table if this installation does not
    -- expose the HNSW cosine operator class.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_opclass AS c
          JOIN pg_catalog.pg_am AS a
            ON a.oid = c.opcmethod
          JOIN pg_catalog.pg_namespace AS n
            ON n.oid = c.opcnamespace
         WHERE c.opcname = 'vector_cosine_ops'
           AND a.amname = 'hnsw'
           AND n.nspname = vector_schema
    ) THEN
        RAISE EXCEPTION
            'The installed pgvector extension does not provide an HNSW cosine operator class';
    END IF;

    EXECUTE pg_catalog.format($ddl$
        CREATE TABLE IF NOT EXISTS public.intake_records (
            id                  uuid PRIMARY KEY
                                DEFAULT pg_catalog.gen_random_uuid(),
            owner_id            uuid NOT NULL
                                REFERENCES auth.users(id)
                                ON DELETE CASCADE,
            raw_payload         text NOT NULL,
            extracted_entities  jsonb NOT NULL,
            embedding           %1$I.vector(768),
            domain_tag          varchar(64),
            status              varchar(32) NOT NULL DEFAULT 'processed',
            created_at          timestamptz NOT NULL
                                DEFAULT pg_catalog.clock_timestamp(),
            updated_at          timestamptz NOT NULL
                                DEFAULT pg_catalog.clock_timestamp(),

            -- NULL permits ingestion before embedding generation. A stored
            -- embedding must have the expected dimension, a nonzero norm, and
            -- a finite norm so cosine distance remains well-defined.
            CONSTRAINT intake_records_embedding_nonzero
                CHECK (
                    embedding IS NULL
                    OR (
                        %1$I.vector_dims(embedding) = 768
                        AND %1$I.vector_norm(embedding) > 0.0
                        AND %1$I.vector_norm(embedding)
                            < 'Infinity'::double precision
                    )
                )
        )
    $ddl$, vector_schema);

    -- CREATE TABLE IF NOT EXISTS intentionally does not alter an existing
    -- table. If an older table is missing the tenant key, fail closed rather
    -- than installing policies that could not enforce ownership. An empty
    -- legacy table can be upgraded safely; populated tables need an explicit
    -- owner backfill outside this migration.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_attribute
         WHERE attrelid = 'public.intake_records'::pg_catalog.regclass
           AND attname = 'owner_id'
           AND NOT attisdropped
    ) THEN
        IF EXISTS (SELECT 1 FROM public.intake_records) THEN
            RAISE EXCEPTION
                'public.intake_records has rows but no owner_id; backfill owners before rerunning this migration';
        END IF;
        ALTER TABLE public.intake_records
            ADD COLUMN owner_id uuid;
    END IF;

    -- These operations are idempotent for a table created by this migration,
    -- and fail rather than weakening the tenant boundary for incompatible data.
    ALTER TABLE public.intake_records
        ALTER COLUMN owner_id SET NOT NULL;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_constraint
         WHERE conrelid = 'public.intake_records'::pg_catalog.regclass
           AND conname = 'intake_records_owner_id_fkey'
    ) THEN
        ALTER TABLE public.intake_records
            ADD CONSTRAINT intake_records_owner_id_fkey
            FOREIGN KEY (owner_id)
            REFERENCES auth.users(id)
            ON DELETE CASCADE;
    END IF;

    -- Add the vector invariant to a pre-existing compatible table as well.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_constraint
         WHERE conrelid = 'public.intake_records'::pg_catalog.regclass
           AND conname = 'intake_records_embedding_nonzero'
    ) THEN
        EXECUTE pg_catalog.format($ddl$
            ALTER TABLE public.intake_records
                ADD CONSTRAINT intake_records_embedding_nonzero
                CHECK (
                    embedding IS NULL
                    OR (
                        %1$I.vector_dims(embedding) = 768
                        AND %1$I.vector_norm(embedding) > 0.0
                        AND %1$I.vector_norm(embedding)
                            < 'Infinity'::double precision
                    )
                )
        $ddl$, vector_schema);
    END IF;

    -- HNSW uses the cosine operator class from the installed extension schema.
    EXECUTE pg_catalog.format($ddl$
        CREATE INDEX IF NOT EXISTS intake_records_embedding_hnsw_idx
            ON public.intake_records
            USING hnsw (embedding %1$I.vector_cosine_ops)
            WITH (
                m = 16,
                ef_construction = 128
            )
    $ddl$, vector_schema);

    EXECUTE pg_catalog.format($ddl$
        CREATE OR REPLACE FUNCTION public.match_intake_records(
            query_embedding %1$I.vector(768),
            match_threshold double precision,
            match_count integer
        )
        RETURNS TABLE (
            id                  uuid,
            raw_payload         text,
            extracted_entities  jsonb,
            embedding           %1$I.vector(768),
            domain_tag          varchar(64),
            status              varchar(32),
            created_at          timestamptz,
            updated_at          timestamptz,
            similarity          double precision
        )
        LANGUAGE plpgsql
        STABLE
        SECURITY INVOKER
        SET search_path = pg_catalog
        SET row_security = on
        SET hnsw.ef_search = 100
        AS $function$
        DECLARE
            query_norm double precision;
        BEGIN
            -- PostgreSQL does not enforce function-argument type modifiers.
            IF query_embedding IS NULL
               OR %1$I.vector_dims(query_embedding) <> 768 THEN
                RAISE EXCEPTION USING
                    ERRCODE = '22023',
                    MESSAGE = 'query_embedding must contain exactly 768 dimensions';
            END IF;

            query_norm := %1$I.vector_norm(query_embedding);
            IF query_norm IS NULL
               OR query_norm <= 0.0
               OR query_norm >= 'Infinity'::double precision THEN
                RAISE EXCEPTION USING
                    ERRCODE = '22023',
                    MESSAGE = 'query_embedding must be a nonzero finite vector';
            END IF;

            -- Cosine similarity ranges from -1 to 1. PostgreSQL's float
            -- ordering makes NOT BETWEEN reject NaN and either infinity too.
            IF match_threshold IS NULL
               OR match_threshold NOT BETWEEN -1.0 AND 1.0 THEN
                RAISE EXCEPTION USING
                    ERRCODE = '22023',
                    MESSAGE = 'match_threshold must be a finite number between -1 and 1';
            END IF;

            -- Bound work performed by an RPC caller, even when the threshold
            -- is broad enough to match most of the owner's records.
            IF match_count IS NULL OR match_count <= 0 OR match_count > 100 THEN
                RAISE EXCEPTION USING
                    ERRCODE = '22023',
                    MESSAGE = 'match_count must be between 1 and 100';
            END IF;

            -- Keep ORDER BY as the distance operator in ascending order so
            -- PostgreSQL can use the cosine HNSW index. The explicit owner
            -- predicate is a second boundary in addition to table RLS: even
            -- if a privileged caller ever receives EXECUTE, this RPC cannot
            -- return another tenant's rows.
            RETURN QUERY
            SELECT
                r.id,
                r.raw_payload,
                r.extracted_entities,
                r.embedding,
                r.domain_tag,
                r.status,
                r.created_at,
                r.updated_at,
                1.0::double precision
                    - (r.embedding OPERATOR(%1$I.<=>) query_embedding)
                    AS similarity
            FROM public.intake_records AS r
            WHERE r.owner_id = (SELECT auth.uid())
              AND r.embedding IS NOT NULL
              AND (r.embedding OPERATOR(%1$I.<=>) query_embedding)
                    <= (1.0::double precision - match_threshold)
            ORDER BY
                r.embedding OPERATOR(%1$I.<=>) query_embedding ASC
            LIMIT match_count;
        END;
        $function$
    $ddl$, vector_schema);

    -- Function execution is intentionally limited to authenticated callers.
    -- In particular, service_role receives no RPC grant here.
    EXECUTE pg_catalog.format(
        'REVOKE ALL ON FUNCTION public.match_intake_records(%I.vector, double precision, integer) FROM PUBLIC, anon, authenticated, service_role',
        vector_schema
    );
    EXECUTE pg_catalog.format(
        'GRANT EXECUTE ON FUNCTION public.match_intake_records(%I.vector, double precision, integer) TO authenticated',
        vector_schema
    );

    -- Invoker execution resolves the vector operators/functions under the
    -- caller's privileges, so grant only schema usage to the API role.
    EXECUTE pg_catalog.format(
        'GRANT USAGE ON SCHEMA %I TO authenticated',
        vector_schema
    );
END;
$migration$;

-- Force every table access through owner policies, including access by the
-- table owner. Supabase's service_role is deliberately not granted table or
-- function access by this migration.
ALTER TABLE public.intake_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intake_records FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS intake_records_select_own ON public.intake_records;
CREATE POLICY intake_records_select_own
    ON public.intake_records
    FOR SELECT
    TO authenticated
    USING ((SELECT auth.uid()) = owner_id);

DROP POLICY IF EXISTS intake_records_insert_own ON public.intake_records;
CREATE POLICY intake_records_insert_own
    ON public.intake_records
    FOR INSERT
    TO authenticated
    WITH CHECK ((SELECT auth.uid()) = owner_id);

DROP POLICY IF EXISTS intake_records_update_own ON public.intake_records;
CREATE POLICY intake_records_update_own
    ON public.intake_records
    FOR UPDATE
    TO authenticated
    USING ((SELECT auth.uid()) = owner_id)
    WITH CHECK ((SELECT auth.uid()) = owner_id);

DROP POLICY IF EXISTS intake_records_delete_own ON public.intake_records;
CREATE POLICY intake_records_delete_own
    ON public.intake_records
    FOR DELETE
    TO authenticated
    USING ((SELECT auth.uid()) = owner_id);

-- Table privileges are necessary for the invoker function and direct
-- owner-scoped API operations; RLS supplies the row-level boundary.
REVOKE ALL ON TABLE public.intake_records FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.intake_records TO authenticated;
