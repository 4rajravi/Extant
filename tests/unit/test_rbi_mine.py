"""Published lists and the label miner, against synthetic pages that pin each rule."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from extant.core.hashing import sha256_hex_bytes
from extant.corpora.rbi import sources
from extant.corpora.rbi.acquisition import CacheEntry, RawCache
from extant.corpora.rbi.extract_html import extract_all_html, extract_html
from extant.corpora.rbi.lists import read_list
from extant.corpora.rbi.mine import (
    DEFERRED_EFFECT,
    NOTIFICATION,
    NOTIFICATION_DATE,
    mine_list,
    mine_repeal_annexes,
    summarise,
)

CAPTION = "Circulars Withdrawn by the Department of Regulation"


def list_page(caption: str, rows: list[tuple[str, str, str, str]], footnote: str = "") -> bytes:
    body = "".join(
        f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in rows
    )
    foot = f'<tr><td colspan="4">{footnote}</td></tr>' if footnote else ""
    return (
        f"<html><body><p>{caption}</p><table class='tablebg'>"
        "<tr><td>S No.</td><td>Circular Number</td><td>Circular Name/Title</td><td>Date</td></tr>"
        f"{body}{foot}</table></body></html>"
    ).encode()


ROWS = [
    ("1.", "DOR.CRE.REC.402/07-01-001/2025-26", "Credit Facilities Amendment", "February 13, 2026"),
    ("2.", "DBOD.No.BP.BC.1/21.04.048/2015-16", "Master Circular", "July 1, 2015"),
    ("3.", "DBR.RRB.BC.No.59/31.01.001/2015-16", "Internet Banking for RRBs@", "November 19, 2015"),
    ("4.", "Mail Box Clarification", "Clarification on something", "June 20, 2018"),
]


def withdrawn() -> list:  # type: ignore[type-arg]
    page = list_page(
        CAPTION, ROWS, "@-circulars shall be repealed with effect from January 01, 2026"
    )
    return read_list("withdrawn", page).rows


# --------------------------------------------------------------------------- lists


def test_list_rows_and_footnotes_are_read_verbatim() -> None:
    page = list_page(
        CAPTION, ROWS, "@-circulars shall be repealed with effect from January 01, 2026"
    )
    pl = read_list("withdrawn", page)
    assert [r.serial for r in pl.rows] == ["1.", "2.", "3.", "4."]
    assert pl.rows[2].title == "Internet Banking for RRBs@"  # marks are kept in the text
    assert pl.rows[2].title_marks == "@"
    assert pl.rows[1].circular_date == date(2015, 7, 1)
    assert pl.footnotes == ["@-circulars shall be repealed with effect from January 01, 2026"]


def test_a_changed_page_fails_loudly() -> None:
    with pytest.raises(ValueError, match="no table captioned"):
        read_list("withdrawn", list_page("Some other table", ROWS))
    renamed = list_page(CAPTION, ROWS).replace(b"Circular Number", b"Reference")
    with pytest.raises(ValueError, match="unexpected header"):
        read_list("withdrawn", renamed)


def test_an_unparseable_date_is_kept_as_text() -> None:
    [row] = read_list("withdrawn", list_page(CAPTION, [("1.", "X.1/2", "t", "sometime 1990")])).rows
    assert row.circular_date is None
    assert row.date_text == "sometime 1990"


# --------------------------------------------------------------------------- miner


def test_withdrawal_dates_follow_the_notification_and_the_mark() -> None:
    later, immediate, deferred, no_ref = mine_list(withdrawn())
    assert (immediate.announced_on, immediate.effective_on) == (
        NOTIFICATION_DATE,
        NOTIFICATION_DATE,
    )
    assert immediate.withdrawn_by == NOTIFICATION
    assert immediate.withdrawn_by_doc_id == "rbi:circ:2025:0100:v1"
    assert deferred.effective_on == DEFERRED_EFFECT == date(2026, 1, 1)
    assert deferred.announced_on == NOTIFICATION_DATE
    # a row added to the live page after the notification gets no invented dates
    assert (later.announced_on, later.effective_on, later.withdrawn_by) == (None, None, None)
    assert later.date_basis is not None
    assert "no withdrawing instrument" in later.date_basis
    # a non-reference is kept, with its error, never dropped
    assert no_ref.canonical_ref is None
    assert "not a circular reference" in (no_ref.ref_error or "")


def test_rows_join_on_the_canonical_reference() -> None:
    mined = mine_list(withdrawn())
    assert mined[1].canonical_ref == "DBOD.BP.BC.1/21.04.048/2015-16"


def _scan(tmp_path: Path, pages: dict[int, str]) -> None:
    cache = RawCache(tmp_path)
    for direction_id, body in pages.items():
        url = sources.detail_url(direction_id)
        data = (
            f'<html><body><table class="td"><tr><td>{body}</td></tr></table></body></html>'.encode()
        )
        sha = sha256_hex_bytes(data)
        cache.put(CacheEntry(url, url, 200, "t", sha, len(data), "text/html", None, None), data)
    extract_all_html(tmp_path)


ANNEX = (
    "<p>15. The list of circulars repealed with respect to the provisions relating to SFBs is in"
    " Annex III.</p><p class='head'>List of circulars repealed</p>"
    "<table class='tablebg' id='T3'><tr><td>Sr. No.</td><td>Circular Number</td>"
    "<td>Date of Issue</td><td>Subject</td></tr>"
    "<tr><td>1.</td><td>DOR.ACC.REC.118/21-02-067/2025-26</td><td>November 28, 2025</td>"
    "<td>Prudential Norms on Declaration of Dividend</td></tr></table>"
)
ATTACHED = (
    "<p>Annex 5 contains list of circulars repealed with respect to the provisions.</p>"
    "<p><a href='https://rbidocs.rbi.org.in/rdocs/content/pdfs/398MD27042026_A5.pdf'>"
    "Annex 5</a></p>"
)


def test_repeal_annexes_attachments_and_gaps(tmp_path: Path) -> None:
    _scan(tmp_path, {13328: ANNEX, 13400: ATTACHED, 11566: ANNEX})
    scan = mine_repeal_annexes(
        tmp_path, {13328: "rbi:md:2025:0391:v1", 13400: "rbi:md:2026:0398:v1"}
    )
    [row] = scan.rows
    assert row.direction_doc_id == "rbi:md:2025:0391:v1"
    assert (row.direction_id, row.canonical_ref, row.anchor) == (
        13328,
        "DOR.ACC.REC.118/21-02-067/2025-26",
        "#T3",
    )
    [att] = scan.attachments
    assert (att.direction_id, att.text) == (13400, "Annex 5")
    assert att.href.endswith("398MD27042026_A5.pdf")
    assert scan.repeal_list_not_inline == [13400]


def test_summary_cross_checks(tmp_path: Path) -> None:
    _scan(tmp_path, {13328: ANNEX})
    standalone_page = list_page(
        "List of Standalone Circulars issued by the Department of Regulation",
        [("1.", "DBOD.No.BP.BC.1/21.04.048/2015-16", "Master Circular", "July 1, 2015")],
    )
    draft_page = list_page(
        "List of Circulars to be Repealed/Withdrawn By Department of Regulation",
        [("1", "DBOD.BP.BC.1/21.04.048/2015-16", "Master Circular", "July 1, 2015")],
    )
    mined = {
        "withdrawn": mine_list(withdrawn()),
        "standalone": mine_list(read_list("standalone", standalone_page).rows),
        "draft_repeal": mine_list(read_list("draft_repeal", draft_page).rows),
    }
    summary = summarise(mined, mine_repeal_annexes(tmp_path, {13328: "rbi:md:2025:0391:v1"}))
    checks = summary["cross_checks"]
    assert checks["withdrawn_and_draft"] == 1  # "No." or not, the same circular
    assert (
        checks["withdrawn_and_standalone"] == 1
    )  # a circular both withdrawn and retained is a finding
    assert checks["annex_rows_not_in_withdrawn_list"] == ["DOR.ACC.REC.118/21-02-067/2025-26"]
    assert summary["withdrawn"]["deferred_to_2026_01_01"] == 1
    assert summary["references"]["failed_to_normalise"][0]["ref"] == "Mail Box Clarification"
    json.dumps(summary, default=str)  # the summary is always serialisable


def test_extraction_keeps_links() -> None:
    page = (
        b'<html><body><table class="td"><tr><td><p>as communicated vide '
        b'<a href="https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13170">circular'
        b" DOR.RRC.REC.302/33-01-010/2025-26</a></p></td></tr></table></body></html>"
    )
    [block] = extract_html(page, source_sha256="s", url="u").blocks
    [link] = block.links
    assert link.text == "circular DOR.RRC.REC.302/33-01-010/2025-26"
    assert link.href.endswith("NotificationUser.aspx?Id=13170")


def test_gold_withdrawals_merge_duplicates_and_leave_out_undated_rows() -> None:
    from extant.corpora.rbi.mine import gold_withdrawals

    page = list_page(
        CAPTION,
        [
            ("1.", "DOR.CRE.REC.402/07-01-001/2025-26", "Amendment", "February 13, 2026"),
            ("2.", "DBOD.No.Dir.BC.8/13.03.00/2005-06", "Master Circular - Loans", "July 1, 2005"),
            ("3.", "DBOD.Dir.BC.8/13.03.00/2005-06", "Master Circular - Loans", "July 1, 2005"),
            ("4.", "Mail Box Clarification", "Clarification on A", "June 20, 2018"),
        ],
    )
    gold = gold_withdrawals(mine_list(read_list("withdrawn", page).rows))
    assert [w.circular_ref for w in gold.rows] == [
        "DBOD.No.Dir.BC.8/13.03.00/2005-06",
        "Mail Box Clarification",
    ]
    assert gold.merged == [["2.", "3."]]  # one circular, listed twice with different spellings
    assert gold.not_gold == 1  # added after the notification: no instrument, no date
    assert gold.rows[1].canonical_ref is None
    assert all(w.withdrawn_by_doc_id == "rbi:circ:2025:0100:v1" for w in gold.rows)


SUPERVISION_CAPTION = "Circulars Withdrawn by the Department of Supervision"


def supervision_page(rows: list[tuple[str, str, str, str]]) -> bytes:
    body = "".join(
        f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in rows
    )
    return (
        f"<html><body><p>{SUPERVISION_CAPTION}</p><table class='tablebg'>"
        "<tr><td>Sr. No.</td><td>Circular Number</td><td>Subject</td><td>Date</td></tr>"
        f"{body}</table></body></html>"
    ).encode()


def test_supervision_withdrawals_follow_their_own_notice() -> None:
    page = supervision_page(
        [
            ("1.", "DoS.CO.PPG.SEC.1/11.01.005/2026-27", "Fair Practices Code", "May 21, 2026"),
            ("2.", "DBOD.No.EFS.109/C.107-66", "Discounting of Hundies@", "February 03, 1966"),
        ]
    )
    rows = mine_list(read_list("withdrawn_supervision", page).rows)
    assert {(r.announced_on, r.effective_on) for r in rows} == {
        (date(2026, 7, 31), date(2026, 7, 31))
    }
    # an @ in a Supervision title defers nothing: only the Regulation list carries that footnote
    assert all(r.withdrawn_by_doc_id == "rbi:circ:2026:0221:v1" for r in rows)
    assert rows[1].date_basis == "notification, with immediate effect"
