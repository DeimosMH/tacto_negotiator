# Review 05 — Model-backed decisions: prompts, output contract, budgets (`agents/llm.py`, `agents/completion.py`, `docs/MODEL_SELECTION.md`)

Reviewer 05 of 50. Read-only analysis plus this file. All commands run offline (pytest, local
python against `tests/fixtures/practice_feedback.json` and `data/`); no paid API calls.

## Executive summary

The model-backed path is architecturally sound — code owns arithmetic, the model owns language and
timing — but the recorded hosted-practice runs show the score is dominated by **whether the model
finishes inside the decision budget**, and the prompt under-informs the two decisions the model
actually controls: **when to accept** and **what to attach to each message**. Measured on the same
hosted practice set (`docs/results/live-practice-2026-10-24-*.json`, 10 cases, hosted scoring):

| run | model / effort | mean score | model timeouts | note |
| --- | --- | --- | --- | --- |
| opus-high-v7 | claude-opus-5-5 high | **0.699** | 1/40 | best |
| opus-high-v6 | claude-opus-5-5 high | 0.652 | — | |
| sol baseline | gpt-6.1-sol low | 0.500 | 0 | |
| opus-max | claude-opus-5-5 **max** | **0.114** | **9/16** | 8/10 cases closed at C_open → score 0 |
| astra-max | gpt-6-astra **max** | 0.113 | 9/16 | same collapse |

So: same code, same prompt, ±0.58 mean score purely from effort-vs-deadline mismatch. The other
large, cheap levers are prompt-side: an explicit accept-timing rule (the guard logged *"model
accepted an open offer above our last ask"* on 4/40 decisions in the best run), a
never-send-without-a-term-sheet rule (a prose-only message gets zero concession and burns one of
2–4 effective rounds in both simulators), a walk-away rule (a well-formed model `walk_away` passes
the guard unchecked in round 1 — verified), and a JSON repair pass (`parse()` fails on the most
common malformation: unescaped newlines inside the e-mail body — verified), which today discards
the entire model call.

## Score mechanics in this area

Score = (C_open − C_deal)/(C_open − C_best). In this architecture the model cannot change the
arithmetic: `cost.py` computes C, `routed.validate` snaps/reprices every sheet to the concession
schedule (`ask_limit`), and accepts are re-checked by `acceptable_offer`. The model's decisions
move the score only through:

1. **Accept timing** — each surviving round closes ~20% (or 10% after a big supplier concession)
   of the gap between the standing offer and our ask (`play.ask_limit`, GAP_STEP/HOLD_STEP).
   Accepting an open offer above our last ask forfeits those rounds; the `_premature` guard
   (routed.py:234–251) catches it but throws away the model's whole letter.
2. **Not ending the case early** — a `walk_away` (or a timeout-fallback that accepts the opening)
   pins the case at score 0 when there is no cheaper alternative.
3. **Evidence** — a first *verified* claim lowers the supplier's first counter (calibrated
   ≈0.5% of opening price, hosted_like.py:161–162); wrong/duplicate IDs cost trust.
4. **Sheet composition** — non-price trades at equal C extract supplier value (hosted P10:
   including the supplier's own lead time drew 31.6 vs 33.8, 0.671 vs 0.555 — routed.py:146–153).

Consequently, prompt work should target timing, round conservation, evidence correctness and
equal-cost composition — not price arithmetic (the prompt already says code reprices sheets).

Prompt size is healthy, not a latency driver: SYSTEM ≈1,250 tokens; full round-1 user prompt
≈2,500–4,800 tokens on the hosted replicas (measured). Latency is dominated by reasoning effort.

## Findings (ranked)

### F1 — Effort/deadline mismatch is the single largest observed score factor (critical)
Evidence: `docs/results/live-practice-2026-10-24-opus-max.json` — 9/16 decisions
`route=fast reason=TimeoutError`, `elapsed_s≈25.0 = budget_s`, `sent_action=accept` of the
supplier's *opening* (`offer:*-0`), supplier reply `agreed` at the opening price; 8/10 matches
score 0.000 with `cost == opening_cost` (P01–P04, P08, P11, P18). Identical pattern in
`astra-max.json` (0.113). Root cause chain: `completion.EFFORTS` (completion.py:17) happily
accepts `xhigh`/`max`; `RoutedAgent`'s own comment admits "Opus at high effort timed out at 25 s
on first messages" (routed.py:56–57); `compete.py:87` defaults `--decision-seconds 25`; the
fallback (`closing_move`) accepts any standing offer that beats the alternative — in round 1 that
is the opening. Nothing in `completion.py` or the run config couples effort to the budget.

### F2 — `parse()` rejects the most common model malformations; no repair, no retry (high)
`src/tacto_negotiator/agents/llm.py:276–287`. Verified offline:
`{"...","body":"Dear supplier,\n\nplease improve."}` (raw newlines inside a JSON string — exactly
what a model writing a business e-mail produces) and a trailing comma both raise `ValueError`;
the whole decision then falls back (llm.py:136–138; routed.py:697–715). There is no second
attempt: `model_attempts: 1` in every recorded decision. The tournament history recorded 10–17
fallbacks per model-only run (MODEL_SELECTION.md:11); v7 lost 5/40 fast calls to `ValueError`
(4 premature-accept + 1 rejected action). Note `test_provider_errors_are_not_retried`
(tests/test_completion.py:66–76) *pins* the zero-retry behavior as intended.

### F3 — SYSTEM tells the model when accepting is *legal*, never when it is *premature* (high)
llm.py:82–85: "Accept only a valid package no worse than the alternative. An exact fully
evaluated cost tie favors a deal…" — in round 2 of most cases the standing open offer already
beats the alternative, so this text reads as "you may accept now". Result: 4/40 decisions in the
best run (v7) ended in `ValueError: model accepted an open offer above our last ask`; the guard
discards the model's e-mail (including its claims — losing the verified-claim bonus) and
substitutes a canned scripted counter (`_structured_bargain`/"Please consider these complete
packages…"). The prompt states the ASK LIMIT for sheets but gives no rule for accepting relative
to it or to our last ask.

### F4 — The prompt encourages question-asking but never requires a term sheet with it (high)
llm.py:59–60: "Ask questions (the earliest lead time, cheaper variants)". Both guards pass a
`send` with empty `term_sheets` unchanged (verified: `LLMAgent.guard` and `RoutedAgent.validate`
both return `{"…", "term_sheets": []}`). Both simulators, calibrated on 383 hosted replies, give
**zero concession** to a prose-only message and consume a round: sim.py:216–217 ("We keep our
offer."), hosted_like.py:176–177 ("We hold our offer."). Suppliers' final offers arrive at fixed
rounds 2–5 (hosted_like.py:33–39 PROFILE_RANGES `final_round`), so one wasted round can cost a
whole concession step. The SYSTEM never warns about this.

### F5 — A well-formed model `walk_away` passes the guard unchecked (high, rare but catastrophic)
Verified offline: a model reply `{"supplier_id": sid, "action": "walk_away"}` in round 1 of a
no-alternative case is returned verbatim by `RoutedAgent.decide_timed` (`fallback: False`) and
ends the case at score 0. `_premature` guards accepts only (routed.py:692–694); the walk-away
repair exists solely on the exception path (routed.py:705–712), and hosted P10 already "walked
in round 1" once (routed.py:706 comment). SYSTEM's only guidance — "No deal can be optimal when
the alternative is cheaper" (llm.py:81) — says nothing about the no-alternative case where
walking scores zero. `render()` likewise only prints the alternative when one exists
(llm.py:174–176); when `alternative` is None the model gets no "no deal scores 0" signal at all.

### F6 — The arena's concession mechanics are in the codebase but not in the prompt (medium)
`hosted_like.py`'s docstring (lines 4–16) records what 383 hosted replies show: concede a share
of the gap to our ask (most in round 1, capped), *nothing* without movement, never below a hidden
floor, **agree once our sheet reaches the counter they would make**, final offer at a fixed small
round or after a large cumulative gap. SYSTEM has only fragments ("status final_offer: push
further and it may walk", llm.py:80–81). The model, which owns accept timing, is never told the
agree-threshold heuristic or the round scarcity. These are observations of the real hosted arena
(already the basis of `ANCHOR_BY_GOAL`'s "observed on this arena" style claims), so stating them
is legitimate prompt content, not simulator overfitting.

### F7 — Prompt invites a price ladder that the guard silently deletes (medium)
llm.py:41: "term_sheets: 0-3 alternative packages". `RoutedAgent.validate` keeps only sheets
within 0.3% of the cheapest `decision_cost` (routed.py:135–137) and reprices everything to
`ask_limit`. A model that dutifully proposes an ambitious / medium / safe ladder gets two sheets
discarded or repriced with no feedback, and its "safe" sheet may survive at a worse C. The prompt
never says the effective rule: *vary non-price terms at equal C*. The biggest recorded win of
exactly that pattern is documented in the code itself (hosted P10, 0.555 → 0.671,
routed.py:148–153).

### F8 — `render()` omits the concession history and the standing-best summary (medium)
llm.py:225–229 shows only the last 6 messages per thread. In a 6+ round thread the model cannot
see its own earlier asks or the supplier's full counter sequence, yet those are precisely what
accept-timing needs (F3). Also missing: a one-line cross-thread ranking ("cheapest live standing
C = X at sup:Y") in multi-supplier RFQ cases; the model must recompute it from per-thread blocks.

### F9 — completion.py contract details that convert into fallbacks (medium)
- `max_output_tokens=4096` default (completion.py:88): on the Responses API, reasoning tokens
  count toward the output cap; a high-effort think plus a 120-word e-mail plus 3 sheets can end
  `status=incomplete` → `response_text` raises → full fallback. Zero observed instances in the
  practice runs (all 5 ValueErrors were guard rejections), so this is insurance, not a measured
  loss. The Anthropic branch defaults to 16,000 (completion.py:177) which adaptive thinking can
  consume → `stop_reason == "max_tokens"` → `ValueError` (completion.py:202–203).
- Zero retries for transient 429/5xx (completion.py:130–152; pinned by test): a single blip on a
  semantic decision → `closing_move` fallback → in round 1 that accepts the opening when it beats
  the alternative (the F1 mechanism, at low effort).
- `"reasoning": {"effort": …}` is sent unconditionally (completion.py:135). `LLM_BASE_URL`
  overrides to non-OpenAI endpoints (e.g. the GLM-5.3-flash candidate in MODEL_SELECTION.md:28,
  "thinking cannot be disabled") risk a 400 on every call → silently scripted-only buyer.

### F10 — Legacy `llm_from_env` diverges from the documented contract (low)
llm.py:109–121: the OpenAI-compatible branch ignores `LLM_EFFORT` entirely, has a fixed 45 s
timeout that is not deadline-aware, and `os.environ["LLM_MODEL"]` raises `KeyError` before any
action when unset. MODEL_SELECTION.md:52 already warns about all three; the residual risk is
someone configuring the final through `--agent llm`. Also `LLMAgent.guard` (llm.py:249–253) does
not reprice sheets to `ask_limit` (the routed path does), so the legacy model's over-priced or
lowball sheets go out verbatim.

### F11 — MODEL_SELECTION.md omits the strongest current model-quality datapoint (low)
The doc's comparisons are pre-migration (correctly flagged), but it never records that on the
same hosted practice set claude-opus-5-5 @ high beat gpt-6.1-sol @ low 0.699 vs 0.500 — the
single most decision-relevant measurement for the final's semantic branch — including its
latency caveat (1 timeout/40 at the 25 s budget).

## Recommendations (prioritized)

### R1. Couple reasoning effort to the decision budget (F1) — protects ~0.5 mean on misconfig
- In `routed_completions_from_env` / `RoutedAgent.__init__`: reject or downgrade
  `effort in {"xhigh", "max"}` unless `max_decision_seconds >= 60`; at decision time, if
  `remaining < 15 s` and effort > low, call the fast client at low effort instead (clients are
  cheap to construct per call).
- Keep `--decision-seconds 25` for hosted (threads expire at 60 s minus margins), but add a
  startup assertion in the live runner: p95(measured) + 5 s <= decision budget, else refuse to
  start (MODEL_SELECTION.md's step 4 already specifies the 20 s/25 s target — enforce it).
- Add one regression test: an injected completion sleeping past the deadline must never lead to
  `sent_action=accept` of an untouched round-1 opening (the repair at routed.py:706–712 exists;
  in the opus-max run it still accepted — likely `clock() >= thread_deadline - 1` after the
  timeout; verify and fix the condition). This is partly reviewer-03 territory; the completion
  side is the effort clamp above.

### R2. Make `parse()` recover the common malformations; add one bounded repair retry (F2)
- In `parse()` (llm.py:276–287), after the strict scan fails, retry once over a sanitized text:
  escape raw control characters inside string literals (`\n`, `\t`), strip trailing commas
  before `}` or `]`, and retry `raw_decode`. Both failure cases verified fixable offline.
- In `RoutedAgent.decide_timed`, on `ValueError` from `parse` or `validate` (not on timeout),
  if `deadline - clock() > 8 s`, re-ask once with:
  `"Your previous reply was not a usable decision ({error}). Reply with ONE JSON object only,
  strings escaped (use \\n for line breaks), exactly one of: accept / send / walk_away."`
  Expected +0.01–0.03 mean (converts a share of the 10–17% fallback rate into model-quality
  moves at Sol's ~5.5 s p95; fits the 25 s budget).

### R3. Add explicit accept-timing and round-conservation rules to SYSTEM (F3, F4, F6)
Concrete replacement text for llm.py:66–85 (new bullets; existing ones unchanged):

```
- Rounds are scarce: each message spends one, and suppliers on this arena usually make their
  final offer after only 2-4 buyer messages. Never send a message without at least one complete
  term sheet: a question without a revised package gets no movement and wastes the round. Fold
  your questions into the e-mail that carries your revised package.
- How suppliers here move (observed on this arena): they close part of the gap between their
  standing price and your ask - most in round 1, less later; nothing without movement from you;
  never below a hidden floor; they AGREE once your ask reaches the counter they were about to
  make, which is usually close to your current ASK LIMIT. Keep each ask within reach of that.
- Accept timing: accept only when (a) the status is agreed or final_offer and the standing C is
  at most 0.5 % above the C of your last term sheet, or (b) the standing C is within 0.2 % of
  your current ASK LIMIT, or (c) the thread is about to expire. With messages left and the
  standing C above your last sheet, counter - do not accept an open offer.
- Never walk away while a live thread still has messages left and its standing term sheet is
  acceptable. When the case shows no alternative, walking away scores zero.
```
Expected +0.02–0.05 mean (v7 lost 4/40 fast calls to premature accepts; unknown but nonzero
prose-only rounds; rare early walk-aways are case-saving). All four rules restate what the code
guard enforces or the calibrated supplier does, so they cannot push the model into illegal moves.

### R4. Gate model `walk_away` in `RoutedAgent.decide_timed` (F5)
Mirror the accept check: if action is `walk_away`, `moves_left > 1`, and
`acceptable_offer(case, threads[sid], sid)` exists for any live sid → treat as the premature
case (raise → repair path already produces a counter). One-line analog of routed.py:692–694.
Prompt rule in R3 reduces the frequency; this removes the residual loss. Case-saving
(+0.00–0.10 on no-alternative cases).

### R5. Say the equal-C sheet rule and add a HISTORY line to `render()` (F7, F8)
- SYSTEM bullet: `Propose sheets that differ in non-price terms at equal C - code keeps only
  sheets within 0.3 % of your cheapest sheet's C and reprices to the ASK LIMIT, so a price
  ladder is wasted.` Expected +0.01–0.03 on trade-heavy cases (P10-class: 0.555 → 0.671).
- In `render()` (llm.py:188–192), extend the THREAD header with:
  `; their prices [138.55, 137.2]; our asks [124.7, 126.1]` and, for multi-supplier cases, one
  summary line `CHEAPEST LIVE STANDING C: 12345 at sup:X` before the thread blocks. Cheap
  (pure string work), directly feeds the R3 accept rule.

### R6. completion.py hardening (F9)
- Default `max_output_tokens` 4096 → 8192 when `effort >= "high"` (reasoning counts toward the
  cap on Responses); Anthropic default 16,000 → 32,000 (thinking + text share it; a
  `max_tokens` stop is a guaranteed fallback). Cost impact nil unless generated.
- One bounded retry (same deadline, `max_retries` stays 0 for the request itself): on
  `http429/500/502/503` with `Retry-After <= remaining - 3 s`, sleep and retry once. Update
  `test_provider_errors_are_not_retried` to pin the new contract instead of the old one.
- Send `"reasoning"` only when the base URL is api.openai.com or a new `LLM_REASONING=off`
  env flag is unset — protects non-OpenAI challengers listed in MODEL_SELECTION.md.

### R7. MODEL_SELECTION.md updates (F11)
Add the hosted-practice table (opus-high 0.699 / sol-low 0.500 / opus-max 0.114 at the same 25 s
budget, with timeouts counted), and an explicit "never run effort above high with a 25 s budget"
rule next to the existing qualification steps. Mark `--agent llm` as deprecated for the final or
port deadline-awareness into `llm_from_env` (F10).

## Risks & trade-offs

- **Longer SYSTEM prompt**: +6 bullets ≈ +200 tokens on a ~2.5–5 k-token prompt — negligible vs
  the latency already spent on reasoning; not cached for OpenAI beyond automatic prefix caching
  (the brief precedes per-round state, so caching still hits).
- **Accept-timing rule could overshoot**: if the model holds out too long on a `final_offer`,
  the supplier walks (the prompt's existing "push further and it may walk" plus the mirror
  alternative remain). The 0.5%/0.2% thresholds mirror the guard's `1.005` multiplier and
  `small_gap` 0.2% (`routed._structured_bargain`), so code remains the arbiter.
- **Retry-on-parse adds a second model call**: bounded by the 8 s reserve check; on Opus-high
  (mean 11.4 s) it usually will not fit — it mostly pays off on Sol-class latency. Never retry
  on timeout (would double the overrun).
- **Effort clamp reduces peak model quality** on the few decisions that could use it; the
  strong-model gate (`strong_min_seconds`) already exists for that, and measured data says a
  timed-out max-effort call is worth −0.58 vs a completed high-effort one.
- **Stating supplier mechanics in the prompt** is calibrated-observation, not ground truth for
  the unseen final; phrasing stays at the "observed on this arena" epistemic level the prompt
  already uses elsewhere.

## Open questions

1. Why did the opus-max fallback accept round-1 openings despite the repair path
   (routed.py:705–712)? Presumably `clock() >= thread_deadline - 1` after the 25 s timeout —
   needs a timing replay to confirm and a threshold fix (overlaps reviewer 03).
2. How often did model `send`s go out prose-only in the hosted runs? The recorded decisions
   capture closes only; adding `sent_message.term_sheets` length to the runner's decision log
   would make F4 measurable.
3. Do hosted suppliers actually penalize a *repeated* package (same sheet resent with a question
   appended)? The calibrated sim treats a repeated ask as zero-movement; if the real arena
   tolerates it, R3's "always attach a sheet" is even cheaper than assumed.
4. Is `output_config: {"effort": …}` (Anthropic) sufficient to cap thinking below the deadline,
   or does Opus need an explicit thinking-token budget for the 25 s window? MODEL_SELECTION's
   step-3 latency protocol answers this once measured.
