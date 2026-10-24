# Turn the official FAQ into a better buyer

Checked against the [official FAQ](https://arena.tacto.ai/faq) on 24 October 2026, with the [participant brief](https://github.com/tacto-playground/negotiation-arena-kit/blob/main/brief/brief.md) and [API contract](https://github.com/tacto-playground/negotiation-arena-kit/blob/main/brief/api.md). This page separates competition rules from our engineering choices.

## What changes our priorities

The FAQ makes the objective clear: normalized scores rank teams; absolute euro savings do not. Every case matters. Cost covers the entire demand horizon, including uncovered demand. Walking away can be optimal. Equal points favor more deals, then fewer rounds. Practice sessions share the ten-session capacity with the final. The final runner must operate unattended. [Source: FAQ](https://arena.tacto.ai/faq).

Our practical advantage should be **correct, timely decisions across all cases**, supported by cheap deterministic evaluation. A larger model helps where interpretation improves an otherwise unresolved decision.

| Opportunity | Project action | Status |
| --- | --- | --- |
| Preserve points while gaining a deal in an exact economic tie | Shared acceptance now accepts a valid package whose evaluated cost equals the alternative. | Implemented for goals without an unestimated supplier-health term. Default, must-have and term checks remain. |
| Expose a low price hiding an expensive package | Model context now includes nonzero cost components, particularly uncovered need and fallback risk. | Implemented in LLMAgent.render, also used by the routed buyer. |
| Avoid pointless rounds | The common model instruction no longer forbids accepting every opening categorically; it asks for supported improvements and explains tie-breaks. | Prompt changed; real-model response quality is not measured by local tests. |
| Keep the result summary honest | A missing final result makes its wave and total unknown; it is not replaced with zero. | Implemented in the CLI, using only the team's case list and local results. |
| Reproduce policy measurements | New reports hash play.py and agents/llm.py alongside the existing policy/evaluator files. | Implemented; historical reports retain their bytes. |

Supplier-health uncertainty still matters. The acceptance guard has no supplier-cost estimate; an apparent tie in stabilise or secure_supply retains the previous conservative behavior. This does not solve the broader relationship-cost gap.

## A concrete cost trap

The published example has a two-year requirement. At €41.60 and 60-day payment, full coverage costs €332,800. A one-year contract leaves the remainder at fallback price plus risk: €355,920 and zero score against the stated opening. The smaller commitment is the worse package. [Source: participant brief, scoring example](https://github.com/tacto-playground/negotiation-arena-kit/blob/main/brief/brief.md#6-the-score-total-cost-in-).

Our regression verifies these totals. The model now sees the short-contract components: €166,400 purchase cost, €184,000 uncovered demand and €5,520 fallback risk. The deterministic evaluator already priced these components; the change exposes the evidence to the semantic route too.

The exact-tie simulation arranges an agreed package matching the outside option. Both accepting and walking score 1.0 at identical full cost. Acceptance adds a deal. The agent sees the public package and alternative; private reference economics belong only to the test evaluator.

## Prepare the final machine

These are operational steps for the event, not actions performed by the local test suite:

1. Refresh Sunday data before freezing the final process. Recreate DataPackage after downloading it: facts and the ID index are cached. Verify release-aware lookups; base SQLite queries alone do not include release CSV overlays.
2. Stop practice processes early and verify their sessions have ended. Stopping a process does not immediately free its arena sessions. The existing sessions API can inspect your team's practice and sandbox sessions.
3. Run the official network/key pre-flight before the final, then launch the frozen commit with explicit capacity:

```sh
.venv/bin/tacto run --set final --agent routed --parallel 10 --waves 5 --data /path/to/package
```

4. Keep the process and network awake. Retain the 180-second arena transport timeout and adaptive closing reserve; the separate model deadline stays short. Existing tests cover pre-start waiting, session reuse, Retry-After and wave-cap extension.
5. Leave the final runner autonomous. Do not connect it to standings, event streams or screen data. The local dashboard reads saved project reports and adds no live arena feed.

The official repository flags the Sunday clock change. Schedule with Europe/Berlin local time rather than assuming Saturday's UTC offset. Submission and process launch remain your event preparation. [Source: official schedule and rules](https://github.com/tacto-playground/negotiation-arena-kit#the-weekend-at-a-glance).

## What to build next

The strongest remaining implementation opportunity is **concurrent servicing of RFQ suppliers**. The holdout still loses other supplier threads while waiting synchronously on one reply. A faster buyer model cannot eliminate that wait. Keep this above more elaborate model routing; see [Sunday readiness](FINAL_READINESS.md).

Next, prepare concise supplier evidence before opening sessions: current IDs, units, dates, relevant correspondence and a few priced trade-offs. The shared ID cache helps, but full pre-session dossiers and grounded claim arithmetic remain unfinished. Cache against a specific release; avoid stale evidence after a data update.

## Local evidence

The [validation record](reviews/faq_validation.json) compares two 300-match runs with identical held-out seeds, ten families, five waves and ten workers. Economic outcomes, routes and validity were unchanged: neither cohort contained the new exact-tie opportunity. The separate targeted simulation proves that behavior. Guarded strategies retained 200/200 valid outcomes; the naive baseline had five invalid outcomes. Scripts still recorded 120 thread timeouts in the delayed-RFQ cases.

The application suite passes 264 tests, including 12 new checks for ties, uncertain health, full-horizon model context, the tie simulation and complete/partial/missing final summaries. No credentials, hosted arena or paid model calls were used. This establishes local correctness, not a measured hosted-score improvement.
