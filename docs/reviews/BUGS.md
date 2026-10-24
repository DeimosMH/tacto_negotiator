What this change does:

This reviews the **current project at commit `4aca02d`**, not just the archive-restoration commit. Tacto Negotiator opens supplier sessions, reads their quotes, chooses scripted or model decisions, validates terms, and sends actions before the supplier and wave deadlines.

The assumed Sunday load is five 480-second waves, up to ten concurrent cases, 60-second buyer windows per supplier thread, and a shared arena allowance of ten requests per second ([readiness assumptions](../FINAL_READINESS.md), [API timing contract](../../materials/starter_kit/brief/api.md#time-errors-and-limits)). The review followed the CLI, HTTP client, runner, routed/legacy policies, document and cost helpers, data access, simulation, competition and accounting paths and relevant callers/tests. It changes no application code.

**Must fix:**

1. **A round-limit error can make the buyer accept an expensive current offer using an old cheap price** — P1; high confidence. ([`play.py:L119–130`](../../src/tacto_negotiator/play.py#L119-L130), [`play.py:L100–104`](../../src/tacto_negotiator/play.py#L100-L104))
   - **What this is:** `RemoteTable.missed()` recovers from every send/accept `409`, including the documented round-limit response, and its synthetic message feeds the next routed decision through [`play.py:L265–277`](../../src/tacto_negotiator/play.py#L265-L277). The [thread contract](../../materials/starter_kit/brief/api.md#L107-L124) separates the current standing offer from historical timeout answers; acceptance must evaluate the current offer's own terms.
   - **Problem:** The method combines the current `offer_id` with `timeouts[-1].terms`, even when that timeout belongs to an older offer; the next timer refresh sees a matching ID/status and leaves the false price intact. In `stale_timeout_acceptance`, resuming after one timeout and reaching the three-round limit causes the actual runner and routed script to accept a €25 current offer using the earlier €17.47 timeout quote, despite a €20 alternative: **C(deal) = €170,077.44; C(alternative) = €136,487.56**, with zero model calls.
   - **Fix:** Recover the current supplier message and its terms as one matched offer, refreshing the full thread after a `409`; never put a new offer ID on a historical timeout's terms. If a matching current offer cannot be recovered, block acceptance until it can.
   - **If we skip it:** A routine recoverable API error can produce a real, irreversible acceptance that passes the common guard only because the guard was given the wrong price. Counting only buyer messages in [`moves_left()`](../../src/tacto_negotiator/play.py#L155-L157) makes this reachable after timeout rounds without needing another process or a concurrent buyer.

2. **Validation silently changes legal supplier-standard terms after package pricing** — P1; high confidence. ([`routed.py:L154–162`](../../src/tacto_negotiator/agents/routed.py#L154-L162), [`cost.py:L189–210`](../../src/tacto_negotiator/cost.py#L189-L210), [`routed.py:L239–246`](../../src/tacto_negotiator/agents/routed.py#L239-L246))
   - **What this is:** The [API explicitly permits a supplier's standard value outside `term_options`](../../materials/starter_kit/brief/api.md#L287-L289), and [package search says it includes these values](../ROUTING.md#L63). Both model proposals and the script's packages pass through `validate()`, which first permits such a value and then calls `complete_offer()`.
   - **Problem:** `complete_offer()` snaps that already-legal standard value back to a listed option: the reproduction's payment term changes **90 → 30 days**, raising C from **€121,030 to €122,626**. On the full `decide()` script path, standard DAP freight with only EXW in the option list is priced as DAP and sent as EXW, producing **€39,990, €39,993.50 and €39,998.67 packages against a €30,000 alternative**; `_packages()` checks its original terms rather than the transformed wire terms.
   - **Fix:** Preserve validated supplier-standard values when completing outgoing sheets, and calculate package ceilings from the exact completed terms that will be sent. Keep snapping for incomplete or invalid legacy inputs, rather than applying it again to values the routed guard has already accepted.
   - **If we skip it:** The buyer sends a different commercial commitment from the one it evaluated, and deterministic package search can exceed its own alternative ceiling. This does not by itself bypass the later acceptance guard, but it worsens proposals and can spend scarce rounds negotiating the wrong package.

3. **An unparsed index clause becomes “no index clause,” allowing a forbidden deal in the simulator** — P2; high confidence for the local reproduction; hosted occurrence unverified. ([`docs.py:L25`](../../src/tacto_negotiator/docs.py#L25), [`docs.py:L46–57`](../../src/tacto_negotiator/docs.py#L46-L57), [`sim.py:L45–53`](../../src/tacto_negotiator/sim.py#L45-L53))
   - **What this is:** A missing term closes on the supplier's quote defaults, and `index_clause` may be a structured object or null under the [document/term contract](../../materials/starter_kit/brief/api.md#L273-L299). The routed guard promises that [unreadable defaults will block acceptance](../ROUTING.md#L95-L97), but `_value()` returns null for every clause string its regular expression cannot parse.
   - **Problem:** The actual simulator PDF writer prints a clause dictionary as Python text, which its paired parser silently converts to null; `index_clause_disappears` then calls `RoutedAgent.closing_move()` and accepts that indexed offer despite a must-have `index_clause == null`. The simulator resolves the real default and returns **`must_haves_ok: false`, `score: 0.0`**; all three shipped demo cases and all ten current benchmark families use fixed null clauses, so they do not exercise this defect.
   - **Fix:** Treat only an explicit supported “none” representation as null and reject any other unparsed clause, so the acceptance path fails safely. Format structured clauses in simulator PDFs using the same representation the quote parser supports.
   - **If we skip it:** Local tests of a supported indexed procurement case can approve a forbidden contract, and an unrecognized hosted clause representation would have the same unsafe parser behavior. No hosted PDF was fetched, so this report does not claim that today's hosted renderer produces that representation.

4. **An opening with no live bidders crashes before the final result is read** — P2; high confidence. ([`play.py:L210–222`](../../src/tacto_negotiator/play.py#L210-L222))
   - **What this is:** The API permits the supplier status [`no_bid`](../../materials/starter_kit/brief/api.md#L194-L200), and the runner already classifies it as ended in [`ENDED`/`live_threads()`](../../src/tacto_negotiator/play.py#L150-L152). A case whose opening messages all decline should follow the existing no-live-thread path and return `table.result()`.
   - **Problem:** For a timed agent, the initial loop calls `read_deadline()` for every opening message even when it has no attachments; that function takes `min()` over live suppliers without handling an empty list. `opening_no_bid_crashes` returns **`ValueError: min() iterable argument is empty`**, and the result reader is never called.
   - **Fix:** Return the case result immediately after opening if no live supplier remains, before calculating document deadlines. Add one regression case for an all-`no_bid` opening using the routed agent.
   - **If we skip it:** A valid no-deal outcome is reported as a failed case, losing its terminal result and score from the local run summary. `play_waves()` isolates the exception but does not recover that case automatically.

The reproductions are in [`repro_bugs.py`](repro_bugs.py), in finding order. The freight example in finding 2 was independently supplied and confirmed by the logic reviewer; it is counted only here.

Run exactly this from `/tmp`:

```sh
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  /home/deimos/Documents/git_prv/tacto_negotiator/.venv/bin/python \
  /home/deimos/Documents/git_prv/tacto_negotiator/docs/reviews/repro_bugs.py
```

Result: exit 0, four JSON observations matching the failures above, about 0.5 seconds. Its assertions confirm the defects in the reviewed code; they are not passing safety checks. The probes use in-memory clients, simulator PDFs, injected time and a disabled model function; no credentials, dotenv files, hosted services or real model calls are involved. Ruff passed for the reproduction file.

The coordinating reviewer separately reported the baseline as **221 tests passed in 25.89 seconds**, with Ruff, 11 documentation tests, the atlas snapshot and browser checks passing. Those checks do not cover these four edge cases.

Already-documented limits are not presented as new findings: sequential RFQ supplier requests, missing supplier-health estimates, incomplete prose-claim grounding, legacy LLM deadlines, cooperative model cancellation, HTTP phase rather than total arena-read limits, missing automatic case recovery, and indistinguishable pre-start/revoked-key `403` handling are explicitly described in [readiness](../FINAL_READINESS.md) and [routing](../ROUTING.md).

Verdict: fix 1–4 first.

Not checked: the hosted arena, live model behavior, venue/provider capacity, Docker execution, and hosted quote formatting; all runtime reproductions were local and credential-free.
