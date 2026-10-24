# Sunday: finish five eight-minute waves reliably

Tacto Negotiator already has a routed deadline controller and shared deterministic action checks. Preserve those controls and prioritize the remaining RFQ scheduling and evidence gaps below. A stronger model does not compensate for missing a thread deadline or leaving a case open at the wave cap.

The recommended initial model is **GPT-6.1 Sol with low reasoning effort**, subject to a representative latency and validity evaluation. The implemented [routed buyer](ROUTING.md) combines tested scripts, Sol and optional Astra with deadline-aware execution and deterministic fallback. [Model selection](MODEL_SELECTION.md) compares the requested candidates and gives the qualification procedure. This recommendation prioritizes hosted API reliability and latency, as requested.

[Local competition](COMPETITION.md) contains the expanded deterministic policy, parallel seeded scenarios and measured OpenAI results. Those paired per-case trials complement the shared-wave runner tests below; their synthetic scores do not predict the final leaderboard.

## What the final changes

The supplied brief schedules the final from Sunday 13:30. The host starts five themed waves; hidden cases appear for every team simultaneously, in a different order per team. Each wave normally lasts 480 seconds. Five waves mean 40 minutes of active negotiation, with host introductions, gaps and possible extensions around them. Do not hard-code the next wave's start time: poll the permitted case list and follow its seconds_left.

Every case has one final session per team. At the cap, an unfinished case closes without a deal and scores its alternative. An invalid deal can score zero. Each wave contributes its mean case score, scaled to 0–100, to a total of up to 500. This makes reliable coverage across cases more valuable than polishing one difficult email while other cases expire. A valid walk-away can still be the best outcome.

The clocks are different:

| Clock or limit | Meaning | Implementation consequence |
| --- | --- | --- |
| 60 seconds per supplier thread | Buyer time since that supplier's reply | Track each live thread independently; model time, document reading and queueing consume the budget. |
| Supplier response up to about a minute | Its processing does not consume that thread's buyer budget | It still consumes wave time; other supplier threads need attention. |
| 480 seconds per wave, unless extended | Shared hard stop for that wave's cases | Reserve time for a validated closing action and follow refreshed seconds_left. |
| 10 open sessions per key | Concurrent cases, not team count | Bound workers; queue extra cases without opening them early. |
| 10 arena calls/second per key | Shared allowance across case workers | Reuse one ArenaClient, which paces at nine; separate processes do not share its limiter. |
| Provider RPM/TPM/concurrency | Separate from arena limits | Bound model calls and queue time as part of each decision deadline. |

Sources: [participant brief](../materials/starter_kit/brief/brief.md), [final and timing API contract](../materials/starter_kit/brief/api.md), [runner](../src/tacto_negotiator/play.py), [client](../src/tacto_negotiator/client.py).

## Implementation order

| Priority | Deliverable | Why it matters at 13:30 | Current state and next change |
| --- | --- | --- | --- |
| P0 · 1 | Absolute decision deadline and deterministic fallback | A slow provider must not consume the entire buyer clock. | Implemented for --agent routed: queue and HTTP share a deadline, output is bounded, expired cooperative I/O is cancelled and fallback is local. Qualify real provider latency; legacy --agent llm remains unchanged. |
| P0 · 2 | Thread scheduling within an RFQ | A single slow supplier can cost the other bids. | Routed decisions receive a budget based on the earliest live thread. Supplier requests remain sequential; concurrent servicing of other RFQ threads is still needed. |
| P0 · 3 | Valid closure on every path | Cheap-looking offers can violate must-haves or lose to the alternative. | Routed scripts, models, fallback and deadline closure share structured checks, including finite values, known defaults and failed latest-quote evidence. Supplier-health estimation and richer evidence validation remain work. |
| P0 · 4 | Wave orchestration, capacity and recovery | All teams start together; request spikes and connection loss are plausible. | Preserve play_waves, per-wave caps, shared client, same-move retry IDs and resume. A task exception is isolated but is not automatically resumed by the same runner. Add bounded recovery and distinguish pre-start 403 from revoked-key 403. |
| P1 · 5 | Prepared evidence and full-cost candidates | Correct arithmetic saves model time and prevents unsupported claims. | Preload data before the final; prepare a case dossier before opening its session. Add release-aware retrieval, unit/date checks and estimated relationship cost. |
| P1 · 6 | Short, grounded negotiation policy | Useful trades and evidence improve results within a small round budget. | Routing now enumerates bounded complete packages, adjusts prices for full-cost trades and selects fast/optional strong work for remaining semantics. Claim arithmetic and supplier-health estimates remain work. |
| P1 · 7 | Local decision journal | Without it, timing and bad decisions are hard to diagnose. | RoutedAgent records case ID, route, reason, elapsed time, budget and fallback, preserving model failures through deadline closure. Add offer IDs, evidence calculations, costs and terminal outcomes, with no credentials. |
| P2 | Learned concession model or elaborate search | Requires trustworthy observations of real suppliers. | Defer until the deterministic buyer and deadline tests pass. The local fixed-concession bot is insufficient training data. |

Do not build separate autonomous buyer agents for sales, finance and management. Those are supplier-side roles in the presentation. A buyer with a small dossier, candidate evaluator, scheduler and one model call per decision is a better starting point.

## A practical time budget

Use these as initial engineering targets, not measured provider guarantees:

1. Prepare shared data and provider configuration before 13:30. Poll for final cases before the first wave starts.
2. When a wave appears, read the case and prepare its local dossier before opening. Fetch quote defaults immediately after opening, with their time charged to the thread.
3. Target model p95 at or below 20 seconds under ten concurrent cases. Bound the complete decision, including provider queueing and retries, to an initial 25-second target when enough thread time remains. Shorten this when a thread already waited.
4. Keep at least five seconds for validation, arena pacing and submission. Derive the actual budget from the earliest live thread deadline and current wave cap, not a fresh 60 seconds on every call.
5. Start closure at least 90 seconds before the cap, or sooner when the slowest observed round plus 15 seconds is longer. The current runner rechecks after each decision as well as before it.
6. If the model fails or the remaining budget is insufficient, use a deterministic legal move or close on the best valid standing offer. Do not start a second provider call merely because the first timed out.

A rough planning example: 20 seconds to decide + 60 seconds for the supplier + 5 seconds of overhead gives an 85-second round. Reserving 90 of 480 seconds leaves room for about four such rounds, before setup costs. This is a budgeting example, not a promise of four rounds for every RFQ. Multiple simultaneously waiting suppliers require scheduling, not simply a faster model.

The routed CompletionClient cancels expired async HTTP work and rejects late output; it does not use a background model-call thread. Only the runner sends a move. Its cancellation is cooperative, so an uncooperative resolver or custom transport is not an operating-system hard deadline. Keep the arena's longer HTTP response timeout separate from the model decision timeout. Legacy arbitrary decide/complete functions cannot be forcibly interrupted by this runner.

## What the local simulations prove

[test_final.py](../tests/test_final.py) adds 25 local scenarios. All model failures and delays are injected; no provider or hosted arena is contacted.

| Scenario | Observed/asserted outcome | Boundary |
| --- | --- | --- |
| Five 480-second waves; three teams; ten concurrent cases each | 150 unique final sessions finish; peak is 30 open globally and ten per team, in every wave; no practice quota consumed. | Controlled virtual time, three demo case types, immediate scripted suppliers. This is not a venue/provider load benchmark. |
| Before start, between waves, exact cap | 403 before start, unrevealed cases unavailable, no new wave during the break, unfinished session becomes wave_cap at its cap. | Tested against the local API. |
| Resume an open or completed final session | Existing session reused; no second session or duplicate acceptance. | Restart/resume is supported; continuous automatic recovery remains work. |
| One policy exception | Other cases finish; the failed case is returned as an exception. | Isolation does not recover the failed case. |
| Host extends an active wave from 480 to 600 seconds | An already-running task observes the refreshed cap through its deadline callable. | Focused runner test observes deadlines; it does not simulate supplier economics. |
| Lost reply after commit; short 429 Retry-After | Retry keeps the same move ID; the simulator applies only one round. | One injected transient fault, not an arbitrary network outage. |
| Concurrent burst through one shared client | Eighteen calls are paced below the configured per-key rate. | Separate from the accelerated 150-session test, where rate limits are lifted to isolate orchestration. |
| Malformed model JSON, timeout, rejected acceptance | Fallback avoids the must-have violation, selects a valid rival when available, otherwise walks away. | Closing checks still lack a supplier-health estimate. |
| Decision enters the final 90-second reserve | Runner closes without sending the now-too-late counter-offer. | Rechecks after return. Routed provider calls additionally use the new bounded adapter. |
| Synthetic 20/45/61/181/481-second first decision | 20 and 45 close successfully; 61 incurs a timeout then recovers; 181 loses the thread; 481 hits wave_cap. | A diagnostic of unbounded legacy/injected decision functions, not the new CompletionClient or a model speed comparison. |
| Three suppliers; three 60-second replies to one bidder | Selected bidder closes successfully while the other two time out. | A diagnostic that exposes the remaining RFQ scheduling gap. A green suite does not mean this is acceptable final behavior. |

The last two tests deliberately preserve adverse cases for unbounded legacy functions and serial supplier requests. The new [routing tests](ROUTING.md) separately verify deadline-aware provider calls, fallback, resumed timers and runner budgets. Their integrated five-wave scenario plays 15 cases with both fast and strong mocked routes; a delayed-provider scenario verifies cancellation and exactly one closing action. Keep both types of coverage: the routed adapter does not make an arbitrary injected blocking function cancellable, and per-thread budgeting does not finish concurrent RFQ scheduling.

The suite does not reproduce hidden supplier reasoning, all competing teams, actual arena capacity, provider outages or realistic negotiation scores. The final's number of teams and cases per wave is not supplied; three teams and ten cases are explicit stress assumptions.

## Run and modify the rehearsal

From the repository root, run the focused suite without inherited credentials:

```sh
TACTO_PYTHON="$PWD/.venv/bin/python"
TACTO_FINAL_TESTS="$PWD/tests/test_final.py"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_PYTHON" -m pytest -q -p no:cacheprovider "$TACTO_FINAL_TESTS"
```

Expected: 25 passed. Virtual time advances across all five waves without waiting 40 minutes. For the full suite and browser checks, use [local testing](LOCAL_TESTING.md).

To watch a real-time local rehearsal, start this server from the repository root. It uses no data download and no model:

```sh
TACTO_SIM="$PWD/.venv/bin/tacto-sim"
cd /tmp
env -i PATH=/usr/bin:/bin "$TACTO_SIM" serve --host 127.0.0.1 --port 8000 \
  --final-rehearsal --rehearsal-waves 5 --rehearsal-cap 480 \
  --rehearsal-wait 60 --rehearsal-break 30
```

In a second terminal, start from the repository root and run a local-only buyer:

```sh
TACTO_PYTHON="$PWD/.venv/bin/python"
cd /tmp
env -i PATH=/usr/bin:/bin "$TACTO_PYTHON" - <<'PY'
import uuid
from tacto_negotiator.agents.naive import NaiveAgent
from tacto_negotiator.client import ArenaClient
from tacto_negotiator.play import closing_move, play_waves

class LocalBuyer(NaiveAgent):
    def decide(self, case, threads):
        move = super().decide(case, threads)
        return closing_move(case, threads) if move["message"].get("action") else move

url = "http://127.0.0.1:8000"
registration = ArenaClient(url, "local-registration")
key = registration.register("rehearsal-" + uuid.uuid4().hex[:8], "sim")["api_key"]
registration.http.close()
client = ArenaClient(url, key)
try:
    results = play_waves(client, LocalBuyer, waves=5, parallel=10)
    print({case: result if isinstance(result, Exception) else result.get("deal")
           for case, result in results.items()})
finally:
    client.http.close()
PY
```

This takes about 43 minutes with the chosen wait and breaks. The server prints the timeline; stop it with Ctrl-C after the buyer returns. The default rehearsal remains two four-minute waves for compatibility. Do not use --no-clock for readiness checks.

To extend the tests step by step:

1. Change the copied demo cases in the concurrency test to add negotiable fields or must-haves. Keep final IDs unique and do not edit the supplied data package.
2. Add a synthetic provider delay or failure via the injected complete/decide function. Name it by delay or failure, never by a real model name unless measured.
3. Add a timing assertion for the behavior you need, then change the scheduler or provider adapter. Ensure fallback wins once and late output cannot send another request.
4. Run the focused suite, then the full suite. Rebuild the atlas so file explanations and dependency edges reflect the change.
5. For any new public file, add its role and rationale to catalog.py. The tree and map then include it automatically.

Before treating the agent as ready, require zero unintended thread timeouts, zero invalid acceptances, no unaccounted cases, successful restart/resume, and every action within both clocks under the chosen delay and failure scenarios. The recorded OpenAI comparison covers synthetic policies; medium-semantic quality, real shared-wave capacity and venue/provider conditions still need qualification. Follow [model selection](MODEL_SELECTION.md).
