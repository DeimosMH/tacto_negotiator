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

Verified suite in this snapshot: 59 passed, 1 hosted variant skipped. This includes 25 new Sunday readiness scenarios. The local suite verifies the kit's contract and arithmetic, not the proposed EvidenceBuyer or real model quality.

The added test_final.py uses the actual simulator HTTP routes through FastAPI TestClient and httpx.MockTransport: no network connection is made. It drives five 480-second waves with virtual time and 150 sessions across three teams. Other tests inject model failures, slow decisions, slow suppliers, retry faults, restart/resume and an active host extension. A separate concurrent burst checks the shared client's pacing. Two diagnostic scenarios explicitly assert the current failure outcomes for uncancelled model calls and neglected RFQ threads; passing them does not mean the agent is final-ready. See [the full scenario table and executable rehearsal](FINAL_READINESS.md).

## What the simulator really proves

Its supplier starts approximately 10% above the current/fallback price (additional bidders get an offset), reduces price by a fixed 2% of its opening when sent offers, and stops at 90% of that opening. It may agree when a proposed price reaches its next threshold, emits a final offer from round five, and may walk if pushed again. Questions without term sheets keep the standing quote. Citation checks mostly test whether IDs exist, not whether the numeric claim is true or relevant.

Its in-memory teams/sessions disappear when the process stops. A controllable clock tests timeouts and configurable rehearsal waves: two by default, or --rehearsal-waves 5 --rehearsal-cap 480 for the final. These mechanics are useful for protocol correctness and recovery, not supplier psychology or realistic business constraints.

Inspection also finds differences from the hosted specification: create_app has no /v1/data or ZIP-download routes; its practice leaderboard ignores the requested final set; its best-score benchmark does not implement the full hidden supplier economics/alternative calculation; and request-field validation is not complete parity with every documented limit. The source calls it an exact contract simulator, but these boundaries are visible in code. Do not extend offline assertions into claims about hosted behavior.

Sources: [sim.py](../starter_kit/starter-kit/arena_kit/sim.py), [test fixture](../starter_kit/starter-kit/tests/conftest.py), [API](../starter_kit/brief/api.md).
