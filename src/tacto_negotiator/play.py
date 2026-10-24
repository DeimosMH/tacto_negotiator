"""Play cases with an agent against the arena over HTTP - the hosted arena (practice, sandbox,
final) or `tacto-sim`, the local contract simulator. Both speak the same API.

An agent is any object with `decide(case, threads) -> {"supplier_id": ..., "message": {...}}`:
  threads: {supplier_id: [message, ...]} - every e-mail so far, both directions, in order;
           supplier attachments carry their text under "text" (see `docs.read_document`).
  message: subject, body, up to 3 term sheets, refs - or an "action" that closes the case:
           {"type": "accept", "term_sheet_id": ...} or {"type": "walk_away"}.
"""

import functools
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .client import ArenaError
from .cost import must_haves_ok, total_cost, valid_value
from .docs import read_document, standard_terms

log = logging.getLogger("arena")

ENDED = {"walked_away", "no_bid"}
# A message later than this after the supplier's last one counts as a timeout.
MOVE_LIMIT_S = 60
SEND_MARGIN_S = 5  # leave time for validation, shared pacing and submitting the move
# In the Live Final, close this long before the wave's cap - or the slowest round of the case so far
# (your decision plus a supplier reply of up to about a minute) plus a margin, if that is longer.
CLOSE_BEFORE_CAP_S, ROUND_MARGIN_S = 90, 15
RUNNING = re.compile(r"one session per case in the final: (\S+)")
# Practice: the case list shows every practice case file; only those marked hosted can be played
# (a session on another is a 403). Without the marker every listed case counts as hosted.
HOSTED_FIELD = "hosted"


class RemoteTable:
    """The arena over HTTP: the practice arena, the final, or a hosted sandbox."""

    def __init__(self, client, case=None):
        self.client, self.session_id, self.case, self.done = client, None, case, None

    def open(self, case_id):
        try:
            r = self.client.open_session(case_id)
        except ArenaError as e:
            running = RUNNING.search(str(e.detail)) if e.status == 409 else None
            if not (running and self.case):
                raise
            return self.resume(running.group(1))
        self.session_id = r["session_id"]
        log.info("session %s", self.session_id)
        return r["case"], r["threads"]

    def resume(self, session_id):
        """Pick up a final session this team already started (e.g. after a crash): every thread
        so far, from the arena."""
        self.session_id = session_id
        log.info("resuming session %s", session_id)
        state = self.client.session(session_id)
        if state["status"] == "ended":
            self.done = self.result()
        return self.case, {s: thread_events(self.client.thread(session_id, s)) for s in state["threads"]}

    def send(self, supplier_id, message):
        return self.client.send_message(self.session_id, supplier_id, message)

    def close(self, supplier_id, message):
        reply = self.client.send_message(self.session_id, supplier_id, message)
        return reply.get("result", reply)  # the supplier's last e-mail carries the result

    def attachment(self, attachment_id, **options):
        return self.client.attachment(self.session_id, attachment_id, **options)

    def thread_deadlines(self, threads, deadline=None):
        """Read authoritative timers, including after resume; use monotonic time locally."""
        requested = time.monotonic()
        read_deadline = min(deadline, requested + SEND_MARGIN_S) if deadline is not None else requested + SEND_MARGIN_S

        def remaining():
            left = read_deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("no budget for a timer refresh")
            return left

        try:
            state = self.client.session(self.session_id, timeout=remaining(), retries=0)
            if state.get("status") == "ended":
                self.done = state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}
                return {}
            deadlines = {}
            for sid, thread in state["threads"].items():
                if thread.get("closed"):
                    if sid in live_threads(threads):
                        threads[sid].append({"from": "arena", "status": "walked_away", "term_sheets": [],
                                             "body": f"Thread ended: {thread['closed']}"})
                    continue
                if sid in threads and (thread.get("offer_id") != standing(threads[sid])[0]
                                       or thread.get("status") != latest(threads[sid]).get("status")):
                    threads[sid] = thread_events(self.client.thread(
                        self.session_id, sid, timeout=remaining(), retries=0))
                    for message in threads[sid]:
                        fetch_documents(self, message, deadline=read_deadline)
                left = thread.get("seconds_left")
                deadlines[sid] = requested + max(0, left if left is not None else MOVE_LIMIT_S)
            return deadlines
        except (httpx.TransportError, TimeoutError):
            log.warning("timer refresh failed; closing without model work")
        except ArenaError as error:
            if error.status not in {429, 500, 502, 503, 504}:
                raise
            log.warning("timer refresh returned %s; closing without model work", error.status)
        return dict.fromkeys(live_threads(threads), requested)

    def missed(self, supplier_id, error):
        """After a 409: the result if the case has ended (e.g. at a wave's cap), else what the
        thread now says (a timeout answer, or the reason it ended)."""
        state = self.client.session(self.session_id)
        if state.get("status") == "ended":
            return {"result": state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}}
        return {"messages": thread_events(self.client.thread(self.session_id, supplier_id))}

    def result(self):
        state = self.client.session(self.session_id)
        return state.get("result") or {"deal": False, "end_reason": state.get("end_reason"), **state}


def latest(events):
    """The supplier's latest message in a thread."""
    return next(m for m in reversed(events) if m.get("from") != "buyer")


def standing(events):
    """(term_sheet_id, terms) of the supplier's standing term sheet, or (None, {})."""
    sheets = latest(events).get("term_sheets") or []
    return (sheets[0]["term_sheet_id"], sheets[0]["terms"]) if sheets else (None, {})


def thread_events(thread):
    """Restore a thread using only terms belonging to its current offer, including timeout rounds."""
    events = list(thread["messages"])
    offer_id = thread.get("offer_id")
    current = next((m for m in reversed(events) if m.get("from") != "buyer"
                    and any(s.get("term_sheet_id") == offer_id for s in m.get("term_sheets") or [])), None)
    if current is None:
        timeout = next((t for t in reversed(thread.get("timeouts", []))
                        if offer_id and t.get("offer_id") == offer_id), None)
        current = {"from": "arena", "term_sheets": [
            {"term_sheet_id": offer_id, "terms": timeout["terms"]}] if timeout else [], "attachments": []}
    restored = {**current, "term_sheets": [s for s in current.get("term_sheets") or []
                                          if s.get("term_sheet_id") == offer_id],
                "status": "walked_away" if thread.get("closed") else thread["status"],
                "rounds_used": sum(m.get("from") == "buyer" for m in events) + len(thread.get("timeouts", []))}
    if events and events[-1] is current:
        events[-1] = restored
    else:
        events.append(restored)
    return events


def live_threads(threads):
    """Suppliers whose thread has not ended."""
    return [sid for sid, ev in threads.items() if latest(ev).get("status") not in ENDED]


def moves_left(case, events):
    """Messages you may still send in this thread (the round limit counts each of them)."""
    used = 0
    for message in events:
        used += message.get("from") == "buyer"
        used = max(used, message.get("rounds_used", 0))
    return max(0, case.get("round_limit", 10) - used)


def fetch_documents(table, message, *, deadline=None):
    """Extract attachment text; timed readers mark unavailable documents for conservative routing."""
    for att in message.get("attachments") or []:
        if not att.get("attachment_id") or "text" in att or att.get("read_failed"):
            continue
        try:
            options = {}
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("no budget for an attachment read")
                options = {"timeout": left, "retries": 0}
            parsed = read_document(att["name"], table.attachment(att["attachment_id"], **options))
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("attachment exceeded its read budget")
            att["text"] = parsed
        except Exception as error:
            if deadline is None or isinstance(error, ArenaError) and error.status in {401, 403}:
                raise
            att["read_failed"] = True
            log.warning("attachment unavailable (%s); defaults must not be assumed", type(error).__name__)
    return message


def known_offer(case, events):
    """Resolve evidenced defaults, fixed and stated terms; identify missing quote information."""
    sheet, terms = standing(events)
    std = standard_terms(events)
    resolved = {**std, **case.get("fixed_terms", {}), **terms}
    required = set(case["negotiable"]) | {rule["field"] for rule in case["buyer"].get("must_haves", [])}
    missing = sorted(f for f in required if f not in resolved or not valid_value(f, resolved[f]))
    for message in reversed(events):
        if message.get("from") == "buyer":
            continue
        quotes = [att for att in message.get("attachments") or []
                  if att.get("name", "").lower().startswith("quote")
                  and att.get("name", "").lower().endswith(".pdf")]
        if quotes:
            if any(att.get("read_failed") for att in quotes):
                missing.append("supplier quote defaults")
            break  # a newer readable quote supersedes older document failures
    return sheet, terms, std, missing


def acceptable_offer(case, events):
    """Return (standing offer ID, cost) only when its known terms safely beat the alternative."""
    try:
        sheet, terms, std, missing = known_offer(case, events)
        if not isinstance(sheet, str) or not sheet or missing or set(terms) - set(case["negotiable"]):
            return None
        if any(not valid_value(f, v) for f, v in {**std, **case.get("fixed_terms", {}), **terms}.items()):
            return None
        for field, value in terms.items():
            options = case.get("term_options", {}).get(field)
            if options and value not in options and value != std.get(field):
                return None
        if not must_haves_ok(case, terms, std):
            return None
        cost = total_cost(case, terms, std)
        alt = case["buyer"].get("alternative") or {}
        if alt.get("kind") not in (None, "none"):
            other = alt.get("terms", {})
            if not valid_value("unit_price", other.get("unit_price")) or any(
                not valid_value(f, v) for f, v in other.items()
            ):
                return None
            other_cost = total_cost(case, other)
            if not math.isfinite(other_cost) or cost >= other_cost:
                return None
        return (sheet, cost) if math.isfinite(cost) else None
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def closing_move(case, threads):
    """Accept the cheapest standing term sheet that keeps the must-haves and beats the alternative
    (C with each supplier's standard terms), else walk away - what to do when time runs out."""
    live = [sid for sid in live_threads(threads) if sid in case["suppliers"]]
    if not live:
        raise ValueError("no live supplier belongs to this case")
    best = None
    for sid in live:
        if accepted := acceptable_offer(case, threads[sid]):
            sheet_id, c = accepted
            best = min(best or (c, sid, sheet_id), (c, sid, sheet_id))
    if best is None:
        return {"supplier_id": live[0], "message": {"action": {"type": "walk_away"}}}
    return {"supplier_id": best[1], "message": {"action": {"type": "accept", "term_sheet_id": best[2]}}}


def play(table, case_id, agent, deadline=None):
    """Play one case to its end. `deadline` (time.time(), or a function returning it): a Live Final
    wave's cap - shortly before it, `closing_move` closes the case so it does not end without a deal."""
    case, threads = table.open(case_id)
    if getattr(table, "done", None):  # resumed a case that had already ended
        return table.done
    if not live_threads(threads):
        return table.result()
    received = dict.fromkeys(threads, time.monotonic())
    timed_decide = getattr(agent, "decide_timed", None)
    close = getattr(agent, "closing_move", closing_move)

    def read_deadline():
        expires = min(received[sid] + MOVE_LIMIT_S for sid in live_threads(threads)) - SEND_MARGIN_S
        cap = deadline() if callable(deadline) else deadline
        if cap is not None:
            expires = min(expires, time.monotonic() + cap - time.time() - SEND_MARGIN_S)
        return min(expires, time.monotonic() + SEND_MARGIN_S)

    def read(message):
        return fetch_documents(table, message, deadline=read_deadline() if timed_decide else None)

    for events in threads.values():
        for m in events:
            read(m)
    slowest = 0.0  # the longest round so far: decision + the supplier's reply
    while live_threads(threads):
        decision_deadline = None
        if timed_decide:
            if get_deadlines := getattr(table, "thread_deadlines", None):
                thread_deadlines = get_deadlines(threads, deadline=read_deadline())
                received.update({sid: expires - MOVE_LIMIT_S for sid, expires in thread_deadlines.items()})
                if getattr(table, "done", None):
                    return table.done
                if not live_threads(threads):
                    break
            else:
                thread_deadlines = {sid: received[sid] + MOVE_LIMIT_S for sid in live_threads(threads)}
            decision_deadline = min(thread_deadlines.get(sid, time.monotonic())
                                    for sid in live_threads(threads)) - SEND_MARGIN_S
        t0 = time.monotonic()
        cap = deadline() if callable(deadline) else deadline
        reserve = max(CLOSE_BEFORE_CAP_S, slowest + ROUND_MARGIN_S)
        if cap and time.time() > cap - reserve:
            log.info("%s: the wave's cap is near - closing", case_id)
            decision = close(case, threads)
        elif timed_decide:
            wave_left = cap - time.time() if cap is not None else None
            if wave_left is not None:
                decision_deadline = min(decision_deadline, time.monotonic() + wave_left - reserve)
            decision = timed_decide(case, threads, deadline=decision_deadline, wave_seconds_left=wave_left)
        else:
            decision = agent.decide(case, threads)
        if (took := time.monotonic() - t0) > MOVE_LIMIT_S * 0.75:
            log.warning("decision took %.1fs - the limit is %ss per message", took, MOVE_LIMIT_S)
        # The decision itself may consume the closing reserve; the host may also extend the cap.
        cap = deadline() if callable(deadline) else deadline
        if (cap and time.time() > cap - max(CLOSE_BEFORE_CAP_S, slowest + ROUND_MARGIN_S)) or (
            decision_deadline is not None and time.monotonic() >= decision_deadline
        ):
            decision = close(case, threads)
        sid, msg = decision["supplier_id"], decision["message"]
        try:
            if msg.get("action"):
                log.info("%s %s", sid, msg["action"])
                return table.close(sid, msg)
            reply = table.send(sid, msg)
        except ArenaError as e:
            if e.status != 409:
                raise
            log.info("%s: %s", sid, e.detail)
            reply = table.missed(sid, e)  # e.g. late, or the thread or the case has ended
            received[sid] = time.monotonic()
            if "messages" in reply:
                threads[sid] = reply["messages"]
                if live_threads(threads):
                    for message in threads[sid]:
                        read(message)
                continue  # the rejected request is not a buyer move
            if msg.get("action") and "result" not in reply:  # the case goes on: decide again
                threads[sid].append(read(reply))
                continue
        if "result" in reply:  # the reply ended the case, e.g. the last supplier walked away
            return reply["result"]
        received[sid] = time.monotonic()
        threads[sid] += [{**msg, "from": "buyer"}, read(reply)]
        slowest = max(slowest, time.monotonic() - t0)
        log.info("%s %s %s", sid, reply.get("status"), standing(threads[sid])[1])
    return table.result()


def hosted(cases):
    """The cases you can play: those marked hosted, or all of them if the list has no marker."""
    if any(HOSTED_FIELD in c for c in cases):
        return [c for c in cases if c.get(HOSTED_FIELD)]
    return cases


def quota_used_up(error):
    """The practice quota's 429 ("practice limit: …"): retrying does not help before it resets."""
    return isinstance(error, ArenaError) and error.status == 429 and str(error.detail).startswith("practice limit")


def play_all(new_table, case_ids, new_agent, parallel=8):
    """Play many cases at once (e.g. every practice case). `new_table()` and `new_agent()` give
    each case its own table and agent. Once the practice quota is used up, no further case starts.
    Returns {case_id: result or the exception that stopped it}."""
    used_up = threading.Event()

    def one(case_id):
        if used_up.is_set():
            return case_id, ArenaError(429, "not started: the practice quota is used up")
        try:
            return case_id, play(new_table(), case_id, new_agent())
        except ArenaError as e:
            if quota_used_up(e):
                used_up.set()
                log.warning("practice quota used up: %s", e.detail)
            elif e.status == 403:
                log.warning("%s: %s", case_id, e.detail)  # e.g. not hosted for practice
            else:
                log.exception("%s failed", case_id)
            return case_id, e
        except Exception as e:  # one broken case must not stop the others
            log.exception("%s failed", case_id)
            return case_id, e

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        return dict(pool.map(one, case_ids))


def play_waves(client, new_agent, waves=5, parallel=10, poll=5.0):
    """The Live Final: wait for each wave, play its cases in parallel within the wave's cap, wait
    for the next. Keep it running from before the first wave until the last has closed; it returns
    after `waves` waves have closed and every case has finished: {case_id: result or exception}."""
    started, closed, caps = {}, set(), {}  # caps: wave -> its cap as time.time()

    def one(case, deadline):
        try:
            return play(RemoteTable(client, case), case["case_id"], new_agent(), deadline)
        except Exception as e:  # one broken case must not stop the others
            log.exception("%s failed", case["case_id"])
            return e

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while len(closed) < waves or not all(f.done() for f in started.values()):
            try:
                cases = client.cases("final")
            except ArenaError as e:
                if e.status != 403:  # 403: no wave has started yet
                    raise
                cases = []
            for case in cases:
                if not case.get("open"):
                    if case["wave"] not in closed:
                        log.warning("wave %s closed (%d of %d)", case["wave"], len(closed) + 1, waves)
                    closed.add(case["wave"])
                    continue
                if case.get("seconds_left") is not None:  # each wave has its own cap; follow an extension
                    caps[case["wave"]] = time.time() + case["seconds_left"]
                if case["case_id"] not in started and case.get("seconds_left"):
                    if case["wave"] not in {c["wave"] for c in cases if c["case_id"] in started}:
                        log.warning("wave %s started: %s", case["wave"], case.get("theme"))
                    started[case["case_id"]] = pool.submit(one, case, functools.partial(caps.get, case["wave"]))
            time.sleep(poll)
    return {case_id: f.result() for case_id, f in started.items()}
