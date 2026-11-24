"""The HTTP API: the second entry point to the same graph, after MCP.

It owns transport and the operational guards (the per client rate limit, the cap's
Retry-After, the probes) and nothing else: every route validates, calls one method of the
``Runtime``, and shapes the result.

Two rules shape the contract.

- A refusal is an outcome, not an error. ``POST /ask`` answers 200 whatever the graph
  decides, refusals included, so a client handles a careful refusal as a result. That holds
  for ``cap_reached`` too: the service is still answering, from its cached examples (rung
  four of the ladder), and adds a Retry-After header naming when the cap resets. And when
  Postgres is unreachable the response is the ``unavailable`` outcome with cached examples,
  also 200, for the same reason.
- A rejection is not an outcome. A per client rate limit breach is 429 with an error body
  and no answer, because nothing was attempted.

Every response names the build it came from: in the body, and in the X-Extant-Index-Version
header, which also covers errors.

``POST /ask/stream`` is the same request as ``POST /ask`` as server sent events: one event as
each graph node starts and finishes (``extant.agent.progress``, emitted by the node wrapper,
never on a timer), then the complete ``AskResponse`` once, verified, as the last event. There
is no token stream of the answer text. It is still one HTTP request with no state kept
between requests.

``GET /`` serves the page in ``extant/web``: static files, nothing compiled.

PII is redacted at admission by ``Service.run``, the first thing ``POST /ask`` calls: before
the state is built, before any embedding, model call or log. Nothing in this module reads,
logs or stores the question, and a validation error does not echo it back.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, time, timedelta
from email.utils import format_datetime
from pathlib import Path as FilePath
from typing import Annotated, Any, Final
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import extant
from extant.agent.examples import STATIC_PAGE
from extant.agent.models import AskResponse, Refusal, RefusalReason
from extant.agent.progress import NodeFinished, NodeStarted, Progress, Restarted
from extant.api.models import (
    AskRequest,
    DailyCap,
    EntityTypes,
    ErrorBody,
    ExamplesResponse,
    Health,
    Live,
    ParagraphResponse,
    StreamFailure,
    StreamFinal,
)
from extant.api.ratelimit import RateLimiter, client_address
from extant.api.runtime import Runtime
from extant.api.settings import ApiSettings
from extant.core.identity import PARA_ID_RE

INDIA: Final = ZoneInfo("Asia/Kolkata")
INDEX_HEADER: Final = "X-Extant-Index-Version"
# A build is immutable, so a paragraph or the examples cannot change under a running build;
# fifteen minutes bounds how long a client keeps one after a new build is rolled out (the
# body names its build either way). The answer is never cached: it moves the daily cap.
READ_CACHE: Final = "public, max-age=900"
WEB: Final = FilePath(__file__).resolve().parent.parent / "web"
# The page loads nothing from anywhere else: no fonts, no scripts, no frames.
PAGE_POLICY: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;"
    " connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
STREAM_HEADERS: Final = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
_log = logging.getLogger(__name__)
_END = object()


def sse(event: str, payload: BaseModel, *, exclude_none: bool = False) -> str:
    """One server sent event; the payload is one line of JSON. Only progress events leave
    out empty fields: the final ``AskResponse`` is serialised exactly as ``POST /ask`` sends it."""
    data = payload.model_dump_json(exclude_none=exclude_none)
    return f"event: {event}\ndata: {data}\n\n"


def next_cap_reset(now: datetime) -> datetime:
    """The next midnight in India, when the daily cap's count starts again, in UTC."""
    local = now.astimezone(INDIA)
    return datetime.combine(local.date() + timedelta(days=1), time(0), INDIA).astimezone(UTC)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RateLimitedError(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after


_RETRY_AFTER = {"description": "When to try again.", "schema": {"type": "string"}}
ASK_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "An outcome, always, never an error: `answer`, `refusal`,"
        " `retrieval_only` or `unavailable` (the `result.outcome` discriminator). A refusal"
        " is a successful response that declines, with its reason and the nearest provisions"
        " it would not stand behind. `cap_reached` is a refusal too: the day's answers are"
        " spent, cached examples are offered, and the Retry-After header (an HTTP date) names"
        " the next midnight in India, when the count resets.",
        "headers": {
            "Retry-After": {
                **_RETRY_AFTER,
                "description": "Only on a `cap_reached` refusal: the next midnight in India.",
            }
        },
    },
    429: {
        "model": ErrorBody,
        "description": "Rejected by the per client rate limit before anything was attempted:"
        " no answer body. Not an outcome.",
        "headers": {"Retry-After": {**_RETRY_AFTER, "description": "Seconds to wait."}},
    },
}
STREAM_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "Server sent events. `node_started` and `node_finished` as each graph"
        " node starts and finishes, with what it wrote (candidates held, rows dropped, checks"
        " recorded); `restarted` if the graph runs again after a database failure; then"
        " exactly one of `response` (the `AskResponse` of `POST /ask`, with `retry_after` on"
        " `cap_reached`) or `failure`. The answer text arrives only in `response`.",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    },
    429: ASK_RESPONSES[429],
}


def create_app(
    runtime: Runtime,
    *,
    settings: ApiSettings | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> FastAPI:
    config = settings or ApiSettings()
    ask_limit = RateLimiter(config.ask_per_minute)
    read_limit = RateLimiter(config.reads_per_minute)
    version = runtime.index_version

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        runtime.close()

    app = FastAPI(
        title="Extant",
        version=extant.__version__,
        summary="Verified answers over RBI Master Directions, or a stated refusal.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def name_the_build(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers[INDEX_HEADER] = version
        return response

    @app.exception_handler(RateLimitedError)
    async def rate_limited(request: Request, exc: RateLimitedError) -> JSONResponse:
        body = ErrorBody(
            detail="Too many requests from this address; nothing was attempted.",
            index_version=version,
            retry_after_seconds=exc.retry_after,
        )
        return JSONResponse(
            body.model_dump(mode="json"),
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # The input is not echoed back: a question that failed validation may carry a PAN.
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse({"detail": errors, "index_version": version}, status_code=422)

    def limited(limiter: RateLimiter) -> Callable[[Request], None]:
        def check(request: Request) -> None:
            retry = limiter.hit(client_address(request, config.trusted_proxy_hops))
            if retry is not None:
                raise RateLimitedError(retry)

        return check

    def error(status: int, detail: str) -> JSONResponse:
        return JSONResponse(
            ErrorBody(detail=detail, index_version=version).model_dump(mode="json"),
            status_code=status,
        )

    @app.post(
        "/ask",
        response_model=AskResponse,
        responses=ASK_RESPONSES,
        summary="Ask a question: an answer with verified citations, or a stated refusal",
        dependencies=[Depends(limited(ask_limit))],
    )
    def ask(body: AskRequest, response: Response) -> AskResponse:
        answer = runtime.ask(body.question, body.entity, body.as_of)
        retry = retry_after(answer)
        if retry is not None:
            response.headers["Retry-After"] = retry
        return answer

    def retry_after(answer: AskResponse) -> str | None:
        result = answer.result
        if isinstance(result, Refusal) and result.reason is RefusalReason.CAP_REACHED:
            return format_datetime(next_cap_reset(now()), usegmt=True)
        return None

    @app.post(
        "/ask/stream",
        response_class=StreamingResponse,
        responses=STREAM_RESPONSES,
        summary="Ask, streaming the graph's progress, then the verified answer once",
        dependencies=[Depends(limited(ask_limit))],
    )
    def ask_stream(body: AskRequest) -> StreamingResponse:
        events: queue.Queue[Progress | StreamFinal | StreamFailure | object] = queue.Queue()

        def work() -> None:
            # The graph runs on its own thread and reports into the queue as each node
            # starts and finishes; the response below drains the queue as events arrive.
            try:
                answer = runtime.ask(body.question, body.entity, body.as_of, observe=events.put)
                events.put(StreamFinal(response=answer, retry_after=retry_after(answer)))
            except Exception:
                _log.exception("POST /ask/stream failed")
                events.put(
                    StreamFailure(
                        detail="The request failed; nothing was answered.", index_version=version
                    )
                )
            finally:
                events.put(_END)

        def stream() -> Iterator[str]:
            while (item := events.get()) is not _END:
                if isinstance(item, StreamFinal):
                    yield sse("response", item)
                elif isinstance(item, StreamFailure):
                    yield sse("failure", item)
                elif isinstance(item, NodeStarted | NodeFinished | Restarted):
                    yield sse(item.event, item, exclude_none=True)

        threading.Thread(target=work, name="ask-stream", daemon=True).start()
        return StreamingResponse(stream(), media_type="text/event-stream", headers=STREAM_HEADERS)

    @app.get(
        "/paragraph/{para_id}",
        response_model=ParagraphResponse,
        responses={
            404: {"model": ErrorBody, "description": "Neither a paragraph nor a retired alias."},
            503: {"model": ErrorBody, "description": "The index cannot be reached."},
        },
        summary="Resolve a citation, through para_id_aliases when the id was retired",
        dependencies=[Depends(limited(read_limit))],
    )
    def paragraph(
        response: Response,
        para_id: Annotated[
            str,
            Path(
                pattern=f"^{PARA_ID_RE.pattern}$",
                description="A paragraph id, <doc_id>#<anchor>, with # sent as %23.",
            ),
        ],
    ) -> ParagraphResponse | JSONResponse:
        try:
            found = runtime.paragraph(para_id)
        except psycopg.Error:
            return error(503, "The index cannot be reached; try again shortly.")
        if found is None:
            return error(404, f"No paragraph {para_id} in {version}, and no alias for it.")
        aliased = found.resolved_from is not None
        response.headers["Cache-Control"] = READ_CACHE
        return ParagraphResponse(
            index_version=version,
            requested=para_id,
            resolved_through_alias=aliased,
            note=(
                f"{para_id} was retired ({found.resolution_reason}); this is {found.para_id},"
                " the paragraph that replaced it. Cite the new id."
                if aliased
                else None
            ),
            paragraph=found,
        )

    @app.get(
        "/entity_types",
        response_model=EntityTypes,
        responses={503: {"model": ErrorBody, "description": "The index cannot be reached."}},
        summary="The build's entity types, for the question's entity selector",
        dependencies=[Depends(limited(read_limit))],
    )
    def entity_types(response: Response) -> EntityTypes | JSONResponse:
        try:
            found = runtime.entity_types()
        except psycopg.Error:
            return error(503, "The index cannot be reached; try again shortly.")
        response.headers["Cache-Control"] = READ_CACHE
        return EntityTypes(index_version=version, entity_types=found)

    @app.get(
        "/examples",
        response_model=ExamplesResponse,
        summary="Cached example answers: no model call, no database",
        dependencies=[Depends(limited(read_limit))],
    )
    def examples(response: Response) -> ExamplesResponse:
        response.headers["Cache-Control"] = READ_CACHE
        cache = runtime.cache
        if cache is None:
            return ExamplesResponse(
                index_version=version,
                examples_index_version=None,
                stale=False,
                built_on=None,
                message=STATIC_PAGE,
                examples=[],
            )
        stale = cache.stale(version)
        message = "Answers Extant gave earlier to example questions."
        if stale:
            message += (
                f" They were answered on build {cache.index_version}; this service runs"
                f" {version}, so an example may cite text that has since changed."
            )
        return ExamplesResponse(
            index_version=version,
            examples_index_version=cache.index_version,
            stale=stale,
            built_on=cache.built_on,
            message=message,
            examples=list(cache.examples),
        )

    @app.get(
        "/health",
        response_model=Health,
        responses={503: {"model": Health, "description": "Not ready: never route traffic."}},
        summary="Readiness: the manifest check, a database query, the daily cap left",
    )
    def health() -> Health | JSONResponse:
        try:
            ready = runtime.readiness()
        except psycopg.Error as exc:
            down = Health(
                status="not_ready",
                index_version=version,
                database="unreachable",
                manifest_ok=False,
                problems=[f"the database cannot be reached ({type(exc).__name__})"],
                warnings=[],
                daily_cap=None,
            )
            return JSONResponse(down.model_dump(mode="json"), status_code=503)
        day = now().astimezone(INDIA).date()
        try:
            answered = runtime.answered(day)
            cap: DailyCap | None = DailyCap(
                day=day,
                cap=runtime.cap,
                answered=answered,
                remaining=max(0, runtime.cap - answered),
            )
        except psycopg.Error:
            cap = None
        report = Health(
            status="ready" if ready.ready else "not_ready",
            index_version=version,
            database="reachable",
            manifest_ok=ready.ready,
            problems=ready.blocking,
            warnings=ready.warnings,
            daily_cap=cap,
        )
        if not ready.ready:
            return JSONResponse(report.model_dump(mode="json"), status_code=503)
        return report

    @app.get(
        "/health/live",
        response_model=Live,
        summary="Liveness: the process answers. Never touches the database",
    )
    def live() -> Live:
        return Live(index_version=version)

    @app.get("/", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(
            WEB / "index.html",
            headers={"Content-Security-Policy": PAGE_POLICY, "Cache-Control": "no-cache"},
        )

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
