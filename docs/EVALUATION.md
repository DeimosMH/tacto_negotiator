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


## Hosted runs on 25 October (Opus high effort, 10 cases each)

| Run | Mean | Model time per case | Change |
|---|---|---|---|
| [v13](results/live-practice-2026-10-24-opus-high-v13.json) | 0.781 | — | levers and prose guards |
| [v16](results/live-practice-2026-10-25-opus-high-v16.json) | 0.752 | 37.1 s | held sheet after an imposed index clause, last-rise claim, hedge rule |
| [v17](results/live-practice-2026-10-25-opus-high-fast-v17.json) | **0.789** | **15.8 s** | Opus fast mode; index-clause sheet on the longest payment term |

One 10-case run varies by about ±0.02, so v13, v16 and v17 are close; fast mode's measured effect is the time. After v17, the shared gate stopped offering one RFQ bidder a sheet dearer than another bidder's standing acceptable offer (hosted P05 v17: 0.699, about 0.77 with the cheaper offer). Every session is readable in the [deal journal](DEALS.md).

## The out-of-sample bench (25 October)

The final plays each case once, on cases that never appeared in practice. So the question that matters is how the agent does on cases it was never tuned on. Hosted practice means cannot answer it: every hosted score comes from the same 10 cases the policy was tuned against.

`tacto-compete --bench oos` plays the 9 practice cases that were never hosted (P06, P09, P12–P17, P19) against the local supplier model. Their case files are saved in `tests/fixtures/practice_cases.json`. What a session would reveal is synthesized from the data package, by the rules the hosted cases show:

- **Opening price:** a price letter's own asked price (P01 and P02 match exactly); otherwise today's price times the hosted opening ratio of the case type.
- **Standard terms:** payment and Incoterm from the supplier master, lead time from its standard lead time, the ppm commitment from its scorecard.
- **Opening terms:** the supplier's own, as hosted openings show: the shortest contract, the earliest effective date, no index clause, a capacity supplier's reduced volume.
- **C_open and C_best:** the cheapest eligible opening, and every valid package at each supplier's floor.

`--bench synth` rebuilds the 10 hosted cases the same way. Its gaps between C_open and C_best track the recorded ones on 9 of the 10 cases, and its scores rank policies like the recorded replica (`--bench replica`). `--timing final` uses 25–60 s supplier replies instead of 15–40 s. `--strategies routed --live` plays the final's Claude Opus setup against these local suppliers; it reads only `LLM_*` settings, never an arena key.

| Mean score | Scripts (45 distinct runs: 9 cases × 5 seeds; waves repeat) | Claude Opus, 45 runs |
|---|---|---|
| v1.0.1 on the never-hosted cases | 0.680 (4 zeros) | 0.667 |
| After the bench fixes | 0.796 (0 zeros) | 0.782 |
| Hosted replica (recorded openings) | 0.830 → 0.830 | |

The fixes are general, not case-specific:
- A fallback no longer walks away while any thread has rounds left.
- No opening sheet asks more than 5 points below the anchor.
- No ask goes above the cheapest opening in the case.
- Supplier-health estimates keep the case's price unit.
- News that a supplier needs work becomes a checked argument.

**Limits:** the bench supplier is a fitted model, and repeated waves or hosted runs of a deterministic supplier are one sample, not replications (review round 3, L5/L10). The bug fixes transfer; gains that come from the supplier model's own valuations (clause, lead time, supplier health) are replica-only evidence. Its lead-time and index-clause valuations are extrapolated from one or two hosted cases, so treat gains that come only from those trades with caution.
