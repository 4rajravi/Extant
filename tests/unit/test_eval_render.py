"""The renderer: byte stable for the same input, one table per set, and no arithmetic."""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from evals import render
from evals import retrieval as runner
from evals.arms import ARMS
from evals.audit import Audit
from evals.retrieval import summaries
from tests.eval_fixtures import row

RENDER = Path(render.__file__)


@pytest.fixture(autouse=True)
def unaudited(monkeypatch: pytest.MonkeyPatch) -> None:
    """The repository's own audit files are not this test's input."""
    monkeypatch.setattr(runner, "read_audit", lambda name: Audit(name, 0, 0, None))


def summary_rows() -> list[dict[str, object]]:
    rows = []
    for arm in ("hybrid", "dense-only", "no-expansion"):
        for i in range(40):
            rows.append(row(f"t{i}", para_rank=(i % 7) or None, doc_rank=1, arm=arm,
                            holdout=i % 3 == 0))  # fmt: skip
            rows.append(row(f"w{i}", doc_rank=1 if i % 5 else None, gold_paras=(), arm=arm,
                            set_name="withdrawal"))  # fmt: skip
    return summaries(rows, ARMS, {"temporal": 2})


def test_the_same_parquet_renders_the_same_bytes(tmp_path: Path) -> None:
    path = tmp_path / "s.parquet"
    pq.write_table(pa.Table.from_pylist(summary_rows()), path)
    out_a, out_b = tmp_path / "a.md", tmp_path / "b.md"
    render.main([str(path), "--out", str(out_a)])
    render.main([str(path), "--out", str(out_b)])
    assert out_a.read_bytes() == out_b.read_bytes()


def test_row_order_in_the_input_does_not_change_the_output() -> None:
    rows = summary_rows()
    shuffled = rows[:]
    random.Random(1).shuffle(shuffled)
    assert render.render(rows, preamble="", notes={}) == render.render(
        shuffled, preamble="", notes={}
    )


def test_one_table_per_set_and_none_across_sets() -> None:
    text = render.render(summary_rows(), preamble="", notes={})
    assert text.count("\n## temporal\n") == 1
    assert "### dev split" in text
    assert text.count("\n## withdrawal\n") == 1
    assert "pooled" not in text.lower()
    assert "Test-half evaluations so far: 2" in text
    assert "not audited" in text


def test_the_renderer_computes_no_metric() -> None:
    """It formats the summary; it never summarises, bootstraps or averages on its own."""
    tree = ast.parse(RENDER.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "evals.metrics"
        for alias in node.names
    }
    assert imported <= {"DEPTHS"}
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not called & {"sum", "max", "min", "summarise", "bootstrap_mean", "mean", "fmean"}


def test_an_audited_set_says_who_audited_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "read_audit", lambda name: Audit(name, 50, 50, 0.96, "Claude"))
    text = render.render(summary_rows(), preamble="", notes={})
    assert "0.96 on 50 judged pairs, audited by Claude" in text
    assert "not audited" not in text
