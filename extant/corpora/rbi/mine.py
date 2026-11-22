"""Step 4, the label miners, over the published lists only (decided 19 November 2026).

Offline: reads the cached list pages and the step 2 HTML blocks, never the network, and never
anything the serving plane writes. Writes row level label files under
``derived/labels/m<MINER_VERSION>/`` plus a ``summary.json`` of every count and cross check.

What the published material supports, and nothing more:

``withdrawals.jsonl``, ``withdrawals_supervision.jsonl``
    The withdrawal gold sets, one per department, as Phase 0 ``Withdrawal`` records, one per
    circular: keyed by canonical reference, circular date and title, duplicates merged, only
    rows with a withdrawing instrument and dates. The Department of Supervision's list is
    covered by RBI/DoS/2026-27/221 of July 31, 2026, with immediate effect.
``withdrawn.jsonl``
    One row per row of "Circulars Withdrawn by the Department of Regulation". For the rows the
    notification RBI/2025-26/100 covers (circular dated before 28 November 2025),
    ``announced_on`` is the notification's date and ``effective_on`` is the same day
    ("withdrawn ... with immediate effect"), or 1 January 2026 where the row carries the ``@``
    mark ("@-circulars shall be repealed with effect from January 01, 2026"). Rows added to the
    live page later state no withdrawing instrument, so their dates are left empty.
``standalone.jsonl``
    The retained circulars. A ``*`` row is retained only in part; the footnote says which part.
``draft_repeal.jsonl``
    The October 2025 draft list. A cross check, never gold.
``repeal_annexes.jsonl``
    Rows of "List of circulars repealed" annexes inside directions: document level supersession,
    with the direction page and the block as evidence.
``annex_attachments.jsonl``
    Annexes a page links as a separate PDF instead of carrying them. Their text is absent from
    the HTML source of record; a repeal list among them is reported, not silently missed.

Rows are keyed by ``canonical_ref`` plus circular date: one reference can appear with several
dates, because a Master Direction is listed once per updated version. A reference that does
not normalise (153 "Mail Box Clarification" rows, one "D.O. Letter") is kept with
``canonical_ref = None`` and counted, never dropped.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from extant.core.identity import (
    InvalidReferenceError,
    canonical_ref,
    make_block_anchor,
    make_id_anchor,
    withdrawal_key,
)
from extant.core.models import Withdrawal
from extant.corpora.rbi import sources
from extant.corpora.rbi.acquisition import RawCache
from extant.corpora.rbi.crawl import listed_rows_from_cache
from extant.corpora.rbi.entities import (
    NBFC,
    category_for_listing,
    check_listing_labels,
    entity_types,
    subtype_evidence,
)
from extant.corpora.rbi.extract_html import (
    HTML_EXTRACTOR_VERSION,
    Block,
    html_extract_path,
    load_html_document,
)
from extant.corpora.rbi.identifiers import (
    DirectionIdentity,
    DocVersion,
    circular_doc_base,
    doc_id,
    identify_all,
    number_versions,
)
from extant.corpora.rbi.lists import LISTS, ListRow, read_list

MINER_VERSION = "1"
NOTIFICATION = "RBI/2025-26/100"
NOTIFICATION_DATE = date(2025, 11, 28)
DEFERRED_EFFECT = date(2026, 1, 1)


@dataclass(frozen=True, slots=True)
class Notice:
    """The notification that withdrew the circulars of one published list."""

    rbi_number: str
    issued: date
    deferred_mark: str | None = None  # a footnote mark deferring repeal, such as "@"
    deferred_to: date | None = None


# Which notice covers which list, each read from the notice's own text:
#   RBI/2025-26/100, November 28, 2025: "9445 circulars listed in the Annex ... are withdrawn ...
#     with immediate effect", and the list's footnote "@-circulars shall be repealed with effect
#     from January 01, 2026".
#   RBI/DoS/2026-27/221, July 31, 2026: "the 628 circulars listed in the Annex ... are hereby
#     repealed by the Reserve Bank with immediate effect".
NOTICES: dict[str, Notice] = {
    "withdrawn": Notice(NOTIFICATION, NOTIFICATION_DATE, "@", DEFERRED_EFFECT),
    "withdrawn_supervision": Notice("RBI/DoS/2026-27/221", date(2026, 7, 31)),
}
PUBLISHED_COUNTS = {"withdrawn": 9445, "withdrawn_supervision": 628}
_ANNEX_HEADER = ("sr. no.", "circular number", "date of issue", "subject")
# An annex published as a separate PDF rather than in the page, such as
# ".../content/pdfs/398MD27042026_A5.pdf".
_ANNEX_ATTACHMENT = re.compile(r"/content/pdfs/[^/]+_A\d+\.pdf$", re.IGNORECASE)
_MENTIONS_REPEAL_LIST = re.compile(r"list of circulars repealed", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MinedRow:
    list_name: str
    row: int
    serial: str
    ref: str
    canonical_ref: str | None
    ref_error: str | None
    title: str
    circular_date: date | None
    marks: str | None
    announced_on: date | None = None
    effective_on: date | None = None
    withdrawn_by: str | None = None
    withdrawn_by_doc_id: str | None = None
    date_basis: str | None = None


@dataclass(frozen=True, slots=True)
class AnnexAttachment:
    """An annex the page links as a separate PDF. Its text is not in the HTML source."""

    direction_id: int
    anchor: str
    text: str
    href: str


@dataclass(frozen=True, slots=True)
class AnnexRow:
    direction_id: int
    direction_doc_id: str  # the version of the direction whose page carries the annex
    source_sha256: str
    anchor: str
    ref: str
    canonical_ref: str | None
    ref_error: str | None
    date_text: str
    subject: str


def _canonical(ref: str) -> tuple[str | None, str | None]:
    try:
        return canonical_ref(ref), None
    except InvalidReferenceError as exc:
        return None, str(exc)


def mine_list(rows: list[ListRow]) -> list[MinedRow]:
    out: list[MinedRow] = []
    for r in rows:
        key, error = _canonical(r.ref)
        marks = r.title_marks or r.ref_marks
        mined = MinedRow(
            list_name=r.list_name,
            row=r.row,
            serial=r.serial,
            ref=r.ref,
            canonical_ref=key,
            ref_error=error,
            title=r.title,
            circular_date=r.circular_date,
            marks=marks,
        )
        notice = NOTICES.get(r.list_name)
        if notice is not None:
            if r.circular_date is not None and r.circular_date < notice.issued:
                deferred = (
                    notice.deferred_mark is not None and r.title_marks == notice.deferred_mark
                )
                mined = MinedRow(
                    **{
                        **asdict(mined),
                        "announced_on": notice.issued,
                        "effective_on": notice.deferred_to if deferred else notice.issued,
                        "withdrawn_by": notice.rbi_number,
                        "withdrawn_by_doc_id": doc_id(circular_doc_base(notice.rbi_number), 1),
                        "date_basis": f"notification, deferred by the {notice.deferred_mark} mark"
                        if deferred
                        else "notification, with immediate effect",
                    }
                )
            else:
                mined = MinedRow(
                    **{
                        **asdict(mined),
                        "date_basis": "added to the live list after the notification; the page"
                        " states no withdrawing instrument or date",
                    }
                )
        out.append(mined)
    return out


def _anchor(block: Block) -> str:
    for element_id in block.ids:
        try:
            return make_id_anchor(element_id)
        except ValueError:  # an id outside the anchor grammar; fall back to the position
            continue
    return make_block_anchor(block.index)


@dataclass
class AnnexScan:
    rows: list[AnnexRow]
    attachments: list[AnnexAttachment]
    repeal_list_not_inline: list[int]  # directions that mention a repeal list we could not read


def mine_repeal_annexes(data_dir: Path, doc_ids: dict[int, str]) -> AnnexScan:
    """Over the directions currently listed (``doc_ids``, page id to current doc_id): rows of
    "List of circulars repealed" tables in their extracted pages, every annex published as a PDF
    attachment instead, and every direction whose text mentions a repeal list that is not in its
    page.

    Withdrawn old-format directions are out of scope: their appendices map old circulars into
    directions that no longer exist, which is not published gold for the current corpus."""
    cache = RawCache(data_dir)
    rows: list[AnnexRow] = []
    attachments: list[AnnexAttachment] = []
    missing: list[int] = []
    for entry in cache.entries():
        direction_id = sources.direction_id_of(entry.url)
        if direction_id not in doc_ids or not sources.is_direction_page(entry.url):
            continue
        path = html_extract_path(data_dir, entry.source_sha256)
        if not path.exists():
            raise FileNotFoundError(f"{entry.url} is not extracted; run extract first")
        doc = load_html_document(path)
        found_rows = 0
        mentions = False
        seen_hrefs: set[str] = set()
        for block in doc.blocks:
            if block.kind != "table" and _MENTIONS_REPEAL_LIST.search(block.text):
                mentions = True
            for link in block.links:
                if _ANNEX_ATTACHMENT.search(link.href) and link.href not in seen_hrefs:
                    seen_hrefs.add(link.href)
                    attachments.append(
                        AnnexAttachment(direction_id, _anchor(block), link.text, link.href)
                    )
            if block.kind != "table" or not block.rows:
                continue
            header = tuple(c.strip().lower() for c in block.rows[0])
            if header != _ANNEX_HEADER:
                continue
            for cells in block.rows[1:]:
                if len(cells) != 4 or not cells[1].strip():
                    continue
                key, error = _canonical(cells[1])
                rows.append(
                    AnnexRow(
                        direction_id,
                        doc_ids[direction_id],
                        entry.source_sha256,
                        _anchor(block),
                        cells[1],
                        key,
                        error,
                        cells[2],
                        cells[3],
                    )
                )
                found_rows += 1
        if mentions and found_rows == 0:
            missing.append(direction_id)
    return AnnexScan(
        sorted(rows, key=lambda a: (a.direction_id, a.anchor, a.ref)),
        sorted(attachments, key=lambda a: (a.direction_id, a.href)),
        sorted(missing),
    )


def identities_from_cache(data_dir: Path) -> list[DirectionIdentity]:
    """Every listed direction's doc_id base, from its extracted masthead or the fallback."""
    cache = RawCache(data_dir)
    rows = listed_rows_from_cache(cache)
    mastheads: dict[int, str | None] = {}
    for r in rows:
        entry = cache.get(sources.detail_url(r.direction_id))
        if entry is None:
            mastheads[r.direction_id] = None
            continue
        path = html_extract_path(data_dir, entry.source_sha256)
        if not path.exists():
            raise FileNotFoundError(f"{entry.url} is not extracted; run extract first")
        doc = load_html_document(path)
        mastheads[r.direction_id] = doc.blocks[0].text if doc.blocks else None
    return identify_all(rows, mastheads)


def versions_from_cache(
    data_dir: Path, identities: list[DirectionIdentity]
) -> tuple[list[DocVersion], list[dict[str, Any]]]:
    """Number every listed direction's versions from its cached history page. The current
    text's "Updated as on" date comes from its own title, else from its listing title; where
    both carry one and they differ, the difference is returned rather than resolved."""
    cache = RawCache(data_dir)
    listing = {r.direction_id: r for r in listed_rows_from_cache(cache)}
    versions: list[DocVersion] = []
    disagreements: list[dict[str, Any]] = []
    for ident in identities:
        did = ident.direction_id
        history_page = cache.get(sources.versions_url(did))
        history = (
            sources.parse_version_history(cache.read_bytes(history_page), did)
            if history_page is not None
            else []
        )
        current_url = sources.detail_url(did)
        title_date: date | None = None
        entry = cache.get(current_url)
        if entry is not None:
            path = html_extract_path(data_dir, entry.source_sha256)
            if not path.exists():
                raise FileNotFoundError(f"{current_url} is not extracted; run extract first")
            for block in load_html_document(path).blocks[:6]:
                title_date = sources.updated_as_on(block.text)
                if title_date is not None:
                    break
        listing_date = sources.updated_as_on(listing[did].title) if did in listing else None
        if title_date and listing_date and title_date != listing_date:
            disagreements.append(
                {"direction_id": did, "title": str(title_date), "listing": str(listing_date)}
            )
        current, source = (
            (title_date, "current title")
            if title_date is not None
            else (listing_date, "listing title")
        )
        versions.extend(
            number_versions(
                ident,
                history,
                current_url=current_url,
                current_updated=current,
                current_date_source=source,
            )
        )
    return versions, disagreements


@dataclass
class GoldWithdrawals:
    rows: list[Withdrawal]
    merged: list[list[str]]  # serials of rows that were one circular listed more than once
    not_gold: int  # rows the page gives no withdrawing instrument or date for


def gold_withdrawals(rows: list[MinedRow]) -> GoldWithdrawals:
    """The withdrawal gold set as Phase 0 ``Withdrawal`` records, keyed by canonical reference,
    circular date and title (decided 19 November 2026). Rows equal on all three are one circular
    listed twice and are merged. Rows added to the live page later carry no withdrawing
    instrument or date, which the model requires, so they stay in withdrawn.jsonl only."""
    by_key: dict[str, Withdrawal] = {}
    serials: dict[str, list[str]] = defaultdict(list)
    not_gold = 0
    for r in rows:
        if (
            r.withdrawn_by_doc_id is None
            or r.announced_on is None
            or r.effective_on is None
            or r.circular_date is None
        ):
            not_gold += 1
            continue
        key = withdrawal_key(r.canonical_ref, r.circular_date, r.title)
        serials[key].append(r.serial)
        if key in by_key:
            continue
        by_key[key] = Withdrawal(
            withdrawal_key=key,
            circular_ref=r.ref,
            canonical_ref=r.canonical_ref,
            title=r.title,
            circular_date=r.circular_date,
            withdrawn_by_doc_id=r.withdrawn_by_doc_id,
            announced_on=r.announced_on,
            effective_on=r.effective_on,
        )
    merged = [s for s in serials.values() if len(s) > 1]
    return GoldWithdrawals(list(by_key.values()), merged, not_gold)


def _keys(rows: list[MinedRow]) -> set[tuple[str, date | None]]:
    return {(r.canonical_ref, r.circular_date) for r in rows if r.canonical_ref}


def summarise(mined: dict[str, list[MinedRow]], scan: AnnexScan) -> dict[str, Any]:
    annexes = scan.rows
    w, s, d = mined["withdrawn"], mined["standalone"], mined["draft_repeal"]
    wk, sk, dk = _keys(w), _keys(s), _keys(d)
    w_refs = {r.canonical_ref for r in w if r.canonical_ref}
    forms: dict[str, set[str]] = defaultdict(set)
    for rows in mined.values():
        for r in rows:
            if r.canonical_ref:
                forms[r.canonical_ref].add(r.ref)
    failures = Counter((r.list_name, r.ref) for rows in mined.values() for r in rows if r.ref_error)
    return {
        "miner_version": MINER_VERSION,
        "html_extractor_version": HTML_EXTRACTOR_VERSION,
        "lists": {name: len(rows) for name, rows in mined.items()},
        "withdrawn": {
            "rows": len(w),
            "published_count": 9445,
            "covered_by_notification": sum(r.withdrawn_by == NOTIFICATION for r in w),
            "added_later": sum(r.withdrawn_by is None for r in w),
            "deferred_to_2026_01_01": sum(r.effective_on == DEFERRED_EFFECT for r in w),
            "canonical_keys": len(wk),
            "references_on_more_than_one_row": sum(
                1 for v in Counter(r.canonical_ref for r in w if r.canonical_ref).values() if v > 1
            ),
        },
        "standalone": {"rows": len(s), "partly_retained": sum(r.marks == "*" for r in s)},
        "references": {
            "distinct_surface_forms": len({r.ref for rows in mined.values() for r in rows}),
            "canonical_forms": len(forms),
            "surface_forms_per_canonical": dict(
                sorted(Counter(len(v) for v in forms.values()).items())
            ),
            "failed_to_normalise": [
                {"list": lst, "ref": ref, "rows": n} for (lst, ref), n in sorted(failures.items())
            ],
        },
        "cross_checks": {
            "withdrawn_and_draft": len(wk & dk),
            "draft_only": len(dk - wk),
            "withdrawn_only": len(wk - dk),
            "withdrawn_and_standalone": len(wk & sk),
            "annex_rows": len(annexes),
            "annex_rows_in_withdrawn_list": sum(a.canonical_ref in w_refs for a in annexes),
            "annex_rows_not_in_withdrawn_list": sorted(
                a.ref for a in annexes if a.canonical_ref not in w_refs
            ),
        },
        "html_gaps": {
            "directions_with_annexes_only_as_pdf": len({a.direction_id for a in scan.attachments}),
            "annex_pdf_attachments": len(scan.attachments),
            "repeal_list_mentioned_but_not_in_page": scan.repeal_list_not_inline,
        },
    }


@dataclass(frozen=True, slots=True)
class ListedUnder:
    """The listing a direction appears under: RBI's own classification of it, kept apart from
    the applicability the parser reads in the direction's text, so the two can be compared."""

    direction_id: int
    doc_id: str  # the current version
    category_code: str
    listing_label: str


@dataclass
class EntityScan:
    listed_under: list[ListedUnder]
    evidence: list[Any]


def mine_entities(data_dir: Path, current: dict[int, str]) -> EntityScan:
    cache = RawCache(data_dir)
    rows = listed_rows_from_cache(cache)  # both departments
    check_listing_labels(rows)
    listed = [
        ListedUnder(
            r.direction_id,
            current[r.direction_id],
            category_for_listing(r.listing_id).code,
            r.category,
        )
        for r in rows
    ]
    texts: dict[int, str] = {}
    for r in rows:
        if r.category != NBFC:
            continue
        entry = cache.get(sources.detail_url(r.direction_id))
        if entry is None:
            continue
        doc = load_html_document(html_extract_path(data_dir, entry.source_sha256))
        texts[r.direction_id] = "\n".join(b.text for b in doc.blocks)
    return EntityScan(sorted(listed, key=lambda x: x.doc_id), subtype_evidence(texts))


def _write_models(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(
                json.dumps(row.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
            )


def _write(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(
                json.dumps(asdict(row), default=str, ensure_ascii=False, sort_keys=True) + "\n"
            )


def mine(data_dir: Path) -> dict[str, Any]:
    cache = RawCache(data_dir)
    mined: dict[str, list[MinedRow]] = {}
    for name, (url, _) in LISTS.items():
        entry = cache.get(url)
        if entry is None:
            raise FileNotFoundError(f"{url} is not cached; run crawl first")
        mined[name] = mine_list(read_list(name, cache.read_bytes(entry)).rows)
    identities = identities_from_cache(data_dir)
    versions, disagreements = versions_from_cache(data_dir, identities)
    current = {v.direction_id: v.doc_id for v in versions if v.current}
    scan = mine_repeal_annexes(data_dir, current)
    out = data_dir / "derived" / "labels" / f"m{MINER_VERSION}"
    for name, rows in mined.items():
        _write(out / f"{name}.jsonl", rows)
    _write(out / "repeal_annexes.jsonl", scan.rows)
    gold = gold_withdrawals(mined["withdrawn"])
    _write_models(out / "withdrawals.jsonl", gold.rows)
    gold_supervision = gold_withdrawals(mined["withdrawn_supervision"])
    _write_models(out / "withdrawals_supervision.jsonl", gold_supervision.rows)
    _write(out / "annex_attachments.jsonl", scan.attachments)
    _write(data_dir / "derived" / "identities.jsonl", identities)
    _write(data_dir / "derived" / "versions.jsonl", versions)
    entities = mine_entities(data_dir, current)
    _write_models(data_dir / "derived" / "entity_types.jsonl", entity_types())
    _write(data_dir / "derived" / "listed_under.jsonl", entities.listed_under)
    summary = summarise(mined, scan)
    summary["identities"] = {
        "directions": len(identities),
        "by_source": dict(Counter(i.source for i in identities)),
        "withdrawal_notifications": {
            name: doc_id(circular_doc_base(n.rbi_number), 1) for name, n in NOTICES.items()
        },
    }
    counts = Counter(v.direction_id for v in versions)
    earlier = [v for v in versions if not v.current]
    summary["withdrawn"]["gold"] = {
        "rows": len(gold.rows),
        "merged_duplicates": gold.merged,
        "not_gold_no_instrument_or_date": gold.not_gold,
    }
    sup = mined["withdrawn_supervision"]
    summary["withdrawn_supervision"] = {
        "rows": len(sup),
        "published_count": PUBLISHED_COUNTS["withdrawn_supervision"],
        "covered_by_notification": sum(r.withdrawn_by is not None for r in sup),
        "gold": {
            "rows": len(gold_supervision.rows),
            "merged_duplicates": gold_supervision.merged,
            "not_gold_no_instrument_or_date": gold_supervision.not_gold,
        },
        "failed_to_normalise": sum(r.ref_error is not None for r in sup),
        "in_draft_list": len(_keys(sup) & _keys(mined["draft_repeal_supervision"])),
        "draft_only": len(_keys(mined["draft_repeal_supervision"]) - _keys(sup)),
        "final_only": len(_keys(sup) - _keys(mined["draft_repeal_supervision"])),
        "also_withdrawn_by_regulation": len(
            {w.withdrawal_key for w in gold_supervision.rows}
            & {w.withdrawal_key for w in gold.rows}
        ),
    }
    summary["entities"] = {
        "categories": 11,
        "nbfc_subtypes": [asdict(e) for e in entities.evidence],
        "directions_per_category": dict(Counter(x.category_code for x in entities.listed_under)),
    }
    summary["versions"] = {
        "documents": len(versions),
        "earlier_versions": len(earlier),
        "earlier_versions_cached": sum(cache.get(v.url) is not None for v in earlier),
        "directions_by_version_count": dict(sorted(Counter(counts.values()).items())),
        "current_date_source": dict(Counter(v.date_source for v in versions if v.current)),
        "current_title_and_listing_title_disagree": disagreements,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return summary


__all__ = ["MINER_VERSION", "mine", "mine_list", "mine_repeal_annexes", "summarise"]
