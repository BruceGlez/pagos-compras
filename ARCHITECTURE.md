# Architecture Decision (ADR-001)

## Decision
Use a **Django modular monolith** as the primary stack for Pagos Compras.

## Why
- Fastest path to operational value for current workflow.
- Existing working modules: auth/roles, imports, queue, Microsip debt sync, invoice validation, payments.
- Lower migration risk vs immediate split to FastAPI + Next.js.

## Boundaries
- `pagos/models.py` → persistence only
- `pagos/services/*` → business/domain logic
- `pagos/views.py` → request orchestration only
- `templates/*` → presentation only

## Integration seams (future extraction candidates)
- `services/microsip_debt.py`
- `services/imports.py`
- `services/invoice_validation.py`
- payment/export services

## API-first preparation
We expose selective JSON endpoints in Django for future decoupled frontends:
- queue summary/list
- compra detail

## Migration strategy (if needed later)
1. Expand Django JSON APIs.
2. Move isolated services to dedicated API workers.
3. Optional external frontend (Next.js) consuming stable APIs.

## Status
Accepted.
