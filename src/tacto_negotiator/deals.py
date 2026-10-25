"""Deal journal: every hosted negotiation stored as a dated conversation with its decision path.

`recording(table, case_id, agent)` wraps one `play()`: the agent's decision calls and the table's
sends and closes are timed and logged, never changed. After every move it writes
`docs/deals/<id>.json` (the record), `docs/deals/<id>.js` (the same, loadable from file://) and
`docs/deals.js` (the index), which the docs atlas shows under "Deals" (docs/index.html#deals).
A recording failure is logged and ignored: the negotiation goes on.

TACTO_DEALS_DIR=off disables it; any other value moves the directory (the index goes next to it).
`tacto deals` rebuilds the index or backfills records from saved hosted results."""

import contextlib
import copy
import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("arena")

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "docs" / "deals"
INDEX_NAME = "deals.js"
_LOCK = threading.Lock()  # parallel cases share the index
_SUMMARIES = {}  # record file -> (mtime, index row)

# The routed buyer's decision tree (agents/routed.py `decide_timed`, `_script`, `_structured_bargain`;
# play.py for the runner). (id, parent, label, why) - the UI draws it and marks each step's path.
TREE = [
    ("decide", None, "Decision request",
     "play() asks the buyer for one move whenever a supplier has replied or a thread clock needs an answer."),
    ("runner_close", "decide", "Runner closes at the wave cap",
     "The wave cap is near (play.py): the runner closes on the best acceptable offer without asking the agent."),
    ("deadline_close", "decide", "No time left",
     "The decision budget or the wave is spent: close on the best acceptable offer instead of risking a timeout."),
    ("scripts", "decide", "Scripts first",
     "Deterministic rules cover the known situations; they cost no model call and cannot misread prose."),
    ("terminal", "scripts", "Every thread final or out of rounds",
     "No supplier will move any more: countering a final offer made hosted suppliers walk away."),
    ("mirror_alternative", "terminal", "Mirror a cheaper alternative",
     "A final offer carried a cheaper alternative package: send it back unchanged once "
     "(9 of 9 hosted mirrors agreed)."),
    ("terminal_offers", "terminal", "Accept the best standing offer",
     "Accept the cheapest acceptable offer by total cost C, or take the alternative."),
    ("missing_quote_terms", "scripts", "Ask for missing quote terms",
     "The quote left terms open; accepting unknown defaults could break a must-have or cost more."),
    ("floor", "scripts", "The supplier named its floor",
     "Probing below a named floor draws a final offer; close if this is our best deal."),
    ("named_floor", "floor", "Accept at the named floor", "The supplier said its standing price is its lowest."),
    ("near_close_answered", "floor", "Near-close answered", "The supplier answered our last near-close ask: close."),
    ("simple_opening_counter", "scripts", "Simple price counter",
     "One supplier, price and payment only: open with the anchored price by category goal."),
    ("bargain", "scripts", "Structured bargain over packages",
     "Complete term sheets of equal cost to us, priced at the shared concession schedule (ask limit)."),
    ("enumerated_opening", "bargain", "Opening packages",
     "First offer: the supplier's own cash terms at the anchored price plus equal-cost trades."),
    ("bounded_concession", "bargain", "Bounded concession",
     "Move a share of the gap to the supplier's best offer: 20 % of it (40 % routine), less after its own big step."),
    ("rfq_package_comparison", "bargain", "RFQ: counter the bidder that matters",
     "Several bidders: counter the one whose offer is furthest from its ask limit, at equal cost to us."),
    ("must_have_repair", "bargain", "Repair a must-have",
     "The offer breaks a requirement (e.g. lead time): ask for a package that meets it."),
    ("small_cost_gap", "bargain", "Accept: gap below 0.2 %",
     "The supplier is within 0.2 % of our last ask: another round costs more than it saves."),
    ("round_reserve", "bargain", "Accept: last round",
     "One move left: accept rather than let the thread run out."),
    ("agreed_package", "bargain", "Close an agreed package", "The supplier agreed to one of our sheets."),
    ("quote_plateau", "bargain", "Close on a plateau", "Every supplier stopped moving: take the best offer."),
    ("concurrent", "scripts", "Concurrent RFQ message",
     "While one bidder replies, a scripted message keeps another bidder's 60 s clock from running out."),
    ("model", "decide", "Model decision",
     "A supplier message carries conditions, questions or claims the scripts do not cover: the model proposes."),
    ("semantic_decision", "model", "Fast model", "The default model call (Claude Opus, high effort)."),
    ("complex_tradeoffs", "model", "Strong model",
     "Relationship goals, 4+ terms or a contradicted claim, with time for it."),
    ("complex_fast_budget", "model", "Complex case, fast model", "Too little time for the strong model."),
    ("strong_unconfigured", "model", "Complex case, one model", "No separate strong model is configured."),
    ("validate", "model", "Shared gate (validate)",
     "Every proposal is checked and repriced: the ask limit, a minimum move, equal-cost sheets only, no counter to a "
     "final offer, no sheet dearer than a rival's standing offer."),
    ("fallback", "model", "Model failed: scripted fallback",
     "A timeout, rejected proposal or provider error: a scripted counter or close replaces it, never a blind accept."),
    ("near_close", "decide", "Near-close ask instead of accepting",
     "An accept of a still-open offer becomes one last ask just below it; a supplier often takes it."),
]
NODES = {node[0]: node for node in TREE}

# Where each branch lives: (file, text on the line that implements it). `source_refs` turns them into line
# numbers when the index is written, so the links follow the code as it changes.
ROUTED, PLAY = "src/tacto_negotiator/agents/routed.py", "src/tacto_negotiator/play.py"
SOURCES = {
    "decide": [(PLAY, "decision = timed_decide(case, threads"), (ROUTED, "def decide_timed(")],
    "runner_close": [(PLAY, "the wave's cap is near - closing")],
    "deadline_close": [(ROUTED, 'self._record(case, "script", "deadline_close"')],
    "scripts": [(ROUTED, "def _script(self, case, threads)")],
    "terminal": [(ROUTED, 'if all(latest(threads[s]).get("status") in {"agreed", "final_offer"}')],
    "mirror_alternative": [(ROUTED, "def _mirror_alternative("), (ROUTED, 'return "mirror_alternative", mirror')],
    "terminal_offers": [(ROUTED, 'return "terminal_offers", closing_move'), (PLAY, "def closing_move(")],
    "missing_quote_terms": [(ROUTED, 'return "missing_quote_terms", move')],
    "floor": [(ROUTED, 'answered = any(NEAR_CLOSE_TEXT in')],
    "named_floor": [(ROUTED, '"near_close_answered" if answered else "named_floor"')],
    "near_close_answered": [(ROUTED, '"near_close_answered" if answered else "named_floor"')],
    "simple_opening_counter": [(ROUTED, 'return "simple_opening_counter", move')],
    "bargain": [(ROUTED, "def _structured_bargain("), (PLAY, "def ask_limit(")],
    "enumerated_opening": [(ROUTED, 'reason = "bounded_concession" if sent else "enumerated_opening"'),
                           (ROUTED, "def _opening_sheets(")],
    "bounded_concession": [(ROUTED, 'reason = "bounded_concession" if sent else "enumerated_opening"'),
                           (PLAY, "def ask_limit(")],
    "rfq_package_comparison": [(ROUTED, 'reason = "rfq_package_comparison"')],
    "must_have_repair": [(ROUTED, 'reason = "must_have_repair"')],
    "small_cost_gap": [(ROUTED, 'reason = "small_cost_gap" if small_gap else "round_reserve"')],
    "round_reserve": [(ROUTED, 'reason = "small_cost_gap" if small_gap else "round_reserve"')],
    "agreed_package": [(ROUTED, 'return "agreed_package", closing_move')],
    "quote_plateau": [(ROUTED, 'return "quote_plateau", closing_move')],
    "concurrent": [(ROUTED, "def concurrent_move("), (PLAY, "def send_all(")],
    "model": [(ROUTED, "if self._semantic_blocker(case, threads):"), (ROUTED, "reply = complete(SYSTEM, prompt")],
    "semantic_decision": [(ROUTED, 'route, reason = "fast", "semantic_decision"')],
    "complex_tradeoffs": [(ROUTED, 'route, reason = "strong", "complex_tradeoffs"')],
    "complex_fast_budget": [(ROUTED, 'reason = "complex_fast_budget" if self.strong')],
    "strong_unconfigured": [(ROUTED, 'reason = "complex_fast_budget" if self.strong')],
    "validate": [(ROUTED, "def validate(self, case, threads, decision"), (ROUTED, "def _rival_limit(")],
    "fallback": [(ROUTED, 'log.warning("decision fallback after')],
    "near_close": [(ROUTED, "def _before_accept(")],
}
ROOT = Path(__file__).resolve().parents[2]
_REFS = {}


def source_refs():
    """{node: [{"file", "line", "text"}]} - the line of each anchor in the current source (cached per mtime)."""
    out = {}
    for node, anchors in SOURCES.items():
        refs = []
        for path, needle in anchors:
            file = ROOT / path
            try:
                stamp = file.stat().st_mtime_ns
                if _REFS.get(path, (None,))[0] != stamp:
                    _REFS[path] = (stamp, file.read_text(encoding="utf-8").splitlines())
                lines = _REFS[path][1]
            except OSError:
                continue
            line = next((i for i, text in enumerate(lines, 1) if needle in text), None)
            if line:
                refs.append({"file": path, "line": line, "text": needle})
        out[node] = refs
    return out
MODEL_REASONS = {"semantic_decision", "complex_tradeoffs", "complex_fast_budget", "strong_unconfigured"}


def path_of(step):
    """The tree nodes from the root to this step's leaf: why the program went where it went."""
    route, reason = step.get("route"), step.get("reason") or ""

    def chain(leaf):
        out = []
        while leaf:
            out.append(leaf)
            leaf = NODES[leaf][1]
        return out[::-1]

    if route == "runner":
        return chain("runner_close")
    if step.get("concurrent"):
        return chain("concurrent")
    if step.get("fallback"):
        return [*chain("fallback"), "error:" + reason]
    if reason in NODES:
        nodes = chain(reason)
        if reason == "near_close":
            nodes = [*chain("model" if route in {"fast", "strong"} else "scripts"), "near_close"]
        if reason in MODEL_REASONS:
            nodes.append("validate")
        return nodes
    return [*chain("model" if route in {"fast", "strong"} else "scripts"), "reason:" + reason]


def directory():
    value = os.environ.get("TACTO_DEALS_DIR", "")
    if value.lower() in {"off", "0", "none", "false"}:
        return None
    if value:
        return Path(value)
    return DEFAULT_DIR if DEFAULT_DIR.parent.is_dir() else None


def now_iso():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sheets(message):
    return [s.get("terms", s) for s in (message or {}).get("term_sheets") or [] if isinstance(s, dict)]


def summarize(move):
    """The move as the UI shows it: supplier, action, term sheets."""
    if not isinstance(move, dict):
        return None
    message = move.get("message") or {}
    action = (message.get("action") or {})
    return {"supplier_id": move.get("supplier_id"), "action": action.get("type") or "send",
            "term_sheet_id": action.get("term_sheet_id"), "term_sheets": _sheets(message)}


def _event(event, sid):
    """One message for the chat view: who, what, the terms, the supplier's status and claim checks."""
    keep = ("message_id", "from", "from_name", "subject", "body", "status", "claim_results", "refs", "at")
    out = {k: copy.deepcopy(event[k]) for k in keep if event.get(k) is not None}
    out["supplier_id"] = sid
    out["from"] = "buyer" if event.get("from") == "buyer" else "supplier"
    out["term_sheets"] = [{"id": s.get("term_sheet_id"), "terms": s.get("terms", s)}
                          for s in event.get("term_sheets") or [] if isinstance(s, dict)]
    if action := event.get("action"):
        out["action"] = action
    if attachments := event.get("attachments"):
        out["attachments"] = [a.get("name") for a in attachments if isinstance(a, dict)]
    return out


def safe_id(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text)).strip("-")


class DealRecord:
    """One negotiation as it happens; `save` writes it after every move."""

    def __init__(self, case_id, folder, *, source="hosted"):
        self.folder = folder
        self.started = now_iso()
        self.record = {"id": None, "case_id": case_id, "source": source, "status": "live", "started_utc": self.started,
                       "updated_utc": self.started, "finished_utc": None, "session_id": None, "title": None,
                       "goal": None, "suppliers": {}, "messages": [], "decisions": [], "result": None}
        self.threads = None
        self.seen = {}  # (supplier, index) -> when we first saw a message
        self.sends = {}  # supplier -> [time of each buyer message we sent]

    # -- capture -------------------------------------------------------------------------------
    def observe(self, case, threads):
        if case and not self.record["title"]:
            buyer = case.get("buyer") or {}
            self.record.update(title=case.get("title"), goal=buyer.get("category_goal"), type=case.get("type"),
                               negotiable=case.get("negotiable"), round_limit=case.get("round_limit"))
        if threads is not None:
            self.threads = threads
            stamp = now_iso()
            for sid, events in threads.items():
                for index, _ in enumerate(events):
                    self.seen.setdefault((sid, index), stamp)

    def decision(self, step):
        self.record["decisions"].append(step)

    def sent(self, sid, move, stamp, error=None):
        if error is not None:  # a rejected message is not in the thread
            self.record.setdefault("errors", []).append({"at": stamp, "supplier_id": sid, "error": str(error)[:300]})
            return
        if (move or {}).get("action"):
            self.record["closing"] = {"at": stamp, "supplier_id": sid, "action": move["action"]}
            return
        self.sends.setdefault(sid, []).append(stamp)
        if self.record["decisions"]:
            last = self.record["decisions"][-1]
            if (last.get("move") or {}).get("supplier_id") == sid and "sent_utc" not in last:
                last["sent_utc"] = stamp
            elif step := next((d for d in reversed(self.record["decisions"]) if d.get("concurrent")
                               and (d.get("move") or {}).get("supplier_id") == sid and "sent_utc" not in d), None):
                step["sent_utc"] = stamp

    def finish(self, result, error=None):
        self.record["status"] = "error" if error is not None and result is None else "ended"
        self.record["finished_utc"] = now_iso()
        if isinstance(result, dict):
            self.record["result"] = json.loads(json.dumps(result, default=str))
        if error is not None:
            self.record["error"] = f"{type(error).__name__}: {error}"[:300]

    # -- output --------------------------------------------------------------------------------
    def messages(self):
        out = []
        for sid, events in (self.threads or {}).items():
            buyer_index = 0
            for index, event in enumerate(events):
                item = _event(event, sid)
                stamp = self.seen.get((sid, index))
                if item["from"] == "buyer":
                    sends = self.sends.get(sid, [])
                    stamp = sends[buyer_index] if buyer_index < len(sends) else stamp
                    buyer_index += 1
                item.setdefault("at", stamp)
                item["index"] = index
                out.append(item)
        if closing := self.record.get("closing"):
            out.append({"from": "buyer", "supplier_id": closing["supplier_id"], "at": closing["at"],
                        "action": closing["action"], "closing": True, "term_sheets": [], "index": 10_000})
        return out

    def build(self):
        record = self.record
        record["updated_utc"] = now_iso()
        record["messages"] = self.messages() if self.threads is not None else record["messages"]
        names = {}
        for message in record["messages"]:
            if message["from"] == "supplier" and message.get("from_name"):
                names.setdefault(message["supplier_id"], message["from_name"])
        record["suppliers"] = {sid: names.get(sid, sid) for sid in (self.threads or record["suppliers"] or {})}
        if not record["id"]:
            day = record["started_utc"][:10]
            record["id"] = safe_id(f"{day}_{record['case_id'].removeprefix('case:')}_"
                                   f"{record['session_id'] or record['started_utc'][11:19]}")
        for step in record["decisions"]:
            step["path"] = path_of(step)
        return record

    def save(self):
        if self.folder is None:
            return
        save_record(self.build(), self.folder)


def save_record(record, folder):
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, ensure_ascii=False, indent=1, default=str)
    _write(folder / f"{record['id']}.json", text + "\n")
    _write(folder / f"{record['id']}.js", "// Generated by tacto_negotiator.deals. Do not edit.\n"
           f"window.tactoDeal({_script_safe(json.dumps(record, ensure_ascii=False, default=str))});\n")
    write_index(folder)


def _write(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _script_safe(text):
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def summary(record):
    result = record.get("result") or {}
    decisions = record.get("decisions") or []
    return {"id": record["id"], "case_id": record.get("case_id"), "title": record.get("title"),
            "goal": record.get("goal"), "source": record.get("source"), "status": record.get("status"),
            "started_utc": record.get("started_utc"), "finished_utc": record.get("finished_utc"),
            "updated_utc": record.get("updated_utc"), "session_id": record.get("session_id"),
            "run": record.get("run"), "suppliers": record.get("suppliers"),
            "messages": len(record.get("messages") or []), "decisions": len(decisions),
            "model_calls": sum(d.get("route") in {"fast", "strong"} for d in decisions),
            "fallbacks": sum(bool(d.get("fallback")) for d in decisions),
            "nodes": _node_steps(decisions),
            "deal": result.get("deal"), "score": result.get("score"), "end_reason": result.get("end_reason"),
            "supplier_id": result.get("supplier_id"), "unit_price": (result.get("terms") or {}).get("unit_price")}


def _node_steps(decisions):
    """{node: [times taken, first step number]} - lets the full tree link to example deals."""
    out = {}
    for number, step in enumerate(decisions, 1):
        for node in step.get("path") or path_of(step):
            entry = out.setdefault(node, [0, number])
            entry[0] += 1
    return out


def write_index(folder):
    """docs/deals.js: every record's summary and the decision tree, newest first."""
    with _LOCK:
        refs = source_refs()
        rows = []
        for file in sorted(folder.glob("*.json")):
            try:
                stamp = file.stat().st_mtime_ns
                cached = _SUMMARIES.get(file)
                if cached is None or cached[0] != stamp:  # read each record once per change, not per move
                    cached = _SUMMARIES[file] = (stamp, summary(json.loads(file.read_text(encoding="utf-8"))))
                rows.append(cached[1])
            except (OSError, ValueError, KeyError) as error:
                log.warning("deal index skipped %s: %s", file.name, error)
        rows.sort(key=lambda row: (row.get("started_utc") or "", row["id"]), reverse=True)
        data = {"generated_utc": now_iso(), "folder": folder.name, "deals": rows,
                "tree": [{"id": i, "parent": p, "label": label, "why": why, "source": refs.get(i, [])}
                         for i, p, label, why in TREE]}
        _write(folder.parent / INDEX_NAME, "// Generated by tacto_negotiator.deals. Do not edit.\nwindow.DEALS = "
               + _script_safe(json.dumps(data, ensure_ascii=False, sort_keys=True)) + ";\n")


# -- recording one play() ----------------------------------------------------------------------
@contextlib.contextmanager
def recording(table, case_id, agent):
    """Record this negotiation while play() runs; yields the record (or None when disabled)."""
    folder = directory() if hasattr(table, "client") else None
    if folder is None:
        yield None
        return
    deal = DealRecord(case_id, folder)
    patched = {}

    def guard(action):
        try:
            action()
        except Exception:  # recording must never change a negotiation
            log.exception("deal recording failed")

    def save():
        guard(lambda: (deal.record.update(session_id=getattr(table, "session_id", None)), deal.save()))

    def timed(name, route=None, concurrent=False):
        original = getattr(agent, name, None)
        if original is None:
            return

        def wrapper(case, threads, *args, **kwargs):
            started, at = time.monotonic(), now_iso()
            guard(lambda: deal.observe(case, threads))
            if hasattr(agent, "last_decision"):
                with contextlib.suppress(Exception):
                    agent.last_decision = {}
            out = original(case, threads, *args, **kwargs)

            def note():
                if concurrent:
                    if not out:
                        return
                    reason, move = out
                    step = {"route": "script", "reason": reason, "concurrent": True}
                else:
                    move = out
                    trace = dict(getattr(agent, "last_decision", None) or {})
                    step = {k: trace[k] for k in ("route", "reason", "fallback", "error", "budget_s", "closure")
                            if k in trace}
                    if route:
                        step.update(route=route, reason="wave_cap_close")
                    step.setdefault("route", "agent")
                step.update(at=at, decision_s=round(time.monotonic() - started, 3), move=summarize(move))
                deal.decision(step)
                save()

            guard(note)
            return out

        patched[name] = name in vars(agent)
        setattr(agent, name, wrapper)

    timed("decide_timed")
    if not hasattr(agent, "decide_timed"):  # RoutedAgent.decide calls decide_timed: record it once
        timed("decide")
    timed("concurrent_move", concurrent=True)
    timed("closing_move", route="runner")

    original_send, original_close = table.send, getattr(table, "close", None)

    def send(supplier, message):
        try:
            reply = original_send(supplier, message)
        except Exception as error:
            failure = error
            guard(lambda: deal.sent(supplier, message, now_iso(), error=failure))
            raise
        guard(lambda: deal.sent(supplier, message, now_iso()))
        return reply

    def close(supplier, message):
        stamp = now_iso()
        result = original_close(supplier, message)
        guard(lambda: deal.sent(supplier, message, stamp))
        return result

    table.send = send
    if original_close is not None:
        table.close = close
    try:
        yield deal
    finally:
        for name, own in patched.items():
            if not own:  # a method of the class: drop the wrapper (an own attribute stays wrapped)
                with contextlib.suppress(Exception):
                    delattr(agent, name)
        with contextlib.suppress(Exception):
            del table.send
            if original_close is not None:
                del table.close


def recorded_play(play, table, case_id, agent, *args, **kwargs):
    """play() with its deal journal: the result and any error are stored, then passed on unchanged."""
    with recording(table, case_id, agent) as deal:
        if deal is None:
            return play(table, case_id, agent, *args, **kwargs)
        try:
            result = play(table, case_id, agent, *args, **kwargs)
        except BaseException as error:
            try:
                deal.record["session_id"] = getattr(table, "session_id", None)
                deal.finish(None, error)
                deal.save()
            except Exception:
                log.exception("deal recording failed")
            raise
        try:
            deal.record["session_id"] = getattr(table, "session_id", None)
            deal.finish(result)
            deal.save()
        except Exception:
            log.exception("deal recording failed")
        return result


# -- backfill from saved hosted results ---------------------------------------------------------
def backfill(client, reports, folder, *, thread_events, skip_existing=True):
    """Rebuild records of ended hosted sessions from `docs/results/live-practice-*.json`: the
    conversation from the arena (read-only), the decision trail from the saved report. Times of
    messages are not known for these: the report's start date is attached instead."""
    written = 0
    for report_path in reports:
        report = json.loads(Path(report_path).read_text())
        started = report.get("started_utc") or ""
        for match in report.get("matches", []):
            session_id = match.get("session_id")
            if not session_id or not match.get("case_id"):
                continue
            ident = safe_id(f"{started[:10]}_{match['case_id'].removeprefix('case:')}_{session_id}")
            if skip_existing and (folder / f"{ident}.json").exists():
                continue
            try:
                state = client.session(session_id)
                threads = {sid: thread_events(client.thread(session_id, sid)) for sid in state.get("threads", {})}
            except Exception as error:  # ended sessions only; one missing session must not stop the rest
                log.warning("backfill %s %s: %s", match["case_id"], session_id, error)
                continue
            deal = DealRecord(match["case_id"], folder, source="hosted (backfilled)")
            deal.record.update(id=ident, started_utc=started, finished_utc=report.get("finished_utc") or started,
                               session_id=session_id, status="ended", title=match.get("scenario"),
                               run=report.get("phase"), times="approximate: report start")
            deal.threads = threads
            for sid, events in threads.items():
                for index, _ in enumerate(events):
                    deal.seen[(sid, index)] = started
            for decision in match.get("decisions", []):
                step = {k: decision[k] for k in ("route", "reason", "fallback", "error", "budget_s") if k in decision}
                step["decision_s"] = decision.get("decision_wall_s") or decision.get("elapsed_s")
                step["move"] = decision.get("sent_action") or decision.get("proposed_action")
                if decision.get("proposed_action") != decision.get("sent_action"):
                    step["proposed"] = decision.get("proposed_action")
                step["reply"] = decision.get("supplier_reply")
                deal.decision(step)
            last = (match.get("decisions") or [{}])[-1].get("sent_action") or {}
            if last.get("action") in {"accept", "walk_away"}:
                deal.record["closing"] = {
                    "at": report.get("finished_utc") or started, "supplier_id": last.get("supplier_id"),
                    "action": {"type": last["action"], "term_sheet_id": last.get("term_sheet_id")}}
            keys = ("deal", "score", "supplier_id", "terms", "cost_eur", "reference_cost_eur", "opening_cost_eur",
                    "alternative_cost_eur", "cost_breakdown", "rounds", "end_reason", "replay_url")
            deal.record["result"] = {k: match.get(k) for k in keys if k in match}
            deal.record["result"]["replay_url"] = match.get("replay_url") or state.get("replay_url")
            save_record(deal.build(), folder)
            written += 1
    return written
