# Submission and pitch

What to hand in for the Tacto challenge, and how the pitch works. Deadline: **Sunday 13:00, the code
freeze, on the organisers' platform**.

## What to submit

1. **Your repo:** the agent and a README that says how to run it (setup, the environment variables it
   needs, without values, and the command). Put the **commit hash** your agent runs in the final into
   the description. The jury must be able to open the repo.
2. **A short description** of your approach: how your agent prepares, what it reads in the data, how
   it decides what to offer, and when it closes or walks away.
3. **Slides** for the pitch, if you make the top 5: they pitch at 15:10.

Keep every key out of the repo: no `.env`, no LLM key, no arena key.

## At 13:30 your agent is already running

The Live Final starts at 13:30, half an hour after the code freeze. Start your agent before 13:30 and
leave it running:

```bash
uv run arena run --set final --agent llm --parallel 10
```

Or your own runner that polls `GET /v1/cases?set=final`. The kit's runner waits for wave 1, plays each
wave's cases in parallel within the 8-minute cap and stops after the fifth wave. From 13:30 there is no
human in the loop, and your agent must not read `/v1/events`, `/screen`, `/leaderboard` or `/results`.
We check the top 5 for pushes after the freeze.

Checklist for 13:00:

- The machine and network you use for the final; one practice case went through it at the Sunday-morning
  pre-flight.
- `uv run arena data` ran after 08:00, so the Sunday release sits next to the package.
- `uv run arena me` answers with your team: the key works.
- LLM credits and rate limits for about an hour of parallel play.
- The runner is started and logs to a file; the laptop is on power and does not sleep.

The clocks go back in the night to Sunday (03:00 becomes 02:00): every Sunday time here is the new time.

## The pitch

- **The top 5:** the five teams with the most points after the five waves.
- **5 minutes pitch + 2 minutes of questions**, from 15:10 at the Tacto office. The slot does not
  stretch.
- **Every replay is open to the jury.** After the final, each case on https://arena.tacto.ai/results
  links to its replay. Pick one negotiation and walk us through it.
- Questions to expect: Why did your agent write that? Would it have worked on a real supplier? What
  did your agent learn about suppliers, and from where?
- **Places 1-3:** the jury sets them among the top 5, from the Live Final score and the pitch.
