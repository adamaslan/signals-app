"""Configuration and constants for the Signals App.

Centralizes all configuration values as frozen dataclasses and named constants.
No magic numbers — every threshold is a named constant here.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

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

FETCH_BACKOFF_MIN_SECONDS: Final[float] = 1.0
FETCH_BACKOFF_MAX_SECONDS: Final[float] = 10.0
STALE_FALLBACK_HOURS: Final[int] = 24

# ---------------------------------------------------------------------------
# Data quality gates
# ---------------------------------------------------------------------------

MIN_DATA_POINTS: Final[int] = 22
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
RSI_PERIOD: Final[int] = 14
MACD_FAST: Final[int] = 12
MACD_SLOW: Final[int] = 26
MACD_SIGNAL: Final[int] = 9
BOLLINGER_PERIOD: Final[int] = 20
BOLLINGER_STD: Final[float] = 2.0
STOCHASTIC_K_PERIOD: Final[int] = 14
STOCHASTIC_D_PERIOD: Final[int] = 3
ADX_PERIOD: Final[int] = 14
ATR_PERIOD: Final[int] = 14
VOLUME_MA_SHORT: Final[int] = 10
VOLUME_MA_LONG: Final[int] = 20
ICHIMOKU_TENKAN: Final[int] = 9
ICHIMOKU_KIJUN: Final[int] = 26
ICHIMOKU_SENKOU_B: Final[int] = 52
OBV_EMA_PERIOD: Final[int] = 20
CMF_PERIOD: Final[int] = 20

# ---------------------------------------------------------------------------
# RSI thresholds
# ---------------------------------------------------------------------------

RSI_OVERSOLD: Final[float] = 30.0
RSI_OVERBOUGHT: Final[float] = 70.0
RSI_EXTREME_OVERSOLD: Final[float] = 20.0
RSI_EXTREME_OVERBOUGHT: Final[float] = 80.0

# ---------------------------------------------------------------------------
# Stochastic thresholds
# ---------------------------------------------------------------------------

STOCH_OVERSOLD: Final[float] = 20.0
STOCH_OVERBOUGHT: Final[float] = 80.0

# ---------------------------------------------------------------------------
# Volume thresholds
# ---------------------------------------------------------------------------

VOLUME_SPIKE_1_5X: Final[float] = 1.5
VOLUME_SPIKE_2X: Final[float] = 2.0
VOLUME_SPIKE_3X: Final[float] = 3.0

# ---------------------------------------------------------------------------
# ADX / trend thresholds
# ---------------------------------------------------------------------------

ADX_TRENDING: Final[float] = 25.0
ADX_STRONG_TREND: Final[float] = 40.0
ADX_NO_TREND: Final[float] = 20.0

# ---------------------------------------------------------------------------
# Price action thresholds
# ---------------------------------------------------------------------------

LARGE_MOVE_PERCENT: Final[float] = 5.0

# ---------------------------------------------------------------------------
# Detector budget
# ---------------------------------------------------------------------------

DETECTOR_TIMEOUT_MS: Final[int] = 500
MAX_DETECTOR_FAILURES: Final[int] = 4

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

CONFLUENCE_BUY_THRESHOLD: Final[float] = 0.35
CONFLUENCE_SELL_THRESHOLD: Final[float] = -0.35
CONFLUENCE_BUY_MIN_SIGNALS: Final[int] = 3
CONFLUENCE_SELL_MIN_SIGNALS: Final[int] = 3

# ---------------------------------------------------------------------------
# LLM config
# ---------------------------------------------------------------------------

GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL: Final[str] = "gemini-2.0-flash"
GEMINI_TIMEOUT_SECONDS: Final[float] = 10.0
GEMINI_BREAKER_FAILURE_THRESHOLD: Final[int] = 5
GEMINI_BREAKER_WINDOW_SECONDS: Final[float] = 60.0
GEMINI_BREAKER_OPEN_SECONDS: Final[float] = 300.0

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


# ---------------------------------------------------------------------------
# Settings dataclass (reads env vars)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables.

    Attributes:
        env: Deployment mode — "local" or "cloud".
        gemini_api_key: API key for Gemini LLM. Optional in local mode.
        database_url: Postgres connection string. Required in cloud mode.
        output_dir: Directory for local JSON output files.
        log_level: Logging level string.
        gemini_model: Gemini model identifier.
        llm_enabled: True when Gemini API key is available.
    """

    env: str = field(default_factory=lambda: os.getenv("SIGNALS_ENV", "local"))
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL"))
    output_dir: str = field(default_factory=lambda: os.getenv("OUTPUT_DIR", "./output"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", GEMINI_MODEL))

    @property
    def is_cloud(self) -> bool:
        """Return True when running in cloud mode."""
        return self.env == "cloud"

    @property
    def llm_enabled(self) -> bool:
        """Return True when a Gemini API key is available."""
        return self.gemini_api_key is not None and len(self.gemini_api_key) > 0

    def validate(self) -> list[str]:
        """Validate settings and return list of error strings.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []
        if self.is_cloud and not self.database_url:
            errors.append("DATABASE_URL is required in cloud mode")
        if self.is_cloud and not self.llm_enabled:
            errors.append("GEMINI_API_KEY is required in cloud mode")
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
