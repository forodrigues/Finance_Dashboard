from datetime import date
from decimal import Decimal as D

from finance.models import (
    Category,
    CommuteDay,
    FuelEntry,
    InvestmentAsset,
    InvestmentMovement,
    InvestmentValuation,
    RecurringExpense,
    TollEntry,
    Transaction,
)
from finance.services import recurring, stats
from finance.services.dates import add_months, last_n_months
from finance.tests.helpers import OwnedTestCase


class DatesTests(OwnedTestCase):
    def test_add_months_crosses_years(self):
        self.assertEqual(add_months(2026, 1, -1), (2025, 12))
        self.assertEqual(add_months(2026, 12, 1), (2027, 1))
        self.assertEqual(add_months(2026, 3, -14), (2025, 1))

    def test_last_n_months_is_oldest_first(self):
        self.assertEqual(last_n_months(2026, 2, 3), [(2025, 12), (2026, 1), (2026, 2)])


class RecurringTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.gym = Category.objects.get(user=self.user, name='Ginásio')

    def make(self, **kwargs):
        defaults = {'name': 'Ginásio', 'amount': D('35'), 'category': self.gym, 'start_date': date(2026, 1, 1)}
        return RecurringExpense.objects.create(user=self.user, **{**defaults, **kwargs})

    def test_generate_is_idempotent(self):
        self.make()
        self.assertEqual(len(recurring.generate_for_month(self.user, 2026, 3)), 1)
        self.assertEqual(len(recurring.generate_for_month(self.user, 2026, 3)), 0)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_day_is_clamped_to_month_end(self):
        self.make(day_of_month=31)
        (tx,) = recurring.generate_for_month(self.user, 2026, 2)
        self.assertEqual(tx.date, date(2026, 2, 28))

    def test_respects_start_end_and_active(self):
        self.make(start_date=date(2026, 3, 15), day_of_month=10)  # first due date before start
        self.make(name='Fim', end_date=date(2026, 2, 28))
        self.make(name='Inativa', active=False)
        self.assertEqual(recurring.pending_for_month(self.user, 2026, 3), [])
        self.assertEqual([e.name for e, _ in recurring.pending_for_month(self.user, 2026, 4)], ['Ginásio'])

    def test_yearly_only_in_start_month(self):
        self.make(frequency=RecurringExpense.YEARLY, start_date=date(2025, 6, 1))
        self.assertEqual(recurring.pending_for_month(self.user, 2026, 5), [])
        self.assertEqual(len(recurring.pending_for_month(self.user, 2026, 6)), 1)

    def test_yearly_monthly_amount(self):
        self.assertEqual(self.make(frequency=RecurringExpense.YEARLY, amount=D('120')).monthly_amount, D('10.00'))


class MonthlyStatsTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.salary = Category.objects.get(user=self.user, name='Salário')
        self.market = Category.objects.get(user=self.user, name='Supermercado')
        self.etf = InvestmentAsset.objects.create(user=self.user, name='ETF')

    def tx(self, day, amount, category):
        return Transaction.objects.create(user=self.user, date=day, amount=D(amount), category=category)

    def test_monthly_series_totals(self):
        self.tx(date(2026, 3, 1), '1500', self.salary)
        self.tx(date(2026, 3, 5), '300', self.market)
        self.tx(date(2026, 2, 5), '200', self.market)
        InvestmentMovement.objects.create(user=self.user, date=date(2026, 3, 10), asset=self.etf, amount=D('500'))
        InvestmentMovement.objects.create(
            user=self.user, date=date(2026, 3, 20), asset=self.etf, amount=D('100'), kind=InvestmentMovement.SELL
        )
        series = stats.monthly_series(self.user, 2026, 3, 2)
        feb, mar = series
        self.assertEqual((feb['year'], feb['month'], feb['expense']), (2026, 2, D('200')))
        self.assertEqual(mar['income'], D('1500'))
        self.assertEqual(mar['expense'], D('300'))
        self.assertEqual(mar['invested'], D('400'))
        self.assertEqual(mar['balance'], D('800'))
        self.assertEqual(mar['savings_rate'], D('80.0'))
        self.assertIsNone(feb['savings_rate'])  # no income

    def test_category_comparison_and_alert(self):
        for month in (1, 2, 3):
            self.tx(date(2026, month, 10), '100', self.market)
        self.tx(date(2026, 4, 10), '150', self.market)
        (row,) = stats.category_comparison(self.user, 2026, 4)
        self.assertEqual(row['current'], D('150'))
        self.assertEqual(row['previous'], D('100'))
        self.assertEqual(row['change_pct'], D('50.0'))
        self.assertEqual(row['avg3'], D('100.00'))
        self.assertEqual(row['avg6'], D('50.00'))
        self.assertTrue(row['alert'])

    def test_investments_summary_gain(self):
        InvestmentMovement.objects.create(user=self.user, date=date(2026, 1, 1), asset=self.etf, amount=D('1000'))
        InvestmentValuation.objects.create(user=self.user, date=date(2026, 1, 1), asset=self.etf, value=D('900'))
        InvestmentValuation.objects.create(user=self.user, date=date(2026, 2, 1), asset=self.etf, value=D('1100'))
        (row,) = stats.investments_summary(self.user)
        self.assertEqual(row['value'], D('1100'))
        self.assertEqual(row['gain'], D('100'))
        self.assertEqual(row['gain_pct'], D('10.0'))

    def test_cumulative_invested_includes_earlier_history(self):
        InvestmentMovement.objects.create(user=self.user, date=date(2025, 1, 1), asset=self.etf, amount=D('1000'))
        InvestmentMovement.objects.create(user=self.user, date=date(2026, 3, 1), asset=self.etf, amount=D('200'))
        self.assertEqual(stats.cumulative_invested(self.user, 2026, 3, 2), [D('1000'), D('1200')])


class CarTests(OwnedTestCase):
    def fuel(self, day, liters, km=None, full=True, price='1.700'):
        return FuelEntry.objects.create(
            user=self.user, date=day, liters=D(liters), price_per_liter=D(price), odometer_km=km, full_tank=full
        )

    def test_fuel_creates_and_updates_linked_transaction(self):
        entry = self.fuel(date(2026, 3, 1), '40')
        self.assertEqual(entry.transaction.amount, D('68.00'))
        self.assertEqual(entry.transaction.category.name, 'Combustível')
        entry.amount = D('85')
        entry.save()
        self.assertEqual(Transaction.objects.get().amount, D('85.00'))
        self.assertEqual(entry.liters, D('50.00'))

    def test_deleting_entry_deletes_transaction(self):
        toll = TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2.35'))
        self.assertEqual(Transaction.objects.count(), 1)
        toll.delete()
        self.assertEqual(Transaction.objects.count(), 0)

    def test_consumption_fill_to_fill_with_partial(self):
        first = self.fuel(date(2026, 2, 25), '40', km=10000)
        partial = self.fuel(date(2026, 3, 5), '10', full=False)
        second = self.fuel(date(2026, 3, 15), '20', km=10500)
        result = stats.fuel_consumption(FuelEntry.objects.order_by('date', 'id'))
        self.assertNotIn(first.id, result)
        self.assertNotIn(partial.id, result)
        self.assertEqual(result[second.id], D('6.00'))  # (10 + 20) L / 500 km

    def test_car_stats_cost_per_trip(self):
        self.fuel(date(2026, 3, 1), '40', km=10000)  # 68.00
        self.fuel(date(2026, 3, 20), '30', km=10500)  # 51.00 -> 6.00 L/100km
        TollEntry.objects.create(user=self.user, date=date(2026, 3, 2), amount=D('6.00'))
        for day in (2, 3, 4, 5, 6):
            CommuteDay.objects.create(user=self.user, date=date(2026, 3, day))
        result = stats.car_stats(self.user, 2026, 3)
        self.assertEqual(result['trips'], 5)
        self.assertEqual(result['fuel_cost'], D('119.00'))
        self.assertEqual(result['toll_cost'], D('6.00'))
        self.assertEqual(result['cost_per_trip'], D('25.00'))
        self.assertEqual(result['consumption'], D('6.00'))

    def test_car_stats_without_trips(self):
        self.assertIsNone(stats.car_stats(self.user, 2026, 3)['cost_per_trip'])
