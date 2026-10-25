"""Reproducible local competitions; paid OpenAI calls require explicit --live and --key-file.

Run python -m tacto_negotiator.compete --help. No arena client or dotenv loader is used.
Only aggregate metrics, route telemetry and usage accounting are saved, never model text.
"""

import argparse
import hashlib
import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from .agents.completion import CompletionClient
from .agents.naive import NaiveAgent
from .agents.routed import RoutedAgent
from .benchmark import BudgetedCompletion, BudgetLedger, preflight_models, read_llm_key
from .competition import run_competition
from .data import DataPackage

MODELS = {"sol": "gpt-6.1-sol", "astra": "gpt-6-astra", "luna": "gpt-6-luna"}
STRATEGIES = ("naive", "close", "scripts", "sol", "astra", "luna", "hybrid", "routed")


def unavailable(system, user, *, deadline):
    """Expose cases outside script coverage as fallback; no remote request is made."""
    raise RuntimeError("models disabled for this policy")


class CloseAgent(RoutedAgent):
    """Guarded immediate closing baseline, without bargaining."""

    def decide_timed(self, case, threads, *, deadline, wave_seconds_left=None):
        return self.closing_move(case, threads)


class ModelAgent(RoutedAgent):
    """Ask the named model on every non-deadline decision, retaining the common guard."""

    def _script(self, case, threads):
        return None


def make_factories(strategies, completions=None, *, decision_seconds=25, package=None):
    """Fresh policy state per match; clients, budget ledger and the read-only package may be shared."""
    completions = completions or {}
    factories = {}
    for strategy in strategies:
        if strategy not in STRATEGIES:
            raise ValueError("unknown competition strategy")
        if strategy == "naive":
            factories[strategy] = NaiveAgent
        elif strategy == "close":
            factories[strategy] = lambda: CloseAgent(unavailable)
        elif strategy == "scripts":
            factories[strategy] = lambda: RoutedAgent(unavailable, package=package,
                                                      max_decision_seconds=decision_seconds)
        elif strategy == "routed":  # the final's buyer: Claude Opus from LLM_* settings (routed_completions_from_env)
            fast, strong = completions["routed"]
            factories[strategy] = lambda f=fast, s=strong: RoutedAgent(
                f, strong=s, package=package, max_decision_seconds=decision_seconds)
        elif strategy == "hybrid":
            fast, strong = completions["sol"], completions["astra"]
            factories[strategy] = lambda f=fast, s=strong: RoutedAgent(
                f, strong=s, package=package, max_decision_seconds=decision_seconds)
        else:
            complete = completions[strategy]
            factories[strategy] = lambda c=complete: ModelAgent(c, package=package,
                                                                max_decision_seconds=decision_seconds)
    return factories


def write_report(path, report):
    """Atomically replace sanitized progress/final output, so interruption leaves accounting."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tacto-compete", description=__doc__)
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=["naive", "close", "scripts"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--scenarios", nargs="+")
    parser.add_argument("--waves", type=int, default=5)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--decision-seconds", type=float, default=25)
    parser.add_argument("--holdout", action="store_true")
    parser.add_argument("--replica", action="store_true",
                        help="the 10 hosted practice cases with a supplier calibrated on hosted replies")
    parser.add_argument("--bench", choices=["replica", "oos", "synth"],
                        help="replica: the 10 hosted cases on recorded openings; oos: the 9 never-hosted practice "
                             "cases with synthesized openings; synth: the hosted cases synthesized the same way")
    parser.add_argument("--timing", choices=["practice", "final"], default="practice",
                        help="supplier reply delays: practice 15-40 s, final 25-60 s")
    parser.add_argument("--live", action="store_true", help="Authorize paid OpenAI completions; suppliers stay local")
    parser.add_argument("--key-file", type=Path, help="Read only literal LLM_API_KEY from this file")
    parser.add_argument("--budget-usd", type=float, default=60, help="Conservative reservation ceiling for this run")
    parser.add_argument("--output", type=Path, required=True, help="Sanitized JSON report; progress saved every 15s")
    args = parser.parse_args(argv)
    if args.workers < 1 or args.waves < 1 or args.decision_seconds <= 0:
        parser.error("workers, waves and decision-seconds must be positive")
    requested = set(args.strategies) & (set(MODELS) | {"hybrid"})
    if requested and (not args.live or args.key_file is None):
        parser.error("model strategies require both --live and --key-file")
    if "routed" in args.strategies and not args.live:
        parser.error("the routed strategy calls the configured model: add --live (LLM_* from the environment)")
    if args.live and not requested and "routed" not in args.strategies:
        parser.error("--live requires a model strategy")
    logging.getLogger("arena").setLevel(logging.CRITICAL)
    ledger = BudgetLedger(args.budget_usd) if requested else None
    completions, preflight = {}, None
    if requested:
        names = (requested - {"hybrid"}) | ({"sol", "astra"} if "hybrid" in requested else set())
        key = read_llm_key(args.key_file)
        preflight = preflight_models(key, [MODELS[name] for name in sorted(names)])
        if preflight["status"] != "ok" or any(v != "available" for v in preflight["models"].values()):
            write_report(args.output, {"status": "preflight_failed", "preflight": preflight})
            return 1
        semaphore = threading.BoundedSemaphore(args.workers)
        for name in names:
            client = CompletionClient(MODELS[name], key, effort="low", service_tier="default", semaphore=semaphore)
            completions[name] = BudgetedCompletion(client, ledger)
    if "routed" in args.strategies:  # suppliers stay local; only LLM_* settings are read, never an arena key
        from .agents.completion import routed_completions_from_env

        completions["routed"] = routed_completions_from_env()
    package = None
    if args.replica or args.bench:
        package = DataPackage(Path(__file__).resolve().parents[2] / "data")
        package.lookup("")
    factories = make_factories(args.strategies, completions, decision_seconds=args.decision_seconds, package=package)
    metadata = {"started_utc": datetime.now(UTC).isoformat(), "models": {n: (c[0] if isinstance(c, tuple) else c).model
                                                                for n, c in completions.items()},
                "effort": "low", "service_tier": "default", "max_output_tokens": 4096,
                "preflight": preflight, "status": "running"}
    metadata["source_sha256"] = {
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("compete.py", "competition.py", "benchmark.py", "cost.py", "play.py", "sim.py", "hosted_like.py")
    }
    for name in ("routed.py", "llm.py", "completion.py"):
        metadata["source_sha256"]["agents/" + name] = hashlib.sha256(
            Path(__file__).parent.joinpath("agents", name).read_bytes()).hexdigest()
    stop = threading.Event()

    def checkpoint():
        while not stop.wait(15):
            snapshot = ledger.snapshot() if ledger else None
            write_report(args.output, {**metadata, "budget": snapshot})
            print(json.dumps({"status": "running", "calls": len(snapshot["calls"]) if snapshot else 0,
                              "committed_usd": snapshot["committed_usd"] if snapshot else 0}), flush=True)

    monitor = threading.Thread(target=checkpoint, daemon=True)
    monitor.start()
    try:
        result = run_competition(factories, args.scenarios, args.seeds, args.waves, args.workers,
                                 holdout=args.holdout, decision_seconds=args.decision_seconds, replica=args.replica,
                                 bench=args.bench, timing=args.timing)
    finally:
        stop.set()
        monitor.join()
        write_report(args.output, {**metadata, "status": "interrupted",
                                   "budget": ledger.snapshot() if ledger else None})
    result.update(metadata, status="completed", finished_utc=datetime.now(UTC).isoformat(),
                  budget=ledger.snapshot() if ledger else None)
    write_report(args.output, result)
    budget = {k: v for k, v in result["budget"].items() if k != "calls"} if ledger else None
    print(json.dumps({"status": result["status"], "aggregate": result["aggregate"], "budget": budget}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
