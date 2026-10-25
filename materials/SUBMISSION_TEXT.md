# Submission text

Copy-paste fields for the organisers' platform. Limits: architecture 800 characters, pitch 140.

## Your Architecture and Innovation

**Routed buyer:** each move comes from a script, Claude Opus 5.5 or a closing fallback.

- **Scripts** do the maths: full cost C (price, payment, freight, index risk), complete term sheets that keep must-haves, bounded concessions, accept/walk.
- **Claude** handles prose. A validator gates every move: real IDs, must-haves, price limits.
- **Tuned for the mean, not the max:** rules take the best average score over many runs, not the deepest cut a supplier might reject, because a new supplier may behave differently.
- **Readable decision tree:** each node names the code that runs it, and every deal logs its path, so a human or an LLM can change a rule fast.
- **Facts, not bluffs:** claims cite only real contract, invoice or index IDs. Index clauses and rival quotes become leverage.

## One-line pitch

A buyer that does the maths in code and the talking in Claude, and never cites a fact it cannot trace to real data.

### Alternatives (character count in brackets)

1. Code does the maths, Claude does the talking: every offer priced on full cost, every claim backed by a real document. (117)
2. A procurement buyer that counts every euro of full cost, keeps every must-have and cites only facts it can prove. (113)
3. We negotiate whole packages, not prices: payment, freight, index clauses and supplier health, all priced in euros. (114)
4. Scripts for the maths, Claude for the prose, one validator in between: no move goes out unchecked. (98)
5. Our buyer reads the contracts, invoices and indices, then turns them into leverage that suppliers can't argue with. (115)
6. Hard numbers, soft words: a buyer agent that negotiates like a category manager with a calculator. (98)


### For non-technical readers (simple and catchy)

7. The buyer that does its homework before it haggles. (51)
8. It reads the fine print, so you get the better price. (53)
9. Tough on price, fair to suppliers, honest with the facts. (57)
10. A negotiator that never bluffs and never gets tired. (52)
11. Knows every contract, checks every number, gets the deal. (57)
12. Your best buyer, on every deal, at the same time. (49)


### In the spirit of 7 (homework before haggling)

13. The buyer that reads the contract before it names a price. (58)
14. Homework first, haggling second. (32)
15. It does the homework, so the supplier can't do the bluffing. (60)
16. The buyer that checks the numbers before it shakes hands. (57)
17. Prepared, not pushy: the buyer that knows the facts before it haggles. (70)
18. The buyer that comes to every negotiation with its homework done. (65)


## Architecture and Innovation: other versions

### B — technical, compact (773 characters)

**Code for the maths, Claude for the words, a validator in between.**

- Scripts price every package on full cost (price, payment, freight, index risk), keep must-haves and decide accept or walk.
- Claude writes and reads the prose.
- **The validator** is one gate for every move, from script or Claude: complete legal terms, must-haves kept, price within our concession schedule and only real IDs cited. A failed move falls back to a script.
- **Safe over greedy:** rules are tuned for the best average score, not the deepest cut, because an unseen supplier may react differently.
- **Change it in minutes:** a simple decision tree points to the code behind each node, and every deal logs its path.
- **Proof, not bluff:** claims cite real contract, invoice and index IDs.

### C — technical, problem → solution (652 characters)

**Problem:** an LLM alone haggles well but miscounts costs and invents facts. Rules alone are rigid.

**Our answer: a routed buyer.**
- Known situations go to tested scripts: full-cost maths, complete term sheets, must-haves, accept/walk.
- Open prose goes to Claude Opus 5.5.
- One validator gates every move (terms, must-haves, price limit, cited IDs); a failed move falls back to a script.
- We tune for the reliable average, not the risky maximum that a new supplier might reject.
- The decision tree is short and points to its code, so a person or an LLM can fix a rule in minutes; each deal shows its path.
- Every claim cites a real document ID.

### D — non-technical, simple (417 characters)

**Our buyer does its homework before it haggles.**

- It reads the contracts, invoices and market prices first.
- It counts the full cost of a deal, not just the price tag.
- It never makes up facts: every argument points to a real document.
- It aims for a good deal every time, not a lucky one, so a new supplier doesn't walk away.
- Its rules fit on one page, so we can see why it did something and change it fast.

### E — non-technical, catchy (520 characters)

**Hard numbers, friendly words.**

- **The calculator:** code works out what each deal really costs us.
- **The talker:** Claude writes the messages.
- **The referee:** checks every message before it goes out: right terms, a fair price, real facts.
- **The honest one:** it only argues with facts it can prove.
- **The steady one:** it goes for good deals every time, not risky record deals that make suppliers leave.
- **The open book:** every decision is traced on a simple map, so anyone can see why, and fix it fast.

## What "the validator" is (for questions)

`RoutedAgent.validate` in `src/tacto_negotiator/agents/routed.py` is the single gate that every move passes
through, whether a script, Claude or the fallback proposed it. Only a move it returns reaches the arena.

- **Accept:** only an offer that is still standing.
- **Walk away:** not while an acceptable offer stands, and not while rounds remain.
- **Send:**
  - every term sheet is complete, uses allowed options and keeps the must-haves;
  - every cited ID must be a real offer or data-package fact;
  - the price is moved onto our concession schedule (repriced rather than rejected);
  - never dearer than a rival's standing offer;
  - no new term sheet in reply to a final offer.
- **On failure:** if the move is rejected, a deterministic script move is used instead.

## Recommended final version (727 characters, pairs with pitch 7)

**Code for the maths, Claude for the words, a validator in between.**

- **Homework first:** before the first offer it reads the contracts, invoices, price indices and supplier documents.
- **Scripts** price every package on full cost (price, payment, freight, index risk) and decide accept or walk.
- **Claude** reads and writes the prose. Every move, from script or Claude, passes one validator: complete legal terms, must-haves, our price schedule, real IDs only. A rejected move falls back to a script.
- **Safe over greedy:** tuned for the best average score, not the deepest cut an unseen supplier might reject.
- **Change it in minutes:** a simple decision tree names the code behind each node; every deal logs its path.

Pitch: The buyer that does its homework before it haggles.
