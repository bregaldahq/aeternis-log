---
name: flow-audit-reports
description: >-
  Deep reference for the audit report endpoints GET /api/v1/{domain}/report
  (JSON) and /report.pdf (PDF via go-pdf/fpdf): tenant/domain scoping, the
  Mongo aggregation over batches, the from/to period semantics, and the PDF
  layout. Use when changing report content, filters, the aggregation, PDF
  rendering, or when report numbers don't match expectations.
---

# Flow: audit reports

## Entry points

| What | Where |
|---|---|
| Routes (ValidateDomain + auth) | `api/cmd/api/main.go:342-351` |
| Handler | `api/internal/handlers/report.go:26` (`build`), `:64` (JSON), `:84` (PDF) |
| Aggregation | `api/internal/database/collections.go:265` (`AggregateRecordBatches`) |
| PDF renderer | `api/internal/report/report.go:16` (`BuildAuditReportPDF`) |
| Model | `api/internal/models/record.go:188-207` (`AuditBatch`, `AuditReport`) |

## Step by step

1. `tenant = tenantFrom(c)`, domain from the path, `from`/`to` query strings
   (documented as RFC3339, empty means open). 15 s context.
2. Aggregation over `records`:
   `$match {tenant, domain, batch_id exists, [batched_at ≥ from], [batched_at ≤ to]}`
   → `$group by batch_id {merkle_root: $first, tx_id: $first, batched_at: $first, num_records: $sum 1}`
   → `$sort batched_at ↓`.
3. Totals: `TotalBatches = len`, `TotalRecords = Σ num_records`.
4. JSON → `200 AuditReport`. PDF → `application/pdf` with
   `Content-Disposition: attachment; filename="audit-<tenant>-<domain>.pdf"`.
   Errors → `500 report_error` / `pdf_error`.
5. PDF: header (tenant, domain, period, generated, totals), one block per
   batch (id, records, time, merkle root, fabric tx or "(not recorded)"),
   footer explaining independent verification (CLI + `/public/anchors`).
   **Compression is disabled** so the text stays greppable (the tests rely on
   this).

## Semantics to know

- `batch_id exists` includes **pending and failed** batches, not only
  anchored ones, even though the PDF labels them "anchored" and the docs say
  "anchored batches". A failed batch shows `fabric tx: (not recorded)`.
- The "anchored" time printed is **`batched_at`** (claim time), not the
  ledger commit time.
- `batched_at` is stored as an RFC3339 **string**, so `from`/`to` are
  **lexicographic string** comparisons. This works for `…Z` UTC timestamps in
  the same format. A `from` with an offset (`-03:00`) or a date-only value
  compares incorrectly.
- Counts include soft-deleted records (they are still leaves of their
  batches).
- The report does **not** verify anything. It lists roots. Integrity is
  proven through `verify` or `/public/anchors` plus local recomputation.

## Invariants

- Always scoped by `tenant` + `domain`.
- No record payloads in reports, only batch-level proof metadata.

## Tests

`api/internal/report/report_test.go` (`TestBuildAuditReportPDF`,
`TestBuildAuditReportPDFEmpty`). **Gap:** no test for the aggregation or the
handler (period filter, pending batches). Add Mongo-backed tests when you
touch it.

## Changing it safely

- Fixing the semantics above (anchored-only filter, date parsing) changes
  customer-visible output. Update the website page `guides/audit-reports.md`
  and note it in `reference/changelog.md`.
- Adding columns: add them to `AuditBatch` (bson tag matching the `$group`
  field) and to the PDF, and extend the PDF test with a grep for the new
  text.

## Debug recipes

```bash
curl -s "localhost:5001/api/v1/contracts/report?from=2026-01-01T00:00:00Z" | jq '.total_batches,.total_records'
curl -s -o /tmp/r.pdf localhost:5001/api/v1/contracts/report.pdf && grep -a 'merkle root' /tmp/r.pdf | head
```

## Related

`flow-batch-anchoring` · `flow-tenancy` · `flow-public-verification`
