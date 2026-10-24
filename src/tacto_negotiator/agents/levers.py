"""Three general levers another team on this arena found on the same practice cases (ideas only).

- Order path (their rules 76/81): when our orders with this supplier for the case part rose at least
  `PATH_MIN_RISE` over twelve months, the first message leads with that path - "our orders moved from
  X to Y; that rise is already in our price" - citing the order IDs. Their P08 first counter fell from
  5.41 to 5.25 and the final followed (0.912). A flat history moved no supplier (0 of 23 citations by
  earlier teams), so only a rising one is cited. Every claim is checked with our offline checker first.
- Ask the floor (rules 58/63): once per case, when the supplier holds after our move and is not yet
  final, ask `FLOOR_QUESTION`. A supplier that names a floor equal to its standing price is not probed
  below it.
- Near-close (rules 23/50): before accepting an OPEN offer, ask once `NEAR_CLOSE_SHARE` below it on its
  own terms; it agreed once, held twice and never walked. Never after a final offer: hosted suppliers
  walk away when a final offer is countered.
"""

import datetime
import math
import re

from ..cost import decision_cost, must_haves_ok
from ..evaluation import ClaimChecker
from ..play import known_offer, latest, moves_left, standing

PATH_MIN_RISE = 0.03
PATH_MAX_IDS = 8  # a hosted claim takes 2-8 order IDs of one part
FLOOR_QUESTION = "What is the lowest price at which you can sign these terms today?"
NEAR_CLOSE_SHARE = 0.0075
NEAR_CLOSE_TEXT = ("Thank you - we are very close. We can sign today on your own terms at the price in the "
                   "attached term sheet.")
HOLD_SHARE = 0.005  # a counter that moved less than this share of its price after our move is a hold
_FLOOR_WORDS = re.compile(r"lowest|any further|further than|floor|best possible|minimum|cannot go below|"
                          r"cannot reduce|cannot drop|bottom line", re.I)
_PRICE = re.compile(r"(?<![\w.])(\d{1,6}(?:[.,]\d{1,2})?)(?![\w.%])")


def _buyer_bodies(events):
    return [m.get("body", "") or "" for m in events if m.get("from") == "buyer"]


def _months_back(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return day.replace(year=year, month=month + 1, day=min(day.day, 28))


def order_path(package, case, sid):
    """The rising order-price path of `sid` for one case part over the twelve months before the
    package's newest order, or None: {"part", "refs", "prices", "dates", "rise"}.

    The window includes the last order before it (the price twelve months ago). The path keeps the
    first order of each price level (today's level by its newest order), ignores one-order blips (a
    single order at another price between two runs) and starts at the last low, so it rises all the
    way: "last old price -> today's"."""
    facts = getattr(package, "facts", None) or {}
    orders = [(f.get("date"), ref, f) for ref, f in facts.items() if ref.startswith("po:")
              and isinstance(f.get("unit_price"), (int, float)) and isinstance(f.get("date"), str)]
    if not orders:
        return None
    try:
        newest = datetime.date.fromisoformat(max(o[0] for o in orders)[:10])
    except ValueError:
        return None
    start = _months_back(newest, 12).isoformat()
    best = None
    for part in case.get("parts", []):
        rows = sorted((d, ref, f["unit_price"]) for d, ref, f in orders
                      if f.get("supplier") == sid and f.get("part") == part)
        before = [r for r in rows if r[0] < start]
        window = before[-1:] + [r for r in rows if r[0] >= start]
        if len(window) < 2 or window[-1][0] < _months_back(newest, 3).isoformat():
            continue
        runs = []  # [first row, length] per price level
        for row in window:
            if runs and math.isclose(runs[-1][0][2], row[2]):
                runs[-1][1] += 1
            else:
                runs.append([row, 1])
        kept = [run for i, run in enumerate(runs) if run[1] > 1 or i in (0, len(runs) - 1)]
        path = []
        for run in kept:
            if not path or not math.isclose(path[-1][2], run[0][2]):
                path.append(run[0])
        if not path or not math.isclose(window[-1][2], path[-1][2]):
            continue
        path[-1] = window[-1]  # today's level by its newest order
        trough = min(range(len(path)), key=lambda i: (path[i][2], -i))
        path = path[trough:]  # from the last low to today's price, rising all the way
        if any(b[2] < a[2] for a, b in zip(path, path[1:], strict=False)):
            continue
        if len(path) > PATH_MAX_IDS:
            path = path[:1] + path[-(PATH_MAX_IDS - 1):]
        if len(path) < 2 or path[0][2] <= 0:
            continue
        rise = path[-1][2] / path[0][2] - 1
        if rise >= PATH_MIN_RISE and (best is None or rise > best["rise"]):
            best = {"part": part, "refs": [r[1] for r in path], "prices": [r[2] for r in path],
                    "dates": [r[0][:10] for r in path], "rise": rise}
    return best


def order_path_sentence(package, case, threads, sid):
    """Round 1 only: the rising order path as one checked claim plus its framing, else ""."""
    events = threads.get(sid) or []
    if package is None or any(m.get("from") == "buyer" for m in events):
        return ""
    path = order_path(package, case, sid)
    if not path:
        return ""
    cited = {ref for evs in threads.values() for body in _buyer_bodies(evs) for ref in re.findall(r"po:\S+", body)}
    if cited & set(path["refs"]):
        return ""
    claim = {"type": "price_history", "refs": path["refs"], "asserted": {"prices": path["prices"]}}
    facts = {**getattr(package, "facts", {}), **case.get("facts", {})}
    if ClaimChecker(case, facts).check(sid, [claim])[0]["result"] != "verified":
        return ""
    steps = [f"EUR {price:.2f} ({ref})" for price, ref in zip(path["prices"], path["refs"], strict=True)]
    middle = f" through {', '.join(steps[1:-1])}" if len(steps) > 2 else ""
    return (f"Our orders with you for {path['part']} rose from {steps[0]}{middle} to {steps[-1]} between "
            f"{path['dates'][0]} and {path['dates'][-1]}. That rise is already in our price.")


def _counters(events):
    """[(index, unit_price)] of every supplier message carrying a term sheet."""
    return [(i, standing(events[:i + 1])[1].get("unit_price")) for i, e in enumerate(events)
            if e.get("from") != "buyer" and e.get("term_sheets")]


def holding(events):
    """The supplier moved less than `HOLD_SHARE` of its price in answer to our latest sheet."""
    counters = [(i, p) for i, p in _counters(events) if isinstance(p, (int, float)) and p > 0]
    if len(counters) < 2:
        return False
    (before, previous), (_, last) = counters[-2], counters[-1]
    moved = any(e.get("from") == "buyer" and e.get("term_sheets") for e in events[before + 1:counters[-1][0]])
    return moved and previous - last < HOLD_SHARE * previous


def floor_question_due(case, threads, sid):
    """Once per case, to an open supplier that holds, with moves left after its answer."""
    events = threads.get(sid) or []
    if any(FLOOR_QUESTION in body for evs in threads.values() for body in _buyer_bodies(evs)):
        return False
    return (bool(events) and latest(events).get("status") == "open" and moves_left(case, events) >= 3
            and holding(events))


def named_floors(events):
    """Prices a supplier named in a floor sentence ("the lowest we can go", "no further than") in a
    reply to our floor question."""
    asked, out = False, []
    for message in events:
        if message.get("from") == "buyer":
            asked = asked or FLOOR_QUESTION in (message.get("body") or "")
            continue
        if not asked:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n", message.get("body") or ""):
            if _FLOOR_WORDS.search(sentence):
                out += [float(p.replace(",", ".")) for p in _PRICE.findall(sentence)]
    return out


def floor_reached(events):
    """The supplier named a floor equal to its standing price: stop probing below it."""
    price = standing(events)[1].get("unit_price") if events else None
    return isinstance(price, (int, float)) and any(abs(floor - price) <= 0.005 for floor in named_floors(events))


def near_close_sheet(case, events, sid):
    """One sheet on the supplier's own standing terms `NEAR_CLOSE_SHARE` below its OPEN offer, never
    below our own previous ask on that package; None after a final offer, once sent, or without room."""
    if not events or latest(events).get("status") != "open" or moves_left(case, events) < 2:
        return None
    if any(NEAR_CLOSE_TEXT in body for body in _buyer_bodies(events)):
        return None
    _, terms, std, missing = known_offer(case, events)
    if missing or not isinstance(terms.get("unit_price"), (int, float)):
        return None
    resolved = {**std, **case.get("fixed_terms", {}), **terms}
    if any(f not in resolved for f in case["negotiable"]):
        return None
    sheet = {f: resolved[f] for f in case["negotiable"]}
    price = terms["unit_price"]

    def package(t):
        return {f: {**std, **case.get("fixed_terms", {}), **t}.get(f) for f in case["negotiable"] if f != "unit_price"}

    ours = [s.get("terms", s).get("unit_price") for e in events if e.get("from") == "buyer"
            for s in e.get("term_sheets") or [] if package(s.get("terms", s)) == package(sheet)]
    ask = max([round(price * (1 - NEAR_CLOSE_SHARE), 2), *[p for p in ours if isinstance(p, (int, float))]])
    if ask >= price - 0.005:
        return None
    sheet["unit_price"] = ask
    if not must_haves_ok(case, sheet, std):
        return None
    if not decision_cost(case, sheet, std, sid) < decision_cost(case, terms, std, sid):
        return None  # e.g. below a relationship supplier's health floor
    return sheet
