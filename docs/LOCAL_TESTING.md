# Verify locally without credentials

These checks use the root .venv and the active tests directory. Install the project from the root pyproject.toml using [the README](../README.md) first. Run tests with a cleared environment and no credential file loading; do not source ./env or .env. Normal tacto commands load .env after parsing arguments, while tacto --help exits before loading it. The commands below use injected/local services and do not contact the hosted arena.

## Documentation checks

Run from the repository root:

```sh
python3 -B docs/build.py
python3 -B docs/build.py --check
python3 -B -m unittest discover -s docs -p 'test_docs.py' -v
node --check docs/app.js
node docs/test_browser.mjs
```

The generator uses the standard library, reads allowlisted public files, skips symlinks and env/runtime directories, and never imports tacto_negotiator. The browser check uses Node 22+ and an installed Chrome/Chromium executable (override with CHROME=/path/to/chrome). It opens the site with file:// in a temporary browser profile, blocks HTTP(S) requests and tests interactions through DevTools. It needs permission to launch a browser and bind a local debugging socket; no arena or model service is involved.

## Project contract suite

From the repository root, capture the executable and test path, then run in /tmp so the working directory contains no repository .env:

```sh
TACTO_PYTHON="$PWD/.venv/bin/python"
TACTO_TESTS="$PWD/tests"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_PYTHON" -m pytest -q -p no:cacheprovider "$TACTO_TESTS"
```

The active suite has no hosted-service parametrization. HTTP integration tests start uvicorn on 127.0.0.1 with a simulator-issued key that works only in that simulator. Unit tests inject model completions and mock HTTP. No real keys are read. The root pytest configuration collects tests only; the archived starter-kit tests under materials are outside that default collection. Local HTTP fixtures need socket permissions even though they never contact the internet.

Verified after the review fixes: **244 passed, 0 skipped**. The suite covers contract behavior, routing, provider cancellation, script coverage, budget accounting, competition and CLI behavior. New regressions cover stale-offer recovery, timeout rounds, terminal openings, shared acceptance, supplier-standard preservation, indexed quotes and scorer boundaries. See [resolved findings and validation](reviews/FIXES.md). Real model measurements remain a separate opt-in experiment recorded in [the competition guide](COMPETITION.md).

After adding the local dashboard: **252 passed, 0 skipped**. The eight additional tests cover public trace snapshots and the dashboard's report API, field projection, static assets, legacy data, malformed input, file boundaries, report limits and CLI help.

## Dashboard checks

From the repository root, with Node 22+ and Chrome/Chromium installed:

```sh
node --check src/tacto_negotiator/web_ui/dashboard.js
node tests/test_web_browser.mjs
```

The browser check launches its own temporary loopback server, clears the child processes' environment, and supplies synthetic current, historical, empty and malformed reports. It checks score conversion from 0–1 to points out of 100, charts, filters, pagination, keyboard operation, deadline overrides, missing values, browser-only file import, HTML escaping, mobile layout and the absence of remote requests. Set CHROME to the browser executable if needed. Optional DASHBOARD_SCREENSHOT=/tmp/tacto-dashboard.png saves a screenshot.

Validated with this change: 252 application tests, 11 atlas tests, both Chrome interaction suites, Ruff and JavaScript syntax checks. A new 150-match local report, docs/results/dashboard-demo.json, exercises three strategies across ten families and five waves. Existing reports and original materials retain their bytes. The wheel and source distribution build offline; the installed wheel's command, packaged HTML/CSS/JS and report API work from /tmp. That wheel smoke test reused already installed base dependencies because a clean offline dependency resolution lacked cached httpx metadata; it was not a fresh dependency-download test.

The [dashboard guide](WEB_UI.md) explains how to start the UI, inspect a match and add reports. It is a saved-run viewer; no real provider or hosted arena call is part of these checks.

## Final-wave tests

The added test_final.py uses the actual simulator HTTP routes through FastAPI TestClient and httpx.MockTransport: no network connection is made. It drives five 480-second waves with virtual time and 150 sessions across three teams. Other tests inject model failures, slow decisions, slow suppliers, retry faults, restart/resume and an active host extension. A separate concurrent burst checks the shared client's pacing. Two diagnostic scenarios preserve failure outcomes for unbounded legacy/injected decision calls and neglected RFQ threads; passing them does not mean the agent is final-ready. See [the full scenario table and executable rehearsal](FINAL_READINESS.md).

## Routed buyer checks

[test_routed.py](../tests/test_routed.py) uses fixed completions to check script/fast/strong routing, available-time selection, common validation and deterministic fallback. [test_completion.py](../tests/test_completion.py) injects mock HTTP and delayed response bodies to check output limits, effort settings, queue deadlines, cooperative cancellation and rejection of partial or late output. [test_routing_integration.py](../tests/test_routing_integration.py) checks runner budgets/closing hooks, resumed timers, aged RFQ threads, changed supplier status, metadata failures, opening/reply quote failures, fallback telemetry, five virtual waves and CLI integration. [test_client_deadline.py](../tests/test_client_deadline.py) verifies per-call read timeout/retry overrides while preserving the normal POST retry ID. [test_cli.py](../tests/test_cli.py) checks the active command defaults and help/configuration boundaries. None needs a real provider key.

For a focused command and a small example that prints the selected routes, see [the routing guide](ROUTING.md). The tests prove behavior under their injected conditions. They do not establish actual Sol/Astra speed or success under arbitrary uncooperative transports.

## Parallel strategy competitions

[Local competition](COMPETITION.md) documents the new ten-scenario harness, offline commands, measured OpenAI comparison and its sanitized reports. The default command uses no credentials. Paid runs require both --live and an explicitly selected --key-file; only LLM_API_KEY is extracted, and all suppliers remain local. This opt-in benchmark is separate from the credential-free unit suite above.

The additional tests cover bounded full-cost package enumeration, semantic near misses, private supplier data isolation, feasible references, quote-floor consistency, parallel worker overlap, model deadlines, budget reservations and the CLI opt-in boundary. These tests inject HTTP transports and never make real provider calls.

Post-review validation ran two parallel cohorts of 450 matches each, with ten workers per cohort and five repeated waves. Both guarded strategies returned 600/600 valid outcomes; scripts needed the deliberately unavailable model callback in 60 cases, with no API calls. The delayed-supplier holdout still recorded thread timeouts. [Compact results and source hashes](reviews/validation.json) preserve these observations; this is not a shared-wave capacity or provider qualification run.

## Migration verification

The application moved from the historical arena_kit package into src/tacto_negotiator, with root packaging, tests and data. The recorded model reports retain their original bytes and pre-migration source hashes/commits; they were not regenerated by this migration.

| Check | Verified result |
| --- | --- |
| Root test suite and default collection | 221 passed, zero skipped; collection includes the root tests only. |
| Package builds | Source distribution and wheel built from the root pyproject.toml. |
| Clean wheel installation | Installed into a separate temporary environment with locked dependencies; tacto_negotiator loaded from site-packages, arena_kit was unavailable, and all three packaged demo cases were present. |
| Installed commands | tacto, tacto-sim, tacto-compete and python -m tacto_negotiator help checks passed. Ten local scenarios completed with valid outcomes from the installed wheel. |
| Behavior comparison | 150 offline paired matches across five waves reproduced the previous report's economic results, validity and routes exactly. Timing is a new local measurement, not expected to match byte-for-byte. |
| Static checks | Ruff over src, tests and docs, plus Git whitespace checks, passed. |
| Original-kit archive | All 319 original tracked files are restored under materials/starter_kit. Its tracked Git tree matches 0166e6948f53ecbf629ab371135f69ec3243f998:starter_kit exactly: 4af48d9f151551f985a0e8819f1896f4287fc0ef. See [the archive verification instructions](../materials/README.md). |
| Container execution | Docker was unavailable, so the image was not built or run. |

These checks validate packaging and retained local behavior. They do not repeat the historical paid model experiment or establish performance against the hosted arena.

## What the base simulator really proves

Its supplier starts approximately 10% above the current/fallback price (additional bidders get an offset), reduces price by a fixed 2% of its opening when sent offers, and stops at 90% of that opening. It may agree when a proposed price reaches its next threshold, emits a final offer from round five, and may walk if pushed again. Questions without term sheets keep the standing quote. Citation checks mostly test whether IDs exist, not whether the numeric claim is true or relevant.

Its in-memory teams/sessions disappear when the process stops. A controllable clock tests timeouts and configurable rehearsal waves: two by default, or --rehearsal-waves 5 --rehearsal-cap 480 for the final. These mechanics are useful for protocol correctness and recovery, not supplier psychology or realistic business constraints.

The competition subclass replaces this fixed concession script with seeded reservation prices, term premiums, different concession rates and delays. Its evaluator includes feasible outside options and private relationship costs. That broadens policy checks; it still does not reproduce the hosted supplier model.

Inspection also finds differences from the hosted specification: create_app has no /v1/data or ZIP-download routes; its practice leaderboard ignores the requested final set; its best-score benchmark does not implement the full hidden supplier economics/alternative calculation; and request-field validation is not complete parity with every documented limit. The source calls it an exact contract simulator, but these boundaries are visible in code. Do not extend offline assertions into claims about hosted behavior.

Sources: [sim.py](../src/tacto_negotiator/sim.py), [test fixture](../tests/conftest.py), [API](../materials/starter_kit/brief/api.md).
