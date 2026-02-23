from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _to_decimal(value):
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


@register.filter
def money(value):
    amount = _to_decimal(value)
    return f"$ {amount:,.2f}"


@register.filter
def money4(value):
    amount = _to_decimal(value)
    return f"$ {amount:,.4f}"
