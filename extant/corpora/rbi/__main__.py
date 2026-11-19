"""python -m extant.corpora.rbi crawl | pdf-list | import-pdfs | extract | parse | mine | report"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from extant.corpora.rbi.settings import RbiSettingsError, load_rbi_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m extant.corpora.rbi")
    sub = parser.add_subparsers(dest="command", required=True)
    crawl = sub.add_parser(
        "crawl", help="step 1: fetch listings, directions and PDFs into the cache"
    )
    crawl.add_argument("--force", action="store_true", help="refetch cached URLs conditionally")
    crawl.add_argument(
        "--retry-blocked", action="store_true", help="contact hosts that served a bot challenge"
    )
    sub.add_parser("pdf-list", help="write the page of PDFs still to download by hand (offline)")
    imp = sub.add_parser(
        "import-pdfs", help="file PDFs downloaded in a browser into the cache (offline)"
    )
    imp.add_argument("folder", type=Path, help="folder holding the downloaded PDFs")
    imp.add_argument(
        "--replace", action="store_true", help="replace cached bytes that differ from the file"
    )
    for name, text in (
        ("extract", "step 2: blocks from cached direction pages, and any imported PDFs"),
        ("parse", "step 3: paragraph records"),
        ("mine", "step 4: label rows from the published lists"),
        ("report", "the coverage report"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--sample", type=int, metavar="N", help="only the first N documents")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_rbi_settings()
    except RbiSettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data_dir = settings.data_dir.resolve()

    if args.command == "crawl":
        from extant.corpora.rbi.acquisition import (
            CrawlBlockedError,
            Fetcher,
            RateLimiter,
            RawCache,
            make_client,
        )
        from extant.corpora.rbi.crawl import crawl

        with make_client(settings.user_agent, settings.timeout_seconds) as client:
            fetcher = Fetcher(
                RawCache(data_dir), client, RateLimiter(settings.request_interval_seconds)
            )
            try:
                result = crawl(
                    fetcher, data_dir, force=args.force, retry_blocked=args.retry_blocked
                )
            except CrawlBlockedError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        print(f"network requests this run: {result.network_requests}")
        for host, info in result.blocked_hosts.items():
            print(f"blocked host: {host} since {info['since']}: {info['reason']}")
        return 0

    if args.command in {"pdf-list", "import-pdfs"}:
        return _pdfs(args, data_dir)

    if args.command == "report":
        from extant.corpora.rbi.report import build_report

        evals = Path(__file__).resolve().parents[3] / "evals"
        try:
            baseline = build_report(data_dir, evals)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(baseline, indent=1))
        return 0

    if args.command == "parse":
        from extant.corpora.rbi.parse_run import parse_all

        try:
            run = parse_all(data_dir, sample=args.sample)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(run)
        return 0

    if args.command == "mine":
        from extant.corpora.rbi.mine import MINER_VERSION, mine

        try:
            summary = mine(data_dir)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
        print(f"labels: {data_dir / 'derived' / 'labels' / f'm{MINER_VERSION}'}")
        return 0

    from extant.corpora.rbi.extract import extract_all
    from extant.corpora.rbi.extract_html import extract_all_html

    # HTML is the source of record; PDFs, when a person has imported any, are extracted too.
    html = extract_all_html(data_dir, sample=args.sample)
    print(html)
    for url in html.no_body:
        print(f"  no body: {url}")
    for failure in html.failed:
        print(f"  failed: {failure}")
    print(extract_all(data_dir, sample=args.sample))
    return 0


def _pdfs(args: argparse.Namespace, data_dir: Path) -> int:
    from extant.corpora.rbi.acquisition import RawCache
    from extant.corpora.rbi.crawl import pdf_targets_from_cache
    from extant.corpora.rbi.importer import import_pdfs, write_download_page

    cache = RawCache(data_dir)
    try:
        targets = pdf_targets_from_cache(cache)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "import-pdfs":
        try:
            report = import_pdfs(
                cache, args.folder.expanduser(), [t.url for t in targets], replace=args.replace
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(report)
        for label, names in (
            ("not a PDF (a saved CAPTCHA or error page?)", report.not_pdf),
            ("matched no target", report.unmatched),
            ("conflict, different bytes already cached", [f for f, _ in report.conflicts]),
        ):
            for name in names:
                print(f"  {label}: {name}")

    missing = [t for t in targets if cache.get(t.url) is None]
    page = data_dir / "derived" / "pdfs_to_download.html"
    write_download_page(
        [(t.url, " | ".join(x for x in (t.category, t.title) if x)) for t in missing], page
    )
    (page.with_suffix(".txt")).write_text("".join(t.url + "\n" for t in missing), encoding="utf-8")
    print(
        f"{len(targets)} PDF targets, {len(targets) - len(missing)} cached, {len(missing)} missing"
    )
    print(f"download page: {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
