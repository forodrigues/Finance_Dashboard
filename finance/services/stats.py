from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth

from ..models import (
    Category,
    CommuteDay,
    FuelEntry,
    InvestmentAsset,
    InvestmentMovement,
    RecurringExpense,
    TollEntry,
    Transaction,
)
from .dates import add_months, last_n_months, month_bounds
from .recurring import due_date

ZERO = Decimal('0')
# A category is flagged when it is this much above its 3-month average.
ALERT_RATIO = Decimal('1.2')


def _pct(part, whole):
    if not whole:
        return None
    return (part / whole * 100).quantize(Decimal('0.1'))


def _net_invested(movements):
    total = ZERO
    for kind, amount in movements:
        total += amount if kind == InvestmentMovement.BUY else -amount
    return total


def monthly_series(user, year, month, n=12):
    """Income, expense, net invested, balance and savings rate for the last n months."""
    months = last_n_months(year, month, n)
    start, _ = month_bounds(*months[0])
    _, end = month_bounds(*months[-1])

    tx_rows = (
        Transaction.objects.filter(user=user, date__range=(start, end))
        .annotate(m=TruncMonth('date'))
        .values('m', 'category__kind')
        .annotate(total=Sum('amount'))
    )
    inv_rows = (
        InvestmentMovement.objects.filter(user=user, date__range=(start, end))
        .annotate(m=TruncMonth('date'))
        .values('m', 'kind')
        .annotate(total=Sum('amount'))
    )
    totals = defaultdict(lambda: {'income': ZERO, 'expense': ZERO, 'invested': ZERO})
    for row in tx_rows:
        totals[(row['m'].year, row['m'].month)][row['category__kind']] += row['total']
    for row in inv_rows:
        key = (row['m'].year, row['m'].month)
        totals[key]['invested'] += _net_invested([(row['kind'], row['total'])])

    series = []
    for y, m in months:
        t = totals[(y, m)]
        saved = t['income'] - t['expense']
        series.append(
            {
                'year': y,
                'month': m,
                'income': t['income'],
                'expense': t['expense'],
                'invested': t['invested'],
                'saved': saved,
                'balance': saved - t['invested'],
                'savings_rate': _pct(saved, t['income']),
            }
        )
    return series


def expenses_by_category(user, year, month):
    first, last = month_bounds(year, month)
    return list(
        Transaction.objects.filter(user=user, date__range=(first, last), category__kind=Category.EXPENSE)
        .values('category_id', 'category__name', 'category__color')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )


def category_comparison(user, year, month):
    """Per expense category: this month, previous month, 3/6/12-month averages and an alert flag.

    Averages use the months before the selected one, so the current month is compared against history.
    """
    current = {row['category_id']: row for row in expenses_by_category(user, year, month)}
    first12, _ = month_bounds(*add_months(year, month, -12))
    _, last_prev = month_bounds(*add_months(year, month, -1))
    history = (
        Transaction.objects.filter(user=user, date__range=(first12, last_prev), category__kind=Category.EXPENSE)
        .annotate(m=TruncMonth('date'))
        .values('category_id', 'm')
        .annotate(total=Sum('amount'))
    )
    by_cat = defaultdict(dict)
    for row in history:
        by_cat[row['category_id']][(row['m'].year, row['m'].month)] = row['total']

    names = dict(Category.objects.filter(user=user, kind=Category.EXPENSE).values_list('id', 'name'))
    result = []
    for cat_id in set(current) | set(by_cat):
        months = by_cat.get(cat_id, {})

        def avg(n, months=months):
            keys = [add_months(year, month, -i) for i in range(1, n + 1)]
            return (sum((months.get(k, ZERO) for k in keys), ZERO) / n).quantize(Decimal('0.01'))

        this = current[cat_id]['total'] if cat_id in current else ZERO
        prev = months.get(add_months(year, month, -1), ZERO)
        avg3 = avg(3)
        result.append(
            {
                'category_id': cat_id,
                'name': names.get(cat_id, '?'),
                'current': this,
                'previous': prev,
                'change_pct': _pct(this - prev, prev),
                'avg3': avg3,
                'avg6': avg(6),
                'avg12': avg(12),
                'alert': avg3 > 0 and this > avg3 * ALERT_RATIO,
            }
        )
    result.sort(key=lambda r: r['current'], reverse=True)
    return result


def _recurring_due(user, year, month, kind):
    items = []
    recurring = RecurringExpense.objects.filter(user=user, active=True, category__kind=kind).select_related('category')
    for item in recurring:
        if due_date(item, year, month):
            items.append(item)
    return items, sum((i.amount for i in items), ZERO)


def fixed_expenses_for_month(user, year, month):
    """Recurring expenses due in the given month (used to forecast next month)."""
    return _recurring_due(user, year, month, Category.EXPENSE)


def recurring_income_for_month(user, year, month):
    """Recurring income (e.g. salary) due in the given month."""
    return _recurring_due(user, year, month, Category.INCOME)


def investments_summary(user):
    """Net invested, latest valuation and gain per asset."""
    rows = []
    for asset in InvestmentAsset.objects.filter(user=user).prefetch_related('movements', 'valuations'):
        invested = _net_invested((mv.kind, mv.amount) for mv in asset.movements.all())
        valuation = asset.valuations.all().first()  # ordered by -date
        value = valuation.value if valuation else None
        rows.append(
            {
                'asset': asset,
                'invested': invested,
                'value': value,
                'value_date': valuation.date if valuation else None,
                'gain': value - invested if value is not None else None,
                # A percentage is meaningless once more was withdrawn than put in.
                'gain_pct': _pct(value - invested, invested) if value is not None and invested > 0 else None,
            }
        )
    rows.sort(key=lambda r: r['invested'], reverse=True)
    return rows


def investments_by_kind(summary):
    totals = defaultdict(lambda: ZERO)
    for row in summary:
        totals[row['asset'].get_kind_display()] += row['invested']
    return dict(totals)


def cumulative_invested(user, year, month, n=12):
    """Total net invested at the end of each of the last n months."""
    months = last_n_months(year, month, n)
    start, _ = month_bounds(*months[0])
    base = _net_invested(InvestmentMovement.objects.filter(user=user, date__lt=start).values_list('kind', 'amount'))
    monthly = {(r['year'], r['month']): r['invested'] for r in monthly_series(user, year, month, n)}
    result, running = [], base
    for key in months:
        running += monthly[key]
        result.append(running)
    return result


def fuel_consumption(entries):
    """L/100km per full-tank fill, using the fill-to-fill method.

    `entries` must be ordered by date ascending. A fill only gets a value when it is a full tank with an
    odometer reading and there is an earlier full tank with an odometer reading. Partial fills in between
    are added to the litres. If any fill in the interval has unknown litres (no price entered), the value is
    left out rather than understated.
    """
    result = {}
    prev_full = None
    litres_since = ZERO
    litres_known = True
    for entry in entries:
        if entry.liters is None:
            litres_known = False
        else:
            litres_since += entry.liters
        if not entry.full_tank or entry.odometer_km is None:
            continue
        if prev_full is not None and litres_known and entry.odometer_km > prev_full.odometer_km:
            km = entry.odometer_km - prev_full.odometer_km
            result[entry.id] = (litres_since / km * 100).quantize(Decimal('0.01'))
        prev_full = entry
        litres_since = ZERO
        litres_known = True
    return result


def car_stats(user, year, month):
    first, last = month_bounds(year, month)
    trips = CommuteDay.objects.filter(user=user, date__range=(first, last)).count()
    fuel_month = list(FuelEntry.objects.filter(user=user, date__range=(first, last)))
    fuel_cost = sum((f.total for f in fuel_month), ZERO)
    priced = [f for f in fuel_month if f.liters is not None]
    litres = sum((f.liters for f in priced), ZERO)
    priced_cost = sum((f.amount for f in priced), ZERO)
    tolls = TollEntry.objects.filter(user=user, date__range=(first, last)).aggregate(t=Sum('amount'), n=Count('id'))
    toll_cost = tolls['t'] or ZERO

    # Consumption needs the fill before this month, so compute over all history up to month end.
    consumption = fuel_consumption(FuelEntry.objects.filter(user=user, date__lte=last).order_by('date', 'id'))
    month_values = [consumption[f.id] for f in fuel_month if f.id in consumption]
    avg_consumption = (sum(month_values, ZERO) / len(month_values)).quantize(Decimal('0.01')) if month_values else None
    total = fuel_cost + toll_cost
    return {
        'trips': trips,
        'fuel_cost': fuel_cost,
        'litres': litres,
        'fills': len(fuel_month),
        'toll_cost': toll_cost,
        'tolls': tolls['n'],
        'total': total,
        'cost_per_trip': (total / trips).quantize(Decimal('0.01')) if trips else None,
        # Only fills with a price have known litres, so the average uses just those.
        'avg_price': (priced_cost / litres).quantize(Decimal('0.001')) if litres else None,
        'litres_complete': len(priced) == len(fuel_month),
        'consumption': avg_consumption,
        'consumption_by_entry': consumption,
    }
