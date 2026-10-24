# Tacto Negotiator source atlas

Open [index.html](index.html) directly in your browser. It works offline with local assets, no npm install and no API keys.

For charts, match filters and step-by-step decision explanations, start the [local results dashboard](WEB_UI.md) at localhost:1234. The atlas explains the source; the dashboard explores saved runs.

The atlas explains the active application in src/tacto_negotiator, its root tests and configuration, and the data snapshot. The supplied starter kit remains archived under materials/starter_kit. Guides and historical result reports are available here without appearing as program modules in the source map.

For the implemented scripts, parallel local scenarios and measured OpenAI comparison, open [Local competition](COMPETITION.md). It includes reproducible commands, results, limitations and a step-by-step guide to changing a scenario or policy.

New to the project? Open the **Start here** tab or read [the beginner tutorial](TUTORIAL.md). It explains one negotiation, walks through a local buyer change with expected results, and shows how to modify the project and this atlas.

Preparing for Sunday 13:30? Open **Sunday final** for [implementation priorities, five-wave simulations and the rehearsal guide](FINAL_READINESS.md), then read [the hosted model recommendation and qualification procedure](MODEL_SELECTION.md).

The implemented **Routing guide** explains [how a decision selects a script, Sol or optional Astra](ROUTING.md), including the deadline adapter, configuration, local examples and how to change a rule.

The [review resolution record](reviews/FIXES.md) maps all nine findings to fixes and regression tests, with results from 900 new offline matches. The original reviews remain available as historical evidence.

For a local HTTP URL, run this from the repository root:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory docs
```

Then open http://127.0.0.1:8000. The entire explanation is embedded in data.js; original-source links work when opening index.html directly or when serving the repository root at /docs/. When serving only docs, links to files outside docs are hidden; use the embedded source view instead.

## Explore

- Expand directories in the file tree. Select a file for its purpose, rationale, classes/functions, internal state or data schema, source content and relationships.
- Search names, paths, explanations, identifiers and symbol names. Matching branches open automatically.
- Enable Full explanations to show each visible file's complete explanation in the tree; Expand all reveals the entire file guide at once.
- The landing page's Source files view shows the implemented Python modules, dashboard assets and connected runtime systems/libraries. Switch to Groups & subgroups to inspect the logical organization.
- Selecting a source, test, configuration or data file shows its direct incoming and outgoing neighbors. Edges describe imports, joins, configuration or reviewed interactions. The relationship list includes direction, meaning and evidence, and is a keyboard-accessible alternative to the graph.
- Use the graph controls to zoom, drag its background to pan, or Fit to reset. Group boundaries and node colors follow the legend. Nodes can be activated with Enter or Space. Browser Back/Forward restores selections.
- Start here contains the beginner walkthrough. Sunday final contains priorities and simulation results; Routing guide explains the implemented routed buyer. Implementation describes the current architecture and separates remaining work. Proposals remain in the written guides rather than appearing as implemented graph nodes.

## Refresh and verify

```sh
python3 -B docs/build.py
python3 -B docs/build.py --check
python3 -B -m unittest discover -s docs -p 'test_docs.py' -v
node docs/test_browser.mjs
```

build.py uses Python 3.11+ and extracts actual files, AST symbols and state, table schemas, row counts, foreign keys, document records and declared imports. catalog.py holds reviewed purpose/rationale, logical groups and semantic edges. Add a FILES explanation when adding an active source/test/configuration file. Register guide basenames in GUIDES; their content is embedded separately from graph nodes. data.js is included so opening the page needs no generator. --check detects stale source snapshots.

This is a static dependency model, not a full runtime call graph. The file tree and graph cover src, tests, active data and allowlisted root project files. docs and materials are excluded from that inventory. Guide pages and compact report summaries have separate payload sections. env/.env, virtual environments, caches, Git internals and symlinks are excluded from source reading. The Python AST extractor includes deferred imports but does not infer arbitrary reflection or dynamic file paths; reviewed links explain those interactions. Table samples show three rows; full data remains in its files.

The page uses native HTML disclosures and SVG. Display settings are stored only in the browser; if localStorage is unavailable, navigation still works. The app loads no fonts, libraries, analytics or remote content. External reference links are user-initiated navigation.

Source attribution: see [NOTICE.md](../NOTICE.md). Starter code is [MIT](../materials/starter_kit/LICENSE); upstream documentation and data are [CC BY 4.0](../materials/starter_kit/LICENSE-data). Preserve [market data attribution](../data/ATTRIBUTION.md) when sharing derived material. Historical competition reports retain their original paths, source hashes and commits; [the competition guide](COMPETITION.md) explains the package-name mapping.

Further reading: [beginner tutorial](TUTORIAL.md), [implementation](IMPLEMENTATION.md), [data guide](DATA_GUIDE.md), [local test commands and limitations](LOCAL_TESTING.md).
