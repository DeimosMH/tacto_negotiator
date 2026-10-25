"""Read-only access to the data package: SQL, lookup by citable ID, documents, practice cases -
plus the Sunday release (new news, letters and a memo) once you have downloaded it."""

import csv
import json
import math
import os
import sqlite3
import threading
from functools import cached_property
from pathlib import Path

MAX_ROWS = 200
RELEASE = "release-sunday"


class DataPackage:
    def __init__(self, root, release=None):
        """`root`: the unpacked package. `release`: the unpacked Sunday release (default: a
        `release-sunday` folder inside or next to the package, or ARENA_RELEASE), if present."""
        self.root = Path(root).expanduser().resolve()
        # facts.json holds every citable fact with the fields the arena checks claims against.
        own = self.root / "facts.json"
        facts = [own] if own.is_file() else [p for p in sorted(self.root.rglob("facts.json")) if RELEASE not in p.parts]
        self.facts = json.loads(facts[0].read_text(encoding="utf-8")) if facts else {}
        release = release or os.environ.get("ARENA_RELEASE")
        candidates = [Path(release).expanduser()] if release else [self.root / RELEASE, self.root.parent / RELEASE]
        self.release = next((p.resolve() for p in candidates if (p / "facts.json").is_file()), None)
        if self.release:  # new IDs only: nothing in the package changes
            self.facts |= json.loads((self.release / "facts.json").read_text(encoding="utf-8"))
        dbs = sorted(self.root.rglob("*.sqlite")) + sorted(self.root.rglob("*.db"))
        if not dbs and not facts:
            raise FileNotFoundError(f"no facts.json or .sqlite under {self.root}")
        self.db = None
        # Parallel cases share one package: one connection used from several threads interleaves
        # cursors (a KeyError in a 10-worker replica run), so every database access holds this lock.
        self._lock = threading.RLock()
        if dbs:
            self.db = sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only = ON")

    def tables(self):
        """{table: {"columns": [...], "rows": n}}"""
        if self.db is None:
            return {}
        with self._lock:
            names = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
            return {
                t: {
                    "columns": [f"{c[1]} {c[2]}".strip() for c in self.db.execute(f'PRAGMA table_info("{t}")')],
                    "rows": self.db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0],
                }
                for t in names
            }

    def query(self, sql, params=(), limit=MAX_ROWS):
        if self.db is None:
            raise ValueError("this package has no SQLite file")
        if sql.lstrip().split(None, 1)[0].lower() not in {"select", "with"}:
            raise ValueError("read-only: SELECT or WITH statements only")
        with self._lock:
            cur = self.db.execute(sql, params)
            return [dict(r) for r in cur.fetchmany(limit)]

    @cached_property
    def _ids(self):
        # A table's citable ID sits in its `id` column, else in its first column.
        index = {}
        with self._lock:
            for t, meta in self.tables().items():
                cols = [c.split()[0] for c in meta["columns"]]
                key = "id" if "id" in cols else cols[0]
                for row in self.db.execute(f'SELECT * FROM "{t}"'):
                    value = row[key]
                    if isinstance(value, str) and ":" in value:
                        index[value] = {"table": t, **dict(row)}
        for path in sorted(self.release.glob("data/*.csv")) if self.release else []:  # not in the SQLite
            with path.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    index[row["id"]] = {"table": path.stem, **row, "root": str(self.release)}
        return index

    def lookup(self, ref):
        """The fact behind a citable ID (from facts.json, else its table row), or None."""
        return self.facts.get(ref) or self._ids.get(ref)

    def ids(self, prefix):
        """All citable IDs starting with `prefix`, sorted, e.g. ids("idx:GP19-245113@")."""
        return sorted(i for i in self.facts.keys() | self._ids.keys() if i.startswith(prefix))

    def document(self, ref):
        """A document's text by ID (row with a path or body column) or by path in the package."""
        row, base = self._ids.get(ref) or self.facts.get(ref), self.root
        if row:
            for col in ("body", "text", "content"):
                if row.get(col):
                    return row[col]
            base = Path(row.get("root") or self.root)
            ref = next((row[c] for c in ("path", "file") if row.get(c)), None)
            if ref is None:
                return json.dumps(row, ensure_ascii=False)
        path = (base / ref).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise FileNotFoundError(ref)
        return path.read_text(encoding="utf-8")

    def negotiation_facts(self, case):
        """A bounded dossier from supplied evidence, including the Sunday overlay."""
        suppliers, parts = set(case["suppliers"]), set(case["parts"])
        selected = {}
        quotes = set(case.get("refs", [])) | {(case["buyer"].get("alternative") or {}).get("ref")}
        for prefix, limit in (("doc:", 24), ("news:", 8), ("cost:", 8), ("quote:", 12), ("fc:", 6)):
            rows = [(ref, fact) for ref, fact in self.facts.items() if ref.startswith(prefix) and (
                fact.get("supplier") in suppliers if prefix in ("doc:", "news:") else fact.get("part") in parts)
                and (prefix != "quote:" or ref in quotes)]
            rows.sort(key=lambda item: (item[1].get("date", ""), item[0]), reverse=True)
            for ref, fact in rows[:limit]:
                selected[ref] = dict(fact)
                if prefix in ("doc:", "news:"):
                    selected[ref]["text"] = self.document(ref)[:1800]
                elif prefix in ("cost:", "quote:"):
                    # The estimate's supplier and uncertainty matter; it is not a private reservation price.
                    selected[ref].update(self._ids.get(ref, {}))
        series = set(case["buyer"].get("index_paths", {}))
        if self.db and "cost_drivers" in self.tables():
            for part in sorted(parts):
                drivers = self.query("SELECT * FROM cost_drivers WHERE part_id = ?", (part,))
                if drivers:
                    selected[part] = {"estimated_cost_drivers": drivers}
                    series.update(row["series_id"] for row in drivers)
        for clause in case.get("term_options", {}).get("index_clause", []):
            if clause:
                series.add(clause["series"])
        for name in sorted(series):
            refs = self.ids(f"idx:{name}@")
            if not refs:
                continue
            newest = refs[-1]
            month = self.lookup(newest)["month"]
            previous = f"idx:{name}@{int(month[:4]) - 1}{month[4:]}"
            for ref in (previous, newest):
                if fact := self.lookup(ref):
                    selected[ref] = fact
            if previous in selected and selected[previous]["value"] > 0:
                change = (selected[newest]["value"] / selected[previous]["value"] - 1) * 100
                selected[newest] = {**selected[newest], "comparison_ref": previous,
                                    "computed_change_pct": round(change, 2)}
                if change > 0:  # keep the level, drop the year-ago row: the rise must not become a claim
                    selected.pop(previous)
                    selected[newest] = {k: v for k, v in selected[newest].items()
                                        if k not in ("comparison_ref", "computed_change_pct")}
                    selected[newest]["note"] = "rose over twelve months: supports the supplier's case, do not cite"
            if fall := self._steepest_fall(refs):
                start, end, change = fall
                selected[start] = self.lookup(start)
                selected[end] = {**self.lookup(end), "comparison_ref": start, "computed_change_pct": round(change, 2),
                                 "note": "steepest recent fall: cite both IDs and this change"}
        return selected

    def supplier_estimates(self, case):
        """{supplier: estimate} for the supplier-health term of relationship goals, from should-cost.

        Fit to 10 hosted relationship deals (max error EUR 349; EUR 144,100 predicted for a charged
        EUR 143,186): full cost 0.98 x the should-cost estimate, 7 % cost of money, standard payment
        and Incoterm from the supplier record. Suppliers without data get no estimate."""
        if case["buyer"].get("category_goal") not in ("stabilise", "secure_supply") or not self.db:
            return {}
        out = {}
        for sid in case["suppliers"]:
            try:
                record = self.query("SELECT payment_days, incoterm FROM suppliers WHERE id = ?", (sid,))
                costs = [row for row in self.query("SELECT * FROM should_cost WHERE supplier_id = ?", (sid,))
                         if row["part_id"] in case["parts"] and row["unit_cost"]]
            except Exception:  # an older or partial package: decide without the estimate
                continue
            if record and costs:
                # Case prices are per price unit (motor cable: EUR per 100 m), like should-cost rows; only a
                # per-piece case price needs the division. Take the scale nearest today's price (P14: 304.49
                # per 100 m against 316.35, not 3.04).
                raw, per = costs[0]["unit_cost"], costs[0].get("price_unit") or 1
                current = case["buyer"].get("current_unit_price") or raw
                unit = min((raw, raw / per), key=lambda value: abs(math.log(value / current)))
                standard = {k: v for k, v in record[0].items() if v is not None}
                out[sid] = {"standard": standard, "interest_rate": 0.07, "full_cost": round(0.98 * unit, 4)}
        return out

    def index_falls(self, case):
        """[(weight, series, start_ref, end_ref, change_pct)] for the case's cost-driver and index-clause
        series with a steepest recent fall (`_steepest_fall`), heaviest-weighted fall first. The weight is
        the part's cost-driver weight (an index clause's material share otherwise)."""
        weights = {}
        if self.db and "cost_drivers" in self.tables():
            for part in sorted(case["parts"]):
                for row in self.query("SELECT series_id, weight FROM cost_drivers WHERE part_id = ?", (part,)):
                    weights[row["series_id"]] = max(weights.get(row["series_id"], 0.0), row.get("weight") or 0.0)
        for clause in case.get("term_options", {}).get("index_clause") or []:
            if clause and clause.get("series"):
                weights.setdefault(clause["series"], clause.get("material_share") or 0.0)
        out = []
        for name, weight in weights.items():
            refs = self.ids(f"idx:{name}@")
            if refs and (fall := self._steepest_fall(refs)):
                out.append((weight, name, *fall))
        return sorted(out, key=lambda row: (row[0] * row[4], row[4], row[1]))

    def _steepest_fall(self, refs):
        """The steepest index fall of 3-12 months ending in the series' last 6 months, at least -1 %.

        Earlier teams on this arena saw rising indices confirm suppliers' cases; falls move them."""
        rows = [(ref, self.lookup(ref)) for ref in refs]
        rows = [(ref, fact, int(fact["month"][:4]) * 12 + int(fact["month"][5:7])) for ref, fact in rows
                if fact and fact.get("month") and isinstance(fact.get("value"), (int, float))]
        if not rows:
            return None
        last = max(month for _, _, month in rows)
        best = None
        for end_ref, end, end_month in rows:
            if last - end_month > 5:
                continue
            for start_ref, start, start_month in rows:
                if 3 <= end_month - start_month <= 12 and start["value"] > 0:
                    change = (end["value"] / start["value"] - 1) * 100
                    if change <= -1 and (best is None or change < best[2]):
                        best = (start_ref, end_ref, change)
        return best

    @cached_property
    def cases(self):
        """Practice (and demo) case files in the package: {case_id: case}."""
        out = {}
        for p in sorted(self.root.rglob("cases/**/*.json")):
            case = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(case, dict) and "case_id" in case:
                out[case["case_id"]] = case
        return out
