# Extant

Question answering over Indian regulatory corpora. Extant answers which rule governs
something, as of when, and for whom, and every answer carries a verified citation or a
refusal. The first corpus is the RBI Master Directions, followed by the Income-tax Act and
then GST; each corpus is an adapter under `extant/corpora/`, not a rewrite.

This repository is at **Phase 9**. On top of the Phase 0 scaffold, the RBI corpus is crawled,
extracted, parsed and mined for gold labels (Phase 1), chunked, embedded and assembled into a
versioned, verified build schema searched by a hybrid retrieval library (Phase 2), and
retrieval is measured by an eval harness with six ablations and a CI gate (Phase 3). An agent
graph answers a question with verified citations or a stated refusal (Phase 4), and guardrails
wrap it: redaction at admission, middleware around every model call, a daily cap in Postgres,
a degradation ladder, structured request logs and an adversarial suite that gates CI
(Phase 5). Answers are measured end to end, with ablation arms and a bounded retry (Phase 6),
the whole is served over MCP, statelessly, as six tools and two resources (Phase 7), and
over HTTP as four endpoints with rate limiting, cap enforcement and split probes (Phase 8).
One static page shows the working: the graph's progress node by node as it runs, then the
verified answer once, with every citation expandable to its paragraph and the quoted span
highlighted, and what the gate set aside visible beside it (Phase 9).

- The corpus adapter and how to run it: [`extant/corpora/rbi/README.md`](extant/corpora/rbi/README.md)
- The coverage report: [`evals/coverage-rbi-20261119.md`](evals/coverage-rbi-20261119.md), with
  the baseline in [`evals/baseline-counts.json`](evals/baseline-counts.json) and the label sets
  in [`evals/labels/rbi/`](evals/labels/rbi/)
- The reconnaissance and every decision taken in Phase 1:
  [`evals/recon-rbi-20261119.md`](evals/recon-rbi-20261119.md)
- The retrieval eval, its leakage controls and the CI gate: [`evals/README.md`](evals/README.md),
  with the ablation table in [`evals/ablations.md`](evals/ablations.md)
- The agent graph, its nodes, checks and refusals: [`extant/agent/README.md`](extant/agent/README.md)
- The guardrails, the degradation ladder and the cap: [`extant/guards/README.md`](extant/guards/README.md),
  with the adversarial suite's results in [`evals/adversarial-results.md`](evals/adversarial-results.md)
- The answer level eval: [`evals/answers.md`](evals/answers.md)
- The MCP server, its tools and its verification boundary: [`extant/mcp_server/README.md`](extant/mcp_server/README.md)
- The HTTP API, its status codes, probes and limits: [`extant/api/README.md`](extant/api/README.md)
- The page, its boundary and the manual check: [`extant/web/README.md`](extant/web/README.md),
  with screenshots in [`docs/screenshots/phase-9/`](docs/screenshots/phase-9/)

## Why prompt injection is already bounded

Extant has no injection filter, deliberately. A filter would add the appearance of security
without the substance; the defence is structural.

The corpus is government material and trusted. User input is not. But the one model that
writes prose, the composer, has an output surface too narrow to do damage. It can emit claim
text, integer indices into a candidate list that retrieval built, and quotes. It cannot emit a
paragraph identifier (indices are resolved to ids by code, and an index out of range fails),
cannot reach the database, cannot change the entity filter or the as-of date (both are fixed
before it runs and set once), and cannot skip the checks. Those are post checks: pure
functions of the final state that `verify` runs and `finalize` runs again, so whether they
run is not something any model output decides. Every quote must be a literal substring of
the cited paragraph, every number in a claim must appear in its own quotes, nothing cited may
be out of force, and the prose may carry no internal label.

So the worst outcome of a successful injection is a claim that fails verification and becomes
a refusal. That is a bounded failure, bounded by the shape of the schema rather than by the
wording of a system prompt. The adversarial suite holds it: a question that tells the system
to ignore its instructions and assert that a bank needs no capital gets four verified claims
about the 15 per cent ratio, and the suite fails if any answer ever carries the planted claim.

One provision is made now for later. If third party commentary is ever ingested it enters the
index marked untrusted (`chunks.trusted`, migration 0005), and retrieval excludes it unless a
caller asks for it by name. That boundary is a column in the filter, not a sentence in a
prompt, and adding it later would have meant migrating the one table the design never
migrates.

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
