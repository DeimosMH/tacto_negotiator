What this change does: The current project negotiates procurement cases through scripts, optional model calls and a deadline-aware runner, with a local simulator, comparisons and an offline source atlas. This reviews the full application at commit `4aca02d`, including its callers, tests, packaging and guides, rather than only the archive commit.

The load assumed is five 480-second waves, up to ten concurrent cases per key, 60-second supplier windows and a shared arena request limiter. Findings below distinguish live decision work from documentation maintenance; none justifies deleting the requested starter-kit archive, interactive atlas or historical benchmark reports.

**Should fix:**

1. **Three acceptance checks already disagree about missing defaults** — P2, high confidence (`src/tacto_negotiator/agents/llm.py:L158–171`, `src/tacto_negotiator/play.py:L184–197`, `src/tacto_negotiator/agents/routed.py:L109–133,L172–183`).
   - **What this is:** The legacy model guard, the runner's default closer and the routed closer separately decide whether a standing offer is safe to accept; `cli.py:L29–47,L73–79` still exposes both model policies. Legacy `llm.py:L6–8` promises must-have protection, while the deliberately naive comparison policy has a different documented purpose.
   - **Problem:** With the packaged S01 case, a required payment period of at least 30 days and no visible payment default, the legacy guard and default closer accept while the routed closer refuses. The local probe submits that legacy move to `Sim`: its actual 14-day default breaks the must-have and scores zero, because the older checks silently fill the gap from the buyer's 60-day plan.
   - **Fix:** Reuse the routed acceptance and closing checks through shared functions, preserving the legacy proposal policy and the explicitly naive comparison baseline; cover the missing-default case at each guarded entry point. Replacing the older acceptance branch and closer with calls should remove roughly 20 source lines after allowing for shared entry points; retain historical benchmark files as recorded.
   - **If we skip it:** A safety correction in the routed path still leaves the supported legacy path and generic deadline closer with different answers to the same evidence. Each future acceptance-rule change must be maintained and tested in three places.

**Nice to have:**

2. **Remove 25 catalog explanations that the atlas cannot display** — P3, high confidence (`docs/catalog.py:L260,L272–295`; consumers: `docs/build.py:L48–60,L85–124,L163–177,L254–255`).
   - **What this is:** `SYMBOLS` still describes the atlas builder's `report_summary` and 24 JavaScript/browser-test functions. The current inventory excludes `docs/`, and the guides and report summaries are embedded separately from source symbols.
   - **Problem:** Instrumenting every catalog lookup during a complete build finds zero reads of these 25 entries; deleting only those keys in memory produces byte-identical serialized atlas output. They duplicate explanations of code outside the source map and create an editing surface whose changes never reach the user.
   - **Fix:** Delete those 25 entries, leaving the actual builder, browser functions, generated snapshot and requested atlas intact. This removes exactly 25 source lines; the probe prints every unused key and verifies unchanged output.
   - **If we skip it:** Maintainers can spend time updating these entries and still publish no corresponding explanation. There is no material runtime or artifact-size penalty.

3. **Do not rebuild the whole message check for every candidate package** — P3, high confidence (`src/tacto_negotiator/agents/routed.py:L223–247`, especially `L245`; final check: `L338–339`; repeated parser: `src/tacto_negotiator/docs.py:L75–80`).
   - **What this is:** The bounded package search calls the full outgoing-message validator for each candidate, then validates the chosen complete message again. Every inner call resolves the same thread and reparses the same quote, although `_structured_bargain` already checked the source choices and `_packages` checked the generated price and cost.
   - **Problem:** At the existing 256-candidate limit, an isolated decision parses one unchanged quote 259 times. A local substitution of only the inner check with `must_haves_ok` keeps the final validation and returned move identical, reduces parsing to three calls, and measured about 22.5 ms versus 8.9 ms per decision over 30 repeats on this machine.
   - **Fix:** Use the already imported `must_haves_ok(case, proposal)` to filter generated candidates and retain the full validator for the selected message; verify the existing package and near-miss tests. This is a one-line substitution with no net line reduction or new cache.
   - **If we skip it:** Up to ten simultaneous case workers repeat a small amount of avoidable CPU work for each decision. These measurements do not establish a threat to the 60-second supplier window, and this is lower priority than the documented supplier scheduling work.

The [local reproduction](repro_bloat.py) performs no HTTP requests or model calls and reads no credentials. Run it from `/tmp`:

```sh
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  /home/deimos/Documents/git_prv/tacto_negotiator/.venv/bin/python \
  /home/deimos/Documents/git_prv/tacto_negotiator/docs/reviews/repro_bloat.py
```

The probe asserts the divergent acceptance results, all 25 unused catalog entries, unchanged atlas serialization, identical candidate-search output and the exact parser call counts. Timings vary by machine and concurrent load; they are local CPU measurements, not hosted-provider measurements. The coordinating review also ran the baseline: 221 tests, 11 documentation tests, Ruff, atlas snapshot and browser checks passed.

No unused declared dependency was found: HTTPX serves the arena/model clients, pypdf and openpyxl handle attachments, FastAPI/Uvicorn serve the optional simulator, MCP exposes the optional data tools, and Anthropic serves the retained legacy provider option. The public client methods, explicit legacy/naive comparison choices, source snapshot, original-kit archive, data representations and preserved comparison reports have identifiable purposes; their existence or size is not a finding.

Verdict: Ship. The bloat findings do not block the default routed path; fix 1 before relying on the legacy/default closer for must-have safety. This verdict is limited to this review's scope and does not certify overall final readiness.

Lean: -45 lines possible, approximately: 20 from shared acceptance/closure logic and exactly 25 unused catalog entries; test changes are not included.

Not checked: Hosted arena/model behavior, real shared-wave latency, private credentials, or a full implementation/regression run of the proposed guard reuse and candidate optimization; application code was not changed.
