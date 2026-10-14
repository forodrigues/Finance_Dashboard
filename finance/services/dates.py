import calendar
from datetime import date


def month_bounds(year, month):
    """Return the first and last day of a month."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def add_months(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def last_n_months(year, month, n):
    """Months ending at (year, month), oldest first."""
    return [add_months(year, month, -i) for i in range(n - 1, -1, -1)]
