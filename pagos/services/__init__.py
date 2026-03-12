from .debt import add_manual_deduction, calculate_payable, payable_breakdown, register_debt_snapshot
from .imports import (
    detect_compras_conflicts,
    import_anticipos_excel,
    import_compras_excel,
    preview_anticipos_excel,
    preview_compras_excel,
)
from .gmail import gmail_ready, send_gmail
from .invoice_templates import build_invoice_request_email, build_invoice_request_message, render_invoice_email_html
from .invoice_validation import create_invoice_validation_for_compra, parse_and_validate_cfdi_xml
from .microsip_debt import (
    find_microsip_candidates_for_productor,
    list_all_microsip_debt_clients,
    sync_microsip_debt_for_compra,
)
from .workflow import transition_compra
from .payment_receipt import extract_pdf_text, parse_payment_receipt_text
