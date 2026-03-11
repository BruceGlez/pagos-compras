from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from xml.etree import ElementTree as ET

from pagos.models import Compra, InvoiceValidationResult


def _find_attr(root: ET.Element, local_name: str):
    for el in root.iter():
        if el.tag.endswith(local_name):
            return el
    return None


def _normalize_text(s: str) -> str:
    s = (s or "").lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_and_validate_cfdi_xml(
    xml_bytes: bytes,
    *,
    expected_rfc_receptor: str = "",
    expected_regimen_fiscal_receptor: str = "",
    expected_codigo_fiscal_receptor: str = "",
    expected_nombre_receptor: str = "",
    expected_efecto_comprobante: str = "",
    expected_impuesto_trasladado: str = "",
    expected_moneda: str = "",
    expected_uso_cfdi: str = "",
    expected_metodo_pago: str = "",
    expected_forma_pago: str = "",
    requires_resico_retention: bool = False,
    resico_policy: str = "AUTO",
):
    root = ET.fromstring(xml_bytes)

    comp = _find_attr(root, "Comprobante") or root
    emisor = _find_attr(root, "Emisor")
    receptor = _find_attr(root, "Receptor")

    uuid = ""
    for el in root.iter():
        if el.tag.endswith("TimbreFiscalDigital"):
            uuid = el.attrib.get("UUID", "")
            break

    iva_tasa = ""
    isr_retencion = Decimal("0")
    isr_tasa_125 = False
    textos_xml = []
    for el in root.iter():
        if el.text and str(el.text).strip():
            textos_xml.append(str(el.text).strip().lower())
        for _k, _v in el.attrib.items():
            if _v and str(_v).strip():
                textos_xml.append(str(_v).strip().lower())
        if el.tag.endswith("Traslado") and el.attrib.get("Impuesto") == "002":
            iva_tasa = el.attrib.get("TasaOCuota", "")
        if el.tag.endswith("Retencion") and el.attrib.get("Impuesto") == "001":
            isr_retencion += Decimal(el.attrib.get("Importe", "0") or "0")
            tasa = (el.attrib.get("TasaOCuota", "") or "").strip()
            if tasa in {"0.012500", "0.0125", "1.25"}:
                isr_tasa_125 = True

    texto = " ".join(textos_xml)
    texto_norm = _normalize_text(texto)
    has_exencion_leyenda = (
        (
            ("supuesto de ex" in texto_norm)
            and ("113 e" in texto_norm or "113e" in texto_norm)
            and ("ley de isr" in texto_norm)
        )
        or ("sin retencion" in texto_norm)
        or ("no se efectuara retencion" in texto_norm)
    )

    result = {
        "uuid": uuid,
        "rfc_emisor": (emisor.attrib.get("Rfc", "") if emisor is not None else ""),
        "rfc_receptor": (receptor.attrib.get("Rfc", "") if receptor is not None else ""),
        "regimen_fiscal_receptor": (receptor.attrib.get("RegimenFiscalReceptor", "") if receptor is not None else ""),
        "codigo_fiscal_receptor": (receptor.attrib.get("DomicilioFiscalReceptor", "") if receptor is not None else ""),
        "nombre_receptor": (receptor.attrib.get("Nombre", "") if receptor is not None else ""),
        "uso_cfdi": (receptor.attrib.get("UsoCFDI", "") if receptor is not None else ""),
        "metodo_pago": comp.attrib.get("MetodoPago", ""),
        "forma_pago": comp.attrib.get("FormaPago", ""),
        "efecto_comprobante": comp.attrib.get("TipoDeComprobante", ""),
        "moneda": comp.attrib.get("Moneda", ""),
        "iva_tasa": iva_tasa,
        "isr_retencion": str(isr_retencion),
        "isr_tasa_125": isr_tasa_125,
        "has_exencion_leyenda": has_exencion_leyenda,
        "errors": [],
        "warnings": [],
        "resico_policy_used": (resico_policy or "AUTO"),
    }

    if not result["uuid"]:
        result["errors"].append("CFDI sin UUID timbrado")

    if expected_rfc_receptor and result["rfc_receptor"].upper() != expected_rfc_receptor.upper():
        result["errors"].append("RFC receptor no coincide")
    if expected_regimen_fiscal_receptor and result["regimen_fiscal_receptor"].upper() != expected_regimen_fiscal_receptor.upper():
        result["errors"].append("Régimen fiscal receptor no coincide")
    if expected_codigo_fiscal_receptor and result["codigo_fiscal_receptor"].upper() != expected_codigo_fiscal_receptor.upper():
        result["errors"].append("Código fiscal receptor no coincide")
    if expected_nombre_receptor and result["nombre_receptor"].upper() != expected_nombre_receptor.upper():
        result["errors"].append("Nombre receptor no coincide")
    if expected_efecto_comprobante and result["efecto_comprobante"].upper() != expected_efecto_comprobante.upper():
        result["errors"].append("Efecto comprobante no coincide")

    if expected_moneda and result["moneda"].upper() != expected_moneda.upper():
        result["errors"].append("Moneda no coincide")

    if expected_uso_cfdi:
        if result["uso_cfdi"].upper() != expected_uso_cfdi.upper():
            result["errors"].append("Uso CFDI no coincide con lo solicitado")
    else:
        allowed_uso_cfdi = {"G01", "S01"}
        if result["uso_cfdi"] and result["uso_cfdi"] not in allowed_uso_cfdi:
            result["errors"].append("Uso CFDI fuera de política")

    if expected_metodo_pago:
        if result["metodo_pago"].upper() != expected_metodo_pago.upper():
            result["errors"].append("Método de pago no coincide con lo solicitado")
    else:
        if result["metodo_pago"] and result["metodo_pago"] not in {"PUE", "PPD"}:
            result["errors"].append("Método de pago CFDI no permitido")

    if expected_forma_pago and result["forma_pago"]:
        if result["forma_pago"].upper() != expected_forma_pago.upper():
            result["errors"].append("Forma de pago no coincide con lo solicitado")

    if expected_impuesto_trasladado in {"0", "16"}:
        if expected_impuesto_trasladado == "0" and result["iva_tasa"] not in {"0.000000", "0", ""}:
            result["errors"].append("IVA trasladado debe ser 0%")
        if expected_impuesto_trasladado == "16" and result["iva_tasa"] not in {"0.160000", "0.16", "16"}:
            result["errors"].append("IVA trasladado debe ser 16%")
    else:
        if result["iva_tasa"] not in {"0.000000", "0", ""}:
            result["errors"].append("IVA debe ser 0%")

    if requires_resico_retention:
        policy = (resico_policy or "AUTO").upper()
        has_ret = isr_retencion > 0 and isr_tasa_125
        has_leyenda = has_exencion_leyenda
        if policy == "RETENCION_125":
            if not has_ret:
                result["errors"].append("RESICO requiere retención ISR con tasa 1.25%")
        elif policy == "EXENCION_LEYENDA":
            if not has_leyenda:
                result["errors"].append("RESICO requiere leyenda de exención en descripción")
        else:
            if not (has_ret or has_leyenda):
                result["errors"].append("RESICO requiere retención ISR 1.25% o leyenda de exención")

    result["valid"] = len(result["errors"]) == 0
    return result


def create_invoice_validation_for_compra(
    compra: Compra,
    xml_bytes: bytes,
    *,
    expected_rfc_receptor: str = "",
    expected_regimen_fiscal_receptor: str = "",
    expected_codigo_fiscal_receptor: str = "",
    expected_nombre_receptor: str = "",
    expected_efecto_comprobante: str = "",
    expected_impuesto_trasladado: str = "",
    expected_moneda: str = "",
    expected_uso_cfdi: str = "",
    expected_metodo_pago: str = "",
    expected_forma_pago: str = "",
    requires_resico_retention: bool = False,
    resico_policy: str = "AUTO",
):
    parsed = parse_and_validate_cfdi_xml(
        xml_bytes,
        expected_rfc_receptor=expected_rfc_receptor,
        expected_regimen_fiscal_receptor=expected_regimen_fiscal_receptor,
        expected_codigo_fiscal_receptor=expected_codigo_fiscal_receptor,
        expected_nombre_receptor=expected_nombre_receptor,
        expected_efecto_comprobante=expected_efecto_comprobante,
        expected_impuesto_trasladado=expected_impuesto_trasladado,
        expected_moneda=expected_moneda,
        expected_uso_cfdi=expected_uso_cfdi,
        expected_metodo_pago=expected_metodo_pago,
        expected_forma_pago=expected_forma_pago,
        requires_resico_retention=requires_resico_retention,
        resico_policy=resico_policy,
    )
    return InvoiceValidationResult.objects.create(
        compra=compra,
        uuid=parsed["uuid"],
        rfc_emisor=parsed["rfc_emisor"],
        rfc_receptor=parsed["rfc_receptor"],
        uso_cfdi=parsed["uso_cfdi"],
        metodo_pago=parsed["metodo_pago"],
        moneda=parsed["moneda"],
        iva_tasa=parsed["iva_tasa"],
        isr_retencion=Decimal(parsed["isr_retencion"] or "0"),
        valid=parsed["valid"],
        blocked_reason="; ".join(parsed["errors"]),
        raw_result=parsed,
    )
