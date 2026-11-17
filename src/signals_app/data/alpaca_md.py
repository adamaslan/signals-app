"""Alpaca-first market data (daily bars, snapshots) with a shared rate budget.

Canonical copy of the NuWrrrld market-data client. Each repo vendors this file
unchanged; edit it in one place and re-copy. Policy: ~/.claude/rules/market-data-fallback.md.

* Paper key pair only. Never points at the live trading host.
* ``daily_bars`` returns yfinance-shaped DataFrames (Open/High/Low/Close/Volume,
  tz-naive DatetimeIndex) so existing indicator code needs no changes.
* Every request first reserves a token from the DynamoDB ``nwf_rate_budget``
  counter (cap 190/min across all pipelines). When boto3, AWS credentials or the
  table are unavailable it logs a WARNING once and paces locally instead; it
  never switches vendor because the budget was unreachable.
* Missing tickers are returned as absent keys (fail closed). Callers decide
  whether a fallback vendor is allowed on their host.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from typing import Iterable, Sequence

import requests

log = logging.getLogger(__name__)

DATA_URL = "https://data.alpaca.markets"
SYMBOLS_PER_REQUEST = 150
HTTP_TIMEOUT_SECONDS = 30
MAX_RETRIES = 4
RATE_LIMIT_STATUS_CODE = 429
ALPACA_MAX_PER_MINUTE = 190
BUDGET_TABLE = "nwf_rate_budget"
BUDGET_TTL_SECONDS = 3600
BUDGET_MAX_WAITS = 3
LOCAL_MIN_INTERVAL_SECONDS = 0.35
SIP_DELAY_MINUTES = 20  # SIP is only served for bars older than ~15 min on the Basic plan

_budget_table = None
_budget_disabled_reason: str | None = None
_last_request_at = 0.0


class AlpacaError(RuntimeError):
    """Alpaca request failed after retries (callers may fall back, and must log it)."""


class AlpacaBudgetExhausted(AlpacaError):
    pass


def to_alpaca_symbol(ticker: str) -> str:
    """Yahoo BRK-B -> Alpaca BRK.B."""
    return ticker.strip().upper().replace("-", ".")


def from_alpaca_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


def _credentials() -> dict[str, str]:
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        raise AlpacaError("ALPACA_API_KEY / ALPACA_API_SECRET not set")
    if "//api.alpaca.markets" in os.environ.get("ALPACA_BASE_URL", ""):
        raise AlpacaError("refusing live Alpaca trading host; paper key pair only")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def is_configured() -> bool:
    return bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_API_SECRET"))


# -- shared rate budget -------------------------------------------------------------
def _get_budget_table():
    global _budget_table, _budget_disabled_reason
    if _budget_table is not None or _budget_disabled_reason:
        return _budget_table
    if os.environ.get("NWF_RATE_BUDGET", "").lower() == "off":
        _budget_disabled_reason = "NWF_RATE_BUDGET=off"
        return None
    try:
        import boto3

        session = boto3.Session(profile_name=os.environ.get("NWF_AWS_PROFILE") or None,
                                region_name=os.environ.get("AWS_REGION", "us-east-1"))
        if session.get_credentials() is None:
            raise RuntimeError("no AWS credentials")
        _budget_table = session.resource("dynamodb").Table(BUDGET_TABLE)
    except Exception as exc:  # boto3 absent, no creds, bad profile: all mean "budget unreachable"
        _budget_disabled_reason = f"{type(exc).__name__}: {exc}"
        log.warning("alpaca rate budget unavailable (%s); pacing locally at %.2fs/request",
                    _budget_disabled_reason, LOCAL_MIN_INTERVAL_SECONDS)
    return _budget_table


def _take_token(n: int = 1) -> None:
    global _last_request_at
    table = _get_budget_table()
    if table is None:
        wait = LOCAL_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()
        return
    from botocore.exceptions import ClientError

    for _ in range(BUDGET_MAX_WAITS):
        now = time.time()
        try:
            table.update_item(
                Key={"bucket": f"alpaca#{int(now // 60)}"},
                UpdateExpression="ADD used :n SET expires_at = :exp",
                ConditionExpression="attribute_not_exists(used) OR used <= :room",
                ExpressionAttributeValues={":n": n, ":room": ALPACA_MAX_PER_MINUTE - n,
                                           ":exp": int(now) + BUDGET_TTL_SECONDS})
            return
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                log.warning("alpaca rate budget write failed (%s); pacing locally", exc)
                return
            time.sleep(60 - (time.time() % 60) + 1)  # wait for the next minute, never switch vendor
    raise AlpacaBudgetExhausted("shared Alpaca budget still exhausted after waiting")


# -- http ----------------------------------------------------------------------------
def _get(path: str, params: dict) -> dict:
    headers = _credentials()
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        _take_token(1)
        try:
            resp = requests.get(DATA_URL + path, params=params, headers=headers,
                                timeout=HTTP_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last = exc
        else:
            if resp.status_code == RATE_LIMIT_STATUS_CODE or resp.status_code >= 500:
                last = AlpacaError(f"alpaca {resp.status_code}")
                retry_after = float(resp.headers.get("Retry-After", 0) or 0)
                if retry_after:
                    time.sleep(min(retry_after, 60))
            elif resp.status_code >= 400:
                raise AlpacaError(f"alpaca {resp.status_code}: {resp.text[:200]}")
            else:
                return resp.json()
        time.sleep(min(2 ** attempt, 20))
    raise AlpacaError(f"alpaca request failed after {MAX_RETRIES} attempts: {last}")


def _chunks(items: Sequence[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield list(items[i:i + size])


# -- public API ----------------------------------------------------------------------
def daily_bars(symbols: Sequence[str], start: dt.date, end: dt.date | None = None, *,
               feed: str = "sip") -> dict[str, list[dict]]:
    """Raw split-adjusted daily bars keyed by the caller's (Yahoo-style) ticker."""
    wanted = {to_alpaca_symbol(s): s for s in dict.fromkeys(symbols)}
    end_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=SIP_DELAY_MINUTES)
              if end is None else dt.datetime.combine(end + dt.timedelta(days=1), dt.time(),
                                                      dt.timezone.utc))
    out: dict[str, list[dict]] = {}
    for chunk in _chunks(list(wanted), SYMBOLS_PER_REQUEST):
        token: str | None = None
        while True:
            params = {"symbols": ",".join(chunk), "timeframe": "1Day", "start": start.isoformat(),
                      "end": end_ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "adjustment": "split",
                      "feed": feed, "limit": 10000}
            if token:
                params["page_token"] = token
            data = _get("/v2/stocks/bars", params)
            for sym, rows in (data.get("bars") or {}).items():
                out.setdefault(wanted.get(sym, from_alpaca_symbol(sym)), []).extend(rows)
            token = data.get("next_page_token")
            if not token:
                break
    return out


def daily_bars_frames(symbols: Sequence[str], days: int = 120, *, feed: str = "sip") -> dict:
    """yfinance-shaped DataFrames per ticker; tickers Alpaca could not serve are absent."""
    import pandas as pd

    start = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days)
    frames = {}
    for sym, rows in daily_bars(symbols, start, feed=feed).items():
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        df.index = pd.to_datetime(df["t"]).dt.tz_localize(None).dt.normalize()
        frames[sym] = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close",
                                         "v": "Volume"})[["Open", "High", "Low", "Close", "Volume"]]
    return frames


def latest_prices(symbols: Sequence[str], *, feed: str = "iex") -> dict[str, dict]:
    """Latest trade per ticker from snapshots (IEX). Absent keys = no price."""
    wanted = {to_alpaca_symbol(s): s for s in dict.fromkeys(symbols)}
    out: dict[str, dict] = {}
    for chunk in _chunks(list(wanted), SYMBOLS_PER_REQUEST):
        data = _get("/v2/stocks/snapshots", {"symbols": ",".join(chunk), "feed": feed})
        for sym, snap in data.items():
            trade = (snap or {}).get("latestTrade") or {}
            daily = (snap or {}).get("dailyBar") or {}
            prev = (snap or {}).get("prevDailyBar") or {}
            if trade.get("p"):
                price, prev_close = float(trade["p"]), prev.get("c")
                out[wanted.get(sym, from_alpaca_symbol(sym))] = {
                    "price": price, "prev_close": prev_close, "day_open": daily.get("o"),
                    "change_pct": ((price - prev_close) / prev_close * 100) if prev_close else None,
                    "ts": trade.get("t"), "source": "alpaca", "feed": feed}
    return out


def warn_fallback(what: str, symbols: Iterable[str], reason: object) -> None:
    """Log an Alpaca -> other-vendor fall-through at WARNING, per the policy."""
    missing = sorted(symbols)
    log.warning("market-data fallback: %s for %d symbols left Alpaca (%s): %s",
                what, len(missing), reason, ",".join(missing[:12]))
