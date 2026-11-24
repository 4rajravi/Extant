"""The page, driven in Chromium.

The two smoke tests are the gate: a question answered with citations, one expanded to show
the quoted span highlighted inside its paragraph; and an example opened from the cache with
no model call. The rest hold the page to its boundary: a refusal renders as an outcome, the
dropped list is visible without any interaction, the progress panel moves only on real
events, and no date on the page differs from a date in what the API returned.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from extant.agent.examples import ExampleCache
from extant.agent.models import AskResponse, Refusal, RefusalReason
from extant.agent.nodes.finalize import _NEAREST
from extant.agent.progress import NodeStarted, Observe, Progress
from tests import replay_support
from tests.api_support import FakeRuntime, recorded_paragraphs, spec

pytestmark = pytest.mark.web

FULL = "full_answer_third_person"
DROPPING = "adv_twin_entity"  # an answer with four paragraphs set aside
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def ask(page: Page, base: str, fixture: str) -> None:
    s = spec(fixture)
    page.goto(base)
    page.fill("#question", s["question"])
    if s["entity"]:
        expect(page.locator(f'#entity option[value="{s["entity"]}"]')).to_have_count(1)
        page.select_option("#entity", s["entity"])
    if s["as_of"]:
        page.fill("#as-of", s["as_of"])
    page.click("#ask-button")


# --------------------------------------------------------------------------- the gate


def test_a_question_is_answered_with_citations_and_the_quote_highlighted(
    page: Page, serve: Callable[[Any], str]
) -> None:
    runtime = FakeRuntime(FULL)
    ask(page, serve(runtime), FULL)
    result = page.locator("#result-body")
    expect(result).to_have_attribute("data-outcome", "answer")
    expect(result.locator(".claim")).to_have_count(4)
    first = result.locator(".citation").first
    first.locator(".cite").click()
    expect(first.locator(".cite").first).to_have_attribute("aria-expanded", "true")
    mark = first.locator(".provision mark")
    expect(mark).to_be_visible()
    expect(first).to_have_attribute("data-highlight", "located")

    # The highlight is exactly the cited span of the stored paragraph, inside its full text.
    response = runtime_answer(runtime)
    cited = response.result.claims[0].citations[0]  # type: ignore[union-attr]
    body = recorded_paragraphs(FULL)[cited.para_id].body
    assert mark.inner_text() == body[cited.quote_start : cited.quote_end]
    assert first.locator(".provision").inner_text() == body


def test_an_example_shows_its_cached_answer_with_no_model_call(
    page: Page, serve: Callable[[Any], str]
) -> None:
    runtime = FakeRuntime(FULL)
    asked: list[str] = []
    page.on("request", lambda r: asked.append(r.url) if "/ask" in r.url else None)
    page.goto(serve(runtime))
    first = ExampleCache.load().examples[0]  # type: ignore[union-attr]
    page.get_by_role("button", name=first.question).click()
    result = page.locator("#result-body")
    expect(result).to_have_attribute("data-outcome", "cached_example")
    expect(result.locator(".claim")).to_have_count(len(first.answer.claims))
    expect(result).to_contain_text(first.answer.claims[0].text)
    expect(page.locator("#progress-now")).to_contain_text("no model was called")
    assert asked == []  # no request to /ask or /ask/stream
    assert runtime.asked == []
    assert runtime.seen.prompts == []


# --------------------------------------------------------------------------- refusals


def _citations() -> list[Any]:
    answer = runtime_answer(FakeRuntime(FULL))
    return [c for claim in answer.result.claims for c in claim.citations]  # type: ignore[union-attr]


class Canned(FakeRuntime):
    """Answers every question with one prepared response, through the real stream."""

    def __init__(self, response: AskResponse) -> None:
        super().__init__(FULL)
        self.canned = response

    def ask(self, question: str, entity: Any, as_of: Any, observe: Observe | None = None) -> Any:
        return self.canned


@pytest.mark.parametrize("reason", list(RefusalReason))
def test_each_refusal_renders_as_an_outcome_not_an_error(
    page: Page, serve: Callable[[Any], str], reason: RefusalReason
) -> None:
    examples = list(ExampleCache.load().examples) if reason is RefusalReason.CAP_REACHED else []  # type: ignore[union-attr]
    nearest = _citations()[:2] if reason in _NEAREST else []
    message = f"What Extant says when it refuses for {reason.value}."
    response = AskResponse(
        result=Refusal(reason=reason, message=message, nearest=nearest, examples=examples),
        as_of=date(2026, 11, 20),
        entity_type="cb",
        index_version=FakeRuntime().index_version,
        retrieved=[],
        dropped=[],
        degraded=["cached_examples"] if examples else [],
        cost_eur=0.0,
        latency_ms=3,
    )
    ask(page, serve(Canned(response)), FULL)
    result = page.locator("#result-body")
    expect(result).to_have_attribute("data-outcome", "refusal")
    expect(result).to_have_attribute("data-reason", reason.value)
    expect(result).to_have_class(re.compile(r"\bis-declined\b"))
    expect(result.locator(".prose")).to_have_text(message)  # the message is the content
    expect(result.locator(".reason-code")).to_have_text(f"reason: {reason.value}")
    expect(page.get_by_role("alert")).to_have_count(0)
    expect(result).not_to_contain_text("Nothing was answered")
    if nearest:
        expect(result.get_by_text("Found, but not relied on")).to_be_visible()
        expect(result.locator(".citation")).to_have_count(len(nearest))
    if examples:
        expect(result.locator(".example")).to_have_count(len(examples))
        result.locator(".example").first.click()  # the cached examples still work
        expect(result).to_have_attribute("data-outcome", "cached_example")


# --------------------------------------------------------------------------- dropped, progress


def test_the_dropped_list_is_visible_without_any_interaction(
    page: Page, serve: Callable[[Any], str]
) -> None:
    runtime = FakeRuntime(DROPPING)
    ask(page, serve(runtime), DROPPING)
    expect(page.locator("#result-body")).to_have_attribute("data-outcome", "answer")
    rows = page.locator("#dropped-list .dropped-row")
    dropped = runtime_answer(runtime).dropped
    expect(rows).to_have_count(len(dropped))
    for row, d in zip(rows.all(), dropped, strict=True):
        expect(row).to_be_visible()
        expect(row).to_contain_text(d.para_id)
        expect(row).to_contain_text(d.reason)
        expect(row).to_contain_text(d.detail)
    # The retrieved list keeps the response's order: nothing is sorted on the page.
    shown = page.locator("#retrieved-list .retrieved-id").all_inner_texts()
    assert shown == [r.para_id for r in runtime_answer(runtime).retrieved]


class Gated(FakeRuntime):
    """Replays a fixture's real events, holding each node open until the test releases it."""

    def __init__(self, fixture: str) -> None:
        super().__init__(fixture)
        self.events: list[Progress] = []
        data = replay_support.load(fixture)
        self.response = replay_support.serve(data, observe=self.events.append).response
        self.release = threading.Semaphore(0)

    def ask(self, question: str, entity: Any, as_of: Any, observe: Observe | None = None) -> Any:
        assert observe is not None
        for event in self.events:
            observe(event)
            if isinstance(event, NodeStarted) and not self.release.acquire(timeout=15):
                raise TimeoutError("the test never released the node")
        return self.response


def test_the_progress_panel_moves_only_when_an_event_arrives(
    page: Page, serve: Callable[[Any], str]
) -> None:
    runtime = Gated(FULL)
    ask(page, serve(runtime), FULL)
    steps = page.locator("#progress-steps .step")

    expect(page.locator('.step[data-node="parse"]')).to_have_attribute("data-state", "running")
    expect(page.locator("#progress-now")).to_have_text("Reading the question…")
    page.wait_for_timeout(800)  # held: nothing advances while the node runs
    expect(steps).to_have_count(1)
    expect(page.locator("#result-body")).to_have_attribute("data-outcome", "none")

    runtime.release.release()
    expect(page.locator('.step[data-node="parse"]')).to_have_attribute("data-state", "done")
    expect(page.locator('.step[data-node="parse"]')).to_contain_text("route full")
    expect(page.locator('.step[data-node="retrieve"]')).to_have_attribute("data-state", "running")
    page.wait_for_timeout(500)
    expect(steps).to_have_count(2)

    runtime.release.release()
    expect(page.locator('.step[data-node="retrieve"]')).to_contain_text("6 paragraphs retrieved")
    for _ in range(7):
        runtime.release.release()
    expect(page.locator("#result-body")).to_have_attribute("data-outcome", "answer")
    expect(steps).to_have_count(9)
    expect(page.locator("#checks-list .check")).not_to_have_count(0)


# --------------------------------------------------------------------------- dates


def _dates_in(value: Any) -> set[str]:
    return set(DATE.findall(json.dumps(value)))


def test_no_date_on_the_page_differs_from_a_date_the_api_returned(
    page: Page, serve: Callable[[Any], str]
) -> None:
    runtime = FakeRuntime(DROPPING)
    base = serve(runtime)
    returned: set[str] = set()

    def keep(response: Any) -> None:
        if (
            response.url.startswith(base)
            and "/static/" not in response.url
            and response.url != f"{base}/"
        ):
            returned.update(DATE.findall(response.text()))

    page.on("response", keep)
    ask(page, base, DROPPING)
    expect(page.locator("#result-body")).to_have_attribute("data-outcome", "answer")
    for button in page.locator("#result-body .cite").all():
        button.click()
    expect(page.locator("#result-body .provision").first).to_be_visible()
    page.wait_for_load_state("networkidle")
    shown = set(DATE.findall(page.locator("body").inner_text()))
    assert shown  # the page does show dates: as of, in force from
    assert shown <= returned, shown - returned


def runtime_answer(runtime: FakeRuntime) -> AskResponse:
    """The response the runtime gives for its own fixture, asked directly."""
    s = spec(runtime.fixture)
    return FakeRuntime(runtime.fixture).ask(
        s["question"], s["entity"], date.fromisoformat(s["as_of"]) if s["as_of"] else None
    )


# --------------------------------------------------------------------------- citation edges


def _answer_citing(citation: Any) -> AskResponse:
    answer = runtime_answer(FakeRuntime(FULL))
    claim = answer.result.claims[0].model_copy(update={"citations": [citation]})  # type: ignore[union-attr]
    result = answer.result.model_copy(update={"claims": [claim]})
    return answer.model_copy(update={"result": result})


def test_offsets_outside_the_paragraph_are_not_highlighted_and_say_so(
    page: Page, serve: Callable[[Any], str]
) -> None:
    cited = _citations()[0]
    body = recorded_paragraphs(FULL)[cited.para_id].body
    wrong = cited.model_copy(update={"quote_start": 3, "quote_end": len(body) + 40})
    ask(page, serve(Canned(_answer_citing(wrong))), FULL)
    citation = page.locator("#result-body .citation").first
    citation.locator(".cite").click()
    expect(citation).to_have_attribute("data-highlight", "not_located")
    expect(citation.locator(".provision mark")).to_have_count(0)
    expect(citation.locator(".provision")).to_have_text(body)
    expect(citation.locator(".cite-status")).to_contain_text("could not be located")


def test_a_citation_resolved_through_an_alias_says_the_identifier_moved(
    page: Page, serve: Callable[[Any], str]
) -> None:
    cited = _citations()[0]
    retired = "rbi:md:2025:0387:v1#p77"
    runtime = Canned(_answer_citing(cited.model_copy(update={"para_id": retired})))
    runtime.aliases[retired] = cited.para_id
    ask(page, serve(runtime), FULL)
    citation = page.locator("#result-body .citation").first
    citation.locator(".cite").click()
    status = citation.locator(".cite-status")
    expect(status).to_contain_text(f"{retired} was retired")
    expect(status).to_contain_text(f"this is {cited.para_id}")
    expect(citation).to_have_attribute("data-highlight", "not_located")
    expect(citation.locator(".provision mark")).to_have_count(0)


# --------------------------------------------------------------------------- layout


@pytest.mark.parametrize("width", [390, 1280])
def test_nothing_moves_when_the_answer_arrives(
    page: Page, serve: Callable[[Any], str], width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(reduced_motion="reduce")  # the scroll to the answer is then instant
    runtime = Gated(FULL)
    ask(page, serve(runtime), FULL)
    page.evaluate(
        """() => {
          window.__shift = 0;
          new PerformanceObserver((list) => {
            for (const e of list.getEntries()) if (!e.hadRecentInput) window.__shift += e.value;
          }).observe({ type: "layout-shift", buffered: false });
        }"""
    )
    for _ in range(8):
        runtime.release.release()
    expect(page.locator('.step[data-node="finalize"]')).to_have_attribute("data-state", "running")
    watched = page.locator("#progress")

    def placed() -> tuple[float, float, float]:
        box = watched.bounding_box()
        assert box is not None
        return box["x"], box["y"], box["width"]  # it may grow downwards; it may not move

    before = placed()
    page.wait_for_timeout(700)  # past the browser's 500 ms grace after input
    runtime.release.release()
    expect(page.locator("#result-body")).to_have_attribute("data-outcome", "answer")
    page.wait_for_timeout(200)
    assert placed() == before  # the panel being watched stays put
    assert page.evaluate("window.__shift") == 0
    page.locator("#result-body .cite").first.click()
    expect(page.locator("#result-body .provision mark").first).to_be_visible()
    wide = "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    assert not page.evaluate(wide)  # the page never scrolls sideways; a table may, inside itself
