/**
 * Plain-English catalogue of every detector the engine runs, for the landing
 * page's "signals explained" section.
 *
 * Mirrors `signals_app.detection.orchestrator.get_default_detectors()` and the
 * thresholds in `signals_app.config` / `signals_app.indicators.grids`. It is
 * hand-maintained: when a detector, threshold or grid changes on the Python
 * side, update the matching entry here (signalGlossary.test.ts pins the count
 * and family grouping so a silent drift is at least visible).
 */

/** The five independent-evidence families from `signals_app.scoring.families`. */
export type SignalFamily = "trend" | "momentum" | "mean_reversion" | "volume_flow" | "structure";

/** Which way a fired signal votes. `none` = counted, but carries no side. */
export type VoteSide = "bull" | "bear" | "none";

/** Vote weight from `confluence._STRENGTH_BULL_WEIGHT`: 1 normal, 2 strong, 3 extreme. */
export type VoteWeight = 0 | 1 | 2 | 3;

export interface FiredSignal {
  /** The label as it appears in a deep dive, e.g. "GOLDEN CROSS". */
  label: string;
  /** Exact trigger condition, in words. */
  when: string;
  side: VoteSide;
  weight: VoteWeight;
}

export interface DetectorEntry {
  /** Python class name — stable key, also shown as a small monospace hint. */
  id: string;
  name: string;
  family: SignalFamily;
  /** One line: what market fact this detector watches. */
  watches: string;
  signals: FiredSignal[];
  /** How a trader should read it, including where it tends to mislead. */
  readIt: string;
}

export interface FamilyInfo {
  label: string;
  blurb: string;
  /** Tailwind classes for the family pill / accent. */
  tone: string;
}

export const FAMILIES: Record<SignalFamily, FamilyInfo> = {
  trend: {
    label: "Trend",
    blurb: "Is price moving in a sustained direction?",
    tone: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  },
  momentum: {
    label: "Momentum",
    blurb: "Is the move speeding up or running out of steam?",
    tone: "border-violet-400/30 bg-violet-400/10 text-violet-300",
  },
  mean_reversion: {
    label: "Mean reversion",
    blurb: "Has price stretched unusually far from its average?",
    tone: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  },
  volume_flow: {
    label: "Volume & flow",
    blurb: "Is real money backing the move?",
    tone: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  },
  structure: {
    label: "Structure",
    blurb: "Where is price relative to its recent range?",
    tone: "border-rose-400/30 bg-rose-400/10 text-rose-300",
  },
};

export const FAMILY_ORDER: SignalFamily[] = [
  "trend",
  "momentum",
  "mean_reversion",
  "volume_flow",
  "structure",
];

export const DETECTORS: DetectorEntry[] = [
  // ── Trend ────────────────────────────────────────────────────────────────
  {
    id: "MovingAverageSignalDetector",
    name: "Moving-average crosses & stack",
    family: "trend",
    watches: "The classic 50/200 cross, price vs its 20-bar average, and whether the short averages are stacked in order.",
    signals: [
      { label: "GOLDEN CROSS", when: "50 SMA crosses above 200 SMA (needs 200+ bars)", side: "bull", weight: 2 },
      { label: "DEATH CROSS", when: "50 SMA crosses below 200 SMA", side: "bear", weight: 2 },
      { label: "PRICE ABOVE / BELOW 20 MA", when: "Close crosses the 20 SMA this bar", side: "bull", weight: 1 },
      { label: "MA ALIGNMENT BULLISH / BEARISH", when: "10 > 20 > 50 SMA (or the reverse)", side: "bull", weight: 2 },
    ],
    readIt: "Crosses are late by design — they confirm a trend that has already started. The stack is the steadier read.",
  },
  {
    id: "ExpandedMACrossDetector",
    name: "MA cross grid",
    family: "trend",
    watches: "Eleven fast/slow SMA pairs from 5/10 up to 50/200, so short- and long-horizon crosses both register.",
    signals: [
      { label: "{fast}/{slow} MA BULL CROSS", when: "Fast SMA crosses above slow SMA, any of 11 pairs", side: "bull", weight: 1 },
      { label: "{fast}/{slow} MA BEAR CROSS", when: "Fast SMA crosses below slow SMA", side: "bear", weight: 1 },
    ],
    readIt: "A 5/10 cross is noise-level; 20/100 or 50/100 carries more. The pair in the label tells you the horizon.",
  },
  {
    id: "TrendSignalDetector",
    name: "ADX trend strength",
    family: "trend",
    watches: "ADX(14) — how strong the trend is, regardless of direction; direction comes from price vs the 50 SMA.",
    signals: [
      { label: "STRONG UPTREND", when: "ADX > 25 and close above 50 SMA", side: "bull", weight: 1 },
      { label: "STRONG DOWNTREND", when: "ADX > 25 and close below 50 SMA", side: "bear", weight: 1 },
    ],
    readIt: "Silence here is information: ADX under 25 means a range, where the oscillators below work better than crosses.",
  },
  {
    id: "IchimokuDetector",
    name: "Ichimoku cloud",
    family: "trend",
    watches: "Tenkan/Kijun crosses, where price sits relative to the cloud (kumo), and the cloud's own colour.",
    signals: [
      { label: "ICHIMOKU TK BULL / BEAR CROSS", when: "Tenkan crosses Kijun", side: "bull", weight: 2 },
      { label: "PRICE ABOVE / BELOW KUMO", when: "Close above cloud top / below cloud bottom", side: "bull", weight: 1 },
      { label: "PRICE INSIDE KUMO", when: "Close between the spans — indecision", side: "none", weight: 0 },
      { label: "BULLISH / BEARISH KUMO", when: "Span A above Span B (green) or below (red)", side: "bull", weight: 1 },
    ],
    readIt: "Strongest when all three agree: TK cross, price outside the cloud, and cloud colour on the same side.",
  },
  // ── Momentum ─────────────────────────────────────────────────────────────
  {
    id: "RSISignalDetector",
    name: "RSI (14)",
    family: "momentum",
    watches: "The standard 14-bar Relative Strength Index against the textbook 30/70 bands.",
    signals: [
      { label: "RSI EXTREME OVERSOLD", when: "RSI < 20", side: "bull", weight: 2 },
      { label: "RSI OVERSOLD", when: "RSI < 30", side: "bull", weight: 1 },
      { label: "RSI OVERBOUGHT", when: "RSI > 70", side: "bear", weight: 1 },
    ],
    readIt: "A bounce call, not a trend call. In a strong uptrend RSI can sit above 70 for weeks.",
  },
  {
    id: "MultiRSIDetector",
    name: "Multi-period RSI",
    family: "momentum",
    watches: "RSI at 5, 10, 14, 20 and 30 bars, each against 20/80, 25/75 and 30/70 bands, plus crosses of the 50 midline.",
    signals: [
      { label: "RSI{n} OVERSOLD (<lvl)", when: "Any period below its oversold band (most extreme reported)", side: "bull", weight: 1 },
      { label: "RSI{n} OVERBOUGHT (>lvl)", when: "Any period above its overbought band", side: "bear", weight: 1 },
      { label: "RSI{n} CROSSED 50 BULL / BEAR", when: "An RSI crosses its 50 midline", side: "bull", weight: 1 },
    ],
    readIt: "The period tells you the horizon: RSI5 flips daily, RSI30 is slow. A 50-line cross is a momentum shift, not an extreme.",
  },
  {
    id: "MACDSignalDetector",
    name: "MACD (12, 26, 9)",
    family: "momentum",
    watches: "The standard MACD line vs its signal line, and the MACD line vs zero.",
    signals: [
      { label: "MACD BULL / BEAR CROSS", when: "MACD crosses its signal line", side: "bull", weight: 1 },
      { label: "MACD ZERO CROSS UP / DOWN", when: "MACD crosses zero (12 EMA crosses 26 EMA)", side: "bull", weight: 1 },
    ],
    readIt: "Signal-line crosses are early and noisy; zero crosses are later but mean the trend itself flipped.",
  },
  {
    id: "MultiMACDDetector",
    name: "MACD variants",
    family: "momentum",
    watches: "Three non-standard MACDs — (10,20,5) fast, (19,39,9) and (20,50,10) slow — to catch turns on other horizons.",
    signals: [
      { label: "MACD(f,s,sig) BULL / BEAR CROSS", when: "Variant MACD crosses its signal line", side: "bull", weight: 2 },
      { label: "MACD(f,s,sig) ZERO BULL / BEAR", when: "Variant MACD crosses zero", side: "bull", weight: 1 },
    ],
    readIt: "When the fast and slow variants cross together, the turn is showing up on more than one timescale.",
  },
  {
    id: "StochasticSignalDetector",
    name: "Stochastic level",
    family: "momentum",
    watches: "Where the close sits in its 14-bar high–low range (%K).",
    signals: [
      { label: "STOCHASTIC OVERSOLD", when: "%K < 20", side: "bull", weight: 1 },
      { label: "STOCHASTIC OVERBOUGHT", when: "%K > 80", side: "bear", weight: 1 },
    ],
    readIt: "Faster and twitchier than RSI. Most useful in a sideways market.",
  },
  {
    id: "StochasticCrossDetector",
    name: "Stochastic cross",
    family: "momentum",
    watches: "%K crossing %D, but only counted when it happens inside an extreme zone.",
    signals: [
      { label: "STOCH BULL CROSS (OVERSOLD)", when: "%K crosses above %D with %K < 30", side: "bull", weight: 2 },
      { label: "STOCH BEAR CROSS (OVERBOUGHT)", when: "%K crosses below %D with %K > 70", side: "bear", weight: 2 },
    ],
    readIt: "The filtered version of the level signal: it waits for the turn instead of firing on the extreme alone.",
  },
  {
    id: "PriceActionSignalDetector",
    name: "Large move",
    family: "momentum",
    watches: "The size of today's bar.",
    signals: [
      { label: "LARGE GAIN", when: "Close-to-close change > +5%", side: "bull", weight: 2 },
      { label: "LARGE LOSS", when: "Close-to-close change < −5%", side: "bear", weight: 2 },
    ],
    readIt: "Usually news or earnings. Check whether volume confirms it before reading it as a trend.",
  },
  // ── Mean reversion ───────────────────────────────────────────────────────
  {
    id: "BollingerBandSignalDetector",
    name: "Bollinger touch",
    family: "mean_reversion",
    watches: "Price touching the standard 20-bar, 2σ Bollinger Bands.",
    signals: [
      { label: "AT LOWER BB", when: "Close within 1% of the lower band", side: "bull", weight: 1 },
      { label: "AT UPPER BB", when: "Close within 1% of the upper band", side: "bear", weight: 1 },
    ],
    readIt: "A fade call: the band edge is treated as stretched. It can disagree with the breakout detector below on the same bar, and that is intentional.",
  },
  {
    id: "BBExpansionDetector",
    name: "Bollinger breakout grid",
    family: "mean_reversion",
    watches: "Closes outside any of 16 band sets (10–50 bars × 1.5–3σ), and runs along the upper band.",
    signals: [
      { label: "ABOVE UPPER BB(p,σ)", when: "Close above an upper band (widest breached set reported)", side: "bull", weight: 3 },
      { label: "BELOW LOWER BB(p,σ)", when: "Close below a lower band", side: "bear", weight: 3 },
      { label: "BB(p,σ) RIDING UPPER BAND", when: "Closed above the upper band two bars running", side: "bull", weight: 2 },
    ],
    readIt: "A breakout call, the opposite reading from the touch detector. A 3σ break is rare, so it gets a heavy vote.",
  },
  {
    id: "MADistanceExpandedDetector",
    name: "Distance from average",
    family: "mean_reversion",
    watches: "How far price is above or below its 5- to 200-bar SMAs, in 5/10/15/20% steps.",
    signals: [
      { label: ">x% ABOVE nSMA", when: "Price more than x% above an SMA (largest gap reported)", side: "bear", weight: 1 },
      { label: ">x% BELOW nSMA", when: "Price more than x% below an SMA", side: "bull", weight: 1 },
    ],
    readIt: "“Extended” is not “about to fall”. In strong uptrends this bearish vote has historically been wrong more often than right.",
  },
  // ── Volume & flow ────────────────────────────────────────────────────────
  {
    id: "VolumeSignalDetector",
    name: "Volume spike",
    family: "volume_flow",
    watches: "Today's volume vs its 20-bar average.",
    signals: [
      { label: "EXTREME VOLUME 3X", when: "Volume > 3× average", side: "none", weight: 0 },
      { label: "VOLUME SPIKE 2X", when: "Volume > 2× average", side: "none", weight: 0 },
    ],
    readIt: "No direction on its own: a spike can be buying or capitulation. It amplifies whatever price did.",
  },
  {
    id: "VolumeDivergenceDetector",
    name: "Volume divergence",
    family: "volume_flow",
    watches: "Spikes against 5/10/20/50-bar volume averages, and whether 10-bar price and volume trends disagree.",
    signals: [
      { label: "VOLUME SPIKE >1.5x / >2x / >3x (MAn)", when: "Volume above a multiple of an n-bar average", side: "none", weight: 0 },
      { label: "VOLUME BEARISH DIVERGENCE (10b)", when: "Price up over 10 bars while volume fell", side: "bear", weight: 1 },
      { label: "VOLUME BULLISH DIVERGENCE (10b)", when: "Price down over 10 bars while volume rose", side: "bull", weight: 1 },
    ],
    readIt: "A rally on shrinking volume has fewer buyers behind it. That is a warning, not a reversal.",
  },
  {
    id: "OBVCMFDetector",
    name: "OBV & money flow",
    family: "volume_flow",
    watches: "On-Balance Volume (running up-volume minus down-volume) and 20-bar Chaikin Money Flow.",
    signals: [
      { label: "OBV BULLISH DIVERGENCE", when: "Price down over 20 bars, OBV up", side: "bull", weight: 2 },
      { label: "OBV BEARISH DIVERGENCE", when: "Price up over 20 bars, OBV down", side: "bear", weight: 1 },
      { label: "OBV BULL / BEAR CROSS EMA", when: "OBV crosses its own EMA", side: "bull", weight: 1 },
      { label: "CMF STRONG BUYING / SELLING", when: "CMF > +0.10 / < −0.10", side: "bull", weight: 1 },
      { label: "CMF CROSSED POSITIVE / NEGATIVE", when: "CMF crosses zero", side: "bull", weight: 1 },
    ],
    readIt: "Accumulation shows up in OBV before it shows up in price. This is the detector most likely to lead.",
  },
  // ── Structure ────────────────────────────────────────────────────────────
  {
    id: "HLProximityDetector",
    name: "Near the high / low",
    family: "structure",
    watches: "How close the close is to its 20-, 50-, 100-, 200- or 252-bar (52-week) high and low.",
    signals: [
      { label: "WITHIN x% OF nb HIGH", when: "Close within 1/2/5% of an n-bar high (1% votes as extreme)", side: "bull", weight: 1 },
      { label: "WITHIN x% OF nb LOW", when: "Close within 1/2/5% of an n-bar low", side: "bear", weight: 1 },
    ],
    readIt: "Near a 52-week high counts as strength, not resistance. Stocks making new highs tend to keep making them.",
  },
];

/** Paired labels ("X / Y") describe both sides; the stored side is the first one. */
export function isPairedLabel(label: string): boolean {
  return label.includes(" / ");
}

export function detectorsByFamily(family: SignalFamily | "all"): DetectorEntry[] {
  return family === "all" ? DETECTORS : DETECTORS.filter((d) => d.family === family);
}

/** Total signal patterns the catalogue documents (a parameterized or paired entry counts once). */
export function signalPatternCount(): number {
  return DETECTORS.reduce((n, d) => n + d.signals.length, 0);
}

/** Mirrors `config.py` so the "how a call is made" copy can't quietly drift. */
export const SCORING_RULES = {
  buyThreshold: 0.35,
  minAgreeingSignals: 3,
  minDataQuality: 0.7,
} as const;
