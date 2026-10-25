# Review round 3 — commit `d842b8e` / `737c1d3` (2026-10-25)

Three reviewers worked in parallel, each on one focus, over the whole application and in more depth on
the 28 commits since round 2 (`5dea00a..d842b8e`). `737c1d3` only changes the README. The previous round is
[round2/README.md](../round2/README.md).

| Report | Focus | Findings | Reproduction |
|---|---|---|---|
| [BUGS.md](BUGS.md) | Code that does something other than its docstring, caller, API or tests expect | B1-B6 (1 Medium, 5 Low) and round-2 bug status | [repro_bugs.py](repro_bugs.py) |
| [FALLACIES.md](FALLACIES.md) | Conclusions that don't follow; unsupported assumptions and evidence | L1-L11 (3 High, 2 Medium, the rest Low) and round-2 logic status | [repro_fallacies.py](repro_fallacies.py) |
| [BLOAT.md](BLOAT.md) | Dead code, duplicated rules, oversized artifacts, each tagged [safe-now] or [after-final] | X1-X17 and earlier bloat status | [repro_bloat.py](repro_bloat.py) |

Verification by the coordinating reviewer:
- All three repro scripts were re-run, and each printed the behaviour its report describes.
- B1 and L3 were also confirmed by reading `routed.py:611-630` and `routed.py:220-345`.
- The suite passes from an empty directory: 349 tests.
- Nothing under `src/`, `tests/` or config was changed.

```sh
.venv/bin/python docs/reviews/round3/repro_bugs.py
.venv/bin/python docs/reviews/round3/repro_fallacies.py
.venv/bin/python docs/reviews/round3/repro_bloat.py
```

Testing hazard: a leftover `/tmp/h2.py` shadows the `h2` package that httpx imports. Running the documented
`cd /tmp && … pytest` command fails because of it (B6). Run from a fresh `mktemp -d` directory, or delete that file.

## Merged priority list

| # | Problem | Sources | Severity | Before 13:30? |
|---|---|---|---|---|
| 1 | **The clause sheet for any goal (c99893a) switches off the hold-one-sheet rule (3b94da9).** `_imposed_clause` returns False when our first message carried a clause sheet. On P16, round 2 then sends 3 package variants instead of 1. That is the pattern that drew early final offers on hosted P08/P18 (about -0.15) | L3 | High | Yes: one line, limit `_clause_sheet` to relationship goals again |
| 2 | **`_opening_limit` counts openings that break a must-have** and has no floor at our last ask. On S02 the ask to the compliant bidder is 13.72 instead of 13.88. A late-read quote can make our ask go backwards (found by reading the code) | B1 | Medium | Yes: two lines, skip openings failing `must_haves_ok`, and `max(best*0.999, last ask)` |
| 3 | **A lone supplier's final offer still gets a mirrored term sheet.** The comment "v5: 3 of 3" is wrong (3 of 7), and FINAL_RUNBOOK contradicts itself | L2 (round 2 #1b) | High in the rare cases it fires | Maybe: mirror only when a rival has an acceptable offer |
| 4 | **We concede more when the supplier holds** (holds → 1.0 of our step; concedes to 17 → 0.4) | L1 (round 2 A1) | High | Only a cap of our step at the supplier's last concession, if anything |
| 5 | **The 3.0 clause threshold rests on one case.** P08/P18 sit at 2.96. The P16 gain comes from the local supplier model, which is built to want the clause | L4 | Medium | No; evidence |
| 6 | **The bench overstates its sample.** Waves are exact repeats (225 runs are 45 samples). Seed noise (0.93 vs 0.70) is far larger than the claimed +0.005 gains. Repeated hosted scripted runs are n = 1 per arm | L5, L10 | Medium (evidence) | No |
| 7 | **The bench runner `competition._match` is a hand copy of `play._play`.** It lacks the stale-accept re-read (d842b8e), the own-thread clock for accepts and the document re-read, and defaults to a 25 s decision budget where live play uses 40 s | X10, R-A2 round 2 | Medium (evidence) | No |
| 8 | **422 handling.** The stale-accept retry doesn't count its timeout (B2). A 422 on a non-accept message ends the case with no deal (B3, not confirmed) | B2, B3 | Low | Optional |
| 9 | **Fast mode is forced on every Anthropic model;** only a 429 falls back to standard speed. The README final command (Opus 5.5, no strong model) is not affected | B4 | Low | No |
| 10 | **Small mismatches between code and prose:** rival ID stripped per case instead of per supplier (B5), "held" sheet changes the payment term (L8), `short_time_work` read two ways (L9), prompt, runbook and code disagree on accepts (L11), stale ROUTING.md (X13) | B5, L6-L9, L11, X13 | Low | No |
| 11 | **Duplicated rules keep growing.** `routed.py` has 49 hard-coded numbers (41 in round 2), and `validate` has grown from 116 to 142 lines. The OpenAI experiment code, the old `LLMAgent`, deal records saved as both `.json` and `.js`, and two near-identical Dockerfiles are still there | X3-X12, X15-X17 | — | No; ~480 src lines removable after the final |
| 12 | **Housekeeping:** `.claude/worktrees` holds 854 MB in 18 stale worktrees. Live runs write deal journals into `docs/deals` (140 untracked files today). `LLM_PROVIDER` defaults to OpenAI when unset (the README command sets it) | X14, bloat cross-notes | Low | `.gitignore` `.claude/` is safe now |

Safe now, with no behaviour change (applied in a /tmp copy, 349 tests pass): X1, X2, X4, X13, X14.
