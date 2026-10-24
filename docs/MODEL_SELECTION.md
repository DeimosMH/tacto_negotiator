# Choose a model for the final

**Initial choice: deterministic Python for quantitative decisions, with hosted GPT-6.1 Sol at low reasoning effort for unresolved semantics and optional Astra for qualified complex decisions.** Sol remains a provisional engineering choice for the requested reliability/latency priority. The paid OpenAI pilot confirms that the adapter can complete real requests; it does not establish a model winner. Use [competition method and results](COMPETITION.md) as the authoritative record of subsequent measurements, alongside [Sunday priorities and simulations](FINAL_READINESS.md).

The implemented [routed buyer](ROUTING.md) handles known quantitative choices with bounded scripts, uses Sol as its default completion and can choose Astra for structured complexity when enough budget remains. Astra is disabled until configured. Each decision makes at most one completion request; failures use a local fallback. Use Astra for difficult preparation before a live session as well, if evaluation shows it improves those tasks.

The diagnostic pilot made **26 paid completion calls**, all completed by the provider. One Luna decision needed the common guard's fallback. Provider completion and an acceptable buyer action are separate measurements. The adjusted conservative cost estimate is **$0.198582125**, pricing every input token at the default 30-minute cache-write rate and output at the standard rate; this is not a billing reconciliation. The original $0.1701888 estimate is retained in [the pilot report](results/pilot.json) for traceability. Cache writes can cost more than ordinary input, even without explicit cache configuration. [Official prompt-cache pricing](https://developers.openai.com/api/docs/guides/prompt-caching).

The pilot ran before simulator fixes, so its negotiation scores are diagnostic and should not be compared with the corrected broader run. That broader paired comparison is pending. This small pilot supplies no reliable cross-model or cross-provider ranking.

## Requested candidates and their roles

Official model information checked on 2026-10-24. Names here are provider API identifiers, not a guarantee that your account has access. Comparative role recommendations are our inference from the task and documentation, not measured reliability rankings.

| Candidate | Verified information | Recommendation for this system |
| --- | --- | --- |
| GPT-6.1 Sol · gpt-6.1-sol | OpenAI describes near-Astra capability at lower cost; supports low reasoning effort. Chat Completions supports this model without tools; tool calls require Responses. | Primary candidate with short context and low effort. Confirm total-response p95 under concurrent load before deployment. [Official model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| GPT-6 Astra · gpt-6-astra | OpenAI's most capable general model; reasoning effort includes low through max. | Use for difficult dossier analysis or policy development. Use live only if its measured quality gain survives the same deadline budget. [Official model page](https://developers.openai.com/api/docs/models/gpt-6-astra) |
| GPT-6 Luna · gpt-6-luna | OpenAI's model for focused, high-volume work; supports low reasoning effort and Responses. | Inexpensive comparison candidate. Retain common validation and measure complete decisions, because a completed response may still need fallback. [Official model page](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| Claude Opus · claude-opus-5-5 | Current Opus is 5.5, with always-on adaptive thinking; Anthropic labels its relative latency moderate. | Quality challenger for hard cases; do not assume it fits the buyer timer merely because it is capable. [Official model comparison](https://platform.claude.com/docs/en/models/overview) |
| Claude Mythos · Mythos 5.1 | Access is limited to verified organizations through Anthropic's verification programs. | Exclude from the default final plan unless access and the actual API deployment are already confirmed and tested. [Official Mythos page](https://www.anthropic.com/claude/mythos) |
| GLM-5.3-Flash · glm-5.3-flash | Official Z.ai API model; thinking cannot be disabled. FlashX is a separate model/endpoint option. | Hosted challenger. Test JSON compliance, evidence accuracy, thinking budget and account concurrency. The word Flash is not a wall-clock SLA. [Official GLM guide](https://docs.z.ai/guides/vlm/glm-5.3-flash) |
| Claude Sonnet · claude-sonnet-5-5 | Anthropic positions it for speed and intelligence, with fast relative latency. | Additional candidate for a provider-diverse replacement if it beats Sol on the same workload. [Official model comparison](https://platform.claude.com/docs/en/models/overview) |

Do not infer cross-provider speed from published relative labels or token-generation throughput. Time to a complete valid decision includes queueing, input processing, reasoning, output and retries. Availability and account-level quotas can outweigh small differences in model capability. OpenAI also recommends comparing candidates on representative tasks: [model selection guide](https://developers.openai.com/api/docs/guides/model-selection).

## What should use no LLM

| Responsibility | First implementation | Why |
| --- | --- | --- |
| Evidence lookup | Read-only SQLite, DataPackage.lookup, exact supplier/part/period filters | The package already supplies structured IDs and tables. Exact joins are easier to audit than generated retrieval guesses. |
| Cost and constraints | cost.py plus explicit finite-value, unit and allowed-option checks | Arithmetic and must-haves should give the same answer regardless of provider wording. |
| Candidate packages | Bounded enumeration of allowed terms and price anchors; remove dominated candidates | Small discrete choices need no trained model. A dominated package costs more without improving a relevant trade-off. |
| Wave/thread control | A deterministic state machine with monotonic deadlines, bounded queues and one case-level closing decision | Timing must remain predictable during a provider outage. |
| Fallback | Existing legal offer policy plus validated closing_move, extended with full-cost checks | Still runs when model access disappears. NaiveAgent alone is not a safe closing policy. |
| Supplier-response prediction | Initially simple observed concession/status tracking | There is too little representative labeled interaction data here to justify supervised learning or reinforcement learning. |

A MILP solver becomes useful only if a future case couples many products, capacity constraints and sourcing allocations. The present per-case term-sheet contract uses bounded enumeration; no new solver dependency is needed. Embeddings or learned reranking can be considered if deterministic dossier retrieval later misses relevant prose. The basic simulator's fixed 2% concession script and the competition harness's varied private profiles remain synthetic supplier behavior. Their scores test policy mechanics and cannot establish real negotiation ability.

## Implemented adapter and remaining qualification

The --agent routed path uses [CompletionClient](../starter_kit/starter-kit/arena_kit/agents/completion.py). It sends requests through OpenAI Responses, explicitly sets reasoning effort and a default 4,096-token output cap, and includes shared-provider queue time and HTTP reading in the supplied absolute deadline. It performs no automatic retries. Timed-out async I/O is cancelled and incomplete or late output is rejected; RoutedAgent then uses local fallback.

Fast and strong calls share one concurrency limit. A fresh HTTP client is created and closed for each call, so persistent connection warming remains an optimization to evaluate. Cancellation is cooperative I/O rather than a hard operating-system kill; an uncooperative resolver or injected transport requires stronger isolation. The router applies common structured-action checks, including finite values, permitted terms and guarded acceptance.

The legacy --agent llm path still uses [llm_from_env](../starter_kit/starter-kit/arena_kit/agents/llm.py): **LLM_EFFORT has no effect on its OpenAI-compatible branch**, its nominal 45-second timeout does not cover an entire decision, and the Anthropic branch allows 16,000 output tokens. Choose the routed path to use the new adapter. Changing only LLM_MODEL does not change those legacy behaviors.

For GPT-6.1 Sol use a supported effort such as low, not none/minimal. The routed adapter uses Responses for plain structured decision text; it does not add model tool calls. [GPT-6.1 Sol capabilities](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

Representative latency, output-budget adequacy and model quality still need qualification beyond the pilot. Complete dossier preparation, evidence-claim arithmetic, supplier-health estimation and concurrent RFQ scheduling also remain work; see [the readiness plan](FINAL_READINESS.md).

## How to qualify the choice step by step

Start with the local [routing and adapter tests](ROUTING.md), which use injected completions and transports. They make no external calls. The [competition CLI](COMPETITION.md) keeps suppliers local and requires explicit live opt-in for paid OpenAI measurements. Other providers have not been measured here.

1. Freeze representative prompts: opening offer, conflicting evidence, missing quote defaults, final offer, valid alternative, multi-supplier RFQ, stabilise/secure_supply, and a nearly expired decision. Include a known acceptable action and numeric checks for each.
2. Compare the same dossier and candidate packages across models. Use each provider's lowest supported effort that passes the quality checks. Keep the total decision budget identical.
3. Record complete-response p50/p95/p99, invalid JSON, truncated outputs, rejected actions, unsupported claims, retry/fallback rate and cost. Start with at least 100 decisions and repeat under ten concurrent cases; p99 from 100 samples is only a rough indicator.
4. Target p95 at or below 20 seconds with a 25-second total decision budget when the thread has room. Count deadline expirations as failures/fallbacks, including queue wait; do not discard them from the latency distribution. These thresholds are initial engineering targets, not official requirements.
5. Require zero unsafe accepted actions in the fixtures, including malformed responses and better-alternative cases. A guard rejection can be safe operationally while still indicating poor model quality. Measure both.
6. Run five local 480-second waves with injected provider faults and delayed suppliers. Require that no case fails because the selected provider stalls. The paired competition harness gives each case its own virtual clock and repeats the same scenario economics across waves; it excludes worker queue wait. Preserve a separate real-time team rehearsal before final deployment to test shared wave capacity, CPU contention and local network behavior.
7. Select by deadline-compliant, evidence-correct decisions and valid economic outcomes. Use cost as a tie-breaker after reliability and latency. The simulator's numeric score is not a credible cross-model negotiation leaderboard.

Capacity planning example, not a prediction: ten cases requesting one decision every 30 seconds is about 20 requests/minute. At 8,000 input and 1,000 total generated/reasoning tokens per request, that is roughly 180,000 tokens/minute, plus a synchronized opening burst. Check both model and account quotas against measured prompt/token sizes. All teams starting together may affect arena and provider latency, but three local fake teams cannot measure that shared-service load.

If no hosted candidate clears the timing and validation gates, use the deterministic buyer for time-critical moves and spend model time only where a deadline permits it. Reliable closure is the deployment requirement; a preferred model name is not.
