# Mapeo Excel -> Sistema

Este sistema fue alineado con las hojas:
- `COMPRAS`
- `TC`
- `ANTICIPOS`

## TC

- `FECHA` -> `TipoCambio.fecha`
- `TC` -> `TipoCambio.tc`

## ANTICIPOS

- `Anticipo` -> `Anticipo.numero_anticipo`
- `FECHA DE PAGO` -> `Anticipo.fecha_pago`
- `PRODUCTOR` -> `Anticipo.productor`
- `PERSONA QUE FACTURA` -> `Anticipo.persona_que_factura`
- `FACTURA` -> `Anticipo.factura`
- `ANTICIPO` -> `Anticipo.monto_anticipo`
- `MONEDA` -> `Anticipo.moneda`
- `PENDIENTE APLICAR` -> `Anticipo.pendiente_aplicar` (calculado tambien por aplicaciones)
- `ESTADO` -> `Anticipo.estado`
- `UUID NOTA DE CREDITO` -> `Anticipo.uuid_nota_credito`
- `TOTAL EN PESOS` -> `Anticipo.total_en_pesos`
- `CUENTA DE PAGO` -> `Anticipo.cuenta_de_pago`
- `CUENTA` -> `Anticipo.cuenta`
- `CONTADOR` -> `Anticipo.contador`
- `CORREO PARA FACTURAS` -> `Anticipo.correo_para_facturas`
- `TELEFONO` -> `Anticipo.telefono`

## COMPRAS

- `COMPRA` -> `Compra.numero_compra`
- `INTERESES` -> `Compra.intereses`
- `FECHA DE PAGO` -> `Compra.fecha_de_pago`
- `FECHA LIQ` -> `Compra.fecha_liq`
- `REGIMEN FISCAL` -> `Compra.regimen_fiscal`
- `PRODUCTOR` -> `Compra.productor`
- `UUID FACTURA` -> `Compra.uuid_factura`
- `FACTURA` -> `Compra.factura`
- `PACAS` -> `Compra.pacas`
- `COMPRA EN LIBRAS` -> `Compra.compra_en_libras`
- `ANTICIPO` -> `Compra.anticipo`
- `PAGO` -> `Compra.pago`
- `DIAS TRANSCURRIDOS` -> `Compra.dias_transcurridos`
- `TIPO DE CAMBIO` -> `Compra.tipo_cambio` / `Compra.tipo_cambio_valor`
- `RETENCION (DEUDAS) USD` -> `Compra.retencion_deudas_usd`
- `RETENCION (DEUDAS) MXN` -> `Compra.retencion_deudas_mxn`
- `TOTAL DE DEUDA EN DLS` -> `Compra.total_deuda_en_dls`
- `RETENCION RESICO 1.25%` -> `Compra.retencion_resico`
- `SALDO PENDIENTE` -> `Compra.saldo_pendiente`
- `ESTATUS DE FACTURA` -> `Compra.estatus_factura`
- `VENCIMIENTO` -> `Compra.vencimiento`
- `CUENTA DE PAGO` -> `Compra.cuenta_de_pago`
- `METODO DE PAGO` -> `Compra.metodo_de_pago`
- `MONEDA` -> `Compra.moneda`
- `TOTAL EN PESOS` -> `Compra.total_en_pesos`
- `CUENTA PRODUCTOR` -> `Compra.cuenta_productor`
- `ESTATUS DE PAGO` -> `Compra.estatus_de_pago`
- `CONTADOR` -> `Compra.contador`
- `CORREO` -> `Compra.correo`
- `ESTATUS DE REP` -> `Compra.estatus_rep`
- `UUID PPD` -> `Compra.uuid_ppd`

## Regla de aplicacion de anticipos

La tabla `AplicacionAnticipo` relaciona cada anticipo con compras del mismo productor:
- evita sobreaplicar sobre el saldo del anticipo
- evita sobreaplicar sobre el saldo de la compra
