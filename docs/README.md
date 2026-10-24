# Tacto project atlas

Open [index.html](index.html) directly in your browser. It works offline with local assets, no npm install and no API keys.

New to the project? Open the **Start here** tab or read [the beginner tutorial](TUTORIAL.md). It explains one negotiation, walks through a local buyer change with expected results, and shows how to modify the project and this atlas.

Preparing for Sunday 13:30? Open **Sunday final** for [implementation priorities, five-wave simulations and the rehearsal guide](FINAL_READINESS.md), then read [the hosted model recommendation and qualification procedure](MODEL_SELECTION.md).

For a local HTTP URL, run this from the repository root:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory docs
```

Then open http://127.0.0.1:8000. The entire explanation is embedded in data.js; original-source links work when opening index.html directly or when serving the repository root at /docs/. When serving only docs, links to files outside docs are hidden; use the embedded source view instead.

## Explore

- Expand directories in the file tree. Select a file for its purpose, rationale, classes/functions, internal state or data schema, source content and relationships.
- Search names, paths, explanations, identifiers and symbol names. Matching branches open automatically.
- Enable Full explanations to show each visible file's complete explanation in the tree; Expand all reveals the entire file guide at once.
- The landing page shows the whole project grouped by responsibility and subgroup. Select a group to inspect its members, or switch to All files for the complete graph.
- Selecting a file shows its incoming and outgoing neighbors. Solid edges describe existing imports, joins, configuration or reviewed interactions. Dashed edges describe proposed agent responsibilities. The relationship list includes direction, meaning and evidence, and is a keyboard-accessible alternative to the graph.
- Use the graph controls to zoom, drag its background to pan, or Fit to reset. Group boundaries and node colors follow the legend. Nodes can be activated with Enter or Space. Browser Back/Forward restores selections.
- Start here contains the beginner walkthrough. Sunday final contains priorities, simulation results and model selection. Implementation, Data guide and Local checks contain the deeper design notes. Proposed nodes are clearly labelled; the full proposed buyer remains unimplemented.

## Refresh and verify

```sh
python3 -B docs/build.py
python3 -B docs/build.py --check
python3 -B -m unittest discover -s docs -p 'test_docs.py' -v
node docs/test_browser.mjs
```

build.py uses Python 3.11+ and extracts actual files, AST symbols and state, table schemas, row counts, foreign keys, document records and declared imports. catalog.py holds reviewed purpose/rationale, logical groups and semantic edges. Add a reviewed explanation there when adding a public file. data.js is included so opening the page needs no generator. --check detects stale source snapshots; the generated file does not recursively embed itself.

This is a static dependency model, not a full runtime call graph. It covers the public files in materials, starter_kit, docs and the root orientation files. env/.env, virtual environments, caches, Git internals and symlinks are excluded before reading. The Python AST extractor includes deferred imports but does not infer arbitrary reflection or dynamic file paths. Those interactions are represented by reviewed links. Table samples show three rows; full data stays in the original files. PDF entries explain their role and sections rather than embedding binary contents.

The page uses native HTML disclosures and SVG. Display settings are stored only in the browser; if localStorage is unavailable, navigation still works. The app loads no fonts, libraries, analytics or remote content. External reference links are user-initiated navigation.

Source attribution: starter code is [MIT](../starter_kit/LICENSE); upstream documentation and data are [CC BY 4.0](../starter_kit/LICENSE-data). Preserve [market data attribution](../starter_kit/data/ATTRIBUTION.md) when sharing derived material.

Further reading: [beginner tutorial](TUTORIAL.md), [implementation](IMPLEMENTATION.md), [data guide](DATA_GUIDE.md), [local test commands and limitations](LOCAL_TESTING.md).
