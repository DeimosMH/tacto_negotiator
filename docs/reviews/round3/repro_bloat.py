"""Round 3 bloat review: offline evidence per finding (X1..X17) and status of earlier bloat findings.

No credentials, no .env, no network, no model calls. Reads source text, the AST, `git ls-files` and
a few module constants. Run from an EMPTY directory (a stray /tmp/h2.py on this machine shadows the
httpx dependency `h2` when Python starts in /tmp):

    mkdir -p /tmp/blrun && cd /tmp/blrun && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
      /home/deimos/Documents/git_prv/tacto_negotiator/.venv/bin/python \
      /home/deimos/Documents/git_prv/tacto_negotiator/docs/reviews/round3/repro_bloat.py
"""

import ast
import collections
import io
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "tacto_negotiator"
sys.path.insert(0, str(SRC.parent))


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def tree(rel):
    return ast.parse(text(rel))


def func(rel, name, cls=None):
    """The ast node of a function (optionally inside a class)."""
    for node in ast.walk(tree(rel)):
        if cls and isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
        if not cls and isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise LookupError(name)


def source_of(rel, node):
    return "\n".join(text(rel).splitlines()[node.lineno - 1:node.end_lineno])


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def head(title):
    print(f"\n=== {title}")


# ---------------------------------------------------------------------------------------------------
def x0_dead_code_scan():
    head("X0 baseline: definitions in src/ with no reference anywhere else (token scan, src+tests+tools+docs)")
    files = list(SRC.rglob("*.py"))
    others = [p for d in ("tests", "tools", "docs") for p in (ROOT / d).rglob("*.py") if "reviews" not in p.parts]
    counts = collections.Counter()
    for p in files + others:
        for tok in tokenize.generate_tokens(io.StringIO(p.read_text(encoding="utf-8")).readline):
            if tok.type == tokenize.NAME:
                counts[tok.string] += 1
            elif tok.type == tokenize.STRING and tok.string.strip("\"'").isidentifier():
                counts[tok.string.strip("\"'")] += 1
    unreferenced = []
    for p in files:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith("__") \
                    and not node.decorator_list and counts[node.name] <= 1:
                unreferenced.append(f"{p.relative_to(ROOT)}:{node.lineno} {node.name}")
    print("unreferenced undecorated defs:", unreferenced or "none")
    print("(only http.server hooks do_GET/log_message are expected here; vulture 2.16 at 60 % confidence over"
          " src+tests+tools also reports only framework routes)")


def x1_backfill_titles():
    head("X1 deals.backfill: `titles` is written and never read")
    node = func("src/tacto_negotiator/deals.py", "backfill")
    stores = [n.lineno for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == "titles"
              and isinstance(n.ctx, ast.Store)]
    sub_stores = [n.lineno for n in ast.walk(node) if isinstance(n, ast.Subscript)
                  and isinstance(n.value, ast.Name) and n.value.id == "titles" and isinstance(n.ctx, ast.Store)]
    loads = [n.lineno for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == "titles"
             and isinstance(n.ctx, ast.Load) and not any(
                 isinstance(s, ast.Subscript) and s.value is n and isinstance(s.ctx, ast.Store)
                 for s in ast.walk(node))]
    print(f"assigned at line {stores}, item-assigned at {sub_stores}, read at {loads or 'nowhere'}")
    assert stores and sub_stores and not loads


def x2_unused_params():
    head("X2 parameters accepted and ignored")
    for rel, cls, name, param in (("src/tacto_negotiator/agents/completion.py", "AnthropicCompletionClient",
                                   "__init__", "base_url"),
                                  ("src/tacto_negotiator/agents/routed.py", "RoutedAgent", "_new_fact", "case")):
        node = func(rel, name, cls)
        names = [a.arg for a in node.args.args + node.args.kwonlyargs]
        used = any(isinstance(n, ast.Name) and n.id == param for n in ast.walk(node))
        print(f"{rel}:{node.lineno} {cls}.{name}({param}) declared={param in names} used={used}")
        assert param in names and not used
    callers = re.findall(r"AnthropicCompletionClient\([^)]*base_url", text("src/tacto_negotiator/agents/completion.py")
                         + text("tests/test_completion.py"))
    print("callers passing base_url to the Anthropic client:", len(callers))


def x3_limit_counter():
    head("X3 RoutedAgent._limit_counter re-implements _limit_sheet")
    rel = "src/tacto_negotiator/agents/routed.py"
    a = source_of(rel, func(rel, "_limit_sheet", "RoutedAgent")).splitlines()
    b = source_of(rel, func(rel, "_limit_counter", "RoutedAgent")).splitlines()
    same = [line.strip() for line in b if line.strip() in {x.strip() for x in a} and len(line.strip()) > 12]
    print(f"_limit_sheet {len(a)} lines, _limit_counter {len(b)} lines, identical statements: {len(same)}")
    for line in same:
        print("   ", line)
    assert len(same) >= 3


def x4_prompt_duplicates():
    head("X4 numbers and strings in the model prompt that duplicate code constants")
    from tacto_negotiator.agents import facts, levers, llm
    checks = [("At most three claims", facts.ROUND_ONE_CLAIMS == 3, "facts.ROUND_ONE_CLAIMS"),
              (">= 3 % in twelve months", levers.PATH_MIN_RISE == 0.03, "levers.PATH_MIN_RISE"),
              (levers.FLOOR_QUESTION, True, "levers.FLOOR_QUESTION"),
              ("120 words", "120" not in text("src/tacto_negotiator/agents/routed.py"), "(enforced nowhere)")]
    for literal, matches, constant in checks:
        print(f"SYSTEM contains {literal!r}: {literal in llm.SYSTEM.replace(chr(10) + '  ', ' ')}; mirrors {constant}"
              f" (value agrees: {matches})")
    print(f"SYSTEM length {len(llm.SYSTEM)}; rendering FLOOR_QUESTION and PATH_MIN_RISE with an f-string was tried"
          " in a /tmp copy: byte-identical SYSTEM, 349 tests pass")
    tree_text = text("src/tacto_negotiator/deals.py")
    for prose, code in (("within 0.2 %", "small_gap = current_cost <= previous_cost * 1.002"),
                        ("20 % of it (40 % routine)", "GAP_STEP, HOLD_STEP = 0.20 / GAP_STEP_BY_GOAL routine 0.40"),
                        ("Claude Opus, high effort", "completion.py default LLM_EFFORT 'low'")):
        print(f"deals.TREE prose {prose!r}: {prose in tree_text}; code: {code}")


def x5_tolerances():
    head("X5 unnamed acceptance/filter tolerances (A3 [now] item, still open)")
    routed = text("src/tacto_negotiator/agents/routed.py")
    for literal in ("1.005", "1.003", "1.002", "0.999", "0.998", "0.997", "0.9 *", "* 1.5"):
        lines = [i for i, line in enumerate(routed.splitlines(), 1) if literal in line and not line.strip().startswith("#")]
        print(f"routed.py {literal:>6}: {len(lines)} x at lines {lines}")
    floats = [n for n in ast.walk(ast.parse(routed)) if isinstance(n, ast.Constant) and type(n.value) is float]
    print(f"float literals in routed.py: {len(floats)} (round 2 counted 41)")


def x6_anchor_sites():
    head("X6 anchored opening price computed in five places (A3 'opening anchor', still open)")
    for rel in ("src/tacto_negotiator/play.py", "src/tacto_negotiator/agents/routed.py"):
        for i, line in enumerate(text(rel).splitlines(), 1):
            if "ANCHOR_BY_GOAL" in line and "import" not in line and not line.startswith("ANCHOR_BY_GOAL") \
                    and line.strip() != "ANCHOR_BY_GOAL,":
                print(f"  {rel}:{i}: {line.strip()[:110]}")
    print("default 0.15 for an unknown goal repeated:", sum(t.count("ANCHOR_BY_GOAL.get(") for t in (
        text("src/tacto_negotiator/play.py"), text("src/tacto_negotiator/agents/routed.py"))), "times")


def x7_idioms():
    head("X7 repeated idioms that want a helper")
    resolved = '{**std, **case.get("fixed_terms", {}), **terms}'
    total = 0
    for p in sorted(SRC.rglob("*.py")):
        n = p.read_text(encoding="utf-8").count(resolved)
        if n:
            print(f"  resolved terms {resolved} in {p.relative_to(SRC)}: {n}")
            total += n
    print("  total:", total)
    pat = re.compile(r'ours = min\(\(?(total_cost|decision_cost)\(case, s\.get\("terms", s\)')
    for rel in ("src/tacto_negotiator/play.py", "src/tacto_negotiator/agents/routed.py"):
        for i, line in enumerate(text(rel).splitlines(), 1):
            if m := pat.search(line):
                print(f"  'our last ask' {rel}:{i} via {m.group(1)}")
    sent = sum(text(r).count('e.get("from") == "buyer" and e.get("term_sheets")') for r in (
        "src/tacto_negotiator/play.py", "src/tacto_negotiator/agents/routed.py", "src/tacto_negotiator/agents/levers.py"))
    print("  'buyer messages with sheets' filter:", sent, "times")


def x8_legacy_agent():
    head("X8 legacy LLMAgent: a second agent, guard and provider client kept for `--agent llm` (A5, still open)")
    rel = "src/tacto_negotiator/agents/llm.py"
    for name in ("decide", "guard"):
        node = func(rel, name, "LLMAgent")
        print(f"  LLMAgent.{name}: {node.end_lineno - node.lineno + 1} lines")
    node = func(rel, "llm_from_env")
    print(f"  llm_from_env: {node.end_lineno - node.lineno + 1} lines (second Anthropic + OpenAI client)")
    routed = text("src/tacto_negotiator/agents/routed.py")
    used = sorted(set(re.findall(r"self\.context\.(\w+)", routed)) | set(re.findall(r'getattr\(self\.context, "(\w+)"', routed)))
    print("  RoutedAgent uses LLMAgent only for:", used, "(never decide/guard)")
    print("  cli choices:", re.findall(r'choices=\[([^\]]+)\]', text("src/tacto_negotiator/cli.py")))


def x9_openai_experiment():
    head("X9 OpenAI model experiment path (credits exhausted; the final runs Anthropic)")
    compete = text("src/tacto_negotiator/compete.py")
    print("  compete.MODELS:", re.search(r"MODELS = (\{.*\})", compete).group(1))
    print("  compete.STRATEGIES:", re.search(r"STRATEGIES = (\(.*\))", compete).group(1))
    for rel in ("src/tacto_negotiator/benchmark.py", "tests/test_benchmark.py"):
        print(f"  {rel}: {len(text(rel).splitlines())} lines")
    completion = text("src/tacto_negotiator/agents/completion.py")
    print("  routed_completions_from_env default provider:",
          re.search(r'os\.environ\.get\("LLM_PROVIDER", "(\w+)"\)', completion).group(1),
          "| default OpenAI model:", re.search(r"= CompletionClient, \"([\w.-]+)\"", completion).group(1))


def x10_runner_copy():
    head("X10 competition._match re-implements play._play (A2, still open) and has drifted")
    play = func("src/tacto_negotiator/play.py", "_play")
    match = func("src/tacto_negotiator/competition.py", "_match")
    print(f"  play._play {play.end_lineno - play.lineno + 1} lines, competition._match "
          f"{match.end_lineno - match.lineno + 1} lines")
    play_src = source_of("src/tacto_negotiator/play.py", play)
    match_src = source_of("src/tacto_negotiator/competition.py", match)
    for marker, what in (("rejected_accepts", "422 stale-accept re-read (d842b8e)"),
                         ('if kind == "accept"', "accept judged on its own thread clock (B4)"),
                         ("reread_skipped_documents", "document re-read before deciding")):
        print(f"  {what}: in play={marker in play_src} in _match={marker in match_src}")
    print("  default decision budget: RoutedAgent 40 s;", "competition/compete 25 s:",
          "decision_seconds=25" in text("src/tacto_negotiator/competition.py"))


def x11_validate():
    head("X11 RoutedAgent.validate: validator + policy + writer (A4, still open, grown)")
    rel = "src/tacto_negotiator/agents/routed.py"
    node = func(rel, "validate", "RoutedAgent")
    branches = sum(isinstance(n, (ast.If, ast.For, ast.While, ast.IfExp, ast.BoolOp, ast.comprehension))
                   for n in ast.walk(node))
    calls = collections.Counter(n.func.attr for n in ast.walk(node) if isinstance(n, ast.Call)
                                and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                                and n.func.value.id == "self")
    print(f"  {node.end_lineno - node.lineno + 1} lines (116 at 5dea00a), {branches} branch nodes")
    print("  self.* rewrite/limit helpers it calls:", dict(calls))
    print("  calls to self.validate across routed.py:", text(rel).count("self.validate("))


def x12_deal_journal():
    head("X12 deal journal stores every record twice and writes into the repo during play")
    files = git("ls-files", "docs/deals").split()
    js = [f for f in files if f.endswith(".js")]
    js_json = [f for f in files if f.endswith(".json")]
    size = lambda fs: sum((ROOT / f).stat().st_size for f in fs)  # noqa: E731
    print(f"  tracked: {len(js_json)} .json ({size(js_json):,} B) + {len(js)} .js ({size(js):,} B)")
    untracked = [line for line in git("status", "--porcelain", "docs/deals").splitlines() if line.startswith("??")]
    print(f"  untracked records written by today's runs: {len(untracked)}")
    ui = text("docs/deals-ui.js")
    print("  UI loads only .js:", "deals/${encodeURIComponent(id)}.js" in ui, "| deals.write_index globs *.json:",
          'folder.glob("*.json")' in text("src/tacto_negotiator/deals.py"))
    print("  backfilled records:", sum('"source": "hosted (backfilled)"' in (ROOT / f).read_text(encoding="utf-8")
                                       for f in js_json))


def x13_config_copies():
    head("X13 the final's model settings live in docs/IaC, the code defaults differ (A8, still open)")
    hits = collections.Counter()
    for rel in ["README.md", *[f"docs/{p.name}" for p in (ROOT / "docs").glob("*.md")], "02-vera-iac/variables.tf"]:
        t = text(rel)
        n = len(re.findall(r"LLM_EFFORT\s*=\s*\"?high", t)) + t.count("`high` for the final")
        if n:
            hits[rel] = n
    print("  'LLM_EFFORT=high' stated in:", dict(hits))
    completion = text("src/tacto_negotiator/agents/completion.py")
    print("  code default effort:", re.search(r'LLM_EFFORT", "(\w+)"', completion).group(1),
          "| code default provider:", re.search(r'LLM_PROVIDER", "(\w+)"', completion).group(1))
    routing = text("docs/ROUTING.md")
    for stale in ("90%", "midpoint", "deterministic closure"):
        print(f"  ROUTING.md still says {stale!r}: {stale in routing}")


def x14_worktrees():
    head("X14 untracked agent worktrees inside the repo")
    base = ROOT / ".claude" / "worktrees"
    dirs = sorted(p.name for p in base.iterdir()) if base.is_dir() else []
    print(f"  .claude/worktrees: {len(dirs)} directories")
    print("  git worktree entries:", len(git("worktree", "list").splitlines()))
    print("  .claude ignored by .gitignore:", ".claude" in text(".gitignore"))
    print("  git branches named worktree-agent-*:", sum("worktree-agent-" in b for b in git("branch").splitlines()))


def x15_paths():
    head("X15 repository-root path recomputed per module; runtime code reads tests/fixtures")
    for p in sorted(SRC.rglob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "parents[2]" in line:
                print(f"  {p.relative_to(ROOT)}:{i}: {line.strip()[:100]}")


def x16_dockerfiles():
    head("X16 Dockerfile and Dockerfile.azure share their build steps")
    a, b = text("Dockerfile").splitlines(), text("Dockerfile.azure").splitlines()
    same = [line for line in a if line in b and line and not line.startswith("#")]
    print(f"  {len(same)} identical lines of {len(a)}")


def x17_results():
    head("X17 large committed result reports")
    rows = sorted(((ROOT / f).stat().st_size, f) for f in git("ls-files", "docs/results").split())
    print(f"  docs/results: {len(rows)} files, {sum(s for s, _ in rows):,} B; largest:")
    for s, f in rows[-4:]:
        print(f"    {s:>10,} {f}")


def earlier():
    head("Earlier bloat findings (round 1 docs/reviews/BLOAT.md)")
    import copy
    import json

    from tacto_negotiator.agents.llm import LLMAgent
    from tacto_negotiator.agents.routed import RoutedAgent
    from tacto_negotiator.play import closing_move
    from tacto_negotiator.sim import Sim, load_cases
    case = copy.deepcopy(load_cases()["case:S01"])
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 30}]
    opened = Sim({case["case_id"]: case}).open("local", case["case_id"])
    sid = case["suppliers"][0]
    threads = copy.deepcopy(opened["threads"])
    threads[sid][0]["attachments"] = []
    legacy = LLMAgent(lambda *_: json.dumps({"supplier_id": sid, "action": "accept"})).decide(case, threads)
    kinds = [m["message"].get("action", {}).get("type", "send") for m in
             (legacy, closing_move(case, threads), RoutedAgent(None).closing_move(case, threads))]
    print(f"  #1 S01 missing default: legacy={kinds[0]} default_closer={kinds[1]} routed_closer={kinds[2]}"
          " -> no accept anywhere: FIXED (shared acceptable_offer); the separate guard itself remains (X8)")
    sys.path.insert(0, str(ROOT / "docs"))
    import build

    class Tracked(dict):
        def __init__(self, value):
            super().__init__(value)
            self.used = set()

        def get(self, key, default=None):
            if key in self:
                self.used.add(key)
            return super().get(key, default)

    original = build.SYMBOLS
    try:
        build.SYMBOLS = tracked = Tracked(original)
        build.serialize(build.generate())
    finally:
        build.SYMBOLS = original
    print(f"  #2 catalog SYMBOLS: {len(original)} entries, {len(set(tracked) - tracked.used)} never read: FIXED")
    packages = source_of("src/tacto_negotiator/agents/routed.py",
                         func("src/tacto_negotiator/agents/routed.py", "_packages", "RoutedAgent"))
    print(f"  #3 _packages filters with must_haves_ok: {'must_haves_ok(' in packages}, calls validate: "
          f"{'self.validate(' in packages}: FIXED")


if __name__ == "__main__":
    os.environ.setdefault("TACTO_DEALS_DIR", "off")
    print("HEAD", git("rev-parse", "--short", "HEAD").strip())
    for step in (x0_dead_code_scan, x1_backfill_titles, x2_unused_params, x3_limit_counter, x4_prompt_duplicates,
                 x5_tolerances, x6_anchor_sites, x7_idioms, x8_legacy_agent, x9_openai_experiment, x10_runner_copy,
                 x11_validate, x12_deal_journal, x13_config_copies, x14_worktrees, x15_paths, x16_dockerfiles,
                 x17_results, earlier):
        step()
