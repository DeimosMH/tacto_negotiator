# What earlier teams learned on the same arena

Research on 24 October 2026 with the `tech-research` skill. Only the web source ran: no browser for X/Grok, DeepWiki not configured. The repositories were cloned and read directly; no code was run or copied.

The same Tacto arena and the same ten practice cases (P01–P18) were used at the **EHL Grand Finale, Munich, 10–11 October 2026**. Teams published their agents afterwards.

| Team | Result | Source | Licence |
|---|---|---|---|
| Codacabana (ProcureCabana) | Jury winner; 2nd on the board, 347.0 of 500, 29/29 deals; practice best-per-case mean 0.812 | [stegra05/ehl-final-procurecabana](https://github.com/stegra05/ehl-final-procurecabana) | None stated: ideas only |
| Fake It Till You Make It | 3rd, 341.5 points, 29/29 deals; practice runs 0.67–0.70 | [CL1nu5/munich-ehl-2026-final](https://github.com/CL1nu5/munich-ehl-2026-final) | MIT |
| sellm | 1st, 351.6 points | not found | — |

Practice board, Saturday night of that event: best team 0.836, Tacto's own agent 0.697. Our best-per-case mean on 24 October is 0.635.

## Where both top teams agree

1. **Code owns every number; the model picks among priced options and writes.** We already do this.
2. **An ambitious, explicit opening.** Codacabana opens at 0.78 (leverage) to 0.92 (stabilise) of the quote, higher for frozen prices, full capacity or near-cost suppliers. Fake It found that the attainable C_best prices sat 12–25 % below the openings (P05 −25.6 %, P18 −22.5 %, P08 −15.7 %, P07/P10 −12.5 %), while their first asks were only 4–8 % below. They raised the anchor to 18 %, at or below today's price in increase/renewal cases, below the cheapest rival bid in RFQs, and never below the point where squeezing a relationship supplier costs more than it saves.
3. **A concession schedule that always moves.** Suppliers concede only in response to a move, and an unchanged sheet draws nothing ("no concession without reciprocal movement"). Codacabana closes 30 % of the gap each round and holds after a large supplier concession. Fake It moves 10 % of the gap when the supplier stands still, more when it moves.
4. **Two or three term sheets of equal cost to us** that differ in what the supplier values, so its choice reveals preferences for free.
5. **Supplier tags from the data** (cash short, price list frozen, hedged forward, sold out, spare capacity) decide which levers are cheap for us to give: earlier payment for a cash-short supplier, longer contract for one with idle capacity.
6. **Closing rules.** Accept only a standing sheet. Hold an acceptable agreement while a rival can still beat it. Before refusing a final offer, send one sheet at the price ceiling. Fake It also accepts when the offer is within 5 % of the way from the opening cost to our last ask, after the cheapest P05 bidder walked over a 0.7 % gap.

## Observed supplier behaviour (from their practice and sandbox runs)

- **Citing a rising index confirms the supplier's case** ("your figures confirm our case", 14 replies). Cite only falls: the steepest 3–12-month window ending in the last 6 months, at least −1 %.
- **Purchase-order price history moved no supplier** (0 of 23 citations).
- **Verified claims work mostly in the first round.** One sandbox supplier countered at 51.30 without claims and 50.00 with three. Irrelevant claims had no effect.
- **Bluffs and urgency changed nothing**, and all twelve suppliers asked declined to send a cost breakdown.
- **Volume questions need figures.** "Which volume can you commit to?" recurred in P02, P04, P05, P11 and P18; a vague answer lost the cheapest P05 bidder.
- **Final offers come at a fairly fixed round** whatever the anchor (P10 round 2, P08 3, P02 4, P01 5–6), or after a cumulative gap of 36–42 % (patient suppliers) or 15–19 % (impatient).
- **The cheaper rival bid should lead each RFQ message.** RFQ cases improved from 0.67 to 0.82 for Fake It.
- **After a session ends, `GET /v1/sessions/{sid}/backoffice` returns the suppliers' back-office log.** Fake It used it to learn what each supplier was about to do.
- Accepting a supplier's *alternative* sheet ID was rejected with 422; mirror the alternative back as our own sheet instead.

## What this means for us

Our replays already show the timid-anchor and standstill patterns: P10's first ask was 29.2 against a 32.2 quote (−9 %), and the supplier issued its final offer at round 2. The highest-value changes, in order:

1. An explicit anchor and gap-closing concession schedule in the scripted bargain.
2. Index claims only for falls; drop `po` price-history claims.
3. Answer volume questions in figures from our own sheets.
4. Read the back-office endpoint after practice sessions to calibrate 1 and 3.
5. Lead RFQ messages with the cheaper rival bid (we already cite feasible rival offers).

One practice run per change is noisy: Fake It saw the same first ask score 0.47–0.72 in P08. Keep a change only when it helps across several suppliers or fixes a defect reproduced offline.
