# How the arena evaluates, and what we reproduce locally

This guide separates the parts of hosted evaluation we can reproduce exactly from the parts we can only approximate. It uses 32 completed winsortf practice replays from 24 October 2026, stored in [tests/fixtures/practice_feedback.json](../tests/fixtures/practice_feedback.json).

## What is evaluated, and why

| What | How | Why it matters |
|---|---|---|
| Score | `score = (C_open − C_eval) / (C_open − C_best)`, clipped to 0–1. C_eval is the accepted deal's total cost, or the declared alternative's cost if there is no deal or a must-have is missed. | Only the accepted term sheet counts. Prose, claims and tone matter only through the deal they lead to. |
| Total cost C | Published case rates price the price, payment, lead time, quality, contract and freight terms over the fixed need and horizon. | The best unit price is not always the cheapest deal. |
| Supplier health | An extra `relationship` cost component, seen in 7 of 32 replays (€113–€2,889). | It comes from the supplier's private state, so it can only be observed afterwards. |
| Claims | Up to 3 checks per message: `verified`, `contradicted`, `irrelevant` or `duplicate`. | A contradicted claim costs trust. A verified claim allows a concession but does not force one. |
| Supplier decisions | A private language model limited by the supplier's cost floor and approval rules. | Not reproducible. Identical messages can get different replies. |

## Exact local reproduction

`python -m tacto_negotiator.evaluation --feedback tests/fixtures/practice_feedback.json --output docs/reviews/hosted-evaluation-audit.json` recomputes, from the public case data:

- 310 cost components and all 32 scores, using [cost.py](../src/tacto_negotiator/cost.py)
- 38 claim verdicts, using `ClaimChecker` in [evaluation.py](../src/tacto_negotiator/evaluation.py)

The [current audit](reviews/hosted-evaluation-audit.json) has **no mismatches**. `tests/test_evaluation.py` keeps it that way.

### An undocumented rule we found

The brief says each ID counts once per case. The hosted arena goes further for forecasts: **every forecast (`fc:`) claim after the first is `duplicate`, whatever the part or month.** In P07, forecasts for three different parts got `verified, duplicate, duplicate`. In P02, the same part's June forecast was `duplicate` after September's.

The scripted buyer used to send up to three forecasts per message. Two of the three claim slots were wasted. It now sends one forecast per case, choosing the part with the largest volume increase. The model prompt says so once a forecast has been cited, and the local simulator follows the same rule.

Other observations:

- Rival quotes cited in single-supplier cases were always `irrelevant` (P03, P04, P08, P11 and P18). Only quotes listed in the case, or other bidders' offers in an RFQ, count.
- Only `verified` claims are used up. Citing the same quote again after an `irrelevant` verdict gave `irrelevant` again, not `duplicate`.

## Not reproducible

- The host's interpretation of free text into claims, which the audit takes from the replay
- Supplier concessions, patience and private cost floors
- The C_open and C_best of unseen final cases

Local competitions therefore use scripted suppliers for robustness, timing and validity. Only hosted practice measures negotiation skill.

## Astra at max effort

| Run | Mean score | Note |
|---|---:|---|
| Hosted, Sol 6.1 low | 0.500 | [Baseline, 10 cases](results/live-practice-2026-10-24.json) |
| Hosted, Astra low | 0.543 | [10 cases](results/live-practice-2026-10-24-astra.json) |
| Hosted, Astra max | 0.113 | [All 9 model calls hit the 25 s deadline](results/live-practice-2026-10-24-astra-max.json); 8 of 10 cases scored 0 |
| Local replay, Astra max, 240 s | — | [2 decisions completed](results/astra-max-replay.json): 53 s and 96 s. P01's proposal cost the same as Sol low's. |

The replay stopped when the OpenAI account ran out of credits (HTTP 429 `credit_balance_exhausted`); the other calls in that report are those errors. Even so, the result is clear. Astra at max effort takes 2–4× the 25 s decision budget, and longer than the arena's 60 s per-message limit, without a better proposal. **Do not use max effort in competition.** Low effort remains the setting to qualify, as described in [MODEL_SELECTION.md](MODEL_SELECTION.md).

## Claude Opus 5.5

`LLM_PROVIDER=anthropic` routes model decisions to `claude-opus-5-5` with the key in `LLM_API_KEY_ANTHROPIC`. Hosted practice on 24 October, 10 cases each:

| Run | Mean | Deals | Model calls | Note |
|---|---:|---:|---|---|
| [Opus low](results/live-practice-2026-10-24-opus-low.json) | 0.418 | 9 | 18 completed, 8.1 s mean | P05 lost an agreed deal (unread quote), P01 accepted the opening after a rejected reply |
| [Opus high](results/live-practice-2026-10-24-opus-high.json) | 0.429 | 9 | 15 completed, 2 timeouts, 14.9 s mean | P10 walked away from an acceptable final offer (unread quote) |
| [Opus max](results/live-practice-2026-10-24-opus-max.json) | 0.114 | 9 | all 9 timed out at 25 s | Same failure as Astra max |
| [Opus high, with fixes](results/live-practice-2026-10-24-opus-high-fixed.json) | **0.502** | **10** | 12 completed, 17.1 s mean | No zero scores; P10 0.671 is a new team best |

The [local replay](results/opus-replay.json) agrees: max effort took 230 s on average and timed out on 8 of 10 decisions even with 240 s, while high stayed under 25 s.

The fixes between the high runs:

- Quotes skipped because the read deadline had passed are now read once before deciding.
- A model failure no longer accepts an untouched opening; the buyer sends a scripted counter while the thread's own deadline allows.

**Use Opus at high effort.** Runs use different policy commits and suppliers vary between attempts, so differences of a few hundredths are noise.

