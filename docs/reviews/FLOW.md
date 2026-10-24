# Step-by-step logic review of `src/tacto_negotiator`

Review date 2026-10-24, commit `8454ec2`. The review follows one buyer decision from the arena
reply to the sent message and checks each step for logical errors: wrong ordering, conclusions that
do not follow, rules that contradict each other, and evidence that cannot carry the claim made from
it. The checklist comes from [negmas](https://github.com/yasserfarouk/negmas) (SAO protocol,
acceptance and offering components) and [negmas-llm](https://github.com/autoneg/negmas-llm)
(LLM negotiators, meta-negotiator and PABLO-ve validation components).

Eight findings are reproduced by [repro_flow.py](repro_flow.py). It uses only local imports and
in-memory threads on the shipped sim cases, with no arena, model or `.env`. Its assertions describe
the current behaviour and should fail once a finding is fixed:

```sh
.venv/bin/python docs/reviews/repro_flow.py
```

Scope: the decision path was read line by line: `play.py`, `cost.py`, `agents/routed.py`,
`agents/llm.py`, `agents/completion.py` and `agents/naive.py`. From `hosted_like.py` only the design
assumptions in its docstring and profile table were reviewed. The local evaluators (`sim.py`,
`competition.py`, `evaluation.py`, `benchmark.py`) and the HTTP and data layers were not traced;
[LOGIC.md](LOGIC.md) covered competition scoring earlier.

---

## 1. One decision, step by step

| # | Step | Where | Logic check |
|---|---|---|---|
| 1 | Open or resume the session; read every attachment | `play.py:418-477` | OK |
| 2 | Re-read skipped or failed terminal quotes, then refresh authoritative timers | `play.py:482-492`, `86-124` | OK. A failed refresh falls back to local estimates instead of "now" |
| 3 | Per-thread deadline = earliest thread with more than 3 s left, minus a 5 s margin | `play.py:498-503` | **F3**: missing this deadline later ends the whole case |
| 4 | Close the case if the wave cap is near | `play.py:505-509` | OK. This is the only place where closing is the right response |
| 5 | `decide_timed`: close at once if the budget is already spent | `routed.py:829-833` | **F3** |
| 6 | Script, in order: all threads terminal → mirror or close; missing quote terms → ask; semantic blocker → model; simple opening; structured bargain | `routed.py:770-804` | **F6** (latch), **F12** (ranking) |
| 7 | Model route: prepare the context, render the prompt, check the time, call the model, parse | `routed.py:840-865` | **F6** (latch set before the time check), **F9** (prompt contradicts code) |
| 8 | `validate`: allowed fields and options, completion, must-haves, repricing to `ask_limit`, firm opening floor, `_off_repeat`, minimum move, equal-cost filter, clause sheet, trade sheets, 0.9 × price filter | `routed.py:76-172` | **F2** (order), **F4**, **F5**, **F8**, **F1b** |
| 9 | Veto a model accept that `_premature` rejects, then fall back | `routed.py:868-891` | **F10** |
| 10 | Re-check the deadline after deciding: if passed, `close()` | `play.py:519-524` | **F3** |
| 11 | Send; while the reply is pending, send scripted messages to other RFQ threads about to time out | `play.py:450-473` | **F11** |
| 12 | Merge replies, record the slowest round, loop | `play.py:536-561` | OK |

The concession schedule used in step 8 is `ask_limit` (`play.py:345-377`). Each round it moves our
cash cost from our last sheet toward the supplier's standing offer by `GAP_STEP` (0.20; 0.40 for
`routine`). It moves `HOLD_STEP` (0.10) instead after the supplier concedes more than half of the
previous gap.

---

## 2. Findings

Severity is the expected effect on score: **High** can turn a case into 0, **Medium** loses part of
a case's score or wastes rounds, **Low** is an inconsistency with small or unmeasured effect.

### F3 — High — Running out of decision time ends the whole case
`routed.py:830-833`, `play.py:519-524`, `play.py:501`

- **Step:** if the per-thread budget is spent before the decision starts (step 5) or after it ends
  (step 10), the agent calls `closing_move`. That accepts the cheapest acceptable offer or **walks
  away**.
- **Flaw (false dilemma):** the code treats "no time to answer this message" as "the case must end
  now". The API says a late message counts as one timeout: "your last offer is repeated and the
  supplier's patience drops … Three timeouts in a row, or one after the last round, end the thread"
  (`api.md` 453-456). Ending the case early is only right at the wave cap, or when the timeout would
  be the one after the last round.
- **Self-contradiction:** the code already says this for RFQs at `play.py:495-497` ("An idle RFQ
  thread about to time out only costs supplier patience … Closing the whole case for it lost hosted
  P05 bidders"). That reasoning is applied when choosing which thread to message, but not when the
  budget runs out. In `play.py:501`, when no thread is usable, the deadline is set to the earliest
  expiry, which may already be past. The decision then becomes `close()`.
- **Repro:** `f3_no_time_ends_the_case`. With 10 moves left and a standing offer that breaks a
  must-have, the decision is `walk_away`, which scores 0.
- **The fallback does not cover this:** the repair in `routed.py:882-888` (counter instead of
  walking) only runs in the exception branch and only while time remains. `play.py:524` calls
  `agent.closing_move`, which never repairs.
- **Fix:** when the thread deadline is missed and the wave cap is not near, send nothing and let the
  thread time out once. Track consecutive timeouts per thread, and only close (accept if acceptable)
  before the timeout that would end the thread.

### F4 — High — A model can counter a supplier's `final_offer`
`routed.py:76-172`, `llm.py:247`

- **Step 8:** `validate` blocks sends with no moves left or to unavailable threads. It does not
  block a counter-sheet to a thread whose status is `final_offer` or `agreed`.
- **Evidence:** hosted v5 suppliers P02, P04 and P18 withdrew after a counter 0.2 % below their final
  offer (project memory; `api.md` 199: "push further and it may walk"). The prompt only warns about
  this (`llm.py:80-81`).
- **When it happens:** in a single-supplier case the script catches it first (`routed.py:772`: all
  threads terminal → mirror or close). In an RFQ where one supplier is final and another is open,
  the model route is reached and nothing stops a counter to the final supplier.
- **Repro:** `f4_final_offer_can_be_countered`.
- **Related equivocation:** `_mirror_alternative` (`routed.py:729-768`) deliberately sends a term
  sheet to a `final_offer` thread, on the premise that "mirroring it is no counter". The API has no
  "mirror" concept: a message with a term sheet to a final-offer thread is a new round, the same act
  that drew the walk-aways. The cited gains (+0.03 to +0.10) are single hosted sessions. The loss is
  the whole case whenever the best acceptable offer is that supplier's final offer.
- **Fix:** in `validate`, reject term sheets to a thread whose latest status is `final_offer` or
  `agreed`. Keep mirroring only where a rival offer that is at least as good covers the risk, i.e.
  when the mirrored supplier's own final offer is not the best acceptable offer.

### F5 — Medium/High — `walk_away` is the only action the validator never checks
`routed.py:87-88`, `llm.py:237-238`

- An accept passes `acceptable_offer` and, for model accepts, `_premature`. A walk-away goes
  straight through, even with an acceptable offer standing and every round left. A walk-away ends
  the case and scores the alternative, or 0 if there is none (`api.md` 96, 425).
- **Repro:** `f5_walk_away_is_ungated`.
- **negmas comparison:** negmas `ACCombi` and `AcceptAnyRational` accept any rational offer at the
  deadline, and PABLO-ve's `LLMAcceptance` vetoes irrational LLM accepts. In each case the rule that
  can lose the deal is enforced in code, not left to the prompt.
- **Fix:** allow `walk_away` only when no live thread has an acceptable offer and either no live
  thread has moves left or the alternative is strictly cheaper than every reachable offer.
  Otherwise return `None` so the fallback counters.

### F1 — Medium — Our next ask rewards a supplier that raises its price; we also concede into a stall
`play.py:364-375`, `routed.py:150-156`

- **F1a, reciprocity inverted:** the next ask is `ours + step × (current − ours)`, where `current` is
  the supplier's *latest* offer. If the supplier raises its price, the gap widens and our next ask
  rises with it.
  - **Repro:** `f1_retraction_raises_our_ask` gives a next ask of 15.80 after the supplier concedes
    to 19, and 16.20 after it moves to 21.
  - **negmas:** `KindConcessionRecommender` floors a negative partner concession at 0 unless an
    explicit `punish` rule is on. No negmas strategy pays more for a retraction.
- **F1b, conceding into a stall:** when the supplier does not move, the step is still the full
  `GAP_STEP` (0.20 or 0.40). When it concedes a lot, the step drops to `HOLD_STEP`. So we move most
  exactly when the supplier moved least.
  - `validate` then adds a minimum move (`routed.py:150-156`): any sheet below half the step is
    repriced to the *whole* limit (the comment says "at least half").
  - The only brake is `_plateau`, which needs three identical quotes.
  - The premise "Suppliers concede only in response to movement" (`play.py:349`) supports moving
    something. It does not support moving unconditionally by the same share. The replica builds in
    the same premise (see section 3), so it cannot test this.
- **Fix:** measure the gap against the best offer this supplier has made so far,
  `min(current, best_so_far)`. Scale the step by the supplier's last concession, e.g. `GAP_STEP`
  when it moved and a smaller fixed step when it did not. Correct the comment or the code at
  `routed.py:151-156`.

### F2 — Medium — `_off_repeat` runs after the ask limit is enforced and can break the hold
`routed.py:139-145`, `routed.py:252-273`

- **Order:** `validate` reprices to `ask_limit` first, then `_off_repeat` raises a repeated price by
  `gap_step` (0.20 or 0.40). The result is never checked against the limit again.
- **When it bites:** after a large supplier concession `ask_limit` holds at 0.10. If the model then
  repeats its last price, `_off_repeat` moves 0.20 anyway.
- **Repro:** `f2_off_repeat_breaks_the_hold`. Hold limit 11.30, sent 11.60 (cash EUR 79,782 against
  a limit of EUR 77,774).
- **Fix:** clamp the `_off_repeat` result with `price_within(..., limit)`, or compute it from the
  same step `ask_limit` used.

### F6 — Medium — One routing decision locks the case to the model, even without a model call
`routed.py:388-389`, `routed.py:844-854`

- `_semantic_blocker` returns `True` for the rest of the case once `context.case_id` matches.
  `decide_timed` sets that ID while preparing, *before* the "too little time for a model call"
  check.
- A single conditional word such as "unless" can therefore block scripts for every later round,
  even when the model was never called. Those rounds then depend on model time or on fallbacks
  (F3, F10).
- **Repro:** `f6_latch_without_model_call` shows scripts blocked after zero model calls, after the
  conditional wording is gone.
- **Fix:** set the latch only after a model reply was validated and sent, or make it expire, e.g.
  when the next supplier reply carries no conditional wording.

### F7 — Medium — The rival claim can cite a rival price above the supplier's own
`routed.py:586-597`

- **Flaw (equivocation between total cost C and unit price):** the rival is chosen because its
  *total cost* is lower, but the message cites its *unit price*. When the rival wins on other terms,
  such as lead time, the e-mail tells the supplier that the competition charges more.
- **Repro:** `f7_rival_claim_can_cite_a_higher_price` sends "A valid competing offer is EUR 10.2"
  to a supplier quoting 10.0.
- **Fix:** cite a rival only if its unit price is below the recipient's on comparable terms.
  Otherwise argue the specific term that makes it cheaper.

### F9 — Medium — The prompt contradicts what the code shows and enforces
`llm.py:77-84`, `llm.py:184-201`, `routed.py:868-870`

1. The prompt says the C shown "leaves out" the supplier-health term. But `render` shows
   `decision_cost`, which *includes* it when an estimate exists, and lists `relationship` among the
   components. The model is told to correct for a term that is already included.
2. The ASK LIMIT line says "20 % of the remaining gap per round". The code uses 0.40 for `routine`
   and 0.10 after a large concession.
3. "Accepting a standing offer is not limited" and "Do not spend a round merely to avoid accepting an
   opening" invite accepts that `_premature` then vetoes (F10).
4. The ASK LIMIT is in cash cost without health, while the C next to it includes health. Two units
   sit side by side with no label on the difference.

**Fix:** generate these lines from the same constants and functions the code uses, and state which
C each number is.

### F10 — Low/Medium — The `_premature` veto can end in the accept it vetoed
`routed.py:868-888`

- The veto raises `ValueError`, and the exception branch runs `closing_move`, which accepts the same
  cheapest acceptable offer.
- It is replaced only if `_structured_bargain` or `_limit_counter` produce a send. Both can return
  `None`: `_structured_bargain` gives up on missing quote terms in any thread, more than 256
  combinations or a prose-only exchange, and `_limit_counter` gives up when the ask limit already
  reaches the supplier's price. Then nothing is sent and the vetoed accept stands.
- In those states the veto is cosmetic, and the trace records it as a model failure rather than a
  policy decision.
- **Fix:** when an accept is vetoed and no counter is available, record it explicitly. Choose
  deliberately between "accept anyway" and "wait one round".

### F8 — Low — Sent packages are checked against the buyer's plan, not the supplier's defaults
`routed.py:125`, `routed.py:478`

- `validate` and `_packages` call `must_haves_ok(case, complete)` without the supplier's standard
  terms. A must-have on a field that is neither negotiable nor fixed (e.g. lead time from the quote
  PDF) is then checked against the buyer's own plan value, and always passes.
- `acceptable_offer` checks the same field with the supplier's defaults and rejects it. The agent
  negotiates rounds toward a package it can never accept.
- **Repro:** `f8_must_have_checked_without_supplier_defaults`.
- **Fix:** pass `std` in both places.

### F11 — Low — RFQ threads are served once, not "while that supplier replies"
`play.py:450-473`

- `send_all` collects the threads due at the moment of sending (8-50 s left), sends to them, and
  then blocks on all futures.
- A thread with 55 s left at that moment is down to about 8 s after a 47 s reply. It is not served
  during the wait, and the next loop may find it unusable.
- The docstring ("while that supplier replies") promises more than the code does.
- **Fix:** keep polling until the primary future is done, e.g. `wait(..., timeout=1)` in the loop.

### F12 — Low — Concession rounds rank packages by the ranking the opening code rejects
`routed.py:480-482` against `routed.py:514-518`

- `_packages` sorts by C at the supplier's *quoted* price. All rows are priced to the same cash
  ceiling, so this ranking prefers packages with the smallest committed volume, such as the
  shortest contract.
- `_opening_sheets` says the opposite explicitly: "open with the one cheapest at the price we ask,
  not at its quote: there the quote's own short contract looked cheapest". Openings follow that
  comment. Concession rounds (`rows[0]`, `routed.py:688`) still use the rejected ranking, and
  `rows[0][0]` is not the minimum cost, which `routed.py:700` and `:723` assume.
- Effect unmeasured. One ranking should be chosen deliberately.

### Minor
- `price_within` (`play.py:388-389`): the comment says "ask at the floor", but it returns half the
  price when that is above the floor.
- `validate` (`routed.py:166-167`): the 0.9 × top-price filter compares unit prices of packages with
  different commitments. It can drop the clause sheet `_clause_sheet` just added.
- `_script` (`routed.py:795`): reads `terms["unit_price"]` instead of the resolved value. A sheet that
  leaves the price to the quote PDF raises `KeyError`, and the exception falls through to the
  closing fallback.
- `naive.py:4`: the docstring says it "never walks away", but `:18-22` walks away when no supplier
  quotes.

---

## 3. Fallacies in the evidence behind the constants

Most constants (`ANCHOR_BY_GOAL`, `GAP_STEP_BY_GOAL`, `CLAUSE_*`, `CONCURRENT_BELOW_S`, the 0.9 and
1.003 filters) are justified in comments by hosted runs. Those comments contain recurring reasoning
errors. The constants may still be right, but the cited evidence does not establish them.

- **Hasty generalisation (n = 1):**
  - The cited single-session comparisons are often smaller than the noise the comments themselves
    report: "P10 0.555 vs 0.671", "P18 +0.03", "replica 0.731 vs 0.708". For comparison, P02 ranges
    0.39-0.50 over eight runs of the *same* policy (`routed.py:215`).
  - No interval or repeat count is given for the sweeps.
- **Post hoc / confounding:**
  - "Sheets including the supplier's 56 days drew 31.6" (`routed.py:178-179`, `:437-439`) attributes
    the outcome to one sheet, but those sessions also differed in claims, timing and model text.
  - The same applies to "with two verified index falls cited … 5.40 … with a forecast alone … 5.70"
    (`routed.py:562-564`).
- **Circular validation:**
  - `hosted_like.py` encodes "concedes nothing without movement from us" and a final offer at a
    fixed cumulative gap. Its `secure_supply` and `quality_first` profiles are single points fitted
    to one case each (`hosted_like.py:42-48`).
  - Anchors and steps were then swept on this replica (`play.py:291-292`, `:293-296`). Such a sweep
    can only rediscover the rule built into the replica: e.g. "open just below the 14 % final-offer
    gap" for `secure_supply`. It cannot test it.
- **Appeal to precedent / survivorship:** "both top teams accepted only final offers and agreements"
  (`routed.py:353-354`) is the visible behaviour of winners, not evidence that accepting an open
  offer loses.
- **Equivocation:** "mirroring … is no counter" (F4), and C with health against C without it (F7, F9).

A cheap remedy: whenever a constant is changed on hosted evidence, record n, the spread across
repeats, and whether the run differed in anything else.

---

## 4. Checklist against negmas / negmas-llm

| Principle (source) | Here | Status |
|---|---|---|
| Never propose below the reservation value; enforce it in code (`gb/components/inverter.py` clamps, `TimeBasedOfferingPolicy` asserts) | `_packages` drops packages dearer than the alternative. `validate` does not compare a sent sheet with the alternative, so a model sheet dearer than the alternative can be sent, and agreement to it can never be accepted | Partial |
| Never accept below the reservation value, even at the deadline (`ACCombi`, `AcceptFinalOffer`, PABLO-ve `LLMAcceptance`) | `acceptable_offer` on every accept path | OK |
| Accept if the offer is at least as good as our next offer (`ACNext`) | `small_cost_gap` (0.2 %) and `_premature` (0.5 %) | OK, though the two tolerances differ |
| Concession follows relative time, with an explicit last step (`PolyAspiration`, `AcceptFinalOffer` at `n_steps-1`) | Steps are per round, not per relative time. `round_reserve` handles the last round. Timeouts are conflated with the deadline (F3) | Partial |
| Reciprocate; never pay for a negative concession unless `punish` is set (`KindConcessionRecommender`) | Inverted (F1) | Violated |
| Offer the outcome in the target band that is best for the opponent (`worst_in`) | Equal-cost trade sheets toward the supplier's terms (`_toward_sheets`) | OK, a good analogue |
| Validate every LLM output and fall back deterministically; count fallbacks (PABLO-ve `LLMOffering`) | `validate` + `last_decision.fallback`. Walk-away unchecked (F5); final-offer counters unchecked (F4) | Partial |
| Keep decision and text separate; check the text against the action (PABLO-ve `LLMValidation`) | The prompt forbids figures in the body and code strips unknown IDs, but no check ties body claims to the sent sheets. The scripted rival text can contradict the action (F7) | Partial |
| Opponent model: issues the opponent concedes on often matter less to it (`FrequencyLinearUFunModel`, `ConcessionRatioUFunModel`) | None. The supplier's quoted terms stand in for its preferences (`_toward`) | Absent; deliberate, but an option for RFQ trades |

---

## 5. Suggested order of work

1. **F3:** skip instead of close on a per-thread miss.
2. **F4, F5:** validator rules for final offers and walk-aways.
3. **F2, F1a:** clamp `_off_repeat`; measure the gap against the best offer so far.
4. **F9:** generate the prompt's numbers from the code.
5. **F6, F7, F8, F10, F11, F12:** as time allows.
6. **F1b** (conditional concession) changes strategy. Test it on hosted practice, not only on the
   replica (section 3).

Not checked: hosted or live-arena behaviour of any proposed fix, the local evaluators line by line,
provider latency, and whether the archived practice results would rank differently. No source,
test or configuration file was changed; this review added only `FLOW.md` and `repro_flow.py`.
