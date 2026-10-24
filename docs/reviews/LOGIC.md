What this change does: Tacto Negotiator chooses complete procurement packages, routes unresolved decisions to a model, and validates the result before the runner sends it. This review covers the active project at commit `4aca02d`, including its economic helpers, policy callers, tests, competition evaluator and original API brief; it is not limited to the latest archive commit.

The expected deployment is five 480-second waves with up to ten concurrent cases and 60-second buyer-response windows per supplier thread. The findings below concern local benchmark correctness, not measured hosted results. The review used the [ponytail-review skill](/home/deimos/.codex/skills/ponytail-review/SKILL.md).

**Must fix — before treating these boundary cases as comparable arena scores:**

1. **An opening that breaks a must-have can erase a valid saving** (`src/tacto_negotiator/competition.py:246–258`). **Severity: P2. Confidence: high; reproduced locally.**
   - **What this is:** `CompetitionSim._end()` sets the opening cost used to normalize a negotiation score, then `_match()` and `run_competition()` publish that score (`competition.py:403–410, 469–475`). The API defines this baseline as the cheapest opening that satisfies the must-haves ([API:423](../../materials/starter_kit/brief/api.md#cost-and-score)).
   - **Problem:** The calculation includes every opening without checking eligibility; the reproduction gives cheaper supplier A a 45-day lead time against a 14-day requirement and supplier B a valid 14-day lead time. B improves from **€45,247.85 to €44,843.76**, but the evaluator uses A's invalid **€44,439.68** baseline and reports **0** instead of **0.0325469** against the same feasible reference.
   - **Fix:** Filter the opening candidates through `must_haves_ok()` using each supplier's own standard terms before selecting the minimum. Handle an empty eligible set explicitly: make the fixture supply an eligible baseline, or mark its score as using a separate synthetic convention; do not silently use an invalid offer or call `min()` on an empty list.
   - **If we skip it:** A policy that buys a valid improvement can be scored as making no improvement, distorting policy comparisons when this mixed-eligibility RFQ is added. The current `leadtime_repair` family already starts with **no** eligible opening, so its normalization needs an explicit convention before claiming parity with the published score.

   Reproduce with `ineligible_opening()` in [repro_logic.py](repro_logic.py). It changes the existing seed-0 `competing_rfq` fixture to two suppliers, allows 45/14-day delivery, requires at most 14 days, sets lateness cost to zero to isolate eligibility, and uses a €30 private floor so B accepts the tested package. The existing tests check that invalid *accepted deals* score zero (`tests/test_competition.py:185–195`), but do not test eligibility of the opening baseline, so passing the baseline suite does not refute this case.

   Historical scope: the mixed-eligibility RFQ is a new diagnostic fixture. Scanning development seeds 0–4 and holdout seeds 201–205 found that only `leadtime_repair` lacks an eligible opening; all ten instances have that condition. This review does not recompute or claim a reranking of the archived model tournament.

2. **Walking away with no alternative can earn full marks** (`src/tacto_negotiator/competition.py:134–150, 249–258`). **Severity: P2. Confidence: high; reproduced locally.**
   - **What this is:** `_outside_cost()` supplies both `feasible_reference()` and the no-deal result with an alleged outside-option cost. The API distinguishes the rate for uncovered demand from `buyer.alternative`, and explicitly assigns score zero to no deal when the alternative is `none` ([API:328–339, 423–426](../../materials/starter_kit/brief/api.md#cost-and-score)).
   - **Problem:** Starting with the seed-0 `simple_price` fixture, change only `fallback_unit_price` to **€20**, keep `alternative.kind="none"`, and walk away immediately. The evaluator returns **`deal=false`, `c_alt=null`, `c_best=20600`, `score=1.0`** because it invents a purchasable outside option from the uncovered-demand rate; the required no-alternative score is **0**.
   - **Fix:** Include an outside option in the feasible reference only when the case declares one, and score a no-deal result with no declared alternative as zero. A fallback-based cost or regret estimate may remain a separately labelled economic diagnostic, but must not become a feasible alternative or arena score.
   - **If we skip it:** Cheap fallback rates can make immediate abandonment look optimal and alter every deal's score denominator. That rewards a behavior the actual competition explicitly scores at zero.

   Reproduce with `invented_outside_option()` in [repro_logic.py](repro_logic.py). `CompetitionSim._end()` is reached through ordinary `open()` / `message()` calls, so this is the evaluator used by the competition, not an unused helper. The scanned current development/holdout fixtures never select this invented option as their best reference; this boundary reproduction does not establish that the archived aggregate scores changed.

The package-pricing defect independently confirmed during this review is **BUGS finding 2**, in [BUGS.md](BUGS.md). With DAP supplied as an allowed supplier standard but only EXW in `term_options`, the router prices DAP packages and then rewrites them to EXW before sending; one reproduced €29.99 package costs **€39,990** against a **€30,000** alternative. That shared defect is counted in BUGS, not duplicated here.

Already documented boundaries were not turned into new findings: missing supplier-health estimates, the deliberately narrow conditional-language heuristic, incomplete prose-claim grounding, sequential RFQ requests, private virtual clocks, repeated scenario economics and the unqualified medium-semantic model branch. These limit final readiness, but the current documentation names them and does not present synthetic scores as a hosted leaderboard. The two scoring findings above are different: they contradict explicit published scoring rules even within the local evaluator.

Verification used only local imports and in-memory suppliers. Run both probes from an empty environment without loading `.env`:

```sh
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  /home/deimos/Documents/git_prv/tacto_negotiator/.venv/bin/python \
  /home/deimos/Documents/git_prv/tacto_negotiator/docs/reviews/repro_logic.py
```

The probe assertions describe the present defects and should fail after those defects are corrected. No source, tests, configuration, archived materials or existing result files were modified by this reviewer.

Verdict: fix 1 and 2 before extending or interpreting the benchmark as the published arena score. See BUGS for the deployment-blocking package rewrite.

Not checked: hosted supplier behavior, Sunday-release contents, live arena or provider calls, real final-wave capacity, actual billing, or a recomputation of archived paid model results; the parent review runs the full baseline suite separately.
