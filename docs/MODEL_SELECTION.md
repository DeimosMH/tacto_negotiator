# Choose a model for the final

**Initial choice: hosted GPT-6.1 Sol at low reasoning effort, with deterministic Python for costs, constraints, timing and fallback.** This is a provisional engineering choice for the requested reliability/latency priority. It is not a measured winner: no real model API was called, and the local simulator cannot rank negotiation skill. Promote a model only after the qualification below. See [Sunday priorities and simulations](FINAL_READINESS.md).

Use GPT-6 Astra for difficult preparation and analysis before a live session, if it improves those tasks. During the final, use one qualified primary provider; an immediate local fallback has a more predictable response time than sequentially trying several providers. A second hosted provider is useful as a prequalified replacement between decisions, when enough budget remains.

## Requested candidates and their roles

Official model information checked on 2026-10-24. Names here are provider API identifiers, not a guarantee that your account has access. Comparative role recommendations are our inference from the task and documentation, not measured reliability rankings.

| Candidate | Verified information | Recommendation for this system |
| --- | --- | --- |
| GPT-6.1 Sol · gpt-6.1-sol | OpenAI describes near-Astra capability at lower cost; supports low reasoning effort. Chat Completions supports this model without tools; tool calls require Responses. | Primary candidate with short context and low effort. Confirm total-response p95 under concurrent load before deployment. [Official model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| GPT-6 Astra · gpt-6-astra | OpenAI's most capable general model; reasoning effort includes low through max. | Use for difficult dossier analysis or policy development. Use live only if its measured quality gain survives the same deadline budget. [Official model page](https://developers.openai.com/api/docs/models/gpt-6-astra) |
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

A MILP solver becomes useful only if a future case couples many products, capacity constraints and sourcing allocations. The present per-case term-sheet contract can begin with bounded enumeration; no new solver dependency is needed. Embeddings or learned reranking can be considered if deterministic dossier retrieval later misses relevant prose. Do not train a policy against this simulator's fixed 2% concession script and treat the score as evidence of real negotiation ability.

## Adapter changes needed before relying on this choice

The current [llm_from_env](../starter_kit/starter-kit/arena_kit/agents/llm.py) does not implement a deadline-aware model gateway. The source has been inspected; these are implementation gaps, not tasks already completed by the documentation:

1. The OpenAI-compatible branch sends model and messages only. **LLM_EFFORT currently has no effect on that branch.** Wire the selected provider's supported effort parameter explicitly. For GPT-6.1 Sol, use low, not none/minimal. Avoid adding unsupported parameters to every compatible endpoint.
2. Bound generated output as well as elapsed time. Start with a concise email and one JSON object; measure the reasoning/output token allowance needed to finish it without truncation. The Anthropic branch currently allows 16,000 output tokens.
3. A nominal 45-second HTTP timeout is not a whole-decision deadline. Disable implicit SDK retry loops or fit them inside a shared absolute deadline, including queue time and fallback. Handle cancellation and discard late results.
4. Warm reusable client connections, reuse stable prompt context, and limit concurrent provider work. Share one prepared data snapshot; do not rebuild it for each round.
5. Validate model output into a bounded action schema. Check types, finite amounts, allowed fields, live supplier/offer ownership, evidence IDs and actual arithmetic. Keep code-computed cost comparisons authoritative.

The current compatible branch can send plain messages to GPT-6.1 Sol via Chat Completions. If adding model tool calls, use its supported Responses interface; that is an adapter change. Merely changing LLM_MODEL cannot make effort controls, cancellation or tool support appear. [GPT-6.1 Sol capabilities](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

The fallback closing fix in this change checks defaults, must-haves and the alternative. It does not finish the universal guard, supplier-health estimation, provider cancellation or RFQ scheduling. Those remain P0/P1 items in [the readiness plan](FINAL_READINESS.md).

## How to qualify the choice step by step

First complete the local adapter/scheduler tests using injected completions. The existing test suite makes no external calls. Real provider measurements would be a separate evaluation using explicitly configured hosted access, with the arena still local.

1. Freeze representative prompts: opening offer, conflicting evidence, missing quote defaults, final offer, valid alternative, multi-supplier RFQ, stabilise/secure_supply, and a nearly expired decision. Include a known acceptable action and numeric checks for each.
2. Compare the same dossier and candidate packages across models. Use each provider's lowest supported effort that passes the quality checks. Keep the total decision budget identical.
3. Record complete-response p50/p95/p99, invalid JSON, truncated outputs, rejected actions, unsupported claims, retry/fallback rate and cost. Start with at least 100 decisions and repeat under ten concurrent cases; p99 from 100 samples is only a rough indicator.
4. Target p95 at or below 20 seconds with a 25-second total decision budget when the thread has room. Count deadline expirations as failures/fallbacks, including queue wait; do not discard them from the latency distribution. These thresholds are initial engineering targets, not official requirements.
5. Require zero unsafe accepted actions in the fixtures, including malformed responses and better-alternative cases. A guard rejection can be safe operationally while still indicating poor model quality. Measure both.
6. Run five local 480-second waves with injected provider faults and delayed suppliers. Require that no case fails because the selected provider stalls. Preserve a real-time rehearsal before final deployment; virtual tests cannot reveal CPU contention or local Wi-Fi behavior.
7. Select by deadline-compliant, evidence-correct decisions and valid economic outcomes. Use cost as a tie-breaker after reliability and latency. The simulator's numeric score is not a credible cross-model negotiation leaderboard.

Capacity planning example, not a prediction: ten cases requesting one decision every 30 seconds is about 20 requests/minute. At 8,000 input and 1,000 total generated/reasoning tokens per request, that is roughly 180,000 tokens/minute, plus a synchronized opening burst. Check both model and account quotas against measured prompt/token sizes. All teams starting together may affect arena and provider latency, but three local fake teams cannot measure that shared-service load.

If no hosted candidate clears the timing and validation gates, use the deterministic buyer for time-critical moves and spend model time only where a deadline permits it. Reliable closure is the deployment requirement; a preferred model name is not.
