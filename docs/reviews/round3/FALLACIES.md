# Round 3: logical fallacies and unsupported assumptions

- **Commit:** `d842b8e` (2026-10-25; HEAD `737c1d3` changes only the README). Extra depth on the 28 commits since round 2 (`5dea00a..HEAD`).
- **Method:** each premise in the code, comments, commit messages and docs was traced to its conclusion. Each was then checked against `api.md`, the code that enforces it, the hosted deal journal (`docs/deals/*.json`: terms, `cost_breakdown` and score only) and the hosted A/B result files.
- **Probes:** in-memory threads on the practice case files, the local hosted-like supplier (`--bench oos`, scripts only) and our own cost model.
  - No arena, model or `.env` was used.
  - The suite passes: 349 tests. Run it from an empty directory: a stray `/tmp/h2.py` shadows the `h2` package and fails 25 tests when pytest runs from `/tmp`.
- **Reproduce:** `.venv/bin/python docs/reviews/round3/repro_fallacies.py` (about 2 s; every L-line below matches its output).

Findings are ordered by expected effect on the Live Final, where every supplier is unseen.

---

## L1 — High — Our concession still shrinks as the supplier concedes more (round 2 A1, open)
`play.py:366-405` (`ask_limit`), `routed.py:216-220`

- **No change since round 2.** Our next step is still a share of the remaining gap.
- **Probe:** from 15, the supplier holds at 20 and we move 1.0. It concedes to 19 and we move 0.8. It concedes to 17 and we move 0.4.
- **Why it matters most in the final:** every round of every case runs through this rule. An unseen supplier that holds is paid most for holding, which is the opposite of the reciprocity the code cites (negmas `KindConcessionRecommender`).
  - This is the "relative tit-for-tat" family in Faratin, Sierra and Jennings (1998), *Negotiation decision functions for autonomous agents*.
- **Fix (after the final, or with a guard):** `step = clamp(k × their_last_concession / gap, s_min, GAP_STEP)`.
- **Smallest safe change before 13:30:** cap our step at the supplier's last concession, in cost, whenever it moved less than `HOLD_SHARE`.

## L2 — High (tail) — A term sheet still goes to a lone supplier's final offer (round 2 A2, open)
`routed.py:1011-1050`, `routed.py:1055-1057`, `routed.py:133`, `FINAL_RUNBOOK.md:64`

- **Probe:** `validate` refuses the sheet on the guarded path, yet `_script` sends the same sheet as `mirror_alternative` to the *only* supplier's final offer.
- **Comment still wrong:** `routed.py:133` still reads "v5: 3 of 3". The record is 3 of 7.
- **Contradictions:** the runbook says final offers are "never countered" and, in the same line, that one sheet is sent back.
- **Evidence for the mirror:** 8 uses, 7 of them on P18, with gains of +0.03 to +0.10. A walk-away loses the whole case.
- **Fix:** mirror only when a rival's acceptable offer covers a walk-away; otherwise accept the final offer.

## L3 — High for affected cases — The new clause sheet switches off the best-evidenced new rule
`routed.py:224-230`, `routed.py:307-315` (`_imposed_clause`), `routed.py:355` (c99893a), `routed.py:316-329` (3b94da9)

The two new rules were each argued on their own. Together they cancel out.

- **3b94da9 (hold one sheet):** once a lone supplier answers our *clause-free* first sheets with its own index clause, send one sheet on our package.
  - This is the strongest hosted evidence among the new commits. On P08 it lifted 0.645-0.715 (arm A, three runs) to 0.855; on P18 it lifted 0.54-0.59 to 0.707.
- **c99893a (clause sheet for any goal):** adds an equal-cost clause sheet to the *first* message for any goal, leverage included. Leverage is the goal of P08 and P18.
- **The interaction:** `_imposed_clause` returns False as soon as our first message carried *any* clause sheet. So on every case where the clause sheet fired, the held-sheet rule cannot fire.
- **Probe on P16** (leverage, raw material, synthesized opening 93.42):
  - Round 1 sends `[(69.50, db3), (70.06, no clause)]`.
  - The supplier then answers with its own 0 % clause.
  - With our clause sheet in round 1, round 2 sends 3 sheets with new deadband and payment variants: `(75.19, none, 90d)`, `(71.91, db0, 30d)`, `(72.85, db0, 90d)`. That is exactly the pattern 3b94da9 measured as drawing a final offer (P18 11 of 16).
  - Without the clause sheet, round 2 sends 1 sheet.
- **Scenario where it costs score:** an unseen raw-material leverage case with clause options whose 12-month rise clears the ratio (see L4). The supplier imposes its own clause, as both hosted steel suppliers did, and we fall back to the variant spray. Per occurrence that cost about 0.15 on P08 and P18.
- **Fix (one line, before the final):** restore the `RELATIONSHIP_GOALS` check for the clause sheet, i.e. revert the goal widening in c99893a. The relationship path has hosted evidence (P02); the leverage path has only the replica (L4, L5).
  - Alternative: in `_imposed_clause`, ignore our own clause sheets and fire when the supplier's clause differs from every clause we sent.

## L4 — Medium — The clause gate's threshold sits on one case, and its quantity measures our cost, not the supplier's appetite
`routed.py:424-443` (`_clause_pays`, `CLAUSE_RISE_RATIO = 3.0`), `hosted_like.py:47-52`, `hosted_like.py:91-109`

- **Knife edge:** the threshold separates P02 (ratio ≥ 5.3, worked) from P08/P18 (best ratio **2.96**, "moved nothing"). A 1.5 % difference in one denominator flips the rule. The 3.0 is a cut placed between two data points, not a measured effect.
- **Wrong quantity:**
  - The premise is that a supplier passing on a material rise prefers the clause. That is a property of the supplier's rise.
  - The gate divides that rise by *our* expected clause cost (the path beyond the deadband, plus index risk).
  - P16 (rise 6.5 %) passes at 6.54 only because its expected path (3 %) falls inside a 3 % deadband. On the supplier side it is the same situation as P08/P18 (rise 5.9 %), which is the only hosted evidence for a non-relationship goal, and there clause sheets moved nothing.
- **Built-in evidence:** the replica's supplier values a clause at `0.7 × share × (12-month rise − deadband)`. `CLAUSE_PASS_THROUGH = 0.7` comes from P02 alone. `clause_reserve` adds that a clause-free package can never reach the floor.
  - So the commit's "P16 0.719 → 0.800" rests on a supplier built to want the clause.
  - The replica's own comment admits that P08/P18 showed nothing measurable, against its predicted 0.3-1.9 %.
- **Fix:** keep the clause sheet for relationship goals only (L3), and treat the ratio as unvalidated outside P02.

## L5 — Medium (evidence) — The out-of-sample bench is out-of-sample in case files, not in supplier behaviour, and its n is inflated
`docs/EVALUATION.md:85-111`, commits c3e0bd6, c99893a, b15060f, `competition.py:611`, `hosted_like.py:237`, `:323`, `:407`

1. **Waves are exact repeats.** The probe and a seeds 0-1 × 3-wave run give identical scores across waves for all 18 (case, seed) pairs.
   - "Scripts, 225 runs" is therefore 45 distinct samples. `competition.py:611` says as much; the doc does not.
2. **Seed noise exceeds the claimed effects.** P16 scores 0.933 at seed 0 and 0.704 at seed 1. The fixes are reported as +0.005 to +0.009 on the mean (0.804 → 0.809, 0.796 → 0.805).
   - These are paired and deterministic, so the sign is reproducible. But they are changes *of the replica's response*, not evidence about unseen suppliers.
3. **Strategic gains are built in.**
   - Clause: see L4. The probe on P16 gives 0.817/0.640 without the clause sheet and 0.933/0.704 with it.
   - Lead time: see L6.
   - Health term: the replica scores it with the agent's own estimate, so P14 (stabilise) scores exactly 1.0 on every seed, and P17 does too.
4. **What does transfer:** the four bug fixes in c3e0bd6 (fallback walk-away with rounds left, price unit, opening above C_open, a 38 % ask). They are general and the zeros are real.
- **Fix to the docs:** say "45 runs (9 cases × 5 seeds; waves repeat)". Label clause, lead-time and relationship gains as "replica-only".

## L6 — Low — A longer lead time "is worth something to the supplier": the hosted evidence is for the other direction
`routed.py:63-67` (`SUPPLIER_FAVOURS`), `routed.py:283-304` (`_free_terms`), commit b15060f, `hosted_like.py:82`

- **The evidence:** hosted P10 priced 54 → 42 days at +6.5 %. That is a *faster delivery than the supplier's own standard*, which costs it.
- **What `_free_terms` asks:** a *slower delivery than its standard* (P12: the quote's 21 days moved to 23). That a faster delivery costs the supplier does not mean a slower one earns it anything.
- **Replica:** it is linear and symmetric (`0.005 × p0` per day). The probe gives −0.10 EUR/unit for 21 → 23 days and +0.25 for 21 → 16. The bench gain (+0.005) is that formula.
- **Cost in C:** none (`late_cost × max(0, lead − need)`), so the downside is small. It does tell the supplier our exact need on every sheet, which the brief's "don't leak urgency" tip warns against. PLAUSIBLE, untested.
- **Fix:** keep it, but don't cite P10 or the bench as evidence of a gain.

## L7 — Low — "Our model rates them alike, the arena does not" (P02 payment term) is false; the hard-coded max hides the real cause
`routed.py:362-368` (3698c0a)

- **Deal journal against our model.** Our `cost_breakdown` reproduces the arena's C within about EUR 900 and ranks the three terms in the same order:

  | Payment term | Price | Score | Arena C | Our C |
  |---|---|---|---|---|
  | 30 days | 357.19 | 0.634 | 2,252,844 | 2,251,941 |
  | 60 days | 359.30 | 0.684 | 2,250,455 | 2,250,299 |
  | 90 days | 361.42 | 0.735 | 2,248,046 | 2,248,638 |

- **At each term's health floor,** our model gives 30 d 2,250,749, 60 d 2,249,108 and 90 d 2,247,447. It already prefers 90 days.
- **The real defect:** `_clause_sheet` keeps the payment term of the cheapest *clause-free* sheet.
- **Why the fix generalises poorly:** `max(days)` equals our argmin only while the buyer's rate (0.08 in all 19 cases) exceeds the fixed supplier estimate of 0.07.
- **Fix:** choose the clause variant's payment term by `decision_cost` over the options.

## L8 — Low/Medium (crosses into bugs) — The "held package" is not held
`routed.py:316-329` (`_held_package`)

- **Premise:** one sheet on *our previous lead package*.
- **What happens:** when none of this round's proposals keeps that package, the fallback is our cheapest sheet, which may change terms.
- **Probe (P16, clause sheet off):** the lead was `(70.06, none, 24 m, 30 d)` and the "held" sheet is `(75.20, none, 24 m, 90 d)`, so the payment term moved.
  - In the script flow this is the normal case: the round-1 opening uses the supplier's own terms and round 2 picks the cheapest package.
- **Fix:** reprice the previous lead's terms with `price_within`, instead of falling back to a different package.

## L9 — Low — The news and hedge classifiers contradict themselves or over-match
`facts.py:34`, `:37`, `:46`, `:151-163`, `:195-207`

- **Contradiction:** `short_time_work` is in both `NEED_WORK_TOPICS` and `CASH_STRESS_TOPICS`.
  - As need-work news it is cited as a reason for a lower price, with priority 3, as strong as a forecast.
  - As cash stress it marks the supplier as one that "cuts one step per e-mail however much it is shown".
  - The brief warns that a cash-short supplier should be paid in timing, not squeezed. For relationship goals our health estimate (a fixed 7 % rate) does not see the stress.
- **Over-match:** `HEDGED` matches "We have secured additional capacity until March". Any "secured … until" sentence silently removes every index-fall fact for that supplier.
- **The evidence itself:** "Hosted suppliers took 8 of 8 price histories" (13f781d) means *verified*, not *persuaded*. The persuasion evidence is 31.50 vs 31.70, n = 1 per arm, from different teams.

## L10 — Low (evidence) — Deterministic repeats are reported as replications
Commits 08bbfc7, 3698c0a, d0c0f40

- **Repeats:** "0.855 × 3", "0.707 × 3", "90 days 0.735 (5 runs)" and "5 deterministic runs per arm" are identical outputs. Each arm is n = 1:
  - `heldB-P08` is [0.8555] × 3.
  - `heldB-P18` is [0.7068] × 3.
  - P07 is [0.7234] × 5 against [0.721] × 5.
- **What still holds:**
  - The held-sheet effect (+0.14 to +0.21) exceeds arm A's own spread (0.645-0.715), so it is credible for *those two suppliers*. That is why L3 matters.
  - The P07 A/B (−0.002) was correctly not merged.
- **Fix:** report "n = 1 per arm (deterministic)" and judge rules by the number of distinct cases.

## L11 — Low — Prompts and docs contradict the code
- **`FINAL_RUNBOOK.md:64`:** see L2.
- **`FINAL_READINESS.md:75`:** says the fallback "otherwise walks away". Since c3e0bd6 it holds instead while rounds are left (`routed.py:1186-1191`).
- **Accept rule (`llm.py:110-111`, `:232-233`):** the prompt says "Accept only final offers and agreements, or an open offer that meets your last ask". `_premature` lets an open offer above our ask through at ≤ 3 moves left, or after two equal counters. Round 2 A8, still open.
- **Ask limit:** the ASK LIMIT line shows the model `ask_limit` only. `validate` now also caps at `_rival_limit` and `_opening_limit` (`routed.py:177`), so the model is shown a looser limit than the code enforces.
  - `_rival_limit` compares a rival's *cash* C, while acceptability uses the decision C with the health term. For relationship goals these differ. PLAUSIBLE, minor.

---

## Round-2 logic status (FALLACIES A1-A9)

| Round 2 | Status at d842b8e | Note |
|---|---|---|
| A1, concession inverted | **Open** | L1. The probe matches round 2 exactly |
| A2, mirror to final offer | **Open** | L2. "3 of 3" comment unchanged |
| A3, circular replica | **Open, wider** | The OOS bench reuses the same supplier, health estimate and clause valuation (L4, L5). The docs now carry a caveat (EVALUATION.md "Limits") |
| A4, single runs cannot rank | **Partly fixed** | EVALUATION.md now states ±0.02. New commits repeat the pattern with deterministic repeats (L10) and 5× inflated bench n (L5) |
| A5.1, fact pacing contradicts its premise | Open | Unchanged |
| A5.2, any uncited ID exempts from movement | Open | `_new_fact` is now per case (fixes the B2 half), but still not "strong only" |
| A5.3, shrug attributed to every kind | **Open** | The round-2 probe still gives `['cost','fc','idx']` |
| A5.4, unsourced PRIORITY | Open | Now also `supplier_news: 3`, no source |
| A5.5, rival claim cites a higher price | **Fixed** | `facts.py:320-325`. Round-2 probe returns no fact |
| A6, round-1 walk-away with an alternative | **Fixed** | The model walk-away is blocked while any thread has more than one move left. The fallback holds (`_safe_hold`). The script can still walk at terminal offers (intended) |
| A7, near-close below a named floor | **Partly fixed** | Sentence-final floors and "1,250.00" are now parsed. The near-close still asks 17.87 under a named 18.00 |
| A8, accept prompt against code | Open | L11 |
| A9, timeout cost assumed | Open | New evidence that timeouts are not free: P05 (d842b8e) lost its standing offer to a lapse while we worked on another bidder. The runner still lets idle bidders time out |

### Round-2 merged list (README), logic-relevant items

| # | Status |
|---|---|
| 1a, limit sheet to a final offer (B1) | Fixed (`status == "open"` added, `routed.py:216`) |
| 1b, mirror | Open (L2) |
| 2, catch-all turns bugs into moves | Partly fixed (traceback logged; a text-filter failure now sends the unfiltered body) |
| 3, concede into a stall | Open (L1) |
| 4, late accept dropped | Fixed (`play.py:637-639`) |
| 5, rival claim price | Fixed |
| 6, cited IDs per case / shrug | IDs fixed; shrug open |
| 7, walk-away gate | Fixed |
| 8, floor parsing / near-close | Parsing fixed; near-close open |
| 9, replica cannot validate | Open, now also the OOS bench (L5) |
| 10, single runs | Partly fixed (L10) |
| 11, 12 | Not logic; not re-examined |

## Before 13:30 (small, local, testable)

1. **L3:** restore the relationship-goal check on `_clause_sheet`, i.e. revert the goal widening in c99893a. This is one line and re-enables the held-sheet rule on leverage clause cases.
2. **L2:** skip `_mirror_alternative` unless another live supplier has an acceptable offer. Fix the "3 of 3" comment.
3. **L1:** if anything, only the cap of our step at the supplier's last concession. The full reciprocal step needs hosted runs, which the deterministic hosted suppliers make cheap: one run per variant.

Everything else is documentation or after-final work.
