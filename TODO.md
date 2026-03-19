# TODO - Pagos Compras

Use this file to park ideas and implement them later.

## Backlog

- [ ] Implement in **Revisar deudas** more sections, like:
  - [ ] Coberturas
  - [ ] Otros pendientes
  - [ ] Breakdown by source (Microsip / manual)
  - [ ] Clear summary of impact on total a pagar

- [ ] Add progress bar UX for **Leer inbox ahora** in `Revisar factura` (implement later)
  - [ ] Scope files: `templates/pagos/compra_flujo.html`, `pagos/views.py`, `pagos/urls.py`
  - [ ] Add async scan flow (start + status polling) so button is non-blocking
  - [ ] Show live stages + percent (auth/list/fetch/ranking)
  - [ ] Keep existing ranking/import logic unchanged; only change execution flow
  - [ ] Update docs (`docs/STATUS.md`) after rollout
