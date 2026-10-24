# Bug review, round 2: `src/tacto_negotiator` at `5dea00a`

Review date 2026-10-25. Scope: **bugs only**, meaning code that does something other than its own
docstring or comment, the API contract (`materials/starter_kit/brief/api.md`) or its caller expects.
Logic fallacies and architecture are covered by the other two reviewers.

- **Read line by line:** `git diff 8454ec2..HEAD -- src` (`agents/facts.py`, `agents/levers.py`, and the
  changes in `routed.py`, `play.py`, `llm.py`, `competition.py`, `evaluation.py`), all of `play.py`
  and `agents/routed.py`, the `llm.py` render/guard/parse path, `client.py` and `docs.py`.
  `evaluation.ClaimChecker` and the `sim.py` timeout and claim logic were read where the new code
  depends on them.
- **Not read:** `cost.py` and `data.py` beyond their call sites, `completion.py`, `hosted_like.py`,
  `benchmark.py`, `web.py`, `cli.py`, `compete.py`, and `docs/results/`.
- **Test suite:** `.venv/bin/python -m pytest -q -x` gives **333 passed in 29.3 s**.
- **Repro:** `.venv/bin/python docs/reviews/round2/repro_bugs.py`. The script runs locally only: in-memory
  threads and the in-memory `Sim` on the shipped cases, with a virtual clock. It needs no arena, model
  or `.env`. Each `bN_*` assertion describes the **current** behaviour, so it fails once that bug is
  fixed.

---

## Findings

### B1: High: a message with no term sheet to a `final_offer` thread goes out as a counter
`agents/routed.py:192-195` (the gate it bypasses is at `:127-128`)

1. The F4 fix at `:127` rejects a model decision that carries term sheets to a thread whose status is
   `final_offer` or `agreed`.
2. A decision with no term sheets passes that gate. Typical examples are a question, or "we will come
   back to you" while the agent negotiates with the other bidder.
3. At `:192`, `clean` is empty, we have sent sheets before, `ask_limit` is finite, and the fact bank has
   no new fact for this supplier. In that state `validate` appends `_limit_sheet`: the supplier's own
   terms priced at our ask limit, below its final offer.
4. **Evidence:** `b1_final_offer_countered_via_limit_sheet`. The final offer is EUR 18. A sheet from the
   model is rejected (`None`). The message without a sheet goes out with `unit_price 15.6`.
5. **Where it happens:** the model route of an RFQ in which one bidder is final and another is open.
   A single-supplier case is caught earlier by `_script`'s "all threads terminal" branch. The model route
   is the normal route for `stabilise`/`secure_supply` cases (`_semantic_blocker` at `:558-559`).
6. **Consequence:** hosted suppliers walk away when a final offer is countered (memory; `sim.py:218-220`
   does the same). If that supplier's final offer was the best acceptable one, the result drops to the
   rival's offer or the alternative, which can mean a score of 0.
7. **Fix:** require `latest(events).get("status") == "open"` in the `:192` condition. Or move the
   final-offer check after all of `clean` has been assembled, so it applies to every sheet that will be
   sent.

### B2: Medium: the fact bank re-cites facts across RFQ bidders, and the arena answers `duplicate`
`agents/facts.py:310-314` (`used`) and `:347-349` (`assign`); `routed.py:346-348` (`_new_fact`)

1. `FactBank.used` collects cited IDs only from the buyer messages in **this** supplier's thread. The
   module docstring says so on purpose: "what was cited is tracked per supplier".
2. The API says "each ID counts once per case, except a rival's quote" (api.md, What counts). Our own
   `Sim._answer` applies the same rule (`sim.py:206-209`). The old `_strip_wasted_claims` was correct:
   it collects the cited IDs over all threads.
3. In an RFQ, every supplier-independent fact is therefore cited again to the next bidder: index
   falls, the forecast, and a generic `cost:<part>` should-cost. In round 1 and in the "exactly one new
   fact" rounds that follow, the claim comes back `duplicate`.
4. `_new_fact` uses the same per-supplier set. It reports such a duplicate as a new fact, so `validate`
   does not add the `_limit_sheet` movement. The supplier receives neither a new fact nor movement,
   which is the situation the comment at `:193` blames for a walk-away (P18) and a final offer (P02).
5. **Evidence:** `b2_fact_bank_duplicates_across_bidders` adds one case forecast to S02 and sends a
   validated scripted message to each bidder. Results: `{'sup:70340': ['verified'], 'sup:70360':
   ['duplicate', 'verified']}`. `_new_fact` returns `True` for the second bidder.
6. **Consequence:** RFQ claims are wasted (at most 3 per message), and a round without movement and
   without a new fact can occur.
7. **Fix:** in `used` (and `_new_fact`), take the union over all threads for every key except
   `quote:`/`offer:` refs, which stay per supplier. This mirrors `Sim._answer`.

### B3: Medium: FLOW F7 moved into `facts.py` and is now added automatically to every message
`agents/facts.py:279-290` (`_rival_offers`), `:379-406` (`enforce`)

1. `_rival_offers` picks the rival with the lower **total cost**, then cites its **unit price**. This is
   FLOW F7, which was left open, but the code has moved and changed. It is now a `strong` fact of
   priority 2.
2. `FactBank.enforce`, called from `validate` through `_paced_body`, appends any assigned fact the body
   lacks. So the sentence is no longer only something the model may write. It is now added to scripted
   messages as well.
3. **Evidence:** `b3_rival_fact_cites_a_higher_price`. A scripted message to the supplier quoting 10.0
   goes out with "A valid competing offer is EUR 10.2 per unit (offer:b)". The claim checks as
   `verified`, but it tells the supplier that the competition charges more.
4. `_case_quotes` (`:263-277`) already guards with `quoted < price`. `_rival_offers` has no such guard.
5. **Fix:** in `_rival_offers`, keep only rivals whose unit price is below the recipient's standing
   unit price.

### B4: Medium: a late `accept` is discarded even when its thread still has time
`play.py:608-621`

1. The decision deadline is the earliest deadline among the usable threads (`:578`). In an RFQ that
   deadline can belong to an idle bidder B, while the decision is an accept to bidder A.
2. When the decision finishes after that deadline, `on_time = not action and ...` is `False` for **any**
   action. `close_before_lapse` is false unless B's timeout would end B. So the code `continue`s, and the
   accept to A is never sent, although A has about 35 s left.
3. **Evidence:** `b4_late_accept_to_a_thread_with_time_is_dropped` plays S02 through `play()` with a
   virtual clock. Decision 2 (accept A, `a_seconds_left 34.9`) is dropped. Decision 3 makes the same
   accept and closes the case.
4. **Consequence:** in the real loop, the second decision re-runs the scripts or a model call (up to
   40 s) on a shorter clock. Its result can differ from the first, for example a timeout fallback.
5. **Plausible, not reproduced:** in the same branch, a late accept of a supplier's `final_offer` on its
   **own** expiring thread is also dropped. That thread then times out, and per api.md "your last offer
   is repeated". On the hosted arena that may count as countering the final offer, which makes hosted
   suppliers walk. The local `Sim._tick` only records the timeout, so this was not reproduced.
6. **Fix:** treat a closing action like a message. Send it when its own thread is not lapsing. For an
   accept, also send it inside the `SEND_MARGIN_S` (5 s) margin rather than letting the thread time out.

### B5: Low: a named floor with a thousands separator is not recognised
`agents/levers.py:33` (`_PRICE`), `:156-158`

- `_PRICE` reads "EUR 1,250.00 is the lowest we can go" as `[1.0, 250.0]`, so `floor_reached` is
  `False` for a standing price of 1250.00.
- **Consequence:** the "named floor" close (`routed.py:890-899`) and the `FLOOR NAMED` prompt line are
  skipped for four-digit prices. The agent keeps probing below a named floor.
- **Evidence:** `b5_named_floor_with_thousands_separator`.
- **Fix:** strip thousands separators (`(?<=\d),(?=\d{3}\b)`) before matching.

---

## Verification of the claimed FLOW fixes

| Item | Status | Evidence |
|---|---|---|
| F1a (a retraction raised our ask) | **Fixed.** The gap is measured against the best offer so far (`play.py:369-375`). A retraction now gives the same ask as a hold, 16.0 vs 16.0 (`verify_flow_fixes`). The old `f1` assertion still passes (15.8 vs 16.0), but only because of F1b, which is open: a concession lowers the ask further than a hold does | `verify_flow_fixes` |
| F2 (`_off_repeat` broke the hold) | **Fixed.** The result is re-clamped to the limit (`routed.py:177-180`). The sheet is now 11.30 at the limit | `verify_flow_fixes`; the old `f2` assertion fails |
| F3 (missed deadline ended the case) | **Fixed.** The late move is skipped and the thread times out once. Timeouts in a row are tracked (`lapses`, `last_chance`, `close_before_lapse`, `wait_for_lapse`). The timer bookkeeping in `note_window` was traced for refreshes before and after the arena records the timeout and does not double-count. **New side effect: B4** | `repro_flow.f3` (updated) passes |
| F4 (countering a final offer) | **Partially fixed.** `validate` rejects model sheets to `final_offer`/`agreed` threads, but **B1** bypasses the gate. `_mirror_alternative` still sends a term sheet to `final_offer` threads (`reprice=False` skips the gate). This is now limited to a sheet cheaper than the best acceptable offer, which is still the FLOW F4 risk | `b1`; the old `f4` assertion fails |
| F5 (`walk_away` unchecked) | **Partially fixed.** A walk-away is blocked while an acceptable offer stands, and without an alternative while rounds are left. With an alternative, a model walk-away still passes with all 10 rounds left when no standing offer beats the alternative yet (FLOW proposed allowing it only when no live thread has moves left) | `verify_flow_fixes` |
| F6 (model latch without a model call) | **Fixed.** `model_case` is set only after a validated model move (`routed.py:986`) | the old `f6` assertion fails |
| F8 (must-haves without supplier defaults) | **Fixed.** `validate:158` and `_packages:646` pass `std` | the old `f8` assertion fails |
| F9 (prompt contradicts code) | **Fixed.** The step comes from `gap_step`, C is described as including health when estimated, the ASK LIMIT unit is labelled, and the accept wording matches `_premature` (`llm.py:103-108`, `:225-229`) | read |
| F7 (left open) | **Changed:** moved and now added automatically, see **B3** | `b3` |

The items left open (F1b, F10, F11, F12) are unchanged by the new code and are not re-reported.
