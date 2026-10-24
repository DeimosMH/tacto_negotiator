# Your first run, from start to finish

Get a feel for Tacto Negotiator, run a local experiment, and follow a buyer's decisions in Run Explorer. Start with the included results if you just want to look around. No AI account, API key or Python coding is needed for this guide.

Tacto is a procurement buyer: it negotiates the price and conditions of a supply contract. It compares the **whole package**, checks the buyer's requirements, and decides whether to accept or walk away. The local suppliers are simulations, so you can experiment without contacting the hosted arena.

## 1. Know the three places

| Place | What you do there |
| --- | --- |
| These docs | Learn the workflow, read guides and explore the source map. The docs work offline. |
| Your terminal | Install the project and run a negotiation experiment. Run commands from the repository folder containing `pyproject.toml`. |
| Run Explorer | Open saved results at [localhost:1234](http://localhost:1234), compare strategies and inspect decisions. Start its server in your terminal first. |

A **case** is one negotiation problem. A **strategy** is the buyer's decision policy. A **match** is one attempt by a strategy at a case. A **run** is a saved collection of matches. You will open a run, pick a match, and follow its decisions.

## 2. Install and open the example

You need **Python 3.12** and **uv**, the project's package manager. If they are already installed, check them in your terminal:

```sh
python3.12 --version
uv --version
```

If either command is missing, install that tool using its official instructions: [Python downloads](https://www.python.org/downloads/) and [uv installation](https://docs.astral.sh/uv/getting-started/installation/). The commands below use a macOS or Linux shell.

In the repository folder, install the dependencies and start the Explorer:

```sh
uv sync --frozen --extra sim --extra mcp
.venv/bin/tacto-ui
```

The first command creates `.venv`, a local folder containing the project's Python environment. It needs internet access to download dependencies. Later local runs do not need credentials or model access.

Open [localhost:1234](http://localhost:1234). Leave the terminal running while you use the Explorer. Select **dashboard-demo.json** in **Saved run**. It contains 150 synthetic matches across three strategies, ten scenario families and five repeated waves.

**Checkpoint:** you can see the match count, valid outcomes, mean score and strategy bars. You have opened an existing result; no negotiation starts when you browse it.

## 3. Read one negotiation

1. Read **Valid outcomes** beside **Mean score**. A valid walk-away is allowed; a valid deal can still have a poor score. The score is shown out of 100, not in euros.
2. Select the **scripts** bar under **Score by strategy**. This filters the run to the scripted buyer. **Reset** shows all strategies again.
3. Go to **Matches**. The initial sort puts the lowest scores first so you can investigate weak outcomes. Select a case to open its details.
4. Compare **Total cost** and **Feasible reference**. Total cost includes more than unit price: payment, freight, tooling and other terms can change it. Expand **Final terms & cost context** for the recorded package.
5. Follow **Price movement**, then choose a step in the **Decision timeline**. **What** is the action, **Why** explains the recorded policy reason, and **How** shows the route and timing.

The chart's prices are **per unit**; they are not the package's total cost. A missing value means it was not recorded. Model attempts can include deliberately unavailable callbacks in offline tests; the count does not mean paid API calls occurred.

**Checkpoint:** you can explain one result by pointing to a submitted action and its recorded reason. For more detail, see [Using Run Explorer](WEB_UI.md).

## 4. Make your own run

Keep Run Explorer running in the first terminal. Open a second terminal in the same repository folder, then run:

```sh
.venv/bin/tacto-compete --strategies naive close scripts \
  --seeds 1 --waves 1 --workers 4 \
  --output docs/results/my-first-run.json
```

This compares three policies against the ten local scenario families: **30 matches** in total. It uses local suppliers and does not load credentials or call a paid model. `naive` is the simple baseline, `close` checks closing options, and `scripts` uses deterministic negotiation rules.

Wait for the command to finish. In the Explorer, press **Refresh** and choose **my-first-run.json**. Use the same strategy and outcome filters to compare it with the included example, one report at a time.

A **seed** selects a reproducible synthetic variation. A **wave** repeats the trials; it is not a live tournament round. **Workers** controls how many matches the local runner processes concurrently. For a first experiment, keep these settings unchanged.

**Checkpoint:** a new JSON file exists in `docs/results`, and its 30 matches appear in the Explorer. Choose a new output filename when you want to keep another experiment separately.

## 5. If something gets stuck

| What you see | What to do |
| --- | --- |
| `uv` or Python is missing | Install the prerequisites in step 2, then open a fresh terminal. |
| `.venv/bin/tacto-ui` is missing | Check that you are in the repository folder and run the `uv sync` command again. |
| The browser cannot connect | Start `.venv/bin/tacto-ui` and keep that terminal open. The docs themselves do not start a server. |
| Port 1234 is already in use | Try `.venv/bin/tacto-ui --port 1235`, then open [localhost:1235](http://localhost:1235). |
| Your new run is not listed | Wait for the competition command to finish, confirm the file is in `docs/results`, then press **Refresh**. **Open JSON** can also read it directly. |
| No matches after filtering | Press **Reset**. Filters apply to the cards, charts and match list together. |
| An older report has missing details | Older runs may lack price snapshots or decision traces. Use `dashboard-demo.json` to learn the full view. |

Press **Ctrl+C** in the Explorer terminal when you are finished. This stops the local web server; saved reports remain on disk.

## 6. Choose your next step

- **Change the buyer:** [Your first negotiation](TUTORIAL.md) walks through a small Python policy change with an expected result.
- **Understand the decisions:** [Decision routing](ROUTING.md) explains scripts, the fast model and the optional stronger model.
- **Find the code:** open the [Project map](index.html), select a file, and follow its incoming and outgoing connections. Use **Browse source** or press `/` to search.
- **Compare more experiments:** [Local competition](COMPETITION.md) covers scenarios, seeds and the limits of synthetic results.
- **Prepare for hosted use:** read [Hosted practice](LIVE_PRACTICE.md) and [Model selection](MODEL_SELECTION.md) before configuring arena access. Hosted commands are a separate workflow.
