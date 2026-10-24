"""Build the offline atlas using only public repository files and the Python standard library."""

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import sqlite3
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from pathlib import Path

from catalog import (
    CODE,
    DATA,
    DEPENDENCIES,
    ENTITIES,
    FIELD_NOTES,
    FILES,
    GROUPS,
    GUIDES,
    INTERNALS,
    LINKS,
    SYMBOLS,
    TABLES,
)

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = "docs/data.js"
SKIP = {".git", ".venv", "venv", "env", ".agents", ".codex", ".aws", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
TEXT_TYPES = {".py", ".js", ".mjs", ".html", ".css", ".md", ".toml"}
ROOT_FILES = ("README.md", ".gitignore", ".dockerignore", ".env.example", "pyproject.toml", "uv.lock", "Dockerfile", "LICENSE", "LICENSE-data", "NOTICE.md")


def allowed(path):
    """Reject secret/runtime paths before opening files; the traversal also rejects symlinks."""
    return not any(
        p in SKIP or (p.startswith(".env") and p != ".env.example")
        or p.lower().endswith((".pem", ".key"))
        for p in Path(path).parts
    )


def inventory(root=ROOT):
    """Only these source roots belong in the published atlas; never crawl the user's home."""
    paths = [root / name for name in ROOT_FILES if (root / name).is_file() and not (root / name).is_symlink()]
    for base in ("src", "tests", "data"):
        if (root / base).is_symlink():
            continue
        for directory, dirs, names in os.walk(root / base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if allowed(d) and not (Path(directory) / d).is_symlink())
            for name in sorted(names):
                path = Path(directory) / name
                if allowed(path.relative_to(root)) and not path.is_symlink():
                    paths.append(path)
    return sorted(paths)


def field(name, value, description=""):
    return {"name": name, "value": value, "description": description}


def report_summary(report):
    """Keep competition metrics useful in the atlas without embedding every trace or API call."""
    keys = ("status", "phase", "started_utc", "finished_utc", "baseline_commit", "policy_commit", "source_sha256",
            "provider_calls", "campaign_accounting",
            "models", "effort", "service_tier", "max_output_tokens", "config", "peak_active_matches", "aggregate",
            "limitations", "excluded_from_comparison", "exclusion_reason")
    summary = {key: report[key] for key in keys if key in report}
    matches = report.get("matches", [])
    budget = report.get("budget") or {}
    summary.update(match_count=len(matches), provider_call_count=len(budget.get("calls", [])))
    if budget:
        summary["budget"] = {key: value for key, value in budget.items() if key != "calls"}
    sample_fields = ("variant_id", "scenario", "seed", "wave", "case_id", "valid", "score", "cost_eur",
                     "reference_cost_eur", "regret_eur", "error", "model_attempts", "decision_wall_s")
    summary["sample_matches"] = [{key: row[key] for key in sample_fields if key in row} for row in matches[:3]]
    return summary


def python_details(text, path):
    """Read classes, function contracts and stored state without executing source code."""
    tree = ast.parse(text)
    symbols, state = [], []

    def explain(node, owner=""):
        name = f"{owner}.{node.name}" if owner else node.name
        doc = ast.get_docstring(node)
        role = SYMBOLS.get(name) or SYMBOLS.get(node.name) or doc
        if not role:
            words = node.name.removeprefix("test_").strip("_").replace("_", " ")
            role = ("Regression check: " if node.name.startswith("test_") else "Operation: ") + words + ". See the source body for its exact steps."
        signature = node.name if isinstance(node, ast.ClassDef) else f"{node.name}({ast.unparse(node.args)})"
        item = {"name": name, "signature": signature, "kind": "class" if isinstance(node, ast.ClassDef) else "function", "line": node.lineno, "description": role}
        if isinstance(node, ast.ClassDef):
            attrs = set()
            for assignment in ast.walk(node):
                if isinstance(assignment, (ast.Assign, ast.AnnAssign)):
                    targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
                    attrs.update(a.attr for target in targets for a in ast.walk(target)
                                 if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name) and a.value.id == "self")
            item["state"] = sorted(attrs)
        symbols.append(item)
        for child in definitions(node):
            explain(child, name)

    def definitions(parent):
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                yield child
            else:
                yield from definitions(child)

    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            explain(node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        value = ast.unparse(node.value) if node.value else "annotation only"
                        state.append(field(name.id, value[:450], "Module-level state or configuration; extracted from source."))
    return tree, symbols, state


def module_name(path):
    path = path.removeprefix("src/")
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def imports(tree, path, modules):
    """Resolve local imports, including deferred imports inside functions, with their line numbers."""
    package = module_name(path).split(".")
    if not path.endswith("__init__.py"):
        package.pop()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Import):
            names = [n.name for n in node.names]
        else:
            prefix = package[:len(package) - node.level + 1] if node.level else []
            base = ".".join(prefix + ([node.module] if node.module else []))
            names = [base] + [f"{base}.{n.name}" if base else n.name for n in node.names]
        targets = set()
        for name in names:
            if name in modules:
                targets.add(modules[name])
            elif name.split(".")[0] in DEPENDENCIES:
                targets.add("lib:" + name.split(".")[0])
            elif name and name.split(".")[0] not in {"tacto_negotiator", "tests"}:
                targets.add("system:python")
        for target in sorted(targets):
            if target != path:
                yield path, target, "imports", f"AST line {node.lineno}"


def generate(root=ROOT):
    """Explain active project files; keep guides and reports outside the source dependency graph."""
    paths = inventory(root)
    names = {p.relative_to(root).as_posix() for p in paths}
    guides = {}
    for name in GUIDES:
        file = root / "docs" / name
        if file.is_symlink():
            raise ValueError(f"Guide must be a regular local file: {name}")
        guides["docs/" + name] = {"id": "docs/" + name, "content": file.read_text(encoding="utf-8")}
    reports = {}
    for file in sorted((root / "docs/results").glob("*.json")):
        if not file.is_symlink() and allowed(file.relative_to(root)):
            raw = file.read_bytes()
            reports[file.relative_to(root).as_posix()] = {**report_summary(json.loads(raw)), "sha256": hashlib.sha256(raw).hexdigest()}
    schema = json.loads((root / DATA / "datapackage.json").read_text())
    resources = {r["name"]: r for r in schema["resources"]}
    rows = {}
    for name in resources:
        with (root / DATA / "data" / f"{name}.csv").open(newline="", encoding="utf-8") as stream:
            rows[name] = list(csv.DictReader(stream))
    documents = {DATA + row["file"]: (table, row) for table in ("documents", "contracts") for row in rows[table]}
    modules = {module_name(p): p for p in names if p.endswith(".py")}
    modules.update({Path(p).stem: p for p in names if p.startswith("tests/") and p.endswith(".py")})
    nodes, edges = [], []

    def link(source, target, relation, evidence):
        if source != target:
            edges.append({"source": source, "target": target, "relation": relation, "evidence": evidence})

    for path in sorted(names):
        file = root / path
        node = {"id": path, "label": file.name, "kind": "file", "status": "existing", "fields": [], "symbols": []}
        if path in FILES:
            group, subgroup, role, why = FILES[path]
        elif path in documents:
            table, row = documents[path]
            if table == "documents":
                group, subgroup = "data", "Correspondence / " + row["kind"]
                role = f"{row['subject']} — {row['kind']}, {row['date']}, {row['supplier_id']}."
                why = "Supplier context or a negotiation trigger. Read the actual text before choosing an argument; a note is context, not automatically a checked numerical claim."
            else:
                group, subgroup = "data", "Contract texts"
                role = f"Framework {row['id']} for {row['supplier_id']}, valid {row['valid_from']} to {row['valid_to']}."
                why = f"Full agreement behind the table row: {row['payment_days']} days net, {row['incoterm']}, {row['notice_days']} days notice. Read clause text before arguing contractual rights."
            node["fields"] = [field(k, v, "Record from " + table + ".csv") for k, v in row.items() if v]
            link(DATA + f"data/{table}.csv", path, "file column → document text", "CSV record " + row["id"])
            link(path, DATA + "data/suppliers.csv", "context for " + row["supplier_id"], "CSV metadata")
            if row.get("part_ids"):
                link(path, DATA + "data/parts.csv", "context for " + row["part_ids"], "CSV metadata")
            if table == "contracts":
                link(path, DATA + "data/contract_clauses.csv", "clauses of " + row["id"], "CSV metadata")
        elif path.startswith(DATA + "data/") and file.suffix == ".csv":
            resource = resources[file.stem]
            group, (subgroup, why), role = "data", TABLES[file.stem], resource["description"]
        elif path.startswith(CODE + "sim_cases/"):
            case = json.loads(file.read_text())
            group, subgroup = "local", "Demo cases"
            role = f"{case['case_id']}: {case['title']} ({case['type']})."
            why = f"Exercises {', '.join(case['negotiable'])} with {len(case['suppliers'])} supplier thread(s). Public scripted demo, not a hidden or hosted practice case."
            link(CODE + "sim.py", path, "loads demo case", "load_cases()")
            link(path, DATA + "data/suppliers.csv", "suppliers: " + ", ".join(case["suppliers"]), "case JSON")
            link(path, DATA + "data/parts.csv", "parts: " + ", ".join(case["parts"]), "case JSON")
        else:
            raise ValueError(f"Add a reviewed explanation to docs/catalog.py for {path}")
        node.update(group=group, subgroup=subgroup, role=role, why=why)
        node["fields"].extend(field(name, description) for name, description in INTERNALS.get(path, []))
        raw = file.read_bytes()
        node.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        if file.suffix in TEXT_TYPES or file.name in {"LICENSE", "LICENSE-data", "Dockerfile", ".gitignore", ".dockerignore", ".env.example"}:
            text = raw.decode("utf-8")
            node["content"] = text
            if file.suffix == ".py":
                tree, node["symbols"], state = python_details(text, path)
                node["fields"].extend(state)
                for edge in imports(tree, path, modules):
                    link(*edge)
            elif file.suffix == ".md":
                node["headings"] = re.findall(r"^#{1,4} (.+)$", text, re.MULTILINE)
                for target in re.findall(r"\]\(([^)\s]+)(?:\s+[^)]*)?\)", text):
                    if "://" in target or target.startswith("#"):
                        continue
                    resolved = (file.parent / target.split("#")[0]).resolve()
                    if resolved.is_relative_to(root):
                        relative = resolved.relative_to(root).as_posix()
                        if relative in names:
                            link(path, relative, "references", "Markdown link")
                if path in documents:
                    prefixes = {"ctr": "contracts", "sc": "scorecards", "part": "parts", "po": "po_lines", "idx": "index_values", "quote": "quotes", "rfq": "rfqs"}
                    for prefix in set(re.findall(r"\b(ctr|sc|part|po|idx|quote|rfq):", text)):
                        link(path, DATA + f"data/{prefixes[prefix]}.csv", "cites " + prefix + " identifiers", "document text")
            elif file.suffix in {".js", ".mjs"}:
                node["symbols"] = [{"name": m.group(1), "signature": m.group(1) + "(" + m.group(2) + ")", "kind": "function", "line": text[:m.start()].count("\n") + 1, "description": SYMBOLS.get(m.group(1), "Operation: " + re.sub(r"([A-Z])", r" \1", m.group(1)).lower() + ". See source for the exact behavior.")} for m in re.finditer(r"^(?:async )?function (\w+)\(([^)]*)\)", text, re.MULTILINE)]
            if path == "pyproject.toml":
                project = tomllib.loads(text)
                node["fields"] = [field(k, v) for k, v in project.items()]
                for dep in DEPENDENCIES:
                    link(path, "lib:" + dep, "declares dependency / extra / build tool", "pyproject.toml")
        elif file.suffix == ".csv":
            resource, data = resources[file.stem], rows[file.stem]
            node["rows"] = len(data)
            node["fields"] = [field(f["name"], f["type"], ("Primary key. " if f["name"] in resource["schema"].get("primaryKey", []) else "") + FIELD_NOTES.get(f["name"], "Column in " + file.stem)) for f in resource["schema"]["fields"]]
            node["sample"] = data[:3]
            link(DATA + "world.sqlite", path, "materialized table mirror", "dataset README")
            link(DATA + "datapackage.json", path, "declares schema", "resource path")
            for fk in resource["schema"].get("foreignKeys", []):
                target = DATA + "data/" + fk["reference"]["resource"] + ".csv"
                relation = ", ".join(fk["fields"]) + " → " + ", ".join(fk["reference"]["fields"])
                link(path, target, relation, "declared foreign key")
        elif file.suffix == ".json":
            data = json.loads(raw)
            if file.name == "facts.json":
                buckets = {}
                for key, value in data.items():
                    bucket = buckets.setdefault(key.split(":")[0], {"count": 0, "example": {key: value}})
                    bucket["count"] += 1
                node["fields"] = [field(k + ":", v["count"], "Citable IDs with this prefix") for k, v in buckets.items()]
                node["sample"] = {k: v["example"] for k, v in buckets.items()}
                node["rows"] = len(data)
                owners = {"po": "po_lines", "idx": "index_values", "qn": "quality_notifications", "fc": "forecasts", "sc": "scorecards", "ctr": "contract_clauses", "doc": "documents", "quote": "quotes", "rfq": "rfqs", "news": "news", "cost": "should_cost"}
                for prefix, table in owners.items():
                    link(path, DATA + f"data/{table}.csv", "checked values for " + prefix + ":", "fact prefix")
                link(path, DATA + "data/contracts.csv", "checked contract notice values", "fact prefix ctr:")
            else:
                node["fields"] = [field(k, v) for k, v in data.items() if k != "resources"]
                node["content"] = raw.decode()
        elif file.suffix == ".sqlite":
            with closing(sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                db.execute("PRAGMA query_only = ON")
                for table in resources:
                    columns = [dict(zip(("cid", "name", "type", "notnull", "default", "pk"), row, strict=True)) for row in db.execute(f'PRAGMA table_info("{table}")')]
                    count = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                    node["fields"].append(field(table, {"rows": count, "columns": columns}, "SQLite table; CSV is the master copy"))
        elif file.suffix == ".lock":
            lock = tomllib.loads(raw.decode())
            node["fields"] = [field("requires-python", lock.get("requires-python")), field("packages", [{"name": p["name"], "version": p.get("version", "local")} for p in lock["package"]])]
        elif file.suffix == ".xlsx":
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            with zipfile.ZipFile(file) as archive:
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                node["fields"] = [field("sheets", [s.attrib["name"] for s in workbook.findall("s:sheets/s:sheet", ns)])]
                node["sample"] = {n: ET.tostring(ET.fromstring(archive.read(n)), encoding="unicode") for n in archive.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")}
        elif file.suffix == ".pdf":
            node["fields"] = [field("format", "PDF"), field("sections", ["Challenge and data", "Supplier roles", "Cost and score", "Practice and final", "Pitch", "Cost component backup"] if path.startswith("materials/") else ["Offered terms", "Our standard terms", "Terms fixed by your enquiry"])]
        nodes.append(node)

    for id_, label, group, subgroup, role in ENTITIES:
        nodes.append({"id": id_, "label": label, "group": group, "subgroup": subgroup, "role": role, "why": "Explicit boundary outside the active project files.", "kind": "system", "status": "external", "fields": [], "symbols": []})
    for name, role in DEPENDENCIES.items():
        nodes.append({"id": "lib:" + name, "label": name, "group": "systems", "subgroup": "Libraries", "role": role, "why": "Declared in pyproject.toml; imported edges show consumers. Versions are recorded in uv.lock.", "kind": "library", "status": "external", "fields": [], "symbols": []})
    for edge in LINKS:
        link(*edge)
    # Multiple imports of one module are one logical edge, with every source location preserved.
    unique = {}
    for edge in edges:
        key = edge["source"], edge["target"], edge["relation"]
        if key in unique:
            if edge["evidence"] not in unique[key]["evidence"].split("; "):
                unique[key]["evidence"] += "; " + edge["evidence"]
        else:
            unique[key] = edge
    edges = sorted(unique.values(), key=lambda e: (e["source"], e["target"], e["relation"]))
    ids = {n["id"] for n in nodes}
    assert len(ids) == len(nodes), "Duplicate node ID"
    assert all(e["source"] in ids and e["target"] in ids for e in edges), "Dangling dependency"
    source_ids = {path for path in names if path.startswith(CODE)
                  and (path.endswith(".py") or path.startswith(CODE + "web_ui/"))}
    external_ids = {node["id"] for node in nodes if node["kind"] in {"system", "library"}}
    map_ids = set(source_ids)
    while True:
        neighbors = {edge["target"] for edge in edges if edge["source"] in map_ids and edge["target"] in external_ids}
        if neighbors.issubset(map_ids):
            break
        map_ids.update(neighbors)
    map_edges = sum(edge["source"] in map_ids and edge["target"] in map_ids for edge in edges)
    return {"groups": GROUPS, "nodes": nodes, "edges": edges, "map_ids": sorted(map_ids), "guides": guides,
            "reports": reports, "stats": {"files": len(names), "modules": len(source_ids), "map_edges": map_edges,
            "tables": len(resources), "rows": sum(map(len, rows.values())),
            "facts": next(n["rows"] for n in nodes if n["id"] == DATA + "facts.json"), "edges": len(edges)},
            "scope": "The overview maps active source modules and their runtime systems/libraries. The explorer also explains tests, configuration and public data; select a file for its direct relationships. Guides are separate. Archived materials, credentials, environments, caches and symlinks are not inventoried."}


def serialize(data):
    """A classic script works from file://; escaping '<' also prevents closing a script element."""
    return "// Generated by docs/build.py. Do not edit.\nwindow.ATLAS = " + json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + ";\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if data.js no longer matches the source")
    args = parser.parse_args()
    output, content = ROOT / OUTPUT, serialize(generate())
    if args.check:
        if not output.exists() or output.read_text() != content:
            raise SystemExit("Stale atlas: run python3 docs/build.py")
        print("Atlas matches the current public source files.")
    else:
        output.write_text(content, encoding="utf-8")
        print(f"Wrote {output.relative_to(ROOT)} ({len(content.encode()):,} bytes)")


if __name__ == "__main__":
    main()
