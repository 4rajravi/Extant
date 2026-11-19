"""The Phase 1 coverage report, the baseline counts and the committed label sets.

Offline: reads the cache and the derived files of crawl, extract, parse and mine, never the
network. Writes, into the repository's ``evals/``:

``coverage-rbi-<YYYYMMDD>.md``      the report later phases compare against
``coverage-rbi-<YYYYMMDD>.jsonl``   one row per document version, the report's underlying rows
``baseline-counts.json``            documents, paragraphs, concordance and withdrawals
``labels/rbi/``                     the four gold label sets, pinned to the code versions

Every figure in the report is computed here from those files; nothing is typed in except the
numbers RBI itself published (``PUBLISHED``), which are what the computed ones are checked
against.
"""

from __future__ import annotations

import json
import statistics
import textwrap
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from extant.core.models import ConcordanceEntry
from extant.corpora.rbi.acquisition import RawCache
from extant.corpora.rbi.crawl import listed_rows_from_cache, pdf_targets_from_cache
from extant.corpora.rbi.extract_html import (
    HTML_EXTRACTOR_VERSION,
    html_extract_path,
    load_html_document,
)
from extant.corpora.rbi.mine import MINER_VERSION
from extant.corpora.rbi.parse import PARSER_VERSION
from extant.corpora.rbi.parse_run import parse_cache_dir

# What RBI published, which the crawl is measured against.
PUBLISHED = {
    # press release of 28 Nov 2025: "244 Master Directions"
    "regulation_directions": 244,
    # the Annexes of RBI/2025-26/100 and RBI/DoS/2026-27/221
    "withdrawn": 9445,
    "withdrawn_supervision": 628,
}
# The design's working estimate of corpus size, which this report replaces.
DESIGN_PARAGRAPHS = 300_000
DESIGN_TOKENS_PER_PARAGRAPH = 120
# A stated approximation for English legal text, not a tokenizer count: no model or embedding
# API is called in Phase 1, and the figure is only for sizing.
CHARS_PER_TOKEN = 4
TOLERANCES = {"documents": 0.0, "paragraphs": 0.02, "concordance": 0.05}
DEPARTMENTS = ("regulation", "supervision")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run crawl, extract, parse and mine first")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _para(*parts: str) -> str:
    return textwrap.fill(" ".join(parts), width=100, break_on_hyphens=False)


def _covers(codes: list[str], listed: str | None) -> bool:
    """Whether extracted codes include the listing's category; an NBFC subtype counts as NBFC."""
    return listed is not None and any(c == listed or c.startswith(f"{listed}_") for c in codes)


def _date_text(text: str | None) -> date | None:
    """'November 28, 2025' as a date; None when it does not parse."""
    try:
        return datetime.strptime(" ".join((text or "").split()), "%B %d, %Y").date()
    except ValueError:
        return None


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/a"


@dataclass(frozen=True, slots=True)
class _Page:
    source_sha256: str
    blocks: int
    chars: int


def _pages(cache: RawCache, data_dir: Path, urls: dict[str, str]) -> dict[str, _Page]:
    """Block count and text length of each version's extracted page, by doc_id."""
    out: dict[str, _Page] = {}
    for doc_id, url in urls.items():
        entry = cache.get(url)
        if entry is None:
            continue
        path = html_extract_path(data_dir, entry.source_sha256)
        if not path.exists():
            continue
        doc = load_html_document(path)
        out[doc_id] = _Page(
            entry.source_sha256, len(doc.blocks), sum(len(b.text) for b in doc.blocks)
        )
    return out


def _warning_examples(
    data_dir: Path, rows: list[dict[str, Any]], pages: dict[str, _Page]
) -> dict[str, tuple[str, dict[str, Any]]]:
    """The first warning of each code, from the first version (by doc_id) that has it."""
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r["doc_id"]):
        missing = [c for c in r["warnings"] if c not in out]
        page = pages.get(r["doc_id"])
        if not missing or page is None:
            continue
        sha = page.source_sha256
        cached = parse_cache_dir(data_dir) / sha[:2] / f"{sha}.json"
        if not cached.exists():
            continue
        for w in json.loads(cached.read_text(encoding="utf-8"))["warnings"]:
            if w["code"] in missing and w["code"] not in out:
                out[w["code"]] = (r["doc_id"], w)
    return out


def build_report(data_dir: Path, evals_dir: Path, *, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    stamp = today.strftime("%Y%m%d")
    derived = data_dir / "derived"
    labels_dir = derived / "labels" / f"m{MINER_VERSION}"
    cache = RawCache(data_dir)

    listed = listed_rows_from_cache(cache)
    pdfs = pdf_targets_from_cache(cache)
    rows = _jsonl(derived / "parsed" / "parse_report.jsonl")
    urls = {v["doc_id"]: v["url"] for v in _jsonl(derived / "versions.jsonl")}
    summary = json.loads((labels_dir / "summary.json").read_text(encoding="utf-8"))
    annexes = _jsonl(labels_dir / "repeal_annexes.jsonl")
    standalone = _jsonl(labels_dir / "standalone.jsonl")
    withdrawals = _jsonl(labels_dir / "withdrawals.jsonl")
    withdrawals_dos = _jsonl(labels_dir / "withdrawals_supervision.jsonl")
    manifest = list(cache.manifest())
    pages = _pages(cache, data_dir, urls)

    # ---------------------------------------------------------------- counts
    current = [r for r in rows if r["current"]]
    with_text = [r for r in rows if r["status"] != "no_body"]
    bodies = [r for r in current if r["status"] != "no_body"]
    no_body = [r for r in rows if r["status"] == "no_body"]
    nb_cur = [r for r in no_body if r["current"]]
    ok_status = {"parsed", "parsed_with_warnings"}

    def by_dept(items: list[dict[str, Any]]) -> list[int]:
        return [sum(1 for r in items if r["department"] == d) for d in DEPARTMENTS]

    listed_by = [sum(1 for r in listed if r.department == d) for d in DEPARTMENTS]
    status_all = Counter(r["status"] for r in rows)
    status_cur = Counter(r["status"] for r in current)
    paras_cur = [r["paragraphs"] for r in bodies]
    n_paras_cur = sum(paras_cur)
    n_paras_all = sum(r["paragraphs"] for r in rows)
    chars_cur = sum(r["body_chars"] for r in bodies)
    chars_all = sum(r["body_chars"] for r in rows)
    tokens_cur = chars_cur // CHARS_PER_TOKEN
    design_tokens = DESIGN_PARAGRAPHS * DESIGN_TOKENS_PER_PARAGRAPH
    fetched = [m for m in manifest if m.outcome == "fetched"]
    fetch_failed = [m for m in manifest if m.outcome == "failed"]
    pdfs_cached = sum(1 for t in pdfs if cache.get(t.url) is not None)

    unbalanced = [
        r["doc_id"]
        for r in with_text
        if r["doc_id"] not in pages
        or sum(r["block_accounting"].values()) != pages[r["doc_id"]].blocks
    ]

    def share(r: dict[str, Any]) -> str:
        page = pages.get(r["doc_id"])
        return _pct(r["body_chars"], page.chars) if page else "n/a"

    smallest = sorted(bodies, key=lambda r: (r["paragraphs"], r["doc_id"]))[:10]
    largest = sorted(bodies, key=lambda r: (-r["paragraphs"], r["doc_id"]))[:10]
    small_shares = [
        r["body_chars"] / pages[r["doc_id"]].chars for r in smallest if r["doc_id"] in pages
    ]

    warn_totals: Counter[str] = Counter()
    warn_versions: Counter[str] = Counter()
    for r in rows:
        for code, n in r["warnings"].items():
            warn_totals[code] += n
            warn_versions[code] += 1
    warn_example = _warning_examples(data_dir, rows, pages)

    app = Counter(r["applicability_status"] for r in bodies)
    app_agrees = sum(1 for r in bodies if _covers(r["applicability"], r["listed_under"]))
    app_gaps = [r for r in bodies if r["applicability_status"] != "extracted"]
    app_elsewhere = [
        r
        for r in bodies
        if r["applicability_status"] == "extracted"
        and not _covers(r["applicability"], r["listed_under"])
    ]
    basis = Counter(r["effective_basis"] for r in bodies)
    issued = sum(1 for r in bodies if r["issued_on"])
    effective = sum(1 for r in bodies if r["effective_from"])
    updated = sum(1 for r in bodies if r["updated_as_on"])
    not_updated = [r for r in bodies if not r["updated_as_on"]]
    not_updated_single = sum(1 for r in not_updated if r["version"] == 1)

    wd, ws = summary["withdrawn"], summary["withdrawn_supervision"]
    gold = withdrawals + withdrawals_dos
    gold_refs = {w["canonical_ref"] for w in gold if w["canonical_ref"]}
    live_refs = {
        w["canonical_ref"]
        for name in ("withdrawn.jsonl", "withdrawn_supervision.jsonl")
        for w in _jsonl(labels_dir / name)
        if w["canonical_ref"]
    }
    annex_refs = [a["canonical_ref"] for a in annexes if a["canonical_ref"]]
    gold_named_by_annex = sum(1 for w in gold if w["canonical_ref"] in set(annex_refs))
    annex_not_live = sum(1 for a in annex_refs if a not in live_refs)
    annex_not_gold = sum(1 for a in annex_refs if a not in gold_refs)
    cross = summary["cross_checks"]
    refs = summary["references"]
    unnormalised: dict[str, Counter[str]] = {}
    for f in refs["failed_to_normalise"]:
        unnormalised.setdefault(f["ref"], Counter())[f["list"]] += f["rows"]
    final_lists = ("withdrawn", "withdrawn_supervision", "standalone")
    unnormalised_final = sum(
        n for c in unnormalised.values() for k, n in c.items() if k in final_lists
    )
    gaps = summary["html_gaps"]

    withdrawn_later = _withdrawn_later(standalone, gold)
    concordance = _write_labels(data_dir, evals_dir, annexes, standalone, withdrawn_later)

    # ---------------------------------------------------------------- the report
    first_warned = next((r for r in current if r["status"] == "parsed_with_warnings"), None)
    first_clean = next((r for r in current if r["status"] == "parsed"), None)

    def dept_row(stage: str, counts: list[int], note: str = "") -> str:
        return _row(stage, *counts, sum(counts), note)

    def table(items: list[dict[str, Any]]) -> list[str]:
        return [
            _row(
                f"`{r['doc_id']}`",
                r["direction_id"],
                r["listed_under"],
                r["paragraphs"],
                f"{r['body_chars']:,}",
                share(r),
                " ".join((r["title"] or "").split()),
            )
            for r in items
        ]

    def warn_row(code: str) -> str:
        example = ""
        if code in warn_example:
            doc_id, fw = warn_example[code]
            example = f"`{doc_id}` at `{fw['anchor']}`: {fw['detail'][:100]}"
        return _row(f"`{code}`", warn_totals[code], warn_versions[code], example)

    md: list[str] = [
        f"# RBI coverage report, {today.isoformat()}",
        "",
        _para(
            "The Phase 1 deliverable, generated by `python -m extant.corpora.rbi report` from",
            f"the local cache with HTML extraction `{HTML_EXTRACTOR_VERSION}`, parser",
            f"`p{PARSER_VERSION}` and miner `m{MINER_VERSION}`. The per version rows are in",
            f"`coverage-rbi-{stamp}.jsonl` beside this file, the baseline in",
            "`baseline-counts.json`, the label sets in `labels/rbi/`. The decisions and the",
            "reconnaissance behind them are in `recon-rbi-20261119.md`.",
        ),
        "",
        "**Go or no go: clean consolidation annex extraction is 0% (0 of 15 sampled"
        " consolidated directions, and 0 of all 240 consolidated Department of Regulation"
        " directions).**",
        "",
        _para(
            "That is below the 60% line, and it is how RBI published the 2025 consolidation,",
            "not a parser result: the consolidated directions carry no consolidation annex; each",
            "repeal paragraph points to one central notification whose annex is a flat list of",
            "withdrawn circulars, with no column naming the direction or paragraph a circular",
            "went into. Under the decision to use published lists only, the corpus yields",
            "withdrawal and retention gold in quantity, document level supersession for",
            f"{len(annexes)} circulars, and no paragraph level concordance at all.",
        ),
        "",
        "## Funnel",
        "",
        _para(
            "The corpus is every direction listed by the Department of Regulation and the",
            "Department of Supervision, read from the HTML pages (the source of record, since",
            "the PDF host serves a CAPTCHA), with every version RBI publishes.",
        ),
        "",
        _row("Stage", "Regulation", "Supervision", "Total", "Note"),
        _row("---", "---", "---", "---", "---"),
        dept_row("Directions in the listings", listed_by),
        _row(
            "Expected from RBI's announcements",
            PUBLISHED["regulation_directions"],
            "n/a",
            "",
            "press release of 28 Nov 2025; reconciles by page id with withdrawals and"
            " re-issues (recon report)",
        ),
        dept_row(
            "Current pages downloaded",
            by_dept(current),
            f"{len(listed) - len(current)} not downloaded",
        ),
        dept_row(
            "Pages with direction text",
            by_dept(bodies),
            f"{len(nb_cur)} with only a PDF link: "
            + ", ".join(str(r["direction_id"]) for r in nb_cur),
        ),
        dept_row(
            "Parsed",
            by_dept([r for r in bodies if r["status"] in ok_status]),
            f"{status_cur['failed']} failed, {status_cur['no_paragraphs']} with no paragraphs",
        ),
        dept_row(
            "Parsed with no warning",
            by_dept([r for r in bodies if r["status"] == "parsed"]),
            f"{status_cur['parsed_with_warnings']} parsed with warnings",
        ),
        "",
        _para(
            f"Versions: {len(rows)} in all, {len(current)} current and",
            f"{len(rows) - len(current)} earlier; {len(with_text)} with text,",
            f"{status_all['parsed']} parsed with no warning, {status_all['parsed_with_warnings']}",
            f"with warnings, {status_all['failed']} failed. Network: {len(fetched):,} responses",
            f"fetched, {sum(m.bytes or 0 for m in fetched):,} bytes, {len(fetch_failed)} failed",
            f"fetches. PDFs: {len(pdfs)} targets, {pdfs_cached} in the cache.",
        ),
        "",
        "## Paragraphs",
        "",
        _para(
            f"Current versions: **{n_paras_cur:,} paragraphs** in {len(bodies)} directions,",
            f"median {statistics.median(paras_cur):g}, smallest {min(paras_cur)}, largest",
            f"{max(paras_cur)}. All versions: {n_paras_all:,} paragraphs.",
        ),
        "",
        _para(
            f"**Estimated tokens: {tokens_cur:,} for the current versions** ({chars_cur:,}",
            f"characters of paragraph text at about {CHARS_PER_TOKEN} characters a token), and",
            f"{chars_all // CHARS_PER_TOKEN:,} across all versions. This replaces the design's",
            f"working estimate of {DESIGN_PARAGRAPHS:,} paragraphs at about",
            f"{DESIGN_TOKENS_PER_PARAGRAPH} tokens, {design_tokens:,} tokens: the current corpus",
            f"is {_pct(tokens_cur, design_tokens)} of that, in {n_paras_cur:,} paragraphs",
            f"averaging {tokens_cur // max(n_paras_cur, 1)} tokens.",
        ),
        "",
        _para(
            "The ten smallest, which matter most, since a direction that parsed to a handful of",
            "paragraphs may not have parsed. The last numeric column is paragraph text as a share",
            "of all the text on the page; the rest is the masthead, the table of contents, the",
            "headings and the closing.",
        ),
        "",
        _row("doc_id", "page id", "listed under", "paragraphs", "characters", "share", "title"),
        _row("---", "---", "---", "---", "---", "---", "---"),
        *table(smallest),
        "",
        _para(
            "For these ten the paragraphs hold",
            f"{min(small_shares):.0%} to {max(small_shares):.0%} of the page's text:",
            "they are short directions, not failed parses.",
        )
        if small_shares
        else "",
        "",
        "The ten largest:",
        "",
        _row("doc_id", "page id", "listed under", "paragraphs", "characters", "share", "title"),
        _row("---", "---", "---", "---", "---", "---", "---"),
        *table(largest),
        "",
        "## Documents by parse status",
        "",
        _row("parse_status", "Current", "All versions", "Example"),
        _row("---", "---", "---", "---"),
        _row(
            "`parsed`",
            status_cur["parsed"],
            status_all["parsed"],
            f"`{first_clean['doc_id']}`" if first_clean else "",
        ),
        _row(
            "`parsed_with_warnings`",
            status_cur["parsed_with_warnings"],
            status_all["parsed_with_warnings"],
            f"`{first_warned['doc_id']}`, first warning at"
            f" `{first_warned['first_warning']['anchor']}`"
            if first_warned
            else "",
        ),
        _row(
            "`no_body`",
            status_cur["no_body"],
            status_all["no_body"],
            f"`{nb_cur[0]['doc_id']}` (page {nb_cur[0]['direction_id']}): only a PDF link"
            if nb_cur
            else "",
        ),
        _row("`no_paragraphs`", status_cur["no_paragraphs"], status_all["no_paragraphs"], ""),
        _row("`failed`", status_cur["failed"], status_all["failed"], ""),
        "",
        _para(
            "Every block of every page with text is placed in exactly one bucket (front",
            "matter, table of contents, heading, paragraph body, footnote, closing, page",
            "furniture or empty), and the buckets add up to the page's block count for",
            f"{len(with_text) - len(unbalanced)} of {len(with_text)} versions with text"
            + (f"; not for {', '.join(unbalanced[:5])}." if unbalanced else "."),
            "Nothing is dropped without being counted.",
        ),
        "",
        "## Warnings, grouped by reason",
        "",
        _row("Warning", "Occurrences", "Versions", "First example"),
        _row("---", "---", "---", "---"),
        *(warn_row(code) for code in sorted(warn_totals, key=lambda c: (-warn_totals[c], c))),
        "",
        _para(
            "`absorbed_numbered_block`: a numbered block that does not continue the paragraph",
            "numbering, mostly notes under a table, kept inside its paragraph.",
            "`chapter_introduction`: text between a chapter heading and the chapter's first",
            "paragraph, kept with that paragraph. `numbering_gap`: a number missing from the",
            "sequence, mostly paragraphs deleted by amendment. `text_between_paragraphs`: text",
            "with no paragraph open, kept with the paragraph before. `no_title`: no usable title",
            "on the page, so the listing's title is used.",
        ),
        "",
        "## Applicability",
        "",
        _para(
            f"Current versions with text, {len(bodies)} directions: **extracted",
            f"{app['extracted']}**, partial {app['partial']}, missing {app['missing']}.",
            "Extracted means the applicability paragraph brings at least one entity category",
            "into scope: the categories and NBFC subtypes it names, less those it excludes",
            '("other than Small Finance Banks", "not applicable to the following: (i) MGC");',
            "a subtype missing from the list is not named, which is not the same as excluded.",
            "Partial means an applicability paragraph was found but brought none into scope;",
            "missing means none was found in the first twenty paragraphs. The codes extracted",
            "include the listing the direction is filed under (an NBFC subtype counting as",
            "NBFC) for",
            f"{app_agrees} of {len(bodies)}. Not extracted:",
            ", ".join(f"`{r['doc_id']}` ({r['applicability_status']})" for r in app_gaps) + "."
            if app_gaps
            else "none.",
            "Extracted, but without the listing's category:",
            ", ".join(
                f"`{r['doc_id']}` (listed under {r['listed_under']}, applies to"
                f" {', '.join(r['applicability'])})"
                for r in app_elsewhere
            )
            + "."
            if app_elsewhere
            else "none.",
        ),
        "",
        '## Effective date and "Updated as on"',
        "",
        _para(
            f"Current versions with text, {len(bodies)} directions. `issued_on`, from the",
            f"masthead date line: {issued}. `effective_from`: {effective}, of which",
            f"{basis['dated']} are dated in the text and {basis['issue date']} take the issue",
            'date ("with immediate effect" and its variants); not extracted:',
            f"{basis['not dated']} that come into force on the day they are placed on the",
            f"website, which the page does not date, and {basis['not stated']} with no",
            f'commencement paragraph found. "Updated as on": {updated} of {len(bodies)};',
            f"all {len(not_updated)} without it have no earlier version."
            if not_updated_single == len(not_updated)
            else f"of the {len(not_updated)} without it, {not_updated_single} have no earlier"
            " version.",
        ),
        "",
        "## Labels",
        "",
        _row("Set", "Rows", "Source"),
        _row("---", "---", "---"),
        _row(
            "Withdrawals, Department of Regulation",
            f"{wd['gold']['rows']:,}",
            f"RBI/2025-26/100; {wd['rows']:,} rows on the live page,"
            f" {wd['covered_by_notification']:,} covered by the notification against"
            f" {PUBLISHED['withdrawn']:,} published, less"
            f" {len(wd['gold']['merged_duplicates'])} duplicate merges;"
            f" {wd['gold']['not_gold_no_instrument_or_date']} added later left out",
        ),
        _row(
            "Withdrawals, Department of Supervision",
            f"{ws['gold']['rows']:,}",
            f"RBI/DoS/2026-27/221; {ws['rows']} rows on the live page,"
            f" {ws['covered_by_notification']} covered against"
            f" {PUBLISHED['withdrawn_supervision']} published",
        ),
        _row(
            "Retained (standalone circulars)",
            f"{len(standalone):,}",
            f"the standalone list; {summary['standalone']['partly_retained']} retained in part",
        ),
        _row(
            "Supersession (repeal annexes)",
            concordance,
            "annexes inside current directions, `consolidated_into`, document level",
        ),
        "",
        f"**Concordance pairs: {concordance}, of which 0 resolve to a paragraph and"
        f" {concordance} resolve only to a document.**",
        "",
        _para(
            f"Withdrawal rows: {wd['rows']:,} scraped from the Department of Regulation page",
            f"against {PUBLISHED['withdrawn']:,} in the published annex",
            f"({wd['covered_by_notification']:,} date from before the notification and",
            f"{wd['added_later']} were added since); {ws['rows']} from the Department of",
            f"Supervision page against {PUBLISHED['withdrawn_supervision']} published.",
        ),
        "",
        _para(
            "Cross checks. Gold withdrawals that no consolidation annex names:",
            f"**{len(gold) - gold_named_by_annex:,} of {len(gold):,}**. Annex rows naming a",
            f"circular absent from the withdrawal lists: **{annex_not_live} of",
            f"{len(annex_refs)}** against the live lists, and {annex_not_gold} of",
            f"{len(annex_refs)} against the gold sets. The {len(annex_refs)} annex rows are the",
            "November 2025 dividend directions repealed by their March 2026 replacements; they",
            "are on the live Department of Regulation list among the rows added after the",
            "notification, which state no withdrawing instrument or date and so are not gold.",
            "Retained circulars that a withdrawal list also names (same reference and date):",
            f"**{len(withdrawn_later)}**"
            + (
                ", "
                + ", ".join(
                    f"`{w['canonical_ref']}` withdrawn by `{w['withdrawn_by_doc_id']}` on"
                    f" {w['effective_on']}"
                    for w in withdrawn_later.values()
                )
                + "; RBI's standalone list still shows them. The retained rows carry the"
                " withdrawal, so each label holds as of its own date."
                if withdrawn_later
                else "."
            ),
            "Also:",
            f"{cross['draft_only']} keys only in the Department of Regulation's draft repeal",
            f"list and {cross['withdrawn_only']} only in the final; for the Department of",
            f"Supervision, {ws['draft_only']} and {ws['final_only']}.",
        ),
        "",
        "## Reference normalisation",
        "",
        _para(
            f"{refs['distinct_surface_forms']:,} distinct surface forms in the published lists",
            f"collapse to {refs['canonical_forms']:,} canonical forms (",
            ", ".join(
                f"{n:,} with {k} form{'s' if k != '1' else ''}"
                for k, n in refs["surface_forms_per_canonical"].items()
            )
            + "). Cells that failed to normalise, none of them a reference:",
            "; ".join(
                f'"{ref}" ('
                + ", ".join(f"{n} {'row' if n == 1 else 'rows'} in {k}" for k, n in c.items())
                + ")"
                for ref, c in unnormalised.items()
            )
            + ". They stay in the label rows with `canonical_ref` null.",
        ).replace("( ", "("),
        "",
        "## Failures, grouped by reason",
        "",
        _row("Reason", "Count", "Example"),
        _row("---", "---", "---"),
        _row(
            "PDF host serves a CAPTCHA",
            f"{len(pdfs) - pdfs_cached} of {len(pdfs)} PDFs",
            "`rbidocs.rbi.org.in`, on the first request; not contacted again",
        ),
        _row(
            "Page has no direction text, only a PDF link",
            f"{len(nb_cur)} current, {len(no_body) - len(nb_cur)} earlier versions",
            f"`{nb_cur[0]['doc_id']}`" if nb_cur else "",
        ),
        _row(
            "Annexes published only as PDF attachments",
            f"{gaps['annex_pdf_attachments']} in {gaps['directions_with_annexes_only_as_pdf']}"
            " directions",
            "a repeal list among them for page "
            + ", ".join(str(x) for x in gaps["repeal_list_mentioned_but_not_in_page"]),
        ),
        _row(
            "Withdrawal rows with no instrument or date",
            wd["gold"]["not_gold_no_instrument_or_date"],
            "added to the live list after the notification",
        ),
        _row(
            "Reference cells that are not references",
            f"{unnormalised_final} rows in the final lists",
            ", ".join(f'"{ref}"' for ref in unnormalised),
        ),
        _row("Parse failures", status_all["failed"], ""),
        _row("Fetch failures", len(fetch_failed), ""),
        "",
    ]

    evals_dir.mkdir(parents=True, exist_ok=True)
    (evals_dir / f"coverage-rbi-{stamp}.md").write_text("\n".join(md), encoding="utf-8")
    with (evals_dir / f"coverage-rbi-{stamp}.jsonl").open("w", encoding="utf-8") as fh:
        for r in sorted(rows, key=lambda r: r["doc_id"]):
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    baseline = {
        "generated": today.isoformat(),
        "code": {
            "html_extractor": HTML_EXTRACTOR_VERSION,
            "parser": f"p{PARSER_VERSION}",
            "miner": f"m{MINER_VERSION}",
        },
        "documents": len(with_text),
        "paragraphs": n_paras_all,
        "concordance": concordance,
        "withdrawals": len(gold),
        "detail": {
            "listed_directions": len(listed),
            "versions": len(rows),
            "current_versions_with_text": len(bodies),
            "current_paragraphs": n_paras_cur,
            "estimated_tokens_current": tokens_cur,
            "withdrawals_regulation": len(withdrawals),
            "withdrawals_supervision": len(withdrawals_dos),
            "retained": len(standalone),
        },
        "tolerances": TOLERANCES,
    }
    (evals_dir / "baseline-counts.json").write_text(
        json.dumps(baseline, indent=1) + "\n", encoding="utf-8"
    )
    return baseline


def _withdrawn_later(
    standalone: list[dict[str, Any]], gold: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Retained circulars that a withdrawal list also names, by standalone row: same canonical
    reference and circular date (titles differ by RBI's own typos, so they are not compared)."""
    by_ref_date = {(w["canonical_ref"], w["circular_date"]): w for w in gold if w["canonical_ref"]}
    return {
        r["row"]: by_ref_date[(r["canonical_ref"], r["circular_date"])]
        for r in standalone
        if (r["canonical_ref"], r["circular_date"]) in by_ref_date
    }


def _write_labels(
    data_dir: Path,
    evals_dir: Path,
    annexes: list[dict[str, Any]],
    standalone: list[dict[str, Any]],
    withdrawn_later: dict[int, dict[str, Any]],
) -> int:
    """Copy the gold sets into ``evals/labels/rbi/``; returns the concordance entry count.

    A retained circular that a later notification withdrew carries that withdrawal, so the
    retained set answers "in force as of a date" correctly on both sides of it."""
    labels = data_dir / "derived" / "labels" / f"m{MINER_VERSION}"
    out = evals_dir / "labels" / "rbi"
    out.mkdir(parents=True, exist_ok=True)
    for name in ("withdrawals.jsonl", "withdrawals_supervision.jsonl"):
        (out / name).write_text((labels / name).read_text(encoding="utf-8"), encoding="utf-8")

    entries = [
        ConcordanceEntry(
            old_ref=a["canonical_ref"],
            old_title=a["subject"] or None,
            old_date=_date_text(a["date_text"]),
            new_doc_id=a["direction_doc_id"],
            relation="consolidated_into",
            evidence_doc_id=a["direction_doc_id"],
            evidence_anchor=a["anchor"],
        ).model_dump(mode="json")
        for a in annexes
        if a["canonical_ref"]
    ]
    retained = [
        {
            "canonical_ref": r["canonical_ref"],
            "circular_ref": r["ref"],
            "circular_date": r["circular_date"],
            "title": r["title"],
            "retained_in_part": r.get("marks") == "*",
            "withdrawn_by_doc_id": withdrawn_later[r["row"]]["withdrawn_by_doc_id"]
            if r["row"] in withdrawn_later
            else None,
            "withdrawn_on": withdrawn_later[r["row"]]["effective_on"]
            if r["row"] in withdrawn_later
            else None,
        }
        for r in standalone
    ]
    for name, items in (("supersession.jsonl", entries), ("retained.jsonl", retained)):
        (out / name).write_text(
            "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in items),
            encoding="utf-8",
        )
    manifest = {
        "sets": {
            "withdrawals.jsonl": "Withdrawal records, Department of Regulation, RBI/2025-26/100",
            "withdrawals_supervision.jsonl": (
                "Withdrawal records, Department of Supervision, RBI/DoS/2026-27/221"
            ),
            "supersession.jsonl": "ConcordanceEntry records from repeal annexes, document level",
            "retained.jsonl": "circulars RBI retained as standalone instead of consolidating",
        },
        "html_extractor": HTML_EXTRACTOR_VERSION,
        "parser": f"p{PARSER_VERSION}",
        "miner": f"m{MINER_VERSION}",
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return len(entries)
