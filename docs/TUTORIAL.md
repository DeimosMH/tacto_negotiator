# Your first negotiation, step by step

For installation and your first saved run, start with the [beginner guide](BEGINNER_GUIDE.md). This tutorial is the next step: changing the buyer in Python. You will follow one negotiation, run it on your own computer, change the buyer's behavior and check the result. Basic Python knowledge is enough for the coding steps. You do not need an AI model, a paid account or an arena API key.

The first two sections explain the system. Steps 1–6 are a local exercise. Step 7 explains how to move your experiment into Tacto Negotiator. Step 8 shows how to update this documentation site. The exercise creates a temporary Python file. After it, the [routing guide](ROUTING.md) introduces the default script/fast/strong buyer; [the architecture guide](IMPLEMENTATION.md) explains the implemented blocks and remaining work.

## What are we trying to do?

Imagine you buy parts for a pump manufacturer. A supplier sends a price increase. Your job is to understand the situation, propose a better deal and either accept an acceptable offer or keep your alternative.

The agent is the Python object making those decisions. The runner repeatedly asks it what to do next. The client delivers that decision to a supplier through an HTTP API. For this tutorial the supplier is a local program, called the simulator, with predictable replies. The documentation website is a separate tool for understanding these files; browsing it does not start negotiations.

| Word | What it means here |
| --- | --- |
| Case | One negotiation problem: parts, suppliers, required quantity, allowed terms and buyer objectives. |
| Session | One attempt at a case, with its own history and result. |
| Thread | The conversation with one supplier inside a session. A request for quotation, or RFQ, can have several threads. |
| Term sheet | A structured package of prices and conditions. These values bind; promises in the email body do not change them. |
| Standing offer | The supplier's current term sheet, identified by an offer ID. Accept that ID to close a deal. |
| Standard terms | Supplier defaults from its quote PDF, used when its offer omits a field. |
| Must-have | A hard requirement. Breaking one makes the accepted deal score zero. |
| Alternative | The buyer's option if the whole case ends without a deal. |
| Total cost, C | The cost of covering the required quantity and period, including payment, freight and other case-specific costs. |
| Evidence ID | A reference such as sc:70270@2026Q3 that connects an argument to a recorded fact. |

A lower price is only one way to improve a deal. Paying later can reduce financing cost. A short contract can leave expensive uncovered demand. Read [the cost evaluator](../src/tacto_negotiator/cost.py) when you need the exact arithmetic.

## Follow one decision through the code

```text
Case + conversation history
            |
            v
       agent.decide()          chooses the next message or action
            |
            v
       play() / RemoteTable    manages the session and its history
            |
            v
       ArenaClient            sends HTTP to the selected server
            |
            v
       Local simulator        returns a quote, reply or result
            |
            +---- reply and document text return to the agent
```

For the naive example buyer, a decision is ordinary Python logic. The LLM example buyer asks a language model for a proposed decision, then applies some checks. Both implement the same decide(case, threads) method. You can change the decision policy without rewriting HTTP, PDF reading or the negotiation loop.

RoutedAgent is the default policy for tacto play and tacto run. It implements that same method and also accepts timing information from the runner through decide_timed. It chooses a known script, the fast model or an optional stronger model for the current decision. The [routing walkthrough](ROUTING.md) follows those branches and shows how to test them without a provider. This tutorial deliberately starts with the simpler NaiveAgent so one policy change is easy to observe.

The returned dictionary has supplier_id and message. For example, a buyer proposing a price and payment period in case S01 returns this shape:

```json
{
  "supplier_id": "sup:70270",
  "message": {
    "subject": "Adapter piece HI",
    "body": "Could we agree this price with 90 days net?",
    "term_sheets": [
      {"terms": {"unit_price": 16.59, "payment_days": 90}}
    ]
  }
}
```

This is an example shape, not a saved offer you should accept. The simulator chooses its reply from the live session. Later the buyer accepts the current term_sheet_id, or returns a walk_away action. Even if the supplier says agreed, the buyer must still accept. Walking away ends the entire case, including any other supplier threads.

Use the atlas to see this flow: search for naive.py, select it, and read Classes & functions. Follow its outgoing link to cost.py. Next open play.py and client.py. The graph shows actual source relationships; the arrows are not a chronological execution trace. Incoming means another file uses or references the selected file. Outgoing means the selected file uses or references another file or system. Remaining design work is described in the guides rather than drawn as implemented modules.

## Step 1 — Get your bearings

Open **Project map** in the sidebar, then expand **Browse source**. Expand src → tacto_negotiator in the tree. This is the active application package. Tests live in tests, the procurement snapshot in data, and project configuration in the root pyproject.toml. The original supplied kit is archived in materials/starter_kit and excluded from the active project map. Edit src and tests when changing the application.

| Start with | What to look for |
| --- | --- |
| [S01.json](../src/tacto_negotiator/sim_cases/S01.json) | One supplier, 6,650 parts for one year, unit_price and payment_days negotiable. |
| [agents/naive.py](../src/tacto_negotiator/agents/naive.py) | decide chooses a thread, proposes a price or accepts. |
| [play.py](../src/tacto_negotiator/play.py) | play opens the session, reads documents and repeats decisions until closure. |
| [cost.py](../src/tacto_negotiator/cost.py) | complete_offer fills allowed fields; total_cost evaluates the resulting deal. |
| [data.py](../src/tacto_negotiator/data.py) | DataPackage gives read-only access to facts, SQL and document text. |

In S01, payment_days may be 14, 30, 45, 60 or 90. Freight, duration, volume and lead time are fixed. Changing a negotiable field is our first experiment; changing the case file would change the problem being measured.

Click View source or Read document to inspect a file inside the site. Full explanations in tree is useful when reading a whole directory; keep it off when you want a compact explorer. Use Fit if you lose your place after zooming the map.

## Step 2 — Start the local supplier

Open a terminal in the repository root, the directory containing pyproject.toml, src, tests, data and docs. Install the project using the [root README](../README.md) if this is a fresh checkout. The root .venv supplies Python 3.12 and the simulator dependencies. A .venv is an installed Python environment; it is separate from ./env or .env credential files.

Run these commands in terminal A:

```sh
TACTO_ROOT="$PWD"
TACTO_PYTHON="$TACTO_ROOT/.venv/bin/python"
"$TACTO_PYTHON" --version
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_ROOT/.venv/bin/tacto-sim" serve \
  --host 127.0.0.1 --port 8001 --no-clock \
  --data "$TACTO_ROOT/data"
```

Leave this terminal running. Expect a message explaining that no-clock is enabled and a server listening at http://127.0.0.1:8001. That address means your own computer. env -i starts the process without inherited arena or model credentials. Using tacto-sim also avoids the buyer tacto command's automatic .env loading. Use the executable shown: sim.py does not start its server when invoked with python -m tacto_negotiator.sim. The --data argument supplies evidence IDs; the demo cases themselves are packaged in src/tacto_negotiator/sim_cases.

The no-clock option lets you pause while learning. The normal protocol has a 60-second response window per supplier thread; re-enable the clock when testing timing. If Python or a module is missing, see the troubleshooting section below rather than copying credentials from another setup.

## Step 3 — Run the unchanged buyer

Open terminal B in the repository root and set these variables there too. Shell variables from terminal A are not shared automatically.

```sh
TACTO_ROOT="$PWD"
TACTO_PYTHON="$TACTO_ROOT/.venv/bin/python"
```

In your editor, create /tmp/tacto_first_buyer.py with the following contents:

```python
import json
import uuid

from tacto_negotiator import ArenaClient, RemoteTable, play
from tacto_negotiator.agents import NaiveAgent

BASE_URL = "http://127.0.0.1:8001"
client = ArenaClient(BASE_URL, api_key="local-registration-only")
registration = client.register("Tutorial " + uuid.uuid4().hex[:8], "sim")
client.http.headers["X-API-Key"] = registration["api_key"]


def report(label, result):
    print(label)
    print(json.dumps({
        "deal": result["deal"],
        "terms": result["terms"],
        "total_cost_eur": result["c_deal"],
        "payment_cost_eur": (result["cost_breakdown"] or {}).get("payment_terms"),
        "score": result["score"],
    }, indent=2))


baseline = play(RemoteTable(client), "case:S01", NaiveAgent())
report("BASELINE", baseline)
assert baseline["deal"] and baseline["must_haves_ok"]
```

Run it in terminal B:

```sh
cd /tmp
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$TACTO_PYTHON" /tmp/tacto_first_buyer.py
```

The script registers a temporary team with the local simulator, then runs one complete case. The key is created by that simulator and is not a hosted arena credential. The random team suffix lets you rerun the script without a duplicate-team error. No LLM is involved.

On the supplied S01/scripted supplier, expect a deal with unit_price 16.59, payment_days 60 and total_cost_eur 112451.5. Read the other returned terms: fixed FCA freight still costs money. A score is a normalized share of the available saving, not a euro amount.

Checkpoint: you have successfully exercised the buyer, runner, client, local supplier and quote-document reader. The website did not run this code; your Python process did.

## Step 4 — Make one behavioral change

Keep the baseline code. Append the following block to the same temporary Python file:

```python
from tacto_negotiator.cost import complete_offer
from tacto_negotiator.play import closing_move

PREFERRED_PAYMENT_DAYS = 90


class TutorialBuyer(NaiveAgent):
    def decide(self, case, threads):
        decision = super().decide(case, threads)
        message = decision["message"]

        if "payment_days" in case["negotiable"]:
            for sheet in message.get("term_sheets", []):
                sheet["terms"] = complete_offer(case, {
                    **sheet["terms"],
                    "payment_days": PREFERRED_PAYMENT_DAYS,
                })

        if message.get("action", {}).get("type") == "accept":
            return closing_move(case, threads)
        return decision
```

TutorialBuyer inherits the baseline's price strategy. super().decide gets that strategy's next decision. The new code changes payment_days only when the case permits negotiation of that field. complete_offer keeps the case's required negotiable fields and allowed option values. An unsupported numeric wish may be snapped to the nearest allowed option, so choose a value from S01's list for this exercise.

When the baseline wants to accept, closing_move compares live standing offers using evidenced standard terms, must-haves and the alternative. It uses the same acceptance check as RoutedAgent and the legacy model guard. It does not estimate supplier health, and the inherited price strategy is still simplistic. RoutedAgent also validates outgoing messages; inspect [its routing guide](ROUTING.md) before extending this experiment into a competition buyer.

Notice that changing payment_days in the structured term sheet matters even though we did not change the email wording. For a realistic buyer, make the email describe the same proposed trade.

## Step 5 — Compare two fresh sessions

Append this final block below your new class:

```python
modified = play(RemoteTable(client), "case:S01", TutorialBuyer())
report("MODIFIED", modified)

assert modified["deal"] and modified["must_haves_ok"]
assert modified["terms"]["payment_days"] == 90
assert modified["c_deal"] < baseline["c_deal"]
print("Local comparison passed: the changed terms reduced total cost.")
```

Run the same terminal B command from Step 3 again. It now plays the baseline and modified buyer in separate sessions against the same case. Expect the modified deal to retain unit_price 16.59, use payment_days 90 and have total_cost_eur 111716.01: a reduction of EUR 735.49. The last printed line confirms the checks passed.

Why did cost improve? S01's reference is 60 days, and delaying payment by another 30 days reduces the modeled financing cost. The local supplier script does not price this trade like a real supplier. A hosted supplier may demand a higher price for longer payment terms, so this exercise proves that your change travels through the system and affects the score calculation; it does not establish a universally good strategy.

## Step 6 — Experiment and diagnose

Change PREFERRED_PAYMENT_DAYS from 90 to 30 and rerun. The reports print before the assertions. You should now see a positive payment cost and a higher total cost than the baseline. The 90-day assertion intentionally fails: a valid request can still fail your objective. Restore 90 to return to the passing exercise. Keep a check for the behavior you actually intend rather than deleting a failed assertion without understanding it.

For a different price strategy, change TutorialBuyer() in the modified call to TutorialBuyer(anchor=0.05), keeping the baseline unchanged. The anchor is the fraction below the supplier's price used for the first proposal. Compare the result and number of exchanges, and restore the default before relying on this guide's exact figures. The comparison check may fail if your new strategy costs more. Change one variable at a time so you know what caused the result.

Read [S02](../src/tacto_negotiator/sim_cases/S02.json) before trying a two-supplier RFQ, and [S03](../src/tacto_negotiator/sim_cases/S03.json) before experimenting with contract duration and freight. Their allowed fields, alternatives and expected results differ; update case-specific assertions accordingly. Stop the supplier server with Ctrl+C in terminal A when finished.

## Step 7 — Move a useful experiment into the project

The following edits are instructions for your next change. This tutorial itself leaves the existing buyer source untouched.

1. Create src/tacto_negotiator/agents/tutorial.py. Copy only PREFERRED_PAYMENT_DAYS and the TutorialBuyer class from Step 4, plus imports of NaiveAgent, complete_offer and closing_move. Leave registration, the reports and the two play calls in the temporary experiment. Importing an agent module should not start a negotiation.
2. In [agents/__init__.py](../src/tacto_negotiator/agents/__init__.py), add from .tutorial import TutorialBuyer and include the string "TutorialBuyer" in __all__. This makes the new buyer available through tacto_negotiator.agents.
3. In [cli.py](../src/tacto_negotiator/cli.py), add the branch below inside agent_factory before the model setup. Return the class, as the naive branch does, so each case gets a separate agent instance.

```python
    if name == "tutorial":
        from .agents import TutorialBuyer
        return TutorialBuyer
```

4. In the same CLI file, add tutorial to both allowed --agent choice lists: one for play and one for run. Registering the factory alone does not make argparse accept the new name. Keep the existing default until you intentionally want to change it.
5. In the temporary exercise, import TutorialBuyer from tacto_negotiator.agents and remove its local class/constant/import block. Rerun the comparison. This checks the repository's implementation instead of an old copy in /tmp.
6. Add a regression test beside [test_kit.py](../tests/test_kit.py) or [test_sim.py](../tests/test_sim.py). Test the behavior you changed: complete allowed fields, the intended payment term, must-have/alternative handling and no change to a fixed payment term. Use the existing local sim fixture or plain dictionaries; inject a completion function if your change involves the LLM buyer.
7. Run the clean-environment project suite from [Local checks](LOCAL_TESTING.md). Then add catalogue explanations for your new source/test files and rebuild the atlas as in Step 8. Commit the implementation, tests and regenerated documentation together when you are ready to review the change.

Use this table when your next change has a different goal:

| I want to change… | Start here | What to check |
| --- | --- | --- |
| Price or term-selection policy | The agent's decide method | Message shape, allowed terms, alternatives and the final deal. |
| Evidence given to the model | LLMAgent.prepare and DataPackage | Exact IDs, units, relevance and preparation time. Preparation currently starts after opening the session. |
| Email wording | SYSTEM and rendering in agents/llm.py | Model output still passes the guard and says the same thing as its term sheets. |
| Acceptance or fallback behavior | play.acceptable_offer and play.closing_move; RoutedAgent.validate for outgoing messages | Guarded policies share acceptance rules. Preserve current-offer, default, numeric, must-have and alternative checks; supplier-health estimates remain work. |
| Script/fast/strong choice | RoutedAgent in agents/routed.py | The rule matches complete data, leaves time to send, and every resulting action passes routed validation. |
| Routed model execution | CompletionClient in agents/completion.py | Queue wait, output reading and cancellation share one deadline; incomplete output falls back locally. |
| Document interpretation | docs.py | Quote defaults survive extraction; add a fixture demonstrating the change. |
| Timing and parallel work | play.py | Other RFQ threads stay responsive; a stale offer is not accepted after recovery. |
| Cost calculations | cost.py | This is the published scoring model. Preserve it unless fixing a demonstrated mismatch with the contract. |

For evidence work, read [the data guide](DATA_GUIDE.md) next. The package separates SQL rows, checked fact values and document text. Putting extra IDs into an email does not by itself make an argument convincing.

## Step 8 — Modify the atlas itself

The documentation website has no backend. Its small update pipeline is:

```text
src + tests + data + root project files  ---> active tree and graph
docs guides + compact report summaries ---> guide pages
                         |
              docs/build.py + catalog.py
                         |
                    docs/data.js
                         |
              index.html + app.js + styles.css
```

1. To improve a written explanation, edit the relevant Markdown guide or the file's FILES entry in [catalog.py](catalog.py). Each FILES entry records group, subgroup, role and why it exists. A new ordinary source file needs an entry too; the generator reports its path if it has none.
2. To explain a function more clearly, add its qualified name to SYMBOLS in catalog.py. Python definitions, signatures, class attributes and import edges are extracted automatically; the catalogue supplies the explanation behind them.
3. To add an interaction that imports cannot reveal, add a tuple to LINKS: source ID, target ID, relationship description and evidence label. Both IDs must exist. Use ENTITIES for a real external system or library. Keep proposed components in the written implementation guide until they exist; they are not nodes in the active source map.
4. To change a logical group, edit the file's group/subgroup assignment and, if necessary, GROUPS in catalog.py. This changes the graph grouping; it does not move the file on disk. CSV table groups live in TABLES, and correspondence/contract records are derived from their metadata.
5. To change navigation or appearance, edit [index.html](index.html) or [styles.css](styles.css). Interaction behavior lives in [app.js](app.js). Register a new guide basename in catalog.py's GUIDES tuple and add a visible navigation link. Guides are embedded separately; they do not need FILES entries and do not become graph nodes. The archived materials are excluded too.
6. Regenerate data.js and run the checks below from the repository root. Do not hand-edit data.js: the generator will replace it.

```sh
python3 -B docs/build.py
python3 -B docs/build.py --check
python3 -B -m unittest discover -s docs -p 'test_docs.py' -v
node --check docs/app.js
node docs/test_browser.mjs
```

Refresh the browser after rebuilding. An HTML/CSS change is served directly, while embedded source explanations and guides come from the generated snapshot. If your code changed but the inspector still shows the old version, rebuild first. The browser check needs Node 22+ and an installed Chrome/Chromium; the Python documentation generator needs Python 3.11+.

## If something does not work

| Symptom | What to do |
| --- | --- |
| The .venv Python path does not exist | Confirm you started in the repository root. Follow the installation steps in the root README for Python 3.12 and the root pyproject.toml dependencies. Installing packages may require network access; no arena key is needed. |
| No module named tacto_negotiator, fastapi or uvicorn | Use the root .venv interpreter and install the root project there. The archived starter kit is not the active Python package. |
| Connection refused | Keep terminal A running. Both server and script must use port 8001. The documentation website's port 8000 is a separate service. |
| Address already in use | Choose another free localhost port and change it in both commands and BASE_URL. Leave the host as 127.0.0.1 for this exercise. |
| A timeout while reading the code | Confirm the simulator started with --no-clock for the learning exercise. |
| A 422 response | Read its detail: check term names, allowed values and the standing offer ID. Invalid messages do not use a negotiation round. |
| The changed deal is worse | Inspect cost_breakdown and fixed/standard terms. A lower unit price or a valid message does not guarantee lower total cost. |
| The atlas reports a missing explanation or stale snapshot | Add the named FILES entry if needed, run docs/build.py, then rerun --check. |

Continue with [the architecture guide](IMPLEMENTATION.md) to trace the current modules, shared action guard, state ownership and runtime flow. It separates remaining evidence preparation, supplier-health estimates and scheduling work from the implementation already in use.

Next, use [the routing guide](ROUTING.md) to inspect and change script/fast/strong decisions, and [the Sunday final guide](FINAL_READINESS.md) to rehearse five eight-minute waves. [Model selection](MODEL_SELECTION.md) explains the provider qualification still needed before deployment.
