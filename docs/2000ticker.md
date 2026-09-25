---
Date: 2026-09-24
Branch: feat/landing-signal-guide
Status: Research, nothing built yet. Recommends a design and sizes it.
---

# A second universe: 2,000 more tickers

## TL;DR

- **Source:** the free Nasdaq Trader symbol directory (`nasdaqlisted.txt` +
  `otherlisted.txt`). Filter it to US-listed common stock, drop anything
  already in `seed/universe_symbols.csv`, then **rank by 20-day median dollar
  volume and keep the top 2,000.** This is a universe we derive ourselves, so
  there is no index license to worry about. It is also liquid by construction,
  which the volume and OBV detectors need.
- **Measured on 2026-09-24:** 4,653 candidates after filtering. 4,644 fetched
  from yfinance in **271 s** (1 month of daily bars, batches of 200, one
  machine). **Rank #2,000 trades about $6.5M a day.** None of the top 2,000 is
  under $5M/day, and 106 trade under $5 a share.
- **What it takes:** a new seed CSV plus a script that builds it, a second CI
  scan matrix, a second auto-seeded universe in the browser, and **one fix
  that has to land first**: `LANDING_ROW_LIMIT = 1000` would silently
  truncate the landing page once the scanned total passes 1,000
  ([§5.1](#51-blocker-the-landing-read-truncates-at-1000-rows)).
- **Cost that grows:** LLM synthesis calls. At the current 33–42% publish
  rate, this adds roughly **660–840 LLM calls per daily run**. That is the
  wall-time and dollar driver, not data fetching.

---

## 1. Where the app is today

| Piece | Today | File |
|---|---|---|
| Seed list | 954 rows: 768 equities, 172 ETFs, 4 crypto, 1 fund | `seed/universe_symbols.csv` |
| Seed schema | `ticker,name,asset_type,sector_group` | same |
| Scheduled scan | Cron `25 21 * * 1-5`, **4-way shard** by `ticker_index % 4`, 90 min cap per job | `.github/workflows/signals-scan.yml` |
| Browser default universe | One universe with `isDefault: true`, seeded from a generated TS array | `web/src/lib/universe.ts` → `ensureDefaultUniverse()`, `web/src/lib/defaultUniverseTickers.ts` |
| Universe refresh | Reads Supabase in `IN_CHUNK = 200` ticker chunks, **never scans** | `web/src/lib/api.ts` |
| Manual browser scan cap | `MAX_MANUAL_SCAN_SYMBOLS = 100` | `web/src/lib/api.ts`, `signals_app.config` |
| Landing read | `latest_signals` view, `limit(1000)` | `web/src/lib/api.ts` → `fetchLandingSignals` |

A browser universe only shows what the **CI scan** has already written to
Supabase. So adding 2,000 tickers is mostly a backend job. A browser-only
universe of 2,000 names would sit empty: it can't scan more than 100 symbols
itself.

---

## 2. Source options

| Option | Size | Cost | Licensing | Liquidity | Verdict |
|---|---|---|---|---|---|
| **Nasdaq Trader symbol directory, ranked by dollar volume** | 4,653 non-seed common stocks → take top 2,000 | Free; updated daily | Public listing data; the ranking is ours | Guaranteed by the ranking | **Recommended** |
| Russell 2000 constituents (iShares IWM holdings CSV) | ~1,970 | Free download | Constituents are FTSE Russell IP; committing the list to a public repo is a grey area | Mixed; includes thin micro-caps | Good second choice; the name is recognisable |
| S&P MidCap 400 + SmallCap 600 (IJH + IJR holdings) | ~1,000 | Free download | S&P DJI IP, same grey area | Good | Too small for "2,000" |
| SEC `company_tickers_exchange.json` | ~10k incl. OTC | Free | Public | No filter; lots of OTC and shells | Useful for metadata (CIK, exchange), not selection |
| Paid reference data (Polygon, EODHD, Tiingo) | Anything | $30–$200/mo | Licensed | Filterable | Overkill until yfinance itself is the bottleneck |

**Why rank by liquidity, not by an index:** the detectors care about tradable
price action. The volume detectors, OBV/CMF and the 52-week-high proximity
check all misfire on names that print a few thousand shares a day. An index
membership list doesn't filter those out; a dollar-volume floor does. A
derived list also changes gradually and on our own schedule, not on
Russell's June reconstitution.

### Filter used in the measurement

From both directory files, drop:

- `ETF == Y` or `Test Issue == Y`
- Nasdaq `Financial Status` other than `N` (deficient, delinquent or bankrupt)
- Names matching warrants, units, rights, preferreds, notes/debentures,
  depositary-share fractions, SPACs (`acquisition corp`) and capital trusts
- Symbols containing `$` (preferred series)
- Anything already in the seed. `BRK.B` is mapped to `BRK-B` before comparing,
  which is the yfinance spelling.

---

## 3. Measured results (2026-09-24)

Prototype script in the [appendix](#appendix-prototype-ranking-script);
`signals-app` mamba env.

| Rank | Ticker | Median $ vol / day (20d) | Close |
|---:|---|---:|---:|
| 500 | RYN | $67.7M | $19.12 |
| 1,000 | DRS | $31.4M | $37.65 |
| 1,500 | WDS | $14.6M | $22.30 |
| **2,000** | **BVS** | **$6.5M** | $13.52 |
| 2,500 | HBB | $2.6M | $37.35 |
| 3,000 | ALTG | $1.1M | $5.82 |

- Pool 4,653 → 4,644 with ≥10 bars; 9 failed (delisted or a DNS blip).
- **Fetch time: 271 s for 4,653 symbols**, about 58 ms per symbol, using batched
  `yf.download` (200 per call, threaded). The existing scanner fetches one
  symbol at a time and measured 46 ms/symbol on the 954 seed (dry run,
  `docs/universe-scan-findings.md` §8). So fetching scales linearly and is
  not the constraint.
- Top 2,000: **0 names under $5M/day**, **106 under $5 a share**. Decide
  whether to add a price floor (see §7).

The one thing the ranking can't hand us is `sector_group`. The directory has
no sector field (see §4 step 1).

---

## 4. Implementation plan

Split into PRs that don't share files, in merge order:

### PR A: fix the landing truncation (prerequisite, small)

See §5.1. It has to merge before the new universe's first scan writes rows.

### PR B: seed builder and seed file (backend)

1. `scripts/build_universe_seed.py`: the appendix script, promoted to a real
   CLI. Arguments: `--size 2000 --exclude seed/universe_symbols.csv
   --min-dollar-volume 5e6 --out seed/universe_next2000.csv`. It writes the
   same four-column schema.
   - `asset_type = Equity`.
   - `sector_group`: take it from yfinance `Ticker.info` (`sector →
     industry`), fetched in a slow, rate-limited background pass. That's
     about 2,000 calls, so cache the results in a JSON file next to the seed
     and only look up new tickers. Write `Unknown` rather than failing.
2. Commit `seed/universe_next2000.csv`. Rebuild it **monthly**, not daily. A
   universe that reshuffles every day breaks run-to-run drift comparisons.
3. Tests: filter rules (warrant, unit and preferred names rejected; `.` → `-`),
   exclusion against the existing seed, stable ordering.

### PR C: CI scan for the second seed

4. `.github/workflows/signals-scan.yml`: add a `seed` matrix axis:
   ```yaml
   matrix:
     seed: [universe_symbols, universe_next2000]
     shard: [0, 1, 2, 3, 4, 5, 6, 7]
   ```
   Or add a separate `scan-next2000` job with 8 shards, so a failure in the
   new universe can't take down the existing one. **A separate job is
   recommended.** With 8 shards, each job gets about 250 tickers, the same
   load as today's 954 / 4.
5. Stagger the cron by about 45 minutes (e.g. `10 22 * * 1-5`) so the two
   universes don't hit yfinance and the LLM provider at the same time.
6. `scripts/train_scorer.py`, `backfill_history.py` and `calibrate.py`
   default to `seed/universe_symbols.csv`. **Leave them on it for now.**
   Small caps behave differently, and training on them would shift the
   learned scorer the whole app relies on (see §5.5).

### PR D: the second browser universe (frontend)

7. Generate `web/src/lib/next2000UniverseTickers.ts` from the new seed, the
   same way `defaultUniverseTickers.ts` is generated. Add the generator
   command to `package.json` so it can't drift silently.
8. Generalise `ensureDefaultUniverse()` into `ensureSeededUniverses()` driven
   by a list of `{ seedKey, name, loader }`. Replace the single boolean
   `isDefault` with `seedKey?: "all" | "next2000"`, as a Dexie
   `version(3)` upgrade that maps existing `isDefault: true` rows to
   `seedKey: "all"`. Keep the "name already taken → skip" guard.
9. `useDefaultUniverse()` and the landing CTAs keep pointing at `"all"`.
   Add a second card on `/universe/`.
10. Consider lazy seeding: create the 2,000-ticker universe the first time
    the user opens it, not on every first load. It adds about 16 KB of
    ticker strings to IndexedDB for visitors who never look at it.

### PR E: landing copy

11. Once both universes publish, the hero's "Scan all N tickers" should say
    which universe it means. The heatmap preview should either pick one or
    show both side by side.

---

## 5. What breaks at about 3,000 tickers

### 5.1 Blocker: the landing read truncates at 1,000 rows

`fetchLandingSignals` runs `latest_signals … .eq("period", "3mo").limit(1000)`,
**with no `order()`** on the view path. Once more than 1,000 tickers have a 3mo
row, the landing page gets an arbitrary 1,000. The top-signals list, heatmap,
featured deep dive and AI-degraded share would all be computed on a random
subset, and nothing would error.

Fix, any one of:
- Filter by universe (add a `universe` / `seed_key` column to `signals` and
  `latest_signals`) and read only `"all"` on the landing page. This is the
  cleanest, and PR C will want the column anyway.
- Page through with `.range()` until exhausted (3 requests).
- Move the landing aggregation into a Postgres view or RPC that returns the
  top N per direction plus counts, instead of every row.

### 5.2 LLM synthesis: the real cost

- Publish rate: 403 / 954 = 42% on the 2026-09-22 dry run
  (`docs/universe-scan-findings.md`), and 78 / 235 = 33% on the 2026-09-23
  run shown on the landing funnel.
- On 2,000 more names that's **about 660–840 more calls per run**, and small
  caps may publish at a different rate. Each call has a 15 s timeout and runs
  sequentially per worker.
- **Current state:** the 2026-09-23 landing shows every published signal at a
  flat 55% confidence. That value is hard-coded in the rule-based fallback
  (`synthesis/mtf_llm.py`), so **the AI step is already failing on most of
  today's 954**. Fix that (quota, key or provider) before tripling the load,
  or the new universe will be 100% Rule-Based.

### 5.3 yfinance throttling

Per ticker, the daily scan fetches the full 8-timeframe matrix, not one period.
Tripling tickers triples requests. Yahoo throttles by IP; GitHub Actions
runners get fresh IPs per job, which is one more reason to prefer 8 separate
shard jobs. Batch fetches (`yf.download` with a list) where the scanner
allows it. The prototype's 200-per-call batching is what kept it to 271 s.

### 5.4 Supabase

- Row growth: about `published × timeframes` rows per run, so it scales with
  §5.2's publish count. Measure one real run's row count and bytes before
  assuming the free tier holds (`docs/backend-state-and-supabase-plan.md`
  estimated "within free tiers" for the 954-ticker workload only).
- Universe refresh: 2,000 / `IN_CHUNK` 200 = 10 chunked reads per refresh.
  That's fine.

### 5.5 Scorer and calibration drift

The learned scorer (PR #30) was trained on the 954 seed: mostly large caps
and ETFs. Small and mid caps have fatter tails and more gaps. Scoring them
with the large-cap model will probably miscalibrate `p_outperform`. Options:
keep the scorer on the original seed and only publish rule scores for
`next2000`; or add a market-cap bucket feature and retrain on both. Watch
`scripts/check_scorer_drift.py` after the first week either way.

### 5.6 Detector behaviour on small caps

- `LARGE GAIN` / `LARGE LOSS` (±5%) will fire far more often. Small caps move
  5% on ordinary days, so the vote gets cheap. Consider scaling the threshold
  by ATR.
- The 252-bar high/low proximity check needs a full year of history. Recent
  IPOs will silently skip it.
- The `PUBLISH_MIN_DATA_QUALITY = 0.7` gate should catch gappy histories.
  Check its rejection rate on the first run.

---

## 6. Runbook (once PRs A–D are in)

**Step 1: preflight.** Right directory, env present, directory files reachable.

```bash
cd ~/code/signals-app && git rev-parse --abbrev-ref HEAD
/opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python -c "import yfinance, pandas; print('ok', yfinance.__version__)"
curl -sI https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt | head -1
```
Expect: your branch name, `ok <version>`, and `HTTP/2 200` (or `HTTP/1.1 200`).

**Step 2: build the seed.** This command only works after PR B adds the script.

```bash
cd ~/code/signals-app && /opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python scripts/build_universe_seed.py --size 2000 --exclude seed/universe_symbols.csv --min-dollar-volume 5e6 --out seed/universe_next2000.csv
```
Expect: about 5 minutes, then a summary line with the pool size and the rank-2000 dollar volume.

> **Fallback if the script is missing or fails:** the appendix prototype
> produces the same ranking, but its `liquidity_ranked.csv` has five different
> columns and no `sector_group`. This block ranks, then converts the top 2,000
> rows to the four-column seed schema. `sector_group` is filled with the
> placeholder `Unclassified`; replace it with the real sector mapping before
> relying on sector grouping.
> ```bash
> cd ~/code/signals-app && S=$(mktemp -d) && curl -s https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt -o "$S/nasdaqlisted.txt" && curl -s https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt -o "$S/otherlisted.txt" && /opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python scripts/rank_next2000_prototype.py "$S" && /opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python -c "import pandas as pd, sys; d = pd.read_csv(sys.argv[1] + '/liquidity_ranked.csv').head(2000); pd.DataFrame({'ticker': d.ticker, 'name': d.name, 'asset_type': 'Equity', 'sector_group': 'Unclassified'}).to_csv('seed/universe_next2000.csv', index=False)" "$S" && echo "wrote seed/universe_next2000.csv"
> ```
> (The appendix script is committed as `scripts/rank_next2000_prototype.py`.)

**Step 3: verify the seed.**

```bash
cd ~/code/signals-app && wc -l seed/universe_next2000.csv && head -3 seed/universe_next2000.csv && comm -12 <(cut -d, -f1 seed/universe_symbols.csv | sort) <(cut -d, -f1 seed/universe_next2000.csv | sort) | wc -l
```
Expect: `2001` lines (header + 2,000), the four-column header, and `0` overlap.

**Step 4: dry-run one shard locally** (no LLM, no DB writes).

```bash
cd ~/code/signals-app && /opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python scripts/scan_universe.py --seed seed/universe_next2000.csv --shard 0/8 --period 3mo --trigger manual --dry-run
```
Expect: about 250 symbols attempted, and a gated/published split you can compare with §5.2.

**Step 5: dispatch the CI scan.**

```bash
cd ~/code/signals-app && gh workflow run signals-scan.yml -f full_universe=true -f dry_run=true
```
Expect: `Created workflow_dispatch event`. Once PR C lands, this also runs the new job.

**Step 6: verify the CI run.**

```bash
cd ~/code/signals-app && gh run list --workflow signals-scan.yml --limit 1
```
Expect: the newest run, `completed` / `success`, with every shard green.

---

## 7. Open decisions

| Decision | Options | Lean |
|---|---|---|
| Name shown to users | "Next 2000", "US Mid & Small Caps", "Russell-style 2000" | **"US Mid & Small Caps (2,000)"**; says what it is, and makes no index claim |
| Price floor | none / $2 / $5 | **$2**: keeps ~100 real sub-$5 names, cuts penny stocks |
| Rebuild cadence | daily / monthly / quarterly | **Monthly**, committed, so runs stay comparable |
| Scorer | shared / per-universe / retrain with a cap feature | **Rule scores only for `next2000`** until a week of drift data exists |
| Scan cadence | daily with the main seed / every other day | **Daily, staggered 45 min**, once the LLM step is healthy again (§5.2) |

---

## Appendix: prototype ranking script

What produced §3. Run it with the directory files already downloaded into
`$1` (see §6 step 2's fallback).

```python
"""Rank non-seed US common stocks by 20-day median dollar volume (research prototype)."""
import csv, re, sys, time
import pandas as pd
import yfinance as yf

S = sys.argv[1]
seed = {r["ticker"] for r in csv.DictReader(open("seed/universe_symbols.csv"))}
bad = re.compile(r"warrant|\bunits?\b|\bright(s)?\b|preferred|depositary shares?\W+(representing|each)|notes due|debenture|% |acquisition corp|capital trust", re.I)
pool = {}
for fn, sym in (("nasdaqlisted.txt", "Symbol"), ("otherlisted.txt", "ACT Symbol")):
    for r in csv.DictReader(open(f"{S}/{fn}"), delimiter="|"):
        t = r.get(sym) or ""
        if not t or t.startswith("File Creation") or r["ETF"] == "Y" or r["Test Issue"] == "Y":
            continue
        if fn == "nasdaqlisted.txt" and r["Financial Status"] not in ("N", ""):
            continue
        if bad.search(r["Security Name"]) or "$" in t:
            continue
        y = t.replace(".", "-")
        if y not in seed:
            pool[y] = r["Security Name"]
tickers = sorted(pool)
t0 = time.time()
rows, failed = [], 0
BATCH = 200
for i in range(0, len(tickers), BATCH):
    chunk = tickers[i:i + BATCH]
    df = yf.download(chunk, period="1mo", interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=True)
    for t in chunk:
        try:
            sub = df[t].dropna()
        except KeyError:
            failed += 1; continue
        if len(sub) < 10:
            failed += 1; continue
        dv = (sub["Close"] * sub["Volume"]).tail(20).median()
        rows.append((t, pool[t], float(sub["Close"].iloc[-1]), float(dv), len(sub)))
el = time.time() - t0
out = pd.DataFrame(rows, columns=["ticker", "name", "close", "med_dollar_vol_20d", "bars"]).sort_values("med_dollar_vol_20d", ascending=False)
out.to_csv(f"{S}/liquidity_ranked.csv", index=False)
print(f"pool={len(tickers)} ok={len(out)} failed={failed} elapsed={el:.0f}s")
for k in (500, 1000, 1500, 2000, 2500, 3000):
    if len(out) >= k:
        r = out.iloc[k - 1]
        print(f"rank {k}: {r.ticker} ${r.med_dollar_vol_20d/1e6:.1f}M/day close ${r.close:.2f}")
top = out.head(2000)
print("top2000: price<$5:", int((top.close < 5).sum()), " ADV<$5M:", int((top.med_dollar_vol_20d < 5e6).sum()))
```

Known limits of the prototype: it measures one month only, so a single spike
day can't dominate the median but a month-long news cycle can; it doesn't
check how much history each name has (§5.6); and the filter regex is
name-based, so an odd security name can slip through or be wrongly
rejected. Spot-check the bottom 50 of the output before committing a seed.
