# Verify locally without credentials

Do not source ./env or .env, copy keys, or run arena data/me/play against the hosted service for these checks. The CLI main function automatically loads .env from its current directory. The commands below avoid that entry point and erase inherited provider and arena variables with env -i. Use the existing starter-kit virtual environment; it already contains the Python 3.12 dependencies on this checkout.

## Documentation checks

Run from the repository root:

```sh
python3 -B docs/build.py
python3 -B docs/build.py --check
python3 -B -m unittest discover -s docs -p 'test_docs.py' -v
node --check docs/app.js
node docs/test_browser.mjs
```

The generator uses the standard library, reads allowlisted public files, skips symlinks and env/runtime directories, and never imports arena_kit. The browser check uses Node 22+ and an installed Chrome/Chromium executable (override with CHROME=/path/to/chrome). It opens the site with file:// in a temporary browser profile, blocks HTTP(S) requests and tests interactions through DevTools. It needs permission to launch a browser and bind a local debugging socket; no arena or model service is involved.

## Existing starter-kit contract suite

From the repository root, capture the executable and test path, then run in /tmp so the working directory contains no repository .env:

```sh
TACTO_PYTHON="$PWD/starter_kit/starter-kit/.venv/bin/python"
TACTO_TESTS="$PWD/starter_kit/starter-kit/tests"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_PYTHON" -m pytest -q -p no:cacheprovider "$TACTO_TESTS"
```

The cleared environment removes ARENA_TEST_URL and ARENA_TEST_KEY, so the hosted test variant skips. HTTP integration tests start uvicorn on 127.0.0.1 with a simulator-issued key that works only in that simulator. Unit tests inject model completions and mock HTTP. No real keys are read. The test fixture needs local socket permissions; a restricted sandbox can reject it even though it never contacts the internet.

Verified suite in this snapshot: **146 passed, 1 hosted variant skipped**. This includes the original 59 local checks and 87 added routing, completion, client-read and integration cases. The suite checks the implemented contract and policy behavior, not real model quality or the full proposed evidence buyer.

The added test_final.py uses the actual simulator HTTP routes through FastAPI TestClient and httpx.MockTransport: no network connection is made. It drives five 480-second waves with virtual time and 150 sessions across three teams. Other tests inject model failures, slow decisions, slow suppliers, retry faults, restart/resume and an active host extension. A separate concurrent burst checks the shared client's pacing. Two diagnostic scenarios preserve failure outcomes for unbounded legacy/injected decision calls and neglected RFQ threads; passing them does not mean the agent is final-ready. See [the full scenario table and executable rehearsal](FINAL_READINESS.md).

## Routed buyer checks

[test_routed.py](../starter_kit/starter-kit/tests/test_routed.py) uses fixed completions to check script/fast/strong routing, available-time selection, common validation and deterministic fallback. [test_completion.py](../starter_kit/starter-kit/tests/test_completion.py) injects mock HTTP and delayed response bodies to check output limits, effort settings, queue deadlines, cooperative cancellation and rejection of partial or late output. [test_routing_integration.py](../starter_kit/starter-kit/tests/test_routing_integration.py) checks runner budgets/closing hooks, resumed timers, aged RFQ threads, changed supplier status, metadata failures, opening/reply quote failures, fallback telemetry, five virtual waves and CLI integration. [test_client_deadline.py](../starter_kit/starter-kit/tests/test_client_deadline.py) verifies per-call read timeout/retry overrides while preserving the normal POST retry ID. None needs a real provider key.

For a focused command and a small example that prints the selected routes, see [the routing guide](ROUTING.md). The tests prove behavior under their injected conditions. They do not establish actual Sol/Astra speed or success under arbitrary uncooperative transports.

## What the simulator really proves

Its supplier starts approximately 10% above the current/fallback price (additional bidders get an offset), reduces price by a fixed 2% of its opening when sent offers, and stops at 90% of that opening. It may agree when a proposed price reaches its next threshold, emits a final offer from round five, and may walk if pushed again. Questions without term sheets keep the standing quote. Citation checks mostly test whether IDs exist, not whether the numeric claim is true or relevant.

Its in-memory teams/sessions disappear when the process stops. A controllable clock tests timeouts and configurable rehearsal waves: two by default, or --rehearsal-waves 5 --rehearsal-cap 480 for the final. These mechanics are useful for protocol correctness and recovery, not supplier psychology or realistic business constraints.

Inspection also finds differences from the hosted specification: create_app has no /v1/data or ZIP-download routes; its practice leaderboard ignores the requested final set; its best-score benchmark does not implement the full hidden supplier economics/alternative calculation; and request-field validation is not complete parity with every documented limit. The source calls it an exact contract simulator, but these boundaries are visible in code. Do not extend offline assertions into claims about hosted behavior.

Sources: [sim.py](../starter_kit/starter-kit/arena_kit/sim.py), [test fixture](../starter_kit/starter-kit/tests/conftest.py), [API](../starter_kit/brief/api.md).
