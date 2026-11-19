"""HTML extraction. Synthetic snippets pin each rule; one real page, committed as a fixture,
checks facts observed on it independently of this code."""

from __future__ import annotations

from pathlib import Path

import pytest

from extant.core.hashing import sha256_hex_bytes
from extant.corpora.rbi import extract_html as ex
from extant.corpora.rbi.acquisition import CacheEntry, RawCache

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rbi"
REAL = FIXTURE / "direction_13105_sfb_miscellaneous.html"


def page(body: str) -> bytes:
    return f'<html><body><table class="td"><tr><td>{body}</td></tr></table></body></html>'.encode()


def blocks(body: str) -> list[ex.Block]:
    doc = ex.extract_html(page(body), source_sha256="s", url="u")
    assert doc.status == "extracted", doc.error
    return doc.blocks


def test_kinds_follow_markup_but_keep_tag_and_class() -> None:
    bl = blocks(
        '<p class="head">Chapter II \u2013 Role of the Board</p>'
        "<p>Chapter III \u2013 Depositor Education and Awareness Fund</p>"
        "<p>10. A bank shall ...</p>"
        '<p class="footnote">1 Substituted w.e.f. October 1, 2026</p>'
    )
    assert [(b.kind, b.tag, b.css_class) for b in bl] == [
        ("heading", "p", "head"),
        ("paragraph", "p", None),  # RBI marks some chapters as plain <p>; the parser decides
        ("paragraph", "p", None),
        ("footnote", "p", "footnote"),
    ]
    assert [b.index for b in bl] == [0, 1, 2, 3]


def test_text_is_rendered_like_a_browser() -> None:
    [b] = blocks("<p>  4.1\n   In these\tDirections,<br>(a) the\u00a0Act  </p>")
    assert b.text == "4.1 In these Directions,\n(a) the\u00a0Act"


def test_footnote_markers_stay_in_text_with_offsets() -> None:
    [b] = blocks(
        '<p>(x) <sup data-toggle="tooltip" title="Substituted with effect from May 08, 2026">'
        '<a href="#F9" class="links">9</a></sup>A bank may reckon</p>'
    )
    assert b.text == "(x) 9A bank may reckon"
    [m] = b.markers
    assert b.text[m.start : m.end] == "9"
    assert (m.text, m.href, m.title) == ("9", "#F9", "Substituted with effect from May 08, 2026")


def test_glued_marker_at_the_start() -> None:
    [b] = blocks('<p><sup><a href="#F1">1</a></sup>These Directions shall come into force</p>')
    assert b.text == "1These Directions shall come into force"
    assert (b.markers[0].start, b.markers[0].end) == (0, 1)


def test_ordered_list_numbers_are_computed() -> None:
    bl = blocks(
        '<ol type="a" start="3"><li>third'
        '<ol type="i"><li>inner one</li><li>inner two</li></ol></li>'
        "<li>fourth</li></ol>"
        "<ol><li>plain</li><li value='7'>jumped</li><li>next</li></ol>"
        "<ul><li>bullet</li></ul>"
    )
    got = [(b.text, b.list_depth, b.list_type, b.list_ordinal, b.list_marker) for b in bl]
    assert got == [
        ("third", 1, "a", 3, "c."),  # the nested list's text is not part of its parent item
        ("inner one", 2, "i", 1, "i."),
        ("inner two", 2, "i", 2, "ii."),
        ("fourth", 1, "a", 4, "d."),
        ("plain", 1, "1", 1, "1."),
        ("jumped", 1, "1", 7, "7."),
        ("next", 1, "1", 8, "8."),
        ("bullet", 1, "bullet", None, "•"),
    ]


@pytest.mark.parametrize(
    ("list_type", "ordinal", "marker"),
    [("i", 4, "iv."), ("i", 9, "ix."), ("I", 14, "XIV."), ("a", 1, "a."), ("a", 26, "z."),
     ("a", 27, "aa."), ("A", 2, "B."), ("1", 12, "12.")],
)  # fmt: skip
def test_list_markers(list_type: str, ordinal: int, marker: str) -> None:
    assert ex.list_marker(list_type, ordinal) == marker


def test_tables_keep_rows_and_fold_nested_tables_into_cells() -> None:
    [t] = blocks(
        '<table class="tablebg"><tr><th>Sr. No.</th><th>Circular Number</th></tr>'
        "<tr><td>1.</td><td>DOR.ACC.REC.118/21-02-067/2025-26"
        "<table><tr><td>nested</td></tr></table></td></tr></table>"
    )
    assert t.kind == "table"
    assert t.rows == [
        ["Sr. No.", "Circular Number"],
        ["1.", "DOR.ACC.REC.118/21-02-067/2025-26\nnested"],
    ]


def test_containers_are_descended_and_ids_kept() -> None:
    bl = blocks(
        '<div><p id="C3K">K. Contact Details</p></div>'
        '<p class="footnote"><a name="F9"></a>9 Substituted</p>'
        "loose text in the cell"
    )
    assert [(b.kind, b.text, b.ids) for b in bl] == [
        ("paragraph", "K. Contact Details", ["C3K"]),
        ("footnote", "9 Substituted", ["F9"]),
        ("text", "loose text in the cell", []),
    ]


def test_a_page_without_a_body_is_a_record_not_an_exception() -> None:
    doc = ex.extract_html(
        b"<html><body>(1952 kb) Previous Versions</body></html>", source_sha256="s", url="u"
    )
    assert (doc.status, doc.blocks) == ("no_body", [])
    assert doc.error is not None
    assert ex.extract_html(b"\xff\xfe\x00garbage", source_sha256="s", url="u").status == "no_body"


def test_extraction_is_deterministic() -> None:
    data = REAL.read_bytes()
    assert ex.extract_html(data, source_sha256="s", url="u") == ex.extract_html(
        data, source_sha256="s", url="u"
    )


# --------------------------------------------------------------------------- the real page


@pytest.fixture(scope="module")
def real() -> list[ex.Block]:
    doc = ex.extract_html(REAL.read_bytes(), source_sha256="s", url="u")
    assert doc.status == "extracted"
    return doc.blocks


def test_real_page_masthead(real: list[ex.Block]) -> None:
    texts = [b.text for b in real[:3]]
    # Two lines on the page: a <br> separates the RBI number from the department number.
    assert texts[0] == "RBI/DoR/2025-26/204\nDoR.SOG(SPE).REC.No.123/13-04-001/2025-26"
    assert texts[1] == "November 28, 2025"


def test_real_page_keeps_both_chapter_markups(real: list[ex.Block]) -> None:
    by_text = {b.text: b for b in real if b.text.startswith("Chapter ")}
    assert by_text["Chapter II \u2013 Role of the Board"].kind == "heading"
    assert by_text["Chapter III \u2013 Depositor Education and Awareness Fund"].kind == "paragraph"


def test_real_page_paragraphs(real: list[ex.Block]) -> None:
    texts = [b.text for b in real if b.kind == "paragraph"]
    assert any(
        t.startswith("3. These Directions shall be applicable to Small Finance Banks")
        for t in texts
    )
    assert any(t.startswith("62A.") for t in texts)
    assert sum(b.kind == "footnote" for b in real) == 5


def test_real_page_amendment_markers(real: list[ex.Block]) -> None:
    titles = [m.title or "" for b in real for m in b.markers]
    assert any(t.startswith("Substituted w.e.f. October 1, 2026") for t in titles)


# --------------------------------------------------------------------------- cache


def test_extract_all_html_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = RawCache(tmp_path)
    url = "https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13105"
    data = REAL.read_bytes()
    sha = sha256_hex_bytes(data)
    cache.put(CacheEntry(url, url, 200, "t", sha, len(data), "text/html", None, None), data)
    other = "https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?did=404"
    cache.put(CacheEntry(other, other, 200, "t", "0" * 64, 1, "text/html", None, None), b"x")

    first = ex.extract_all_html(tmp_path)
    assert (first.pages_in_cache, first.extracted, first.from_cache) == (1, 1, 0)

    def must_not_run(*args: object, **kwargs: object) -> ex.HtmlDocument:
        raise AssertionError("a cached page was extracted again")

    monkeypatch.setattr(ex, "extract_html", must_not_run)
    second = ex.extract_all_html(tmp_path)
    assert (second.extracted, second.from_cache, second.blocks) == (0, 1, first.blocks)
    assert ex.load_html_document(ex.html_extract_path(tmp_path, sha)).status == "extracted"
