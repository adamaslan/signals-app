# Signal Schema — the Contract

[`schemas/signal_output.py`](../../src/signals_app/schemas/signal_output.py)
is described in-file as "the crown jewel — the contract between every
pipeline layer," and it's the schema every layer (detection → confluence →
LLM synthesis → API response → frontend `types.ts`) ultimately targets.

## `Signal` — one directional call

```python
class Signal(BaseModel):
    direction: SignalDirection       # strong_buy | buy | hold | sell | strong_sell
    confidence: float                # strictly (0, 1) — never exactly 0 or 1
    timeframe: Timeframe             # 1D | 5D | 1M | 3M | 6M | 1Y
    evidence: Evidence
    ai_degraded: bool = False
    prompt_version: str = "unknown"
```

Three model-level validators enforce invariants beyond simple field types:

1. **`confidence_not_extreme`** — rejects exactly `0.0` or `1.0`. A model
   (LLM or fallback) can never claim absolute certainty.
2. **`hold_confidence_cap`** — a `hold` direction can't carry confidence
   above 0.75. The reasoning: high confidence implies a directional call;
   if the model is >75% confident, it should be picking a side, not
   sitting on the fence.
3. **`high_confidence_requires_counter`** — `confidence > 0.6` requires at
   least one `Evidence` item with `is_counter=True`. Forces the model (or
   fallback author) to articulate what would make the call wrong before it's
   allowed to sound very sure.

## `Evidence` / `EvidenceItem`

```python
class EvidenceItem(BaseModel):
    source: EvidenceSource   # technical | fundamental | macro | news_sentiment |
                             # options_flow | sector_relative | cross_asset |
                             # earnings | rule_based
    weight: float            # 0.0-1.0
    summary: str
    is_counter: bool = False
```

`Evidence.weights_sum_to_one` — a model-level validator requiring that all
**non-counter** (`is_counter=False`) items' weights sum to `1.0 ± 0.01`.
Counter-evidence weights are excluded from this sum deliberately: they
represent risk/caveats, not competing support, so they don't need to
"balance" against the supporting total.

Only `rule_based` is actually used today (by `_fallback_signal()`); the
other `EvidenceSource` values (`fundamental`, `macro`, `news_sentiment`,
`options_flow`, `sector_relative`, `cross_asset`, `earnings`) exist in the
schema as a forward-looking contract for evidence sources not yet
implemented — the LLM prompt allows the model to pick from the full set,
even though today's only non-LLM producer sticks to `rule_based`.

## `TimeframeMatrix` / `SignalOutput`

`TimeframeMatrix` bundles multiple `Signal`s (one per timeframe) plus
`alignment_score` and `divergence_pattern` — see
[concepts/multi-timeframe.md](multi-timeframe.md).

`SignalOutput` is the actual top-level API response: `ticker`, `signal`
(the primary call), `matrix` (currently always `None` on the live route —
see [multi-timeframe.md](multi-timeframe.md#1-weighted-composite-score-scoringmtfpy)),
`feature_unavailable` (list of degraded-feature string tags like
`"detection_degraded"`, `"llm_synthesis"`, `"synthesis_error"`), and
`schema_version` (currently `"1.0"`).

## Frontend mirror (`web/src/lib/types.ts`)

The frontend TypeScript types are a hand-maintained mirror of this Pydantic
schema (comment at the top of `types.ts` says so explicitly) — there is no
codegen link between the two. If a backend field changes shape, `types.ts`
must be updated by hand; nothing currently guards against drift. Flagged in
[ops/known-issues.md](../ops/known-issues.md) as a latent risk.
