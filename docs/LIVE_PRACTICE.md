# Hosted practice improvement log

Live practice is authorized for team **winsortf**. These are Saturday practice results; Sunday's final uses unseen suppliers. The public [live board](https://arena.tacto.ai/live) reports each team's best result per case, so its mean is selected across attempts, not a fresh-run estimate.

The first complete pass used policy `c191073`, Sol at low effort, and four concurrent cases. It produced ten valid deals, no submission errors, a **0.49963 mean**, and **$0.1993 estimated provider usage**. The estimate is not a billing reconciliation. See [the complete baseline report](results/live-practice-2026-10-24.json) and the [dashboard](WEB_UI.md).

| Case | Baseline score |
|---|---:|
| P01 | 0.79894 |
| P02 | 0.27676 |
| P03 | 0.90338 |
| P04 | 0.86045 |
| P05 | 0.43618 |
| P07 | 0.42881 |
| P08 | 0.23063 |
| P10 | 0.59368 |
| P11 | 0.36874 |
| P18 | 0.09869 |

Replay analysis found missing evidence rather than provider failures. The model saw only the case's direct references; it missed supplier correspondence about material hedges, pricing approval, capacity and financing. Scripts ignored economic claims in supplier emails. Their cost ceiling also allowed a higher opening unit price when a longer contract covered more demand.

The next revision retrieves a bounded supplier dossier (including the Sunday overlay), routes cost claims and semantic alternatives to the model, retains that context through the negotiation, and caps the initial scripted unit price below the supplier's quote. Related regression checks run locally without environment credentials. These changes use public case data and supplied evidence; live scores and private reservation prices never enter the runtime policy.

Iteration results will be appended after completion. Run reports include source hashes, actual wall times, provider accounting, replay links and the practice quota before/after each batch. The development campaign reserves at most $60 across these hosted reports. Practice sessions are separate from the final runner and must finish before final launch.
