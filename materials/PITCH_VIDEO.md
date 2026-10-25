# Tacto Negotiation Arena: our buyer agent in two minutes

Pitch video script, about 1:55 at a calm pace (roughly 280 words of narration). Visual cues are in brackets.

---

## 0:00–0:12 · Hook

[Arena replay: a supplier's email announcing a price increase]

> A supplier writes: prices go up nine and a half percent. A human buyer has an afternoon. Our agent has sixty seconds per message, five waves, ten cases at once. And it has to get the deal that's actually cheapest, not just the lowest unit price.

## 0:12–0:30 · The insight

[Total-cost formula; the cost breakdown chips from a deal page]

> The arena doesn't score price alone. It scores total cost: payment terms, freight, lead time, quality, index clauses, even the supplier's health. So our agent never trades on price alone. Every offer it sends is a complete package priced on that total cost.

## 0:30–0:55 · How it decides

[Full decision tree tab with branches lighting up]

> The core is a routed buyer. Scripts go first: openings, concessions, RFQ comparisons, closing. They're deterministic, so they can't misread an email. Only when a supplier writes something new, like a condition, a question or a counter-claim, does Claude Opus decide. Even then, one shared gate checks every move. It enforces the concession schedule, never counters a final offer, and never offers a bidder more than a rival already offers.

## 0:55–1:20 · What we learned from the suppliers

[Score progression chart from 0.63 to 0.79; short snippets]

> We treated practice as experiments. Countering a final offer made suppliers walk away, so we stopped. Sending a final offer's own cheaper alternative back to the supplier: all nine times we tried it, they agreed. Holding a single sheet against an imposed index clause took one case from 0.67 to 0.86. Over about 300 hosted sessions, our single-run average rose from 0.63 to 0.79.

## 1:20–1:45 · Every decision is explainable

[Deal journal: chat view, then click a step and the code excerpt and messages highlight]

> The jury reads replays, so we built our own. Every negotiation is stored as a chat with timestamps. Click any step to see which branch decided it, why, the exact line of code, and the supplier message it answered.

## 1:45–1:55 · Close

[Team name on screen; terminal running the final command]

> Fast where it can be, careful where it must be, and explainable at every step. That's our buyer. Team winsortf.

---

## Production notes

- Record the scenes from `docs/index.html` (#deals, #tree, one deal) and the arena replays, and keep each shot under 8 seconds.
- Every number here comes from our hosted runs: the 0.63 → 0.789 progression, 9 of 9 mirrored alternatives agreed, P08 0.673 → 0.855, and 297 stored sessions. Swap in the final score if you record after 13:30.
- To trim to 90 seconds, cut the "What we learned" section to its first and last sentences.
