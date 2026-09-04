"""Multi-timeframe LLM synthesis — L5 of the pipeline.

Calls Gemini to produce a structured Signal for each timeframe, with:
- Tiered cache TTLs (1D/5D fresh, 1M=4h, 3M=12h, 6M/1Y=24h)
- Rule-based fallback when Gemini is unavailable
- alignment_score and classify_divergence carried over from gcp3

Ported from gcp3/backend/signals/multi_timeframe.py with the Firestore
dependency replaced by an in-memory dict (local mode) or a skip (cloud mode
where the caller owns caching).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC
from typing import Any, Final

from signals_app.config import (
    DIVERGENCE_INTERPRETATIONS,
    GEMINI_TIMEOUT_SECONDS,
    LLM_PROMPT_VERSION,
    OPENROUTER_BASE_URL,
    OPENROUTER_TIMEOUT_SECONDS,
    TIMEFRAME_CACHE_TTL_SECONDS,
    Settings,
    get_settings,
)
from signals_app.schemas.signal_output import (
    DivergencePattern,
    Signal,
    TimeframeMatrix,
    alignment_score,
    classify_divergence,
)

logger = logging.getLogger(__name__)

# In-memory cache for local mode: key → (Signal, expires_at)
_LOCAL_CACHE: dict[str, tuple[Signal, float]] = {}

PROMPT_TEMPLATE: Final[str] = """You are a quantitative equity analyst. Produce a structured signal for {ticker} on the {timeframe} timeframe.

Features:
{features_block}

Return JSON with EXACTLY this structure (no extra fields):
{{
  "direction": "strong_buy" | "buy" | "hold" | "sell" | "strong_sell",
  "confidence": <float strictly between 0 and 1>,
  "timeframe": "{timeframe}",
  "evidence": {{
    "items": [
      {{
        "source": "technical" | "fundamental" | "macro" | "news_sentiment" | "options_flow" | "sector_relative" | "cross_asset" | "earnings" | "rule_based",
        "weight": <float 0-1, supporting weights must sum to 1.0>,
        "summary": "<one-sentence explanation>",
        "is_counter": false | true
      }}
    ]
  }},
  "ai_degraded": false,
  "prompt_version": "{prompt_version}"
}}

Rules:
- confidence must be strictly between 0 and 1 (never exactly 0.0 or 1.0)
- hold signals must have confidence <= 0.75
- if confidence > 0.6, include at least 1 item with is_counter=true
- supporting (non-counter) evidence weights must sum to exactly 1.0

Respond with valid JSON only. No prose, no markdown, no code fences.
"""


def _features_block(features: dict[str, Any]) -> str:
    """Format a features dict as an indented key: value block.

    Args:
        features: Feature dict to format.

    Returns:
        Multi-line string.
    """
    return "\n".join(f"  {k}: {v}" for k, v in features.items())


def _fallback_signal(timeframe: str, features: dict[str, Any]) -> dict[str, Any]:
    """Rule-based fallback when LLM is unavailable.

    Uses the confluence score or price change to produce a simple directional signal.

    Args:
        timeframe: Timeframe label.
        features: Feature dict for the timeframe.

    Returns:
        Dict matching the Signal schema.
    """
    score = features.get("confluence_score", 0.0) or 0.0
    pct = features.get("change_pct", 0.0) or 0.0

    if score >= 0.35 or pct > 2:
        direction = "buy"
        confidence = 0.55
    elif score <= -0.35 or pct < -2:
        direction = "sell"
        confidence = 0.55
    else:
        direction = "hold"
        confidence = 0.30

    return {
        "direction": direction,
        "confidence": confidence,
        "timeframe": timeframe,
        "evidence": {
            "items": [
                {
                    "source": "rule_based",
                    "weight": 1.0,
                    "summary": (
                        f"Rule-based fallback: confluence_score={score:.2f}, change_pct={pct:.2f}%"
                    ),
                    "is_counter": False,
                }
            ]
        },
        "ai_degraded": True,
        "prompt_version": "fallback_v1",
    }


def _get_cached(cache_key: str) -> Signal | None:
    """Retrieve a Signal from the in-memory local cache if not expired.

    Args:
        cache_key: Cache key string.

    Returns:
        Cached Signal or None.
    """
    entry = _LOCAL_CACHE.get(cache_key)
    if entry is None:
        return None
    signal, expires_at = entry
    if time.time() > expires_at:
        del _LOCAL_CACHE[cache_key]
        return None
    return signal


def _set_cached(cache_key: str, signal: Signal, ttl_seconds: int) -> None:
    """Store a Signal in the in-memory local cache.

    Args:
        cache_key: Cache key string.
        signal: Signal to store.
        ttl_seconds: TTL in seconds (0 = no caching).
    """
    if ttl_seconds <= 0:
        return
    _LOCAL_CACHE[cache_key] = (signal, time.time() + ttl_seconds)


async def _call_openrouter(
    prompt: str,
    settings: Settings,
) -> dict[str, Any] | None:
    """Make an OpenRouter API call (OpenAI-compatible) and return parsed JSON.

    OpenRouter is tried first when OPENROUTER_API_KEY is set. Falls back
    gracefully to None on any error so the caller can try Gemini or rule-based.

    Args:
        prompt: Prompt string.
        settings: Application settings with OpenRouter key and model.

    Returns:
        Parsed JSON dict or None on failure.
    """
    if not settings.openrouter_enabled:
        return None

    try:
        import httpx

        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://signals-app.local",
            "X-Title": "Signals App",
        }
        payload = {
            "model": settings.openrouter_model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=OPENROUTER_TIMEOUT_SECONDS) as client:
            resp = await client.post(OPENROUTER_BASE_URL, headers=headers, json=payload)
            resp.raise_for_status()

        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        return json.loads(text)

    except TimeoutError:
        logger.warning("_call_openrouter: timeout after %.1fs", OPENROUTER_TIMEOUT_SECONDS)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("_call_openrouter: JSON parse error: %s", exc)
        return None
    except Exception as exc:
        logger.warning("_call_openrouter: error: %s", exc)
        return None


async def _call_llm(
    prompt: str,
    settings: Settings,
) -> dict[str, Any] | None:
    """Unified LLM call: tries OpenRouter first, falls back to Gemini.

    Args:
        prompt: Prompt string.
        settings: Application settings.

    Returns:
        Parsed JSON dict or None when all providers fail.
    """
    provider = settings.llm_provider

    if provider == "openrouter":
        result = await _call_openrouter(prompt, settings)
        if result is not None:
            return result
        # OpenRouter failed — fall through to Gemini if key is also set
        logger.warning("_call_llm: openrouter failed, trying gemini fallback")
        return await _call_gemini(prompt, settings)

    if provider == "gemini":
        return await _call_gemini(prompt, settings)

    return None


async def _call_gemini(
    prompt: str,
    settings: Settings,
) -> dict[str, Any] | None:
    """Make a Gemini API call and return the parsed JSON response.

    Args:
        prompt: Prompt string.
        settings: Application settings with API key and model.

    Returns:
        Parsed JSON dict or None on failure.
    """
    if not settings.llm_enabled:
        logger.debug("_call_gemini: LLM not enabled (no API key)")
        return None

    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)

        response = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, prompt),
            timeout=GEMINI_TIMEOUT_SECONDS,
        )

        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        return json.loads(text)

    except TimeoutError:
        logger.warning("_call_gemini: timeout after %.1fs", GEMINI_TIMEOUT_SECONDS)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("_call_gemini: JSON parse error: %s", exc)
        return None
    except Exception as exc:
        logger.warning("_call_gemini: error: %s", exc)
        return None


async def _compute_single_timeframe(
    ticker: str,
    timeframe: str,
    features: dict[str, Any],
    settings: Settings,
    prompt_version: str,
) -> Signal | None:
    """Produce a Signal for a single timeframe, using cache and LLM.

    Cache TTL is per-timeframe: 1D/5D always fresh, longer timeframes cached
    for 4h/12h/24h.

    Args:
        ticker: Stock symbol.
        timeframe: Timeframe label.
        features: Feature dict for the timeframe.
        settings: Application settings.
        prompt_version: Prompt version tag.

    Returns:
        Signal or None if both LLM and fallback fail.
    """
    cache_key = f"mtf:{ticker}:{timeframe}"
    ttl = TIMEFRAME_CACHE_TTL_SECONDS.get(timeframe, 0)

    if ttl > 0:
        cached = _get_cached(cache_key)
        if cached is not None:
            logger.info("mtf_cache_hit ticker=%s tf=%s", ticker, timeframe)
            return cached

    prompt = PROMPT_TEMPLATE.format(
        ticker=ticker,
        timeframe=timeframe,
        features_block=_features_block(features),
        prompt_version=prompt_version,
    )

    result_dict = await _call_llm(prompt, settings)

    if result_dict is None:
        logger.warning(
            "mtf_llm_failed ticker=%s tf=%s provider=%s — using rule-based fallback",
            ticker, timeframe, settings.llm_provider,
        )
        result_dict = _fallback_signal(timeframe, features)

    try:
        # Ensure timeframe field matches expected value
        result_dict["timeframe"] = timeframe
        signal = Signal.model_validate(result_dict)
    except Exception as exc:
        logger.warning(
            "mtf_signal_parse_failed ticker=%s tf=%s error=%s — using fallback",
            ticker, timeframe, exc,
        )
        try:
            fallback_dict = _fallback_signal(timeframe, features)
            fallback_dict["timeframe"] = timeframe
            signal = Signal.model_validate(fallback_dict)
        except Exception as fallback_exc:
            logger.error(
                "mtf_fallback_failed ticker=%s tf=%s error=%s",
                ticker, timeframe, fallback_exc,
            )
            return None

    if ttl > 0 and not signal.ai_degraded:
        _set_cached(cache_key, signal, ttl)

    return signal


async def build_timeframe_matrix(
    ticker: str,
    features_by_timeframe: dict[str, dict[str, Any]],
    settings: Settings | None = None,
    prompt_version: str = LLM_PROMPT_VERSION,
) -> TimeframeMatrix:
    """Build a TimeframeMatrix by calling the LLM per timeframe concurrently.

    Args:
        ticker: Stock symbol.
        features_by_timeframe: Map of timeframe string to feature dict.
            Feature dicts should include: confluence_score, change_pct,
            rsi, macd_above_signal, adx, bias, action, etc.
        settings: Application settings. Loaded from env if None.
        prompt_version: Prompt version tag for auditability.

    Returns:
        TimeframeMatrix with signals, alignment score, and divergence pattern.
    """
    from datetime import datetime

    if settings is None:
        settings = get_settings()

    tasks = {
        tf: _compute_single_timeframe(ticker, tf, feats, settings, prompt_version)
        for tf, feats in features_by_timeframe.items()
    }

    results: dict[str, Signal | None] = {}
    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for tf, result in zip(tasks.keys(), gathered):
        if isinstance(result, Exception):
            logger.error(
                "mtf_timeframe_error ticker=%s tf=%s error=%s", ticker, tf, result
            )
            results[tf] = None
        else:
            results[tf] = result  # type: ignore[assignment]

    valid_signals: dict[str, Signal] = {
        tf: s for tf, s in results.items() if s is not None
    }
    signal_list = list(valid_signals.values())

    align = alignment_score(signal_list) if signal_list else 0.0
    div_pattern = (
        classify_divergence(valid_signals) if valid_signals else DivergencePattern.insufficient_data
    )
    div_interp = DIVERGENCE_INTERPRETATIONS.get(div_pattern.value, "")

    logger.info(
        "build_timeframe_matrix: ticker=%s timeframes=%s align=%.3f pattern=%s",
        ticker, list(valid_signals.keys()), align, div_pattern.value,
    )

    return TimeframeMatrix(
        ticker=ticker,
        signals=valid_signals,
        alignment_score=align,
        divergence_pattern=div_pattern,
        divergence_interpretation=div_interp,
        computed_at=datetime.now(UTC).isoformat(),
    )


def synthesize_single(
    ticker: str,
    timeframe: str,
    features: dict[str, Any],
    settings: Settings | None = None,
    prompt_version: str = LLM_PROMPT_VERSION,
) -> Signal:
    """Synchronous wrapper: produce a Signal for a single timeframe.

    Uses the rule-based fallback if LLM is disabled or fails.
    Useful for the CLI (scripts/analyze.py) in --no-llm mode.

    Args:
        ticker: Stock symbol.
        timeframe: Timeframe label.
        features: Feature dict.
        settings: Application settings.
        prompt_version: Prompt version tag.

    Returns:
        Signal (may be ai_degraded=True if fallback was used).
    """
    if settings is None:
        settings = get_settings()

    if not settings.llm_enabled:
        logger.info("synthesize_single: LLM disabled — using rule-based fallback")
        fallback_dict = _fallback_signal(timeframe, features)
        fallback_dict["timeframe"] = timeframe
        return Signal.model_validate(fallback_dict)

    # Run async function synchronously
    loop = asyncio.new_event_loop()
    try:
        signal = loop.run_until_complete(
            _compute_single_timeframe(ticker, timeframe, features, settings, prompt_version)
        )
    finally:
        loop.close()

    if signal is None:
        fallback_dict = _fallback_signal(timeframe, features)
        fallback_dict["timeframe"] = timeframe
        return Signal.model_validate(fallback_dict)

    return signal
