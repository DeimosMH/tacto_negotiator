# Review 03 — Routing architecture (`agents/routed.py`, `docs/ROUTING.md`)

Reviewer 03 of 50. Scope: `src/tacto_negotiator/agents/routed.py` (513 lines at review time — the
file grew from 466 during this review as commits `58486c0` and `9cccd42` landed; everything below
audits the code as-is at HEAD `9cccd42`) and `docs/ROUTING.md`. Evidence: 15 hosted practice result
files in `docs/results/`, the hosted-replica fixture (`tests/fixtures/practice_feedback.json`),
offline reproduction of the fallback paths, and the test suites
(`test_routed.py`, `test_routing_integration.py`, `test_script_coverage.py` — 80 passed).

## Executive summary

The router's bones are good: one shared validator for every route, a concession schedule
(`ask_limit`) that binds scripts and models alike, and fallbacks that have already been hardened
by three shipped fixes (`7b16473`, `f0d65d2`, `58486c0`). The current hosted run
(`live-practice-2026-10-24-opus-high-v4.json`, avg 0.636) shows the architecture surviving model
failure: 11 of 31 model attempts fell back and 9 of 10 cases still closed decently.

Four things are holding the score back, in order of leverage:

1. **The brand-new `_premature` accept-override (58486c0) has no escape valve and zeroed P02.**
   It rejected the model's loss-cutting accept three times in a row, re-sent an unchanged counter
   each time, the supplier walked, and a 0.264-score deal became 0.00. One capped/conditioned
   override is worth roughly +0.03 on a 10-case average and removes a 0-bomb from unknown final
   cases.
2. **The strong route — the best-measured configuration this repo has — has never been enabled in
   a hosted run.** Every hosted result has `strong: null`; v4 alone shows 11 `strong_unconfigured`
   decisions. The offline benchmark measured hybrid (Sol fast + Astra strong) at 0.731 vs 0.670
   Sol-only, and `docs/MODEL_SELECTION.md` already calls it qualified. The 40 s budget (58486c0)
   makes the 20 s strong threshold trivially reachable; the lever is configuration, not code.
3. **8 of 11 v4 fallbacks are `model action rejected` — the validator throwing away a complete,
   on-schema reply over one bad sheet or field.** Per-sheet salvage (drop the offending sheet,
   keep the rest) and unknown-key stripping would convert most of these into usable model moves.
4. **Sticky model routing (`_semantic_blocker`) plus the broad `CONDITIONAL_WORDING` list sends
   purely quantitative rounds to the model**, buying a timeout/rejection lottery ticket on every
   subsequent round of the case. The scripts' closures (`small_cost_gap`, `round_reserve`,
   `quote_plateau`) stay unreachable for the rest of any case once one model decision happens.

`docs/ROUTING.md` has drifted from the code (25 s vs 40 s budget, "90 %" anchors vs the per-goal
anchor table, no mention of `_premature`/`_limit_counter`).

## Score mechanics in this area

Case score = (C_open − C_deal) / (C_open − C_best), the hosted arena fixing C_open and C_best.
The published worked example (opening €347,904, best €324,224, deal €332,800 → 0.64, quoted in
`docs/FAQ_ADVANTAGE.md`) is the shape of every case. Routing touches the score four ways:

- **Which move gets made** — script (anchored schedule, ~0 ms), fast, or strong model, all clamped
  by `validate` to `ask_limit` (`routed.py:114-116`), the per-goal anchor table
  (`play.py:276-277`) and the health floor (`cost.py:269-290`).
- **Whether a deal exists at all** — a walk-away or a lapsed thread scores 0; the fallback chain
  (`routed.py:495-513`) exists to prevent that.
- **Whether rounds are wasted** — each model decision spends 8-40 s and carries a failure
  probability (v4: 11/31 = 35 %); each failure burns the budget and hands the round to a repair
  that repeats the schedule anchor.
- **When the case closes** — `small_cost_gap`/`round_reserve`/`quote_plateau` accept at the right
  moment; `deadline_close`/`terminal_offers` accept whatever stands when time or rounds run out.

The hosted evidence base (per-case scores across runs, `fallbacks` counted per decision):

| run | avg | model attempts | fallbacks | notes |
| --- | --- | --- | --- | --- |
| astra-max (16:13 UTC, pre-fix) | 0.113 | 9 | 9 | every attempt timed out; 8/10 cases accepted the untouched opening for 0 |
| opus-max (17:08, with 7b16473 only) | 0.114 | 9 | 9 | same failure mode; motivated f0d65d2 |
| opus-high-v3 (18:12) | 0.623 | 15 | 5 | P01 both decisions timed out at 25 s → 0.41 |
| opus-high-v4 (current code, 40 s) | 0.636 | 31 | 11 | P02 death spiral → 0; P01 0.874 |

## Findings (ranked)

### F1 — `_premature` override loops unchanged counters and forfeited P02 v4 (severity: high)

`routed.py:490-492` converts a validated model accept into `ValueError` whenever
`_premature` (`routed.py:179-192`) says the standing offer is "open", moves remain, and the
accepted cost is > 1.005× our last ask's cost. In hosted v4 P02 (stabilise, supplier raised its
package-adjusted price 370.96 → 373.88 after our health-floor opening):

- d2, d3, d4: the model accepted the standing open offer each time; the override fired each time
  (`error: "model accepted an open offer above our last ask"`, v4 decision log); the fallback
  re-sent the same 361.51/90-day sheet; the supplier held ("no movement from us, none from it" —
  the calibrated model in `hosted_like.py:76-77`) and finally walked. End: `no_deal`, score 0.00.

Offline reconstruction (`tests/fixtures/practice_feedback.json` P02, d2 state) confirms
`_premature → True` and the fallback producing another counter. Accepting the d2 standing offer
scores 0.264 (C 2,270,369 vs opening 2,282,867, best 2,235,514) — exactly what v3's P02 achieved
when the model's accept went through. **The override cost the full case score.**

Why the loop never exits: the escape conditions are (a) supplier status changes to
final_offer/agreed, (b) `moves_left <= 1`, or (c) the standing cost falls within 0.5 % of our ask.
A supplier that restates its open offer with no movement satisfies none — the override is
unbounded and repeats an *identical* ask, which the calibrated (and, evidently, hosted) supplier
treats as zero movement. The check also ignores direction: it fires even when the supplier's
price *rose* since its previous offer, i.e. when we are losing ground and cutting losses is
correct.

### F2 — the strong route is fully built, offline-qualified, and never configured hosted (severity: high)

`routed.py:477-481` routes `complex_` decisions (relationship goals, ≥4 negotiable fields,
contradicted claims) to the strong completion when `remaining >= strong_min_seconds` (20 s).
Evidence:

- All 15 hosted result files have `models.strong = null`. v4 shows 11 of 31 model decisions
  labelled `strong_unconfigured` (P02/P03/P10 relationship goals, P08/P18 four-field cases).
- The only strong-route executions anywhere are offline: `docs/results/models.json` (35 strong
  calls) and `pilot.json` (1). There, **hybrid (Sol fast + Astra strong, low effort) averaged
  0.731 vs 0.670 Sol-only and 0.569 scripts-only, with zero fallbacks**, Astra p95 latency 5.07 s.
  `docs/MODEL_SELECTION.md:5` already documents this as the measured hierarchy.
- Before 58486c0 the 20-of-25 s threshold was nearly unreachable in wall-clock terms; with the
  40 s budget, `remaining` after script + prepare + render measures ~39.9 s on every replica case
  (measured: scripts 0.1-5 ms, `prepare` 16-28 ms, `render` 0.3-3.9 ms), so the gate is now
  effectively "is a strong model configured?".

The nuance from models.json: in the hybrid configuration *all* 35 model calls went to Astra and
zero to Sol — with strong configured, the fast branch only appears on non-complex cases that
scripts miss. Today's hosted setup (fast = Opus high) is carrying complex decisions Opus handles
slowly (v3 timeouts) or rejects (v4's 8 `model action rejected`).

### F3 — validator rejects whole model replies; 8 of 11 v4 fallbacks are `model action rejected` (severity: high)

`validate` (`routed.py:62-128`) is all-or-nothing: one sheet above `ask_limit` (line 114-116),
one unknown ID in the body (line 87), one extra JSON key (line 67), one off-option value
(line 103-105) — and the entire reply is discarded to the fallback. v4: 8 `model action rejected`
fallbacks (P01, P02, P03, P04, P05×2, P08, P18) versus 3 `_premature` and 0 timeouts. The 40 s
budget eliminated timeouts as the top failure source; schema/schedule rejections took their place.

This is doubly wasteful: the model call is spent, and the repair then re-derives a move from the
schedule that the model may have beaten. Note `llm.py`'s legacy `guard` (the non-routed path)
already softens some of this — it *strips* unknown IDs (`llm.py:248-254`) and completes/snaps
sheets (`llm.py:241-244`) instead of rejecting, while `validate` hard-rejects. The raw model
replies are not recorded in the run JSONs (only the final decision), so the exact reject reason
distribution is an open question — but the asymmetry with the legacy guard is code fact.

### F4 — sticky semantic routing and broad wording list keep quantitative rounds on the model (severity: medium)

`_semantic_blocker` (`routed.py:218-229`) returns True for the rest of the case once
`self.context.case_id` matches (set at `routed.py:470-472` after the first model decision), for
every relationship-goal case unconditionally (line 221), or when the latest reply contains any of
14 phrases (`CONDITIONAL_WORDING`, `routed.py:41-43`) — including ordinary prose words
("alternatively", "whole range", "of our cost", "no-go", "faster payment"; verified: each alone
routes a routine, fully-quantitative round to the model).

Consequences, verified offline on a routine case:

- After one wording hit, a later round with a clean complete quote and no conditional language
  still routes to the model (sticky), and never returns to `bounded_concession`,
  `small_cost_gap`, `round_reserve` or `quote_plateau` — those closures live in
  `_structured_bargain`, which `_script` can no longer reach. Only `terminal_offers` (rounds
  exhausted) and `deadline_close` remain.
- Relationship-goal cases (4 of 10 hosted: P01, P02, P03, P10) never see the bargain scripts at
  all, even though the health-estimate machinery that prices them (`decision_cost` +
  `cheapest_price` floors, `cost.py:259-290`, estimates present for all four cases) was built
  exactly for that. Every bargaining round of 40 % of the hosted set is a model call — in v4 that
  is 4 attempts on P02, 4 on P03, 1 on P10, 3 on P01.

The cost is not that the model moves badly — it is that every sticky model round is another
8-40 s, 35 %-failure lottery ticket (v4), and the scripts' mid-case accept triggers are lost.

### F5 — fallback repair still has two dead-end states (severity: medium)

The repair chain (`routed.py:504-510`) improved a lot across the three commits, but:

- **Walk-away with no enumerated package forfeits the case.** `_limit_counter` is only tried when
  the fallback action is an *accept of an open offer* (`elif open_offer and ...`, line 509). When
  `closing_move` walks away (no acceptable standing offer) and `_structured_bargain` returns None,
  nothing is tried — the walk-away stands and the case is 0. `_limit_counter` is exactly the
  "keep negotiating at the schedule price" move for this state (hosted P10 walked in round 1
  historically). Note the earlier `eace94d` commit message claims "counter instead of walking
  away while moves remain, for every goal" — the code only half-honours that for the walk-away
  branch.
- **A single prose-only buyer message kills every later repair.** `_structured_bargain` returns
  None if any buyer event lacks term sheets and is not the confirmation template
  (`routed.py:327-329`). Since validated model sends may carry zero sheets, one conversational
  model round permanently disables the structured repair for the whole case (all threads — the
  loop `return None`s on the first hit). The new `_limit_counter` does not check prose, so the
  `elif` branch covers open-offer accepts — but the walk-away branch is left with nothing.

Verified offline: timeout fallback with a prior prose-only buyer message in a routine case →
repair returns None; only the open-offer path (`_limit_counter`) still produces a counter.

### F6 — ROUTING.md has drifted from the code (severity: low, but it is the routing contract)

- `docs/ROUTING.md:59`: "maximum 25-second decision" — the default is now 40 s
  (`routed.py:51`, commit 58486c0).
- `docs/ROUTING.md:51` and `:67`: openings anchored at "90 % of the standing unit price" / "90 %
  of the current quote's full cost C" — the code uses the per-goal anchor table
  (`ANCHOR_BY_GOAL`, `play.py:276-277`: leverage 0.20, routine 0.18, quality_first 0.15,
  secure_supply 0.12, stabilise 0.08) applied to unit price, plus the health floor and the
  opening cap (`routed.py:378-382`, `play.py:281-294`).
- Nothing documents `_premature`, `_limit_counter`, the 40 s budget rationale, or the new
  fallback order (`routed.py:504-510`); the doc still describes the pre-58486c0 world. Its
  "Modify one behavior step by step" checklist would have a reader test the wrong defaults.
- The route-reason table (`ROUTING.md:79-91`) is still accurate for reasons but the fallback
  section (line 37, "Model error, timeout or rejected output ---> deterministic fallback") now
  undersells a three-stage repair.

### F7 — near-dead branches (severity: low)

- `simple_opening_counter` (`routed.py:430-443`) requires ≤ 2 negotiable fields, one supplier,
  no alternative, routine/leverage. Every hosted practice case has ≥ 3 negotiable fields
  (fixture audit), so on the hosted set this branch can never fire; only local `case:S01`
  qualifies. Its anchor also duplicates `_structured_bargain`'s opening path with a narrower
  cap. Not harmful — but it is untested strategy weight that will not run where score is earned.
- `strong_min_seconds=20` vs `max_decision_seconds=40`: the gate is now almost always satisfied
  (measured), so it no longer discriminates anything except very late-wave decisions. If strong
  is enabled per F2, consider giving strong a *sub-deadline* (e.g. 28 s) so a slow strong call
  still leaves the repair path time — currently strong gets the entire budget and its timeout
  consumes all of it (the astra-max run: max effort, 9/9 timeouts, all cases 0).

## Recommendations (prioritized)

1. **Cap and condition the `_premature` override** (`routed.py:490-492`, `_premature` at
   179-192). Concretely, allow the model's accept when any of: (a) this supplier's standing cost
   has *risen* since its previous offer (we are losing ground); (b) the fallback counter would
   repeat our previous ask unchanged (no movement → no concession, per the calibrated supplier
   and P02); (c) the override has already fired once for this supplier (one free push, then
   trust the model). Each is a small predicate on data already in `threads`.
   *Expected impact:* P02-class cases keep ~0.26 instead of 0.00 → +0.02-0.03 on a 10-case
   hosted average, and removes an unbounded repeat-loop that could zero unknown final cases.
   *Risk:* re-introduces some premature accepts the commit was designed to stop (v3 P01/P02) —
   condition (b) alone keeps most of that protection.
2. **Configure and rehearsal-test the strong route hosted** (`LLM_STRONG_MODEL=gpt-6-astra`,
   `LLM_STRONG_EFFORT=low` per `docs/MODEL_SELECTION.md:15`; 98 practice sessions remain).
   No code change needed. If practice confirms the offline delta even partially
   (offline: +0.06 over Sol-only on 100 replica matches), this is the largest single lever in
   this file — v4 spent 11 of 31 model decisions on `strong_unconfigured` cases.
   *Risk:* strong latency (astra-max at *max* effort timed out 9/9; qualification must pin
   effort=low, p95 ≈ 5 s offline).
3. **Salvage partial model replies in `validate`** (`routed.py:62-128`): drop only the sheets
   that violate `ask_limit`/options/must-haves and keep the clean ones (the equal-cost filter at
   line 121-123 already prunes to the cheapest); strip unknown JSON keys and unknown IDs
   (as the legacy guard `llm.py:248-254` already does) instead of rejecting the reply; log the
   reject reason into `last_decision` so the 8-v4-style failures become diagnosable.
   *Expected impact:* converts most of the 8/31 `model action rejected` fallbacks into model
   moves; each recovered decision avoids one schedule-anchored repair round. Estimate +0.02-0.05
   hosted; also cuts per-case model spend. *Risk:* a salvaged sheet set the model did not intend
   — bounded by the shared `ask_limit` and must-have gates, so the floor is the current repair.
4. **Let quantitative closure run even when model context is sticky** (`routed.py:218-229`):
   before returning True, check `_structured_bargain`'s own closure triggers — if the latest
   replies carry no conditional wording and no contradicted claims, allow
   `small_cost_gap`/`round_reserve`/`quote_plateau`/`bounded_concession` to run (the model keeps
   prose rounds). *Expected impact:* fewer model rounds on long cases (P03 v4: 4 model calls;
   P05: 4 calls, 2 fallbacks) → fewer 35 %-failure lotteries and earlier optimal accepts;
   +0.01-0.03 with high variance. *Risk:* a semantics-bearing reply misread as quantitative —
   mitigated by still requiring the wording/claim checks on the *latest* reply.
5. **Extend `_limit_counter` to the walk-away fallback** (`routed.py:509`): try it whenever the
   fallback would walk away with moves left, not only for open-offer accepts. One-line change
   (`elif (open_offer or action == "walk_away") and ...`). *Expected impact:* prevents
   P10-class round-1 forfeits when `_structured_bargain` bails (missing options, >256 combos,
   prior prose); hard to bound, but a saved case is worth its whole score.
6. **Refresh `docs/ROUTING.md`** (40 s default, per-goal anchors, `_premature`/`_limit_counter`
   fallback order, strong-route configuration pointer). Zero score impact, but the doc is the
   stated contract for anyone tuning this file under time pressure.
7. (Optional) **Give the strong route a sub-deadline** (e.g. `deadline=min(deadline,
   start + 0.7 * budget)` for the strong call only, falling through to the repair chain on
   timeout) so enabling F2 cannot reproduce the astra-max all-timeout pattern.

## Risks & trade-offs

- Relaxing `validate` (R3) trades rejection-strictness for intent fidelity; the safety net
  (`ask_limit`, must-haves, option snapping, alternative comparison) is unchanged, but a
  half-salvaged reply could pick a worse *legal* move than the scripted repair. Ship behind the
  reject-reason logging first.
- The `_premature` fix (R1) and stickiness fix (R4) both hand more authority to the model. The
  hosted record justifies suspicion (v3 P01/P02 premature accepts; the SYSTEM prompt already
  warns "do not spend a round merely to avoid accepting an opening") — which is why R1's
  conditions keep the override for the first occurrence rather than deleting it.
- Enabling strong (R2) adds a second provider dependency and a second latency distribution to
  the final; the astra-max disaster is the cautionary tail. Effort must stay low/medium with a
  measured p95 under ~15 s, and R7's sub-deadline is cheap insurance.
- Every recommendation increases routing complexity in a file that is already dense; each should
  land with the near-miss fixtures the test suite already favours (`test_routed.py` covers the
  current `_premature`/`_limit_counter` behaviour — 80 tests green at HEAD).

## Open questions

1. What exactly did `validate` reject in v4's 8 `model action rejected` replies? The run JSONs
   record only the post-fallback `sent_action`, not the model's raw output. Adding raw-reply
   capture (already sanitized of prompts elsewhere) would settle whether R3 should target
   `ask_limit` overshoot, schema keys, or ID citations.
2. Did the hosted P02 supplier walk because of the *identical* repeated ask, the 90-day payment
   premium, or a per-supplier patience limit? The replica (`hosted_like.py`) models the first
   two; if patience is real, R1(b) is even more valuable.
3. Would the health-floor-anchored scripts (estimates exist for all four relationship cases)
   outperform the model on relationship openings if `_semantic_blocker` admitted them for round
   1 only? P02 v4's d1 rejection pushed the opener into the floor-lifted repair, after which
   every path scored 0 — the opening round looks under-scripted for exactly the case type the
   estimates were built for.
4. Is `strong_min_seconds` still meaningful at 40 s (F7)? If strong is enabled, measure whether
   a 20 s floor ever diverts a complex decision to fast in practice on hosted timing.
