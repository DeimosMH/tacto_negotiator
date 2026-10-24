# Architecture review, round 2

- **Commit:** `5dea00a` (hosted v15). The package is 6,410 lines in `src/tacto_negotiator`. 333 tests pass in 29 s.
- **Scope:** module boundaries, where policy lives, state, concurrency, error handling, testability, configuration and how the simulators are coupled to production code. Individual bugs are out of scope unless an architectural problem causes them.
- **Method:**
  - I extracted an AST import graph and per-function line and branch counts with a script kept in `/tmp`.
  - I read `play.py`, `agents/routed.py`, `agents/llm.py`, `agents/levers.py`, the structure of `agents/facts.py` and `competition._match`.
  - I mined `git log` for commits that had to change several places at once.
  - One offline probe (below) checks how exceptions travel. It made no network or model calls.
  - I compared the design with negmas (`SAOMechanism`, `BOANegotiator` built from `negmas.gb.components`) and negmas-llm (`LLMMetaNegotiator`, the `pablove_components` slots).
- **Earlier reviews:** see "Status of earlier findings" at the end.

## Dependency and flow sketch

```
cli / compete / web / mcp
   |                \
   v                 v
play.py  ---------------------------------------------+  competition._match  (virtual-time copy of the play() loop)
 |  RemoteTable (transport + 409/resume)               |      | uses play constants, closing_move, close_before_lapse
 |  thread_events / fetch_documents (state repair)     |      v
 |  play() loop: timers, lapses, wave cap, concurrent  |   CompetitionSim <- hosted_like (replica) -- imports agents.llm.ID_RE
 |  POLICY: ANCHOR_BY_GOAL, GAP_STEP, HOLD_STEP,       |                     \-- evaluation.ClaimChecker
 |          ask_limit, opening_price_cap/floor,        |
 |          price_within, acceptable_offer,            |
 |          closing_move, close_before_lapse           |
 ^  (15 names imported)                                |
 |                                                     |
agents/routed.py  RoutedAgent (1,009 lines)            |
 |  _script -> _structured_bargain -> _packages        |
 |  validate (schema + policy + sheet generation + prose rewriting)
 |  decide_timed: script | fast | strong -> except Exception -> fallback chain
 |-- agents/llm.py   LLMAgent used as "context" (prepare/render/SYSTEM/ID_RE); legacy guard()
 |-- agents/facts.py FactBank --> evaluation.ClaimChecker, play
 |-- agents/levers.py         --> evaluation.ClaimChecker, play
 v
cost.py (C, must-haves, health floor; reads case["_supplier_estimates"])
```

The graph has no import cycle, but the dependencies point the wrong way. Agents import strategy from the runner, and the production agents import the offline evaluator (`evaluation.ClaimChecker`).

Measured hot spots, as (lines, branch nodes):

| function | lines | branches |
|---|---|---|
| `play.play` | 217 | 86 |
| `routed.validate` | 115 | 84 |
| `routed._structured_bargain` | 128 | 81 |
| `competition._match` | 150 | 58 |
| `routed._grounded_body` | 69 | 51 |
| `routed.decide_timed` | 75 | 35 |

## Findings, ranked by risk

### A1. High: broad excepts turn code bugs into negotiation moves, and the trace blames the model

**Where:**
- `routed.validate` (`routed.py:211`) returns `None` on `KeyError`, `TypeError`, `ValueError`, `OverflowError` or `AttributeError` anywhere in its body. That body now includes the 400+ lines of fact pacing and prose filtering added in `7bc585f` and `8a4ab19`.
- `decide_timed` (`routed.py:989`) catches every `Exception` and runs `closing_move` plus repairs.
- `facts.py` has six `except Exception` blocks that hide failures.

**Structure:** "this move is infeasible", "the model wrote garbage" and "our code raised" all collapse into the same `None`. That `None` then changes the route.

**Cost:** a probe on `case:S01` replaced `_grounded_body` with a function that raises `KeyError`:

| `_grounded_body` | route | reason | fallback | model calls | sent action |
|---|---|---|---|---|---|
| working | script | `simple_opening_counter` | False | 0 | counter |
| raises `KeyError` | fast | `TimeoutError` | True | 1 | **accept `offer:1`** |

A bug in a text post-processor stops the scripted counter, spends a model call, and accepts the untouched opening, which scores 0.

The trace records only the model's error (here a timeout). With a working model it would say `model action rejected`. That is the label behind 8 of 11 v4 fallbacks (03 F3), so code defects cannot be told apart from model rejections in the hosted logs today.

**Recommendation:**
- **[now]** Add `log.exception(...)` in the `except` of `validate`. In `decide_timed`, store `traceback.format_exception_only` in `last_decision["error"]` when the error is not one the code raises itself. This is logging only and changes no behaviour.
- **[now, small]** In `validate`, put `_paced_body` and `_grounded_body` in their own `try`. On failure, fall back to `_strip_wasted_claims(body)` instead of dropping the move. A text filter should never veto a decision.
- **[later]** Return a reason from validation (see A4) and let only `TimeoutError` and provider errors reach the model-failure branch.

### A2. High: the replica evaluates a hand-copied runner, not `play()`

**Where:** `competition._match` (`competition.py:377-526`) re-implements the `play()` loop (`play.py:448-664`) in virtual time. It contains six "as play()" comments covering idle-thread lapses, `ending()`, the late-decision skip, concurrent RFQ moves and `agent.unavailable`.

**Cost:**
- Four commits had to edit both loops: `6e41c4d`, `8adcbb6`, `9fc5753` (play +97/-, competition +61/-) and `b160ad3` (play +109, competition +26) for FLOW F3.
- The copies have already drifted:
  - `_match` decides `ending()` from simulator internals (`timeouts >= 2`, `round`). `play()` uses its own `lapses` counter inferred from timers.
  - `_match` has no `reread_skipped_documents`, no failed timer refresh (`thread_deadlines` returning `None`) and no 409, `missed()` or resume path.
  - Those `play()` paths are exactly where hosted P01, P10 and P08 lost points (comments at `play.py:126` and `play.py:224`). The replica cannot see regressions there.
- Every replica score in `docs/results/` therefore measures the agent under a runner that is similar to, but not the same as, the one used in the final.

**Recommendation:**
- **[now]** Do not touch either loop before the final. Treat replica deltas on runner or timing changes as indicative only.
- **[later]** Run `play()` itself against `CompetitionSim` through a `Table` adapter with an injected clock and sleep. The loop already takes `table` and `agent` by duck typing. Alternatively, extract a pure step function `next_step(state, now) -> Decide | Wait | Close` that both loops call. negmas makes this split by design: one `SAOMechanism` drives every negotiator, whether the run is real or simulated.

### A3. High: no single home for the acceptance rule, the concession schedule or the opening anchor

**Acceptance.** The rule is spread over about 12 sites with three different tolerances:
- `play.acceptable_offer`, `play.closing_move` and `play.close_before_lapse`
- the accept and walk-away branches of `validate` (`routed.py:109-123`)
- `_premature`, at 1.005 times our last ask
- `small_cost_gap`, at 1.002
- `round_reserve`, `agreed_package` and `quote_plateau` in `_structured_bargain`
- `named_floor` and `near_close_answered` in `_script`, together with `_before_accept` and `levers.near_close_sheet`
- the repair rules for open offers and walk-aways in `decide_timed` (`routed.py:996-1006`)
- `llm.guard`
- the prompt line "Accept an open offer only when it meets your last ask"

What this cost:
- 03 F1: the `_premature` veto zeroed P02 in v4.
- FLOW F10: the veto can end in the accept it vetoed.
- BLOAT #1: three acceptance checks disagree.

Each fix landed at one site. None of them could be checked against "the" rule, because there is no such function.

**Concession.** `play.ask_limit` sets the limit. `validate` then applies an ordered pipeline of about 10 rewrites:
1. reprice to the limit
2. firm-opening floor
3. `_off_repeat`
4. re-clamp to the limit
5. at least half a step (×1.5)
6. `_limit_sheet`
7. equal-cost filter (1.003)
8. `_clause_sheet`
9. `_toward_sheets`
10. credibility filter (0.9 × the top sheet)

FLOW F2 was purely an ordering bug in this pipeline: `_off_repeat` ran after the clamp. It was fixed by adding step 4.

**Opening anchor.** `ANCHOR_BY_GOAL` is read in three places:
- `play.opening_price_cap`
- `_structured_bargain` (`routed.py:778-780`), which recomputes `max(round(price*(1-anchor)), opening_price_cap(...))` with its own default of 0.15
- `simple_opening_counter` (`routed.py:908`)

`bb1635e` ("Open secure_supply 7 % below") had to edit both `play.py` and `routed.py`.

**Recommendation:**
- **[now, optional]** Name the inline tolerances: 1.005, 1.003 and 1.002 for "meets our ask", 0.997 and 0.9 for the sheet filters, and 0.5 and 1.5 for the minimum move. Keep their values unchanged. This is mechanical and covered by the tests.
- **[later]** Split along BOA lines:
  - `Offering`: `ask_limit` plus package search plus the rewrite pipeline as one ordered, documented function.
  - `Acceptance`: one `decide_accept(case, threads, sid, proposed) -> accept | near_close | counter | hold`, with a reason. Every path calls it, including `llm.guard` and the fallback.
  - `Validation`: feasibility only (see A4).

  negmas puts these in separate components that a `BOANegotiator` composes (for example `GTimeDependentOffering`, `GACNext` and `GACTime`). Each one can be tested on its own.

### A4. Medium-high: `validate` is a validator, a policy and a writer at once

**Where:** `routed.py:98-212` (84 branch nodes). It is called 9 times inside the agent, with `reprice=False` switching off half of it.

**Structure:**
- Besides checking schema, liveness, options and must-haves, `validate` makes policy decisions: walk-away rules, the ban on countering a final offer, and the minimum move.
- It generates new sheets: `_clause_sheet`, `_toward_sheets` and `_limit_sheet` can add up to 3 sheets the caller never proposed.
- It rewrites the body: `_paced_body` and `_grounded_body`.
- So a "validated" script move is not the script's move. Failure is a bare `None` with no reason.

**Cost:**
- 03's open question #1 is still unanswered: the hosted JSONs cannot say why a model reply was rejected.
- BLOAT #3: each candidate runs the full pipeline.
- A1: text code can veto decisions.
- `_structured_bargain` (128 lines, 81 branches) depends on `validate` adding sheets, so neither can be read alone.

**Recommendation:**
- **[later]** Split it into three parts:
  1. `check(move) -> list[reason]`: pure, no rewriting.
  2. `shape(move)`: the Offering pipeline from A3.
  3. `write(body, sheets)`: facts and grounding.

  This mirrors negmas-llm, where `LLMOffering` snaps an outcome and falls back to the aspiration policy, and `LLMValidation` may rewrite the words but never the decision.
- **[now]** Nothing beyond A1.

### A5. Medium: two LLM agents with two guards; the routed agent borrows the legacy one as "context"

**Where:**
- `llm.LLMAgent.guard` (`llm.py:266`) is still a separate gate.
  - It lets `walk_away` through unchecked; FLOW F5 was fixed only in `validate`.
  - It does not block countering a final offer (F4 fixed only in `validate`).
  - It applies no `ask_limit`.
- `RoutedAgent` builds `LLMAgent(None, package)` only to reach `prepare`, `render`, `fact` and `brief`. Then:
  - `decide_timed` writes `context.brief` and `context.case_id` from outside (`routed.py:956-958`).
  - `_grounded_body` reads them back through `getattr`.

**Cost:**
- Every validator fix since `7b16473` has had to ask "and llm.guard?". BLOAT #1 found that they already disagree.
- `cli --agent llm` remains a supported final entry point with weaker safety.

**Recommendation:**
- **[now]** Run the final only with `--agent routed`, which is the default. Do not offer `llm` as a fallback during the final.
- **[later]** Move `prepare`, `render` and `SYSTEM` into a `prompt.py` that both agents use. Then either route `LLMAgent` through the shared validator or delete it, keeping `NaiveAgent` as the baseline.

### A6. Medium: hidden state passes between runner, agent and cost model

**Where:**
- **Hidden keys on `case`.** `case["_supplier_estimates"]` is written by `RoutedAgent._prepare` and read by `cost.decision_cost` and `cheapest_price` (`cost.py:263,274`) and by `llm.render`. `case["_index_falls"]` is written by `facts.py:151`.
- **Runner-set attribute.** `agent.unavailable` is assigned by the runner in `play.py:540,580` and `competition.py:409,483`, guarded by `hasattr`. It is read in four places in `routed.py`.
- **Mutated thread events.** The runner and readers change thread events in place:
  - `mark_lapse` writes `rounds_used`.
  - `fetch_documents` and `reread_skipped_documents` write `text`, `read_failed`, `skipped` and `retried`.
- **Duck-typed hooks.** `play()` discovers its hooks through `getattr`: `decide_timed`, `closing_move`, `concurrent_move` and `table.thread_deadlines`.

**Cost:**
- The same `(case, sheet)` costs different amounts depending on whether some agent method already ran `_prepare`. Any path that bypasses it is silently priced without the supplier-health term. Examples are `play.closing_move` called directly, `close_before_lapse`, or a test.
- `hosted_like.py:264` has to rebuild a case with the private key to score correctly.
- The `unavailable` set is an implicit parameter, and its lifetime depends on the loop: it stays stale after `send_all` until the next refresh.

**Recommendation:**
- **[later]** Pass a `DecisionContext(case, threads, now, deadline, unavailable, estimates)` value explicitly, and keep `case` read-only.
- **[now]** Nothing. The current paths call `_prepare` first, and the tests cover them.

**Concurrency is sound for its purpose.** Each case gets its own thread and agent. Pool threads in `send_all` do only HTTP and do not touch `case` or `threads`. The arena pacing lock is shared (`client.py:14`, 9 calls per second). `DataPackage` has an `RLock` and is indexed before agents share it. One semaphore covers fast and strong, sized `LLM_MAX_CONCURRENT=10`, which equals `--parallel 10` and so never binds.

The real concurrency cost is that `with ThreadPoolExecutor` in `send_all` blocks the case loop until the slowest reply (FLOW F11). That is a known issue, not new.

### A7. Medium: the replica grades claims with the agent's own checker

**Where:**
- `facts.FactBank._verified` and `levers.order_path_sentence` pre-check every planned claim with `evaluation.ClaimChecker`.
- `hosted_like.py:179` judges claims in the replica with the same `ClaimChecker`.
- `hosted_like` also imports `agents.llm.ID_RE`.

**Cost:** in the replica, every fact the bank sends is verified by construction. Any replica gain from the fact bank and levers (`7bc585f`, `9b10c51`) is an upper bound that cannot show a mismatch with the host's interpreter. The hosted runs are the only real signal: v13 scored 0.781, then v14 and v15 scored 0.762-0.763 with the fact bank. The coupling also points the wrong way: production code imports the offline audit module.

**Recommendation:**
- **[now]** When comparing variants, do not count replica score differences that come only from claims.
- **[later]** Move `ClaimChecker` into a neutral `claims.py`. Give the replica an independent or perturbed verdict model.

### A8. Medium-low: configuration is spread across code comments, the prompt and docs

**Where:**
- 41 float literals in `routed.py`.
- Constants live in `play.py`, `routed.py`, `facts.py` and `levers.py`. Next to them are 17 (`play.py`) and 23 (`routed.py`) hosted-run anecdotes, which serve as their only rationale.
- Prompt numbers are duplicated as literals:
  - "At most three claims" (`llm.py:58`) duplicates `facts.ROUND_ONE_CLAIMS`.
  - ">= 3 %" (`llm.py:65`) duplicates `levers.PATH_MIN_RISE`.
  - "120 words" is not enforced anywhere.
- The ASK LIMIT line is rendered from code, which fixes FLOW F9.2.
- `docs/ROUTING.md` still states:
  - a "maximum 25-second decision" (the code uses 40)
  - "90 %" anchors and midpoint concession (the code uses per-goal anchors and a 0.20/0.40/0.10 gap step)
  - that a failed metadata refresh "triggers deterministic closure" (`play.py:126` now falls back to local estimates)
- `ROUTING.md` does not mention the fact bank, levers, near-close or prose grounding.

**Recommendation:**
- **[now]** Render "three claims" and "3 %" from the constants with an f-string. The text and values stay identical.
- **[now]** Correct the four stale `ROUTING.md` statements. Documentation only.
- **[later]** Create one `policy_config.py` (or dataclass) that holds every tunable with a one-line rationale and links to `docs/results`, so the code is no longer annotated with run anecdotes.

### A9. Low: `play.py` mixes three layers

**Where:** `play.py` holds transport (`RemoteTable`, 409 and resume), state repair (`thread_events`, `fetch_documents`, `mark_lapse`), negotiation policy (lines 236-445, about 30 % of the file) and two orchestrators (`play_all`, `play_waves`).

**Cost:** it is the direct cause of A3 and of the wrong-way imports: `agents` imports 15 names from it, and `facts` and `levers` import from it too. It also means a policy change in `play.py` cannot be reviewed apart from runner timing.

**Recommendation:**
- **[later]** Split it into `table.py`, `events.py`, `policy.py` and `runner.py`, and keep `play.py` re-exporting the old names for compatibility.
- **[now]** Do not do this. It is mechanically safe, but it churns imports on the day of the final for no score benefit.

## What to do today

Only do work tagged [now] that is logging-only, prompt-identical or documentation-only:
- A1: exception logging.
- A1: optionally, isolate the text-filter failure.
- A3: name the tolerances.
- A8: render the prompt numbers from constants, and fix `ROUTING.md`.
- Operational: A5 (final runs routed only) and A7 (discount replica gains that come from claims).

Everything structural (A2, A3 and A4 splits, A6 context object, A9) waits until after the competition.

## Comparison with negmas and negmas-llm

| Concern | negmas / negmas-llm | here | gap |
|---|---|---|---|
| Protocol vs strategy | `SAOMechanism` runs the protocol; negotiators only `propose` and `respond` | `play()` runs the protocol but also owns `closing_move` and the policy helpers; the replica copies the loop (A2) | runner and policy are mixed; two runners |
| Offering | `GTimeDependentOffering` and other offering components, composed in `BOANegotiator` | `ask_limit` (play), plus `_packages`/`_structured_bargain`, plus about 10 rewrites in `validate` | no single offering component (A3, A4) |
| Acceptance | `GACNext`, `GACTime`, `ACCombi`; `LLMAcceptance` vetoes an accept below the reserved value | about 12 sites and three tolerances; prompt prose too | no single acceptance function (A3) |
| Opponent model | `GSmithFrequencyModel`, `LLMUFunModel` | none; the supplier's quoted terms stand in (`_toward`), plus health estimates on `case` | absent by design; state is hidden (A6) |
| Validation | `LLMValidation` checks text against the decision and may rewrite words, never the decision; offering snaps and falls back with counted `stats` | `validate` decides, generates sheets and rewrites text; failure is a bare `None` with no reason | the validator does three jobs (A4); failures are unexplained (A1) |
| LLM role | `LLMMetaNegotiator` wraps a base strategy and, by default, the LLM only writes text (`enforce_base_offer`); invalid output falls back to the base | scripts first; the model decides on semantic rounds, clamped to the schedule; fallback is `closing_move` plus repairs | closest to `enforce_base_offer=False` with a clamp. Reasonable, but the base strategy is not one object, so "fall back to base" is a chain of special cases (`routed.py:989-1009`) |
| Language | `TemplateLanguage` and `LLMLanguage` are a separate slot | scripted body strings inline, fact bank, `_grounded_body` inside `validate` | language is not a separate slot (A4) |
| Configuration | constructor argument, then `NEGMAS_LLM_<Class>_<VAR>` environment variable, then default | environment variables for models only; strategy constants are module literals with anecdotes | A8 |

## Status of earlier findings

**massive/03:**

| finding | status |
|---|---|
| F1 (`_premature` loop) | partly addressed: a stall exit at `routed.py:515` |
| F3 (whole-reply reject) | partly addressed: sheets are now repriced, not rejected (`routed.py:162-164`), but rejection reasons are still not recorded |
| F4 (sticky routing) | latch fixed (FLOW F6); relationship goals still always go to the model (`routed.py:558`) |
| F5 (walk-away repair) | fixed (`routed.py:998`) |
| F6 (`ROUTING.md` drift) | still stands, and wider now (A8) |

**BLOAT:**

| finding | status |
|---|---|
| #1 (divergent acceptance checks) | still stands, generalised in A3 and A5 |
| #2 (catalog) | out of scope |
| #3 (full validate per candidate) | still stands as a symptom of A4 |
