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

PII is redacted at admission by ``Service.run``, the first thing ``POST /ask`` calls: before
the state is built, before any embedding, model call or log. Nothing in this module reads,
logs or stores the question, and a validation error does not echo it back.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, time, timedelta
from email.utils import format_datetime
from typing import Annotated, Any, Final
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import extant
from extant.agent.examples import STATIC_PAGE
from extant.agent.models import AskResponse, Refusal, RefusalReason
from extant.api.models import (
    AskRequest,
    DailyCap,
    ErrorBody,
    ExamplesResponse,
    Health,
    Live,
    ParagraphResponse,
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
        result = answer.result
        if isinstance(result, Refusal) and result.reason is RefusalReason.CAP_REACHED:
            response.headers["Retry-After"] = format_datetime(next_cap_reset(now()), usegmt=True)
        return answer

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

    return app
