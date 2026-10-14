from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def eur(value):
    """Format a number as Portuguese currency: 1 234,56 €."""
    if value is None or value == '':
        return '—'
    try:
        value = Decimal(value).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return value
    sign = '-' if value < 0 else ''
    integer, _, cents = f'{abs(value):.2f}'.partition('.')
    groups = []
    while integer:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    return f'{sign}{" ".join(groups)},{cents} €'


@register.filter
def pct(value):
    if value is None:
        return '—'
    return f'{value:.1f}'.replace('.', ',') + ' %'


@register.filter
def num(value, places=2):
    if value is None:
        return '—'
    return f'{Decimal(value):.{int(places)}f}'.replace('.', ',')
