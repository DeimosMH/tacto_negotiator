# Reading a deal

Every hosted negotiation is stored as it happens and can be read like a chat: what each supplier wrote, what we sent back, when, and which part of the buyer program chose each move and why. Open the [deal journal](index.html#deals) under **Negotiations → Deal journal** (`docs/index.html#deals`).

## What is stored

`play()` records every case it plays against a hosted arena: practice, the final, or a sandbox. The recording lives in [deals.py](../src/tacto_negotiator/deals.py). It wraps the buyer's decision calls and the arena sends; it never changes a move, and a recording error is only logged.

After every move it writes three files:

| File | Content |
|---|---|
| `docs/deals/<date>_<case>_<session>.json` | The record: case, start and end time (UTC), every message with its time, every decision with its path, the result and its cost breakdown |
| `docs/deals/<id>.js` | The same record in a form the page can load from `file://` |
| `docs/deals.js` | The index: one line per deal, newest first, and the decision tree |

So a live case shows up within seconds, with status **live**, and becomes **ended** with its score when the arena closes it. `TACTO_DEALS_DIR=off` switches recording off; any other path moves it. Tests never record.

Older hosted practice sessions were backfilled from the saved results: `tacto deals docs/results/live-practice-*.json` reads those ended sessions from the arena (read-only, no quota) and adds the decisions saved in each report. Their message times are not known, so they carry the run's start time and say so. `tacto deals` alone rebuilds the index.

## The deal list

Deals are grouped by day. Each row shows when the case started, the case and its title, the status, the score, the supplier and price of the deal, the number of messages, and how many decisions went to the model and how many fell back to a script. **Find** filters by case, title, supplier or run name. **Auto-refresh every 5 s** reloads the index while a run is going, so you can watch the final without touching the runner.

## One deal

The top of the page shows the score, the deal, and its total cost C next to the opening cost and the best possible cost, followed by the cost components (price, payment terms, quality, freight, supplier health and so on). **Arena replay ↗** opens the organisers' replay, which also shows the supplier's reasons.

**The conversation** (left) shows supplier messages on the left and ours on the right, each with its time. Term sheets appear as small tables; the accepted sheet is highlighted. Under a supplier message, claim checks show as `verified`, `contradicted`, `irrelevant` or `duplicate`. The tabs switch between all threads and one supplier at a time. Each of our messages links to the decision step that produced it.

**The decision path** (right) is the buyer program's decision tree:

- **Decision request:** the runner asks for one move whenever a supplier replied or a clock needs an answer.
- **Runner closes / No time left:** the wave cap or the decision budget is spent, so the best acceptable offer is closed.
- **Scripts first:** deterministic rules for the known situations. When every thread is final, it accepts the best offer or mirrors a cheaper alternative. It also asks for missing quote terms, accepts a named floor, and runs the structured bargain (openings, bounded concessions, RFQ comparisons, must-have repairs and accepts on a small gap or the last round).
- **Model decision:** a supplier message the scripts do not cover (conditions, questions, claims) goes to Claude Opus. Its proposal always passes the **shared gate** (`validate`), which reprices it to the concession schedule and blocks unsafe moves. If the model times out or is rejected, a scripted **fallback** replaces it.
- **Near-close ask:** accepting a still-open offer becomes one last ask just below it.

Branches this deal used are solid and carry how often they were taken; unused branches are faded. Select a branch to list only the steps that went through it, or select a step to highlight its whole path. Each step shows its route (script, fast, fallback, concurrent), the path in words, why that branch exists, what was sent at what prices, how long the decision took, and the supplier's reply. A step marked **not sent** was decided too late: the runner let that thread time out once instead of sending a stale move.

## Reading a deal to improve the agent

- **A low score with few rounds:** look for an accept step early in the path, and for which branch made it (`small_cost_gap`, `round_reserve`, `terminal_offers`).
- **Fallbacks:** the step's error says whether the model timed out, was rejected by the gate, or hit a provider error.
- **RFQs:** use the per-supplier tabs to compare what each bidder offered at the same time. The gate never offers one bidder a sheet dearer than another's standing acceptable offer.
- **Claims:** `duplicate` or `irrelevant` results show wasted arguments; `contradicted` costs trust.
