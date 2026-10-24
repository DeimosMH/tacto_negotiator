# Round 2: logical fallacies and unsupported assumptions

- **Commit:** `5dea00a` (2026-10-25).
- **Scope:** the new `agents/facts.py` and `agents/levers.py`, and the changes to `routed.py`, `play.py` and `llm.py`
  since `8454ec2`. Then the decision path and the modelling assumptions in `hosted_like.py`.
  Plain bugs and architecture are covered by the other two reviewers.
- **Method:** each premise was traced to its conclusion and checked against `api.md`, the code that
  enforces it, the evidence cited in comments and commit messages, and the hosted per-case scores for
  v10-v15. Only the `matches[].score` and decision `reason` fields were extracted from those files.
- **Probes:** `repro_fallacies.py` uses in-memory threads on the sim cases, with no network, model or `.env`.
  Its assertions describe the current behaviour, so each one should fail once its finding is fixed.

```sh
.venv/bin/python docs/reviews/round2/repro_fallacies.py
```

---

## Findings, by expected score impact

### A1 — High — Our concession still shrinks as the supplier's concession grows (F1b open; F1a only half-fixed)
`play.py:352-387` (`ask_limit`), `routed.py:191-195`

- **Premise → conclusion:** "Suppliers concede only in response to movement, so every round moves - but by
  a share of the gap" (`play.py:355`).
  Since F1a, the gap is measured to the supplier's best offer so far.
- **Why it doesn't follow:** the premise supports moving *something*, not moving a fixed share of the
  *remaining* gap.
  - Because the step is a share of what is left, a supplier that holds keeps a wide gap and draws a large
    step from us. A supplier that concedes narrows the gap and draws a smaller one.
  - That inverts tit-for-tat. negmas `KindConcessionRecommender` scales our concession *with* the partner's.
  - The F1a fix stops a retraction from earning *more* than a hold. It does not stop either from earning
    more than a concession.
- **Evidence (A1):** our next move from 15 after the supplier's reply. Holding and retracting draw the
  biggest move, and the more the supplier concedes, the less we move.

  | Supplier's reply | Our next move |
  |---|---|
  | Retracts to 21 | 1.0 |
  | Holds at 20 | 1.0 |
  | Concedes to 19 | 0.8 |
  | Concedes to 17 | 0.4 |

- **New rule that adds to it:** `routed.py:191-195` forces a full ask-limit sheet whenever a message carries
  "neither movement nor a new fact". Once the fact bank runs dry, every message concedes, whatever the
  supplier did.
  - The rule is justified by two anecdotes: "hosted P18 walked and P02 went final".
  - The P18 walk-away is the v5 episode, where we had countered a final offer, so it is confounded.
- **Consequence:** this runs every round of every case. Holding is the supplier's best response, which is
  what the replica (A3) and the P07 comment both describe.
- **To make it sound:** use a reciprocal step, e.g. `step = clamp(k × their_last_concession / gap, s_min, GAP_STEP)`,
  with a small fixed `s_min` when they held. Check the change against hosted runs only (see A3).

### A2 — High (tail) — Mirroring still sends a term sheet to a final offer, contrary to the prompt and the guard
`routed.py:127-128`, `routed.py:833-872`, `llm.py:106-107`

- **Premise → conclusion:** "Its own alternative is a package it already offered, so mirroring it is no
  counter" (`routed.py:837-839`).
  The F4 guard therefore applies only when `reprice=True`, and the mirror sends with `reprice=False`.
- **Why it doesn't follow (equivocation):**
  - The API knows only messages with term sheets, and a status of `final_offer` means "push further and it
    may walk".
  - Whether a supplier treats a mirrored sheet as a counter is an empirical question. Calling the act
    "mirroring" does not answer it.
  - The prompt tells the model "status final_offer: accept it if acceptable - never counter it", while the
    script does the opposite.
  - The guard's comment misstates its own base rate. `routed.py:128` says "v5: 3 of 3". The v5 record (project
    memory) is 3 of 7: three suppliers walked and four repeated their final offer.
- **Evidence:**
  - **A3 probe:** `validate` rejects the sheet on the guarded path, yet `_script` sends the same sheet to the
    *only* supplier's final offer as `mirror_alternative`.
  - **Hosted record:** 8 mirrors and 0 walk-aways, but 7 of the 8 were on P18 (the other on P11). That
    supports "P18 does not walk on a mirror", not "suppliers do not". The rule of three puts the 95 % upper
    bound on the walk rate at about 35 %.
  - **Unclear gain:** P18 scored 0.377-0.61 with a mirror, no better than without one.
- **Consequence:** in a single-supplier case, a walk-away loses the whole case. The cited gain is +0.03 to
  +0.10. Break-even therefore needs a walk probability below about 5-15 %, which the evidence cannot show
  outside P18.
- **To make it sound:** mirror only when a different supplier's acceptable offer covers the walk-away (FLOW F4's
  proposal). Otherwise accept the final offer. Fix the "3 of 3" comment.

### A3 — Medium/High — The replica and the hosted comments assume incompatible supplier models; the replica validates the agent against itself
`hosted_like.py:113-128`, `:180`, `:209-210`, `:264-265`, `:17`; `play.py:295-310`

- **Two models, one supplier class:**
  - **Replica:** a round-2 concession is `a2 × (previous − ask)`. It is a share of the *remaining* gap and is
    released by any movement at all (`ask > last_ask`).
  - **`GAP_STEP_BY_GOAL` comment:** "Routine suppliers answer each of our moves with about 0.4 of it", so
    the concession is proportional to *our movement*.
  - **A7 probe:** in the replica, a 0.01 move draws a concession of 0.289 and a move of 0.4 × the gap draws
    0.174.
  - The code takes `GAP_STEP` (0.20) and the deeper anchors from a sweep on the first model
    (`play.py:295-297`), and `routine`'s 0.40 from the second. Under each model, the other constant is wrong.
- **Circular validation:**
  - **Health term:** the replica scores the relationship term with the agent's own estimate
    (`state["estimates"] = package.supplier_estimates(...)`, `:180`, `:264`). That is the same estimate the
    agent's health floor `cheapest_price` uses, so the replica cannot reveal a wrong health estimate. For
    `stabilise` and `secure_supply` goals, that estimate is the main risk.
  - **Final offers:** the replica's walk-away rule treats any sheet that is not agreed after a final offer as
    a walk. A mirror priced at the supplier's own premium counts as "agreed", so the replica builds A2's
    premise in.
- **Blind spots:**
  - **Claims:** the replica gives a single 0.5 % bonus for the first verified claim (`:209-210`). It cannot
    reward fact pacing, the order path or the floor question.
  - **Prose:** the docstring says prose is not modelled (`:17`).
  - **What it was used for anyway:** "Replica: mean 0.8294 (baseline 0.829)" (commit `8a4ab19`, prose guards)
    and "Replicas unchanged (0.829)" (`eebf2d8`) are reported as checks, but are null tests by construction.
- **Resolution:** the replica predicts the next counter only to 2.1 % of the opening price on held-out data
  (`:14-15`). The levers it is used to tune are smaller than that: the near-close step is 0.75 % and the
  equal-cost filter is 0.3 %.
- **To make it sound:**
  - Pick one concession model and test it on recorded hosted threads, regressing the supplier's concession
    on our move and on the gap.
  - Score the replica's health term with a perturbed hidden margin, not the agent's estimate.
  - Label replica results as "regression check only" for prose, claims and final-offer behaviour.

### A4 — Medium — Hosted single runs cannot rank v13, v14 and v15; the headline numbers mix in selection
Commit messages `37110d7`, `706dd5d`, `5dea00a`

- **Per-case spread across six hosted runs (v10-v15):**

  | Spread | Cases |
  |---|---|
  | Large | P05 0.30-0.83 (SD 0.195), P08 (SD 0.077), P11 (SD 0.069) |
  | Small | P02, P03, P04, P07, P10, P01, P18 (SD ≤ 0.03) |

- **What that means for a 10-case mean:**
  - The SD of one run's mean is about 0.023. That is an upper bound, since the policy also changed between
    runs.
  - The difference between two runs has an SD of about 0.03.
  - v13 0.781, v14 0.763 and v15 0.762 are within one SD of each other.
- **Fallacies:**
  1. **Pooling to hide a drop:** "v13-v14 ~0.77" pools the run before the fact bank and FLOW fixes (v13 ran
     `8a4ab19`) with the run after them. The change (-0.018) comes almost entirely from P05 (-0.109) and P11
     (-0.084), the two noisiest cases. Neither "better" nor "worse" is established.
  2. **Selection on the maximum:** "New bests … board 0.7915" takes per-case maxima over runs, which rise with
     every noisy draw. The Live Final plays each hidden case once, so the expected per-run mean (~0.76) is the
     number to plan with.
  3. **Post hoc, n = 1:** "0.762 with F3 (P05 survived 3 thread timeouts)" rests on one session. P05 scored
     0.814 in v13 without F3.
- **To make it sound:** repeat only the noisy cases (P05, P08, P11, P18). Use the same seeds where possible,
  report n and the spread, and compare paired per-case differences.

### A5 — Medium — The fact bank's pacing contradicts the premise that motivates it
`facts.py:1-8`, `:339-369`, `routed.py:191-195`, `:346-348`

- **Premise:** "a code-priced supplier gives one cut per NEW verified fact it takes and holds when nothing
  new comes" (another team's observation).
- **Conclusions as written:** round 1 carries every strong fact (up to three), and each later message
  carries exactly one.
- **Why they don't follow:**
  1. **Pacing contradicts itself:**
     - If the cut comes once per message that carries a new fact, front-loading three facts in round 1
       spends two of them for nothing.
     - If the cut comes once per fact, rationing later facts gains nothing and costs rounds.
     - The design is optimal under neither reading unless round 1 is special, and no evidence for that is
       cited. "Verified claims work best in the first message" (`llm.py:67`) is the earlier team's practice
       (appeal to precedent), not a measured comparison.
  2. **Equivocation on "new fact":** the premise speaks of a fact the supplier *takes*. `_new_fact` counts
     any uncited checked ID, including fillers the bank has already classed as weak or shrugged off. A
     message with a filler is exempt from the "must move" rule (`routed.py:191`), so the case the premise
     warns about (no fact taken and no movement) is exactly the one let through.
  3. **Attribution (A5 probe):** one "does not change our calculation" in reply to a round-1 e-mail marks
     *every* claim kind in that e-mail as shrugged (`['cost', 'fc', 'idx']` when only the index was
     answered).
  4. **Unsourced ranking:** `PRIORITY` claims "measured take-rates" (`facts.py:38`) with no source, n or
     spread. The order path leads (priority 0) on one P08 observation by another team (5.41 → 5.25).
  5. **F7 still stands, moved to `facts.py:279-297` (A6 probe):** a rival is chosen by total cost and its
     unit price is cited, so the supplier quoting 10.0 is told "A valid competing offer is EUR 10.2". It is
     marked `strong`, so it ranks ahead of index facts.
- **To make it sound:**
  - Choose one reading and pace by it: one new strong fact per message from round 1, or all of them in
    round 1 with nothing rationed later.
  - Exempt only *strong* facts from the movement rule.
  - Attribute a shrug only to the claim the supplier's sentence names, or to the message's kinds only when it
    cited a single kind.
  - Cite a rival only when its unit price is below the recipient's.

### A6 — Medium — The walk-away gate treats the current offer as the best reachable one
`routed.py:109-118`

- **Premise → conclusion:** if no standing offer is acceptable and an alternative exists, a walk-away is
  allowed, even in round 1 with every round left.
- **Why it doesn't follow:**
  - "No acceptable offer *now*" is not "no acceptable deal *reachable*".
  - The code's own anchor comment (`play.py:292-293`) says the best attainable prices lie 12-25 % below the
    openings. An opening quote above the alternative is the expected state, not evidence that the case is
    lost.
  - FLOW F5 asked for "no moves left, or the alternative strictly cheaper than every reachable offer". Only
    the no-alternative half was implemented.
- **Evidence (A4 probe):** in S03, a round-1 model `walk_away` validates with 10 moves left, against an
  opening of 300 and an alternative of 245.59.
- **Consequence:** the case takes the alternative's score at once, forgoing every deal below the
  alternative.
- **To make it sound:** allow a walk-away only with no moves left on every live thread, or after the
  supplier has gone final above the alternative.

### A7 — Low/Medium — The near-close ask probes below a floor the code says it will not probe
`routed.py:890-899`, `levers.py:8-10`, `:146-165`, `:168-197`, `llm.py:71-72`, `:258`

- **Premise:** "A supplier that names a floor equal to its standing price is not probed below it"
  (docstring and prompt).
- **What the code does:** the `named_floor` branch passes its accept through `_before_accept`, which turns it
  into a near-close sheet 0.75 % *below* that floor. The comment even says "a first named floor gets one
  near-close".
- **Evidence:**
  - **A2 probe:** after the named floor 18.00, the route is `near_close` with a sheet at 17.87.
  - **A2b probe:** `_PRICE` rejects any number followed by ".", so "The lowest we can go is 18.00." names no
    floor at all. Whether the rule fires depends on punctuation.
- **Further assumptions:**
  - A floor the supplier states is taken as truthful, so it stops probing, although the API says a supplier
    "decides as the person it is".
  - The near-close lever rests on "agreed once, held twice and never walked" (n = 3, another team). Our
    hosted runs add two uses, P03 v13 and P01 v14, both without a walk-away.
- **To make it sound:** skip the near-close after a named floor, as the prompt says. Treat a named floor as
  an upper bound on further gain, not as a fact.

### A8 — Low — Accept rules: the prompt differs from the code, and the cited evidence does not match the rule
`llm.py:107`, `llm.py:228-229`, `routed.py:507-523`

- **Prompt against code:** the prompt says "Accept an open offer only when it meets your last ask". The code
  lets the model accept an open offer above our ask when `moves_left ≤ 3` or the supplier repeated its price.
  F9 is fixed only in part.
- **Equivocation (open against final):** the stalled-supplier exception cites "further counters only risk a
  walk-away (hosted P02 v4/v5)". The v5 walk-aways followed counters to *final* offers, not to open ones.
- **Survivorship:** "both top teams accepted only final offers and agreements" (FLOW §3) still stands as the
  premise of `_premature`.
- **To make it sound:** generate the accept sentence from `_premature`'s actual conditions, and drop the P02
  citation or replace it with open-offer evidence.

### A9 — Low — F3's cost model for a timeout is assumed, not measured
`play.py:37-40`, `:426-446`, `:477-516`, `:606-621`

- The fix follows `api.md` ("one timeout … patience drops"), which is right on the contract.
- The decision to lapse instead of sending a late move assumes the patience drop is cheaper than what the
  move would have gained. That is unmeasured: the only evidence is "P05 survived 3 thread timeouts", n = 1
  (see A4).
- The patience effect on a later final offer or walk-away is not modelled anywhere, the replica included.
- **To settle it:** compare threads with and without timeouts in the existing hosted traces. Look at the
  status and concession in the reply that follows a timeout.

---

## Status of earlier findings

| Finding | Status at 5dea00a |
|---|---|
| F1a, retraction raises our ask | Fixed for retraction above a hold. The broader inversion is A1 |
| F1b, concede into a stall | **Open**, and extended by `routed.py:191-195` (A1) |
| F2, `_off_repeat` breaks the hold | Fixed. `repro_flow.py` F2 now fails as intended |
| F3, missed deadline ends the case | Fixed. The evidence for it is weak (A9) |
| F4, counter to a final offer | **Partial.** The mirror bypasses it (A2) |
| F5, walk-away ungated | **Partial** (A6) |
| F6, latch | Fixed |
| F7, rival claim by C, cites price | **Open**, now `facts.py:279-297` (A5.5) |
| F8, must-haves without std | Fixed |
| F9, prompt against code | Partial (A7, A8, A2) |
| F10, F11, F12 | Not re-examined. The code paths are unchanged apart from `_before_accept` in the fallback |
| §3 evidence fallacies (n = 1, confounding, circular replica, survivorship) | All still stand. New instances in A3, A4, A5, A7 |

## negmas / negmas-llm checklist (updated)

| Principle | Here | Status |
|---|---|---|
| Never propose or accept below reservation value (clamp in code) | `acceptable_offer` on every accept. `_packages` drops sheets dearer than the alternative. A model sheet dearer than the alternative is still not compared | Partial |
| AC_next: accept if offer ≥ our next offer | `small_cost_gap` 0.2 % against `_premature` 0.5 %. The near-close inserts one more ask first | OK (two tolerances) |
| Time-based aspiration with an explicit last step | Steps are per round. `round_reserve` handles the end. Timeouts are now separate from the deadline (F3 fixed) | Partial |
| Tit-for-tat: concession scales with the partner's, negatives floored (`KindConcessionRecommender`) | Negative concessions are floored (F1a). The step still scales inversely with the partner's concession (A1) | **Violated** |
| Respect the partner's terminal signal (`AcceptFinalOffer`) | The guard blocks repriced counters. The mirror and the prompt disagree (A2) | Partial |
| Opponent model learned from behaviour (frequency or concession models) | None. Fact pacing assumes the supplier model of one other team, untested (A5) | Absent |
| LLM writes text, strategy decides (negmas-llm) | Facts are assigned by code and enforced in the body. Sheets are repriced by code | OK |
| PABLO-ve `LLMValidation`: the text matches the action | `_grounded_body` drops unsupported figures and roles. The rival sentence can still contradict the sheet's logic (A5.5) | Partial |
| `LLMAcceptance` vetoes irrational accepts and walk-aways | Accepts are gated. A walk-away is gated only half (A6) | Partial |
| Validate the strategy on an opponent independent of the strategy's assumptions | The replica embeds the agent's health estimate, the mirror premise and a concession model that contradicts the P07 one (A3) | **Violated** |
