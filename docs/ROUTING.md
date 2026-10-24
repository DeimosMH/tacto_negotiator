# Choose scripts, Sol or Astra for each decision

The routed buyer chooses how to make the next decision from the information and time available. It uses tested scripts for supported quantitative decisions; a fast model for ordinary interpretation; and an optional stronger model for consequential complexity when enough time remains. The choice is made again after each supplier reply.

It is the default for tacto play and tacto run; --agent routed selects it explicitly. The naive and llm policies remain available for comparison. The active implementation lives in src/tacto_negotiator/agents; [the beginner tutorial](TUTORIAL.md) explains the surrounding negotiation loop.

## The three implementation blocks

| Block | Files | Responsibility and boundary |
| --- | --- | --- |
| Routing and validation | [routed.py](../src/tacto_negotiator/agents/routed.py), [policy tests](../tests/test_routed.py), [script coverage tests](../tests/test_script_coverage.py) | Select script/fast/strong, enumerate bounded complete packages, prepare the same context for either model, validate every action and choose a deterministic fallback. It does not send arena requests. |
| Deadline-aware model execution | [completion.py](../src/tacto_negotiator/agents/completion.py), [adapter tests](../tests/test_completion.py) | Apply one absolute deadline across concurrency queueing and HTTP response reading. Send effort and output limits to OpenAI Responses; cancel expired work, with no automatic retries. |
| Runner and configuration | [play.py](../src/tacto_negotiator/play.py), [cli.py](../src/tacto_negotiator/cli.py), [integration tests](../tests/test_routing_integration.py) | Pass remaining thread/wave time to the router, use its closing guard, and construct shared completion clients with a separate agent instance for each case. |

These blocks were implemented as separate work assignments. They run as one buyer application; there is no extra coordinator model and no group of competing buyer agents at runtime.

## Follow one decision

```text
Case and current supplier replies
  |
  +-- Closing budget exhausted? --------> deterministic validated closure
  |
  +-- Tested rule with enough data? ----> script
  |     final/agreed offers, missing-term question,
  |     complete package search, concessions or plateau closure
  |
  +-- Material complexity + configured strong model
  |   + enough decision time? ----------> strong model (optional Astra)
  |
  +-- Otherwise ------------------------> fast model (default Sol)
                                            |
                              common validation
                                            |
                           one move returned to the runner

Model error, timeout or rejected output ---> deterministic fallback
```

There is at most one completion request for a decision. The router does not ask Sol to classify a case and then ask Astra to solve it. A model failure goes to local fallback rather than starting another provider request with the remaining seconds.

The code distinguishes a deterministic calculation from a deterministic negotiation policy. Knowing which offer is cheapest does not prove that accepting it immediately is optimal. The scripts operate on public case terms and observed negotiations; they do not inspect the simulator's private floors or predict hidden supplier preferences. Compare their actual outcomes using [the local competition guide](COMPETITION.md), rather than treating script coverage alone as success.

## What each route is for

| Situation | Route | Why |
| --- | --- | --- |
| Decision time is exhausted or the wave is in its closing reserve | Script closure | Another model call would compete with the time needed to submit the result. |
| Every live supplier is agreed/final or has no moves left | Script closure | Compare validated standing offers and the alternative; accept an eligible offer or walk away. |
| A required negotiable quote term is missing from the offer and known defaults | Targeted scripted question, where a move remains | Missing information should be requested rather than invented by a larger model. |
| Routine/leverage case with one supplier, only price/payment negotiation, no stated alternative and no prior buyer move | One scripted opening counter at 90% of the standing unit price, if valid and cheaper under C | Preserves the initial narrow policy; further structured replies can use bounded package search. |
| Complete routine/leverage quote with bounded legal options | Enumerated complete packages | Compare full C, including payment/freight/coverage effects, and submit up to three validated alternatives. |
| A must-have is currently violated but can be repaired through negotiable fields | Explicit repaired package | A cheaper invalid deal is not accepted; the proposed term sheet must satisfy the requirement. |
| Several suppliers have complete current quotes | Full-cost package comparison | Service untried and less-visited bidders before repeatedly favoring one; rank candidates by C rather than unit price. |
| A previous structured offer receives another complete quote | Bounded concession or validated closure | Use observed offers to narrow the price gap; close on sufficient agreement, a small cost gap, a round reserve or a genuine quote plateau. |
| Ordinary reply or semantic choice outside those rules | Fast model | Sol is the initial default, with low effort and a bounded response. |
| Structured evidence conflict, relationship-sensitive goal or several negotiable terms, with enough time | Optional strong model | Extra reasoning may help, but neither novelty nor model self-confidence establishes that it is needed. |

When no supported script applies, the model complexity checks are explicit: category_goal is stabilise or secure_supply, at least four fields are negotiable, or a latest supplier reply reports a contradicted claim. Four fields alone no longer force a model call when a bounded quantitative script can handle them. The strong route is disabled unless a strong completion is configured. Its default minimum remaining budget is 20 seconds within a maximum 25-second decision. These are engineering defaults, not measured provider performance promises. If a hard case has less time, it uses the fast route; if there is no usable model budget, it closes deterministically.

## Supported quantitative scripts

Package search applies only to routine/leverage cases with complete known quote terms and explicit finite option lists. It includes each supplier's standard values alongside the case's permitted options. Search is limited to **256 non-price combinations across the active suppliers in one decision**. If the limit is exceeded, an option list is missing, or the data is unsuitable, normal model routing takes over; the search never silently truncates the legal space and calls its partial result optimal.

Legal supplier standards survive completion unchanged, even when absent from term_options. Candidate prices therefore describe the same terms that reach the wire. The inner search checks cost and must-haves; the chosen message receives full validation once, avoiding hundreds of repeated parses of an unchanged quote.

For enumerated opening packages, the anchor is 90% of the current quote's full cost C. After a structured offer, the next cost ceiling is the midpoint between the cheapest prior proposed package's C and the current quote's C, capped at that current cost. A known alternative can lower the ceiling further. Code solves each package's unit price under the same cost ceiling and verifies the rounded result. These are explicit bargaining policies, not inferred supplier floors. The original narrow first price/payment counter retains its 90%-of-unit-price anchor.

The packages include favorable non-price terms, ranked by their cost at a common reference price, and, when feasible, an alternative retaining the current non-price terms. A third distinct combination can fill the remaining slot. Their final prices compensate for those trade-offs: DAP freight and longer payment may receive a higher unit price than EXW and early payment, while all packages stay under the same buyer-cost ceiling. Offering an identical unit price for both packages would let the supplier choose terms that increase the buyer's total cost. Unit-price rounding can leave small differences below the ceiling. The supplier can consider the complete trades; code does not promise that any package will be accepted.

Must-have repair works through explicit outgoing terms. If lead time is negotiable over 14/28/42 days and the requirement is at most 14 days, every offered package must explicitly say 14. If that requirement is fixed or unavailable in the declared options, the script does not pretend to repair it. Acceptance still checks the current standing supplier offer, not the buyer's desired counter-offer.

RFQs use the same cost function for every bidder. A higher unit price can win when delivered cost is lower. Untried suppliers are considered before suppliers that already received a structured bid; subsequent choices favor fewer bid rounds and then lower candidate C. This improves turn selection but does not implement concurrent requests within an RFQ: the runner's supplier calls are still sequential.

Closure is also conditional. An agreement is considered after all live bidders have been tried or reached a terminal state. A quote plateau requires at least two buyer offers and three consecutive identical complete supplier quotes. A sufficiently small full-cost gap (within 1% of the cheapest previous proposed package) or the final available round can also trigger closure after all bidders have been tried. Every closure runs the shared guard; a plateau against a better alternative ends in a walk-away, not an inferior deal.

These rules defer to a model when the latest reply reports a contradicted claim, the goal requires relationship-health reasoning, quote defaults remain unknown, or prior buyer messages contain a prose-only exchange outside the known clarification template. Current conditional-language checks include subject to, unless, pending approval and similar phrases. **This phrase detection is a conservative heuristic, not proof that arbitrary prose contains no additional conditions.** Novel semantic cases still need representative evaluation.

The route reasons distinguish useful scripted work from emergency closure:

| Reason | Meaning |
| --- | --- |
| simple_opening_counter | Original narrow price/payment anchor. |
| enumerated_opening | Bounded full-term opening packages. |
| must_have_repair | Explicit package satisfies a requirement violated by the current offer. |
| rfq_package_comparison | Select a bidder and complete packages using visit count and full C. |
| bounded_concession | Advance from a previous structured offer without exceeding the current quote's full C. |
| agreed_package / small_cost_gap / round_reserve | Close through the guard after sufficient negotiation evidence. |
| quote_plateau | Repeated complete quotes stopped improving after structured attempts. |
| missing_quote_terms | Request missing information once. |
| terminal_offers / deadline_close | Validated closing policy; report separately from supported quantitative bargaining. |

A model failure preserves fallback=True and its attempted route. Count that as a failure/fallback even if the closing action itself uses deterministic code. The competition report should distinguish this from successful quantitative script coverage.

The missing-term question is asked once per thread by this rule, with more than one move remaining. It asks for the missing fields and a complete term sheet. It does not repeatedly consume rounds asking the same question.

An unreadable latest quote is missing evidence too. A failed routed document read or parse marks the attachment with read_failed and leaves its text absent; subsequent reads of the same cached history skip that marked attachment. The router blocks acceptance and can ask once for supplier quote defaults, even if the explicitly listed negotiable fields appear complete. A later successfully read quote can replace that failed evidence.

The same action checks apply to scripts, either model and fallback. They protect the structured message, term types and values, supplier/standing-offer selection, resolved quote defaults, must-haves and alternative comparison. No acceptance should silently turn an omitted supplier term into the buyer's preferred value. A walk-away ends the entire case, including its other supplier threads.

Acceptance and closing now use acceptable_offer and closing_move in play.py, also used by the legacy model guard and generic deadline closer. An unrecognized index-clause string is rejected; only explicit none means no clause. The simulator formats structured clauses in the parser's supported representation. See the [review fixes and regressions](reviews/FIXES.md).

This does not complete the larger evidence buyer. Claim IDs and numeric terms are checkable, but arbitrary natural-language claims still require better grounding. Full supplier-health estimation, case dossiers and concurrent service of RFQ threads remain further work.

## How the time budget works

The runner supplies an absolute monotonic deadline to decide_timed. RemoteTable refreshes the arena's authoritative per-thread seconds_left, so a resumed session does not receive a fresh minute by mistake. It refreshes an offer when its ID or status changes and excludes closed threads. The budget accounts for the earliest live supplier thread and leaves a five-second send margin, as well as following the wave's current cap. The router also limits its own decision to max_decision_seconds. Time spent preparing context and waiting for a provider slot comes out of the same budget. Initial attachment reading also consumes the supplier window.

Resume and 409 recovery restore offer IDs together with their own terms and include timeout rounds in the remaining-move count. A rejected request is not appended as a successful buyer message. If the current offer cannot be matched to a supplier message or timeout answer, acceptance is blocked. An all-no-bid opening returns its result without calculating an empty thread deadline.

Routed read calls use a per-call timeout no greater than the remaining five-second read allowance, with retries disabled. This covers session/changed-thread metadata and attachments in opening messages, state refreshes and normal supplier replies. A transient metadata-read failure triggers deterministic closure rather than spending another model call on uncertain timing. Attachment read/parse failures leave explicit missing-evidence markers for the routed guard. These are HTTP phase limits: they do not impose a hard total bound on arena pacing, every read phase or supplier POST retries. The longer supplier-response timeout remains unchanged; legacy untimed document errors still propagate normally.

CompletionClient waits for a shared concurrency slot only until that deadline, then runs its HTTP operation with the time still available. It reads the complete response inside the deadline, rejects incomplete responses and cancels timed-out async I/O. It creates no background model-call worker. Only the runner sends arena moves, after the router returns a validated decision. Cancellation is cooperative: an uncooperative resolver or custom transport would require process isolation for a hard operating-system deadline.

The adapter creates an HTTP client for each call and closes it after use. It does not yet pool warmed connections across worker calls. That is a possible optimization after measurements; it does not change the deadline contract.

The arena HTTP timeout is separate. A supplier can take about a minute to answer, and its response time still consumes wave time. Requests to suppliers within one RFQ remain sequential. Budgeting against the earliest thread prevents spending an available window on the wrong model call, but it cannot make a synchronous supplier request service the other threads. See [Sunday readiness](FINAL_READINESS.md).

An injected completion used with RoutedAgent must honor deadline itself. Python cannot safely stop an arbitrary blocking function. The supplied CompletionClient provides that behavior; the legacy --agent llm path still uses its earlier completion interface and is not upgraded by selecting a model name.

## Try the routes locally without an API key

From the repository root, run the policy, transport and integration checks:

```sh
TACTO_PYTHON="$PWD/.venv/bin/python"
TACTO_TESTS="$PWD/tests"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_PYTHON" -m pytest -q -p no:cacheprovider \
  "$TACTO_TESTS/test_routed.py" \
  "$TACTO_TESTS/test_script_coverage.py" \
  "$TACTO_TESTS/test_completion.py" \
  "$TACTO_TESTS/test_routing_integration.py"
```

The completions and HTTP transports are injected. The command clears inherited credentials and does not call the hosted arena or a provider. The integration tests include five 480-second virtual waves with both model routes and a cancelled completion that closes exactly once. For all tests, read-timeout overrides and the larger rehearsal, use [local testing](LOCAL_TESTING.md).

To see routing without a server, start again from the repository root:

```sh
TACTO_PYTHON="$PWD/.venv/bin/python"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 "$TACTO_PYTHON" - <<'PY'
import json
from tacto_negotiator.agents.routed import RoutedAgent
from tacto_negotiator.cost import complete_offer
from tacto_negotiator.sim import load_cases

case = load_cases()["case:S03"]
case["buyer"]["category_goal"] = "secure_supply"  # deliberately require semantic reasoning in this demo
supplier = case["suppliers"][0]
threads = {supplier: [{
    "from": supplier,
    "status": "open",
    "term_sheets": [{
        "term_sheet_id": "offer:local-demo",
        "terms": complete_offer(case, {"unit_price": 230}),
    }],
    "attachments": [],
}]}

def fake_completion(system, user, *, deadline):
    # A fixed response only for demonstrating dispatch. No HTTP or model is used.
    return json.dumps({"supplier_id": supplier, "action": "walk_away"})

for label, strong in [("fast only", None), ("strong enabled", fake_completion)]:
    agent = RoutedAgent(fake_completion, strong=strong)
    agent.decide(case, threads)
    print(label, agent.last_decision)

threads[supplier][0]["status"] = "final_offer"
agent = RoutedAgent(fake_completion)
agent.decide(case, threads)
print("final offer", agent.last_decision)
PY
```

This fixture deliberately gives S03 a relationship-sensitive goal. The first two runs demonstrate fast and optional strong dispatch; the final-offer run demonstrates the existing scripted closing path, whose supplier-health limitation remains documented. The unmodified fully structured routine case can now use package scripts. All completions return a fixed walk-away solely to make the example easy to inspect. The exercise does not compare the negotiation quality of real models.

## Configure the hosted adapter

The tacto CLI uses this implementation by default or with --agent routed. These are process-environment settings consumed by routed_completions_from_env; the factory does not read files. Normal CLI commands load .env from the working directory after argument parsing. Help exits before loading it. Keep local tests on the injected path above; the root .env.example documents configuration without real credentials.

| Variable | Default / meaning |
| --- | --- |
| LLM_PROVIDER | openai; other values are rejected for the routed Responses adapter. |
| LLM_MODEL | gpt-6.1-sol, the fast route. |
| LLM_EFFORT | low, sent explicitly to the API. |
| LLM_STRONG_MODEL | Unset: strong route disabled. Set gpt-6-astra only when access and qualification are established. |
| LLM_STRONG_EFFORT | medium when the strong route is configured. |
| LLM_MAX_CONCURRENT | 10 slots shared by fast and strong calls across case workers. |
| LLM_BASE_URL | https://api.openai.com/v1; any replacement must implement the Responses interface. |
| LLM_API_KEY / OPENAI_API_KEY | Credentials supplied to the process; do not place real values in examples or tests. |

The default output cap is 4,096 tokens. The adapter sends reasoning effort and an output limit; it makes no automatic retry and rejects an incomplete/truncated result. Configure other providers through a deadline-respecting injected completion or a separately tested adapter. An OpenAI-compatible Chat Completions endpoint does not necessarily implement Responses.

These settings select behavior; they do not establish model latency, quota or quality. [Model selection](MODEL_SELECTION.md) explains the qualification procedure and alternative candidates.

## Modify one behavior step by step

1. Find the decision you want to change in routed.py. Add a focused fixture in test_routed.py or test_script_coverage.py that states both the selected route and expected legal action. For a deterministic rule, also add a near-miss case that must continue to the model.
2. Change the smallest rule or constructor setting. For example, raise strong_min_seconds to require more budget before Astra, or narrow the simple price/payment rule. Do not use model-reported confidence as permission to bypass validation.
3. Keep all routes on the same validator. A new scripted offer needs the same permitted terms and must-have checks as model output. A new acceptance must use a current supplier offer and resolved defaults.
4. Add a delayed or malformed completion to the test. Check that fallback happens once, a late result cannot submit another action, and queue time does not create a fresh budget.
5. Run the focused suite above, then the full local suite. Compare route choice, decision duration, fallback rate and valid outcomes before judging any score change.
6. Rebuild the atlas with python3 -B docs/build.py and run its checks. Update this guide when the routing conditions or configuration changes.

RoutedAgent.last_decision records case_id, route, reason, elapsed_s, budget_s and fallback. Logs include the case ID so concurrent decisions can be distinguished. If the runner invokes closing_move after a model fallback in the same case, that attempted-route record is preserved and closure is set to deadline_close. This keeps a timeout visible instead of relabelling the whole turn as a successful script. Use verbose logging or inspect the dictionary in local experiments. Keep raw prompts, supplier text and credentials out of routing telemetry. The complete negotiation journal listed in [remaining architecture work](IMPLEMENTATION.md#9-remaining-work) is still a separate task.
