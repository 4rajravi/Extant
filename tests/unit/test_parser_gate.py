"""The parser gate: three directions with hand checked expectations.

A parser regression rarely crashes; it silently drops paragraphs, and every metric downstream
still looks plausible. So the parser is checked against values a person wrote down by reading
the source pages, never against the parser's own output. Each fixture directory holds the page as
fetched (``source.html``), its extracted blocks (``page.txt``) and ``EXPECTED.md``.

While an ``EXPECTED.md`` still holds TODO_HUMAN, its test fails rather than skips. Once filled,
the test fails until the parser (``extant.corpora.rbi.parse``, step 3) produces exactly the
expected paragraph count, anchors in order, and chapter and annex counts.

The parser is expected to offer ``parse_document(source_html: bytes, *, doc_id: str)``
returning an object with ``paragraphs`` (Phase 0 ``Paragraph`` records in document order),
``chapters`` and ``annexes``.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from extant.core.identity import ANCHOR_RE, parse_para_id

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURES = sorted(p.parent for p in FIXTURES_DIR.glob("rbi-md-*/EXPECTED.md"))
UNFILLED = "TODO_HUMAN"


class UnfilledError(AssertionError):
    pass


@dataclass(frozen=True)
class Expected:
    counts: dict[str, int]
    anchors: list[str]


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if m is None:
        raise AssertionError(f"EXPECTED.md has no '## {heading}' section")
    return m.group(1)


def read_expected(text: str, *, where: str = "EXPECTED.md") -> Expected:
    counts_text = _section(text, "Counts")
    fence = re.search(r"```anchors\n(.*?)```", text, re.DOTALL)
    if fence is None:
        raise AssertionError(f"{where} has no ```anchors fence")
    if UNFILLED in counts_text or UNFILLED in fence.group(1):
        raise UnfilledError(
            f"{where} still has {UNFILLED}: fill the counts and the anchor list from the source"
            " page by hand before the parser can be checked against it"
        )
    counts: dict[str, int] = {}
    for key, value in re.findall(r"^- (\w+): (.+)$", counts_text, re.MULTILINE):
        if not value.strip().isdigit():
            raise AssertionError(f"{where}: count {key!r} is not a whole number: {value!r}")
        counts[key] = int(value)
    for key in ("paragraphs", "chapters", "annexes"):
        if key not in counts:
            raise AssertionError(f"{where}: missing count {key!r}")
    anchors = [line.strip() for line in fence.group(1).splitlines() if line.strip()]
    bad = [a for a in anchors if ANCHOR_RE.fullmatch(a) is None]
    if bad:
        raise AssertionError(f"{where}: not valid anchors: {bad}")
    if len(set(anchors)) != len(anchors):
        raise AssertionError(f"{where}: an anchor is listed twice")
    if len(anchors) != counts["paragraphs"]:
        raise AssertionError(
            f"{where}: {len(anchors)} anchors listed but paragraphs is {counts['paragraphs']}"
        )
    return Expected(counts, anchors)


def test_there_are_three_gate_fixtures() -> None:
    # Deleting a fixture must not be a way to make the gate pass.
    assert [f.name for f in FIXTURES] == [
        "rbi-md-2025-0175-v1",
        "rbi-md-2025-0259-v1",
        "rbi-md-2025-0382-v1",
    ]
    for fixture in FIXTURES:
        assert (fixture / "source.html").is_file()
        assert (fixture / "page.txt").is_file()


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_parser_matches_hand_checked_expectations(fixture: Path) -> None:
    try:
        expected = read_expected(
            (fixture / "EXPECTED.md").read_text(encoding="utf-8"), where=str(fixture)
        )
    except UnfilledError as exc:
        pytest.fail(str(exc))
    if importlib.util.find_spec("extant.corpora.rbi.parse") is None:
        pytest.fail("the paragraph parser (extant.corpora.rbi.parse, Phase 1 step 3) is not built")
    parse = importlib.import_module("extant.corpora.rbi.parse")
    doc_id = fixture.name.replace("-", ":", 3).replace("-v", ":v")
    result = parse.parse_document((fixture / "source.html").read_bytes(), doc_id=doc_id)
    anchors = [parse_para_id(p.para_id).anchor for p in result.paragraphs]
    assert anchors == expected.anchors
    assert len(result.chapters) == expected.counts["chapters"]
    assert len(result.annexes) == expected.counts["annexes"]


# --------------------------------------------------------------------------- the gate's own reader

FILLED = """# Expected parse

## Counts

- paragraphs: 3
- chapters: 1
- annexes: 0

## Anchors, in document order

```anchors
preamble
p1
p2
```

## Reading aid (not read by the test)

TODO_HUMAN may appear here without effect.
"""


def test_reader_accepts_a_filled_file() -> None:
    expected = read_expected(FILLED)
    assert expected.counts == {"paragraphs": 3, "chapters": 1, "annexes": 0}
    assert expected.anchors == ["preamble", "p1", "p2"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("- paragraphs: 3", "- paragraphs: TODO_HUMAN"), "still has TODO_HUMAN"),
        (("p1\n", "TODO_HUMAN\n"), "still has TODO_HUMAN"),
        (("- paragraphs: 3", "- paragraphs: 4"), "4 anchors|3 anchors listed"),
        (("p2\n", "p1\n"), "listed twice"),
        (("p2\n", "2\n"), "not valid anchors"),
        (("- annexes: 0\n", ""), "missing count"),
    ],
)
def test_reader_refuses_unfilled_or_inconsistent_files(
    change: tuple[str, str], message: str
) -> None:
    with pytest.raises(AssertionError, match=message):
        read_expected(FILLED.replace(*change))
