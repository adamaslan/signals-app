#!/usr/bin/env python3
"""Rich universe-scan report — every ticker, every category, in detail.

``scripts/scan_universe.py --dry-run`` answers "how many published?" and
nothing else. This script answers "what did the engine actually *see* across
the universe?" — per-symbol confluence, bias, top signals, per-category
firing, and the gate verdict *with its reason*, plus universe-wide
aggregates (category firing rates, bias distribution, strongest names,
publish/gate/fail breakdown).

It is read-only: it never synthesizes (no LLM calls) and never writes to
Supabase. It re-uses ``signals_app``'s own layers (fetch → indicators →
detect → confluence → gate) so it cannot drift from the real scan.

Usage:
    python scripts/scan_universe_report.py AAPL MSFT NVDA
    python scripts/scan_universe_report.py --seed seed/universe_symbols.csv --limit 50
    python scripts/scan_universe_report.py --seed seed/universe_symbols.csv \\
        --limit 100 --format all
    python scripts/scan_universe_report.py AAPL --format html --stdout

Reports are written to ``scans3/`` (created if absent; override with
``--out-dir``). Filenames are ``universe-scan_{YYYYmmdd-HHMMSS}.{ext}`` so
repeated runs accumulate rather than overwrite. Pass ``--stdout`` to print
instead of writing a file.

Output formats (``--format``):
    markdown  (default)  human-readable report
    html                 standalone, offline-openable, light/dark
    json                 the full structured payload
    all                  markdown + html + json

Run it in the ``signals-app`` mamba env:
    mamba run -n signals-app python scripts/scan_universe_report.py AAPL --limit 1
"""
from __future__ import annotations

import argparse
import html as _html
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT))

from signals_app.config import (  # noqa: E402
    DEFAULT_PERIOD,
    PUBLISH_MIN_CONFLUENCE_SCORE,
    PUBLISH_MIN_DATA_QUALITY,
    PUBLISH_MIN_SIGNALS,
    SignalCategory,
    SignalStrength,
    get_settings,
)
from signals_app.data.fetcher import DataFetcher  # noqa: E402
from signals_app.detection.orchestrator import detect_all_signals  # noqa: E402
from signals_app.indicators.compute import compute_indicators  # noqa: E402
from signals_app.indicators.data_quality import score_data_quality  # noqa: E402
from signals_app.scanner import (  # noqa: E402
    apply_shard,
    load_symbols_from_csv,
    parse_shard_spec,
    passes_publication_gate,
)
from signals_app.scoring.confluence import ConfluenceRanker  # noqa: E402

logger = logging.getLogger(__name__)

MAX_CONCURRENT_FETCHES = 4
MIN_BARS_TO_SCORE = 20
DEFAULT_OUT_DIR = "scans3"
TOP_SIGNALS_PER_SYMBOL = 8
STRONGEST_NAMES_IN_SUMMARY = 15

#: SignalStrength value -> signed weight, for ranking a symbol's own signals
#: by conviction (mirrors the spirit of ConfluenceRanker's strength table
#: without importing its private constants).
_STRENGTH_RANK: dict[str, float] = {
    SignalStrength.EXTREME_BULLISH.value: 3.0,
    SignalStrength.STRONG_BULLISH.value: 2.0,
    SignalStrength.BULLISH.value: 1.0,
    SignalStrength.TRENDING.value: 0.5,
    SignalStrength.SIGNIFICANT.value: 0.5,
    SignalStrength.VERY_SIGNIFICANT.value: 0.75,
    SignalStrength.NEUTRAL.value: 0.0,
    SignalStrength.BEARISH.value: -1.0,
    SignalStrength.STRONG_BEARISH.value: -2.0,
    SignalStrength.EXTREME_BEARISH.value: -3.0,
}
_BULLISH_STRENGTHS = {
    SignalStrength.EXTREME_BULLISH.value,
    SignalStrength.STRONG_BULLISH.value,
    SignalStrength.BULLISH.value,
}
_BEARISH_STRENGTHS = {
    SignalStrength.EXTREME_BEARISH.value,
    SignalStrength.STRONG_BEARISH.value,
    SignalStrength.BEARISH.value,
}
ALL_CATEGORIES: tuple[str, ...] = tuple(c.value for c in SignalCategory)


# ---------------------------------------------------------------------------
# Per-symbol result
# ---------------------------------------------------------------------------
@dataclass
class SymbolReport:
    """Everything the report shows for one ticker."""

    ticker: str
    ok: bool
    published: bool
    gate_reason: str | None = None          # why gated, or the error string
    error: str | None = None

    bars: int = 0
    last_bar_ts: str | None = None
    close: float | None = None

    data_quality: float | None = None
    data_quality_reasons: list[str] = field(default_factory=list)

    confluence_score: float | None = None
    bias: str | None = None
    action: str | None = None
    confidence_label: str | None = None
    bull_count: int = 0
    bear_count: int = 0
    neutral_count: int = 0
    total_signals: int = 0

    degraded: bool = False
    detector_warnings: list[str] = field(default_factory=list)

    #: category -> {"total": n, "bull": n, "bear": n}
    category_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)
    #: the highest-conviction signals on the latest scored bar
    top_signals: list[dict] = field(default_factory=list)


def _blank_breakdown() -> dict[str, dict[str, int]]:
    return {c: {"total": 0, "bull": 0, "bear": 0} for c in ALL_CATEGORIES}


def _scan_one(ticker: str, period: str, settings, strength_hit_rates) -> SymbolReport:
    """L1–L4 for one ticker + the gate verdict. Never raises."""
    try:
        fetcher = DataFetcher(settings=settings)
        ohlcv = fetcher.fetch(ticker, period)
        df_raw = ohlcv.df
        bars = len(df_raw)
        if bars < MIN_BARS_TO_SCORE:
            return SymbolReport(
                ticker, ok=False, published=False,
                gate_reason="insufficient_bars", bars=bars,
            )

        dq = score_data_quality(df_raw, period)
        df = compute_indicators(df_raw)
        signal_list = detect_all_signals(df)

        ranker = ConfluenceRanker()
        conf = ranker.rank_signals(
            list(signal_list), strength_hit_rates=strength_hit_rates
        )

        last_ts = df.index[-1].isoformat()
        try:
            close = float(df["Close"].iloc[-1])
        except Exception:
            close = None

        breakdown = _blank_breakdown()
        for s in signal_list:
            cat = s.category if s.category in breakdown else _coerce_category(s.category)
            if cat not in breakdown:
                breakdown[cat] = {"total": 0, "bull": 0, "bear": 0}
            breakdown[cat]["total"] += 1
            if s.strength in _BULLISH_STRENGTHS:
                breakdown[cat]["bull"] += 1
            elif s.strength in _BEARISH_STRENGTHS:
                breakdown[cat]["bear"] += 1

        top = sorted(
            signal_list,
            key=lambda s: abs(_STRENGTH_RANK.get(s.strength, 0.0)),
            reverse=True,
        )[:TOP_SIGNALS_PER_SYMBOL]
        top_signals = [
            {
                "signal": s.signal,
                "description": s.description,
                "strength": s.strength,
                "category": s.category,
            }
            for s in top
        ]

        published = passes_publication_gate(
            dq.score, len(signal_list), conf.score, signal_list.degraded
        )
        gate_reason = None if published else _explain_gate(
            dq.score, len(signal_list), conf.score
        )

        return SymbolReport(
            ticker=ticker,
            ok=True,
            published=published,
            gate_reason=gate_reason,
            bars=bars,
            last_bar_ts=last_ts,
            close=close,
            data_quality=round(dq.score, 4),
            data_quality_reasons=list(dq.reasons),
            confluence_score=conf.score,
            bias=conf.bias,
            action=conf.action,
            confidence_label=conf.confidence_label,
            bull_count=conf.bull_count,
            bear_count=conf.bear_count,
            neutral_count=conf.neutral_count,
            total_signals=len(signal_list),
            degraded=signal_list.degraded,
            detector_warnings=list(getattr(signal_list, "warnings", []) or []),
            category_breakdown=breakdown,
            top_signals=top_signals,
        )
    except Exception as exc:  # noqa: BLE001 — one bad ticker never aborts the run
        logger.warning("scan_universe_report: %s failed: %s", ticker, exc)
        return SymbolReport(
            ticker, ok=False, published=False, gate_reason="error", error=str(exc)
        )


def _coerce_category(raw: str) -> str:
    """Best-effort: keep unknown category strings visible rather than dropping."""
    return raw if raw else "UNKNOWN"


def _explain_gate(dq_score: float | None, n_signals: int, confluence: float) -> str:
    """Which gate condition failed, in the same order passes_publication_gate
    checks. Per-symbol wording; :func:`_gate_bucket` collapses these for the
    universe-wide distribution.
    """
    if dq_score is None or dq_score < PUBLISH_MIN_DATA_QUALITY:
        got = "None" if dq_score is None else f"{dq_score:.2f}"
        return f"data_quality {got} < {PUBLISH_MIN_DATA_QUALITY}"
    if n_signals < PUBLISH_MIN_SIGNALS:
        return f"only {n_signals} signals < {PUBLISH_MIN_SIGNALS}"
    if abs(confluence) < PUBLISH_MIN_CONFLUENCE_SCORE:
        return (
            f"|confluence| {abs(confluence):.3f} < {PUBLISH_MIN_CONFLUENCE_SCORE} "
            f"(too neutral)"
        )
    return "gated"


def _gate_bucket(reason: str | None) -> str:
    """Collapse a per-symbol gate reason to one of a few stable buckets so the
    universe-wide distribution counts *kinds* of rejection, not values."""
    if not reason:
        return "gated"
    if reason.startswith("data_quality"):
        return f"data_quality < {PUBLISH_MIN_DATA_QUALITY}"
    if "signals <" in reason:
        return f"fewer than {PUBLISH_MIN_SIGNALS} signals"
    if reason.startswith("|confluence|"):
        return f"|confluence| < {PUBLISH_MIN_CONFLUENCE_SCORE} (too neutral)"
    if reason == "insufficient_bars":
        return "insufficient_bars"
    return reason


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
@dataclass
class UniverseReport:
    generated_at: str
    period: str
    symbols_requested: int
    symbols_scanned: int
    symbols_ok: int
    symbols_failed: int
    symbols_published: int
    symbols_gated: int
    elapsed_seconds: float

    bias_distribution: dict[str, int]
    action_distribution: dict[str, int]
    confidence_distribution: dict[str, int]
    gate_reason_distribution: dict[str, int]

    #: category -> {"symbols_firing": n, "total_hits": n, "bull_hits": n, "bear_hits": n, "fire_rate_pct": pct}
    category_stats: dict[str, dict[str, float]]

    strongest_bullish: list[dict]
    strongest_bearish: list[dict]
    published_symbols: list[str]
    failures: list[dict]

    symbols: list[dict]  # each SymbolReport as a dict


def _aggregate(reports: list[SymbolReport], period: str, elapsed: float,
               requested: int) -> UniverseReport:
    ok = [r for r in reports if r.ok]
    failed = [r for r in reports if not r.ok]
    published = [r for r in ok if r.published]
    gated = [r for r in ok if not r.published]

    bias_dist = Counter(r.bias for r in ok if r.bias)
    action_dist = Counter(r.action for r in ok if r.action)
    conf_dist = Counter(r.confidence_label for r in ok if r.confidence_label)
    gate_dist = Counter(_gate_bucket(r.gate_reason) for r in gated)

    cat_symbols_firing: Counter = Counter()
    cat_total_hits: Counter = Counter()
    cat_bull_hits: Counter = Counter()
    cat_bear_hits: Counter = Counter()
    for r in ok:
        for cat, b in r.category_breakdown.items():
            if b["total"] > 0:
                cat_symbols_firing[cat] += 1
                cat_total_hits[cat] += b["total"]
                cat_bull_hits[cat] += b["bull"]
                cat_bear_hits[cat] += b["bear"]

    n_ok = len(ok) or 1
    category_stats = {}
    for cat in ALL_CATEGORIES:
        category_stats[cat] = {
            "symbols_firing": cat_symbols_firing[cat],
            "total_hits": cat_total_hits[cat],
            "bull_hits": cat_bull_hits[cat],
            "bear_hits": cat_bear_hits[cat],
            "fire_rate_pct": round(100.0 * cat_symbols_firing[cat] / n_ok, 1),
        }

    ranked = sorted(
        (r for r in ok if r.confluence_score is not None),
        key=lambda r: r.confluence_score,
    )
    strongest_bearish = [
        {"ticker": r.ticker, "confluence": r.confluence_score, "bias": r.bias,
         "action": r.action, "signals": r.total_signals}
        for r in ranked[:STRONGEST_NAMES_IN_SUMMARY]
        if (r.confluence_score or 0) < 0
    ]
    strongest_bullish = [
        {"ticker": r.ticker, "confluence": r.confluence_score, "bias": r.bias,
         "action": r.action, "signals": r.total_signals}
        for r in reversed(ranked[-STRONGEST_NAMES_IN_SUMMARY:])
        if (r.confluence_score or 0) > 0
    ]

    return UniverseReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        period=period,
        symbols_requested=requested,
        symbols_scanned=len(reports),
        symbols_ok=len(ok),
        symbols_failed=len(failed),
        symbols_published=len(published),
        symbols_gated=len(gated),
        elapsed_seconds=round(elapsed, 1),
        bias_distribution=dict(bias_dist),
        action_distribution=dict(action_dist),
        confidence_distribution=dict(conf_dist),
        gate_reason_distribution=dict(gate_dist),
        category_stats=category_stats,
        strongest_bullish=strongest_bullish,
        strongest_bearish=strongest_bearish,
        published_symbols=[r.ticker for r in published],
        failures=[{"ticker": r.ticker, "reason": r.error or r.gate_reason}
                  for r in failed],
        symbols=[asdict(r) for r in reports],
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
def render_markdown(u: UniverseReport) -> str:
    L: list[str] = []
    L.append(f"# Universe Signal Scan — {u.generated_at[:19]}Z")
    L.append("")
    L.append(
        f"**{u.symbols_scanned}** symbols scanned · **{u.symbols_ok}** ok · "
        f"**{u.symbols_failed}** failed · **{u.symbols_published}** cleared the "
        f"publication gate · **{u.symbols_gated}** gated · {u.elapsed_seconds}s · "
        f"period `{u.period}`"
    )
    L.append("")
    L.append(
        "The publication gate requires: data-quality ≥ "
        f"{PUBLISH_MIN_DATA_QUALITY}, ≥ {PUBLISH_MIN_SIGNALS} signals, and "
        f"|confluence| ≥ {PUBLISH_MIN_CONFLUENCE_SCORE}. Most symbols *should* "
        "fail it — an engine that always fires carries no information."
    )
    L.append("")

    L.append("## Bias & action distribution")
    L.append("")
    L.append("| Bias | Symbols | | Action | Symbols | | Confidence | Symbols |")
    L.append("|---|---:|---|---|---:|---|---|---:|")
    biases = list(u.bias_distribution.items()) or [("—", 0)]
    actions = list(u.action_distribution.items()) or [("—", 0)]
    confs = list(u.confidence_distribution.items()) or [("—", 0)]
    for i in range(max(len(biases), len(actions), len(confs))):
        b = biases[i] if i < len(biases) else ("", "")
        a = actions[i] if i < len(actions) else ("", "")
        c = confs[i] if i < len(confs) else ("", "")
        L.append(f"| {b[0]} | {b[1]} | | {a[0]} | {a[1]} | | {c[0]} | {c[1]} |")
    L.append("")
    if u.published_symbols:
        L.append("## Published (cleared the gate)")
        L.append("")
        L.append(", ".join(f"`{t}`" for t in u.published_symbols))
        L.append("")

    L.append("## Per-symbol detail")
    L.append("")
    for s in u.symbols:
        L.append(_markdown_symbol_block(s))
        L.append("")

    if u.failures:
        L.append("## Failures")
        L.append("")
        L.append("| Ticker | Reason |")
        L.append("|---|---|")
        for f in u.failures:
            L.append(f"| {f['ticker']} | {f['reason']} |")
        L.append("")

    return "\n".join(L)


def _markdown_symbol_block(s: dict) -> str:
    L: list[str] = []
    status = (
        "✅ PUBLISHED" if s["published"]
        else ("❌ FAILED" if not s["ok"] else "· gated")
    )
    head = f"### {s['ticker']} — {status}"
    L.append(head)
    if not s["ok"]:
        L.append("")
        L.append(f"- **error / reason:** {s.get('error') or s.get('gate_reason')}")
        L.append(f"- bars fetched: {s['bars']}")
        return "\n".join(L)

    price = f"{s['close']:.2f}" if s.get("close") is not None else "—"
    L.append("")
    L.append(
        f"- close **{price}** · {s['bars']} bars · last bar "
        f"`{(s.get('last_bar_ts') or '')[:19]}`"
    )
    L.append(
        f"- **confluence {s['confluence_score']:+.3f}** → bias *{s['bias']}*, "
        f"action *{s['action']}*, confidence *{s['confidence_label']}*"
    )
    L.append(
        f"- signals: {s['total_signals']} total "
        f"({s['bull_count']} bull / {s['bear_count']} bear / "
        f"{s['neutral_count']} neutral)"
    )
    L.append(
        f"- data quality: {s['data_quality']}"
        + (f" — {', '.join(s['data_quality_reasons'])}"
           if s.get("data_quality_reasons") else "")
    )
    if s["degraded"]:
        L.append(
            f"- ⚠️ detection **degraded**: "
            f"{'; '.join(s.get('detector_warnings') or []) or 'some detectors failed'}"
        )
    if not s["published"]:
        L.append(f"- gate: **{s['gate_reason']}**")

    fired = {
        c: b for c, b in s["category_breakdown"].items() if b["total"] > 0
    }
    if fired:
        L.append("")
        L.append("  | Category | Hits | Bull | Bear |")
        L.append("  |---|---:|---:|---:|")
        for c, b in sorted(fired.items(), key=lambda kv: -kv[1]["total"]):
            L.append(f"  | {c} | {b['total']} | {b['bull']} | {b['bear']} |")

    if s["top_signals"]:
        L.append("")
        L.append("  **Top signals (by conviction):**")
        for t in s["top_signals"]:
            L.append(
                f"  - `{t['strength']}` **{t['signal']}** "
                f"({t['category']}) — {t['description']}"
            )
    return "\n".join(L)


_HTML_STYLE = """
:root { color-scheme: light dark;
  --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#ddd; --accent:#0b6; --bear:#c33;
  --card:#fafafa; }
@media (prefers-color-scheme: dark) { :root {
  --bg:#14161a; --fg:#e6e6e6; --muted:#9aa; --line:#333; --accent:#3d9; --bear:#e66;
  --card:#1b1e24; } }
* { box-sizing:border-box; }
body { margin:0; padding:2rem 1.25rem 5rem; background:var(--bg); color:var(--fg);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  max-width:1080px; margin-inline:auto; }
h1 { font-size:1.6rem; margin:0 0 .3rem; }
h2 { font-size:1.2rem; margin:2.4rem 0 .7rem; border-bottom:1px solid var(--line);
  padding-bottom:.3rem; }
h3 { font-size:1rem; margin:1.6rem 0 .4rem; }
.lede { color:var(--muted); }
table { border-collapse:collapse; width:100%; margin:.6rem 0 1rem; font-size:.92rem; }
th,td { border:1px solid var(--line); padding:.32rem .55rem; text-align:left; }
th { background:var(--card); }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
.sym { background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:.8rem 1rem; margin:.7rem 0; }
.pub { color:var(--accent); font-weight:700; }
.fail { color:var(--bear); font-weight:700; }
.gate { color:var(--muted); }
.pos { color:var(--accent); } .neg { color:var(--bear); }
code { background:rgba(128,128,128,.15); padding:.05rem .3rem; border-radius:4px;
  font-size:.88em; }
ul { margin:.3rem 0 .3rem 1.1rem; padding:0; }
.bar { display:inline-block; height:.7em; background:var(--accent); border-radius:2px;
  vertical-align:middle; }
"""


def _esc(x) -> str:
    return _html.escape(str(x), quote=True)


def render_html(u: UniverseReport) -> str:
    P: list[str] = []
    P.append("<!doctype html><html lang=en><head><meta charset=utf-8>")
    P.append('<meta name=viewport content="width=device-width,initial-scale=1">')
    P.append(f"<title>Universe Signal Scan — {_esc(u.generated_at[:10])}</title>")
    P.append(f"<style>{_HTML_STYLE}</style></head><body>")
    P.append(f"<h1>Universe Signal Scan</h1>")
    P.append(
        f"<p class=lede>{_esc(u.generated_at[:19])}Z · period "
        f"<code>{_esc(u.period)}</code> · {u.elapsed_seconds}s</p>"
    )
    P.append(
        "<p><b>{scanned}</b> scanned · <b>{ok}</b> ok · <b>{fail}</b> failed · "
        "<b class=pub>{pub}</b> published · <b>{gated}</b> gated</p>".format(
            scanned=u.symbols_scanned, ok=u.symbols_ok, fail=u.symbols_failed,
            pub=u.symbols_published, gated=u.symbols_gated,
        )
    )
    P.append(
        f"<p class=lede>Publication gate: data-quality ≥ {PUBLISH_MIN_DATA_QUALITY}, "
        f"≥ {PUBLISH_MIN_SIGNALS} signals, |confluence| ≥ "
        f"{PUBLISH_MIN_CONFLUENCE_SCORE}.</p>"
    )

    P.append("<h2>Bias &amp; action distribution</h2><table>")
    P.append("<tr><th>Bias</th><th class=n>Symbols</th><th>Action</th>"
             "<th class=n>Symbols</th><th>Confidence</th><th class=n>Symbols</th></tr>")
    biases = list(u.bias_distribution.items()) or [("—", 0)]
    actions = list(u.action_distribution.items()) or [("—", 0)]
    confs = list(u.confidence_distribution.items()) or [("—", 0)]
    for i in range(max(len(biases), len(actions), len(confs))):
        b = biases[i] if i < len(biases) else ("", "")
        a = actions[i] if i < len(actions) else ("", "")
        c = confs[i] if i < len(confs) else ("", "")
        P.append(
            f"<tr><td>{_esc(b[0])}</td><td class=n>{_esc(b[1])}</td>"
            f"<td>{_esc(a[0])}</td><td class=n>{_esc(a[1])}</td>"
            f"<td>{_esc(c[0])}</td><td class=n>{_esc(c[1])}</td></tr>"
        )
    P.append("</table>")

    if u.published_symbols:
        P.append("<h2>Published (cleared the gate)</h2><p>")
        P.append(" ".join(f"<code>{_esc(t)}</code>" for t in u.published_symbols))
        P.append("</p>")

    P.append("<h2>Per-symbol detail</h2>")
    for s in u.symbols:
        P.append(_html_symbol_block(s))

    if u.failures:
        P.append("<h2>Failures</h2><table><tr><th>Ticker</th><th>Reason</th></tr>")
        for f in u.failures:
            P.append(f"<tr><td>{_esc(f['ticker'])}</td>"
                     f"<td>{_esc(f['reason'])}</td></tr>")
        P.append("</table>")

    P.append("</body></html>")
    return "".join(P)


def _html_symbol_block(s: dict) -> str:
    P: list[str] = ["<div class=sym>"]
    if not s["ok"]:
        P.append(
            f"<h3>{_esc(s['ticker'])} — <span class=fail>FAILED</span></h3>"
            f"<p>{_esc(s.get('error') or s.get('gate_reason'))} · "
            f"{s['bars']} bars</p></div>"
        )
        return "".join(P)

    status = ("<span class=pub>PUBLISHED</span>" if s["published"]
              else "<span class=gate>gated</span>")
    price = f"{s['close']:.2f}" if s.get("close") is not None else "—"
    cscls = "pos" if (s["confluence_score"] or 0) >= 0 else "neg"
    P.append(f"<h3>{_esc(s['ticker'])} — {status}</h3>")
    P.append(
        f"<p>close <b>{price}</b> · {s['bars']} bars · "
        f"<span class='{cscls}'><b>confluence {s['confluence_score']:+.3f}</b></span> "
        f"→ {_esc(s['bias'])} / {_esc(s['action'])} / "
        f"{_esc(s['confidence_label'])} · "
        f"{s['total_signals']} signals ({s['bull_count']}▲ {s['bear_count']}▼ "
        f"{s['neutral_count']}●) · dq {s['data_quality']}"
        + (f" · <span class=gate>gate: {_esc(s['gate_reason'])}</span>"
           if not s["published"] else "")
        + "</p>"
    )
    if s["degraded"]:
        P.append(
            f"<p class=fail>⚠ detection degraded: "
            f"{_esc('; '.join(s.get('detector_warnings') or []) or 'some detectors failed')}</p>"
        )

    fired = {c: b for c, b in s["category_breakdown"].items() if b["total"] > 0}
    if fired:
        P.append("<table><tr><th>Category</th><th class=n>Hits</th>"
                 "<th class=n>Bull</th><th class=n>Bear</th></tr>")
        for c, b in sorted(fired.items(), key=lambda kv: -kv[1]["total"]):
            P.append(
                f"<tr><td>{_esc(c)}</td><td class=n>{b['total']}</td>"
                f"<td class=n>{b['bull']}</td><td class=n>{b['bear']}</td></tr>"
            )
        P.append("</table>")

    if s["top_signals"]:
        P.append("<p><b>Top signals:</b></p><ul>")
        for t in s["top_signals"]:
            P.append(
                f"<li><code>{_esc(t['strength'])}</code> "
                f"<b>{_esc(t['signal'])}</b> ({_esc(t['category'])}) — "
                f"{_esc(t['description'])}</li>"
            )
        P.append("</ul>")
    P.append("</div>")
    return "".join(P)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _run(symbols: list[str], period: str, max_concurrent: int) -> UniverseReport:
    settings = get_settings()
    # Uncalibrated: this is a read-only report, we don't touch Supabase.
    strength_hit_rates: dict[str, float] | None = None

    reports: list[SymbolReport] = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {
            pool.submit(_scan_one, t, period, settings, strength_hit_rates): t
            for t in symbols
        }
        done = 0
        total = len(futures)
        for fut in as_completed(futures):
            r = fut.result()
            reports.append(r)
            done += 1
            tag = ("published" if r.published
                   else ("FAILED" if not r.ok else "gated"))
            logger.info("[%d/%d] %s — %s", done, total, r.ticker, tag)
    elapsed = time.perf_counter() - started
    reports.sort(key=lambda r: r.ticker)
    return _aggregate(reports, period, elapsed, requested=len(symbols))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("symbols", nargs="*", help="Ticker symbols to scan")
    p.add_argument("--seed", help="CSV with a 'ticker' column")
    p.add_argument("--limit", type=int, default=None, help="Cap symbols scanned")
    p.add_argument("--shard", default=None, metavar="INDEX/TOTAL",
                   help="Process every TOTAL-th symbol from INDEX (post-sort)")
    p.add_argument("--period", default=DEFAULT_PERIOD)
    p.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT_FETCHES)
    p.add_argument("--format", default="markdown",
                   choices=["markdown", "html", "json", "all"])
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                   help=f"Directory for report file(s) (default: {DEFAULT_OUT_DIR}/)")
    p.add_argument("--stdout", action="store_true",
                   help="Print the report instead of writing a file "
                        "(ignored for --format all)")
    args = p.parse_args()

    symbols = list(args.symbols)
    if args.seed:
        symbols.extend(load_symbols_from_csv(args.seed))
    if not symbols:
        p.error("no symbols — pass tickers or use --seed")
    symbols = sorted(set(symbols))

    if args.shard:
        try:
            idx, tot = parse_shard_spec(args.shard)
        except ValueError as exc:
            p.error(str(exc))
        symbols = apply_shard(symbols, idx, tot)
    if args.limit:
        symbols = symbols[: args.limit]

    logger.info("scanning %d symbols, period=%s, concurrency=%d",
                len(symbols), args.period, args.max_concurrent)
    u = _run(symbols, args.period, args.max_concurrent)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # --format all always writes files (can't print 3 documents to one stream).
    to_stdout = args.stdout and args.format != "all"
    out_dir: Path | None = None
    if not to_stdout:
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = _PROJECT_ROOT / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)

    def _emit(fmt: str, text: str, ext: str) -> None:
        if out_dir is not None:
            path = out_dir / f"universe-scan_{stamp}.{ext}"
            path.write_text(text, encoding="utf-8")
            logger.info("wrote %s", path)
        else:
            sys.stdout.write(text + ("\n" if not text.endswith("\n") else ""))

    if args.format in ("markdown", "all"):
        _emit("markdown", render_markdown(u), "md")
    if args.format in ("html", "all"):
        _emit("html", render_html(u), "html")
    if args.format in ("json", "all"):
        _emit("json", json.dumps(asdict(u), indent=2, default=str), "json")

    # Always leave a one-line summary on stderr so piping stdout to a file
    # still shows progress at the terminal.
    sys.stderr.write(
        f"\nDone: {u.symbols_scanned} scanned, {u.symbols_published} published, "
        f"{u.symbols_gated} gated, {u.symbols_failed} failed "
        f"({u.elapsed_seconds}s)\n"
    )


if __name__ == "__main__":
    main()
