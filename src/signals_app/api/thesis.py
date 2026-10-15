"""``/v1`` thesis routes — tunable params, dip-timing study, and chains.

* ``GET  /v1/params``       — every tunable value: shipped default, value in
  force (after ``SIGNALS_<NAME>`` env overrides), plus per-request study knobs.
* ``POST /v1/studies/dip``  — dip-buy timing study for a basket.
* ``GET  /v1/chains/ops``   — the ops a chain step can use.
* ``POST /v1/chains/run``   — run a chain of scans → studies → holdem verdicts.

Same auth as the rest of ``/v1`` (``SIGNALS_API_KEY`` when set).
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from signals_app import service
from signals_app.api.v1 import ApiError, require_api_key
from signals_app.chains import OP_DOCS, ChainError, ChainRunner, ChainStep
from signals_app.clients.holdem import HOLDEM_TIMEOUT_SECONDS, HoldemClient, holdem_base_url
from signals_app.config import (
    MAX_API_BATCH_SYMBOLS,
    TUNABLE_DEFAULTS,
    TUNABLE_EFFECTIVE,
    TUNABLE_ENV_PREFIX,
)
from signals_app.data.fetcher import DataFetcher
from signals_app.studies.dip import DipStudyParams, run_dip_study

router = APIRouter(prefix="/v1", tags=["v1-thesis"], dependencies=[Depends(require_api_key)])

DIP_STUDY_CONCURRENCY = 4


class DipStudyRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=MAX_API_BATCH_SYMBOLS)
    period: str = Field(default="5y", description="Daily history to study (yfinance period).")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Any DipStudyParams override; see GET /v1/params."
    )
    include_episodes: bool = False


class ChainStepIn(BaseModel):
    op: str
    params: dict[str, Any] = Field(default_factory=dict)


class ChainRequest(BaseModel):
    steps: list[ChainStepIn] = Field(min_length=1)


def _fetch_daily(symbol: str, period: str) -> Any:
    return DataFetcher().fetch_daily_history(symbol, period)


@router.get("/params", summary="Tunable values: defaults, values in force, study knobs")
async def params() -> dict[str, Any]:
    defaults = DipStudyParams()
    return {
        "process": {
            "env_prefix": TUNABLE_ENV_PREFIX,
            "values": [
                {
                    "name": name,
                    "env": f"{TUNABLE_ENV_PREFIX}{name}",
                    "default": TUNABLE_DEFAULTS[name],
                    "effective": TUNABLE_EFFECTIVE[name],
                    "overridden": TUNABLE_EFFECTIVE[name] != TUNABLE_DEFAULTS[name],
                }
                for name in sorted(TUNABLE_DEFAULTS)
            ],
        },
        "per_request": {
            "dip_study": {**asdict(defaults), "windows": list(defaults.windows)},
        },
    }


@router.post("/studies/dip", summary="Dip-buy timing study for a basket")
async def dip_study(body: DipStudyRequest) -> dict[str, Any]:
    try:
        study_params = DipStudyParams.from_overrides(body.params)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "InvalidParams", str(exc)) from exc

    gate = asyncio.Semaphore(DIP_STUDY_CONCURRENCY)

    async def one(symbol: str) -> dict[str, Any]:
        async with gate:
            try:
                df = await asyncio.to_thread(_fetch_daily, symbol.upper(), body.period)
                result = run_dip_study(symbol, df, study_params)
            except ValueError as exc:
                return {"symbol": symbol.upper(), "error": str(exc)}
        return result.to_dict(include_episodes=body.include_episodes)

    results = await asyncio.gather(*(one(s) for s in dict.fromkeys(body.symbols)))
    return {
        "ok": [r for r in results if "error" not in r],
        "failed": [r for r in results if "error" in r],
    }


@router.get("/chains/ops", summary="Ops available to a chain step")
async def chain_ops() -> dict[str, str]:
    return OP_DOCS


@router.post("/chains/run", summary="Run a chain: scan → study → verdict → rank")
async def run_chain(body: ChainRequest) -> dict[str, Any]:
    steps = [ChainStep(op=s.op, params=s.params) for s in body.steps]
    base = holdem_base_url()
    async with httpx.AsyncClient(timeout=HOLDEM_TIMEOUT_SECONDS) as http:
        runner = ChainRunner(
            fetch_daily=_fetch_daily,
            analyze_many=service.analyze_many,
            holdem=HoldemClient(base, http) if base else None,
        )
        try:
            result = await runner.run(steps)
        except ChainError as exc:
            raise ApiError(400, "InvalidChain", str(exc)) from exc
    return result.to_dict()
