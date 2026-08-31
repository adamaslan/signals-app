"""Local CLI universes — named ticker baskets on disk (design doc §3.5).

The frontend's universes live in IndexedDB (browser-only). The CLI's live as
plain CSV in ``~/.signals/universes/<name>.csv`` — human-editable,
git-committable, diffable. This module is just the file I/O + the JSON
exchange format the browser's ``exportUniverse()`` / ``importUniverse()`` use,
so a basket moves between browser and shell in either direction.

No pipeline knowledge here — ``signals_app.service`` does the analysis; this
only resolves a name to a list of tickers.
"""
from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Overridable for tests / non-default installs.
_ENV_DIR = "SIGNALS_UNIVERSE_DIR"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class UniverseError(Exception):
    """Base for universe file problems."""


class UniverseNotFound(UniverseError):  # noqa: N818 — matches service.py's naming
    """No universe with that name exists on disk."""


class UniverseExists(UniverseError):  # noqa: N818
    """A universe with that name already exists (create would clobber it)."""


class InvalidUniverseName(UniverseError):  # noqa: N818
    """The name isn't a safe filename stem (lowercase, digits, - and _ only)."""


@dataclass(frozen=True)
class Universe:
    """A named basket of tickers."""

    name: str
    tickers: list[str]
    path: Path

    @property
    def size(self) -> int:
        return len(self.tickers)


def universe_dir() -> Path:
    """The directory universes live in — ``$SIGNALS_UNIVERSE_DIR`` or ``~/.signals/universes``."""
    override = os.getenv(_ENV_DIR)
    return Path(override) if override else Path.home() / ".signals" / "universes"


def _validate_name(name: str) -> str:
    n = name.strip().lower()
    if not _NAME_RE.match(n):
        raise InvalidUniverseName(
            f"invalid universe name {name!r} — use lowercase letters, digits, '-' and '_' only"
        )
    return n


def _path_for(name: str) -> Path:
    return universe_dir() / f"{_validate_name(name)}.csv"


def _normalize_tickers(raw: list[str]) -> list[str]:
    """Upper/strip, drop blanks, de-dupe while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for r in raw:
        t = r.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def list_universes() -> list[Universe]:
    """Every universe on disk, name-sorted. Empty list if the dir doesn't exist."""
    d = universe_dir()
    if not d.is_dir():
        return []
    out: list[Universe] = []
    for p in sorted(d.glob("*.csv")):
        out.append(load_universe(p.stem))
    return out


def load_universe(name: str) -> Universe:
    """Read one universe.

    Raises:
        UniverseNotFound: No file for that name.
    """
    path = _path_for(name)
    if not path.is_file():
        raise UniverseNotFound(f"no universe named {name!r} (looked in {universe_dir()})")
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    tickers = _normalize_tickers([r.get("ticker", "") for r in rows])
    return Universe(name=_validate_name(name), tickers=tickers, path=path)


def create_universe(name: str, tickers: list[str], *, overwrite: bool = False) -> Universe:
    """Write a new universe.

    Raises:
        UniverseExists: A universe of that name exists and ``overwrite`` is False.
        InvalidUniverseName: The name isn't a safe filename stem.
    """
    path = _path_for(name)
    if path.exists() and not overwrite:
        raise UniverseExists(f"universe {name!r} already exists at {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = _normalize_tickers(tickers)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker"])
        for t in clean:
            w.writerow([t])
    return Universe(name=_validate_name(name), tickers=clean, path=path)


def delete_universe(name: str) -> None:
    """Remove a universe file.

    Raises:
        UniverseNotFound: No file for that name.
    """
    path = _path_for(name)
    if not path.is_file():
        raise UniverseNotFound(f"no universe named {name!r}")
    path.unlink()


def tickers_from_csv(path: str | Path) -> list[str]:
    """Read a ``ticker`` column out of an arbitrary CSV (for ``--from``)."""
    with open(path, newline="") as f:
        return _normalize_tickers([r.get("ticker", "") for r in csv.DictReader(f)])


# ---------------------------------------------------------------------------
# Browser interop — the exportUniverse() / importUniverse() JSON (companion §3)
# ---------------------------------------------------------------------------

_EXPORT_VERSION = 1


def to_export_json(u: Universe) -> str:
    """Serialize a universe to the browser exchange JSON."""
    return json.dumps(
        {
            "version": _EXPORT_VERSION,
            "name": u.name,
            "tickers": u.tickers,
            "exported_at": datetime.now(UTC).isoformat(),
            "source": "signals-cli",
        },
        indent=2,
    )


def from_export_json(text: str) -> tuple[str, list[str]]:
    """Parse the browser exchange JSON → ``(name, tickers)``.

    Tolerant of a bare ``{"tickers": [...]}`` and of a top-level list.

    Raises:
        UniverseError: The JSON has no usable ticker list.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UniverseError(f"not valid JSON: {exc}") from exc

    if isinstance(data, list):
        return "imported", _normalize_tickers([str(x) for x in data])
    if isinstance(data, dict):
        raw = data.get("tickers") or data.get("symbols") or []
        name = str(data.get("name") or "imported")
        if raw:
            return name, _normalize_tickers([str(x) for x in raw])
    raise UniverseError("JSON contains no 'tickers' list")
