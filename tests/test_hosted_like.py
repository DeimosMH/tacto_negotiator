"""The calibrated supplier must track recorded hosted replies better than the old scripted rule."""

import json
import statistics
from pathlib import Path

from tacto_negotiator.hosted_like import PROFILE_RANGES, build_replica_scenarios, counter

ROOT = Path(__file__).resolve().parents[1]


def replay_errors(predict):
    feedback = json.loads((ROOT / "tests/fixtures/practice_feedback.json").read_text())
    errors, hits = [], []
    for session in feedback["sessions"]:
        goal = feedback["cases"][session["case_id"]]["buyer"].get("category_goal", "routine")
        profile = {name: sum(r) / 2 for name, r in zip(
            ("a1", "cap1", "a2", "depth", "final_round", "final_gap"), PROFILE_RANGES[goal], strict=True)}
        state, pending = {}, {}
        for event in session["trace"]:
            move = event["move"]
            if event["actor"] == "buyer":
                sheets = [s.get("terms", s) for s in move.get("term_sheets") or []]
                if move.get("supplier_id") in state and sheets:
                    pending[move["supplier_id"]] = min(s["unit_price"] for s in sheets if s.get("unit_price"))
                continue
            sid, price = event["actor"], (move.get("terms") or {}).get("unit_price")
            if price is None:
                continue
            if sid in pending and sid in state:
                thread = state[sid]
                ask = pending.pop(sid)
                thread["round"] += 1
                thread["gap"] += max(0, (thread["price"] - ask) / thread["p0"] * 100)
                predicted, status = predict(profile, thread, ask)
                errors.append(abs(predicted - price) / thread["p0"] * 100)
                hits.append(status[:4] == move["status"][:4])
                thread["last"] = ask
            state.setdefault(sid, {"p0": price, "round": 0, "gap": 0.0, "last": None})["price"] = price
    return statistics.mean(errors), statistics.mean(hits), len(errors)


def calibrated(profile, thread, ask):
    return counter(profile, thread["p0"], thread["round"], thread["price"], ask, thread["gap"], thread["last"])


def old_rule(profile, thread, ask):  # sim.py: fixed 2 % step to a 90 % floor, final at round 5
    price = max(thread["p0"] * 0.9, thread["price"] - thread["p0"] * 0.02)
    if ask >= price:
        return ask, "agreed"
    return price, "final_offer" if thread["round"] >= 5 else "open"


def test_calibrated_supplier_tracks_hosted_replies_better_than_the_scripted_rule():
    error, accuracy, steps = replay_errors(calibrated)
    old_error, old_accuracy, _ = replay_errors(old_rule)
    assert steps >= 40
    assert error < old_error and accuracy >= old_accuracy
    assert error < 2.5  # % of the opening price, with per-goal midpoint profiles (no per-case fitting)


def test_replicas_use_recorded_hosted_references_and_seeded_private_profiles():
    first, again, other = build_replica_scenarios(0), build_replica_scenarios(0), build_replica_scenarios(1)
    assert len(first) == 10 and [s.profiles for s in first] == [s.profiles for s in again]
    assert [s.profiles for s in first] != [s.profiles for s in other]
    assert all(s.reference["c_open"] > s.reference["c_best"] for s in first)
