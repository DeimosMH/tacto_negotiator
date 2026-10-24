# Hosted A/B: mirroring a cheaper alternative from a final offer (round 2 item #1)

The round 2 review proposed mirroring only when another supplier's acceptable offer is at least as
good as the mirrored supplier's own final offer (variant B). The policy at `22b6308` mirrors also
to a sole supplier (variant A). Both ran live on 25 October, two passes each over P18, P11 and P08
(12 sessions; reports `docs/results/live-practice-2026-10-25-mirror-{A,B}-{1,2}.json`).

| Case | A (mirror) | B (review) |
|---|---|---|
| P18 | 0.632, 0.587 (mirrored in run 2) | 0.587, 0.587 |
| P08 | 0.726, 0.749 | 0.744, 0.744 |
| P11 | 0.71, 0.71 | 0.71, 0.71 |

- The one mirrored session scored 0.587, the same as B accepting the final offer directly.
- Across all 9 hosted sessions that mirrored (24-25 October), the supplier accepted every time; none
  walked away. The feared walk-away did not occur.
- The gain is also about zero on these cases: their alternatives differ by a few cents.

**Decision:** keep A (safe in 9 of 9, may gain where an alternative is clearly cheaper). The variants are
equivalent within run-to-run noise on the cases tested.
