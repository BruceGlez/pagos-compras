from __future__ import annotations


def build_invoice_request_message(compra):
    productor = compra.productor
    facturador = compra.facturador
    nombre_factura = (facturador.nombre if facturador else (compra.factura or productor.nombre))
    rfc_factura = (facturador.rfc if facturador else "")
    regimen = ((facturador.regimen_fiscal if facturador else productor.regimen_fiscal) or compra.regimen_fiscal or "").upper()

    ctx = {
        "productor_nombre": productor.nombre,
        "facturador_nombre": nombre_factura,
        "productor_rfc": rfc_factura,
        "compra_numero": compra.numero_compra,
        "monto_compra": f"{(compra.compra_en_libras or 0):,.2f}",
        "moneda": "Dólar americano" if compra.moneda == "DOLARES" else "Pesos mexicanos",
        "regimen_fiscal": productor.regimen_fiscal or compra.regimen_fiscal or "(sin régimen)",
    }

    # AE: Persona Física con Actividad Empresarial
    if "ACTIVIDAD EMPRESARIAL" in regimen or "612" in regimen:
        return (
            "Buen día, le anexo la siguiente compra de algodón para solicitar factura.\n\n"
            "POR FAVOR NO OLVIDAR:\n"
            "- IVA trasladado tasa 0%\n"
            "- Forma de pago: 03 Transferencia\n"
            "- Método de pago: PUE\n"
            "- Moneda: {moneda}\n\n"
            "NOMBRE: {facturador_nombre}\n"
            "RFC: {productor_rfc}\n"
            "COMPRA ({compra_numero}): ${monto_compra}\n"
            "RÉGIMEN FISCAL: {regimen_fiscal}\n\n"
            "Necesito que en la factura vengan las clases desglosadas con número de pacas en la descripción.\n"
            "Ejemplo: ALGODÓN (CLASE SM X PACAS), ALGODÓN (CLASE MP X PACAS).\n"
            "Los castigos se deben aplicar como descuentos.\n\n"
            "Favor de compartir XML y PDF. Gracias."
        ).format(**ctx)

    return (
        "Hola, buen día. Te solicito por favor enviar la factura CFDI de la compra #{compra_numero} "
        "del productor {productor_nombre}. Facturar a nombre de: {facturador_nombre}.\n"
        "Moneda: {moneda}.\n"
        "Favor de compartir XML y PDF. Gracias."
    ).format(**ctx)
