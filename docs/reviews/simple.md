# Codebase Analysis: `tacto_negotiator`

## What this is

A well-structured Python package for an automated buyer agent in a supplier-negotiation competition ("Tacto Negotiation Arena"). The architecture is sound and has several genuinely good ideas:

- **Clean separation of concerns**: `client.py` (HTTP), `cost.py` (deterministic pricing/C), `docs.py`/`data.py` (document & evidence access), `play.py` (orchestration/deadline logic), `agents/` (decision strategies).
- **"Code does numbers, LLM does language"** — the LLM never computes costs; all arithmetic goes through `cost.py`. This is the right design.
- **Idempotent retries** via `X-Move-Id` (safe mid-negotiation replay), rate pacing just under the arena limit, and deadline-aware document reading with a re-read pass for skipped attachments.
- **Defense in depth**: `guard()` validates LLM output (must-haves, term options, unknown data IDs stripped, accepts only via `acceptable_offer`), with a naive fallback if the LLM fails.

I reviewed all of `client.py`, `docs.py`, `data.py`, `cost.py`, `play.py`, `agents/naive.py`, `agents/llm.py` (I did not read `sim.py`, `competition.py`, `compete.py`, `benchmark.py`, `evaluation.py`, `routed.py`, `completion.py`, `cli.py`, `web.py` in detail — findings below are based on the core). Findings, ordered by impact:

---

## 1. Bugs & correctness risks

### 1.1 `parse()` — greedy regex will corrupt JSON (`agents/llm.py:253`)
```python
match = re.search(r"\{.*\}", text, re.S)  # first { to LAST }
```
If the model writes any `{...}` after the decision object (a second example, a stray brace in the body text), `json.loads` fails or silently misparses. Fix:
```python
def parse(text):
    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON in reply: {text[:200]}")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])  # balanced-brace aware
    return obj
```
Even better: use the provider's JSON/structured-output mode.

### 1.2 `NaiveAgent.decide` — `IndexError` when no live threads (`agents/naive.py:17-22`)
If `quoting` is empty it returns `live_threads(threads)[0]` — which crashes if *no* thread is live. It's a fallback agent, so this fires exactly when things are already going wrong. Add `if not live_threads(threads): raise/walk via closing_move`.

### 1.3 `closing_terms` layering order (`cost.py:84`)
```python
for layer in (standard or {}, case.get("fixed_terms", {}), terms):
```
The term sheet **overrides `fixed_terms`**. "Terms fixed by your enquiry" should be immutable — a sheet that (accidentally or via `complete_offer` snapping) changes a fixed term gets mispriced here even though the arena would presumably reject it. Put `fixed_terms` last, or assert no sheet term differs from a fixed term.

### 1.4 `valid_value` — `AttributeError` on bools (`cost.py:41`)
`type(value) is not int and not value.is_integer()` — a `bool` passes the first check and has no `.is_integer()`. Reject `bool` explicitly for numeric fields.

### 1.5 `docs.quote_terms` — one bad field kills the whole quote (`docs.py:65-74`)
`_value` raises `ValueError` on an unrecognized index clause, which `fetch_documents` converts to `read_failed` → conservative routing → potentially worse deal. Parse field-by-field and keep the rest:
```python
try:
    out.setdefault(section, {})[LABELS[label]] = _value(LABELS[label], raw)
except ValueError:
    out.setdefault(section, {}).setdefault("_unparsed", []).append(line)
```
Also make label matching whitespace-tolerant (`re.match(rf"{re.escape(lab)}\s+", line)`); PDF extraction loves to mangle spacing.

### 1.6 `data.document` — binary files crash (`data.py:98`)
`path.read_text(encoding="utf-8")` raises `UnicodeDecodeError` if a package document is a PDF. Route through `read_document(name, bytes)` as fallback.

### 1.7 Mixed clocks (`play.py`)
`deadline`/caps use `time.time()` while everything else uses `time.monotonic()`. An NTP step or manual clock change mid-final silently breaks the cap logic. Convert the cap to a monotonic deadline once at wave start (`mono_cap = time.monotonic() + seconds_left`) and use that.

### 1.8 LLM timeout vs. move limit (`agents/llm.py:83,105`)
`timeout=45.0` for the LLM leaves only ~15 s for `render()` (which recomputes all term tradeoffs) + HTTP submit within the 60 s move limit — and `render` runs *before* the call. Either shrink the LLM timeout to ~30 s with one retry, implement `decide_timed` (the harness already supports it), or precompute `render`'s expensive parts.

---

## 2. Performance & cost improvements

1. **TERM TRADEOFFS recomputed every round** (`llm.py:186-195`): for every field × up to 20 options × every thread × every round. Cache keyed by `(supplier, standing_sheet_id, std)` — the standing sheet only changes when the supplier moves.
2. **The ~1,200-word SYSTEM prompt is resent every call.** Use Anthropic/OpenAI prompt caching (explicit cache-control breakpoints), or split static (system) vs. dynamic (brief) so the static part caches.
3. **No retry on the LLM call** — one 503 from the provider = naive fallback + likely weaker result. Add 1–2 retries with short backoff *inside* the `complete` wrapper; only fall back after retries exhaust.
4. **Rate limiter is process-global** (`client.py:_pace` uses one lock per client) — good — but `play_all(parallel=8)` with one shared client means all cases share 9 req/s. That's intended; just confirm `new_table()` actually creates per-case clients if you ever want per-case pacing.
5. **`_ids` / `negotiation_facts` are rebuilt per lookup path** — they're `cached_property`/local, fine, but `negotiation_facts` runs per case once and re-filters all facts each time; fine at this scale.

---

## 3. Negotiation-quality improvements (score impact)

1. **Relationship cost is shown but never priced into asks.** The prompt says "C shown leaves that out" and `ask_limit` excludes supplier health. For `stabilise`/`secure_supply` cases, add an estimated `supplier_value` (you have `interest_rate`, cost drivers, quotes — estimate `full_cost`) and include `relationship_cost` in the ask limit; otherwise the agent can push a supplier below its sustainable margin and pay for it in C.
2. **`ask_limit` anchoring is price-centric**: the opening moves only `unit_price`, ignoring that `payment_days`, `contract_months`, `volume`, and `effective_date` are cheap concessions that buy price movement (the prompt says this, the limit math doesn't). Compute the opening over the *whole negotiable space* (e.g., greedy: hold payment terms ~15 days better than standard, ask cheapest Incoterm, then anchor price).
3. **No multi-thread interleaving within a case**: `decide` returns one supplier per round. Since each thread has its own 60 s timer, you could message supplier B while waiting for A's reply (the harness's `received`/deadline logic already tracks per-thread clocks).
4. **Walk-away as leverage is unused** — `acceptable_offer` is purely conservative. With a credible cheaper alternative, a conditional walk signal ("we'll decide Friday") can trigger concessions; this needs care given the "never reveal urgency" rule.
5. **Supplier-memory across rounds**: the LLM sees only `events[-6:]` per thread. Summarize earlier rounds (concessions made, claims already used, refusals) into a compact "thread state" so long negotiations don't lose context — the cited-ID list exists; extend it to refusals and asked questions.

---

## 4. Engineering hygiene

- **No tests in the zip.** Add: golden-value unit tests for `cost.py` (`score`, `cost_breakdown`, `_years` edge cases: 0-month contracts, effective dates beyond horizon); fixture-based tests for `quote_terms`/`read_document`; round-trip tests for `thread_events`/`moves_left` with timeout data; replay tests feeding recorded threads through `guard()` (unknown-ID stripping, must-have rejection).
- **No packaging metadata** (`pyproject.toml`, version pinning, `requirements.txt`). `__pycache__` binaries are shipped in the zip — exclude them (`.gitignore`/build hygiene).
- **Observability**: log fallback-to-naive rate, LLM latency, parse-failure rate, and per-case final C vs. alternative C as structured fields; keep prompt/response traces for offline A/B of the SYSTEM prompt (you clearly iterate on it — `benchmark.py` should grade prompt variants, not just agents).
- **Config hardcoding**: `ANCHOR_BY_GOAL`, `GAP_STEP`, `MAX_ROWS`, dossier limits — move to a config so tuning doesn't require code edits.

---

## Suggested priority

| Priority | Item | Effort |
|---|---|---|
| P0 | `parse()` raw_decode fix; `valid_value` bool; naive `IndexError` | minutes |
| P0 | Unit tests for `cost.py` + `quote_terms` | hours |
| P1 | Monotonic cap deadlines; LLM timeout budget; LLM retry | hours |
| P1 | Fixed-terms precedence in `closing_terms`; per-field quote parsing | hours |
| P2 | Tradeoff caching; prompt caching; relationship cost in ask limit | 1–2 days |
| P2 | Multi-thread interleaving; thread-state memory | days |

The foundation is genuinely good — most improvements are about making the existing careful design robust to the failure modes it currently handles by *losing* (fallbacks, conservative routing) rather than by recovering. Happy to go deeper on any specific file or write the fixes/tests for the P0 items if you want.