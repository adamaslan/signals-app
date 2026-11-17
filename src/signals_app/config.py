"""Configuration and constants for the Signals App.

Centralizes all configuration values as frozen dataclasses and named constants.
No magic numbers — every threshold is a named constant here.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------
# Every numeric threshold wrapped in ``_tunable`` can be overridden per process
# with an env var named ``SIGNALS_<NAME>`` (e.g. ``SIGNALS_RSI_OVERSOLD=25``),
# without a code change. ``TUNABLE_DEFAULTS`` / ``TUNABLE_EFFECTIVE`` record the
# shipped default and the value actually in force, so ``GET /v1/params`` can
# show exactly how this process is configured. A malformed override is logged
# and ignored — the default stays in force rather than the process crashing.

TUNABLE_ENV_PREFIX: Final[str] = "SIGNALS_"
TUNABLE_DEFAULTS: dict[str, int | float] = {}
TUNABLE_EFFECTIVE: dict[str, int | float] = {}


def _tunable(name: str, default: int | float) -> Any:
    """Return ``default``, or its ``SIGNALS_<name>`` env override when valid."""
    TUNABLE_DEFAULTS[name] = default
    raw = os.getenv(f"{TUNABLE_ENV_PREFIX}{name}")
    value = default
    if raw is not None:
        try:
            value = type(default)(raw)
        except ValueError:
            logger.warning("ignoring malformed %s%s=%r", TUNABLE_ENV_PREFIX, name, raw)
        else:
            logger.info("tunable override %s=%s (default %s)", name, value, default)
    TUNABLE_EFFECTIVE[name] = value
    return value

# Bumped whenever detection/scoring logic changes — provenance stamp on every
# SignalOutput so two runs on identical data are distinguishable if the logic
# that produced them differs. Independent of schema_version (wire format).
SIGNALS_APP_CODE_VERSION: Final[str] = "signals-app@1.3.0"

# ---------------------------------------------------------------------------
# Environment / deployment mode
# ---------------------------------------------------------------------------

SIGNALS_ENV: Final[str] = os.getenv("SIGNALS_ENV", "local")
IS_CLOUD: Final[bool] = SIGNALS_ENV == "cloud"

LOG_LEVEL: Final[str] = os.getenv("LOG_LEVEL", "INFO")
OUTPUT_DIR: Final[str] = os.getenv("OUTPUT_DIR", "./output")

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

DEFAULT_PERIOD: Final[str] = "3mo"
VALID_PERIODS: Final[tuple[str, ...]] = (
    "15m", "1h", "4h", "1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"
)
MAX_RETRY_ATTEMPTS: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[float] = 1.0

# A manually-triggered scan (POST /scan, e.g. from the frontend's "Run real
# scan" button) is capped well below the 954-ticker seed — a stray click
# should never fan out into hundreds of yfinance fetches + LLM calls.
MAX_MANUAL_SCAN_SYMBOLS: Final[int] = 100

# A frontend-triggered historical backtest (POST /backtest/run) replays every
# detector over every bar per ticker — CPU-bound and synchronous, so capped
# much lower than a scan. /backtest/suggest only reads the latest bar, so it
# can take a larger basket.
MAX_MANUAL_BACKTEST_SYMBOLS: Final[int] = 25
MAX_SUGGEST_BACKTEST_SYMBOLS: Final[int] = 100
# Window fetched to detect a ticker's *current* signals for suggestions —
# must clear the SMA-200 warmup.
SUGGEST_DETECT_PERIOD: Final[str] = "1y"

# /v1 batch endpoints. Rule-based batches are cheap (one yfinance fetch each);
# LLM batches cost a model call per symbol, so they get a much lower ceiling.
MAX_API_BATCH_SYMBOLS: Final[int] = 50
MAX_API_LLM_BATCH_SYMBOLS: Final[int] = 10


def get_api_key() -> str | None:
    """Shared secret for /v1 (``SIGNALS_API_KEY``); None leaves /v1 open.

    Read per request rather than at import so a rotated secret or a test's
    monkeypatched env takes effect without a restart.
    """
    return os.getenv("SIGNALS_API_KEY") or None

FETCH_BACKOFF_MIN_SECONDS: Final[float] = 1.0
FETCH_BACKOFF_MAX_SECONDS: Final[float] = 10.0
STALE_FALLBACK_HOURS: Final[int] = 24

# ---------------------------------------------------------------------------
# Data quality gates
# ---------------------------------------------------------------------------

MIN_DATA_POINTS: Final[int] = _tunable("MIN_DATA_POINTS", 22)
MIN_DATA_POINTS_200MA: Final[int] = 200

MIN_BARS_BY_PERIOD: Final[dict[str, int]] = {
    "15m": 20, "1h": 20, "4h": 20,
    "1d": 20, "5d": 20,
    "1mo": 20, "3mo": 60, "6mo": 120,
    "1y": 200, "2y": 400, "5y": 800,
    "10y": 1000, "ytd": 20, "max": 20,
}
MAX_NAN_RATIO: Final[float] = 0.05
OUTLIER_RETURN_THRESHOLD: Final[float] = 0.50

# ---------------------------------------------------------------------------
# Indicator periods
# ---------------------------------------------------------------------------

MA_PERIODS: Final[tuple[int, ...]] = (5, 10, 20, 50, 100, 200)
RSI_PERIOD: Final[int] = _tunable("RSI_PERIOD", 14)
MACD_FAST: Final[int] = _tunable("MACD_FAST", 12)
MACD_SLOW: Final[int] = _tunable("MACD_SLOW", 26)
MACD_SIGNAL: Final[int] = _tunable("MACD_SIGNAL", 9)
BOLLINGER_PERIOD: Final[int] = _tunable("BOLLINGER_PERIOD", 20)
BOLLINGER_STD: Final[float] = _tunable("BOLLINGER_STD", 2.0)
STOCHASTIC_K_PERIOD: Final[int] = _tunable("STOCHASTIC_K_PERIOD", 14)
STOCHASTIC_D_PERIOD: Final[int] = _tunable("STOCHASTIC_D_PERIOD", 3)
ADX_PERIOD: Final[int] = _tunable("ADX_PERIOD", 14)
ATR_PERIOD: Final[int] = _tunable("ATR_PERIOD", 14)
VOLUME_MA_SHORT: Final[int] = _tunable("VOLUME_MA_SHORT", 10)
VOLUME_MA_LONG: Final[int] = _tunable("VOLUME_MA_LONG", 20)
ICHIMOKU_TENKAN: Final[int] = _tunable("ICHIMOKU_TENKAN", 9)
ICHIMOKU_KIJUN: Final[int] = _tunable("ICHIMOKU_KIJUN", 26)
ICHIMOKU_SENKOU_B: Final[int] = _tunable("ICHIMOKU_SENKOU_B", 52)
OBV_EMA_PERIOD: Final[int] = _tunable("OBV_EMA_PERIOD", 20)
CMF_PERIOD: Final[int] = _tunable("CMF_PERIOD", 20)

# ---------------------------------------------------------------------------
# RSI thresholds
# ---------------------------------------------------------------------------

RSI_OVERSOLD: Final[float] = _tunable("RSI_OVERSOLD", 30.0)
RSI_OVERBOUGHT: Final[float] = _tunable("RSI_OVERBOUGHT", 70.0)
RSI_EXTREME_OVERSOLD: Final[float] = _tunable("RSI_EXTREME_OVERSOLD", 20.0)
RSI_EXTREME_OVERBOUGHT: Final[float] = _tunable("RSI_EXTREME_OVERBOUGHT", 80.0)

# ---------------------------------------------------------------------------
# Stochastic thresholds
# ---------------------------------------------------------------------------

STOCH_OVERSOLD: Final[float] = _tunable("STOCH_OVERSOLD", 20.0)
STOCH_OVERBOUGHT: Final[float] = _tunable("STOCH_OVERBOUGHT", 80.0)

# ---------------------------------------------------------------------------
# Volume thresholds
# ---------------------------------------------------------------------------

VOLUME_SPIKE_1_5X: Final[float] = _tunable("VOLUME_SPIKE_1_5X", 1.5)
VOLUME_SPIKE_2X: Final[float] = _tunable("VOLUME_SPIKE_2X", 2.0)
VOLUME_SPIKE_3X: Final[float] = _tunable("VOLUME_SPIKE_3X", 3.0)

# ---------------------------------------------------------------------------
# ADX / trend thresholds
# ---------------------------------------------------------------------------

ADX_TRENDING: Final[float] = _tunable("ADX_TRENDING", 25.0)
ADX_STRONG_TREND: Final[float] = _tunable("ADX_STRONG_TREND", 40.0)
ADX_NO_TREND: Final[float] = _tunable("ADX_NO_TREND", 20.0)

# ---------------------------------------------------------------------------
# Price action thresholds
# ---------------------------------------------------------------------------

LARGE_MOVE_PERCENT: Final[float] = _tunable("LARGE_MOVE_PERCENT", 5.0)

# ---------------------------------------------------------------------------
# Detector budget
# ---------------------------------------------------------------------------

DETECTOR_TIMEOUT_MS: Final[int] = 500
MAX_DETECTOR_FAILURES: Final[int] = 4

# ---------------------------------------------------------------------------
# Historical scanning / backtesting
# ---------------------------------------------------------------------------

# Longest indicator warmup period (SMA-200) — bars before this index would
# otherwise produce NaN-driven false signals.
MIN_HISTORICAL_LOOKBACK: Final[int] = 200
# Forward-return horizon (trading days) used to score a signal as a hit/miss.
BACKTEST_FORWARD_HORIZON_DAYS: Final[int] = _tunable("BACKTEST_FORWARD_HORIZON_DAYS", 5)
# Where scripts/calibrate.py writes, and the live scoring path reads, the
# strength -> hit-rate calibration table.
CALIBRATION_FILE: Final[str] = os.getenv(
    "CALIBRATION_FILE", "./calibration/strength_hit_rates.json"
)
# Minimum sample size a strength bucket needs before its measured hit-rate is
# trusted enough to influence a live confidence_label — small-n buckets are noise.
CALIBRATION_MIN_BUCKET_SIZE: Final[int] = _tunable("CALIBRATION_MIN_BUCKET_SIZE", 30)

# ---------------------------------------------------------------------------
# Supabase (writer / scan_universe.py)
# ---------------------------------------------------------------------------

SUPABASE_URL: str | None = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY: str | None = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_REQUEST_TIMEOUT_SECONDS: Final[float] = 15.0

# ---------------------------------------------------------------------------
# Data-quality scoring
# ---------------------------------------------------------------------------

# A fresh daily bar older than this looks stale — same margin rationale as
# the portal/mobile STALE_THRESHOLD_MS (26h: daily refresh + weekend slack).
DATA_QUALITY_STALE_HOURS: Final[float] = 26.0
# NaN ratio in the raw OHLCV window above this drags the quality score down hard.
DATA_QUALITY_MAX_NAN_RATIO: Final[float] = 0.02
# A last bar timestamped further in the future than this signals clock skew
# or a timezone parsing bug, not real data.
DATA_QUALITY_FUTURE_TOLERANCE_HOURS: Final[float] = 1.0

# ---------------------------------------------------------------------------
# Cache / TTL config
# ---------------------------------------------------------------------------

CACHE_SCHEMA_VERSION: Final[str] = "v1"
CACHE_MAX_SIZE: Final[int] = 200

TIMEFRAME_CACHE_TTL_SECONDS: Final[dict[str, int]] = {
    "1D": 0,
    "5D": 0,
    "1M": 4 * 3600,
    "3M": 12 * 3600,
    "6M": 24 * 3600,
    "1Y": 24 * 3600,
}

# ---------------------------------------------------------------------------
# Confluence / scoring
# ---------------------------------------------------------------------------

CONFLUENCE_BUY_THRESHOLD: Final[float] = _tunable("CONFLUENCE_BUY_THRESHOLD", 0.35)
CONFLUENCE_SELL_THRESHOLD: Final[float] = _tunable("CONFLUENCE_SELL_THRESHOLD", -0.35)
CONFLUENCE_BUY_MIN_SIGNALS: Final[int] = _tunable("CONFLUENCE_BUY_MIN_SIGNALS", 3)
CONFLUENCE_SELL_MIN_SIGNALS: Final[int] = _tunable("CONFLUENCE_SELL_MIN_SIGNALS", 3)

# ---------------------------------------------------------------------------
# Publication gate — scripts/scan_universe.py (see docs/backend-state-and-
# supabase-plan.md Part 3 §3). Most ticker-days should NOT be published;
# the gate is what makes the engine selective instead of always emitting a
# direction. Run before LLM synthesis, not after — an unpublishable signal
# should never pay for a synthesis call.
# ---------------------------------------------------------------------------

PUBLISH_MIN_DATA_QUALITY: Final[float] = _tunable("PUBLISH_MIN_DATA_QUALITY", 0.7)
PUBLISH_MIN_SIGNALS: Final[int] = _tunable("PUBLISH_MIN_SIGNALS", 3)
# Reuses the existing BUY/SELL confluence bands — a signal weak enough to be
# HOLD-territory carries no information worth persisting.
PUBLISH_MIN_CONFLUENCE_SCORE: Final[float] = CONFLUENCE_BUY_THRESHOLD

# ---------------------------------------------------------------------------
# LLM config — Gemini
# ---------------------------------------------------------------------------

GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL: Final[str] = "gemini-2.0-flash"
GEMINI_TIMEOUT_SECONDS: Final[float] = 10.0
GEMINI_BREAKER_FAILURE_THRESHOLD: Final[int] = 5
GEMINI_BREAKER_WINDOW_SECONDS: Final[float] = 60.0
GEMINI_BREAKER_OPEN_SECONDS: Final[float] = 300.0

# ---------------------------------------------------------------------------
# LLM config — OpenRouter (takes priority over Gemini when key is set)
# ---------------------------------------------------------------------------

OPENROUTER_API_KEY: str | None = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: Final[str] = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL: Final[str] = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001")
OPENROUTER_TIMEOUT_SECONDS: Final[float] = 15.0

LLM_PROMPT_VERSION: Final[str] = "signals_v1"

# ---------------------------------------------------------------------------
# Divergence interpretation strings
# ---------------------------------------------------------------------------

DIVERGENCE_INTERPRETATIONS: Final[dict[str, str]] = {
    "aligned_bullish": "All timeframes agree bullish — high conviction setup.",
    "aligned_bearish": "All timeframes agree bearish — high conviction breakdown.",
    "short_bull_long_bear": "Short-term pop within a longer-term downtrend. Potential bear-market rally; caution on entries.",
    "short_bear_long_bull": "Short-term pullback within a longer-term uptrend. Potential buy-the-dip opportunity.",
    "mixed": "Conflicting signals across timeframes — reduce position size or wait for resolution.",
    "insufficient_data": "Not enough timeframe data to classify divergence.",
}

# ---------------------------------------------------------------------------
# Signal enumerations
# ---------------------------------------------------------------------------


class SignalStrength(str, Enum):
    """Signal strength levels for raw detection layer."""

    EXTREME_BULLISH = "EXTREME BULLISH"
    STRONG_BULLISH = "STRONG BULLISH"
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    STRONG_BEARISH = "STRONG BEARISH"
    EXTREME_BEARISH = "EXTREME BEARISH"
    SIGNIFICANT = "SIGNIFICANT"
    VERY_SIGNIFICANT = "VERY SIGNIFICANT"
    TRENDING = "TRENDING"


class SignalCategory(str, Enum):
    """Signal category labels for grouping and confluence weighting."""

    MA_CROSS = "MA_CROSS"
    MA_TREND = "MA_TREND"
    RSI = "RSI"
    MACD = "MACD"
    BOLLINGER = "BOLLINGER"
    BB_BREAKOUT = "BB_BREAKOUT"
    STOCHASTIC = "STOCHASTIC"
    VOLUME = "VOLUME"
    TREND = "TREND"
    PRICE_ACTION = "PRICE_ACTION"
    ADX = "ADX"
    ICHIMOKU = "ICHIMOKU"
    OBV_CMF = "OBV_CMF"
    SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"
    RANGE = "RANGE"
    MA_DISTANCE = "MA_DISTANCE"
    FIBONACCI = "FIBONACCI"


# ---------------------------------------------------------------------------
# Settings dataclass (reads env vars)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables.

    Attributes:
        env: Deployment mode — "local" or "cloud".
        gemini_api_key: API key for Gemini LLM. Optional in local mode.
        openrouter_api_key: API key for OpenRouter. Takes priority over Gemini when set.
        openrouter_model: OpenRouter model identifier.
        database_url: Postgres connection string. Required in cloud mode.
        output_dir: Directory for local JSON output files.
        log_level: Logging level string.
        gemini_model: Gemini model identifier.
        llm_enabled: True when any LLM key is available.
        llm_provider: Which LLM provider is active — "openrouter", "gemini", or "none".
    """

    env: str = field(default_factory=lambda: os.getenv("SIGNALS_ENV", "local"))
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    openrouter_api_key: str | None = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY"))
    openrouter_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", OPENROUTER_MODEL))
    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL"))
    output_dir: str = field(default_factory=lambda: os.getenv("OUTPUT_DIR", "./output"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", GEMINI_MODEL))

    @property
    def is_cloud(self) -> bool:
        """Return True when running in cloud mode."""
        return self.env == "cloud"

    @property
    def openrouter_enabled(self) -> bool:
        """Return True when an OpenRouter API key is available."""
        return bool(self.openrouter_api_key)

    @property
    def gemini_enabled(self) -> bool:
        """Return True when a Gemini API key is available."""
        return bool(self.gemini_api_key)

    @property
    def llm_enabled(self) -> bool:
        """Return True when any LLM provider key is available."""
        return self.openrouter_enabled or self.gemini_enabled

    @property
    def llm_provider(self) -> str:
        """Return the active LLM provider: 'openrouter', 'gemini', or 'none'.

        OpenRouter takes priority when its key is set.
        """
        if self.openrouter_enabled:
            return "openrouter"
        if self.gemini_enabled:
            return "gemini"
        return "none"

    def validate(self) -> list[str]:
        """Validate settings and return list of error strings.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []
        if self.is_cloud and not self.database_url:
            errors.append("DATABASE_URL is required in cloud mode")
        if self.is_cloud and not self.llm_enabled:
            errors.append("GEMINI_API_KEY or OPENROUTER_API_KEY is required in cloud mode")
        return errors


def get_settings() -> Settings:
    """Create and return a validated Settings instance.

    Returns:
        Populated Settings instance.
    """
    settings = Settings()
    errors = settings.validate()
    if errors:
        for err in errors:
            logger.warning("Config warning: %s", err)
    return settings
