"""Client for the Hold Em / Fold Em verdict API (``holdemfoldemapp``).

The base URL comes from ``HOLDEM_API_URL`` (e.g. ``http://localhost:8001`` for
local dev). Verdict-threshold overrides are forwarded as the request's
``params`` object, so a chain can ask "would this be HOLD EM at a 65 bar?"
without redeploying holdem.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

HOLDEM_API_URL_ENV: Final[str] = "HOLDEM_API_URL"
HOLDEM_TIMEOUT_SECONDS: Final[float] = 45.0


class HoldemUnavailable(RuntimeError):  # noqa: N818 — matches service.py's naming
    """The holdem API is not configured or did not answer."""


def holdem_base_url() -> str | None:
    """Configured holdem base URL without a trailing slash, or None."""
    raw = os.getenv(HOLDEM_API_URL_ENV, "").strip()
    return raw.rstrip("/") or None


class HoldemClient:
    """Thin async wrapper over ``POST /api/analyze`` and ``GET /api/params``."""

    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url
        self._client = client

    async def verdict(
        self, symbol: str, *, period: str = "3mo", params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch one verdict; raises :class:`HoldemUnavailable` on any failure."""
        body: dict[str, Any] = {"symbol": symbol, "period": period}
        if params:
            body["params"] = params
        try:
            resp = await self._client.post(f"{self._base_url}/api/analyze", json=body)
        except httpx.HTTPError as exc:
            raise HoldemUnavailable(f"holdem request failed for {symbol}: {exc}") from exc
        if resp.status_code != httpx.codes.OK:
            raise HoldemUnavailable(
                f"holdem returned {resp.status_code} for {symbol}: {resp.text[:200]}"
            )
        return resp.json()
