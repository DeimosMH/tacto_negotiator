# Project review

Three independent agents reviewed the current application at commit
`4aca02de43bf3f15c554ab6144e438ae91616327`. This covers the implemented project,
not just the last archive-move commit. Application code is unchanged by this review.

| Review | Findings | Focus | Evidence |
| --- | --- | --- | --- |
| [Bugs](BUGS.md) | 4 | Incorrect moves, stale state, parsing and crashes | [Local reproductions](repro_bugs.py) |
| [Logic](LOGIC.md) | 2 | Negotiation economics, scoring and conclusions from simulations | [Local reproductions](repro_logic.py) |
| [Bloat](BLOAT.md) | 3 | Duplicated checks, unused descriptions and repeated work | [Local reproductions](repro_bloat.py) |

Reviewers traced callers and dependencies across `src/tacto_negotiator`, tests,
packaging and documentation. The original API contract in
[`materials/starter_kit/brief/api.md`](../../materials/starter_kit/brief/api.md)
provides the expected behavior. The assumed operating load is five 480-second
waves, ten concurrent cases and independent 60-second supplier-thread windows;
ten cases is a test assumption, not a supplied final case count.

## Fix order

1. **Preserve authoritative offer state during recovery.** A timeout followed by a
   round-limit error can attach old terms to a current offer ID. The local
   reproduction accepts a EUR 170,077.44 deal against a EUR 136,487.56 alternative.
   See Bugs, finding 1.
2. **Preserve legal supplier defaults when building offers.** The validator accepts
   a supplier-standard value, then the completion helper silently changes it.
   A scripted package priced below a EUR 30,000 alternative is sent at EUR 39,990
   after its freight term changes. See Bugs, finding 2; the logic review
   cross-references the same defect rather than counting it twice.
3. **Keep unknown index clauses distinct from no clause.** The local simulator's
   dictionary formatting is parsed as `None`, allowing an acceptance that violates
   a must-have and scores zero. Hosted occurrence is unverified. See Bugs,
   finding 3.
4. **Return the result when every opening thread has ended.** The routed runner
   currently computes a minimum over an empty set and crashes. See Bugs, finding 4.
5. **Correct the competition scorer before extending the comparison.** An invalid
   opening can lower the scoring baseline; a nonexistent outside option can earn
   full points for walking away. These are local evaluator defects, not evidence
   that the hosted arena scores incorrectly. See Logic.
6. **Unify acceptance safety, then do the small cleanup.** Legacy and routed closing
   checks disagree about unknown defaults. Unused catalog descriptions and repeated
   quote parsing are lower-priority cleanup. See Bloat.

The existing limitations around sequential RFQ requests, missing supplier-health
estimates and synthetic model qualification remain relevant. They are already
documented in [Sunday readiness](../FINAL_READINESS.md) and
[competition methodology](../COMPETITION.md); this review does not present them as
new discoveries. It does not establish a new model ranking or rewrite historical
benchmark results.

## Verification

The unchanged baseline passed:

- **221 application tests**, with no skips, in 25.89 seconds.
- **11 documentation tests**, source-map snapshot validation and browser interaction
  checks, including the browser check for remote requests.
- Ruff over `src`, `tests` and `docs`.

The coordinating reviewer independently reran all three review scripts and
confirmed all nine findings. The scripts deliberately assert the reported defects
or measured duplication. A successful script run therefore confirms a finding;
it is not a passing safety regression. Run them independently from a clean
environment, after installing the project's local development dependencies:

```sh
TACTO_ROOT="$PWD"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_ROOT/.venv/bin/python" "$TACTO_ROOT/docs/reviews/repro_bugs.py"
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_ROOT/.venv/bin/python" "$TACTO_ROOT/docs/reviews/repro_logic.py"
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_ROOT/.venv/bin/python" "$TACTO_ROOT/docs/reviews/repro_bloat.py"
```

All review reproductions use local data, in-memory suppliers or mocked calls. No
real `.env`, `./env`, API key, hosted arena or paid model was used. Runtime behavior
was checked against the repository and supplied contract; no external lookup was
needed to resolve these findings.

**Verdict:** fix the acceptance/state defects before treating the buyer as ready for
the final. The green baseline misses these boundary cases.

**Not checked:** actual arena behavior, live provider latency, venue capacity and
Docker execution. The reports describe the reviewed revision, not future fixes.
