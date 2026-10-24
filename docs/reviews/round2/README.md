# Review round 2 — commit `5dea00a` (2026-10-25)

Three reviewers worked in parallel, each on one focus. The previous round was [FLOW.md](../FLOW.md).

| Report | Focus | Findings | Reproduction |
|---|---|---|---|
| [BUGS.md](BUGS.md) | Code that does something other than its docstring, the API or its caller expects | B1-B5 (1 High, 3 Medium, 1 Low) and a check of each FLOW fix | [repro_bugs.py](repro_bugs.py) |
| [FALLACIES.md](FALLACIES.md) | Conclusions that don't follow; unsupported assumptions about suppliers, scoring and evidence | A1-A9, plus an updated negmas checklist | [repro_fallacies.py](repro_fallacies.py) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Boundaries, single source of truth, state, error handling, replica coupling | A1-A9, each tagged [now] or [later] | offline probe described in the report |

Both FALLACIES and ARCHITECTURE number their findings A1-A9, so below they appear as **L-An**
(logic) and **R-An** (architecture).

Verification: the coordinating reviewer re-ran both repro scripts on `5dea00a`, and every probe
printed the behaviour its report describes. B1 and B4 were also confirmed by reading
`routed.py:192-195` and `play.py:608-621`. The test suite passes: 333 tests. Nothing under `src/`,
`tests/` or config was changed.

```sh
.venv/bin/python docs/reviews/round2/repro_bugs.py
.venv/bin/python docs/reviews/round2/repro_fallacies.py
```

## Merged priority list

Several reports found the same problem from different angles. These are merged here.

| # | Problem | Sources | Severity | Status of earlier fix |
|---|---|---|---|---|
| 1 | **The final-offer guard has two bypasses.** (a) A prose-only model message gets a `_limit_sheet` added, which sends a counter below the final offer (18 → 15.6), `routed.py:192-195`. (b) `_mirror_alternative` sends with `reprice=False`, skipping the guard, and so mirrors to a sole supplier's final offer, `routed.py:833-872`. The guard comment says "v5: 3 of 3", but it was 3 of 7 | B1, L-A2 | High | FLOW F4 partial |
| 2 | **Catch-all excepts turn code bugs into moves.** A `KeyError` in the e-mail text filter made the agent accept an untouched opening (scores 0), and the trace blamed the model | R-A1 | High | new |
| 3 | **We still concede more when the supplier stands still.** If it holds or raises, our next move is the full step; if it drops to 17, we move 0.4 of the step. The rule forcing a step when there is no new fact makes this worse (`play.py:352-387`, `routed.py:191-195`) | L-A1 | High | FLOW F1b open; F1a only half-fixed |
| 4 | **A late accept is dropped although its thread still has about 35 s,** and the case waits a full decision more (`play.py:608-621`). Side effect of the F3 fix | B4 | Medium | regression from F3 |
| 5 | **The rival-price claim can cite a price above the supplier's own,** now added automatically by the fact bank, including to scripted messages (`facts.py:279-297`) | B3, L-A5 | Medium | FLOW F7 open, now wider |
| 6 | **The fact bank tracks cited IDs per supplier, but the arena counts them per case.** The second RFQ bidder gets `duplicate`, yet `_new_fact` treats the citation as new. One shrug from a supplier marks every claim kind as used | B2, L-A5 | Medium | new |
| 7 | **A round-1 walk-away still passes when an alternative exists,** because the gate treats the current offer as the best one reachable (`routed.py:109-118`) | L-A6, BUGS FLOW check | Medium | FLOW F5 partial |
| 8 | **Supplier floors are misread.** "1,250.00" is read as 1 and 250; a price at the end of a sentence is never seen as a floor; the near-close then asks below a floor already named (`levers.py`) | B5, L-A7 | Low/Medium | new |
| 9 | **The replica cannot validate the agent.** It is a hand-copied runner rather than `play()`, grades claims with the agent's own checker, scores supplier health with the agent's own estimate, and its concession rule contradicts the P07 model behind the 0.40 routine step. Gains measured on it are upper bounds | R-A2, R-A7, L-A3 | Medium (evidence) | FLOW section 3 still stands |
| 10 | **Single hosted runs cannot rank v13, v14 and v15.** One 10-case run has an SD of about 0.023; "v13-v14 ~0.77" pools runs before and after the change, and 0.7915 is the best of several runs | L-A4 | Medium (evidence) | new |
| 11 | **No single source of truth.** About 12 acceptance sites with three tolerances, a 10-step rewrite pipeline for concessions, and the anchor in 3 places. `validate` also sets policy and rewrites prose, and reports failure as a bare `None`. `llm.guard` duplicates `validate` with weaker checks. Prompt numbers duplicate code constants | R-A3, R-A4, R-A5, R-A8, L-A8 | Medium (risk of the next F2-type bug) | new |
| 12 | Hidden state (`case["_supplier_estimates"]`, `agent.unavailable` set by the runner), and `play.py` mixes transport, orchestration and policy | R-A6, R-A9 | Low | later |

## Suggested order before the final

Only small, local, testable changes:

1. **#1:** in `validate`, block `_limit_sheet` and mirroring for `final_offer` and `agreed`
   threads. Mirroring could still be allowed when another supplier's acceptable offer is at least
   as good.
2. **#4:** let an `accept` through if its own thread still has time.
3. **#2:** log the exception and its traceback before every fallback, so a code bug is no longer
   recorded as a model failure. Optionally, let a failure in the text filter keep the unfiltered
   body instead of falling back.
4. **#5, #6:** cite a rival only when its unit price is below the recipient's; key cited IDs per
   case.
5. **#7, #8:** small guard and parser fixes.
6. Run the final with `--agent routed` only (R-A5).

**#3** changes strategy and needs repeated hosted runs. Given #10, it cannot be judged on a single
run, so it is better left until after the final. **#9, #11 and #12** are refactors for after the
competition.

Not checked: live or hosted behaviour of any proposed fix. The B4 side effect (a dropped late
accept letting a final-offer thread time out, which hosted suppliers might treat as a push) could
not be reproduced locally.
