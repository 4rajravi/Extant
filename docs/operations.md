# Operations

Moved here from the top level README in Phase 12, unchanged, when that README became the
writeup.

## Two planes, one contract

An offline **index plane** writes and a stateless **serving plane** reads. Nothing is
migrated in place: every index build creates a brand new Postgres schema, and that schema is
the contract between the two planes. Rollout is a `search_path` change on a new serving
revision. Rollback is the previous revision, still pointing at the previous schema. Old
schemas are kept for a bounded window so a disputed answer can be reproduced. Only a few things
outlive a build, and all live in `public`: the `vector` extension, `public.vec_cache` (the
embedding cache keyed on content hash plus model identity), and the daily cap counter and
request ledger (`public.usage_daily`, `public.request_ledger`).

Build schemas are named

```
idx_<corpus>_<YYYYMMDD>_<short git commit>      e.g. idx_rbi_20261118_a3f91c4
```

and the tooling refuses to create or drop any schema whose name does not match, so `public`
cannot be dropped by a typo. Serving connects as the read only role `extant_reader`, which
can `SELECT` from build schemas, and write exactly two tables in `public`: the daily cap
counter and the request ledger, since the cap must bind a serving instance.

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
uv run playwright install chromium && uv run pytest -m web   # the page, in Chromium
```

`pytest` runs the unit tests on their own and skips the integration tests unless
`EXTANT_WRITER_DSN` is set, either in the environment or in `.env`. With `make up`,
`make bootstrap` and a filled `.env` it runs everything. CI runs all four checks against a
pgvector service container and fails if any integration test would be skipped.
