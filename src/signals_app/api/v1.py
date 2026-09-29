"""Versioned integration API — ``/v1``.

Built for machine consumers (nuwrrrld-portal's council grounding, the mobile
RAG council's vector store, agents, scripts) rather than this repo's own UI:

* **Typed responses** — every route declares a response model, so
  ``/openapi.json`` generates usable TypeScript / Python clients.
* **Batch-first** — one request for a basket, partial success is a normal
  200 carrying both ``ok`` and ``failed``.
* **LLM-ready** — ``/brief`` returns a deterministic, number-first prompt block;
  ``/rag/documents`` returns upsert-ready documents (``shape=chroma`` is exactly
  the kwargs of ``collection.upsert``).
* **Uniform errors** — ``{"error": {"type", "message"}}`` with the status from
  :data:`ERROR_STATUS`, plus the request id header on every response.
* **Optional shared-secret auth** — when ``SIGNALS_API_KEY`` is set, every
  route except ``/v1/health`` and ``/v1/meta`` requires
  ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.

The unversioned routes in ``routes.py`` are left byte-for-byte compatible.
"""
from __future__ import annotations

import hmac
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from signals_app import service
from signals_app.api.serializers import (
    backtest_to_dict,
    bucket_to_dict,
    failure_to_dict,
    scan_to_dict,
)
from signals_app.config import (
    BACKTEST_FORWARD_HORIZON_DAYS,
    DEFAULT_PERIOD,
    MAX_API_BATCH_SYMBOLS,
    MAX_API_LLM_BATCH_SYMBOLS,
    MAX_MANUAL_SCAN_SYMBOLS,
    SIGNALS_APP_CODE_VERSION,
    VALID_PERIODS,
    get_api_key,
)
from signals_app.schemas.signal_output import SignalOutput

API_VERSION = "1"

def _cache_control(max_age: int) -> str:
    """Cache-Control for a /v1 response, scoped private when API-key auth is on.

    A shared/CDN cache must not reuse a response across callers with
    different API keys, so authenticated responses are marked private.
    """
    scope = "private" if get_api_key() is not None else "public"
    return f"{scope}, max-age={max_age}"

ERROR_STATUS: dict[type[Exception], int] = {
    service.InvalidPeriod: 400,
    service.InsufficientData: 422,
    service.SymbolNotFound: 404,
    service.UpstreamUnavailable: 503,
}


class ApiError(Exception):
    """An error raised by a /v1 route that is not a service domain error."""

    def __init__(self, status: int, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.message = message


def status_for(exc: Exception) -> int:
    """HTTP status for a service domain exception (500 for anything unmapped)."""
    for exc_type, status in ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            return status
    return 500


def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Enforce ``SIGNALS_API_KEY`` when configured; a no-op when it is unset."""
    expected = get_api_key()
    if expected is None:
        return
    supplied = x_api_key
    if supplied is None and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[len("bearer "):].strip()
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise ApiError(401, "Unauthorized", "missing or invalid API key")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

Period = Literal[tuple(VALID_PERIODS)]  # type: ignore[valid-type]


class HitRateBucketOut(BaseModel):
    key: str
    hits: int
    total: int
    hit_rate: float


class BacktestOut(BaseModel):
    symbol: str
    period: str
    horizon_days: int
    bars_scanned: int
    by_category: list[HitRateBucketOut]
    by_strength: list[HitRateBucketOut]


class BatchFailureOut(BaseModel):
    symbol: str
    error_type: str
    message: str


class SymbolsRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=MAX_API_BATCH_SYMBOLS)
    period: Period = DEFAULT_PERIOD  # type: ignore[valid-type]


class SignalsBatchRequest(SymbolsRequest):
    no_llm: bool = Field(
        default=True,
        description=(
            "LLM synthesis costs a model call per symbol; "
            f"capped at {MAX_API_LLM_BATCH_SYMBOLS} symbols."
        ),
    )


class SignalsBatchOut(BaseModel):
    ok: list[SignalOutput]
    failed: list[BatchFailureOut]
    partial: bool


class BacktestBatchRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=MAX_API_BATCH_SYMBOLS)
    period: Period = "2y"  # type: ignore[valid-type]
    horizon_days: int = Field(default=BACKTEST_FORWARD_HORIZON_DAYS, ge=1, le=60)


class BacktestBatchOut(BaseModel):
    symbols_ok: list[str]
    failed: list[BatchFailureOut]
    horizon_days: int
    by_category: list[HitRateBucketOut]
    by_strength: list[HitRateBucketOut]


class BriefOut(BaseModel):
    ticker: str
    period: str
    generated_at: str
    text: str = Field(description="Deterministic, number-first block to paste into a prompt.")
    signal: SignalOutput
    backtest: BacktestOut | None
    omitted: list[str]


class BriefsRequest(SymbolsRequest):
    include_backtest: bool = True


class BriefsOut(BaseModel):
    ok: list[BriefOut]
    failed: list[BatchFailureOut]


class RagDocumentOut(BaseModel):
    id: str
    text: str
    metadata: dict[str, str | int | float | bool]


class RagDocumentsOut(BaseModel):
    documents: list[RagDocumentOut]
    failed: list[BatchFailureOut]


class ChromaUpsertOut(BaseModel):
    """Exactly the kwargs of ChromaDB ``collection.upsert(ids=, documents=, metadatas=)``."""

    ids: list[str]
    documents: list[str]
    metadatas: list[dict[str, str | int | float | bool]]
    failed: list[BatchFailureOut]


class DetectorOut(BaseModel):
    name: str
    category: str
    description: str
    calibrated_hit_rate: float | None


class HealthOut(BaseModel):
    ok: bool
    yfinance_ok: bool
    llm_provider: str
    llm_configured: bool
    supabase_configured: bool
    code_version: str
    detail: dict[str, str]


class MetaOut(BaseModel):
    api_version: str
    code_version: str
    schema_version: str
    valid_periods: list[str]
    default_period: str
    auth_required: bool
    limits: dict[str, int]


class HistoryPageOut(BaseModel):
    symbol: str
    items: list[dict[str, Any]]
    limit: int
    offset: int
    next_offset: int | None


class ScanRequestV1(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=MAX_MANUAL_SCAN_SYMBOLS)
    period: Period = DEFAULT_PERIOD  # type: ignore[valid-type]
    dry_run: bool = False
    compute_matrix: bool = False


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

public = APIRouter(prefix="/v1", tags=["v1"])
protected = APIRouter(prefix="/v1", tags=["v1"], dependencies=[Depends(require_api_key)])


def _brief_out(b: service.TickerBrief) -> BriefOut:
    return BriefOut(
        ticker=b.ticker,
        period=b.period,
        generated_at=b.generated_at,
        text=b.text,
        signal=b.signal,
        backtest=BacktestOut(**backtest_to_dict(b.backtest)) if b.backtest else None,
        omitted=b.omitted,
    )


@public.get("/meta", response_model=MetaOut, summary="Versions, limits, and whether auth is on")
async def meta() -> MetaOut:
    return MetaOut(
        api_version=API_VERSION,
        code_version=SIGNALS_APP_CODE_VERSION,
        schema_version=SignalOutput.model_fields["schema_version"].default,
        valid_periods=list(VALID_PERIODS),
        default_period=DEFAULT_PERIOD,
        auth_required=get_api_key() is not None,
        limits={
            "batch_symbols": MAX_API_BATCH_SYMBOLS,
            "llm_batch_symbols": MAX_API_LLM_BATCH_SYMBOLS,
            "scan_symbols": MAX_MANUAL_SCAN_SYMBOLS,
        },
    )


@public.get(
    "/health",
    response_model=HealthOut,
    summary="Deep health: probes yfinance, reports LLM / Supabase configuration",
    responses={503: {"model": HealthOut}},
)
async def health(response: Response) -> HealthOut:
    report = await service.health()
    if not report.ok:
        response.status_code = 503
    return HealthOut(
        ok=report.ok,
        yfinance_ok=report.yfinance_ok,
        llm_provider=report.llm_provider,
        llm_configured=report.llm_configured,
        supabase_configured=report.supabase_configured,
        code_version=report.code_version,
        detail=report.detail,
    )


@protected.get("/detectors", response_model=list[DetectorOut], summary="Registered detectors")
async def detectors() -> list[DetectorOut]:
    return [
        DetectorOut(
            name=d.name,
            category=d.category,
            description=d.description,
            calibrated_hit_rate=d.calibrated_hit_rate,
        )
        for d in await service.detectors()
    ]


@protected.get(
    "/signals/{symbol}",
    response_model=SignalOutput,
    summary="Full pipeline signal, including the deterministic `state` block",
)
async def signal(
    symbol: str,
    response: Response,
    period: Period = Query(default=DEFAULT_PERIOD),  # type: ignore[valid-type]
    no_llm: bool = Query(default=False),
) -> SignalOutput:
    result = await service.analyze(symbol, period, no_llm=no_llm)
    response.headers["Cache-Control"] = _cache_control(300)
    return result


@protected.post(
    "/signals/batch",
    response_model=SignalsBatchOut,
    summary="Signals for a basket; partial success returns 200 with `failed` populated",
)
async def signals_batch(body: SignalsBatchRequest) -> SignalsBatchOut:
    if not body.no_llm and len(body.symbols) > MAX_API_LLM_BATCH_SYMBOLS:
        raise ApiError(
            400,
            "BatchTooLarge",
            f"no_llm=false is limited to {MAX_API_LLM_BATCH_SYMBOLS} symbols per request",
        )
    result = await service.analyze_many(body.symbols, body.period, no_llm=body.no_llm)
    return SignalsBatchOut(
        ok=result.ok,
        failed=[BatchFailureOut(**failure_to_dict(f)) for f in result.failed],
        partial=result.partial,
    )


@protected.get(
    "/backtest/{symbol}",
    response_model=BacktestOut,
    summary="Historical hit-rates for one symbol (cached ~6h per instance)",
)
async def backtest(
    symbol: str,
    response: Response,
    period: Period = Query(default="2y"),  # type: ignore[valid-type]
    horizon_days: int = Query(default=BACKTEST_FORWARD_HORIZON_DAYS, ge=1, le=60),
) -> BacktestOut:
    result = await service.backtest(symbol, period, horizon_days)
    response.headers["Cache-Control"] = _cache_control(3600)
    return BacktestOut(**backtest_to_dict(result))


@protected.post(
    "/backtest/batch",
    response_model=BacktestBatchOut,
    summary="Hit-rates merged across a basket (sum hits / sum totals, not a mean of rates)",
)
async def backtest_batch(body: BacktestBatchRequest) -> BacktestBatchOut:
    result = await service.backtest_many(body.symbols, body.period, body.horizon_days)
    return BacktestBatchOut(
        symbols_ok=result.symbols_ok,
        failed=[BatchFailureOut(**failure_to_dict(f)) for f in result.symbols_failed],
        horizon_days=result.horizon_days,
        by_category=[HitRateBucketOut(**bucket_to_dict(b)) for b in result.by_category],
        by_strength=[HitRateBucketOut(**bucket_to_dict(b)) for b in result.by_strength],
    )


@protected.get(
    "/brief/{symbol}",
    response_model=BriefOut,
    summary="LLM grounding brief: signal + state + hit-rates, pre-formatted",
    responses={200: {"content": {"text/plain": {}}}},
)
async def brief(
    symbol: str,
    response: Response,
    period: Period = Query(default=DEFAULT_PERIOD),  # type: ignore[valid-type]
    include_backtest: bool = Query(default=True),
    format: Literal["json", "text"] = Query(  # noqa: A002 — public query param name
        default="json", description="`text` returns just the prompt block as text/plain."
    ),
) -> Any:
    b = await service.brief(symbol, period, include_backtest=include_backtest)
    if format == "text":
        return PlainTextResponse(b.text, headers={"Cache-Control": _cache_control(300)})
    response.headers["Cache-Control"] = _cache_control(300)
    return _brief_out(b)


@protected.post("/briefs", response_model=BriefsOut, summary="Grounding briefs for a basket")
async def briefs(body: BriefsRequest) -> BriefsOut:
    ok, failed = await service.brief_many(
        body.symbols, body.period, include_backtest=body.include_backtest
    )
    return BriefsOut(
        ok=[_brief_out(b) for b in ok],
        failed=[BatchFailureOut(**failure_to_dict(f)) for f in failed],
    )


@protected.post(
    "/rag/documents",
    response_model=RagDocumentsOut | ChromaUpsertOut,
    summary="Vector-store-ready documents, one per ticker, with stable upsert ids",
)
async def rag_documents(
    body: BriefsRequest,
    shape: Literal["records", "chroma"] = Query(
        default="records",
        description="`chroma` returns {ids, documents, metadatas} for `collection.upsert(**body)`.",
    ),
) -> RagDocumentsOut | ChromaUpsertOut:
    docs, failed = await service.rag_documents(
        body.symbols, body.period, include_backtest=body.include_backtest
    )
    failed_out = [BatchFailureOut(**failure_to_dict(f)) for f in failed]
    if shape == "chroma":
        return ChromaUpsertOut(
            ids=[d.id for d in docs],
            documents=[d.text for d in docs],
            metadatas=[d.metadata for d in docs],
            failed=failed_out,
        )
    return RagDocumentsOut(
        documents=[RagDocumentOut(id=d.id, text=d.text, metadata=d.metadata) for d in docs],
        failed=failed_out,
    )


@protected.get(
    "/history/{symbol}", response_model=HistoryPageOut, summary="Persisted runs, newest first"
)
async def history(
    symbol: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> HistoryPageOut:
    rows = await service.history(symbol, limit=limit, offset=offset)
    items = [r.to_dict() for r in rows]
    return HistoryPageOut(
        symbol=symbol.upper().strip(),
        items=items,
        limit=limit,
        offset=offset,
        next_offset=offset + limit if len(items) == limit else None,
    )


@protected.post(
    "/scan",
    summary="Real scan over a basket; publishes to Supabase unless dry_run",
)
async def scan(body: ScanRequestV1) -> dict[str, Any]:
    result = await service.scan(
        symbols=body.symbols,
        period=body.period,
        dry_run=body.dry_run,
        trigger="manual",
        compute_matrix=body.compute_matrix,
    )
    return scan_to_dict(result)
