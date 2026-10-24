# Tacto Negotiator

A procurement buyer that negotiates complete packages under supplier and wave deadlines.
It uses deterministic scripts for supported quantitative decisions, a model for unresolved
semantics, and a shared validator before any message or acceptance reaches the arena.

## Project structure

```text
src/tacto_negotiator/
  agents/             Script/fast/strong routing and deadline-bound model calls
  client.py           Arena HTTP, pacing, retries and move IDs
  play.py             Sessions, document loading, case workers and final waves
  cost.py             Full-cost arithmetic, defaults and must-have checks
  data.py, docs.py     Evidence lookup and supplier document parsing
  cli.py              Main buyer command; routed agent by default
  sim.py, sim_cases/   Local supplier API and packaged demo cases
  competition.py      Paired scenarios and private synthetic suppliers
  benchmark.py        Model access checks and shared spending reservations
  compete.py          Offline or explicitly enabled paid comparisons
  mcp_server.py       Optional read-only data tools
tests/                Local policy, protocol, deadline and accounting checks
data/                 Active public procurement dataset
docs/                 Offline source map, tutorials, architecture and reports
materials/            Challenge materials and restored original starter kit
pyproject.toml        Package, commands, dependencies and test configuration
uv.lock               Reproducible dependency resolution
```

The application is installed from `src/`; `materials/` is reference-only. There is no
runtime dependency on the archived starter kit. The source map shows the implemented
application and its dependencies, with documentation and archived materials kept out
of the program graph.

## Install and run locally

Use Python 3.12 and uv from the repository root:

```sh
uv sync --frozen --extra sim --extra mcp
uv run --frozen tacto --help
uv run --frozen tacto-compete --strategies naive close scripts \
  --seeds 0 1 2 --waves 5 --workers 8 --output /tmp/tacto-local.json
```

The competition command runs local suppliers and reads no credentials by default.
`tacto-sim serve --host 127.0.0.1 --port 8000` starts the separate local supplier API.
Follow [your first negotiation](docs/TUTORIAL.md) for registration and a complete
local session. `python -m tacto_negotiator` is equivalent to the `tacto` command.

## How the program works

1. `cli.py` selects the agent and creates a shared `ArenaClient`.
2. `play.py` discovers cases, opens sessions and reads supplier documents.
3. `RoutedAgent` uses the remaining time and tries a supported script. Complete
   package prices compensate for payment, freight and other cost differences.
4. If interpretation is needed, one bounded model call proposes a decision. The fast
   and optional strong tiers share provider capacity and the same deadline contract.
5. The common validator checks terms, defaults, must-haves, alternatives and the
   standing offer. Failure uses local fallback; only the runner submits moves.
6. The runner refreshes state, repeats the loop and closes before the wave cap.

See [project architecture](docs/IMPLEMENTATION.md), [routing](docs/ROUTING.md) and
the [interactive source map](docs/index.html). Every source file has its role,
classes, internal state and dependency links in the atlas.

## Configure a hosted buyer

Use `.env.example` as a configuration reference. Put actual values in an ignored
`.env` or the process environment. The main buyer command loads `.env` after parsing
arguments; process variables win. Help and offline competitions do not load it.

Set `ARENA_URL` and `ARENA_API_KEY` for the intended arena, `ARENA_DATA=data`, and
`LLM_API_KEY` for model calls. The measured configuration is Sol low for the ordinary
tier and Astra low for the optional strong tier. `tacto play CASE_ID --agent routed`
plays a supplied case; `tacto run --set final --waves 5 --parallel 10 --agent routed`
waits for and plays final waves. These commands contact the configured arena.

Explicit `--agent naive` and `--agent llm` select comparison/legacy policies.
The legacy LLM path does not have the routed adapter's total deadline guarantees.
Paid local comparisons require both `tacto-compete --live` and `--key-file`;
see [competition commands and spending controls](docs/COMPETITION.md).

## Check changes

Run from the repository root after installing dependencies:

```sh
TACTO_ROOT="$PWD"
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_ROOT/.venv/bin/python" -m pytest -q -p no:cacheprovider "$TACTO_ROOT/tests"
cd "$TACTO_ROOT"
uv run --frozen ruff check src tests docs
python3 -B docs/build.py
python3 -B docs/build.py --check
python3 -B -m unittest discover -s docs -p test_docs.py
node docs/test_browser.mjs
```

Tests use local suppliers and injected model transports. The root pytest configuration
collects `tests/`, so archived starter-kit tests are never run accidentally.
See [local verification](docs/LOCAL_TESTING.md) for the complete workflow.

## Results and remaining work

All nine findings in the [project review](docs/reviews/README.md) are addressed; the
[resolution record](docs/reviews/FIXES.md) links fixes, 244 passing tests and 900 new
offline matches. The guarded policies had 600/600 valid outcomes. Delayed RFQs
still expose the documented scheduling gap.

The recorded comparison contains 2,700 scored local matches and 1,699 actual OpenAI
calls, including its diagnostic pilot. Scripts covered 80% of the tested families;
hybrid scored 73.07/100 with 35 model calls versus Sol-only's 67.00 with 498 calls.
These are synthetic supplier results, not a hosted final leaderboard. Read the
[method, results and limitations](docs/COMPETITION.md).

Those historical scores predate scoring version 2 and its eligible-opening repair
scenario. They are preserved measurements, not scores revalidated for the current
evaluator; the paid models were not rerun during the review fixes.

Sequential supplier requests within RFQs, supplier-health estimation, grounded prose
claims and representative medium-semantic qualification remain work. Migration does
not change those boundaries. [Sunday readiness](docs/FINAL_READINESS.md) prioritizes them.

See [NOTICE.md](NOTICE.md) for source/data attribution and the restored upstream snapshot.
