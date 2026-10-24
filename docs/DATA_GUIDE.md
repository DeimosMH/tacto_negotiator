# Turn the data into useful evidence

The package is a frozen procurement world, not a live ERP connection. CSVs are the master copy; world.sqlite mirrors their 24 tables. facts.json contains 10,356 citable fact IDs with the values the arena checks. There are 447 supplier records, including spend-only vendors and an old alias; 46 detailed suppliers; 60 parts; 46 contract texts; and 208 correspondence documents. The atlas derives table row counts and schemas from the actual files.

Sources: [data inventory](../starter_kit/data/README.md), [machine-readable schema](../starter_kit/data/datapackage.json), [known issues](../starter_kit/data/KNOWN_ISSUES.md), [company policies](../starter_kit/data/company.md).

## Three representations, three jobs

Use SQL for relationships and aggregation. Use lookup(ref) to retrieve the checked values of a citable ID. Use document(ref) or a document's package-relative path to read a contract, letter or note. A document fact may carry notice days and an asked price while its CSV row supplies the text path. DataPackage.document resolves its row index before reading the file and prevents leaving the package directory.

The optional MCP server exposes list_tables, query, lookup, list_ids, read_document and get_case. It is another interface to DataPackage, not a separate evidence database. query is SELECT/WITH-only with a default 200-row cap; a WITH expression does not bypass the read-only database. Aggregate in SQL or paginate deliberately instead of assuming a truncated response is the whole dataset.

## The main join paths

| Question | Follow these records | Watch for |
| --- | --- | --- |
| Who can supply the part? | parts → sourcing → suppliers → supplier_intel | Qualification status, tooling ownership and old supplier alias |
| What is the contractual baseline? | suppliers/contracts → contract_clauses → contracts/*.md | Validity, notice, index provisions and standard terms |
| What did we actually pay? | po_lines → goods_receipts and invoices | Partial receipts, multiple invoices, price_unit and stale prices |
| How reliable are deliveries? | po_lines → goods_receipts, plus scorecards | Requested-date versus confirmed-date OTD |
| Is quality deteriorating? | goods_receipts → quality_notifications, plus scorecards | Supplier-caused flags, rolling denominator, overdue 8D |
| What volume can we promise? | parts → forecasts → forecast_lines | One snapshot at a time; overlapping snapshots are not additive |
| Is a rival offer useful? | parts → rfqs → quotes → suppliers | Case eligibility, missing terms, quote validity and qualification |
| Does a claimed cost increase fit? | parts → cost_drivers → index_series → index_values | Relevant input, date window, lag and supplier hedging |
| Which trade might work? | suppliers → documents/news → correspondence/*.md | Cash, capacity, authority, relationships and noise |

The map's solid declared-join edges come directly from datapackage.json. Extra links such as price_conditions.event_id to negotiation_history.event_id and contract_clauses.series to index_series.id are labelled inferred. They are useful schema relationships, not runtime calls.

## Worked recipe: a price-increase case

First identify the part and supplier from the case. For Brandlhuber's casing situation, start with part:310010 and sup:70110, then read [the trigger letter](../starter_kit/data/correspondence/20260911-70110-P01.md), [the agreement](../starter_kit/data/contracts/4600070110.md) and recent supplier notes. Do not assume the presentation's example prices are the actual case's inputs.

```sql
SELECT d.id, d.date, d.kind, d.subject, d.file
FROM documents AS d
WHERE d.supplier_id = 'sup:70110'
ORDER BY d.date DESC;

SELECT series_id, weight
FROM cost_drivers
WHERE part_id = 'part:310010';

SELECT id, month, value
FROM index_values
WHERE series_id = 'PPI-D351'
  AND month BETWEEN '2025-08' AND '2026-08'
ORDER BY month;
```

Compute change = (later_value / earlier_value − 1) × 100 in code, preserve both IDs and place the number next to them in the email. The documented check requires one series, endpoints at most 12 months apart and a recent ending period. Select dates using the available observations; different indices end in different months.

The [August price-discussion note](../starter_kit/data/correspondence/20260824-70110-M1.md) says the supplier works from an old spreadsheet. That supports asking to review the calculation; it is not a guarantee of a lower offer. The [payment email](../starter_kit/data/correspondence/20260902-70110-M2.md) suggests faster payment might be valuable. Price a payment trade with total_cost before sending it. Other suppliers have forward purchases or frozen prices, so the same market fact may have little effect.

## Claims are not arbitrary citations

The supplied API checks at most three arguments per message. Relevant index pairs support percentage changes; eligible quotes and current session offers support competitive prices; scorecards support delivery/quality claims; two to eight same-part PO rows support price arguments; forecast, should-cost and supplier news have their own checked values; notice arguments pair a letter and contract. Context IDs such as parts and meeting notes do not automatically count as numerical checks.

The response reports verified, contradicted, irrelevant or duplicate. Verified means the checked data matches, not that the supplier must concede. A factual statement can be irrelevant to that supplier's current economics. Unknown IDs and wrong figures can cost trust. Keep both the numerical calculation and the reason the claim matters. Do not repeatedly spend the message's argument slots on IDs already used; rival quotes have a separate per-supplier reuse rule.

Source: [API “What counts”](../starter_kit/brief/api.md#what-counts).

## Common mistakes to avoid

Price_unit is part of the number. ERP line value is qty × price / price_unit; some prices are per 100 or 1,000 pieces, kg or m. Cases already expose their negotiated price units. cost.py multiplies its case-level volume and price directly and does not inspect the parts table. Keep incoming case quantities consistent; do not silently rescale only one side of a comparison. Confirm any unclear case convention with the authoritative case/API definition.

Join at the right grain. A PO line can have more than one receipt and invoice. Joining both one-to-many tables before summing duplicates money or quantities. Aggregate each at the PO-line grain first, or use the published rolling scorecard for its intended comparison.

Requested-date OTD measures a different commitment from first-confirmed-date OTD. Partial receipts count as separate lines, and calendar effects are intentional. PPM on tiny lots is volatile. The [scorecard methodology](../starter_kit/data/SCORECARD.md) defines the rolling calculation.

Canonicalize the old sup:71999 record to sup:70210 only in analysis views. Preserve original source IDs in evidence and avoid claiming that every one of the 447 vendors has detailed operational data. Generic should-cost estimates use a German cost base with stated uncertainty; selected strategic estimates are supplier-plant-specific. Treat weights and cost estimates as estimates, not hidden supplier facts.

Sunday data is an overlay. Release facts and CSV lookups are added by DataPackage; ordinary SQL still queries the original database. The supplied checkout has no release and no public practice-case JSON directory. Neither should be invented during local testing.

## Public source provenance

The company and counterparties are fictional. Indices are frozen real public series from Eurostat, World Bank, ECB and SMARD; see [ATTRIBUTION.md](../starter_kit/data/ATTRIBUTION.md). Derived copper and alloy series have explicit formulas and are not independent publisher observations. This documentation does not refresh the snapshot from the internet.
