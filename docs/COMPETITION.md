# Local competition: scripts and real models

Explore these saved reports in the [local dashboard](WEB_UI.md): strategy charts, match filters, public quote snapshots and a decision-by-decision explanation. Start it with `.venv/bin/tacto-ui` and open http://localhost:1234. New snapshots are recorded for future runs; historical reports retain their original bytes.

The Tacto Negotiator competition harness runs independent buyer strategies against the same synthetic supplier scenarios. Suppliers and arena state stay in memory. The optional model strategies make real, measured OpenAI requests. Its current entry points are tacto-compete and python -m tacto_negotiator.compete. Start with [the tutorial](TUTORIAL.md) for the negotiation loop and [the routing guide](ROUTING.md) for the decision tree.

**Review update:** the tables below preserve the historical pre-review experiments. Scoring version 2 fixes eligible-opening and nonexistent-alternative errors, and adds a valid second bidder to leadtime_repair. Historical economic scores have not been recomputed and must not be presented as current model rankings. The new [900-match offline validation](reviews/FIXES.md) checks the repaired implementation without paid calls; all original result files remain unchanged.

## Results and recommendation

The expanded scripts solve eight of the ten scenario families without a model or fallback. Keep the hierarchical design: deterministic quantitative work, model interpretation for the remaining semantic cases, and one shared validator on every path. This is supported within the tested families; it is not a guarantee about unseen final cases.

The offline comparison contains 2,000 matches: four policies, ten scenarios, five seeds and five repeated waves for both development and fresh holdout. The final policy was frozen before holdout seeds 201–205 were run. Each cell below summarizes 250 holdout matches per policy.

| Policy | Valid outcomes | Fully scripted cases without fallback | Mean synthetic score /100 | Mean regret EUR among valid outcomes | Decision p95 |
| --- | --- | --- | --- | --- | --- |
| Previous router, model disabled | 250/250 | 18% | 20.64 | 5,249.15 | 1.1 ms |
| Expanded scripts, model disabled | 250/250 | 80% | 58.23 | 2,179.75 | 7.2 ms |
| Naive price baseline | 240/250 | Not a routing-coverage measure | 57.76 | 2,893.85 | 0.07 ms |
| Immediate guarded closure | 250/250 | Not a routing-coverage measure | 10.00 | 7,289.52 | 0.37 ms |

Script coverage means an entire valid match with no completion attempt and no fallback. Merely closing after a disabled model raises an error does not count. For the expanded policy, all 50 offline fallbacks are the deliberately unsupported contradiction and relationship cases. The holdout has 50 distinct scenario/seed combinations repeated five times per policy; it does not have 250 independent businesses.

The scripts improve must-have repair, plateau handling and alternative selection. They do not win every family: on development payment/freight cases their mean score is 62.29 versus naive's 76.49, and on holdout slow replies it is 66.49 versus 72.59. Do not remove safeguards or semantic routing to chase the simulator's score. Both rules and tests should address real causes.

Raw reports: [development](results/offline.json), [fresh holdout](results/holdout.json), [diagnostic API pilot](results/pilot.json), [measured model tournament](results/models.json). The pilot exposed non-neutral case labels and a supplier quote-floor bug; both were fixed before the scored run. Its economic scores are excluded. Its 26 actual calls and USD 0.198582125 conservative usage estimate remain in campaign accounting.

**Historical provenance:** these JSON files are preserved byte-for-byte from the completed experiment before the project migration. Their source paths, hashes and commit IDs identify the then-measured files under starter_kit/starter-kit/arena_kit. Current source lives under src/tacto_negotiator, tests under tests, and the active dataset under data. Map old arena_kit imports to tacto_negotiator when following current commands. The original supplied starter kit was separately restored to commit 0166e6948f53ecbf629ab371135f69ec3243f998 and archived in materials/starter_kit; it does not contain the later measured buyer. Migration does not regenerate these reports or rerun paid calls.

### Real model comparison

The model tournament uses the same frozen policy and corrected simulator, fresh seeds 201 and 202, all ten families, five repeated waves and ten concurrent workers. Every strategy receives 100 matches, for 700 total. It ran on 2026-10-24 from 14:34:00 to 14:46:39 UTC. Together with the offline reports, this is 2,700 scored matches. The ten diagnostic pilot matches are additional and excluded from scoring comparisons.

| Strategy | Valid outcomes | Mean synthetic score /100 | Real model calls | Fallback decisions | Policy decision p95 |
| --- | --- | --- | --- | --- | --- |
| Hybrid scripts + models | 100/100 | 73.07 | 35 | 0 | 4.30 s |
| Sol on every decision | 100/100 | 67.00 | 498 | 10 | 5.51 s |
| Astra on every decision | 100/100 | 62.70 | 479 | 10 | 5.07 s |
| Luna on every decision | 100/100 | 57.18 | 661 | 17 | 6.31 s |
| Expanded scripts, model disabled | 100/100 | 56.92 | 0 | 20 | 2.2 ms |
| Naive price baseline | 90/100 | 50.97 | 0 | 0 | 0.08 ms |
| Immediate guarded closure | 100/100 | 10.00 | 0 | 0 | 0.37 ms |

The hybrid made **93.0% fewer model calls than Sol-only**, with the best mean score and lowest mean regret in this comparison: EUR 1,663.18 per match. It completed 80 matches without any model call and used 35 Astra calls for the remaining 20 semantic cases. Its Sol branch was configured but not selected: these scenario families classify all unscripted work as complex. The experiment therefore supports the overall tree and the tested strong path; it does not qualify the fast branch on genuinely medium-complexity prose.

Every variant finished before its private wave cap. Supplier-thread timeouts still occurred: 120 for scripts and hybrid, 60 each for Sol, Astra and naive, and 56 for Luna. These totals are repeated timeout events, not distinct failed matches. Fast buyer decisions do not solve sequential RFQ scheduling.

All ten Sol and ten Astra fallbacks occurred in slow_reply with ValueError after completed provider responses; that reason code does not distinguish missing JSON from an action rejected by validation. Luna had 14 parse/action fallbacks plus two HTTP failures and one deadline timeout. Valid outcomes include deliberate walk-aways and safe fallback, so 100/100 validity does not mean every model decision was correct. Pure model policies scored zero on the lead-time repair family for Sol/Astra and poorly on price_plateau. The hybrid still trails models on simple_price and competing_rfq. These weaknesses remain visible in the full report rather than being averaged away.

The following latency table measures actual completion calls, including failures. Astra includes both its model-only matches and the hybrid's strong calls. The usage ledger cannot assign exact spending to individual strategies because it records the requested model rather than the caller's strategy.

| Provider model | Requests | Completed responses | Call p50 | Call p95 | Conservative usage plus unknown reservations |
| --- | --- | --- | --- | --- | --- |
| GPT-6.1 Sol | 498 | 498 | 3.91 s | 5.51 s | USD 2.530685 |
| GPT-6 Astra | 514 | 514 | 3.80 s | 5.07 s | USD 13.513413 |
| GPT-6 Luna | 661 | 658 | 4.20 s | 6.31 s | USD 0.244021 |

The scored run made **1,673 real calls**. Including the pilot, the campaign made **1,699 calls**, with a **USD 16.486701 conservative usage-plus-reservation total** against its USD 60 ceiling. The scored run has USD 0.008816125 in unreconciled reservations for its three failed Luna requests. There were no budget rejections. This is not an invoice; cached input may make actual charges lower, and unknown requests retain their full estimated allowance.

**Recommended configuration: scripts first, Sol low for the ordinary model tier, Astra low for qualified hard decisions.** Keep the common guard and fallback. The hybrid wins this synthetic comparison, and Sol is the strongest model-only baseline here. Astra was slightly faster at p95 in this run, so the labels fast and strong are routing roles, not measured speed guarantees. Luna was inexpensive but slower here, with more calls and fallbacks; it is not the preferred live buyer from these results. No Claude or GLM model was measured.

For the tested effort setting, explicitly set LLM_STRONG_EFFORT=low when enabling LLM_STRONG_MODEL=gpt-6-astra. The adapter's unconfigured strong-effort default is medium, which this comparison did not measure. Larger models should be enabled for a demonstrated task benefit, not merely for unfamiliar wording. Keep this recommendation provisional until the medium-semantic branch, evidence quality, RFQ scheduling and real shared-wave capacity are qualified.

## How it works

```text
Seed + scenario definition
  |
  +-- Public case, quotes, defaults ----> fresh buyer policy
  |                                         |
  |                                  validated decision
  |                                         |
  +-- Private supplier profile <----- local supplier response
  |                                         |
  +-- Private feasible reference ----> outcome evaluator
                                            |
                                 sanitized JSON report
```

The buyer sees public case data and parsed quote attachments. It cannot see reservation prices, future concessions, supplier cost assumptions or the evaluator's reference. Public case IDs and titles are neutral. Scenario labels remain in the report so a human can identify which behavior was tested.

| Block | File | What to change here |
| --- | --- | --- |
| Scenario and supplier behavior | [competition.py](../src/tacto_negotiator/competition.py) | Public cases, private economics, concession rules, delays, evaluation and parallel matches. |
| Strategy selection and command | [compete.py](../src/tacto_negotiator/compete.py) | Fresh policy factories, explicit live opt-in, model configuration, progress checkpoints and report writing. |
| Spend accounting | [benchmark.py](../src/tacto_negotiator/benchmark.py) | Literal key extraction, model-access preflight, thread-safe reservations and sanitized usage records. |
| Deterministic policy | [routed.py](../src/tacto_negotiator/agents/routed.py) | Supported situations, complete package enumeration, full-cost concessions, validation and fallback. |
| Model transport | [completion.py](../src/tacto_negotiator/agents/completion.py) | Absolute deadlines, shared concurrency slots, Responses requests and usage-only observation. |

These are logical code blocks implemented by separate contributors. The deployed buyer does not need a coordinator model to route each decision.

## Scenarios

| Report label | Situation | Behavior to test |
| --- | --- | --- |
| simple_price | One supplier and a price-only negotiation | Immediate scripted anchor, bounded concessions and timely closure. |
| payment_freight | Several payment and delivery options | Compare complete costs and compensate for freight/payment in each package's price. |
| leadtime_repair | Supplier A violates a must-have; supplier B has an eligible opening | Repair A explicitly or choose a valid bidder. B provides the score baseline in version 2. |
| competing_rfq | Three suppliers with different economics | Compare complete offers and give untried suppliers a turn. |
| cheaper_alternative | An eligible outside quote is cheaper | Cap proposals against the alternative and walk away when appropriate. |
| price_plateau | Supplier stops moving on price | Detect repeated complete quotes and avoid spending rounds indefinitely. |
| incomplete_defaults | Payment terms are omitted from the initial quote | Ask once, read the replacement quote, then negotiate without inventing defaults. |
| contradicted_claims | Supplier flags disputed evidence | Keep the semantic model route visible; a price-only script is insufficient qualification. |
| relationship_risk | Supply continuity carries an additional cost | Preserve the model route and expose the missing supplier-health estimate. |
| slow_reply | Three RFQ suppliers consume 55 or 60 virtual seconds per reply | Detect neglected supplier threads and pressure on closure. |

Seeded variation changes starting prices, private reservation prices and concession rates. The holdout also changes payment premiums and supplier delay. These are fresh numerical cases from the same scenario families; they do not establish generalization to arbitrary unseen negotiation language.

## Strategies and fair comparison

| Strategy | Behavior |
| --- | --- |
| naive | Existing price anchor/split baseline. Its acceptance ignores some safeguards, so invalid outcomes count against it. |
| close | Immediately choose a guarded standing offer or the outside option. Shows the cost of refusing to bargain. |
| previous_scripts | Router from commit 5c0fcd1 with model access disabled. Used only for the before/after offline report. |
| scripts | Expanded router with model access disabled. Unsupported semantic cases produce a recorded fallback; they are not counted as script coverage. |
| sol / astra / luna | Ask the named real model on every non-deadline decision, retaining the same action validator and closing fallback. |
| hybrid | Expanded scripts first, Sol for ordinary interpretation, Astra for qualified complexity with sufficient time. |

All live models use low reasoning effort, a 4,096-token output cap, standard service tier and the same 25-second total decision ceiling. A short remaining supplier window can reduce the actual allowance below 25 seconds. A shared semaphore bounds concurrent calls. The comparison includes complete-response latency, queue time, invalid outputs and fallbacks. A successful HTTP response can still produce a rejected decision; report those separately. Recorded HTTP failures expose the exception class, not the server response body or status code, so this run cannot distinguish rate-limit responses from other HTTP errors.

Every policy gets an independent simulation with the same scenario and seed. Worker threads run matches in parallel. Five sequential wave batches each give a match a 480-second virtual window. Supplier delay advances its private clock without sleeping, while actual model decision time advances that clock by the measured elapsed time.

This is a paired policy comparison. A queued match starts its own clock when its worker begins: worker queue delay is excluded. Therefore the experiment does not establish how many cases the application can finish inside one shared eight-minute final wave. Repeated waves reuse the same seeded economics; they are repetitions, not five independent datasets. The separate [final rehearsal tests](FINAL_READINESS.md) exercise the real runner and shared wave timing.

The reported score uses the public cost function with an enumerated feasible reference and the best eligible outside option. Relationship cases use an evaluator-only supplier-cost assumption and a finite price grid. The reference is not a proof of the global optimum. Invalid accepted deals or execution errors count as invalid; a deliberate walk-away can be valid. Regret is the realized cost minus the reference cost, in euros. Compare both cost and score: averaging normalized scores can hide large absolute losses.

Current reports identify config.scoring_version=2. C_open is captured from the cheapest opening that satisfies all must-haves, using its own supplier defaults. A scenario with no eligible opening is rejected with an explicit error rather than assigned an invented baseline. A fixture with no feasible deal or declared alternative is also rejected. Reference enumeration includes legal supplier-standard values outside term_options.

Only a declared buyer.alternative enters the outside-option calculation. The fallback_unit_price rate prices uncovered demand within a deal; it does not create an attainable outside quote. Walking away with alternative.kind=none scores zero and records null cost_eur and regret_eur. Mean regret excludes these unknown outcomes, so always read validity, deals and scores alongside that conditional average. With a declared eligible alternative, the walk-away cost and score use that alternative.

## Why the expanded scripts matter

The scripted path enumerates at most 256 legal non-price combinations across live suppliers and returns up to three validated complete packages. It uses known quote terms, public choices and the buyer's cost function. Opening and follow-up concessions are policy choices; the code does not know a supplier's private minimum.

Alternative packages should preserve buyer economics. For 1,000 units, switching from supplier-paid freight to a term that costs the buyer another EUR 2 per unit adds EUR 2,000. Offering both packages at the same price gives away EUR 2,000 if the supplier chooses the latter. The expanded package builder adjusts each package's price against a common full-cost ceiling, including payment and freight effects.

Do not expand the deterministic route merely to improve its coverage percentage. Contradictory claims, relationship goals, unresolved defaults, unbounded choices and recognized conditional wording still need interpretation. Conditional phrase matching is a conservative heuristic; it cannot prove arbitrary prose contains no hidden constraints. Final/deadline closure is deterministic even when earlier interpretation was unsuccessful, so it must remain distinct from a successfully solved scripted case.

## Run it locally

Install the root project using [the README](../README.md), then run a complete offline comparison from the repository root:

```sh
TACTO_ROOT="$PWD"
TACTO_PYTHON="$TACTO_ROOT/.venv/bin/python"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_PYTHON" -m tacto_negotiator.compete \
  --strategies naive close scripts --seeds 0 1 2 3 4 \
  --waves 5 --workers 8 --output /tmp/tacto-offline.json
```

No environment file is read by this command. To inspect only one behavior, add --scenarios payment_freight. Use --holdout with a fresh list of seeds for validation. Reports contain aggregate results and a matches array; each match records route reasons, fallback status and decision/supplier/wave timing separately.

The installed .venv/bin/tacto-compete command accepts the same arguments as the module invocation shown above. Keep new experiments in a separate output file; the checked-in results are the historical measurement record.

Real model comparison is explicitly opt-in. The following example starts a separate paid run with a USD 5 ceiling:

```sh
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_PYTHON" -m tacto_negotiator.compete \
  --live --key-file "$TACTO_ROOT/.env" --budget-usd 5 \
  --strategies scripts sol astra luna hybrid \
  --scenarios payment_freight contradicted_claims relationship_risk \
  --seeds 301 --waves 1 --workers 4 --output /tmp/tacto-models.json
```

The parser extracts only the literal LLM_API_KEY assignment. It does not source the file, expand shell expressions, load Arena credentials or honor another endpoint from that file. Model listings and Responses requests go to the fixed OpenAI URL; suppliers remain local. Reports omit keys, authorization headers, raw response text, prompts and prose bodies.

Budget reservations happen before dispatch. Missing usage, timeout or cancellation retains the full reservation because remote work may still be billed. Known usage is conservatively priced with the default 30-minute cache-write input rate and standard output rate; reasoning tokens are already included in output tokens. No cache discount is assumed. These are estimates, not a provider invoice. See [OpenAI pricing](https://developers.openai.com/api/docs/pricing) and [prompt-cache accounting](https://developers.openai.com/api/docs/guides/prompt-caching).

The cap applies to one process. For a multi-run campaign, subtract every previous run's committed_usd, including unreconciled reservations, before choosing the next cap. A progress checkpoint is written every 15 seconds and a final ledger is written on ordinary interruption. An abrupt process kill can lose the latest interval; reconcile provider usage before resuming such a run.

## Modify one scenario or script step by step

1. Find the closest scenario in build_scenarios. Keep public facts in case and hidden supplier behavior in profiles. Use a neutral public ID/title and keep the descriptive label only in evaluation data.
2. Add a focused test in test_competition.py. Verify the offered reference can actually be accepted, every quote respects its terms' reservation price, private data never reaches the policy, and timings use the appropriate clock.
3. Reproduce the behavior with --scenarios and a small fixed seed set. Inspect the public structured proposals, final terms, route reasons and cost differences before changing the policy.
4. Add a fixture to test_script_coverage.py for the desired deterministic rule and a near miss that must still use a model. A semantic dispute should not disappear just because price arithmetic is easy.
5. Change the shared rule in routed.py. Keep its output on the same validator as model output, and preserve the candidate limit and remaining-round/deadline reserve.
6. Compare against naive, close and the previous script version using the same development seeds. Count error-free valid outcomes, full-case script coverage, fallback rate and regret; do not select only favorable scores.
7. Freeze the policy and run fresh holdout seeds. If those results cause another policy change, reserve a new holdout. Do not call repeated trials on the same seed independent evidence.
8. Run the focused checks below, then the full suite described in [local testing](LOCAL_TESTING.md). Rebuild the atlas after source or report changes.

```sh
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_PYTHON" -m pytest -q -p no:cacheprovider \
  "$TACTO_ROOT/tests/test_script_coverage.py" \
  "$TACTO_ROOT/tests/test_competition.py" \
  "$TACTO_ROOT/tests/test_benchmark.py" \
  "$TACTO_ROOT/tests/test_compete.py"
```

## What matters most for Sunday

The first priority is valid closure within the real thread/wave budget. Next comes serving all RFQ threads while suppliers are replying: sequential sends can still lose other threads even when buyer decisions take milliseconds. The slow-reply fixtures expose this risk. Concurrent RFQ scheduling remains a separate implementation block.

Continue expanding scripts for structured, auditable quantitative cases. Use model calls for interpretation that changes the decision, and prepare evidence before opening time-sensitive sessions. Supplier-health estimates and arithmetic grounding for prose claims remain gaps. A larger model alone does not fill them, and the fixed contradiction feedback in this simulator does not evaluate persuasion or evidence repair.

These simulations do not contact the hosted arena, measure venue networking or recreate every team's load on a shared supplier service. Cross-provider candidates without configured credentials were not benchmarked. Use the results to choose what to qualify next, then run a real-time rehearsal under the expected operational conditions.
