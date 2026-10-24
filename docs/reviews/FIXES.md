# Review findings resolved

All nine findings from the review of `4aca02d` are addressed. The original reports
and defect probes describe that historical revision. Current regressions live in
`tests/`; the old probes intentionally assert broken behavior and are not current
validation commands.

| Finding | Change | Regression evidence |
| --- | --- | --- |
| BUGS 1: stale timeout terms | Recovery replaces local history with authoritative messages, matches terms to their offer ID and counts timeout rounds. Missing current terms block acceptance; rejected requests are not recorded as successful moves. | `test_409_recovery_keeps_current_offer_terms_and_rounds`, `test_recovery_without_matching_offer_cannot_accept`, `test_resumed_timeout_rounds_do_not_authorize_extra_sends` in [runner integration tests](../../tests/test_routing_integration.py). |
| BUGS 2: rewritten legal defaults | `complete_offer` preserves explicit supplier-standard values, including values outside the case's option list. Both guarded offer builders retain them; script prices describe the terms actually sent. | `test_legal_supplier_standard_survives_both_offer_builders` in [acceptance tests](../../tests/test_acceptance.py), `test_supplier_standard_packages_keep_the_cost_ceiling` in [script tests](../../tests/test_script_coverage.py). |
| BUGS 3: index clause becomes null | Only explicit `none` parses as no clause. Unknown clause text raises an error; simulator PDFs render the supported structured format, including fractional material percentages. | `test_unrecognized_clause_is_not_a_null_clause`, `test_indexed_supplier_document_round_trip_and_safe_closure` in [acceptance tests](../../tests/test_acceptance.py). |
| BUGS 4: empty opening deadline | An opening with no live bidders returns its terminal result before reading documents or calculating deadlines. | `test_terminal_opening_returns_result_without_document_work`, for both no-bid and walked-away openings, in [runner integration tests](../../tests/test_routing_integration.py). |
| LOGIC 1: invalid opening baseline | Scoring v2 uses the cheapest eligible opening and rejects fixtures without one. The lead-time repair family keeps its invalid first bidder and adds a valid second bidder. | `test_score_baseline_excludes_ineligible_openings`, `test_no_eligible_opening_is_rejected_instead_of_inventing_a_baseline` in [competition tests](../../tests/test_competition.py). |
| LOGIC 2: invented outside option | Only a declared alternative enters the reference/no-deal cost. No-alternative walk-away scores zero, with unknown cost and regret recorded as null. No feasible reference is an explicit fixture error. | `test_no_alternative_means_no_walkaway_score_or_feasible_outside_option`, `test_no_feasible_reference_is_an_invalid_scenario` in [competition tests](../../tests/test_competition.py). |
| BLOAT 1: divergent acceptance guards | `play.acceptable_offer` and `play.closing_move` serve routed, legacy and generic deadline closing. Field validation lives with the term helpers in `cost.py`; outgoing legacy/naive policies remain separate. | `test_guarded_entry_points_agree_on_acceptance` checks missing, failed and malformed evidence, constraints, alternatives and a valid offer in [acceptance tests](../../tests/test_acceptance.py). |
| BLOAT 2: unused catalog descriptions | Deleted the 25 descriptions for excluded documentation/browser symbols. Updated descriptions for moved/shared source functions and the new tests. | In-memory comparison before deletion confirmed identical serialized atlas output. Existing source-map checks validate the refreshed catalog. |
| BLOAT 3: repeated quote parsing | Candidate search filters costs and must-haves locally; the selected message still receives full validation. The inner loop no longer reparses the quote for every candidate. | `test_bounded_search_parses_the_quote_once_per_decision_stage` exercises 256 candidates and bounds quote parsing independently of candidate count in [script tests](../../tests/test_script_coverage.py). |

The evaluator also includes legal supplier-standard values in its feasible package
reference; `test_reference_includes_legal_supplier_standard_values` covers that
connection to the offer-completion fix.

## Validation

The full application suite passes **244 tests, zero skipped**, in 27.40 seconds.
It includes the five-wave, three-team, 150-session orchestration rehearsal and
provider cancellation tests with mocked transports. The initial focused checks
passed 64 runner/protocol tests, 77 policy/term tests and 26 competition tests;
these overlap with the full suite and are not additional test counts.

The 11 documentation tests, source-map snapshot check, Ruff and browser interaction
suite also pass. Browser checks cover the tree, file details, dependency groups,
source view, navigation, settings and offline behavior.

Two local competition processes ran in parallel, each with ten workers, ten
scenario families and five repeated 480-second virtual waves. Development seeds
were 0–2; holdout seeds were 201–203. All suppliers stayed in memory. No provider
or arena request was made, and no real dotenv file or key was read.

| Cohort | Policy | Valid outcomes | Mean synthetic score /100 | Thread timeout events |
| --- | --- | --- | --- | --- |
| Development | Naive comparison | 145/150 | 54.69 | 0 |
| Development | Guarded immediate close | 150/150 | 10.00 | 0 |
| Development | Scripts with local fallback | 150/150 | 51.97 | 0 |
| Holdout | Naive comparison | 140/150 | 55.43 | 90 |
| Holdout | Guarded immediate close | 150/150 | 10.00 | 0 |
| Holdout | Scripts with local fallback | 150/150 | 58.74 | 180 |

All **900 matches** finished before their private caps, with no execution errors.
The guarded policies had **600/600 valid outcomes**. Scripts handled 240/300 cases
without a model attempt or fallback; the other 60 invoked the deliberately
unavailable local completion callback and closed through the shared guard. Those
callbacks are telemetry attempts, not API calls. The naive policy's 15 invalid
deals remain visible as comparison failures.

[validation.json](validation.json) records the compact aggregates, source hashes,
scoring version and limitations. Full transient traces were written to `/tmp`
during validation; the durable record is the compact file. Historical
`docs/results/*.json` files are unchanged. Their scores predate scoring v2 and
must not be compared directly with the new scenario/evaluator as evidence of a
model ranking change.

To repeat the application checks, start at the repository root:

```sh
TACTO_ROOT="$PWD"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_ROOT/.venv/bin/python" -m pytest -q -p no:cacheprovider "$TACTO_ROOT/tests"
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_ROOT/.venv/bin/tacto-compete" --strategies naive close scripts \
  --seeds 0 1 2 --waves 5 --workers 10 --output /tmp/tacto-development.json
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_ROOT/.venv/bin/tacto-compete" --strategies naive close scripts \
  --seeds 201 202 203 --holdout --waves 5 --workers 10 --output /tmp/tacto-holdout.json
```

The Python API was used for the recorded parallel batches; these CLI commands use
the same factories and competition runner and save full traces. Time measurements
vary with machine load. Each match starts its own virtual wave clock, so the
900-match comparison is not a shared-wave capacity test.

Implementation commits: `cd93155` (recovery and terminal openings), `dbb93ba`
(shared guards, quote/term fixes and candidate work), `05920ab` (scoring v2), and
`0fddd10` (selecting the current sheet from multi-offer messages). The comparison
batches used `05920ab`; the final 244-test run includes the multi-offer regression.

**Remaining limits:** RFQ supplier calls are still sequential, and the holdout
confirms neglected-thread timeouts. Supplier-health estimation and prose-claim
arithmetic remain incomplete. Live arena behavior, real provider latency, Docker
execution and paid model reranking were not checked in this task.
