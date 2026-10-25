"""A per-case, per-supplier bank of pre-verified facts and the pace at which messages spend them.

An earlier team on this arena (same practice cases) found that a code-priced supplier gives one cut per
NEW verified fact it takes and holds when nothing new comes: round 1 carries every strong fact (within
the arena's three claims per message), each later e-mail exactly one new fact, and an e-mail with neither
a fact nor movement drew a hold or a final offer. Facts a supplier shrugged off ("does not change our
calculation") become last-resort fillers. Every bidder of an RFQ is its own negotiation, so what was
cited is tracked per supplier.

Each entry carries the ready sentence the arena checks (the checked figure next to its data ID) and is
checked with `ClaimChecker` before it may be sent, so a planned claim cannot come back contradicted.
"""

import math
import re
from dataclasses import dataclass, field

from ..cost import RELATIONSHIP_GOALS
from ..evaluation import ClaimChecker
from ..play import acceptable_offer, live_threads, standing
from .llm import ID_RE

# Data IDs the arena checks as claims; doc:/ctr: are context and pass untouched.
CHECKED_PREFIXES = ("idx:", "fc:", "sc:", "cost:", "quote:", "offer:", "po:", "qn:", "news:")
ROUND_ONE_CLAIMS = 3  # the arena checks at most three claims per message
ROUND_ONE_INDEX_CLAIMS = 2
MAX_CLAIM_IDS = 8  # a 19-ID claim crashed the arena (HTTP 500)  # two different series in round 1; one per later e-mail
# weight x fall below this many percentage points is a filler: P03 -0.3 % labour was worth EUR 0.02
WEAK_INDEX_POINTS = 0.25
WEAK_FORECAST_PCT = 2.0
# A cost driver up this much over twelve months makes the increase justified: falls of other series then
# read as a cherry-picked calculation (hosted P02, copper +47.7 %), so they are fillers only.
JUSTIFIED_RISE_PCT = 10.0
CASH_STRESS_TOPICS = {"credit_watch", "credit_downgrade", "restructuring", "short_time_work", "insolvency"}
# News that the supplier needs work: its own idle capacity is our leverage (the Sunday release adds such news
# for three suppliers; the arena verifies a news claim when the news is about the supplier it is cited to).
NEED_WORK_TOPICS = {"free_capacity", "customer_loss", "short_time_work"}
CASH_STRESS_WORDS = ("cash flow", "liquidity", "cannot give up margin", "squeeze our margin", "cash is tight")
SHRUG_WORDS = ("not change our calculation", "not alter", "does not change our", "do not change our",
               "don't change our", "doesn't change our")
# Order of strong facts (measured take-rates: should-cost and rival quotes > forecast > index > records).
# The supplier's own rising order-price path leads (`price_path`).
PRIORITY = {"price_path": 0, "should_cost": 1, "rival_quote": 2, "volume_change": 3, "supplier_news": 3,
            "index_change": 4,
            "quality_record": 5, "delivery_record": 5}
# "secured ... until" alone also matched "secured additional capacity until March" (review round 3, L9).
HEDGED = re.compile(r"(covered|bought|hedged)[^.]{0,40}(fixed prices?|until)|secured[^.]{0,40}fixed prices?"
                    r"|fixed prices? until", re.IGNORECASE)
SENTENCE_END = re.compile(r"((?<=[.!?])\s+)")
CLOSING = re.compile(r"\n\s*(?:kind regards|best regards|regards|best|sincerely|yours)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Fact:
    key: str                # one per key and supplier: an index series counts once, as does a forecast
    kind: str               # the arena's claim type
    refs: tuple
    figure: str             # the checked figure as written in the sentence
    sentence: str
    strong: bool
    rank: float = 0.0       # within a kind, lower first
    claim: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def priority(self):
        return PRIORITY.get(self.kind, 9), self.rank, self.key


def checked_ids(text):
    return [ref for ref in ID_RE.findall(text or "") if ref.startswith(CHECKED_PREFIXES)]


def fact_key(ref):
    if ref.startswith("idx:"):
        return ref.split("@")[0]
    if ref.startswith("fc:"):
        return "fc:"
    return ref


class FactBank:
    """Facts by supplier from public data; `assign` returns the facts the next message to it carries."""

    def __init__(self, package=None):
        self.package = package
        self._static = {}

    # ----- the bank -------------------------------------------------------------------------------
    def facts(self, case):
        return {**(getattr(self.package, "facts", None) or {}), **case.get("facts", {})}

    def lookup(self, case, ref):
        return case.get("facts", {}).get(ref) or (self.package.lookup(ref) if self.package is not None else None)

    def entries(self, case, threads, sid):
        """Every verified fact for `sid` now: the static ones of the case plus current rival offers and
        a should-cost below the supplier's standing price."""
        key = (case.get("case_id"), sid)
        if key not in self._static:
            self._static[key] = self._build(case, sid)
        out = list(self._static[key])
        try:
            price = standing(threads[sid])[1].get("unit_price") if threads.get(sid) else None
        except StopIteration:
            price = None
        out += self._should_cost(case, sid, price)
        out += self._case_quotes(case, sid, price)
        out += self._rival_offers(case, threads, sid)
        return sorted((f for f in out if self._verified(case, threads, sid, f)), key=lambda f: f.priority)

    def _build(self, case, sid):
        out = []
        for provider in (self._price_path, self._forecast, self._news, self._index_falls, self._scorecard):
            try:
                out += provider(case, sid)
            except Exception:  # an older or partial package: fewer facts, never a crash
                continue
        return out

    def _price_path(self, case, sid):
        """Slot for the supplier's own rising order-price path (rule: it leads round 1 when our orders
        with it rose >= 3 % in twelve months), from `levers.order_path`; round 1 only."""
        from .levers import order_path, order_path_sentence

        sentence = order_path_sentence(self.package, case, {sid: []}, sid)
        path = order_path(self.package, case, sid) if sentence else None
        if not path:
            return []
        claim = {"type": "price_history", "refs": list(path["refs"]), "asserted": {"prices": path["prices"]}}
        return [Fact("po:path", "price_path", tuple(path["refs"]), "", sentence, True, claim=claim)]

    def _forecast(self, case, sid):
        facts, best = self.facts(case), None
        for part in case.get("parts", []):
            refs = sorted(ref for ref, fact in facts.items() if ref.startswith("fc:") and fact.get("part") == part)
            if not refs:
                continue
            ref = refs[-1]
            change, volume = facts[ref].get("change_pct"), facts[ref].get("volume_per_year") or 1
            if type(change) in (int, float) and math.isfinite(change) and change > 0:
                if best is None or change * volume > best[0]:
                    best = (change * volume, part, change, ref)
        if best is None:
            return []
        _, part, change, ref = best
        return [Fact("fc:", "volume_change", (ref,), f"{change:g}%",
                     f"The forecast for {part} increased by {change:g}% ({ref}); "
                     "please reflect this additional business in your price.",
                     change >= WEAK_FORECAST_PCT, -change,
                     {"type": "volume_change", "refs": [ref], "asserted": {"change_pct": change}})]

    def _news(self, case, sid):
        """The newest news that this supplier needs work (idle capacity, a lost customer, short-time work)."""
        found = sorted((fact.get("date") or "", ref, fact) for ref, fact in self.facts(case).items()
                       if ref.startswith("news:") and fact.get("supplier") == sid
                       and fact.get("topic") in NEED_WORK_TOPICS and fact.get("headline"))
        if not found:
            return []
        _, ref, fact = found[-1]
        headline = fact["headline"].rstrip(".")
        return [Fact("news:need", "supplier_news", (ref,), "",
                     f"We read that {headline} ({ref}); our volume fills capacity you have free, "
                     "and we expect that to show in your price.", True,
                     claim={"type": "supplier_news", "refs": [ref]})]

    def _index_falls(self, case, sid):
        package = self.package
        if package is None or not hasattr(package, "index_falls"):
            return []
        if "_index_falls" not in case:
            try:
                case["_index_falls"] = package.index_falls(case)
            except Exception:
                case["_index_falls"] = []
        try:
            justified = case["buyer"].get("category_goal") in RELATIONSHIP_GOALS and self._justified_rise(case)
        except Exception:
            justified = False
        if self._hedged(case, sid):
            return []  # "That is not our reality": hosted P08 answered all 12 index citations so (hedged material)
        out = []
        for weight, series, start, end, change in case["_index_falls"]:
            first, last = package.lookup(start), package.lookup(end)
            if not first or not last:
                continue
            points = abs(change) * (weight or 0)
            text = f"{change:.2f}%"
            out.append(Fact(f"idx:{series}", "index_change", (start, end), text,
                            f"{series} fell from {round(first['value'], 4)} ({start}) to "
                            f"{round(last['value'], 4)} ({end}), a change of {text}.",
                            points >= WEAK_INDEX_POINTS and not justified, -points,
                            {"type": "index_change", "refs": [start, end],
                             "asserted": {"change_pct": round(change, 2)}}))
        return out

    def _hedged(self, case, sid):
        """The supplier's own letters or news say its material is covered at fixed prices or hedged until a
        later date: its costs do not follow the index, so an index fall is no argument to it."""
        facts = self.facts(case)
        for ref, fact in facts.items():
            if ref.startswith(("doc:", "news:")) and fact.get("supplier") == sid:
                try:
                    text = self.package.document(ref) if self.package is not None else fact.get("body", "")
                except Exception:
                    text = fact.get("body", "") or ""
                if HEDGED.search(text or ""):
                    return True
        return False

    def _justified_rise(self, case):
        """A cost driver or index-clause series of the case rose >= JUSTIFIED_RISE_PCT over twelve months."""
        package, series = self.package, set(case["buyer"].get("index_paths", {}))
        series |= {c["series"] for c in case.get("term_options", {}).get("index_clause") or [] if c}
        if package.db and "cost_drivers" in package.tables():
            for part in case.get("parts", []):
                series |= {row["series_id"] for row in package.query(
                    "SELECT series_id FROM cost_drivers WHERE part_id = ?", (part,))}
        for name in series:
            refs = package.ids(f"idx:{name}@")
            if not refs:
                continue
            newest = package.lookup(refs[-1])
            month = newest["month"]
            previous = package.lookup(f"idx:{name}@{int(month[:4]) - 1}{month[4:]}")
            if previous and previous.get("value") and (
                    newest["value"] / previous["value"] - 1 >= JUSTIFIED_RISE_PCT / 100):
                return True
        return False

    def _scorecard(self, case, sid):
        """Quality and delivery records, only when the supplier's latest scorecard is amber or red: a green
        supplier answered them with "they do not alter" and cut less all game. The latest scorecard's
        record leads (its ppm where quality is negotiated, else its on-time delivery); the other record
        from the previous quarter and recent quality notifications (cited without a figure: the arena
        checks the data's own value) are fillers."""
        facts = self.facts(case)
        refs = sorted(ref for ref, fact in facts.items() if ref.startswith(f"sc:{sid[4:]}@")
                      and fact.get("supplier") == sid)
        if not refs or facts[refs[-1]].get("colour") not in {"amber", "red"}:
            return []
        quality = ("ppm_commitment" in case.get("negotiable", [])
                   or case["buyer"].get("category_goal") == "quality_first")
        kinds = ["quality_record", "delivery_record"] if quality else ["delivery_record", "quality_record"]
        out = []
        for ref, kind, strong in ((refs[-1], kinds[0], True), (refs[-2] if len(refs) > 1 else None, kinds[1], False)):
            row = facts.get(ref) if ref else None
            key = "ppm" if kind == "quality_record" else "otd_pct"
            if not row or type(row.get(key)) not in (int, float):
                continue
            figure = f"{row[key]:g}" + ("%" if key == "otd_pct" else "")
            text = f"{figure} ppm" if key == "ppm" else f"on-time delivery of {figure}"
            when = "latest" if strong else "previous"
            out.append(Fact(ref, kind, (ref,), figure, f"Your {when} scorecard records {text} ({ref}).", strong,
                            0, {"type": kind, "refs": [ref], "asserted": {key: row[key]}}))
        return out + self._notifications(case, sid, facts)

    @staticmethod
    def _notifications(case, sid, facts):
        """Up to two filler claims of at most `MAX_CLAIM_IDS` quality notifications of the last twelve
        months, one part each."""
        months = [ref.split("@")[1] for ref in facts if ref.startswith("idx:") and "@" in ref]
        if not months:
            return []
        latest = max(months)
        since = f"{int(latest[:4]) - 1}{latest[4:7]}"
        out = []
        for part in case.get("parts", []):
            rows = sorted(((fact.get("date", ""), ref) for ref, fact in facts.items() if ref.startswith("qn:")
                           and fact.get("supplier") == sid and fact.get("part") == part
                           and fact.get("date", "") >= since), reverse=True)
            for start in range(0, min(len(rows), 2 * MAX_CLAIM_IDS), MAX_CLAIM_IDS):
                refs = tuple(sorted(ref for _, ref in rows[start:start + MAX_CLAIM_IDS]))
                out.append(Fact(refs[0], "quality_record", refs, "",
                                f"Our quality notifications {', '.join(refs)} record defective deliveries of {part}.",
                                False, 100 + start, {"type": "quality_record", "refs": list(refs)}))
        return out[:2]

    def _should_cost(self, case, sid, price):
        """A should-cost only where it is below the supplier's standing price (above it, it supports the
        supplier); a supplier-specific estimate first. Only per-unit estimates: one number per sentence."""
        if not isinstance(price, (int, float)) or price <= 0:
            return []
        facts, out = self.facts(case), []
        for part in case.get("parts", []):
            number = part.split(":", 1)[-1]
            for ref in (f"cost:{number}-{sid[4:]}", f"cost:{number}"):
                row = facts.get(ref)
                if not row or row.get("part") != part or (row.get("price_unit") or 1) != 1:
                    continue
                cost = row.get("unit_cost")
                if type(cost) in (int, float) and 0 < cost < price:
                    figure = f"EUR {cost:g}"
                    out.append(Fact(ref, "should_cost", (ref,), figure,
                                    f"Our should-cost estimate for {part} is {figure} per unit ({ref}), "
                                    "below your price.", True, cost / price,
                                    {"type": "should_cost", "refs": [ref], "asserted": {"unit_cost": cost}}))
                    break
        return out[:1]

    def _case_quotes(self, case, sid, price):
        """The case's own competing quotes (another supplier, same part) below the standing price."""
        if not isinstance(price, (int, float)) or price <= 0:
            return []
        refs = set(case.get("refs", [])) | {(case["buyer"].get("alternative") or {}).get("ref")}
        out = []
        for ref in sorted(r for r in refs if isinstance(r, str) and r.startswith("quote:")):
            row = self.lookup(case, ref)
            if (not row or row.get("supplier") == sid or row.get("part") not in case.get("parts", [])
                    or (row.get("price_unit") or 1) != 1):
                continue
            quoted = row.get("unit_price")
            if type(quoted) in (int, float) and 0 < quoted < price:
                out.append(self._rival(ref, quoted))
        return out

    def _rival_offers(self, case, threads, sid):
        """The cheapest feasible standing offer of another bidder, if cheaper for us than this one's."""
        if sid not in threads:
            return []
        current = acceptable_offer(case, threads[sid], sid)
        price = standing(threads[sid])[1].get("unit_price")
        rivals = [(accepted[1], other, accepted[0]) for other in live_threads(threads) if other != sid
                  if (accepted := acceptable_offer(case, threads[other], other))
                  and (not current or accepted[1] < current[1])
                  # its unit price must be below this one's: else the claim says the market charges more (B3)
                  and (price is None or standing(threads[other])[1].get("unit_price", math.inf) < price)]
        if not rivals:
            return []
        _, other, ref = min(rivals)
        return [self._rival(ref, standing(threads[other])[1]["unit_price"])]

    @staticmethod
    def _rival(ref, price):
        figure = f"EUR {price:g}"
        return Fact(ref, "rival_quote", (ref,), figure,
                    f"A valid competing offer is {figure} per unit ({ref}); please improve your complete package.",
                    True, price, {"type": "rival_quote", "refs": [ref], "asserted": {"unit_price": price}})

    def _verified(self, case, threads, sid, fact):
        offers = {sheet.get("term_sheet_id"): sheet.get("terms") for events in threads.values() for message in events
                  if message.get("from") != "buyer" for sheet in message.get("term_sheets") or []
                  if isinstance(sheet, dict) and sheet.get("term_sheet_id")}
        try:
            result = ClaimChecker(case, self.facts(case)).check(sid, [fact.claim], offers)
        except Exception:
            return False
        return bool(result) and result[0]["result"] == "verified"

    # ----- pacing ---------------------------------------------------------------------------------
    @staticmethod
    def used(threads, sid):
        """Fact keys already cited: per case, as the arena counts them, except rival quotes and offers,
        which count once per supplier (an index series and a forecast count once)."""
        return {fact_key(ref) for other, events in threads.items() for message in events
                if message.get("from") == "buyer" for ref in checked_ids(message.get("body", ""))
                if other == sid or not ref.startswith(("quote:", "offer:"))}

    @staticmethod
    def shrugged(threads, sid):
        """Claim kinds whose citation drew "does not change our calculation" from this supplier."""
        events, kinds, pending = threads.get(sid, []), set(), []
        for message in events:
            if message.get("from") == "buyer":
                pending = checked_ids(message.get("body", ""))
                continue
            if pending and any(w in (message.get("body") or "").lower() for w in SHRUG_WORDS):
                kinds |= {ref.split(":", 1)[0] for ref in pending}
            pending = []
        return kinds

    def cash_short(self, case, threads, sid):
        """A supplier that "cannot squeeze its margin" cuts one step per e-mail however much it is shown."""
        facts = self.facts(case)
        if any(ref.startswith("news:") and fact.get("supplier") == sid and fact.get("topic") in CASH_STRESS_TOPICS
               for ref, fact in facts.items()):
            return True
        return any(message.get("from") != "buyer" and any(w in (message.get("body") or "").lower()
                                                         for w in CASH_STRESS_WORDS)
                   for message in threads.get(sid, []))

    def assign(self, case, threads, sid):
        """The facts the next message to `sid` carries: in its first message every strong fact (at most
        three claims, two index series; one fact for a cash-short supplier), later exactly one new fact -
        the strongest left, else a filler (weak or shrugged-off kinds)."""
        try:
            entries = self.entries(case, threads, sid)
        except Exception:
            return []
        used = self.used(threads, sid)
        shrugged = self.shrugged(threads, sid)
        fresh = [f for f in entries if f.key not in used and not any(fact_key(r) in used for r in f.refs)]
        strong = [f for f in fresh if f.strong and f.refs[0].split(":", 1)[0] not in shrugged]
        first = not any(message.get("from") == "buyer" for message in threads.get(sid, []))
        if first:
            if self.cash_short(case, threads, sid):
                lead = [f for f in strong if f.kind == "quality_record"] or strong
                return lead[:1]
            out, indices = [], 0
            for fact in strong:
                if len(out) >= ROUND_ONE_CLAIMS:
                    break
                if fact.kind == "index_change":
                    if indices >= ROUND_ONE_INDEX_CLAIMS:
                        continue
                    indices += 1
                out.append(fact)
            return out
        if strong:
            return strong[:1]
        fillers = [f for f in fresh if f not in strong]
        return fillers[:1]

    def has_any(self, case, threads, sid):
        try:
            return bool(self.entries(case, threads, sid))
        except Exception:
            return False

    # ----- enforcement ----------------------------------------------------------------------------
    @staticmethod
    def enforce(body, assigned):
        """Keep sentences citing only assigned facts (with the checked figure), drop any other sentence
        with a checked data ID, then add each assigned fact's sentence the body lacks."""
        allowed = {ref: fact for fact in assigned for ref in fact.refs}
        parts = SENTENCE_END.split(body or "")
        kept, present = [], set()
        for index in range(0, len(parts), 2):
            sentence, gap = parts[index], parts[index + 1] if index + 1 < len(parts) else ""
            ids = checked_ids(sentence)
            if ids:
                facts = {allowed.get(ref) for ref in ids}
                flat = sentence.replace(" ", "")
                if None in facts or any(f.figure.replace(" ", "") not in flat or not set(f.refs) <= set(ids)
                                        for f in facts):
                    if "\n" in gap and kept:
                        kept[-1] = kept[-1].rstrip(" ") + gap  # keep the paragraph break
                    continue
                present |= {f.key for f in facts}
            kept.append(sentence + gap)
        text = re.sub(r"\n\s*\n(\s*\n)+", "\n\n", "".join(kept)).strip()
        missing = " ".join(f.sentence for f in assigned if f.key not in present)
        if not missing:
            return text
        closing = list(CLOSING.finditer(text))
        if closing:
            at = closing[-1].start()
            return f"{text[:at].rstrip()}\n\n{missing}{text[at:]}"
        return f"{text} {missing}".strip()
