# Implementation Log (project_specs aligned)

## 2026-03-09 — Batch 1

Implemented foundational workflow/audit entities to move from spreadsheet-style tracking to explicit lifecycle states.

### Added
- `Compra.workflow_state` with spec-aligned states:
  - IMPORTED
  - DEBT_CALCULATED
  - WAITING_INVOICE
  - INVOICE_RECEIVED
  - INVOICE_VALID
  - INVOICE_BLOCKED
  - WAITING_BANK_CONFIRMATION
  - READY_TO_PAY
  - PAID
  - ARCHIVED
- `WorkflowEvent` model to persist state transition logs (audit trail).
- `DebtSnapshot` model to store debt totals + detail snapshot payloads.
- `Deduccion` model for manual deductions (coberturas, ajustes, etc).

### Migration
- `pagos/migrations/0006_compra_workflow_state_debtsnapshot_deduccion_and_more.py`

### Admin coverage
Registered in Django admin:
- `PagoCompra`
- `DebtSnapshot`
- `Deduccion`
- `WorkflowEvent`

### Next
1. Add invoice validation result model/service (XML checks + blocking reasons).
2. Build readiness queue view driven by `workflow_state`.
3. Add purchase import module from Excel (algodon.net export).

## 2026-03-09 — Batch 2

Wired first workflow transitions into operational views and exposed workflow filtering in UI.

### Added/Updated
- `pagos/views.py`
  - On purchase creation, transitions `IMPORTED -> DEBT_CALCULATED`.
  - On debt review save, attempts transition to `WAITING_INVOICE`.
  - On facturas save with UUID, attempts chain:
    - `... -> INVOICE_RECEIVED -> INVOICE_VALID -> WAITING_BANK_CONFIRMATION`
  - On payment registration, attempts chain:
    - `... -> READY_TO_PAY -> PAID`
- `pagos/forms.py`
  - Added `workflow_state` filter in `CompraFiltroForm`.
- `templates/pagos/compras_operativas.html`
  - Added workflow-state filter control.
  - Added `workflow_state` badge in each row.

### Notes
- Transitions are currently best-effort (`ValueError` from invalid transition is ignored in UI flow) to avoid blocking operators while we normalize legacy states.
- Next step is to tighten transition consistency with explicit preconditions and user-facing warnings.

## 2026-03-09 — Batch 3

Added invoice XML validation scaffold and integrated result storage.

### Added
- `InvoiceValidationResult` model + migration `0007_invoicevalidationresult.py`.
- Service: `pagos/services/invoice_validation.py` with baseline CFDI checks.
- Admin registration for invoice validations.

## 2026-03-09 — Batch 4

Added readiness queue UI.

### Added
- Route/view/template: `/queue/`
- Category counters and state filters for payment readiness.

## 2026-03-09 — Batch 5

Implemented first `algodon.net` import module from Excel (phase 1 in spec roadmap).

### Added
- Service: `pagos/services/imports.py`
  - `import_compras_excel(path, dry_run=False)`
  - Reads `COMPRAS` sheet, maps key fields to `Compra`.
  - Dedup key: `numero_compra + productor + fecha_liq`.
  - Handles repeated rows as initial division scaffold (`parent_compra` + porcentaje estimado).
- UI form/view for imports:
  - Route: `/import/compras/`
  - Upload Excel + optional dry-run simulation.
  - Returns summary counters (created/duplicates/divisions).
- Dashboard shortcut button to import screen.

### Notes
- This is an MVP importer; mapping and division rules can be tightened with real production files.
