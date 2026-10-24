# Sunday final runbook

The exact steps for running the final from this machine. Times are Europe/Berlin. The final starts at 13:30, and the code freeze is at 13:00.

## Configuration

The buyer is the routed agent with **Claude Opus 5.5 at high effort**. It reads `LLM_API_KEY_ANTHROPIC` and `ARENA_API_KEY` from `.env`. Pass the model settings on the command line, so `.env` stays unchanged:

```sh
export LLM_PROVIDER=anthropic LLM_MODEL=claude-opus-5-5 LLM_EFFORT=high
```

Do not use max effort: every call exceeded the decision deadline in hosted tests.

## 1. After 08:00: get the Sunday release

```sh
.venv/bin/tacto data --out data
ls data/release-sunday/facts.json
.venv/bin/python -c "from tacto_negotiator.data import DataPackage; print(DataPackage('data').release)"
```

The second command must print the `release-sunday` path. The package itself does not change; the release adds news, letters and a memo that some final cases cite. Without it, those claims fail.

## 2. Pre-flight (morning, before the freeze)

1. Run the tests: `.venv/bin/python -m pytest -q docs/test_docs.py tests`. All must pass.
2. Free sandbox check (no quota): `.venv/bin/tacto play case:D01 --agent routed --data data`. Expect a deal and a score near 0.87.
3. One practice case on the final settings: `.venv/bin/tacto play case:P04 --agent routed --data data`.
4. Check that the Anthropic account has credit for about 60 cases (roughly $0.20 per case).
5. Stop all practice and experiment processes (`pgrep -af tacto`). Practice sessions count against the 10 open sessions and can block wave 1.

## 3. 13:00: freeze

Commit, note the hash (`git rev-parse HEAD`) and submit it on the organisers' platform. Run exactly that commit.

## 4. Before 13:30: start and leave running

```sh
systemd-inhibit --what=idle:sleep --why="Tacto final" \
  env LLM_PROVIDER=anthropic LLM_MODEL=claude-opus-5-5 LLM_EFFORT=high \
  .venv/bin/tacto run --set final --agent routed --data data --parallel 10 --waves 5 2>&1 | tee final.log
```

Until wave 1 starts, the case list returns 403 and the runner keeps waiting. That is expected.

## 5. During the final: hands off

- Do not pick cases, write messages, or connect the runner to the leaderboard or event feed.
- If the runner crashes, start the same command again. It resumes its open sessions and never opens a second session per case.
- Keep the machine awake and online. A wired connection is safer than venue Wi-Fi.

## What the agent does

- **Openings:** keeps the supplier's own cash terms (payment, chargeback, ppm, lead time) and anchors 10–25 % below the quote by category goal.
- **Concessions:** 20 % of the gap per round, measured in cash cost.
- **Supplier-health floor:** never pushes a stabilise or secure_supply supplier below the price where the health charge outweighs the saving.
- **Offer sheets:** the extra sheets in a message cost us the same, and none is priced far below the rest.
- **Final offers:** never countered, because hosted suppliers walk away. A cheaper alternative in a final offer is sent back once.
- **Deadlines:** a slow model falls back to a scripted counter, and idle RFQ threads may lapse instead of forcing the case to close.

Hosted practice on 24 October: best single run 0.699 (v7), best-per-case board 0.741.
