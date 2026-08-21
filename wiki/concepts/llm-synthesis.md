# LLM Synthesis (L5)

[`synthesis/mtf_llm.py`](../../src/signals_app/synthesis/mtf_llm.py) is
where rule-based confluence data becomes a structured, evidenced directional
call. This is the boundary the frontend cares most about surfacing honestly
(`ai_degraded` — see [signal-rendering.md](signal-rendering.md)).

## Provider chain

`Settings.llm_provider` resolves to `"openrouter"` (if `OPENROUTER_API_KEY`
is set — takes priority), `"gemini"` (if only `GEMINI_API_KEY` is set), or
`"none"`.

`_call_llm()` tries providers in order and never raises — every failure mode
returns `None` up the chain instead:

1. **OpenRouter** (`_call_openrouter`) — POSTs to
   `OPENROUTER_BASE_URL` with `response_format: json_object`, model default
   `google/gemini-2.0-flash-001` (overridable via `OPENROUTER_MODEL`).
   15s timeout. Strips markdown code fences if the model wraps its JSON.
   On any exception (timeout, JSON parse error, HTTP error) → `None`, and
   if OpenRouter was configured but failed, `_call_llm` explicitly falls
   through to **also try Gemini** if a Gemini key exists.
2. **Gemini** (`_call_gemini`) — `google-generativeai` SDK,
   `gemini-2.0-flash` model, 10s timeout via `asyncio.wait_for` wrapping
   `asyncio.to_thread(model.generate_content, prompt)` (the SDK call itself
   is synchronous).
3. **Rule-based fallback** (`_fallback_signal`) — used when both LLM calls
   return `None`, or when `no_llm=true` is passed, or when `llm_enabled` is
   `False` (no keys configured at all).

## The fallback signal

`_fallback_signal(timeframe, features)` is deliberately simple — it does not
try to imitate LLM reasoning:

```python
if score >= 0.35 or pct > 2:   direction, confidence = "buy", 0.55
elif score <= -0.35 or pct < -2: direction, confidence = "sell", 0.55
else:                            direction, confidence = "hold", 0.30
```

Always returns exactly one evidence item, `source="rule_based"`, `weight=1.0`,
summarizing the confluence score and price change that drove the call.
Always sets `ai_degraded=True`, `prompt_version="fallback_v1"` — so a
fallback can never be mistaken for an LLM-reasoned signal downstream.

## The prompt contract

`PROMPT_TEMPLATE` asks for exactly one JSON object matching the `Signal`
schema ([schemas/signal_output.py](../../src/signals_app/schemas/signal_output.py)),
and repeats the schema's own validation rules in the prompt itself so the
model is steered toward valid output before Pydantic ever sees it:

- confidence strictly between 0 and 1 (never exactly 0.0 or 1.0)
- HOLD confidence ≤ 0.75
- confidence > 0.6 requires ≥1 counter-evidence item (`is_counter: true`)
- supporting (non-counter) evidence weights must sum to exactly 1.0

If the LLM's JSON fails `Signal.model_validate()` anyway (a real model can
still violate its instructions), `_compute_single_timeframe()` catches the
validation error and substitutes the rule-based fallback rather than
propagating a 500 — this is a second, independent safety net beyond the
prompt wording itself.

## Caching

Handled by `_compute_single_timeframe()` — see
[multi-timeframe.md](multi-timeframe.md#caching) for the per-timeframe TTL
table. Only non-degraded (real LLM) signals get cached.

## Entry points

- `synthesize_single()` — sync wrapper (spins up its own event loop) used by
  the single-symbol API route and the `--no-llm` CLI mode
  (`scripts/analyze.py`).
- `build_timeframe_matrix()` — async, concurrent per-timeframe, used for the
  multi-timeframe matrix (see [multi-timeframe.md](multi-timeframe.md)).
