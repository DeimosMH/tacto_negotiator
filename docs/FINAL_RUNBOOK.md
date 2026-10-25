# Sunday final runbook

The exact steps for running the final from this machine. Times are Europe/Berlin. The final starts at 13:30, and the code freeze is at 13:00.

## Configuration

The buyer is the routed agent with **Claude Opus 5.5 at high effort in fast mode**. It reads `LLM_API_KEY_ANTHROPIC` and `ARENA_API_KEY` from `.env`. Pass the model settings on the command line, so `.env` stays unchanged:

```sh
export LLM_PROVIDER=anthropic LLM_MODEL=claude-opus-5-5 LLM_EFFORT=high LLM_SPEED=fast
```

Do not use max effort: every call exceeded the decision deadline in hosted tests.

`LLM_SPEED=fast` uses the same model and effort with about 2.3× faster output. In hosted v17 it cut model time per case from 37 s to 16 s on average and scored 0.789 (v16 at normal speed: 0.752). If fast mode is rate-limited, a call falls back to normal speed on its own.

## 1. After 08:00: get the Sunday release

**Automatic:** a systemd user timer runs `tools/sunday_release.py` at 08:10. It retries every minute until the release is public (until 10:30), downloads only `release-sunday` into `data/release-sunday`, checks that `DataPackage` merges it, runs the tests, and shows a desktop notification with the result (log: `logs/sunday-release.log`). Check it with `systemctl --user list-timers tacto-sunday-release.timer`. The timer does not survive a reboot, and the machine must be awake at 08:10. If the notification says it failed, or never comes, do it by hand:

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
4. Check that the Anthropic account has credit for about 60 cases (roughly $0.40 per case in fast mode, so at least $25).
5. Stop all practice and experiment processes (`pgrep -af tacto`). Practice sessions count against the 10 open sessions and can block wave 1.

## 3. 13:00: freeze

Commit, note the hash (`git rev-parse HEAD`) and submit it on the organisers' platform. Run exactly that commit.

## 4. Before 13:30: start and leave running

```sh
systemd-inhibit --what=idle:sleep --why="Tacto final" \
  env LLM_PROVIDER=anthropic LLM_MODEL=claude-opus-5-5 LLM_EFFORT=high LLM_SPEED=fast \
  .venv/bin/tacto run --set final --agent routed --data data --parallel 10 --waves 5 2>&1 | tee final.log
```

Until wave 1 starts, the case list returns 403 and the runner keeps waiting. That is expected.

## 5. During the final: hands off

- Do not pick cases, write messages, or connect the runner to the leaderboard or event feed.
- To watch, open `docs/index.html#deals` in a browser and tick **Auto-refresh**. Every case appears there as it runs, with each message, its time and the decision path ([Reading a deal](DEALS.md)). It only reads local files the runner writes; it sends nothing.
- If the runner crashes, start the same command again. It resumes its open sessions and never opens a second session per case.
- Keep the machine awake and online. A wired connection is safer than venue Wi-Fi.

## What the agent does

- **Openings:** keeps the supplier's own cash terms (payment, chargeback, ppm, lead time) and anchors 10–25 % below the quote by category goal.
- **Concessions:** 20 % of the gap per round, measured in cash cost.
- **Supplier-health floor:** never pushes a stabilise or secure_supply supplier below the price where the health charge outweighs the saving.
- **Offer sheets:** the extra sheets in a message cost us the same, and none is priced far below the rest.
- **Final offers:** never countered, because hosted suppliers walk away. A cheaper alternative in a final offer is sent back once.
- **Deadlines:** a slow model falls back to a scripted counter, and idle RFQ threads may lapse instead of forcing the case to close.

Hosted practice on 25 October (v1.1 candidate, Claude Opus fast mode): passes of 0.789, 0.804, 0.789, 0.790, 0.797 and 0.793, plus 0.793 through `tacto run --set practice` itself. On the out-of-sample bench (the 9 practice cases never hosted, real model against local suppliers) v1.1 scores 0.797 against v1.0.1's 0.667 ([EVALUATION.md](EVALUATION.md)). Two live runs lost a case to runner errors that are now fixed: an accept of an offer replaced while we decided (re-read and decide again), and a network error (the runner resumes the session). Every hosted practice session since 23 October is in the [deal journal](DEALS.md).
