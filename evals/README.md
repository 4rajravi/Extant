# Retrieval eval (Phase 3)

The eval harness measures retrieval and nothing else. It owns the metric definitions and the
ablation table, reads an index build by schema name, writes Parquet, and is never importable
from the serving path (`tests/unit/test_eval_boundary.py` holds that line).

The answer-level metrics (citation validity, groundedness, refusal correctness, false refusal
rate, cost per question) and the two answer-level arms (the supersession gate, citation
verification) need the agent and are Phase 6. Their signatures are in `metrics.py`; their
bodies raise.

## Commands

```bash
uv run python -m evals.sets build                 # the eval sets, from the Phase 1 labels
uv run python -m evals.query_vectors --refresh    # the only command that embeds queries
uv run python -m evals.retrieval --schema idx_rbi_... --out results.parquet         # dev, gated arm
uv run python -m evals.retrieval --schema idx_rbi_... --arms all \
    --sample-schema idx_rbisample_... --out evals/results/dev.parquet               # every arm
uv run python -m evals.render evals/results/dev.summary.parquet                     # ablations.md
uv run python -m evals.gate --baseline evals/baseline.json --results results.parquet
uv run python -m evals.baseline --results evals/results/dev.parquet               # a new bar
uv run python -m evals.audit draw | status        # the label audit files
```

## What is committed

| Path | What | Who writes it |
|---|---|---|
| `sets/rbi/*.p2.jsonl`, `sets/rbi/MANIFEST.json` | the eval sets, pinned to parser `p2` and miner `m1` | `evals.sets build` |
| `query_vectors/<model>_<dim>_<quant>.parquet` | sha256 of each query's text to its vector | `evals.query_vectors --refresh` |
| `samples/rbi-sample.json` | the reduced corpus for arms 4 to 6 | `evals.sets sample` |
| `label-audit/<set>.md` | 50 mined labels per set with a verdict line each | drawn by `evals.audit draw`, **judged by a person** |
| `test-evaluations.json` | how many times each set's test half has been evaluated | the runner, automatically |
| `baseline.json` | the gated arm's per set scores on dev, and per metric tolerances | `evals.baseline`, by a deliberate commit |
| `ablations.md` | the ablation table | `evals.render` |
| `ablations-notes.json` | per set prose the table carries: what each set cannot tell you | by hand |
| `results/*.parquet` | per query rows and per set summaries behind `ablations.md` | the runner |

## Three baseline files, three jobs

They are not interchangeable. One file serving two of these jobs would make a parser regression
and a recall regression indistinguishable.

- `evals/baseline-counts.json` holds row counts. Build step 8 compares a new build against it
  (or against the previous build) to catch a silent parser regression.
- `evals/baseline.json` holds per set, per metric retrieval scores of the gated arm on the dev
  split, with per metric tolerances. The CI gate compares an eval run against it.
- A build manifest's `eval_baseline` field names `evals/baseline.json`, so a build records which
  retrieval bar it was measured against; its `counts_baseline` field names the first file.

The gate never writes `baseline.json`, not even with a flag. Raising or lowering the bar is a
commit of that file on its own, so improving the numbers and lowering the bar look different
in a diff.

## Leakage controls

- **Memorisation.** Each query carries `holdout: true` when its gold material was published
  after the generation model's training cutoff, recorded in `sets/rbi/MANIFEST.json` (Claude
  Opus 5.5, 30 June 2026). The table shows the holdout beside the rest. Retrieval runs no
  generation model and text-embedding-3-large predates the whole corpus, so a retrieval gap
  here is not memorisation; the split is in place for Phase 6, when an answer model is.
- **Development overfitting.** Every set splits into dev and test by group, so near-duplicate
  queries never straddle the halves. The runner defaults to dev; `--split test` must be said,
  and each test run increments `test-evaluations.json` before it runs. The table carries that
  count. A test set looked at forty times is a dev set.
- **Label contamination.** `label-audit/<set>.md` holds a seeded sample of each set's mined
  labels with a blank verdict for a person. The table reads the miner's precision from those
  files and shows a gap until every verdict is filled in: a recall figure has a ceiling at the
  gold set's precision.

## Never pooled

No metric is averaged across sets. `metrics.SetRows` refuses rows from more than one set, arm,
split or corpus, `metrics.summarise` takes only a `SetRows`, and the renderer writes one table
per set.

## CI gate

The `retrieval-gate` job in `.github/workflows/ci.yml` runs the gated arm (`hybrid`) on every
set's dev split against a build published to Neon, then the gate. It embeds nothing, so it is
free to run on every push. It needs, once:

1. A Neon project. Put an owner connection string in `.env` as `EXTANT_PUBLISH_DSN`, and choose a
   password for the read only role: `EXTANT_PUBLISH_READER_DSN=postgresql://extant_reader:<password>@<neon host>/<db>?sslmode=require`.
2. `uv run python -m extant.corpora.rbi publish --schema <build>` copies a verified build there
   (about 330 MB of the 0.5 GB free tier for one build).
3. In the GitHub repository settings: the secret `EXTANT_EVAL_READER_DSN` set to the same value
   as `EXTANT_PUBLISH_READER_DSN`, and the variable `EXTANT_INDEX_SCHEMA` set to the build name.

Until then the job skips the gate and posts a warning saying what is missing on every run's
summary page, so a green run without Neon never looks like a gated one.
The gate measures retrieval code and eval changes against a fixed published build; a change to
chunking or parsing shows up only once a new build is published and a new baseline committed.

## Answer level evaluation (Phase 6)

Everything above measures retrieval and calls no model, so it runs on every push. The answer
level eval (`evals/answers/`) measures whole answers against real model calls, so it costs
money and runs on demand, after a dry run:

```bash
uv run python -m evals.answers.sets check      # the question sets and their evidence
uv run python -m evals.answers.run plan        # the dry run: counts and EUR, no calls at all
uv run python -m evals.answers.run ask         # the run: resumable, with a hard budget stop
uv run python -m evals.answers.judge pass      # the judge over every answered claim
uv run python -m evals.answers.judge draw      # 50 blind pairs for a person to label
uv run python -m evals.answers.render          # evals/answers.md and the row level Parquet
```

- **Sets** (`evals/answers/sets/`): `answerable` (56 questions written from sampled in-force
  paragraphs, the gold known by construction; this replaces the FAQ derived set, since RBI's
  FAQ answers cite no provisions), `as_of` (five amended provisions asked on both sides of the
  change) and `unanswerable` (ten each: absent topics, entity types no provision covers, dates
  before the governing instrument, provisions repealed by the date asked). Every unanswerable
  question records why, and a retrieval check of what the index does and does not hold for
  it; one whose check does not bear out its reason fails validation.
- **Arms** (`extant/agent/arms.py`, never set by serving): the baseline; no supersession
  handling anywhere; no verification; and the bounded retry Phase 4 left out.
- **Cassettes** (`evals/answers/cassettes/`): every model call and query embedding, keyed on
  the exact call and stamped with the model and the date. A call already recorded is
  replayed and costs nothing, so an arm that asks nothing new is free and a resumed run never
  pays twice. No gold answer is in any prompt, fixture or cassette.
- **Metrics** (`evals/answers/metrics.py`): citation validity as a count of violations (any
  is a bug), the refusal pair (correct refusals on unanswerable questions and false refusals
  on answerable ones) from one function that cannot return either alone, gold accuracy,
  as-of correctness, recall beside it for the ablations, groundedness always beside the
  judge's agreement with a person, and cost and latency at p50 and p95.
