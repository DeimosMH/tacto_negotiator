# Explore negotiation results locally

The run explorer shows **what happened, why the controller chose an action, and how the negotiation progressed**. It reads saved competition JSON and runs at [localhost:1234](http://localhost:1234). It needs no frontend build, npm packages, API key or internet connection.

## Start the dashboard

Install the project using the root README, then run from the repository root:

```sh
.venv/bin/tacto-ui
```

Open http://localhost:1234. Keep the terminal open; Ctrl+C stops the server. If the command is missing after an update, reinstall with `uv sync --frozen --extra sim --extra mcp`. The module form also works:

```sh
.venv/bin/python -m tacto_negotiator.web --port 1234 --reports docs/results
```

The server binds to 127.0.0.1. Change `--port` if another program uses 1234. `--reports` selects one directory of saved JSON reports; the default is docs/results relative to the working directory. The installed wheel includes all three web assets.

New to the project? Start with the [beginner guide](BEGINNER_GUIDE.md) for installation and your first 30-match experiment.

## Your first walkthrough

1. Select **dashboard-demo.json**. This fresh scoring-v2 run contains 150 local matches: three strategies, ten scenario families, one seed and five repeated waves. It uses synthetic suppliers and no paid model calls. Its model attempts are deliberately unavailable callbacks used to exercise fallback.
2. Read the five summary metrics: filtered match count, valid outcomes, mean score, model attempts and mean decision time. The raw report stores scores from 0 to 1; the dashboard converts them to points out of 100. Missing costs stay **Unknown**.
3. Select a strategy bar or outcome bar. This sets the matching filter. Use scenario, wave and case/seed search to narrow it further. **Reset** clears the filters. Charts and cards always describe the filtered set.
4. Use **Explore matches** to jump to the match list, then select a match in the table. The inspector shows its final outcome, score, full cost, feasible reference, supplier delay, timeouts and final terms. On mobile it scrolls to the inspector; **Back to matches** returns to the list.
5. Follow **Price movement**. Green dots are standing quotes before a decision; orange dots are submitted buyer offers; blue dots are supplier replies. Hover for supplier, step and unit price. This is unit price, not total package cost. Multiple bidders or package options can produce several dots per step.
6. Select a numbered timeline step. **What** shows the submitted action. **Why** translates the recorded policy reason. **How** shows route, model attempts and measured decision time against its budget. Remaining thread and wave time show the situation before the decision.
7. Expand **Proposed action** and **Submitted action** when a deadline override appears. They preserve the distinction between an agent's proposal and what the runner actually sent. Submission errors are shown separately.

The **Reading guide** in the header opens a short walkthrough and explains scores, timings and model attempts. **Match investigation** shows how many matches remain after filtering. Empty filter results offer a direct reset.

Historical offline, holdout and model reports remain available. A banner identifies scores that predate evaluator v2, and diagnostic pilots stay visibly excluded from comparison. Older reports may lack quote snapshots, final terms or decision details; the inspector tells you when these were not recorded. Reports are viewed individually, so different evaluator versions are never merged into one ranking.

## Add your own local results

Generate a new report with local suppliers, without reading credentials:

```sh
.venv/bin/tacto-compete --strategies naive close scripts \
  --seeds 1 2 --waves 5 --workers 8 --output docs/results/my-run.json
```

Press **Refresh** and select my-run.json. Refresh also reloads the current saved report while keeping its filters. In-progress accounting checkpoints have no matches and are omitted until a result report exists; this is a saved-run explorer, not a live progress monitor.

Alternatively, choose **Open JSON** to inspect a file elsewhere on your computer. Its content stays in the browser and is never uploaded. Switching back to a saved report discards that imported view; reopen the file to return to it. Invalid imports leave the previous view intact. Reports must contain a matches array, have no more than 50,000 matches, and be at most 32 MB.

## What the explanations establish

“Why” explains a controller reason code, not a model's hidden reasoning. An exception reason may identify a failed model/validation path without a complete diagnosis. Model attempts include failed or disabled callbacks and must not be read as billed API calls.

Elapsed negotiation time uses the simulator clock, including supplier delay and measured decision work. Decision time is measured locally. Each match has its own virtual clock; wave labels do not prove shared-wave throughput or hosted final readiness. Full-cost comparisons include non-price terms and evaluator assumptions. See [competition methodology](COMPETITION.md) for those limits.

## Change the dashboard step by step

1. Edit the layout in [index.html](../src/tacto_negotiator/web_ui/index.html) or appearance in [styles.css](../src/tacto_negotiator/web_ui/styles.css). Reload the browser; no build is needed.
2. Edit filters, charts or reason wording in [dashboard.js](../src/tacto_negotiator/web_ui/dashboard.js). The reasons table must describe the actual policy in agents/routed.py. Keep missing evidence explicit and escape imported text before inserting HTML.
3. To expose a new recorded field, add it to the relevant allowlist in [web.py](../src/tacto_negotiator/web.py), then read it in the browser. Restart the Python server after server changes. Do not expose prompts, authorization headers or raw provider ledgers.
4. If a field is not recorded yet, add its public snapshot in [competition.py](../src/tacto_negotiator/competition.py), test it and generate a new local report. Existing reports remain historical evidence.
5. Run the Python suite and browser check in [local verification](LOCAL_TESTING.md). The dashboard browser check is `node tests/test_web_browser.mjs`, using Node 22+ and an installed Chrome/Chromium. It starts its own temporary server and supplies synthetic reports.
6. Update docs/catalog.py when adding a source file, then regenerate the atlas with `python3 -B docs/build.py`. The source map groups the server, HTML, JavaScript and stylesheet under **Results dashboard**, with links to the saved-report boundary.

The server uses Python's standard library and exposes only its three assets and report API. It cannot start a negotiation, execute a model request or write a result file. It does not load .env or serve arbitrary repository paths.
