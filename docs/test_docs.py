"""Offline checks for the source-derived atlas. Run with unittest; no third-party packages."""

import ast
import csv
import json
import re
import tempfile
import unittest
from pathlib import Path

import build
from catalog import CODE, DATA, FILES


class AtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build.generate()
        cls.nodes = {node["id"]: node for node in cls.data["nodes"]}
        cls.edges = cls.data["edges"]

    def test_committed_snapshot_matches_sources(self):
        self.assertEqual((build.ROOT / build.OUTPUT).read_text(), build.serialize(self.data))

    def test_every_public_file_has_a_reviewed_role(self):
        expected = {p.relative_to(build.ROOT).as_posix() for p in build.inventory()}
        actual = {node["id"] for node in self.nodes.values() if node["kind"] == "file"}
        self.assertEqual(actual, expected)
        self.assertTrue(set(FILES).issubset(actual))
        for node in self.nodes.values():
            self.assertTrue(node["role"] and node["why"] and node["subgroup"], node["id"])
            self.assertIn(node["group"], self.data["groups"])

    def test_secret_paths_and_symlinks_are_excluded_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = ["materials/slides.pdf", "starter_kit/public.py", "starter_kit/.env.example", "starter_kit/.env", "starter_kit/.env.backup", "starter_kit/env/key.txt", "starter_kit/.venv/private.py", "docs/.aws/credentials", "docs/secret.key", "docs/node_modules/pkg/index.js", "docs/__pycache__/cached.pyc"]
            for path in paths:
                file = root / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("fixture")
            (root / "docs/link.py").symlink_to(root / "starter_kit/.env")
            (root / "docs/linked-dir").symlink_to(root / "starter_kit/env", target_is_directory=True)
            found = {p.relative_to(root).as_posix() for p in build.inventory(root)}
            self.assertEqual(found, {"materials/slides.pdf", "starter_kit/public.py", "starter_kit/.env.example"})

    def test_graph_edges_have_valid_endpoints_and_provenance(self):
        self.assertEqual(len(self.nodes), len(self.data["nodes"]))
        keys = set()
        for edge in self.edges:
            self.assertIn(edge["source"], self.nodes)
            self.assertIn(edge["target"], self.nodes)
            self.assertTrue(edge["relation"] and edge["evidence"])
            key = edge["source"], edge["target"], edge["relation"]
            self.assertNotIn(key, keys)
            keys.add(key)
        self.assertTrue(any(e["source"] == CODE + "agents/llm.py" and e["target"] == CODE + "cost.py" and "AST line" in e["evidence"] for e in self.edges))
        self.assertTrue(any(e["source"] == CODE + "play.py" and e["target"] == CODE + "docs.py" for e in self.edges))

    def test_proposed_components_cannot_be_mistaken_for_files(self):
        proposals = [node for node in self.nodes.values() if node["status"] == "proposed"]
        self.assertEqual(len(proposals), 6)
        self.assertTrue(all(node["kind"] == "proposal" and node["id"].startswith("proposal:") for node in proposals))
        for edge in self.edges:
            if edge["source"].startswith("proposal:"):
                self.assertEqual(edge["evidence"], "proposed")

    def test_every_python_class_and_function_is_listed(self):
        def collect(parent, scope=""):
            names = []
            for item in ast.iter_child_nodes(parent):
                if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = f"{scope}.{item.name}" if scope else item.name
                    names.append(name)
                    names.extend(collect(item, name))
                else:
                    names.extend(collect(item, scope))
            return names

        for node in self.nodes.values():
            if not node["id"].endswith(".py"):
                continue
            tree = ast.parse((build.ROOT / node["id"]).read_text())
            expected = collect(tree)
            self.assertEqual(expected, [symbol["name"] for symbol in node["symbols"]], node["id"])
        client = self.nodes[CODE + "client.py"]
        state = next(symbol["state"] for symbol in client["symbols"] if symbol["name"] == "ArenaClient")
        self.assertTrue({"http", "practice_remaining", "_lock", "_next"}.issubset(state))

    def test_all_table_schemas_rows_and_declared_foreign_keys_match(self):
        schema = json.loads((build.ROOT / DATA / "datapackage.json").read_text())
        for resource in schema["resources"]:
            path = DATA + resource["path"]
            node = self.nodes[path]
            with (build.ROOT / path).open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(node["rows"], len(rows))
            self.assertEqual([f["name"] for f in node["fields"]], [f["name"] for f in resource["schema"]["fields"]])
            for foreign_key in resource["schema"].get("foreignKeys", []):
                target = DATA + "data/" + foreign_key["reference"]["resource"] + ".csv"
                self.assertTrue(any(e["source"] == path and e["target"] == target and e["evidence"] == "declared foreign key" for e in self.edges), (path, target))

    def test_each_contract_and_correspondence_has_its_own_record_and_edge(self):
        for table in ("documents", "contracts"):
            with (build.ROOT / DATA / "data" / f"{table}.csv").open(newline="") as stream:
                for row in csv.DictReader(stream):
                    path = DATA + row["file"]
                    node = self.nodes[path]
                    self.assertTrue(any(f["name"] == "id" and f["value"] == row["id"] for f in node["fields"]))
                    self.assertTrue(any(e["target"] == path and e["source"] == DATA + f"data/{table}.csv" for e in self.edges))

    def test_json_snapshot_is_script_safe_and_round_trips(self):
        payload = {"text": "</script><img src=x onerror=alert(1)>\u2028"}
        script = build.serialize(payload)
        self.assertNotIn("<", script)
        self.assertEqual(json.loads(script.split("window.ATLAS = ", 1)[1].removesuffix(";\n")), payload)

    def test_competition_reports_embed_metrics_and_bounded_samples(self):
        report = {"status": "completed", "config": {"waves": 5}, "aggregate": {"script": {"valid": 100}},
                  "matches": [{"variant_id": "script", "score": 1, "decisions": [{"body": "omitted trace"}]}] * 100,
                  "budget": {"committed_usd": 0.5, "calls": [{"usage": "omitted call"}] * 20}}
        summary = build.report_summary(report)
        self.assertEqual(summary["aggregate"], report["aggregate"])
        self.assertEqual(summary["match_count"], 100)
        self.assertEqual(summary["provider_call_count"], 20)
        self.assertEqual(len(summary["sample_matches"]), 3)
        self.assertEqual(summary["budget"], {"committed_usd": 0.5})
        self.assertNotIn("omitted", json.dumps(summary))
        for path, node in self.nodes.items():
            if path.startswith("docs/results/"):
                self.assertNotIn("content", node)
                self.assertLessEqual(len(node["sample"]), 3)
                self.assertLess(len(json.dumps(node)), 50_000)

    def test_app_loads_only_local_assets_and_guides_exist(self):
        html = (build.ROOT / "docs/index.html").read_text()
        for asset in re.findall(r'<script[^>]+src="([^"]+)"|<link[^>]+href="([^"]+)"', html):
            path = asset[0] or asset[1]
            if path.startswith("data:"):
                continue
            self.assertFalse(path.startswith(("http:", "https:", "//")))
            self.assertTrue((build.ROOT / "docs" / path).is_file())
        self.assertIn("connect-src 'none'", html)
        for guide in ("README.md", "TUTORIAL.md", "IMPLEMENTATION.md", "DATA_GUIDE.md", "LOCAL_TESTING.md", "FINAL_READINESS.md", "MODEL_SELECTION.md", "ROUTING.md", "COMPETITION.md"):
            text = self.nodes["docs/" + guide]["content"]
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                from urllib.parse import unquote
                self.assertTrue((build.ROOT / "docs" / unquote(target.split("#")[0])).exists(), target)


if __name__ == "__main__":
    unittest.main()
