# Review 01 — Scoring math: `src/tacto_negotiator/cost.py` and every caller

Scope: `cost.py` (all 290 lines), all callers (`play.py`, `agents/routed.py`, `agents/llm.py`, `agents/naive.py`, `sim.py`, `competition.py`, `hosted_like.py`, `evaluation.py`, `data.py:supplier_estimates`), the arena kit's published copy (`materials/starter_kit/starter-kit/arena_kit/cost.py`), the brief's formula (`materials/starter_kit/brief/api.md` "Cost and score"), and the hosted audit (`docs/reviews/hosted-evaluation-audit.json`, `tests/fixtures/practice_feedback.json`).

## Executive summary

1. **The arithmetic is correct and must not be changed.** `cost.py`'s shared functions (`closing_terms`, `_years`, `_units_before`, `supplier_value`, `_soft`, `relationship_cost`, `cost_breakdown`, `total_cost`, `score`, `must_haves_ok`, `snap`) are byte-identical to the arena kit's published cost function (verified by diff), match the brief's formula term by term, and reproduce **310/310 hosted cost components, 32/32 hosted scores, and 10/10 hosted `c_alt` values to the cent** (`tests/test_evaluation.py` passed today; my independent re-run of the alternative-cost check matched all 10 hosted `c_alt` with Δ = 0.00). Any "fix" to the arithmetic is a score risk, not a score gain.
2. **The score is lost in term choice, not in the math.** Re-pricing the 16 accepted hosted deals with the unchanged `cost.py`, the non-price levers the function already prices correctly were left unclaimed, worth **+0.02 to +0.33 score per deal (mean ≈ +0.08 at a fixed price)**: `payment_days` 90 vs accepted 14–60 in 16/16 deals, an index clause kept where `None` was cheaper in 7 deals (up to €32,430, +0.19 score), `contract_months` 12 in a 2-year horizon in 2 deals (+0.12–0.15), and one `effective_date` chosen against the cost gradient (+0.18). These are ceilings — each lever is a trade — but the current policy already enumerates them in `_packages`, so the remaining leaks are narrow and fixable (findings 1–4).
3. The supplier-health estimate (`data.py:160-180`) fits hosted charges within €349 but is ~2x off near the sustainable-margin threshold (P03: observed €297/€337 vs predicted €628/€686). At ≤ 0.01 score it is a low-priority refinement, not a bug.
4. Two genuine code defects found, both minor: `valid_value` raises `AttributeError` on boolean values (`cost.py:41`), and `complete_offer`'s gap-fill defaults are not cost-minimizing (`cost.py:78`, `cost.py:252-253`). Neither affected hosted runs.

## Score mechanics in this area

- `score(c_open, c_deal, c_best) = clip((C_open − C_deal)/(C_open − C_best), 0, 1)` (`cost.py:212-217`), exactly the brief's formula including the ≤ €0.01-room edge case. Verified on 32 hosted sessions.
- `total_cost` (`cost.py:204-209`) sums `cost_breakdown`'s 11 components (`cost.py:167-201`): price (pre-effective-date units at `current_unit_price`, the rest at the new price), uncovered need + fallback risk, payment float at 8 %/360 on `spend`, freight on **committed** volume, late delivery beyond `need_lead_time_days`, quality (`ppm/1e6 × (cost_per_defect − min(chargeback, cost_per_defect))`), tooling, index clause (signed by the path) + index risk, and the supplier-health/relationship term.
- Who computes what:
  - **Decisions**: `decision_cost` (`cost.py:259-266`, adds the estimated relationship term via `case["_supplier_estimates"]`) gates acceptance (`play.py:264-277 acceptable_offer`), ask limits (`play.py:308-336`), model proposals (`routed.py:110-130`), and package enumeration (`routed.py:262-291 _packages`). `cheapest_price` (`cost.py:269-290`) sets the never-ask-below floor where supplier health binds.
  - **Local scoring**: `sim.py:354-367` derives `c_open`/`c_best` from scripted floors; `competition.py:162-194 feasible_reference` enumerates the best feasible deal with true floors and health; `hosted_like.py:103,211-213` uses the **recorded hosted `c_open`/`c_best`** — the local scale is the hosted scale.
  - **Audit**: `evaluation.py:108-138` recomputes components/scores from the fixture; `mismatches: []`.
- "Best possible": hosted `C_best` is the host's private number ("the cheaper of your alternative and the best package any supplier would accept with every valid lever applied", `api.md`). Nothing local computes it for unseen cases; tuning uses recorded values. Consequence: **every lever the host's `C_best` assumes that our deal omits is pure score lost** — the denominator already assumes we applied it.

## Findings (ranked)

### F1. Accepted deals keep supplier-authored non-price terms; `validate()` conforms price only — the enumerated levers never reach supplier-authored final offers
Evidence: `routed.py:117-127` (only `unit_price` is repriced to the ask limit/ceiling); `play.py:339-352 closing_move` accepts the cheapest standing **supplier** sheet that merely beats the alternative (`play.py:264-277` has no lever-optimality check). Hosted cost of this, recomputed with the unchanged `cost.py` (health excluded; ±second-order):

| Lever (deal re-priced at same price) | Deals affected | ΔC | Score gain |
|---|---|---:|---|
| `payment_days` → 90 (options `[14,30,45,60,90]`, ref 60) | 16/16 | −€1,309 … −€18,227 | +0.024 … +0.331 (P01 pay 14 → 1.000, P02 pay 60 → +0.331, P04 → 1.000) |
| `index_clause` → None (path +0.06, risk 1 %) | 7/16 | −€2,474 … −€32,550 | +0.026 … +0.206 |
| `contract_months` 12 → 24 (horizon 2) | 2/16 (P18 s=0.099, P08 s=0.231) | −€11,263 / −€21,654 | +0.155 / +0.123 |
| `effective_date` 2027-01-01 → 2027-04-01 (P01, P > current) | 1/2 P01 deals | −€14,353 | +0.184 |
| P11 `ppm` 300 + `chargeback` 500 (quality → 0) | vs ppm 800/cb 0: −€5,724; vs ppm 1500/cb 0: −€10,732 | | +0.060 / +0.113 |

These runs predate the current policy (which enumerates in `_packages`), but the leak paths remain: model-proposed sheets keep the model's non-price choice, `_limit_counter` (`routed.py:224-239`) builds its repair counter from the **supplier's** terms, and terminal supplier offers are accepted as authored. Note the P07 counter-evidence (a pay-90 deal scored 0.429 vs pay-60's 0.697): suppliers charge for payment days, so each lever must stay a priced trade — the fix is to make the *cost-minimal option the default* and require the model/script to justify departures, not to demand 90 unconditionally.

### F2. `effective_date` is a signed lever the code prices but only the scripted path chooses
`cost.py:104-110 _units_before` buys the lag fraction at `current_unit_price`. When the settled price is **above** current (price-increase cases like P01: 136.2 vs 126.53), the **latest** option is cheapest; below current, the earliest. Recomputed P01: choosing 2027-04-01 over 2027-01-01 at the settled terms is worth +0.24 (first deal) / +0.18 (second deal) of score. `_packages` enumerates the dates (`routed.py:262-291`), but `simple_opening_counter` (`routed.py:453-466`) and `_limit_counter` keep the supplier's date, and the model path only sees the one-field-at-a-time `TERM TRADEOFFS` table (`llm.py:203-213`).

### F3. The model prompt shows per-field deltas but never states the conclusion
`llm.py:211-213` prints `TERM TRADEOFFS (one field changed, other terms and price held fixed…)` — the exact € value of every lever — but leaves the synthesis to the model. A model under time pressure can still propose the P18-style clause with `deadband_pct: 0` (the worst of the three options: 2.7 % of indexed spend vs 0.45 % at deadband 5). If a clause must be traded for price, the **largest deadband** strictly dominates the smaller ones at every price (moved = max(0, |path| − deadband/100), `cost.py:184`); nothing in the prompt says so.

### F4. `complete_offer` gap-fill defaults are not cost-minimizing
`cost.py:78` fills a missing `payment_days` with `reference_payment_days` (60), not the cost-minimizing option (90 when offered); `cost.py:252-253` fills an explicit `None` with `options[0]`, which for `payment_days` is **14 — the worst choice** (costs `spend·0.08·46/360` ≈ +1.02 % of spend). Only reachable when a sheet explicitly carries `payment_days: None` (plan always supplies a value; `std` usually does), but it is exactly the kind of malformed-model-value case `valid_value` exists to guard.

### F5. Supplier-health estimate: good fit, threshold-sensitive
`data.py:160-180`: `full_cost = 0.98 × should-cost`, `interest_rate = 0.07`, DB standard terms. Against the 7 observed hosted charges: 5 within €200, P03 ~2x over (observed €297/€337 vs predicted €628/€686 — margin sits within ±0.4 pt of the 8 % threshold, where `worth` errors amplify). Max error €349 as documented. Where it bites: `cheapest_price`'s floor (`cost.py:282`, search bounds `0.5×–3× full_cost`) and `acceptable_offer`'s threshold. Score impact ≤ ~0.01 per case (P03's denominator is €337k). The accept-rule asymmetry (`play.py:272-276`): our cost includes the *estimated* health term, the alternative (`total_cost(case, other)`) includes none — consistent with the host (the audit adds observed health only to `c_deal`), and the recorded `c_alt` values match our computation exactly, so this is working as designed.

### F6. `valid_value` crashes on booleans
`cost.py:41`: `type(value) is not int and not value.is_integer()` — `True.is_integer()` doesn't exist → `AttributeError`, not `False`. All current callers catch `AttributeError` (`play.py:278`, `routed.py:134`), so it is latent, but `tests` or a future caller without the catch would turn one malformed field into a fallback decision.

### F7. `cheapest_price` degenerates outside relationship goals (latent foot-gun)
With a `full_cost` estimate but a non-relationship goal, `relationship_cost` returns 0, `decision_cost` is monotone in price, and the ternary search returns the low bound `0.5 × full_cost` (`cost.py:282-290`). Today `data.py` only builds estimates for `stabilise`/`secure_supply`, and `competition.py:_supplier` supplies `full_cost` for any goal — where the degenerate floor only ever *raises* an ask that was below half of full cost. Harmless now; wrong if someone reads "cheapest_price = the optimal ask" for leverage cases.

### F8. Local-only: `sim.py` assumes `negotiable[0]` is `unit_price`
`sim.py:356-362` builds `c_best` combos from `case["negotiable"][1:]`. If a case ever lists another field first, the zip misprices every candidate. Local simulators only; hosted cases all list `unit_price` first.

## Recommendations (prioritized)

1. **Make the cost-minimal non-price option the default everywhere a sheet is built or conformed.**
   - `complete_offer` (`cost.py:241-256`): when filling a gap, choose the legal option minimizing `total_cost` at the current price (payment 90, `index_clause` None on positive paths, months covering the horizon, lowest ppm, chargeback ≥ `cost_per_defect` when free), not `options[0]`/`reference_payment_days`.
   - `routed.py:117-127 validate()`: after price conformance, if a model sheet's non-price field choice is strictly cost-dominated at the *same* price by another legal option **and** the field was not part of a deliberate trade (heuristic: the model's chosen value equals the supplier's standing value), snap it. Cheapest version: apply it only in the fallback/repair paths (`_limit_counter`, `routed.py:224-239`) where no model intent exists.
   - Expected impact: removes the F1 leak paths; on the hosted distribution worth up to **+0.05–0.10 mean score** if even half the lever value survives supplier re-pricing; zero downside on the scripted path, which already enumerates.
2. **Add one prompt line with the conclusion of the tradeoff table** (`llm.py:203-213`): e.g. `"Cheapest option per field at the current price: payment_days=90 (−€6,990), index_clause=None (−€32,430), contract_months=24 (−€21,654); take these unless you are deliberately trading them for price; if a clause is required, prefer the largest deadband."` Expected impact: +0.02–0.05 on model-routed decisions (the P18/P08 clause deals were model-era mistakes worth 0.03–0.21 each); no code risk.
3. **Teach the `effective_date` sign rule explicitly** (prompt + `_limit_counter`): if the target price exceeds `current_unit_price`, ask for the latest option; else the earliest. Evidence ceiling +0.18–0.24 per price-increase case (P01-type = 1 of 10 practice cases; raw-material waves of the final likely repeat the pattern).
4. **Freeze the arithmetic with a guard test**: assert the shared functions of `src/tacto_negotiator/cost.py` stay identical to `materials/starter_kit/starter-kit/arena_kit/cost.py` (a byte-diff of the function bodies, or the existing `test_evaluation.py` audit against the fixture — keep both). This makes any future "improvement" to the formula an intentional, reviewed act. Impact: protects the 0.64+ baseline from silent desync.
5. **Fix `valid_value` booleans** (`cost.py:41`): `if type(value) is not int and not (isinstance(value, float) and value.is_integer()): return False` — booleans are not valid terms. Zero score impact directly; removes a latent `AttributeError` from the decision gate.
6. **Narrow the health-estimate threshold error (optional)**: for stabilise cases, blend the predicted margin toward the threshold (or clamp the predicted relationship cost to the observed range when the predicted margin is within ±1 pt of `sustainable_margin_pct`) before using it in `cheapest_price`/`acceptable_offer`. Expected impact ≤ +0.01; do it only if (1)–(3) are done.

## Risks & trade-offs

- **Every lever is a trade.** Hosted P07 shows a pay-90 deal scoring 0.429 vs pay-60's 0.697 — suppliers price payment days (the local model charges ~1 % of price per 30 days ≈ 12 %/yr vs our 8 %/yr gain). Recommendation 1 must keep the *enumeration under the cost ceiling* (`_packages`) as the mechanism; the defaults only apply where no trade is being made (gap fills, repair counters, model omissions).
- **Conforming model sheets can destroy deliberate trades** (a clause exchanged for a lower base price is sometimes right — `hosted_like.premium` gives −2 % price for accepting a clause). Gate the snap on "value equals the supplier's standing value" or restrict it to fallback paths.
- **Do not touch the published formula.** The exact-match audit (310 components, 32 scores, 10 `c_alt`) is the project's single strongest asset; a "cleaner" rounding or sign change would silently move every accept/walk threshold.
- Rejecting a supplier's final offer to chase levers risks the thread (hosted suppliers walked after refused finals — `hosted_like.py:178-180` models this). Lever asks belong in the bargaining rounds, never as a response to `final_offer`.
- The quantified lever table excludes the health term's second-order response (pay-90 slightly lowers supplier `worth` → slightly raises the relationship charge in stabilise cases); effects are €10² on €10⁵ denominators.

## Open questions

1. Does the host's `C_best` really assume every lever at its best value (payment 90, index None when the path is positive, horizon-covering months)? If some finals' `C_best` is computed at the supplier's *own* standard non-price terms, the levers are worth less than the table suggests. The recorded `c_best` values in the fixture cannot distinguish this; a sandbox case where a lever is the only difference would.
2. `_units_before` (`cost.py:110`) accrues pre-effective-date units at the **committed** volume rate `V`, not the need rate `N`. All audited cases had `V = N` or fixed volume, so the audit cannot discriminate. If a hidden final case negotiates `volume_per_year ≠ need`, our pricing (and the kit's — they are identical) could diverge from the host's intent. Worth a sandbox probe if a volume-negotiable case appears.
3. What do hosted suppliers actually charge for payment days? The local `premium` (1 % of price per 30 days) may be miscalibrated in either direction; two hosted P02 pay-90 deals scored *worse* than pay-60 twins, but confounded by price. A targeted sandbox sweep (same price, pay 30 vs 90) would calibrate the only lever with a guaranteed € value formula.
4. Is the `0.98 ×` should-cost factor and the flat 7 % interest rate stable across unseen final suppliers (the brief hints cash-short suppliers value early payment more, `usual_rate` exists for that)? `supplier_value` supports `usual_rate`/`ppm`/`extra` overrides that `data.py` never fills.
