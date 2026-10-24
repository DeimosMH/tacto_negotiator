# Project review and fixes

**All nine findings are addressed.** Start with [FIXES.md](FIXES.md) for the
implementation changes, regression tests, commit references and validation results.
At that review's resolution, the application suite passed 244 tests. Two parallel offline cohorts completed
900 matches; the guarded policies had 600/600 valid outcomes. The source atlas and
current guides explain the repaired implementation.

Subsequent [FAQ alignment](../FAQ_ADVANTAGE.md) brings the suite to 264 tests and adds
[a separate 600-match before/after validation record](faq_validation.json). Those
results preserve the original review evidence and document the remaining RFQ timeouts.

| Historical review | Findings | Original reproduction |
| --- | --- | --- |
| [Bugs](BUGS.md) | 4: stale state, rewritten terms, quote parsing and terminal openings | [repro_bugs.py](repro_bugs.py) |
| [Logic](LOGIC.md) | 2: opening eligibility and nonexistent outside options | [repro_logic.py](repro_logic.py) |
| [Bloat](BLOAT.md) | 3: duplicated acceptance, unused descriptions and repeated parsing | [repro_bloat.py](repro_bloat.py) |

Three independent agents reviewed the full application at
`4aca02de43bf3f15c554ab6144e438ae91616327`, rather than only the preceding archive
move. They traced source callers, tests, packaging, guides and the supplied
[API contract](../../materials/starter_kit/brief/api.md). The baseline had 221
passing application tests and 11 documentation tests; the coordinating reviewer
independently reproduced all nine findings despite that green suite.

The original reports and scripts are historical evidence. Their assertions
confirm the old defects and are expected to fail after the fixes; they are not
current regression commands. Use the tests and commands in [FIXES.md](FIXES.md)
or [local testing](../LOCAL_TESTING.md) to validate the current checkout.

[validation.json](validation.json) contains the new compact offline results and
source hashes. No real dotenv file, credential, hosted arena or paid model was
used for the review or remediation. The old paid experiment in `docs/results/`
remains unchanged; it predates scoring version 2 and is not a current model ranking.

The operating assumption remains five 480-second waves, ten concurrent cases
and independent 60-second buyer windows per supplier thread. Ten cases is a test
assumption, not a supplied final case count. The full suite includes a shared-wave
runner rehearsal; the 900-match policy comparison gives each match its own clock.

The remaining sequential RFQ scheduling, supplier-health and prose-evidence limits
are documented in [Sunday readiness](../FINAL_READINESS.md). The holdout still
records supplier-thread timeouts. Fixing these review findings does not establish
hosted final readiness.

Not checked: live arena behavior, provider/venue capacity, Docker execution and
paid model reranking.
