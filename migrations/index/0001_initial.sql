-- Index build schema, version 0001.
--
-- Applied by `python -m extant.core.schema create --schema <name>`, in one transaction
-- together with the build manifest and grants.sql. Placeholders in braces are filled with
-- psycopg.sql.Identifier, never by string formatting, so a literal brace anywhere in this
-- file must be written doubled.
--
-- One build, one brand new schema. Nothing here is ever migrated in place: a changed DDL is
-- a new numbered file, and it takes effect in the next build.

CREATE SCHEMA {schema};
SET LOCAL search_path TO {schema}, public;

CREATE TABLE documents (
  doc_id            text PRIMARY KEY,
  corpus            text NOT NULL,
  title             text NOT NULL,
  instrument_type   text NOT NULL,
  authority_rank    smallint NOT NULL,
  issued_on         date,
  effective_from    date,
  effective_to      date,
  supersedes_doc_id text REFERENCES documents(doc_id),
  version_seq       smallint NOT NULL DEFAULT 1,
  source_url        text NOT NULL,
  source_sha256     char(64) NOT NULL,
  parse_status      text NOT NULL,
  CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TABLE paragraphs (
  para_id       text PRIMARY KEY,
  doc_id        text NOT NULL REFERENCES documents(doc_id),
  ordinal       integer NOT NULL,
  chapter       text,
  para_number   text,
  heading_path  text,
  body          text NOT NULL,      -- verbatim as published; content_hash = sha256(body)
  content_hash  char(64) NOT NULL,
  source_page   integer NOT NULL,
  UNIQUE (doc_id, ordinal)
);
CREATE INDEX paragraphs_doc  ON paragraphs (doc_id, ordinal);
CREATE INDEX paragraphs_hash ON paragraphs (content_hash);

-- Everything conditional that is not on the hot path
CREATE TABLE facets (
  scope   text NOT NULL CHECK (scope IN ('document','paragraph')),
  ref_id  text NOT NULL,
  key     text NOT NULL,
  value   text NOT NULL,
  PRIMARY KEY (scope, ref_id, key, value)
);

CREATE TABLE entity_types (
  code   text PRIMARY KEY,   -- nbfc_ul, sfb, ucb, pb, arc, cic, aifi ...
  label  text NOT NULL
);

-- The hot table. Filters, lexical index and vector all on one row.
--
-- body and embed_text are stored separately on purpose. embed_text is what gets embedded:
-- the document title, heading path and entity scope composed in front of the paragraph.
-- body is the paragraph as published. Groundedness checking and quote verification always
-- run against body, never against embed_text, otherwise the composed header could be
-- quoted back as if it were regulation. fts is generated from body for the same reason.
--
-- content_hash is sha256(embed_text) and keys public.vec_cache.
--
-- valid_from and valid_to are a half open interval. A chunk is in force on asof when
--   valid_from <= asof AND (valid_to IS NULL OR valid_to > asof)
CREATE TABLE chunks (
  chunk_id        bigserial PRIMARY KEY,
  para_id         text NOT NULL REFERENCES paragraphs(para_id),
  doc_id          text NOT NULL,
  body            text NOT NULL,
  embed_text      text NOT NULL,
  content_hash    char(64) NOT NULL,
  entity_codes    text[] NOT NULL,
  valid_from      date NOT NULL,
  valid_to        date,
  authority_rank  smallint NOT NULL,
  embedding       halfvec(256) NOT NULL,
  fts             tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED
);
CREATE INDEX chunks_fts    ON chunks USING gin (fts);
CREATE INDEX chunks_entity ON chunks USING gin (entity_codes);
CREATE INDEX chunks_valid  ON chunks (valid_from, valid_to);
CREATE INDEX chunks_para   ON chunks (para_id);
-- Deliberately no HNSW index. Retrieval v1 is an exact distance scan over the
-- prefiltered set, which sidesteps pgvector's filtered search problem instead of
-- tuning around it. Do not add one.

-- Gold label set 1, mined from the consolidation annexes
CREATE TABLE concordance (
  old_ref          text NOT NULL,
  old_title        text,
  old_date         date,
  new_doc_id       text REFERENCES documents(doc_id),
  new_para_id      text REFERENCES paragraphs(para_id),
  relation         text NOT NULL
                   CHECK (relation IN ('consolidated_into','repealed','obsolete','retained')),
  evidence_doc_id  text NOT NULL,
  evidence_page    integer,
  PRIMARY KEY (old_ref, relation, evidence_doc_id)
);

-- Gold label set 2, the published withdrawal list
--
-- announced_on and effective_on are separate because some withdrawn circulars carry a
-- deferred repeal date later than the announcement date, so collapsing them would give
-- wrong answers for as of dates in between.
CREATE TABLE withdrawals (
  circular_ref         text PRIMARY KEY,
  title                text,
  circular_date        date,
  withdrawn_by_doc_id  text NOT NULL,
  announced_on         date NOT NULL,
  effective_on         date NOT NULL
);

-- Parser drift. A paragraph id retired by a reparse resolves through here.
CREATE TABLE para_id_aliases (
  old_para_id    text PRIMARY KEY,
  new_para_id    text REFERENCES paragraphs(para_id),
  retired_in     text NOT NULL,   -- index schema that retired it
  reason         text NOT NULL
);

CREATE TABLE build_manifest (key text PRIMARY KEY, value jsonb NOT NULL);
