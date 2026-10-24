# Tacto Negotiator architecture

Tacto Negotiator is the active Python application in [src/tacto_negotiator](../src/tacto_negotiator/__init__.py). Its default buyer chooses a tested script, a hosted model or deterministic fallback for each decision. The runner owns sessions and timing; shared code evaluates costs and validates actions. This guide describes the implementation that exists, followed by the remaining work.

The project is installed from the root [pyproject.toml](../pyproject.toml), uses the root .venv, runs its tests from [tests](../tests/test_kit.py), and reads the active [data](../data/README.md) snapshot. The supplied starter kit is preserved separately under [materials/starter_kit](../materials/starter_kit/README.md). Its historical arena_kit package is reference material; current imports use tacto_negotiator. See the [root README](../README.md) for installation and quickstarts, [the tutorial](TUTORIAL.md) for a first change, and [the routing guide](ROUTING.md) for the policy rules.

## 1. What the application does

The buyer represents Hollerau Pumpenwerke. A case specifies suppliers, parts, demand, permitted terms, must-haves and an outside option. The buyer exchanges email-shaped JSON, then accepts a currently standing supplier offer or walks away. Structured term sheets bind; a promise in the email body alone does not change the contract. A supplier's agreed status still requires explicit buyer acceptance.

The objective is the cost of covering a fixed need over a fixed horizon. Cost includes uncovered demand, payment timing, freight, quality, lead time, tooling and index exposure. Relationship-sensitive goals can also penalize an unsustainable supplier margin. A lower unit price can produce a worse complete deal, and the outside option can beat an agreement.

The hosted arena owns supplier bots, hidden economics and the authoritative score. This project implements the buyer and local test suppliers. The presentation's sales, finance, management and logistics roles describe the remote supplier organization; they are not separate buyer agents in this application.

Sources: [participant brief](../materials/starter_kit/brief/brief.md), [API contract](../materials/starter_kit/brief/api.md), [presentation](../materials/Tacto%20Challenge%20Presentation.pdf).

## 2. Entry points and logical blocks

| Block | Implemented files | Responsibility |
| --- | --- | --- |
| Application command | [cli.py](../src/tacto_negotiator/cli.py) | The tacto command selects a policy, lists cases, runs one case or a set, and exposes read-only data tools. play/run default to routed. |
| Session execution | [play.py](../src/tacto_negotiator/play.py) | RemoteTable adapts HTTP to the negotiation loop. play manages one case; play_all and play_waves schedule independent cases. |
| Arena transport | [client.py](../src/tacto_negotiator/client.py) | ArenaClient provides API calls, shared pacing, retry handling and a stable move ID for each logical POST. |
| Routed buyer | [agents/routed.py](../src/tacto_negotiator/agents/routed.py) | Script rules, bounded package enumeration, model selection, common validation and fallback. |
| Model execution | [agents/completion.py](../src/tacto_negotiator/agents/completion.py) | OpenAI Responses calls with an absolute deadline, shared concurrency slots, bounded output and cancellation. |
| Prompt context and baselines | [agents/llm.py](../src/tacto_negotiator/agents/llm.py), [agents/naive.py](../src/tacto_negotiator/agents/naive.py) | LLMAgent supplies case/context rendering reused by the router. The legacy LLM and naive policies remain comparison and teaching baselines. |
| Economic calculations | [cost.py](../src/tacto_negotiator/cost.py) | Resolve terms, calculate cost components, check must-haves and construct complete offers. |
| Evidence and attachments | [data.py](../src/tacto_negotiator/data.py), [docs.py](../src/tacto_negotiator/docs.py) | Read-only SQL and citable lookup, document access, PDF/XLSX text extraction and supplier defaults. |
| Optional data tools | [mcp_server.py](../src/tacto_negotiator/mcp_server.py) | Six read-only MCP tools. Starting them does not automatically add retrieval to the buyer. |
| Contract simulator | [sim.py](../src/tacto_negotiator/sim.py), [sim_cases](../src/tacto_negotiator/sim_cases/S01.json) | tacto-sim serves local HTTP cases and a controllable final rehearsal. |
| Policy comparison | [competition.py](../src/tacto_negotiator/competition.py), [compete.py](../src/tacto_negotiator/compete.py), [benchmark.py](../src/tacto_negotiator/benchmark.py) | tacto-compete runs paired synthetic scenarios, with explicit live opt-in and sanitized model-spend accounting. |
| Verification | [tests](../tests/test_routing_integration.py) | Protocol, policy, timing, malformed response, simulator, budget and command tests. |

These blocks are Python modules in one project. They do not require separate deployed services or a coordinator model.

## 3. One case through the current runtime

```text
tacto play / tacto run
        |
        +-- shared ArenaClient + optional DataPackage
        +-- fresh agent instance for each case
                    |
          play / RemoteTable.open
                    |
          current threads + parsed quotes
                    |
          refresh thread and wave timing
                    |
          RoutedAgent.decide_timed
             |              |
          script      one fast/strong completion
             +--------------+
                    |
             common validation
                    |
          RemoteTable.send or close
                    |
          supplier reply -> update history -> repeat
```

1. The CLI constructs one shared arena client and a factory that returns a new agent per case. With --data, it creates a DataPackage and warms its ID index before sharing it. It does not yet build a full case dossier before opening.
2. RemoteTable opens a session and keeps its session ID. In the final, a conflict identifying an existing session can be resumed when the runner has the case metadata.
3. play reads the opening attachments and maintains threads as supplier ID → ordered messages. Extracted document text is attached to each message's attachment records. Unreadable routed quotes are marked read_failed and cannot silently become known defaults.
4. The runner refreshes authoritative thread timers, removes closed threads and refreshes changed offer IDs or statuses. It derives a decision budget from the earliest waiting supplier and the current wave reserve.
5. RoutedAgent first tries a supported script. If interpretation remains, it prepares context using LLMAgent and calls either the fast or configured strong completion once. Preparation currently occurs after opening and uses the same decision budget.
6. Every routed proposal passes the same validator. If a model fails, is late or returns an invalid decision, the agent returns a local closing fallback. Only the runner sends the arena move.
7. The runner records the supplier response, reads new attachments, and repeats until acceptance, walk-away or an ended session. A 409 causes state recovery rather than blindly replaying stale acceptance.

The extension interface remains small:

```python
# A basic policy implements:
decision = agent.decide(case, threads)
# A timed policy additionally supports:
decision = agent.decide_timed(case, threads, deadline=absolute_monotonic_deadline,
                             wave_seconds_left=remaining_wave_seconds)
# Both return {"supplier_id": "sup:...", "message": {...}}.
```

An injected completion has the signature complete(system, user, *, deadline) and must enforce that absolute monotonic deadline. CompletionClient implements that contract; an arbitrary Python function cannot be forcibly stopped by the runner.

## 4. State and ownership

| Object | Important state | Ownership |
| --- | --- | --- |
| ArenaClient | HTTP client, endpoint/key headers, pacing lock and next request time | Shared across case workers so the per-key limiter is shared. |
| RemoteTable | client, session_id, case metadata and done result | One active session adapter per case. |
| play | Thread history, received times, slowest round and current deadline | Mutable case-local execution state. |
| RoutedAgent | fast/strong callables, decision thresholds, prompt context and last_decision | Fresh per case; completion clients can be shared. |
| LLMAgent context | case_id, prepared brief and optional DataPackage | Cached prompt context for that agent's case. |
| CompletionClient | Model/effort/output limit, shared semaphore and transport configuration | Reusable configuration; each call owns its async HTTP client. |
| DataPackage | facts, read-only SQLite connection, release root and cached _ids | Prepared read-only evidence shared by policies. |
| BudgetLedger | Settled estimates, outstanding reservations and sanitized call records | Shared and locked across paid benchmark workers. |

RoutedAgent.last_decision records case_id, route, reason, elapsed_s, budget_s and fallback. Deadline closure after model fallback preserves the attempted route and adds closure=deadline_close. This telemetry explains dispatch and failure; it is not yet a complete decision journal with evidence calculations and candidate cost breakdowns.

## 5. Costs, package search and action checks

closing_terms resolves buyer plan → supplier standard → fixed case terms → explicit term sheet. Later layers override earlier ones. A valid outgoing sheet carries exactly the negotiable fields, so it cannot override a fixed field.

The routine/leverage script enumerates at most 256 legal non-price combinations across live suppliers and selects up to three validated packages. Each package's unit price is calculated against a shared full-cost ceiling; freight or payment concessions therefore receive a compensating price. A known outside option can lower that ceiling. The narrow first price/payment rule retains its original unit-price anchor. [Routing](ROUTING.md) explains the exact thresholds, plateau detection, RFQ turn selection and semantic exclusions.

The shared routed validator checks live supplier and standing-offer ownership, schema/size, finite and permitted values, known quote defaults, must-haves and alternative comparison. Acceptance uses the supplier's current offer rather than a desired buyer counteroffer. A walk-away ends the whole case, including other supplier threads. Failed latest-quote evidence blocks acceptance until a later readable quote replaces it.

complete_offer fills and snaps values; it is not itself a trust-boundary validator. The legacy LLMAgent and direct NaiveAgent paths do not have the full routed guard. NaiveAgent is useful for a controlled tutorial and benchmark, not as a claim of safe acceptance for arbitrary cases.

Remaining economic limits are explicit: supplier-health estimation is not implemented in the routed acceptance comparison, and checking a cited ID does not verify arbitrary prose arithmetic. Passing no supplier estimate to total_cost makes its relationship-cost component zero; it does not establish that the real relationship cost is zero.

## 6. Timing, parallelism and recovery

The arena gives each supplier thread 60 seconds of buyer response time. Supplier processing is exempt for that supplier's thread but still consumes the wave, and other suppliers keep waiting. Document reads and model preparation after opening consume the buyer window.

For routed decisions, play leaves a five-second send margin and starts wave closure at least 90 seconds before the cap, or earlier when the slowest observed round plus 15 seconds is longer. It checks the closing budget before and after deciding. Current seconds_left from the host remains authoritative for extensions.

CompletionClient budgets provider queueing and response reading together, explicitly sends reasoning effort and a 4,096-token default output cap, and does not retry. Expired async I/O is cancelled and incomplete/late output is rejected. Cancellation is cooperative, not an operating-system hard kill. Routed arena reads use remaining per-phase timeouts of at most five seconds and disable retries; this is not a hard whole-operation bound on pacing, all HTTP phases or supplier POST retries.

play_all and play_waves parallelize cases. Requests to suppliers inside one RFQ remain sequential, so a slow reply can cause other threads to time out even when the buyer decides quickly. The [final-readiness guide](FINAL_READINESS.md) separates the implemented deadline controls from this remaining scheduling problem.

One shared ArenaClient paces requests at nine calls/second against the documented ten-per-key allowance. Independent clients or processes do not share that limiter. Retries of one logical POST retain X-Move-Id. The final runner can resume known sessions, but an isolated case exception is not automatically rescheduled by the same run.

## 7. Evidence and the active dataset

[data](../data/README.md) is the active procurement snapshot as of 2026-09-30. The preserved original lives in materials/starter_kit/data. Read and derive from the active copy; do not modify the archived source to develop the application.

DataPackage exposes SQL rows, citable facts and document text. Its release-aware lookup can overlay release-sunday/facts.json and release CSV IDs, but it does not merge those CSV rows into the base SQLite tables. Base SQL alone can therefore miss release news. The Sunday release is absent from this checkout.

The snapshot contains evidence for 19 practice scenarios; the supplied API lists ten hosted practice cases. There is no active data/cases directory, so DataPackage.cases returns an empty mapping for this data snapshot. The three local demos are packaged separately in src/tacto_negotiator/sim_cases. See [the data guide](DATA_GUIDE.md) for IDs, joins, units, date comparisons and known issues.

## 8. Verification and measured comparisons

The root tests use mock completions, injected transports, local simulator HTTP and controllable clocks. [Local checks](LOCAL_TESTING.md) provides credential-free commands; [Sunday readiness](FINAL_READINESS.md) describes five-wave shared-runner tests and their adverse RFQ cases.

tacto-compete, also available as python -m tacto_negotiator.compete, compares fresh policies against identical seeded private supplier profiles. It defaults to offline strategies. Paid OpenAI calls require explicit --live and --key-file, with reservations before dispatch and retained allowances for unknown usage. Its per-match virtual clocks do not establish shared-wave team throughput.

[Competition results](COMPETITION.md) preserve the completed pre-migration measurements and their limitations. The JSON reports are historical artifacts: old source paths, hashes and commit IDs identify the arena_kit implementation measured at that time. Migration maps that code to tacto_negotiator; it does not turn those reports into new measurements or rerun paid calls.

## 9. Remaining work

| Priority | Change still needed | Verification to add |
| --- | --- | --- |
| First | Service independent RFQ threads during supplier processing, while serializing case closure | Slow-supplier cases with zero unintended timeouts and no duplicate acceptance. |
| First | Bounded recovery of failed case tasks and clearer authentication/capacity errors | Interrupted final sessions resume within their existing timers; revoked access does not become indefinite polling. |
| Next | Prepare case dossiers before opening, using release-aware data | Exact IDs/units/periods, relevant trigger/contract/notes, and preparation outside the supplier clock. |
| Next | Compute and validate evidence claims, not just identifiers | Same-series index windows, correct publication endpoints and no duplicated receipt/invoice aggregation. |
| Next | Estimate supplier health for stabilise/secure_supply | Low/base/high assumptions and complete costs under consistent supplier terms. |
| Next | Qualify medium-semantic cases and real shared-wave capacity | Deadline-compliant grounded actions, provider failures, account quotas and local networking under expected concurrency. |
| Later | Expand the decision journal | Offer/evidence IDs, checked calculations, candidate costs and terminal outcomes without credentials or raw secrets. |

Keep additions in the existing modules until a separate file makes the code easier to understand. New deterministic rules need a focused example and a near miss that still uses interpretation. Preserve the common action guard and the published cost function. [The tutorial](TUTORIAL.md) shows the edit/test/document cycle; [model selection](MODEL_SELECTION.md) explains what the measurements do and do not justify.
