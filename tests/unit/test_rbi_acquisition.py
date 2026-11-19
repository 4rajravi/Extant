"""Acquisition and crawl, entirely offline against an httpx.MockTransport."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from extant.corpora.rbi import sources
from extant.corpora.rbi.acquisition import (
    MAX_ATTEMPTS,
    ChallengeError,
    CrawlBlockedError,
    Fetcher,
    HostBlockedError,
    RateLimiter,
    RawCache,
)
from extant.corpora.rbi.crawl import crawl
from extant.corpora.rbi.settings import RbiSettings

CHALLENGE = (
    b"<html><body>Please enable JavaScript to view the page content. This question is for"
    b" testing whether you are a human visitor. What code is in the image?</body></html>"
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Recorder:
    """A transport that answers from a handler and records every request it sees."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response], clock: FakeClock):
        self.handler = handler
        self.clock = clock
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(self.clock.now)
        return self.handler(request)


def make_fetcher(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response]
) -> tuple[Fetcher, Recorder, FakeClock]:
    clock = FakeClock()
    recorder = Recorder(handler, clock)
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    limiter = RateLimiter(2.0, sleep=clock.sleep, clock=clock.clock)
    return Fetcher(RawCache(tmp_path / "rbi"), client, limiter), recorder, clock


def ok(body: bytes = b"<html>ok</html>", content_type: str = "text/html") -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": content_type, "etag": '"e1"'})


# --------------------------------------------------------------------------- fetcher


def test_cached_url_is_never_fetched_again(tmp_path: Path) -> None:
    fetcher, recorder, _ = make_fetcher(tmp_path, lambda r: ok())
    first = fetcher.get("https://www.rbi.org.in/a")
    second = fetcher.get("https://www.rbi.org.in/a")
    assert first == second
    assert len(recorder.requests) == 1
    assert fetcher.cache.read_bytes(first) == b"<html>ok</html>"  # type: ignore[arg-type]


def test_manifest_has_one_row_per_fetch_with_the_required_fields(tmp_path: Path) -> None:
    fetcher, _, _ = make_fetcher(tmp_path, lambda r: ok(b"abc"))
    fetcher.get("https://www.rbi.org.in/a")
    fetcher.get("https://www.rbi.org.in/a")  # cache hit, not a fetch
    [row] = list(fetcher.cache.manifest())
    assert row.url == "https://www.rbi.org.in/a"
    assert (row.outcome, row.status, row.etag, row.bytes) == ("fetched", 200, '"e1"', 3)
    assert row.source_sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert row.fetched_at


def test_requests_are_at_least_two_seconds_apart(tmp_path: Path) -> None:
    fetcher, recorder, _ = make_fetcher(tmp_path, lambda r: ok())
    for i in range(5):
        fetcher.get(f"https://www.rbi.org.in/{i}")
    gaps = [b - a for a, b in zip(recorder.times, recorder.times[1:], strict=False)]
    assert len(gaps) == 4
    assert all(gap >= 2.0 for gap in gaps)


def test_each_redirect_hop_is_rate_limited(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://www.rbi.org.in/new"})
        return ok()

    fetcher, recorder, _ = make_fetcher(tmp_path, handler)
    entry = fetcher.get("https://www.rbi.org.in/old")
    assert entry is not None
    assert entry.final_url == "https://www.rbi.org.in/new"
    assert len(recorder.requests) == 2
    assert recorder.times[1] - recorder.times[0] >= 2.0


def test_5xx_is_retried_with_backoff_then_succeeds(tmp_path: Path) -> None:
    answers = iter([httpx.Response(503), httpx.Response(502), ok()])
    fetcher, recorder, _ = make_fetcher(tmp_path, lambda r: next(answers))
    entry = fetcher.get("https://www.rbi.org.in/flaky")
    assert entry is not None
    assert len(recorder.requests) == 3
    [row] = list(fetcher.cache.manifest())
    assert row.attempts == 3
    gaps = [b - a for a, b in zip(recorder.times, recorder.times[1:], strict=False)]
    assert gaps[1] > gaps[0] > 2.0  # exponential: each wait longer than the last


def test_gives_up_after_three_attempts_and_records_the_failure(tmp_path: Path) -> None:
    fetcher, recorder, _ = make_fetcher(tmp_path, lambda r: httpx.Response(500))
    assert fetcher.get("https://www.rbi.org.in/down") is None
    assert len(recorder.requests) == MAX_ATTEMPTS == 3
    [row] = list(fetcher.cache.manifest())
    assert (row.outcome, row.status, row.attempts) == ("failed", 500, 3)
    assert fetcher.cache.get("https://www.rbi.org.in/down") is None  # retried next run


def test_transport_errors_are_retried_and_recorded(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    fetcher, recorder, _ = make_fetcher(tmp_path, handler)
    assert fetcher.get("https://www.rbi.org.in/slow") is None
    assert len(recorder.requests) == 3
    [row] = list(fetcher.cache.manifest())
    assert row.outcome == "failed"
    assert "ConnectTimeout" in (row.error or "")


def test_a_challenge_page_is_never_cached_and_blocks_the_host(tmp_path: Path) -> None:
    fetcher, recorder, _ = make_fetcher(tmp_path, lambda r: ok(CHALLENGE))
    with pytest.raises(ChallengeError):
        fetcher.get("https://rbidocs.rbi.org.in/a.pdf", expect="application/pdf")
    assert fetcher.cache.get("https://rbidocs.rbi.org.in/a.pdf") is None
    [row] = list(fetcher.cache.manifest())
    assert row.outcome == "blocked"
    with pytest.raises(HostBlockedError):
        fetcher.get("https://rbidocs.rbi.org.in/b.pdf")
    assert len(recorder.requests) == 1  # the second URL never reached the network


def test_unexpected_content_type_is_not_cached(tmp_path: Path) -> None:
    fetcher, _, _ = make_fetcher(tmp_path, lambda r: ok(b"<html>not a pdf</html>"))
    assert fetcher.get("https://example.org/x.pdf", expect="application/pdf") is None
    assert fetcher.cache.get("https://example.org/x.pdf") is None


def test_repeated_refusals_stop_the_crawl(tmp_path: Path) -> None:
    fetcher, recorder, _ = make_fetcher(tmp_path, lambda r: httpx.Response(418))
    for i in range(2):
        assert fetcher.get(f"https://www.rbi.org.in/{i}") is None
    with pytest.raises(CrawlBlockedError, match="refused 3 requests in a row"):
        fetcher.get("https://www.rbi.org.in/2")
    assert len(recorder.requests) == 3


def test_force_refetches_conditionally(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == '"e1"':
            return httpx.Response(304)
        return ok()

    fetcher, recorder, _ = make_fetcher(tmp_path, handler)
    first = fetcher.get("https://www.rbi.org.in/a")
    again = fetcher.get("https://www.rbi.org.in/a", force=True)
    assert again == first
    assert recorder.requests[1].headers["if-none-match"] == '"e1"'
    assert [r.outcome for r in fetcher.cache.manifest()] == ["fetched", "not_modified"]


def test_user_agent_names_the_project_and_contact(tmp_path: Path) -> None:
    s = RbiSettings(crawl_contact="crawler@example.org", _env_file=None)  # type: ignore[call-arg]
    assert s.user_agent.startswith("Extant/")
    assert "crawler@example.org" in s.user_agent


@pytest.mark.parametrize("interval", [0.0, 0.5, 1.99])
def test_politeness_floor_cannot_be_lowered(interval: float) -> None:
    with pytest.raises(ValueError, match="request interval below"):
        RateLimiter(interval)
    with pytest.raises(ValueError, match="greater than or equal to 2"):
        RbiSettings(
            crawl_contact="crawler@example.org",
            request_interval_seconds=interval,
            _env_file=None,  # type: ignore[call-arg]
        )


def test_contact_must_be_an_email_or_url() -> None:
    with pytest.raises(ValueError, match="email address or an http"):
        RbiSettings(crawl_contact="nobody", _env_file=None)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- whole crawl

LISTING = b"""<html><body><table class="tablebg">
<tr><td class="tableheader">Small Finance Banks</td></tr>
<tr><td class="tableheader">Nov 28, 2025</td></tr>
<tr><td><a href="BS_ViewMasDirections.aspx?id=13105">Reserve Bank of India (Small Finance Banks -
 Miscellaneous) Directions, 2025</a></td>
<td><a href="https://rbidocs.rbi.org.in/rdocs/notification/PDFs/204MD.PDF">1440 kb</a></td></tr>
</table></body></html>"""


def site(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.lower().endswith(".pdf"):
        return ok(b"%PDF-1.7 fake", "application/pdf")
    if "BS_ViewMasterDirections.aspx?did=" in url:
        return ok(LISTING)
    if url == sources.WITHDRAWN_CIRCULARS:
        return ok(b'<a href="https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13110">x</a>')
    if url == sources.WITHDRAWAL_NOTIFICATION:
        return ok(b'<a href="https://rbidocs.rbi.org.in/rdocs/notification/PDFs/100NT.PDF">p</a>')
    return ok()


def test_second_crawl_run_makes_no_network_calls(tmp_path: Path) -> None:
    data = tmp_path / "rbi"
    fetcher, recorder, _ = make_fetcher(tmp_path, site)
    first = crawl(fetcher, data, log=lambda _: None)
    assert first.network_requests == len(recorder.requests) > 0
    assert {r.direction_id for r in first.listing_rows} == {13105}

    second_fetcher, second_recorder, _ = make_fetcher(tmp_path, site)
    second = crawl(second_fetcher, data, log=lambda _: None)
    assert second_recorder.requests == []
    assert second.network_requests == 0
    assert all(stage.fetched == 0 for stage in second.stages)


def test_crawl_skips_a_host_that_served_a_challenge_on_later_runs(tmp_path: Path) -> None:
    def challenging(request: httpx.Request) -> httpx.Response:
        if request.url.host == "rbidocs.rbi.org.in":
            return ok(CHALLENGE)
        return site(request)

    data = tmp_path / "rbi"
    fetcher, recorder, _ = make_fetcher(tmp_path, challenging)
    result = crawl(fetcher, data, log=lambda _: None)
    pdf_stage = next(s for s in result.stages if s.name == "pdfs")
    assert pdf_stage.fetched == 0
    assert pdf_stage.skipped_blocked
    assert sum(r.url.host == "rbidocs.rbi.org.in" for r in recorder.requests) == 1
    assert "rbidocs.rbi.org.in" in result.blocked_hosts

    again, again_recorder, _ = make_fetcher(tmp_path, challenging)
    crawl(again, data, log=lambda _: None)
    assert not any(r.url.host == "rbidocs.rbi.org.in" for r in again_recorder.requests)
