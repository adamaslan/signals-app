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
        if len(sub) < 20:
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
