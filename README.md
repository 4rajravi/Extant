# Extant

Question answering over Indian regulatory corpora. Extant answers which rule governs
something, as of when, and for whom, and every answer carries a verified citation or a
refusal. The first corpus is the RBI Master Directions, followed by the Income-tax Act and
then GST; each corpus is an adapter under `extant/corpora/`, not a rewrite.

This repository is at **Phase 2**: on top of the Phase 0 scaffold, domain models, schema
tooling, settings and CI, the RBI corpus is crawled, extracted, parsed into paragraphs and mined
for gold labels (Phase 1), then chunked, embedded, assembled into a versioned, verified build
schema and searched by a hybrid retrieval library (Phase 2). There is no agent, refusal logic,
reranker or eval harness yet; no generative model is called anywhere.

- The corpus adapter and how to run it: [`extant/corpora/rbi/README.md`](extant/corpora/rbi/README.md)
- The coverage report: [`evals/coverage-rbi-20261119.md`](evals/coverage-rbi-20261119.md), with
  the baseline in [`evals/baseline-counts.json`](evals/baseline-counts.json) and the label sets
  in [`evals/labels/rbi/`](evals/labels/rbi/)
- The reconnaissance and every decision taken in Phase 1:
  [`evals/recon-rbi-20261119.md`](evals/recon-rbi-20261119.md)

## Two planes, one contract

An offline **index plane** writes and a stateless **serving plane** reads. Nothing is
migrated in place: every index build creates a brand new Postgres schema, and that schema is
the contract between the two planes. Rollout is a `search_path` change on a new serving
revision. Rollback is the previous revision, still pointing at the previous schema. Old
schemas are kept for a bounded window so a disputed answer can be reproduced. Only two things
outlive a build, and both live in `public`: the `vector` extension and `public.vec_cache`,
the embedding cache keyed on content hash plus model identity.

Build schemas are named

```
idx_<corpus>_<YYYYMMDD>_<short git commit>      e.g. idx_rbi_20261118_a3f91c4
```

and the tooling refuses to create or drop any schema whose name does not match, so `public`
cannot be dropped by a typo. Serving connects as the read only role `extant_reader`, which
can `SELECT` from build schemas and nothing else.

## Local setup

You need [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
cp .env.example .env      # then fill in the blank values
```

The three local commands:

```bash
make up          # start Postgres 18 + pgvector on localhost:54329 (docker compose up -d --wait)
make bootstrap   # vector extension in public, extant_reader role, public.vec_cache; idempotent
make down        # stop Postgres, keeping its data volume
```

Without make: `docker compose up -d --wait`, `uv run python -m extant.core.schema bootstrap`,
`docker compose down`.

## Schema tooling

```bash
uv run python -m extant.core.schema create --schema idx_rbi_20261118_a3f91c4
uv run python -m extant.core.schema list
uv run python -m extant.core.schema drop --schema idx_rbi_20261118_a3f91c4
```

The DDL lives in `migrations/` as versioned SQL files: `public/` is applied by `bootstrap`,
`index/` builds each new schema, and `index/grants.sql` always runs last.

## Checks

```bash
make lint        # ruff check, ruff format --check, mypy --strict
make test        # pytest
```

`pytest` runs the unit tests on their own and skips the integration tests unless
`EXTANT_WRITER_DSN` is set, either in the environment or in `.env`. With `make up`,
`make bootstrap` and a filled `.env` it runs everything. CI runs all four checks against a
pgvector service container and fails if any integration test would be skipped.
