-- Public bootstrap, version 0003: how often a retired paragraph id is still asked for.
--
-- Idempotent, like 0001 and 0002. A parser change can retire a paragraph id; the build's
-- para_id_aliases table maps it to the paragraph that replaced it, so a citation saved last
-- month still resolves. Each time one resolves through an alias this counter moves. It lives
-- in public, not in a build schema, so the count survives rebuilds: a retired id still
-- being asked for after several builds is the signal that someone holds stale citations.
--
-- Keyed on the retired id and the build that resolved it, so a count can be read per build.

SET LOCAL search_path TO public;

CREATE TABLE IF NOT EXISTS public.para_id_alias_hits (
  old_para_id    text          NOT NULL,
  index_version  text          NOT NULL,
  new_para_id    text          NOT NULL,
  hits           integer       NOT NULL DEFAULT 0 CHECK (hits >= 0),
  first_hit_at   timestamptz   NOT NULL DEFAULT now(),
  last_hit_at    timestamptz   NOT NULL DEFAULT now(),
  PRIMARY KEY (old_para_id, index_version)
);

-- Serving moves the counter, so the read only role may write this one table, as it may the
-- cap's two.
GRANT SELECT, INSERT, UPDATE ON public.para_id_alias_hits TO {reader};
