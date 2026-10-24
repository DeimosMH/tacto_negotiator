# Choose scripts, Sol or Astra for each decision

The routed buyer chooses how to make the next decision from the information and time available. It uses a tested script for a narrow, known situation; a fast model for ordinary interpretation; and an optional stronger model for consequential complexity when enough time remains. The choice is made again after each supplier reply.

Run it through --agent routed. The existing naive and llm agents remain available. This guide explains the new implementation; [the beginner tutorial](TUTORIAL.md) explains the surrounding negotiation loop.

## The three implementation blocks

| Block | Files | Responsibility and boundary |
| --- | --- | --- |
| Routing and validation | [routed.py](../starter_kit/starter-kit/arena_kit/agents/routed.py), [policy tests](../starter_kit/starter-kit/tests/test_routed.py) | Select script/fast/strong, prepare the same context for either model, validate the proposed action and choose a deterministic fallback. It does not send arena requests. |
| Deadline-aware model execution | [completion.py](../starter_kit/starter-kit/arena_kit/agents/completion.py), [adapter tests](../starter_kit/starter-kit/tests/test_completion.py) | Apply one absolute deadline across concurrency queueing and HTTP response reading. Send effort and output limits to OpenAI Responses; cancel expired work, with no automatic retries. |
| Runner and configuration | [play.py](../starter_kit/starter-kit/arena_kit/play.py), [cli.py](../starter_kit/starter-kit/arena_kit/cli.py), [integration tests](../starter_kit/starter-kit/tests/test_routing_integration.py) | Pass remaining thread/wave time to the router, use its closing guard, and construct shared completion clients with a separate agent instance for each case. |

These blocks were implemented as separate work assignments. They run as one buyer application; there is no extra coordinator model and no group of competing buyer agents at runtime.

## Follow one decision

```text
Case and current supplier replies
  |
  +-- Closing budget exhausted? --------> deterministic validated closure
  |
  +-- Tested rule with enough data? ----> script
  |     final/agreed offers, missing-term question,
  |     or a narrow routine price/payment counter
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

The code distinguishes a deterministic calculation from a deterministic negotiation policy. Knowing which offer is cheapest does not prove that accepting it immediately is optimal. The simple script has narrow conditions; ordinary semantic decisions continue to the model.

## What each route is for

| Situation | Route | Why |
| --- | --- | --- |
| Decision time is exhausted or the wave is in its closing reserve | Script closure | Another model call would compete with the time needed to submit the result. |
| Every live supplier is agreed/final or has no moves left | Script closure | Compare validated standing offers and the alternative; accept an eligible offer or walk away. |
| A required negotiable quote term is missing from the offer and known defaults | Targeted scripted question, where a move remains | Missing information should be requested rather than invented by a larger model. |
| Routine/leverage case with one supplier, only price/payment negotiation and no prior buyer move | One scripted opening counter at 90% of the standing unit price, if valid and cheaper under C | The anchor is a policy choice, not proof of an optimal or acceptable supplier price. Later turns use normal routing. |
| Ordinary reply or semantic choice outside those rules | Fast model | Sol is the initial default, with low effort and a bounded response. |
| Structured evidence conflict, relationship-sensitive goal or several negotiable terms, with enough time | Optional strong model | Extra reasoning may help, but neither novelty nor model self-confidence establishes that it is needed. |

The current complexity checks are explicit: category_goal is stabilise or secure_supply, at least four fields are negotiable, or a latest supplier reply reports a contradicted claim. The strong route is disabled unless a strong completion is configured. Its default minimum remaining budget is 20 seconds within a maximum 25-second decision. These are engineering defaults, not measured provider performance promises. If a hard case has less time, it uses the fast route; if there is no usable model budget, it closes deterministically.

The missing-term question is asked once per thread by this rule, with more than one move remaining. It asks for the missing fields and a complete term sheet. It does not repeatedly consume rounds asking the same question.

An unreadable latest quote is missing evidence too. A failed routed document read or parse marks the attachment with read_failed and leaves its text absent; subsequent reads of the same cached history skip that marked attachment. The router blocks acceptance and can ask once for supplier quote defaults, even if the explicitly listed negotiable fields appear complete. A later successfully read quote can replace that failed evidence.

The same action checks apply to scripts, either model and fallback. They protect the structured message, term types and values, supplier/standing-offer selection, resolved quote defaults, must-haves and alternative comparison. No acceptance should silently turn an omitted supplier term into the buyer's preferred value. A walk-away ends the entire case, including its other supplier threads.

This does not complete the larger evidence buyer. Claim IDs and numeric terms are checkable, but arbitrary natural-language claims still require better grounding. Full supplier-health estimation, case dossiers and concurrent service of RFQ threads remain further work.

## How the time budget works

The runner supplies an absolute monotonic deadline to decide_timed. RemoteTable refreshes the arena's authoritative per-thread seconds_left, so a resumed session does not receive a fresh minute by mistake. It refreshes an offer when its ID or status changes and excludes closed threads. The budget accounts for the earliest live supplier thread and leaves a five-second send margin, as well as following the wave's current cap. The router also limits its own decision to max_decision_seconds. Time spent preparing context and waiting for a provider slot comes out of the same budget. Initial attachment reading also consumes the supplier window.

Routed read calls use a per-call timeout no greater than the remaining five-second read allowance, with retries disabled. This covers session/changed-thread metadata and attachments in opening messages, state refreshes and normal supplier replies. A transient metadata-read failure triggers deterministic closure rather than spending another model call on uncertain timing. Attachment read/parse failures leave explicit missing-evidence markers for the routed guard. These are HTTP phase limits: they do not impose a hard total bound on arena pacing, every read phase or supplier POST retries. The longer supplier-response timeout remains unchanged; legacy untimed document errors still propagate normally.

CompletionClient waits for a shared concurrency slot only until that deadline, then runs its HTTP operation with the time still available. It reads the complete response inside the deadline, rejects incomplete responses and cancels timed-out async I/O. It creates no background model-call worker. Only the runner sends arena moves, after the router returns a validated decision. Cancellation is cooperative: an uncooperative resolver or custom transport would require process isolation for a hard operating-system deadline.

The adapter creates an HTTP client for each call and closes it after use. It does not yet pool warmed connections across worker calls. That is a possible optimization after measurements; it does not change the deadline contract.

The arena HTTP timeout is separate. A supplier can take about a minute to answer, and its response time still consumes wave time. Requests to suppliers within one RFQ remain sequential. Budgeting against the earliest thread prevents spending an available window on the wrong model call, but it cannot make a synchronous supplier request service the other threads. See [Sunday readiness](FINAL_READINESS.md).

An injected completion used with RoutedAgent must honor deadline itself. Python cannot safely stop an arbitrary blocking function. The supplied CompletionClient provides that behavior; the legacy --agent llm path still uses its earlier completion interface and is not upgraded by selecting a model name.

## Try the routes locally without an API key

From the repository root, run the policy, transport and integration checks:

```sh
TACTO_PYTHON="$PWD/starter_kit/starter-kit/.venv/bin/python"
TACTO_TESTS="$PWD/starter_kit/starter-kit/tests"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_PYTHON" -m pytest -q -p no:cacheprovider \
  "$TACTO_TESTS/test_routed.py" \
  "$TACTO_TESTS/test_completion.py" \
  "$TACTO_TESTS/test_routing_integration.py"
```

The completions and HTTP transports are injected. The command clears inherited credentials and does not call the hosted arena or a provider. The integration tests include five 480-second virtual waves with both model routes and a cancelled completion that closes exactly once. For all tests, read-timeout overrides and the larger rehearsal, use [local testing](LOCAL_TESTING.md).

To see routing without a server, start again from the repository root:

```sh
TACTO_PYTHON="$PWD/starter_kit/starter-kit/.venv/bin/python"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 "$TACTO_PYTHON" - <<'PY'
import json
from arena_kit.agents.routed import RoutedAgent
from arena_kit.cost import complete_offer
from arena_kit.sim import load_cases

case = load_cases()["case:S03"]
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

S03 has several negotiable terms. The first two runs demonstrate fast and optional strong dispatch; the final-offer run demonstrates the scripted closing path. All completions return a fixed walk-away solely to make the example easy to inspect. The exercise does not compare the negotiation quality of real models.

## Configure the hosted adapter

The CLI selects this implementation with --agent routed. These are process-environment settings consumed by routed_completions_from_env; the factory does not read files. The CLI's existing main entry point still loads .env from its working directory, so keep local tests on the injected path above.

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

1. Find the decision you want to change in routed.py. Add a focused fixture in test_routed.py that states both the selected route and expected legal action. For a deterministic rule, also add a near-miss case that must continue to the model.
2. Change the smallest rule or constructor setting. For example, raise strong_min_seconds to require more budget before Astra, or narrow the simple price/payment rule. Do not use model-reported confidence as permission to bypass validation.
3. Keep all routes on the same validator. A new scripted offer needs the same permitted terms and must-have checks as model output. A new acceptance must use a current supplier offer and resolved defaults.
4. Add a delayed or malformed completion to the test. Check that fallback happens once, a late result cannot submit another action, and queue time does not create a fresh budget.
5. Run the focused suite above, then the full local suite. Compare route choice, decision duration, fallback rate and valid outcomes before judging any score change.
6. Rebuild the atlas with python3 -B docs/build.py and run its checks. Update this guide when the routing conditions or configuration changes.

RoutedAgent.last_decision records case_id, route, reason, elapsed_s, budget_s and fallback. Logs include the case ID so concurrent decisions can be distinguished. If the runner invokes closing_move after a model fallback in the same case, that attempted-route record is preserved and closure is set to deadline_close. This keeps a timeout visible instead of relabelling the whole turn as a successful script. Use verbose logging or inspect the dictionary in local experiments. Keep raw prompts, supplier text and credentials out of routing telemetry. The complete negotiation journal proposed in [the implementation plan](IMPLEMENTATION.md) remains a separate task.
