# Bug review, round 3: `src/tacto_negotiator` at `d842b8e`

Review date 2026-10-25, before the 13:30 Live Final. Scope: **bugs only**, meaning code that does
something other than its docstring, its caller, the API contract
(`materials/starter_kit/brief/api.md`) or the tests expect. Logic fallacies and bloat are covered by
the other two reviewers.

- **Read line by line:** `git diff 5dea00a..HEAD -- src` (28 commits). That covers `routed.py`
  (`validate`, `_free_terms`, `_imposed_clause`/`_held_package`, `_equal_cost_clause`, `_opening_limit`,
  `_rival_limit`, `_safe_hold`, the fallback), `play.py` (the B4 fix and the 422 retry), `facts.py`
  (`_news`, `_hedged`, `used`, `_rival_offers`), `levers.py` (`order_path`, `_PRICE`), `completion.py`
  (fast mode), `data.py` (`supplier_estimates`) and `deals.py` (`recorded_play`/`recording`). The rest of
  `play.py`, `client.py` and `cost.py` was read where the new code depends on it.
- **Not read in depth:** `hosted_like.py`, `competition.py`, `compete.py`, `benchmark.py`, `web.py`.
  These are offline bench and UI code, and none of it runs in the final.
- **Test suite:** **349 passed** (31.6 s) when run from an empty directory. The documented command
  (`cd /tmp && ... pytest`) gives **18 failed, 7 errors, 324 passed** on this machine. See B6.
- **Repro:** `.venv/bin/python docs/reviews/round3/repro_bugs.py`. It runs offline, with no keys, and
  turns off the deal journal (`TACTO_DEALS_DIR=off`). It prints one line per finding. The output was
  checked against this report.

Expected impact on the final is modest. No new High bug was found. The round-2 High (B1) is fixed.

---

## Findings

### B1: Medium: `_opening_limit` counts openings that break a must-have, so it caps asks to the compliant bidder
`agents/routed.py:611-630` (applied at `:177`)

- **What it does:** `_opening_limit` returns 0.999 × the cheapest **opening of any bidder**. It never
  calls `must_haves_ok`. Every repriced sheet to every supplier is capped at that cost.
- **What it should do:** api.md (Cost and score) defines `C_open` as "the cheapest opening quote
  **(among those that keep the must-haves)**". The docstring gives the same reason ("C_open is the
  score's zero").
- **Failure scenario:** in an RFQ, bidder A quotes compliant terms and bidder B quotes cheaper terms
  that break a must-have, for example a lead time over the limit. C_open is A's opening, but every ask
  to A is capped below B's non-compliant cost. That cap holds for the rest of the case. The
  "move at least half a step" rule (`:209`) needs `limit > ours`, so once the cap binds, the sheets to
  A stop moving and the same ask repeats. Repeating the same sheet is the pattern behind the hosted
  P02/P18 walk-aways that the code comments cite.
- **No-retreat floor missing:** unlike `_rival_limit`, this limit has no floor at our last ask. If B's
  quote PDF only becomes readable later (`reread_skipped_documents`), the cache fills mid-case and the
  next ask to A can retreat below the previous one. This was found by reading the code, not reproduced.
- **Evidence:** `B1` in the repro, on S02 with the must-have `lead_time_days <= 42`. B opens at 13.70
  with 49 days (`must_haves_ok` = False). Our ask 13.50 is countered by A at 15.40, so the schedule
  gives 13.88. The sheet to A goes out at **13.72**. With the limit switched off it goes out at **13.88**.
- **Fix:** skip openings with `not must_haves_ok(case, terms, std)` before caching. Also return
  `max(best*0.999, our last ask)` as `_rival_limit` does.

### B2: Low: a 422 "not a standing offer" retry does not record the timeout that caused it
`play.py:656-680`

- **What it does:** the new stale-accept path sends the 422 through the shared error branch. That
  branch sets `received[sid] = now`, pops `lapsed`, and adds to `lapses` only if the detail contains
  "late". A 422 detail ("not a standing offer in this session") never does.
- **Why that is wrong:** the offer is stale because a timeout answer replaced it (commit message, hosted
  P05). One consecutive timeout really happened.
- **Consequence:** the next timer refresh sets `received` back to the arena's window start, so the clock
  is right. `lapses` stays one short, though. `last_chance` / `close_before_lapse` can then let a thread
  take what is really its third timeout in a row, which ends it, instead of closing on its acceptable
  offer first. This needs two earlier timeouts in a row, so it is a narrow case.
- **Evidence:** `B2`. The retry closes the case, but `mark_lapse` is called **0** times.
- **Fix:** when `stale`, do `lapses[sid] += 1` (and `mark_lapse`) as the 409 "late" path does.
  Alternatively, rely on the refreshed thread's `timeouts` count.

### B3: Low (PLAUSIBLE trigger): a 422 on a message ends `play()`, and the case is lost with offers standing
`play.py:538-545` (`send`), `:570` (`future.result()` in `send_all`), `:653-661`

- **What it does:** only 409 (any action) and 422 (accept only) are handled. Any other `ArenaError`,
  including a **422 on a counter**, propagates out of `_play`. `play_waves.one` then logs it, and the
  case ends at the wave cap without a deal (the alternative, or 0).
- **What it should do:** api.md says a 422 means "nothing happens, no round is used". The session is
  intact, so the runner could decide again or close on the best acceptable offer.
- **Possible triggers:** `validate` lets a value outside `term_options` through when it equals the
  supplier's standard value (`routed.py:160`). The arena may or may not accept that. Another trigger is a
  future validator gap. No hosted 422 on a send has been seen, hence PLAUSIBLE.
- **Evidence:** `B3`. A 422 on the first send makes `_play` raise. An acceptable offer stands and 10
  moves are left.
- **Fix:** on a 422 for a non-accept, log it, mark the supplier as unavailable for this round, and loop.
  After N such errors, `close(case, threads)`.

### B4: Low: fast mode is forced on every Anthropic client, and only a 429 falls back to standard
`agents/completion.py:205-212`, `:246-253`

- **What it does:** `LLM_SPEED` defaults to `fast` and goes into `shared`. That dict is passed to the fast
  client **and** to the strong client, whatever `LLM_MODEL` / `LLM_STRONG_MODEL` name. Fast mode exists
  only for Opus 5.5, Opus 5 and Opus 4.8 on the Claude API (claude-api reference). Another model gets an
  error, and only `anthropic.RateLimitError` is caught. A 400 (unsupported), or a 529/5xx while fast
  capacity is short, sends every model decision to the script fallback.
- **Failure scenario:** `LLM_STRONG_MODEL=claude-sonnet-5-5`, or `LLM_MODEL` set to a non-Opus model,
  makes every model call fail without notice. The final command in the README (Opus 5.5, no strong
  model) is **not** affected.
- **Evidence:** `B4` prints `speed=fast` for a Sonnet fast client and a Sonnet strong client. The only
  fast-mode error that falls back to standard is `RateLimitError`.
- **Fix:** set `speed` only for model IDs that support it. Fall back to standard speed on
  `anthropic.APIStatusError` with status 400, 429 or 529 (and 5xx) when time is left.

### B5: Low: `_strip_wasted_claims` drops a rival offer once it has been cited in any thread
`agents/routed.py:489-513`

- **What it does:** `cited` is the union of IDs over **all** threads, and any ref in it is removed. The
  round-2 B2 fix made `FactBank.used` follow the API rule ("each ID counts once per case, except a
  rival's quote … counts once per supplier"). This older filter was left as it was.
- **Where it runs:** `_paced_body` uses this filter when the bank has nothing for the supplier, and
  `validate(reprice=False)` always uses it.
- **Failure scenario:** offer `offer:70340-1` was cited in A's own thread. Later the model cites it to
  B, which is a legitimate rival quote, and the filter removes the ID. The figure stays without its ID,
  and that draws "please send the data" (the code's own `LLM_ASSUMPTIONS` note).
- **Evidence:** `B5` prints `'A competing offer is EUR 16.'`, with the ID stripped.
- **Fix:** exclude `quote:` and `offer:` refs cited in *other* threads from `cited`, as `FactBank.used`
  already does.

### B6: Low (tooling): the documented offline test command is shadowed by stray modules in `/tmp`
`docs/LOCAL_TESTING.md:26`

- `python -m pytest` puts the working directory first on `sys.path`. `/tmp/h2.py`, a scratch script
  from 24 Oct, shadows the `h2` package that `httpx` imports. As a result the documented command gives
  18 failures and 7 errors, all `FileNotFoundError` from `h2.py:3`. From an empty directory, the same
  suite gives 349 passed.
- **Risk:** a pre-final check could report false failures, or worse, a real failure could be dismissed
  as this noise.
- **Evidence:** `B6` lists `['h2.py']`.
- **Fix:** run from a fresh directory (`cd "$(mktemp -d)"`), or use `python -P` / `-I` with an explicit
  `PYTHONPATH`.

### Checked, no bug found (new code)
- **Round-2 B1 fix:** `validate:216` requires `status == "open"`. `_safe_hold` and `near_close_sheet`
  check for `open` too.
- **`_free_terms`:** sound. It moves only within `term_options`, at cash and decision cost equal within
  1 cent, with must-haves checked.
- **`_held_package` / `_imposed_clause`:** these read the shapes of the sent message and the reply
  correctly (`{"terms": …}` versus stored sheets).
- **`_equal_cost_clause`:** sound. It is called inside `validate`'s `try`, so a malformed clause rejects
  the move instead of crashing. See the round-2 R-A1 note below.
- **The B4 fix in `play.py`:** traced. An accept goes out when its own thread has more than 1 s left.
- **Fast-mode request shape:** `client.beta.messages.create(speed="fast", betas=[…,
  "fast-mode-2026-02-01"])` matches the SDK (anthropic 2.3.0 has both `speed` and `fallbacks`
  parameters) and the API reference.
- **Deal journal I/O inside the decision timer:** `write_index` over 357 records takes 0.07 s cold and
  0.01 s warm, so it is no timing risk. Recording errors are guarded and never change a move.
- **`_hedged`:** it suppresses index falls for every series of a supplier when one material is hedged
  (P08 sup:70710 loses AS-14404 and PPI-C2431). Hosted P08 rejected all 12 index citations, so this
  is a deliberate trade-off, not a bug. It is left to the logic reviewer.
- **`_PRICE` / `named_floors`:** "1,250.00" and a price at the end of a sentence now parse. The German
  format "1.250,00" still does not, but hosted e-mails use English decimals.

---

## Round-2 bug status

| Round-2 item | Status at `d842b8e` | Evidence |
|---|---|---|
| **B1** (High): message without a sheet to a `final_offer` thread gets a `_limit_sheet` | **Fixed** | `routed.py:216` requires `status == "open"` |
| **B2** (Medium): fact bank re-cites facts across bidders | **Fixed** in `FactBank.used` (per case, rival quote/offer per supplier) and therefore in `_new_fact`. **Left over:** `_strip_wasted_claims` overshoots the other way, see **B5** | `facts.py:349-355` |
| **B3** (Medium): rival fact cites a higher unit price | **Fixed** | `facts.py:320-325`: the rival's unit price must be below the recipient's |
| **B4** (Medium): late accept dropped although its own thread has time | **Fixed** for that case (`play.py:636-641`). **Still open (plausible):** a late accept on its own expiring `final_offer` thread is still dropped, and the thread then times out ("your last offer is repeated") | read |
| **B5** (Low): floor with a thousands separator | **Fixed** ("1,250.00" → 1250.0). German "1.250,00" is not handled | read and probed |
| Merged #1b: `_mirror_alternative` sends to `final_offer` threads with `reprice=False` | **Open by decision** (hosted A/B recorded in `ef89200` / `AB_MIRROR.md`) | `routed.py` `_mirror_alternative` |
| Merged #2: catch-alls turn code bugs into moves | **Partly fixed.** The text filter now keeps the unfiltered body, and fallbacks log a traceback. But `validate`'s own `except (KeyError, TypeError, …)` (`routed.py:244`) still returns `None`. That becomes `ValueError("model action rejected")`, which is logged **without** a traceback (`:1171` excludes `ValueError`), so a code bug in a helper called from `validate` still looks like a model rejection | read |
| Merged #7: round-1 walk-away with an alternative | **Fixed** (`routed.py:121`). The fallback also replaces a walk-away with `_safe_hold` | read |
| Merged #8: floors misread | **Fixed** (see B5 above) | read |
| Merged #3, #9-#12 | Out of scope (logic, evidence, architecture). Not re-checked | - |
