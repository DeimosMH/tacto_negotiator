# Bloat review, round 3

**Commit:** `737c1d3` (HEAD; the only commit after `d842b8e` is README text). Extra depth on the 29 commits `5dea00a..HEAD`.

**Method:**
- Dead code: `vulture 2.16` (throwaway venv in `/tmp`) over `src`, `tests`, `tools` and `docs/*.py`, plus an AST/token scan of every `src` definition against all other Python files (`repro_bloat.py` X0), and `ruff --select ARG,SIM`.
- Duplication: pylint `symilar` (min 5 lines) over `src`, `tools` and `docs`, and over `tests`. Hand-traced the semantic duplicates in `routed.py`, `play.py`, `competition.py`, `deals.py`, `llm.py`, `completion.py` and `compete.py`.
- Coverage: `coverage 7.17` on the offline suite, 349 passed, 90 % line coverage.
- Artifacts: `git ls-files` sizes and reference counts.
- Validation: a `/tmp` copy of the repo with X1, X2 and X3 applied, and the X4 prompt rendering applied (`TACTO_DEALS_DIR=off`, run from an empty directory): **349 passed**. The rendered `SYSTEM` prompt is byte-identical (6748 chars).
- Evidence: `repro_bloat.py` prints the evidence for every finding. It is offline and reads no keys.

**Headline:**
- Classic dead code is essentially gone. Vulture and the AST scan find no unreferenced function, class or constant outside framework hooks; round-1 cleanup held.
- The bloat today is semantic: the same rule written several times with different numbers (A3/A8 grew), two agents, two runners, two record formats, docs that restate config the code does not default to, and 854 MB of stale agent worktrees inside the repo.

> Note for whoever runs the tests: `/tmp/h2.py` (a scratch script from 24 Oct) shadows the `h2` package that httpx imports. Running the documented `cd /tmp && ... pytest` there fails 1 test (`test_sim.py::test_both_agents_play_unchanged`, FileNotFoundError from `/tmp/h2.py`). Run from an empty directory, e.g. `/tmp/blrun`.

## Findings

### X1 [safe-now] `deals.backfill` builds a `titles` dict nobody reads
- **Where:** `src/tacto_negotiator/deals.py:517` (`titles = {}`) and `:538` (`titles[match["case_id"]] = ...`).
- **Evidence:** the AST shows a store and an item store, and no load (repro X1). Removed in the `/tmp` copy, all tests pass.
- **Removable:** 2 lines.

### X2 [safe-now] Parameters accepted and ignored
- **Where:**
  - `agents/completion.py:177`: `AnthropicCompletionClient.__init__(..., base_url=None, ...)` is never read. No caller passes it (0 matches in `src` and `tests`).
  - `agents/routed.py:455`: `_new_fact(self, case, threads, sid, body)` never uses `case`. It has one caller, `routed.py:217`.
- **Others ruff `ARG` reports are interface conformance and should stay:** `RemoteTable.missed(error)`, the FastAPI route args, `compete.unavailable`, `CloseAgent.decide_timed` and `facts._forecast(sid)` (a provider-table signature).
- **Removable:** 0 lines (signature only). Applied in the `/tmp` copy: 349 passed.

### X3 [after-final] `_limit_counter` re-implements `_limit_sheet`
- **Where:** `agents/routed.py:688-703` against `:459-467`. Four statements are identical: `known_offer`, the resolved terms, the sheet over the negotiables and `_price_within`.
- **Suggested change:**
  ```python
  limit = ask_limit(case, threads[sid], sid)
  sheet = self._limit_sheet(case, threads[sid], sid, limit) if math.isfinite(limit) else None
  return sheet and self.validate(case, threads, {..., "term_sheets": [sheet]})
  ```
- **Why the `isfinite` guard must stay:** with `limit=inf`, `price_within` floors the price to cents and can return a sheet 0.01 below the quote.
- **Why it is not provably identical:** `_limit_sheet` also returns None when the standing terms lack `unit_price` but the quote defaults carry one. That case is rare but reachable, so this is not provably identical.
- **Validation:** 349 tests pass in the `/tmp` copy.
- **Removable:** about 8 lines.

### X4 [safe-now] Prompt text duplicates code constants
- **Where:** `agents/llm.py` `SYSTEM`:
  - `:69` "rose >= 3 % in twelve months" mirrors `levers.PATH_MIN_RISE = 0.03`.
  - `:75` repeats `levers.FLOOR_QUESTION` verbatim.
  - `:62` "At most three claims" mirrors `facts.ROUND_ONE_CLAIMS = 3`. It cannot be imported from `llm.py` because `facts` imports `llm.ID_RE`, which would be a cycle.
  - `:116` "120 words" is enforced nowhere.
- **The decision-tree prose in `deals.py` copies numbers too:**
  - `TREE` (`deals.py:60-67`): "within 0.2 %" for `* 1.002` (`routed.py:951`), and "20 % of it (40 % routine)" for `GAP_STEP`/`GAP_STEP_BY_GOAL`.
  - "The default model call (Claude Opus, high effort)" (`deals.py:76`), while the code default is `LLM_EFFORT=low` (X13).
- **Evidence:** repro X4. An f-string `SYSTEM` that renders `FLOOR_QUESTION` and `PATH_MIN_RISE` (with `{`/`}` of the JSON examples doubled) is **byte-identical** to the current prompt; 349 tests pass.
- **Suggested change:** render those two now. Move `ROUND_ONE_CLAIMS` to `levers.py` (or a `policy.py`) after the final. Render the `TREE` numbers from the constants.
- **Removable:** 0 lines. This removes copies, not code. This is round-2 A8's "[now]" item, still open.

### X5 [safe-now, low value] Unnamed tolerances: A3's "[now]" item is still open and has grown
- **Where (`routed.py`):**
  - `1.005` ×4 (`:184,191,198,673`)
  - `1.003` ×4 (`:224,273,396,984`)
  - `1.002` (`:951`)
  - `0.999` (`:630`)
  - `0.998` ×2 (`:650,1036`)
  - `0.997` (`:878`)
  - `0.9 *` (`:236`)
  - `* 1.5` (`:215`)
- There are now 49 float literals in `routed.py`; round 2 counted 41. `_opening_limit` (0.999) and `_rival_limit` (0.998) were added since then, each with its own margin.
- **Suggested change:** name them (`MEETS_ASK = 1.005`, `EQUAL_COST = 1.003`, `SMALL_GAP = 1.002`, `BELOW_RIVAL = 0.998`, `BELOW_OPENING = 0.999`, `CREDIBLE_SHARE = 0.9`, `MIN_MOVE = 0.5/1.5`) with values unchanged.
- **Classification:** mechanical, but it touches 15 lines of the decision path on final day. Only do it if idle; otherwise after the final.
- **Removable:** 0 lines.

### X6 [after-final] The anchored opening price is computed in five places (A3 "opening anchor", still open)
- **Where:**
  - `play.py:329` (`opening_price_floor`), `:344` (`opening_plausible_floor`, new since round 2) and `:359` (`opening_price_cap`)
  - `routed.py:956-958`: recomputes `max(round(price*(1-anchor)), opening_price_cap(...))`. `opening_price_cap` already returns `max(anchored, health floor)`.
  - `routed.py:1086-1087` (`simple_opening_counter`)
- The 0.15 default for an unknown goal is repeated 4 times.
- **Suggested change:** one `anchored_price(case, resolved)` in `play.py`.
- **Removable:** about 8 lines.

### X7 [after-final] Repeated idioms that want a helper
- **Resolved terms:** `{**std, **case.get("fixed_terms", {}), **terms}` appears 14 times: routed 7, play 5, levers 1, llm 1.
- **"Buyer messages with sheets":** the filter `e.get("from") == "buyer" and e.get("term_sheets")` appears 8 times.
- **"Our last ask" C** is computed 4 times:
  - with `total_cost` at `play.py:385`, `routed.py:212` and `:648`
  - with `decision_cost` (including the supplier-health term) at `routed.py:672` (`_premature`)
- *Cross-over (logic):* two definitions of "our last ask" compared against two different tolerances.
- **Removable:** about 20 lines.

### X8 [after-final] Legacy `LLMAgent` is a second agent with a second guard (round-2 A5, still open)
- **Where:** `agents/llm.py`: `LLMAgent.decide` (14 lines), `guard` (42), `llm_from_env` (34; a second Anthropic SDK client and a chat-completions client), plus the `NaiveAgent` fallback wiring. `cli.py:30-47,73,78` expose `--agent llm`.
- `RoutedAgent` builds `LLMAgent(None, package)` only for `prepare`, `render`, `fact`, `brief`, `case_id` and `package` (repro X8), never `decide` or `guard`.
- **Suggested change:** move `SYSTEM`, `prepare`, `render` and `fact` to `prompt.py`. Delete `decide`, `guard`, `llm_from_env` and the `llm` CLI choice, keeping `NaiveAgent` as the baseline. Tests that use `LLMAgent` (15 references in 5 files) must be adapted.
- **Removable:** about 95 src lines.

### X9 [after-final] The OpenAI model experiment path is dead weight now that the final runs on Anthropic
- **Where:**
  - `compete.py:23-24,62-71,117-127`: `MODELS` sol/astra/luna, `hybrid`, `ModelAgent`, preflight and key file
  - `benchmark.py` (200 lines: `read_llm_key`, `preflight_models`, `BudgetLedger`, `BudgetedCompletion`, GPT price table)
  - `tests/test_benchmark.py` (264 lines)
- The OpenAI credits are exhausted, and every documented final and bench command uses `LLM_PROVIDER=anthropic`. `CompletionClient` (Responses API) must stay as long as `AnthropicCompletionClient` subclasses it, or the two get split.
- *Cross-over (bugs):* `routed_completions_from_env` still defaults to `LLM_PROVIDER=openai` / `gpt-6.1-sol` (`completion.py:229,240`). A shell without `LLM_PROVIDER` silently routes to the exhausted provider (see X13).
- **Removable:** about 240 src lines and about 260 test lines. Keep the historical reports in `docs/results`.

### X10 [after-final] `competition._match` is a hand copy of `play._play` (round-2 A2, still open) and has drifted
- **Where:** `play.py:472-698` (227 lines) against `competition.py:377-527` (150 lines).
- **Features in `play` missing from the replica** (repro X10):
  - the 422 stale-accept re-read (`d842b8e`, today)
  - the B4 rule that judges an accept on its own thread's clock
  - `reread_skipped_documents`
- The bench budget default also differs: `decision_seconds=25` in `competition.py:530` and `compete.py:46`, against 40 s in `RoutedAgent` and live play.
- *Cross-over (bugs):* the replica cannot exercise the newest runner fixes.
- **Suggested change:** drive `play()` against a `CompetitionSim`-backed table with a virtual clock.
- **Removable:** about 100 lines.

### X11 [after-final] `validate` keeps growing as validator, policy and writer at once (round-2 A4, still open)
- **Size:** `routed.py:104-245` is 142 lines (116 at `5dea00a`), with 97 branch nodes. It calls 15 distinct `self.*` helpers, and it is called 10 times inside `routed.py`.
- **New since round 2:** `_rival_limit`, `_opening_limit`, plausible-floor lifting, `_held_package`/`_imposed_clause`, and a second `_free_terms` pass. The ordered rewrite pipeline went from about 10 steps to about 15.
- **Suggested change:** the `check`/`shape`/`write` split from A4. No line savings are claimed; the gain is reviewability.

### X12 [after-final] The deal journal stores every record twice and writes into the repo during live play
- **Where:** `deals.save_record` (`deals.py:323-329`) writes `<id>.json` (indent 1) and `<id>.js` (the same object wrapped in `window.tactoDeal(...)`). `write_index` reads only `*.json`; the UI (`docs/deals-ui.js:40,361`) reads only `*.js`.
- **Tracked today:** 297 `.json` files (4.54 MB) and 297 `.js` files (3.98 MB), all `hosted (backfilled)`.
- **Default output:** `DEFAULT_DIR` is the repo's `docs/deals`, so today's runs already left 140 untracked files and modified `docs/data.js`/`docs/deals.js`.
- **Suggested change:** keep one format. Either index from the `.js` by stripping the wrapper, or have the UI fetch the `.json` behind a server. Decide whether the journal of live runs belongs in git at all, or in a gitignored folder synced by `02-vera-iac/sync-docs.sh`.
- **Removable:** 297 files and 4.5 MB from the tree; about 3 code lines.

### X13 [safe-now for the docs; after-final for the code defaults] Final config is restated in docs and IaC; code defaults disagree (round-2 A8)
- **Docs and IaC:** `LLM_EFFORT=high` is stated in README (×5), FINAL_RUNBOOK (×2), ROUTING (×1) and `02-vera-iac/variables.tf` (×1).
- **Code defaults:** `LLM_PROVIDER=openai`, `LLM_EFFORT=low` (`completion.py:229,250`).
- **`docs/ROUTING.md` still has the three stale statements round 2 asked to fix:** the "90%" anchors (`:53,69`), the "midpoint" concession (`:69`) and "triggers deterministic closure" (`:119`).
- **Suggested change:**
  - **[safe-now, docs only]:** fix those ROUTING lines.
  - **[after-final]:** make the code defaults equal the final (anthropic, `claude-opus-5-5`, high, fast) so the docs stop repeating env lines.
- **Removable:** about 10 doc lines.

### X14 [safe-now: outside the source tree] 854 MB of stale agent worktrees inside the repo
- **Size:** `.claude/worktrees/` holds 18 directories (854 MB), with 22 `git worktree` entries and 19 `worktree-agent-*` branches.
- **Not ignored:** `.claude/` is in neither `.gitignore` nor git status filters (`?? .claude/`).
- **Side effect on lint:** running `ruff check` from the repo root lints the worktree copies. Eleven of its 12 errors come from them; the 12th is `docs/reviews/repro_flow.py:74` I001.
- **Suggested change:** add `.claude/` to `.gitignore` now. Then run `git worktree remove` / `git worktree prune` and `git branch -D worktree-agent-*`, but only once no review agent is running (one worktree is `locked`).

### X15 [after-final] Repo-root path recomputed per module; runtime code reads `tests/fixtures`
- **Repeated root path:** `Path(__file__).resolve().parents[2]` appears 6 times: `compete.py:134`, `deals.py:25,128` and `hosted_like.py:33,34,36`.
- **Runtime reads test fixtures:** `hosted_like` (an importable `src` module) loads `tests/fixtures/practice_*.json` at runtime. The wheel does not ship them.
- **Suggested change:** one `paths.py`, and move the two fixtures used by the bench to `data/bench/`.
- **Removable:** about 4 lines.

### X16 [after-final] `Dockerfile` and `Dockerfile.azure` share 6 of 12 build lines
They differ only in the `--extra anthropic` option, the editable install and the copied `data`/`docs/results`. One Dockerfile with `ARG EXTRAS` and a target stage would remove about 6 lines. Low value.

### X17 [after-final] Large committed reports
- **Size:** `docs/results` holds 69 files (13.6 MB). Three of them make up 9.8 MB: `models.json` 4.16 MB, `holdout.json` 2.97 MB and `offline.json` 2.67 MB.
- **Usage:** they are referenced by docs, and `docs/build.py:174` reduces each one to `report_summary` plus a sha256.
- **Suggested change:** store the per-decision traces compressed or outside git, and keep the summaries.
- **Not findings:**
  - `data/` and `materials/starter_kit/data/` are byte-identical (except `release-sunday/`), but git stores the blobs once.
  - The starter-kit archive and slides are deliberate (`materials/README.md`).

## Summary

| # | Class | Area | Lines removable |
|---|---|---|---|
| X1 | safe-now | `deals.backfill` dead `titles` | 2 |
| X2 | safe-now | unused params `base_url`, `_new_fact(case)` | 0 (signatures) |
| X3 | after-final | `_limit_counter` duplicates `_limit_sheet` | 8 |
| X4 | safe-now | prompt and decision-tree copies of constants | 0 (copies removed) |
| X5 | safe-now (low value) | 15 unnamed tolerances | 0 |
| X6 | after-final | anchored price in 5 places | 8 |
| X7 | after-final | resolved terms ×14, last ask ×4, sent filter ×8 | 20 |
| X8 | after-final | legacy `LLMAgent` decide/guard and `llm_from_env` | 95 |
| X9 | after-final | OpenAI strategies and `benchmark.py` | 240 src + 260 tests |
| X10 | after-final | replica runner copy | 100 |
| X11 | after-final | `validate` triple role | 0 (structure) |
| X12 | after-final | duplicate deal record format; journal in repo | 3 code; 297 files / 4.5 MB |
| X13 | safe-now docs / after-final code | config copies, stale ROUTING | 10 doc lines |
| X14 | safe-now (outside src) | stale worktrees, `.claude/` not ignored | 854 MB working tree |
| X15 | after-final | root path ×6, fixtures read at runtime | 4 |
| X16 | after-final | Dockerfile duplication | 6 |
| X17 | after-final | 9.8 MB of trace reports | (artifacts) |

**Totals:**
- **safe-now:** 2 source lines, 2 parameters and 2 prompt copies, with no behaviour change (validated, 349 passed). Also about 10 stale doc lines, a `.gitignore` line and 854 MB of worktrees.
- **after-final:** about 480 source lines and about 260 test lines, plus 297 duplicate record files (4.5 MB) and about 9.8 MB of reports.

**Before 13:30, do only X1, X2, X4 (`FLOOR_QUESTION` and `PATH_MIN_RISE` rendering), the ROUTING.md doc fix and the `.claude/` ignore.** Everything else touches the decision path or the bench.

## Earlier bloat findings status

**Round 1 (`docs/reviews/BLOAT.md`):**

| Finding | Status at `737c1d3` |
|---|---|
| #1 three acceptance checks disagree on missing defaults | **Fixed.** On the S01 probe, the legacy guard no longer accepts (it falls back to a counter), and the default and routed closers both walk away. The separate legacy guard still exists (X8). |
| #2 25 catalog entries never displayed | **Fixed.** `SYMBOLS` has 79 entries, 0 never read, and the build output is unchanged. |
| #3 full `validate` per package candidate | **Fixed.** `_packages` filters with `must_haves_ok` and no longer calls `validate`. |

**Round 2 (`docs/reviews/round2/ARCHITECTURE.md`):**

| Finding | Status at `737c1d3` |
|---|---|
| A2 replica copies the runner | **Open, wider.** The replica has drifted from three play-only fixes (X10). |
| A3 no single acceptance, concession or anchor rule | **Open, wider.** The tolerances were not named: 49 float literals, against 41 before (X5). The anchor is computed in 5 places, against 3 before (X6). |
| A4 `validate` is validator, policy and writer | **Open, grown.** 142 lines, 15 helpers (X11). |
| A5 two LLM agents, two guards | **Open** (X8). The guard no longer accepts unchecked defaults. |
| A8 config spread across code, prompt and docs | **Open.** The prompt literals are still copies (X4), ROUTING.md is still stale, and the code defaults differ from the final config (X13). |

`repro_bloat.py` prints all of the above: 129 lines, exit 0.
