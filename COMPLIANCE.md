# Compliance & Legal Best Practices — Financial Signals App

*Created: 2026-06-15 · Scope: `signals-app` web + API · Status: guidance, not legal advice*

> ⚠️ **This document is engineering guidance, not legal advice.** A financial
> application carries materially higher liability than a typical web app.
> Before public launch or accepting any real user, have a securities/fintech
> attorney review (1) your investment-advice disclaimers, (2) your market-data
> licensing, and (3) your privacy posture for every jurisdiction you serve.

---

## 0. Why a finance app is different

A signals app outputs BUY/HOLD/SELL calls. That triggers three regimes that a
normal SaaS app never touches:

1. **Investment-adviser regulation** (US: SEC/state RIA rules under the
   Advisers Act; EU: MiFID II investment advice; UK: FCA). Personalized
   "advice" can require registration. Generic, non-personalized,
   clearly-disclaimed educational signals generally do **not** — but the line
   is fact-specific and the disclaimer must be prominent and honest.
2. **Market-data licensing.** Quote/OHLCV data (yfinance scrapes Yahoo) has
   redistribution terms. Showing it to end users is a different license than
   internal research. See §7.
3. **Record-keeping & financial-grade data handling.** Even without holding
   money, you log what trades/tickers a user researched — sensitive behavioral
   + potentially financial data.

The non-negotiables below fold the standard privacy/cookie law (GDPR, ePrivacy,
CCPA/CPRA) **into** these finance-specific duties.

---

## 1. The non-negotiables (ship-blockers)

A financial app should not be public-facing without **all** of these:

- [ ] **Investment disclaimer** shown before/at first use and on every signal
      surface ("not financial advice", "for educational/informational use",
      "past performance ≠ future results", "you bear all risk").
- [ ] **Cookie/storage consent banner** that blocks non-essential storage until
      the user chooses (opt-in, GDPR/ePrivacy style — see §3).
- [ ] **Privacy Policy** page (what is stored, where, why, retention, rights).
- [ ] **Terms of Service** page (license, disclaimers, liability cap,
      arbitration/governing law, acceptable use).
- [ ] **Risk Disclosure** statement (markets can lose money; signals are
      probabilistic; data may be delayed/inaccurate).
- [ ] **Reachable contact** for data-rights requests and complaints.
- [ ] **Honest "AI degraded" + "data delayed" indicators** — never present a
      stale or fallback signal as a live, confident one. (The app already
      surfaces `ai_degraded`; extend this to data freshness.)

If any box is unchecked, gate the app behind a "private beta — no real
decisions" notice.

---

## 2. This app's actual data inventory

Be precise; a privacy policy is only credible if it matches reality. As built,
`signals-app` stores:

### On the user's device (IndexedDB + one cookie) — NOT sent to any server
| Store | Field | Sensitivity |
|---|---|---|
| `profile` | name, defaults, theme, firstSeen, lastSeen, **lastTicker**, **lastSignal**, totalRuns | Name = personal data (GDPR). Ticker history = behavioral/financial interest. |
| `history` | every ticker, period, signal, confidence, timestamp | Reveals investment interests over time — treat as sensitive. |
| `watchlist` | tickers, notes, target prices | Same. Notes are free-text (could contain anything). |
| `savedConfigs`, `alerts` | preferences | Low. |
| **cookie** `sa_profile` | name, lastTicker, lastSignal, lastSeen | Personal + behavioral. Sent on every request to **your own** origin. |

### On the server (FastAPI)
| What | Where | Note |
|---|---|---|
| OHLCV cache (`bars`) | in-memory (local) / Postgres (cloud) | Market data — licensing applies (§7). |
| Request logs | stdout / Cloud Run | If you log ticker + IP, that's a per-user behavioral record. Scrub or shorten retention. |
| LLM prompts | sent to Gemini | A **third-party sub-processor**. The ticker + features leave your infra. Must be disclosed. |

**Key compliance facts that make this app's posture strong:**
- The behavioral history is **local-first** — it never leaves the device. Say
  this loudly; it's a genuine privacy advantage and reduces breach scope.
- BUT the **cookie is technically transmitted** to your origin on every
  request, and **LLM calls do send ticker/features to Gemini.** Both must be in
  the policy. Do not claim "we send nothing to servers."

---

## 3. Cookie / local-storage consent (GDPR + ePrivacy)

### The legal rule
ePrivacy ("cookie law") + GDPR require **prior opt-in** for any storage that is
**not strictly necessary**, including `localStorage` and `IndexedDB` — not just
HTTP cookies. Strictly-necessary storage (security, load-balancing, the user's
explicit consent record itself) is exempt and needs no consent.

### Classify every storage item
| Item | Category | Needs consent? |
|---|---|---|
| `sa_consent` (the consent record) | Strictly necessary | No |
| `sa_profile` cookie | Functional/personalization | **Yes** (it's convenience, not essential) |
| IndexedDB `profile`/`history`/`watchlist`/`alerts` | Functional | **Yes** |
| Any future analytics (GA, PostHog, etc.) | Analytics/marketing | **Yes**, separate toggle |

Because the app's core features (history, watchlist, greeting) depend on
functional storage, present consent as: **"Essential only"** vs **"Accept all
(enables your saved history & watchlist on this device)"** — with a clear
explanation that declining means no personalization is saved.

### Banner requirements (do all)
1. Appears **before** writing any non-essential storage. Until the user
   chooses, do **not** create the profile, write the cookie, or record runs.
2. Equally prominent **Accept** and **Reject** — no dark patterns, reject must
   be one click.
3. Granular categories (at minimum: Functional, Analytics) with independent
   toggles. No pre-ticked non-essential boxes.
4. Store the **consent record** (categories, timestamp, policy version) so you
   can prove consent. This record is the one strictly-necessary item.
5. A persistent way to **change/withdraw** consent later (footer link
   "Cookie preferences"). Withdrawal must be as easy as granting.
6. Re-prompt when the policy version or categories change.

### Implementation gate (architectural)
Wrap all storage writes behind a consent check. Concretely for this app:
`ensureProfile`, `writeProfileCookie`, and `recordRun` must early-return / no-op
until `sa_consent.functional === true`. The home page should show the consent
banner first; onboarding (name prompt) only after Functional is accepted.

---

## 4. Privacy Policy — required contents

Write it to match §2 exactly. Sections:

1. **Who we are** + contact for privacy requests.
2. **What we collect** — split clearly into *on-device only* vs *sent to
   servers*. Name the device-local design as a feature.
3. **Why** (legal basis under GDPR: consent for functional storage; legitimate
   interest for security logs — document the balancing test).
4. **Third parties / sub-processors** — Google Gemini (LLM), Yahoo/yfinance
   (market data source), your host (e.g. Cloud Run / Vercel), any analytics.
   For each: what data, what purpose, link to their policy.
5. **International transfers** — Gemini/host may process data in the US; note
   the transfer mechanism (SCCs / adequacy).
6. **Retention** — device data: until the user wipes it (the app has a "forget
   me" button — cite it). Server logs: state a concrete window (e.g. 30 days).
7. **Your rights** — access, rectification, erasure, portability, withdraw
   consent, complain to a supervisory authority. The app's **Export JSON** and
   **Forget me** features satisfy portability + erasure for local data — point
   to them.
8. **Children** — not directed at under-18s (finance apps generally bar minors;
   state a minimum age and don't knowingly collect from minors).
9. **CCPA/CPRA (California)** — "we do not sell or share personal information"
   (if true), plus the right-to-know/delete and a "Do Not Sell/Share" link if
   any sharing occurs.
10. **Changes** — versioned, with notice on material change.

---

## 5. Terms of Service — required contents (finance-weighted)

1. **Eligibility** — 18+, legal capacity, not barred by sanctions.
2. **Service description** — "educational/informational signal analysis tool."
3. **NOT INVESTMENT ADVICE** — prominent, repeated. The app does not know your
   financial situation, does not provide personalized advice, is not a
   broker-dealer or registered investment adviser, and nothing is a
   solicitation to buy/sell.
4. **No fiduciary relationship.**
5. **Risk disclosure** (or link to §6 doc) — incorporated by reference.
6. **Data accuracy disclaimer** — market data may be delayed, incomplete, or
   wrong; signals are model outputs that can be flat wrong; AI may be degraded.
7. **License & IP** — user gets a limited license; don't scrape/redistribute
   our outputs; market data is subject to upstream terms.
8. **Limitation of liability** — cap damages, exclude consequential/trading
   losses (to the extent legally permitted; some jurisdictions limit this).
9. **Indemnification.**
10. **Governing law, dispute resolution** (arbitration/class-action waiver if
    desired — enforceability varies, get counsel).
11. **Termination, changes to terms, severability.**

---

## 6. Risk & investment-advice disclaimer (the finance-specific core)

Display a short version **at first run** (acknowledged once) and a persistent
short form on **every signal card / dashboard**. Long form lives on a Risk
Disclosure page linked from both.

Minimum short-form text (adapt with counsel):

> **Not financial advice.** Signals are automated, generalized, educational
> information — not a recommendation, solicitation, or personalized advice. We
> are not a registered investment adviser or broker-dealer. Markets are risky;
> you can lose money. Past performance does not guarantee future results. Data
> may be delayed or inaccurate. Do your own research and consult a licensed
> professional before investing. You are solely responsible for your decisions.

Long-form Risk Disclosure should additionally cover: model/AI limitations and
hallucination risk, backtest ≠ live performance, no guarantee of availability,
data-source delays, concentration/leverage risks if relevant, and that
confidence scores are probabilistic estimates, not promises.

Engineering hooks already present to lean on:
- `ai_degraded` flag → render the "AI unavailable, rule-based fallback" banner
  (done). **Add an equivalent "data delayed N min / as-of timestamp" badge** so
  freshness is never misrepresented.
- Confidence is shown as a % — always pair it with "probabilistic, not a
  guarantee" microcopy.

---

## 7. Market-data licensing (often overlooked, real risk)

- **yfinance scrapes Yahoo Finance.** Yahoo's terms restrict commercial use and
  redistribution. Using it to power a **public, user-facing** product is a
  different (and likely non-compliant) use than personal research.
- Before any public/commercial launch, move to a **properly licensed market
  data provider** with end-user-display rights (e.g., Polygon, IEX Cloud,
  Tiingo, Alpaca, or an exchange feed). Confirm the license covers *display to
  end users* and *derived signals*.
- Honor required **attribution** and **delay** terms (many cheap/free tiers are
  15-min delayed and you must label data as delayed).
- Cache only as the license permits; some prohibit storing raw quotes.

---

## 8. AI / LLM-specific compliance

- **Disclose the LLM sub-processor** (Gemini) in the privacy policy; ticker +
  feature data leaves your infra on each synthesis call.
- **Don't send PII to the LLM.** The app sends ticker/features, not the user's
  name — keep it that way. Never include `profile.name` or device history in a
  prompt.
- **Label AI output as AI-generated** and potentially wrong (EU AI Act
  transparency direction; good practice everywhere).
- **Log prompt_version + model** for auditability (the schema already carries
  `prompt_version`) so you can reconstruct what advice-like output a user saw.
- Consider a provider data-processing agreement and opt-out of training on your
  prompts where offered.

---

## 9. Security & record-keeping baseline

- **Transport:** HTTPS everywhere; HSTS. The `sa_profile` cookie must be
  `Secure` in production, `SameSite=Lax` (already set), and contains no
  secrets (it doesn't — name/ticker only; acceptable, but document it).
- **No financial credentials** are handled today — keep it that way unless you
  add brokerage links, which would pull in far stricter rules (PCI/PSD2/SOC 2).
- **Logging hygiene:** avoid logging full request bodies with tickers tied to
  IPs long-term; set a retention window; scrub PII.
- **Breach plan:** because most sensitive data is device-local, server-side
  breach scope is small — state this, and still have a 72-hour GDPR breach
  notification process for server data.
- **Audit trail:** retain consent records and the disclaimer version each user
  accepted, with timestamps.

---

## 10. Implementation checklist (maps to code)

Frontend (`web/`):
- [ ] `ConsentBanner` component — blocks non-essential storage until choice;
      writes `sa_consent` record; "Cookie preferences" footer link to reopen.
- [ ] Gate `ensureProfile` / `writeProfileCookie` / `recordRun` on
      `consent.functional === true` (no-op otherwise).
- [ ] `/legal/privacy`, `/legal/terms`, `/legal/risk` pages (content per §4–6).
- [ ] First-run **disclaimer acknowledgment** (store accepted version in DB).
- [ ] Persistent short disclaimer on every signal card + dashboard footer.
- [ ] "Data as-of <timestamp> · delayed" badge on signal surfaces.
- [ ] Footer links to all legal pages + cookie preferences + contact.
- [ ] Set cookie `Secure` flag in production.

Backend (`src/signals_app/`):
- [ ] Add `as_of` / data-freshness timestamp to the API response so the UI can
      show delay honestly.
- [ ] Document market-data source + swap to a licensed provider before launch.
- [ ] Log scrubbing + retention config; never log `profile.name`.
- [ ] Never forward PII into LLM prompts (audit `_build_features`).

Process:
- [ ] Securities/fintech attorney review of disclaimers + data licensing.
- [ ] Versioned legal docs with change-notice mechanism.
- [ ] Defined retention windows + breach-response runbook.

---

## 11. Quick reference — what triggers what

| If you add… | You pull in… |
|---|---|
| Personalized advice (uses user's portfolio/goals) | RIA/MiFID registration — get counsel **first** |
| Paid subscriptions | Consumer-protection, auto-renewal disclosure, refund terms |
| Brokerage/trade execution links | Broker-dealer rules, PSD2/Open Banking, SOC 2, PCI |
| Analytics (GA/PostHog) | New consent category + sub-processor disclosure |
| EU/UK users | GDPR/UK-GDPR + ePrivacy in full (you should assume this now) |
| California users | CCPA/CPRA notices + Do-Not-Sell/Share |
| Storing money / custody | Money-transmitter licensing — a different universe |

---

*Keep this file current as features change. The privacy policy and this
inventory must always describe what the code actually does.*
