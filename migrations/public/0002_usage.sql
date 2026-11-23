-- Public bootstrap, version 0002: the daily cap counter and the request ledger.
--
-- Idempotent, like 0001. Both tables live in public, not in a build schema, so they survive
-- every index rebuild, and in Postgres rather than in process, because Cloud Run with
-- minimum instances zero resets an in-process counter on every cold start and never shares
-- it between concurrent instances: such a counter would let the cap be exceeded several
-- times over while every test passed.
--
-- The cap counts answered questions, which is what the cap of 50 refers to. Model calls are
-- counted beside them so the ratio is visible. A day is a calendar day in India
-- (Asia/Kolkata), decided by the caller and passed in, so a test can move the clock.

SET LOCAL search_path TO public;

CREATE TABLE IF NOT EXISTS public.usage_daily (
  day          date          PRIMARY KEY,
  requests     integer       NOT NULL DEFAULT 0 CHECK (requests >= 0),
  answered     integer       NOT NULL DEFAULT 0 CHECK (answered >= 0),
  model_calls  integer       NOT NULL DEFAULT 0 CHECK (model_calls >= 0),
  model_eur    numeric(12,6) NOT NULL DEFAULT 0 CHECK (model_eur >= 0)
);

-- One row per request, written in the same transaction as the counter it moves. It holds
-- the redacted query's hash, never the query: repeat questions can be counted without
-- storing what anyone pasted.
CREATE TABLE IF NOT EXISTS public.request_ledger (
  request_id      text          PRIMARY KEY,
  at              timestamptz   NOT NULL DEFAULT now(),
  day             date          NOT NULL,
  index_version   text          NOT NULL,
  query_sha256    char(64)      NOT NULL CHECK (query_sha256 ~ '^[0-9a-f]{{64}}$'),
  outcome         text          NOT NULL,
  refusal_reason  text,
  model_calls     integer       NOT NULL CHECK (model_calls >= 0),
  eur             numeric(12,6) NOT NULL CHECK (eur >= 0)
);
CREATE INDEX IF NOT EXISTS request_ledger_day ON public.request_ledger (day);

-- Serving connects as the read only role. These two tables are the only thing it may write:
-- the cap must bind a serving instance, so the instance must be able to move the counter.
GRANT SELECT, INSERT, UPDATE ON public.usage_daily, public.request_ledger TO {reader};
