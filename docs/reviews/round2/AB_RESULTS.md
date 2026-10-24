# Variant tests, 25 October (round 2 and the case agents)

Rule used: a variant goes live only when the local replica shows a noticeable difference or the
evidence predicts a noticeable change; live A/B arms are current `main` versus `main` plus only the
variant.

| Variant | Source | Replica | Hosted A/B | Decision |
|---|---|---|---|---|
| Mirror a final offer's cheaper alternative only with a covering rival | round 2 #1 | n/a | P18/P08/P11, 2 passes per arm: equivalent; 9 of 9 hosted mirrors agreed | keep current ([AB_MIRROR.md](AB_MIRROR.md)) |
| Cite the last price rise when our order price held all year | case agent P10/P04 | identical | P10: 0.671 x3 -> 0.694 / 0.686 / 0.718 | **merged** (13f781d) |
| One held sheet once a lone supplier imposes its index clause | case agent P18/P08 | identical | P08: 0.673 -> 0.855 x3; P18: 0.560 -> 0.707 x3 | **merged** (3b94da9) |
| No index-fall claims to a hedged supplier | case agent P18/P08 | n/a | 12 of 12 hosted citations drew "That is not our reality" | **merged** (b3482f8) |
| Half step after a supplier stands still | round 2 #3 | identical (0.828) | not run | not merged |
| One payment term per message (the longest) | case agent P10/P04 | worse: 0.828 -> 0.816 (P05 -0.03, P07 -0.03, P18 -0.07) | not run | not merged |
| RFQ "auction ratchet" (show an agreement to the other bidders first) | case agent P07/P05 | identical (never fires) | not run: expected +0.01-0.03, inside noise | not merged |
