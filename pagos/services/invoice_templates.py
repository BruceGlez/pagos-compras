from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from pagos.catalogs import SAT_FORMAS_PAGO, SAT_METODOS_PAGO, SAT_USOS_CFDI
from pagos.models import EmailTemplate


def _label(code: str, options):
    m = {k: v for k, v in options}
    return m.get(code, code)


def _ctx(compra):
    productor = compra.productor
    facturador = compra.facturador
    nombre_factura = facturador.nombre if facturador else (compra.factura or productor.nombre)
    rfc_factura = (
        (compra.expected_rfc_receptor or "").strip().upper()
        or ((facturador.rfc if facturador else "") or "").strip().upper()
        or (productor.rfc or "").strip().upper()
    )
    subtotal = Decimal(str(compra.compra_en_libras or 0))
    ret_125 = (subtotal * Decimal("0.0125")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (subtotal - ret_125).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    moneda_desc = "dólares americanos" if compra.moneda == "DOLARES" else "pesos mexicanos"
    forma = compra.expected_forma_pago or "03"
    metodo = compra.expected_metodo_pago or "PUE"
    uso = compra.expected_uso_cfdi or "G01"

    return {
        "productor_nombre": productor.nombre,
        "facturador_nombre": nombre_factura,
        "productor_rfc": rfc_factura,
        "compra_numero": compra.numero_compra,
        "monto_compra": f"{subtotal:,.2f}",
        "subtotal_compra": f"{subtotal:,.2f}",
        "retencion_125": f"{ret_125:,.2f}",
        "total_con_retencion": f"{total:,.2f}",
        "moneda_detalle": moneda_desc,
        "moneda": "Dólar americano" if compra.moneda == "DOLARES" else "Pesos mexicanos",
        "regimen_fiscal": (facturador.regimen_fiscal if facturador else productor.regimen_fiscal)
        or compra.regimen_fiscal
        or "(sin régimen)",
        "forma_pago": forma,
        "metodo_pago": metodo,
        "uso_cfdi": uso,
        "forma_pago_detalle": _label(forma, SAT_FORMAS_PAGO),
        "metodo_pago_detalle": _label(metodo, SAT_METODOS_PAGO),
        "uso_cfdi_detalle": _label(uso, SAT_USOS_CFDI),
    }


def _select_template(compra):
    fact = compra.facturador
    regimen = ((fact.regimen_fiscal if fact else "") or compra.regimen_fiscal or "").upper()
    scenario = "GENERAL"
    if "626" in regimen or "RESICO" in regimen:
        scenario = "RESICO"
    elif "612" in regimen or "ACTIVIDAD EMPRESARIAL" in regimen:
        scenario = "AE"

    tpl = EmailTemplate.objects.filter(activo=True, scenario=scenario, is_default=True).first()
    if not tpl:
        tpl = EmailTemplate.objects.filter(activo=True, is_default=True).first()
    return tpl, scenario


def build_invoice_request_email(compra):
    ctx = _ctx(compra)
    tpl, scenario = _select_template(compra)
    if tpl:
        return {
            "subject": tpl.subject_template.format(**ctx),
            "body": tpl.body_template.format(**ctx),
            "template_code": tpl.code,
            "scenario": scenario,
        }

    subject = f"Solicitud de factura compra #{ctx['compra_numero']}"
    body = (
        "Buen día, le anexo la siguiente compra de algodón para solicitar factura.\n\n"
        "POR FAVOR NO OLVIDAR:\n"
        "- IVA trasladado tasa 0%\n"
        "- Forma de pago: {forma_pago}\n"
        "- Método de pago: {metodo_pago}\n"
        "- Uso CFDI: {uso_cfdi}\n"
        "- Moneda: {moneda}\n\n"
        "NOMBRE: {facturador_nombre}\n"
        "RFC: {productor_rfc}\n"
        "COMPRA ({compra_numero}): ${monto_compra}\n"
        "RÉGIMEN FISCAL: {regimen_fiscal}\n\n"
        "Favor de compartir XML y PDF. Gracias."
    ).format(**ctx)
    return {"subject": subject, "body": body, "template_code": "", "scenario": scenario}


def build_invoice_request_message(compra):
    return build_invoice_request_email(compra)["body"]


def render_invoice_email_html(body: str) -> str:
    lines = (body or "").splitlines()
    html = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f2937;line-height:1.5">',
        '<div style="background:#f3f4f6;border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;margin-bottom:12px">',
        '<strong style="color:#111827">Solicitud de factura</strong>',
        '</div>',
    ]
    in_list = False
    section_titles = {
        "DATOS EMISOR",
        "REGLA RESICO",
        "CASO RETENCIÓN",
        "CASO LEYENDA DE EXENCIÓN",
        "REQUISITOS ADICIONALES",
        "REFERENCIA SAT",
        "DATOS RECEPTOR",
    }

    for raw in lines:
        line = raw.strip()
        if not line:
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append('<div style="height:8px"></div>')
            continue

        if line in section_titles:
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append(
                f'<div style="margin-top:8px;margin-bottom:6px;font-weight:700;color:#0f766e;text-transform:uppercase">{line}</div>'
            )
            continue

        if line.startswith("- "):
            if not in_list:
                html.append('<ul style="margin:4px 0 8px 20px;padding:0">')
                in_list = True
            item = line[2:]
            item = item.replace("Monto compra:", "<strong style=\"color:#111827\">Monto compra:</strong>")
            item = item.replace("Subtotal:", "<strong style=\"color:#111827\">Subtotal:</strong>")
            item = item.replace("Retención ISR 1.25%:", "<strong style=\"color:#b91c1c\">Retención ISR 1.25%:</strong>")
            item = item.replace("Total:", "<strong style=\"color:#0f766e\">Total:</strong>")
            html.append(f"<li>{item}</li>")
            continue

        if line.startswith("--"):
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append('<hr style="border:none;border-top:1px solid #e5e7eb;margin:14px 0 10px">')
            continue

        text = line.replace("Monto compra:", "<strong style=\"color:#111827\">Monto compra:</strong>")
        text = text.replace("Subtotal:", "<strong style=\"color:#111827\">Subtotal:</strong>")
        text = text.replace("Retención ISR 1.25%:", "<strong style=\"color:#b91c1c\">Retención ISR 1.25%:</strong>")
        text = text.replace("Total:", "<strong style=\"color:#0f766e\">Total:</strong>")
        html.append(f"<div>{text}</div>")

    if in_list:
        html.append("</ul>")
    html.append("</div>")
    return "\n".join(html)
