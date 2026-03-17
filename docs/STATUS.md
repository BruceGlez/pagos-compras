# STATUS.md — Pagos Compras (Ops Snapshot)

Updated: 2026-03-17

## System Health
- App URL: `http://100.76.2.90:8110/`
- Service: `pagos-compras.service` (systemd user)
- State: expected `active (running)`

## Current Workflow State
- Readiness queue active with blocker badges + priority score.
- Core pipeline gates active (invoice, bank confirm, required docs, carátula bancaria).
- Flow/state drift protections in place + backfill command available.

## Inbox (Factura) Status
- Inbox read uses ranking preview (not strict token-only).
- Candidate scoring: RFC + amount + token + date proximity.
- Manual selection before import.
- Dedup protections:
  - message-id dedup
  - file-content hash dedup
- Gmail processed-state:
  - adds label `pagos-processed`
  - removes `UNREAD`
- Required Gmail scopes:
  - `gmail.send`
  - `gmail.modify`

## Solicitud de factura
- Real send to contador: enabled.
- Test send: enabled.
- Both flows create outbox log + expediente proof.
- Receptor RFC policy fixed to company RFC (`UAM140522Q51` fallback via config).

## Expediente
- Upload now driven by `tipo_documento` (human-friendly).
- Internal `etapa` derived automatically from type.
- MXN path supported with explicit document typing:
  - `COMPRA_MXN` for purchase-in-pesos PDF.

## Payment / Deudas
- Deudas total recalculates live in flow.
- Debt discount uses user-captured retención fields (no snapshot fallback when values are 0).
- Payment receipt parser expanded for additional formats and better currency detection.

## Key Commands
- Check app status:
  - `systemctl --user status pagos-compras.service`
- Restart app:
  - `systemctl --user restart pagos-compras.service`
- OAuth reauth:
  - `python manage.py autorizar_gmail_oauth`
- Workflow state backfill:
  - `python manage.py backfill_workflow_states`
  - `python manage.py backfill_workflow_states --apply`

## Immediate Watchlist
- ✅ Division flow rule aligned + test fixed:
  - divisiones inician independientes en `captura` y quedan ligadas por `parent_compra`.
- ✅ Added baseline tests for:
  - queue `mark_ready` blocked/success paths,
  - inbox dedup guards (message-id and content-level no-duplicate insert),
  - RESICO policy matrix core cases.
- Remaining: expand inbox ranking confidence test matrix + processed-marker edge/error cases.
- Confirm canonical expediente storage policy and error handling when storage is unavailable.
- Optional sender-profile scoring for better inbox ranking confidence.
- Optional structured categories for "otros pendientes" in deudas.
