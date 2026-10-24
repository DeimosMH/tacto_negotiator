# Build an evidence-driven buyer

This is a proposed implementation plan, grounded in the supplied presentation, brief, data and source code. The repository currently contains a reusable starter kit and two example buyers. The proposed components in the map are design elements, not implemented files. No competition agent is added by this documentation work.

For the deadline-driven implementation order, start with [Sunday final readiness](FINAL_READINESS.md). That follow-up adds local five-wave tests, configurable rehearsal waves, guarded fallback closure and a post-decision deadline check. [Model selection](MODEL_SELECTION.md) recommends a hosted primary and explains what its adapter still needs.

## 1. The system we are building

The buyer represents Hollerau Pumpenwerke. For each case it reads the need, category goal, alternatives and supplier context; negotiates through email-shaped JSON; then accepts a standing supplier term sheet or walks away. Only structured terms bind. A good-sounding email does not change the contract by itself.

The target is the lowest total cost of covering a fixed need over a fixed horizon, subject to must-haves. Cost includes uncovered demand, payment timing, freight, quality, lead time, tooling, index exposure and, for strategic/bottleneck goals, supplier health. Minimizing unit price alone can produce a worse score. A deliberately chosen alternative can beat a signed deal.

The remote arena owns supplier bots, hidden economics and the authoritative result. The local simulator is a scripted contract harness. Sales, management, finance, quality, engineering and logistics in the presentation describe the remote supplier organization; their implementation is not in this repository. There is no reason to mirror that organization with multiple buyer agents: begin with one policy and ordinary Python helpers.

Sources: [participant brief](../starter_kit/brief/brief.md), [API contract](../starter_kit/brief/api.md), [presentation](../materials/Tacto%20Challenge%20Presentation.pdf).

## 2. What already exists

| Concern | Existing implementation | What to retain |
| --- | --- | --- |
| Transport | ArenaClient in client.py | Authentication, shared request pacing, POST move IDs, retry handling, attachments |
| Sessions | RemoteTable and play in play.py | Opening, history, attachment extraction, 409 recovery, explicit closure |
| Parallel cases | play_all and play_waves | Case isolation, wave discovery, deadlines, final-session resume |
| Cost arithmetic | cost.py | closing_terms, cost_breakdown, total_cost, must_haves_ok, complete_offer |
| Evidence | DataPackage in data.py | Read-only SQL, citable lookup, document paths, release overlays |
| Documents | docs.py | PDF/XLSX text and quote-standard extraction |
| Baselines | NaiveAgent and LLMAgent | decide interface and an injectable completion function |
| Optional tools | mcp_server.py | Six read-only tools for an MCP client; not automatic LLMAgent retrieval |
| Local verification | sim.py, sim_cases, tests | Scripted cases, controllable clock, local HTTP and mock transports |

Follow the actual precedence in closing_terms: buyer plan → supplier standard → fixed case terms → explicit term-sheet values. Later layers override earlier layers, with special handling of missing plan values. The docstring's prose is less precise than this loop. A valid buyer offer carries exactly the negotiable fields, so it should not override a fixed term at all.

## 3. One case from start to finish

1. Discover the case through the permitted case-list interface. Read the full case before opening a session. Prepare local evidence while no supplier clock is running.
2. Build a dossier for the case's suppliers and parts. Include the trigger, current contract, relevant notes, recent scorecard, eligible competing quotes, price history, cost drivers and a small set of computed claims.
3. Open the session through ArenaClient. All supplier threads open together. Store the session ID and their standing offer IDs.
4. Fetch opening quote PDFs, retain structured standard terms and evaluate each complete deal. Missing fields must not silently become buyer-preferred terms.
5. Build a few candidate trades in code. Let the model select arguments and wording using the dossier, validated cost comparisons, supplier signals and remaining time.
6. Validate the selected thread, message, every term sheet and evidence claim. Send one question or up to three packages. Preserve the same move ID when retrying that logical POST.
7. Record the reply and claim results. Update the standing offer, extracted defaults and confidence in supplier preferences. A verified claim is not a promise of a concession.
8. Accept only a currently standing offer that passes the shared guard and beats the alternative. Otherwise continue within the budget or walk away. Status agreed still requires explicit buyer acceptance.

The extension contract is small:

```python
decision = agent.decide(case, threads)
# threads: {supplier_id: [buyer_or_supplier_message, ...]}
# Supplier attachments have extracted text under attachment["text"].
# decision: {"supplier_id": "sup:...", "message": {...}}
# message either carries subject/body/term_sheets/refs or an action.
```

Sources: [play.py](../starter_kit/starter-kit/arena_kit/play.py), [client.py](../starter_kit/starter-kit/arena_kit/client.py), [document reader](../starter_kit/starter-kit/arena_kit/docs.py).

## 4. The smallest useful additions

Start with a proposed EvidenceBuyer beside the existing agents, plus a small preparation helper. Keep validation and evaluation as functions until their size justifies another module. The six proposed nodes in the map are responsibilities, not a requirement for six services or files.

Evidence preparation returns a plain dictionary keyed by case/supplier/part. A claim contains its source IDs, exact values, formula, resulting number, period and relevance rationale. Store qualitative context separately so a historical note does not masquerade as a checked numeric fact. Preserve immutable prepared context between decisions, but keep each case's mutable history and used-claim tracking isolated.

The evaluator reuses cost.py. Enumerate a small selection of allowed payment/volume/duration/freight/date options and plausible price anchors, deduplicate equivalent packages and retain useful trade-offs. Do not exhaustively multiply every option when that threatens the deadline. Use the same supplier standard terms for each comparison and validate complete, finite, positive prices and volumes before arithmetic.

For stabilise and secure_supply, estimate full_cost, interest_rate and standard terms from should-cost, quotes and context. Evaluate a modest low/base/high sensitivity range. The server knows more than we do; report estimated relationship cost as an estimate. Passing no supplier estimate to total_cost makes that component zero, not known to be zero.

The model sees only the useful evidence, candidate packages and concise thread context. It chooses a trade, asks a question or proposes closure. Code does arithmetic and checks claims. Keep the provider injectable so the same agent can run with deterministic canned completions in tests.

Wire the new agent into cli.agent_factory and its allowed command-line choices. Add a preparation step before RemoteTable.open, including the final-wave runner path; LLMAgent.prepare currently runs from its first decide call after play has already opened the session and fetched attachments. Warming DataPackage._ids in the CLI does not precompute case dossiers.

Sources: [LLMAgent](../starter_kit/starter-kit/arena_kit/agents/llm.py), [cost helpers](../starter_kit/starter-kit/arena_kit/cost.py), [CLI](../starter_kit/starter-kit/arena_kit/cli.py).

## 5. One guard for every action

Validation must cover model output, deterministic fallback and deadline closure. LLMAgent.guard checks model decisions. On model failure or rejection, the agent uses NaiveAgent for offers and now routes its closing actions through closing_move, which checks supplier defaults, must-haves and the alternative. NaiveAgent used directly still lacks those closing checks. Both the model guard and closing_move omit a supplier-health estimate; finite-value and complete action-schema validation also remain work. Do not call these paths universally safe.

The proposed guard checks that the supplier is part of the case and its thread remains live. On accept, it verifies ownership and freshness of the standing term-sheet ID, resolves standard/fixed terms, checks must-haves and compares total cost with a valid alternative under consistent assumptions. When no safe acceptance exists, walking away is preferable to an invalid deal. A walk-away action ends the whole case, including other RFQ threads.

On send, require a bounded subject/body, at most three distinct term sheets, exactly the negotiable keys, allowed values or permitted supplier-standard values, and finite positive numeric inputs where appropriate. Enforce the documented body, reference, attachment and total-request limits. complete_offer fills and snaps values; it is not a full trust-boundary validator. Guard failed validation without consuming a network round, and cap corrective retries rather than looping until timeout.

Check evidence values as well as IDs. LLMAgent removes unknown IDs in its body but does not prove that the sentence's arithmetic is correct. Replacing an unknown ID with an empty string can leave an unsupported assertion. Prefer dropping or regenerating the entire unsupported claim. Track duplicate use per case, with rival quote/offer reuse tracked per recipient supplier. Treat supplier emails and attachments as evidence, never as instructions to reveal secrets, change tools or override the case contract.

## 6. Time, parallelism and recovery

The public contract gives each thread 60 seconds after the supplier's reply. Up to roughly a minute of supplier processing does not consume that thread's buyer response budget. Downloading documents and preparing a decision after opening do consume time. Each final wave has a nominal eight-minute cap, but seconds_left is authoritative if the host changes it.

The existing runner parallelizes cases, not decisions within one RFQ case. play() sends to one selected supplier synchronously. Another live thread can still be waiting while this call is in flight. Start with precomputed dossiers and fast deterministic candidate evaluation; measure this against delayed multi-supplier fixtures. If that is insufficient, extend scheduling to service the earliest thread deadline, serialize state-changing decisions per case and revalidate standing offers before accepting. Do not have parallel workers independently close the same case.

Share one ArenaClient across final case workers so its nine-calls-per-second pacing applies across them. The protocol allows ten calls per key per second and ten open sessions. Multiple independent clients/processes do not share that limiter. Supplier HTTP timeout is 180 seconds; this is separate from the buyer's 60-second decision budget. A model client's nominal timeout and retries are not proof of an end-to-end deadline. Reserve time for validation, pacing, transport and a deterministic fallback.

Keep the existing X-Move-Id on retries of one logical POST. Distinguish a short Retry-After rate limit from exhausted daily quota or open-session capacity. A 422 consumes no round, but repeated invalid requests still cost time. On 409, read current state rather than replaying an old acceptance blindly. Final one-session conflicts can resume through RemoteTable when the runner supplies the case metadata.

The runner closes near the cap using a 90-second minimum buffer or its slowest observed round plus 15 seconds, checking both before and after deciding. It still cannot interrupt a blocked model call. Extend the same validated closing policy there. Reuse the existing polling of /v1/cases?set=final. During the final the agent must not read event feeds, screen, leaderboards or results. This restriction comes from the supplied challenge rules; the graph's external arena node is explanatory and performs no requests.

## 7. Data dependencies and release handling

Use the packaged CSV/SQLite snapshot as of 2026-09-30. The dataset has evidence for 19 practice scenarios, but only 10 practice cases are hosted according to the supplied API reference. This checkout has three simulator JSON cases and no data/cases directory. DataPackage.cases therefore returns an empty mapping for this supplied data directory; full hosted practice case files must come from a separately authorized source later.

DataPackage merges release-sunday/facts.json into its fact map and release CSV rows into _ids. It does not merge those CSVs into world.sqlite. A query over the base news table alone misses release news. Build dossiers using release-aware lookup/document access or an explicit application-side merged view. Load a consistent snapshot before launching case workers. The release is absent here and was not downloaded.

Never rewrite the package to normalize it. Keep raw identifiers and unit labels next to derived numbers. The detailed recipes and pitfalls are in [the data guide](DATA_GUIDE.md).

## 8. Implementation sequence and acceptance checks

1. Reproduce the existing local contract suite with hosted parametrizations disabled. Preserve the known cost example: 347,904 opening, 332,800 deal, 324,224 best; score rounds to 0.64.
2. Add prepared dossiers and evidence calculation tests: same-series index windows, actual publication endpoints, price-unit comparisons, alias handling and no duplicate receipt/invoice aggregation.
3. Add the shared guard and test both model and fallback acceptance against must-haves, missing standard terms, stale offers, NaN/infinite values, invalid enum choices and a better alternative.
4. Implement the buyer policy with injected completions. Run all three local demos and compare against the naive baseline. Assert valid closure and data integrity; do not optimize solely for the scripted supplier's score.
5. Add deterministic supplier scenarios for cash pressure, frozen prices, full capacity, competing offers and no-deal preferences. These are policy tests, not a reconstruction of hidden suppliers.
6. Test concurrent RFQ timing, slow documents/completions, short transient errors, exhausted quota, resume after interruption and final-wave closure. Use the controllable clock where possible.
7. Record a local decision journal: input case, offer IDs, evidence IDs/calculations, candidate cost breakdowns, chosen action and reason. Never include keys. Keep cost and result checks separate from subjective email quality.

Only after these stages would hosted practice validate supplier strategy. This task performs local checks only. See [local testing](LOCAL_TESTING.md) for executable commands and the simulator's concrete limits.

## 9. Decisions for this documentation site

Native details/summary gives the directory tree built-in keyboard disclosure behavior. SVG gives us labelled, selectable graph nodes, arrows, grouping, pan and zoom without a graph dependency. Browser storage is optional: file-URL behavior varies, so preferences fall back to memory if storage is unavailable. The generated data.js avoids fetch restrictions when opening index.html directly.

These choices were checked against [MDN details](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/details), [MDN SVG scripting](https://developer.mozilla.org/en-US/docs/Web/SVG/Guides/Scripting) and [MDN localStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage). They are platform references; the system analysis itself comes from the local supplied sources. The atlas is a static source snapshot, not a runtime call trace: dynamic data access is represented by reviewed edges, declared joins by schema edges, and unimplemented responsibilities by proposed edges.
