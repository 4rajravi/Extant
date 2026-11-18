-- Public bootstrap, version 0001. Idempotent: safe to run any number of times.
--
-- Applied by `python -m extant.core.schema bootstrap` in one transaction. Placeholders in
-- braces are filled by psycopg.sql (Identifier or Literal), never by string formatting, so
-- a literal brace anywhere in this file must be written doubled.
--
-- Everything here survives every index rebuild, which is why it lives in public and not
-- in a build schema.

SET LOCAL search_path TO public;

-- The vector extension, created in public exactly once. Never create it while search_path
-- points at a build schema: dropping that schema would then drop pgvector, and with it the
-- halfvec type that every build schema and public.vec_cache depend on.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- The read only role serving connects as. GRANT fails if the role is absent, so it is
-- created here, before any build schema exists. Roles are cluster wide.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = {reader_name}) THEN
    CREATE ROLE {reader} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
  END IF;
END
$$;

-- The embedding cache. Keyed on content hash plus the full model identity (model id,
-- dimension, quantisation), so a rebuild that changes no embedded text spends nothing on
-- embeddings, and a model change misses the cache instead of silently serving stale
-- vectors.
--
-- content_hash is sha256 of chunks.embed_text, the string that was actually embedded, not
-- of chunks.body. A changed title, heading or entity scope over an unchanged body is a
-- different embedding input and must miss.
CREATE TABLE IF NOT EXISTS public.vec_cache (
  content_hash  char(64)            NOT NULL,
  model_id      text                NOT NULL,
  dim           smallint            NOT NULL CHECK (dim = 256),
  quantisation  text                NOT NULL,
  embedding     public.halfvec(256) NOT NULL,
  created_at    timestamptz         NOT NULL DEFAULT now(),
  PRIMARY KEY (content_hash, model_id, dim, quantisation),
  CHECK (content_hash ~ '^[0-9a-f]{{64}}$')
);

-- The lookup this table serves, run during a build. The model identity belongs in the
-- join condition: joining on content_hash alone, as in USING (content_hash), would count a
-- vector from a different model as a hit.
--
--   SELECT c.chunk_id, c.embed_text
--   FROM chunks c
--   LEFT JOIN public.vec_cache v
--     ON  v.content_hash = c.content_hash
--     AND v.model_id = $1 AND v.dim = $2 AND v.quantisation = $3
--   WHERE v.content_hash IS NULL;
--
-- The primary key leads with content_hash, so each chunk is one index probe.
