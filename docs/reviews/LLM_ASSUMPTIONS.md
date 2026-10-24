# LLM assumptions audit (hosted v6-v12)

Scope: the 147 buyer e-mails the model wrote (routes `strong_unconfigured` / `semantic_decision`) in the 67 hosted
practice sessions of `docs/results/live-practice-2026-10-24-opus-high-v6.json` ... `-v12.json`. I read each one next to
the supplier's reply, its `reasons` and `claim_results` (read-only session traces). Counts are e-mails unless stated.

## Findings

| Error type | Count | Examples (session, round) | Supplier reaction / outcome |
|---|---|---|---|
| Naming the supplier's people or processes (pricing committee, owner, management, "authorized review") | 95 e-mails in 50 sessions; 45 repeat an ask already made in that thread | P03 `da7b2c668a4c1dde` R1-R3 asked the committee three times; P11 `ee6ccdc96a0759e5` R2-R4 "review with management authorized to approve pricing" (0.231) | Most asks come from our own meeting notes ("changes above 2 % need the pricing committee"), and our prompt told the model to ask. The model kept asking after "management has frozen all price reductions ... we cannot submit your term sheets to a pricing committee" (P03, 6 sessions) and after P10 said "we do not conduct committee reviews" (`844888567ecff183` asked again in R2). P11's managing director "cannot sign anything that lowers our margin", yet the model asked him for a pricing review in 5 sessions. No ask was ever followed by a price move. |
| Percentage, threshold or cost share with no data ID | 45 e-mails, 50 sentences, 32 sessions | "As the change exceeds 2 %, could your pricing committee ..." (37 sentences); "Copper is roughly a fifth of the motor's cost" (`55cc9258fda7580c` R4); "together roughly 2.4 %, well below the requested 9.5 %" (`dd4ac91e03e680c2` R3) | 20 supplier replies asked for data: 12 x "Which figures is that based on? Please send us the data.", plus "please provide the underlying data" for copper, "secure base load" (`844888567ecff183`), "planning security" (`1329434c4c9b057b`), "call-off flexibility" (`b3028c8c11012400`) and the computed cost shares. Each one used up a round's argument. |
| Citing a fall in an index the supplier has hedged | 7 of 7 P08 openings (`6c66343893e91ad8`, `3f5810df51efda2d`, `a0cd71e6a58fa030`, `4cccc9b16dc65a9e`, `e625eb72845cc146`, `16d8369e4f1f650f`, `f514e91ed2038626`) | The same e-mail also quotes the supplier's letter: "covered at fixed prices until March 2027" | "That is not our reality: our AS-14404 is covered at fixed prices ..." every time. P08 scored 0.54-0.68. |
| A second window of an index already cited | 14 claims (12 in model e-mails): PPI-D351 2025-11..2026-04, then 2025-08..2026-08 | P01 `852eb6639eb88bfb` R3, P11 `88fa911330fc6e2b` R3 | `duplicate` / "already taken into account": the claim was wasted. The existing ID dedup only matched exact IDs. |
| Prose describing a term sheet that was not attached | 11 e-mails "24 or 36 months" with only 24-month sheets attached; 2 payment-day mismatches (`6fcc6b129b02db75` R2 "30-day payment" with 60/90 sheets; `da7b2c668a4c1dde` R3) | P18 `fbb8d074d0b5ca72` R2, P10 `fbc0b93a1d8b06db` R1 | Code-induced: `validate` drops sheets (equal-cost filter, 0.9 x top price) after the model wrote its prose. The supplier reads an option it cannot accept. |
| Blaming or pressure wording | about 8 sentences, mostly P11 | "defect costs are a real burden for us", "the quality cost of this part sits with us", "binding improvement plan for the red scorecard" (P11); "Your cost picture omits ..." (P08 `e625eb72845cc146` R1) | P11 (a supplier short of cash) scored 0.59-0.76 with this wording. The other team's rule 86 reports high first counters after similar phrasing. |
| Invented claims about our own organisation | 4 | "our Supplier Quality team needs a cost-recovery mechanism", "our production plans on that basis", "our category team would gladly share index data" | The last one drew "Our calculation stays internal, so we do not require your index data." |
| Revealing budget, urgency, alternatives or limits | 0 | - | - |
| Reading a supplier alternative as a refusal; promising a lever we cannot negotiate | 0 found | P11 correctly says payment is fixed | - |
| Claim contradicted or unknown ID | 0 contradicted out of 213 checked claims (199 verified, 14 duplicate) | - | - |
| Two figures in one claim sentence | 4 (P11 "2376 ppm and 63.5% on-time delivery (sc:...)") | - | Verified. Not guarded. |

How these relate to scores: the effects are confounded across versions. P02 runs that repeated the committee ask (v6-v9) scored
0.476-0.499 and single-ask runs (v10-v12) scored 0.684-0.735, but `66df9d485056386e` asked once and scored 0.488, and
v10 also brought in the clause sheet. The only statement the data supports is that no ask or ID-less assertion ever
produced a concession. Several made the supplier ask for data, refuse, or mark a claim as a duplicate.

## Guards added

- `agents/llm.py` SYSTEM (wording only):
  - every figure needs its ID, and code drops ID-less ones;
  - never cite a series the supplier has hedged, and cite only one window per series;
  - code may adjust or drop sheets, so prose carries only cited figures and the volume answer;
  - name the concern you remove and never blame;
  - ask for an authorized review at most once, only when the correspondence names that person, never after a
    refusal or price freeze, and never of someone who cannot lower margin; no other mention of the supplier's
    people or processes, and no invented ones of ours.
- `agents/routed.py`:
  - `_strip_wasted_claims` now drops another window of an `idx:` series already cited.
  - New `_grounded_body`, run inside `validate` on every outgoing body (from scripts and from the model), drops:
    - sentences that name the supplier's roles or processes, unless the supplier's words or the case dossier name
      them, and at most once per thread;
    - ID-less percentages and cost shares, unless the figure is an attached deadband or a percentage the
      supplier itself wrote;
    - term values (months, days, ppm) that are in neither the final sheets nor the supplier's terms or words, the
      quote's standard terms, the fixed terms or the buyer data. Lists shrink to their supported values ("24- or
      36-month" -> "24-month").
  - Sentences that cite an ID, and continuations of them ("The same scorecard ..."), are always kept.
- Tests: 5 new in `tests/test_routed.py`.
- Offline replay of the 139 single-supplier model e-mails: the guard changes 89. The replay has no quote-PDF standard
  terms, so a few term-value drops there (for example "7-day lead time") would survive live.

## Residual risks

- Our own contract terms in prose ("current contract ... 90-day term") count as unsupported unless the value is
  also in a sheet, in the supplier's terms or in its words.
- Grounding a role uses the whole dossier. In an RFQ, a note about one bidder grounds the same role for another.
- Hedging, blame and invented claims about our side rely on the prompt only.
- Duplicate arguments without IDs, such as a repeated qualitative point, are not detected.
