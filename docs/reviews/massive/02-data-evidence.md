# Review 02 — The evidence/data layer (data.py, docs.py, data/)

Scope: `src/tacto_negotiator/data.py`, `src/tacto_negotiator/docs.py`, the dataset in `data/`
(facts.json, world.sqlite, CSVs, correspondence, contracts, datapackage.json, KNOWN_ISSUES.md,
SCORECARD.md), and every path that turns that data into negotiation leverage (the dossier consumed
by `agents/llm.py:prepare/render` and `agents/routed.py`). Working tree reviewed as-is (only
README.md modified; the code is at commit `ef72ddb`).

All numeric claims below were verified offline against the package (SQLite queries, `DataPackage`
calls, parsing probes). `uv run pytest tests/test_dossier.py tests/test_script_coverage.py -q` →
27 passed.

---

## Executive summary

The evidence layer already does the hard parts well: it picks citable index falls and flags rises,
pulls the right correspondence, merges should-cost and quote table rows, and reads quote PDFs into
structured terms that feed the cost model correctly (checked against the hosted sample
`tests/fixtures/quote.pdf` and the hosted-feedback audit with zero mismatches). But three things
keep the score lower than it could be:

1. **A whole class of leverage data never reaches the agent.** `negotiation_history` (300 rows:
   every past renewal's *asked* vs *settled* change, plus notes like "accepted below full cost to
   keep the volume") and `price_conditions` (339 rows: the part's year-by-year price trajectory)
   have no citable IDs, are not in `facts.json`, and are not selected by `negotiation_facts`. In
   P01 the supplier announces +9.5% on a part whose price has *fallen three years running*
   (136.00 → 128.42 → 128.27 → 126.53) — the strongest available counter-argument is invisible.
2. **A parsing fault in one quote-PDF line silently degrades every LLM decision in the thread.**
   `docs.quote_terms` raises on plausible format variants (verified: `deadband 3%` without the
   space, `421,06`, `30 days`); `LLMAgent.decide` catches the exception and falls back to the
   naive agent for that round — and every later round while that quote stands. The hosted arena
   has already produced one variant of this bug class (commit `f4a0dbf`: a `none` effective date
   "failed validation and voided every offer").
3. **`DataPackage._ids` mis-keys lookups.** Last-writer-wins across tables means
   `lookup("part:310010")` returns an arbitrary *sourcing* row (for a supplier that is not even in
   the case, `quota_pct 0`), the parts master (`standard_price`, `material_group`, net weight) is
   shadowed for 55 of 60 parts, and `supplier_intel` (turnover, equity ratio, credit index) is
   shadowed for all 46 suppliers.

Additionally the dossier spends ~27% of its rows on forecast snapshots that can never verify more
than once (hosted rule), and shows exactly one scorecard quarter when `quality_record`/
`delivery_record` are verified hosted claim types (observed in the hosted feedback) and 4-5 of the
10 practice suppliers have material amber/red trends worth citing.

---

## Score mechanics in this area

Score = (C_open − C_deal) / (C_open − C_best). The evidence layer moves the score through four
channels only:

| Channel | Mechanism | Where |
|---|---|---|
| Verified claims | Up to 3 checks per message; a verified claim "allows a concession" (hosted docs) and, in the calibrated replica, shaves ~0.5% off the first counter once (`hosted_like.py:161-162`). Wasted/duplicate slots are pure loss. | dossier → LLM body / scripted bodies |
| Argument quality | The hosted supplier is an LLM with its own cost floor: context that makes its increase look unjustified (its own price history, its own past settlements) moves its counters even without a claim verdict. | dossier context rows |
| Correct cost inputs | `standard_terms` from the latest quote PDF feed `closing_terms`, `ask_limit`, `acceptable_offer`, `cheapest_price`. A mis-read standard term mis-prices every accept/counter decision. | `docs.py` → `cost.py`/`play.py` |
| Anchors | `ANCHOR_BY_GOAL` (play.py:289) is a per-goal constant off the standing quote; it currently uses **no** per-supplier data at all. | code, informed by data |

What the arena checks (from `docs/DATA_GUIDE.md:58`, `data/README.md` "IDs" section, and the 38
observed hosted claim verdicts in `docs/reviews/hosted-evaluation-audit.json`): index pairs
(`index_change`), eligible quotes/offers (`rival_quote`), forecasts (`volume_change`, once per
case), should-cost, supplier news, **scorecards (delivery/quality)**, **notice pairs (letter +
contract)**, and 2–8 same-part PO rows (price arguments). `evaluation.py:81-95` reproduces the
scorecard and notice paths with zero mismatches against hosted verdicts.

---

## Findings (ranked)

### F1 — One malformed quote-PDF line voids every LLM decision in the thread (robustness, high)

`docs.py:46-64` (`_value`) raises `ValueError` on: an index clause not matching `CLAUSE`
(`docs.py:25` requires exactly `deadband <n> %` — with the space), a decimal comma
(`float("421,06")`), or any trailing unit (`float("30 days")`). `quote_terms` (`docs.py:67-76`)
propagates it; `standard_terms` (`docs.py:79-85`) is called from `LLMAgent.render`
(`agents/llm.py:181`), which runs inside `decide`'s try/except (`agents/llm.py:133-143`) — the
exception is swallowed and the round (and every subsequent round, because
`standard_terms` reads the *latest* quote) falls back to the naive agent. The naive baseline is
the thing the whole routed/LLM stack exists to beat; EVALUATION.md documents rounds and whole
cases lost to weaker failure modes of the same funnel (P05 unread quote, P01 accepted opening
after a failed reply).

Verified by probe (this repo, `uv run python`):

```
no space before %  -> ValueError: unrecognized index clause in quote
comma decimal      -> ValueError: could not convert string to float: '421,06'
trailing word      -> ValueError: could not convert string to float: '30 days'
```

KNOWN_ISSUES #8 ("past quotes miss fields where the supplier left them out") and the hosted `none`
incident (commit `f4a0dbf`, fixed only for `effective_date`) show the arena does emit such text.
`LLMAgent.render` is also on the routed agent's path (`agents/routed.py:679`), so this affects the
competition configuration, not just the standalone LLM agent.

Impact math: one case degraded from ~0.6 to ~0.1 costs ~0.05 on a 10-case mean. Probability per
case is low (formats have been stable in ~40 hosted replays) but the failure is persistent and
silent.

### F2 — Settlement history and price trajectory never reach the agent (unused leverage, high)

Two tables exist precisely for negotiation anchoring and are unused anywhere in `src/`:

- `negotiation_history` (300 rows): per supplier+part `asked_pct`/`settled_pct` and free-text
  notes. Across 225 renewals, **settled ≈ asked − 2.4 pp** (mean −2.47, range −4.0 … −1.0). For
  P01's exact pair (`sup:70110`/`part:310010`): asked −2.2 → settled −5.6; asked +1.9 → settled
  −0.1; asked +2.1 → settled −1.4, with the note **"accepted below full cost to keep the volume"**
  on all three. Coverage: 8-9 of the 10 hosted practice cases have direct supplier+part history.
- `price_conditions` (339 rows): the price path per pair. `sup:70110`/`part:310010`:
  136.00 (2023) → 128.42 → 128.27 → 126.53 (2026) — three consecutive annual decreases, directly
  contradicting the +9.5% "sustained cost increases" letter (`data/correspondence/20260911-70110-P01.md`).
  `sup:70740`/`part:370010` (P18): 6.45 → 5.26 by 2026-01 before a +11% index adjustment to 5.84.

Why they are invisible: neither table has citable IDs (`event_id` is `EV-...`, no colon; facts.json
prefixes are po/idx/qn/fc/sc/ctr/doc/quote/rfq/news/cost only), `_ids` (`data.py:65-81`) only
indexes key-column values containing `:`, and `negotiation_facts` (`data.py:112-118`) selects only
`doc:/news:/cost:/quote:/fc:` prefixes. `mcp_server.py` can query the SQLite, but the competition
agent never does — it sees only `prepare()` + the dossier.

Note the distinction the current code misses: the commit `05d9e8c` decision to skip PO history *as
claims* is well supported (DATA_GUIDE says PO rows are checkable, but hosted observation says they
move no supplier, and a PO claim still spends one of 3 slots). But *as context* — anchoring the
argument "your own price has fallen three years running; last December you settled at −1.4%" — the
history costs nothing (no ID to cite, no claim slot, no contradiction risk) and is exactly the
kind of fact a supplier-side LLM reacts to.

### F3 — Scorecard trend invisible; quality/delivery claim slots unfilled (medium-high)

Every hosted case's `refs` carry exactly one scorecard (`sc:<sup>@2026Q3`), and
`negotiation_facts` adds none. The checker supports `quality_record`/`delivery_record` with the
mean over cited rows (`evaluation.py:81-86`), and the hosted feedback contains verified
`sc:70140@2026Q3` claims (4 observations) — the channel is real. What the agent cannot see:

- `sup:70110` (P01): green 92-94 through 2025, **amber 84.0** now; ppm 522 → 712 over 2026, **1
  open 8D overdue** — a checkable counterweight to a +9.5% demand (and leverage for a
  ppm_commitment/chargeback instead of price).
- `sup:70240` (P05 bidder): OTD **83.3 → 62.5** collapsed across 2026 — either push its price or
  steer volume to the green bidder.
- `sup:70140` (P11, the quality case): red 69.9, ppm 2,376, OTD 63.5 vs confirmed 73.1.

Without earlier quarters in the dossier the model cannot cite a trend (a two-ref mean claim needs
both IDs) and cannot write "your ppm rose 36% this year" with checkable IDs.

### F4 — `_ids` last-writer-wins mis-keys `part:` and loses `supplier_intel` (medium, correctness)

`data.py:69-76` iterates tables in name order and overwrites `index[value]` per row. Consequences,
verified against the real package:

- `lookup("part:310010")` → a **sourcing** row: `{"supplier_id": "sup:70140", "quota_pct": 0,
  "status": "qualified", ...}` — an arbitrary supplier (not P01's), shown to the LLM as *the* part
  fact in `prepare()` (`agents/llm.py:154-156`). Only 5 of 60 parts rows survive as last writer;
  the parts master (standard_price 128.27 for part:310010 vs current 126.53, material_group
  `cast_iron`, net_weight_kg, planned_lead_time_days, abc_class) is lost.
- `lookup("sup:70110")` → the suppliers master (this one is fine), but `supplier_intel` (46 rows:
  turnover band, equity_ratio_pct, credit_index, vda63_grade) is shadowed for every supplier and
  unreachable through any code path. `spend_cube` (our spend = the supplier's dependency on us) and
  `negotiation_history` are not indexed at all (no `:` in their key columns).

The parts row matters beyond context: `material_group` is the human mapping from part to cost
driver, and `standard_price` is the ERP benchmark the supplier's own letters argue against.

### F5 — Dossier wastes ~27% of rows on duplicate-forever forecasts (medium-low, budget)

Hosted rule (EVALUATION.md, `agents/llm.py:172-173`, `agents/routed.py:414-434`): every `fc:`
claim after the first in a case is `duplicate`. P01's dossier carries **six** `fc:` snapshots of
the same part (`fc:310010@2025-06` … `@2026-09`) — 6 of 22 rows, ~1,100 of ~6,000 characters —
of which at most one can ever verify. Same pattern in P02/P08. The budget this wastes is exactly
the budget F2/F3 need.

### F6 — Market news excluded by the supplier filter (low)

`data.py:114` requires `fact.get("supplier") in suppliers`; the 7 `supplier_id IS NULL` market
items are never selected. One is directly on-point context for the foundry cases: "Industrial
power prices −43% on 2022" (`news:20260912-30`) vs Brandlhuber's "sustained increases in energy…
costs" letter. They are **not** citable (a news claim must be about the supplier,
`evaluation.py:94-95`) — if surfaced they must be labelled context-only or they will burn claim
slots on `irrelevant`.

### F7 — Minor data-layer nits (low)

- `supplier_estimates` (`data.py:172-177`) takes `costs[0]` — fine today (one should-cost row per
  supplier+part in the data) but arbitrary if a future package has several; the relationship-term
  floor it feeds (`cheapest_price`) is score-critical in `stabilise`/`secure_supply` cases.
- `document()` for a release fact without `path`/`body`/`root` returns a JSON dump
  (`data.py:100-101`) — the Sunday-release overlay is integrated for facts/CSVs, but a release
  document row lacking those keys would degrade to JSON in the dossier's `text` field.
  Unverifiable until the release exists (open question).
- The `fc:`/`cost:` dossier sort key is the ref string, not a date field (facts carry no date);
  correct today because the ref embeds the period, but fragile.

---

## Recommendations (prioritized)

### R1. Make `quote_terms` fault-tolerant (F1) — do first, pure protection

Per-line `try/except` in `quote_terms` (`docs.py:67-76`): on a parse failure, log and skip the
line instead of raising; normalize `\u2013`/comma decimals (`text.replace(",", ".")` is wrong for
thousands — strip `.`-grouping only when a comma decimal is present, or use a tolerant regex);
relax `CLAUSE` to `deadband (\d+(?:[.,]\d+)?)\s*%`. Add the three probe strings above as tests in
`tests/test_dossier.py`. Guard: a skipped *standard* term must surface as a missing field
(`play.known_offer` already handles missing quote info) rather than a silent default.

Expected impact: zero when quotes are clean; +0.05–0.10 on the mean in the tail case where the
arena emits one variant (one case rescued from naive-fallback pricing). Cost: ~30 minutes.

### R2. Add a bounded CONTEXT block to `negotiation_facts` from `negotiation_history` + `price_conditions` (F2)

In `data.py:negotiation_facts`, when `self.db` exists: for each (case supplier, case part), select
the last 4 `negotiation_history` rows and last 4 `price_conditions` rows (date, price, basis,
asked/settled, note), keyed `f"history:{sid}:{part}"` / `f"prices:{sid}:{part}"` — deliberately
non-citable prefixes (`ID_RE` will not match them, so the model physically cannot waste a claim on
them). Include the one-line aggregate "settled − asked: mean −2.4 pp (n=…)" per supplier. Extend
`tests/test_dossier.py` with a package fixture containing these tables.

Expected impact: this is the strongest unused argument set for the four increase/surcharge cases
(P01, P02, P08, P18 — exactly where the score headroom sits, since a beaten increase is captured
saving). Honest range: +0.01–0.05 per affected case via better supplier counters and firmer first
messages; 8-9 of 10 cases get data. Cost: ~1 hour + a replica sweep to confirm no regression
(`hosted_like` suppliers won't react to prose, so validate on hosted practice, not locally).

### R3. Add the 4-quarter scorecard trend per case supplier (F3)

In the same CONTEXT block (or as real `sc:` dossier rows — they are citable and checkable): the
last four quarters' `colour/score/otd_pct/ppm/open_8d_overdue` per case supplier. If added as
`sc:` rows, the model can build the verified `quality_record` claim (mean ppm across two refs) the
host already verifies. Keep the prompt's existing "sc is checkable" line.

Expected impact: a fresh verified-claim slot for the 4-5 amber/red suppliers (P01, P05×2, P11) —
in the replica a first verified claim is worth ~0.5% of the opening price once, on the host at
least a concession enabler; and the trend is the natural language lever ("your ppm is up 36% this
year — we will not fund a 9.5% increase on top of it"). Range: +0.01–0.03 per affected case.
Cost: ~30 minutes.

### R4. Fix `_ids` keying and surface `supplier_intel` (F4)

Two options: (a) prefer the table whose name matches the ID prefix (`part:` → `parts`,
`quote:` → `quotes`, `sc:` → `scorecards`, …) instead of last-writer-wins; or (b) merge, letting
the first writer keep priority. Add `supplier_intel` rows per case supplier to the CONTEXT block
(keyed `intel:{sid}`, non-citable) with turnover band, equity ratio, credit index and 12-month
delta, vda63 grade.

Expected impact: small direct (+0–0.02) but it corrects a factually wrong row the model currently
reads as "the part" (an unrelated supplier's 0% quota), and gives `standard_price` as a sanity
benchmark. Cost: ~1 hour including tests.

### R5. Trim `fc:` dossier rows to the latest per part (F5)

Change `("fc:", 6)` to the newest snapshot per part (plus, optionally, the largest-change one).
Frees roughly a quarter of the dossier for R2–R4 at zero information loss given the duplicate
rule. Expected impact: latent (prompt budget for the above; marginally faster first decisions).

### R6. (Optional) Surface market news as context-only (F6)

Add the 7 market items whose publication date falls in the last ~8 weeks, labelled "context only,
not citable". Skip if R2 already fills the budget.

### What *not* to change

- Keep PO history out of claims (the `05d9e8c` observation stands); R2's trajectory context
  supersedes it with cleaner data.
- Keep the rising-index suppression (`data.py:144-152`) — it encodes a real hosted lesson.
- Leave `ANCHOR_BY_GOAL` alone in this layer; if R2's history proves out on hosted practice, a
  follow-up could bias the anchor by the supplier's own past settled depth (mean −2.4 pp), but that
  is play-layer tuning, not data-layer repair.

---

## Risks & trade-offs

- **Prompt size vs the 25–40 s decision budget.** R2+R3+R4 add ~15–25 rows. Mitigate by R5
  (reclaims ~6 rows) and hard caps; the P01 dossier today is only ~6 k characters, and the model
  runs at low/high effort, so the headroom is real but should be measured with
  `tests/test_script_coverage.py`-style checks on render size.
- **Context the model might misuse.** History/price rows have no citable IDs by construction, and
  `LLMAgent.guard`'s `known()` (`agents/llm.py:257-263`) already strips unknown IDs, so the
  failure mode is verbosity, not contradicted claims. Market news (R6) *does* have citable IDs and
  must be labelled; if in doubt, skip R6.
- **Over-anchoring from history.** The settlement history is population-level (n=225, tight range)
  but individual suppliers vary (70250: −1.3 pp mean vs 70710: −3.1). Keep it as argument context
  for the LLM, not as an automatic anchor change, until a hosted A/B shows otherwise.
- **R1's tolerant parsing could hide a genuinely broken quote.** A skipped standard term must be
  treated as *missing* (blocking acceptance on defaults), which `known_offer` already models —
  verify with a test that a quote whose only payment line fails to parse does not silently default
  to the case plan.
- **Local validation is blind to prose effects.** `hosted_like`'s supplier reacts to verified
  claims, not to context arguments, so R2/R3's real test is hosted practice quota (200 sessions /
  24 h), not the replica sweep.

---

## Open questions

1. Does the Sunday release (`release-sunday`, `data.py:26-29`) add rows to `negotiation_history` /
   `price_conditions` / `scorecards`, or only news/letters/memo? R2/R3 should read through
   `self.release` CSVs too if so (`_ids` already indexes release `data/*.csv` by their `id` column).
2. Are release documents guaranteed `path`/`root` fields? If not, `document()` degrades to a JSON
   dump in the dossier text (F7).
3. On the hosted arena, does a *trend* scorecard claim (two `sc:` refs, mean ppm) verify the way
   `evaluation.py:81-86` models it? The 4 observed quality claims were single-ref; the checker's
   mean logic was validated on other types. One hosted practice case can answer this.
4. Does the host's `rival_quote` accept the incumbent's own current quote (`quote:7300008` was
   interpreted as `rival_quote` in P08 feedback) — i.e., is "your own standing quote from last
   week at 5.45" a valid claim when the supplier announces a new basis? If yes, that is another
   verified-claim slot the dossier could make explicit (it already merges quote table rows).
5. `sup:71999` (KNOWN_ISSUES #5, the renamed duplicate of `sup:70210`) has no scorecard/PO rows,
   so no aliasing hazard exists today — worth re-checking if the release adds any.
