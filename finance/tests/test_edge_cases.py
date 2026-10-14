"""Edge cases from the approved plan: year boundaries, empty months, sells above buys, fuel without km,
invalid forms and the transaction category filter."""

from datetime import date
from decimal import Decimal as D

from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.db.models import RestrictedError
from django.urls import reverse

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
from finance.tests.helpers import OwnedTestCase


def tx(day, amount, category):
    return Transaction.objects.create(user=category.user, date=day, amount=D(amount), category=category)


class RecurringEdgeTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.gym = Category.objects.get(user=self.user, name='Ginásio')

    def make(self, **kwargs):
        defaults = {'name': 'Ginásio', 'amount': D('35'), 'category': self.gym, 'start_date': date(2026, 1, 1)}
        return RecurringExpense.objects.create(user=self.user, **{**defaults, **kwargs})

    def test_leap_year_february(self):
        self.make(day_of_month=29, start_date=date(2027, 1, 1))
        (feb27,) = recurring.generate_for_month(self.user, 2027, 2)
        (feb28,) = recurring.generate_for_month(self.user, 2028, 2)
        self.assertEqual(feb27.date, date(2027, 2, 28))
        self.assertEqual(feb28.date, date(2028, 2, 29))

    def test_day_31_in_30_day_month(self):
        self.make(day_of_month=31)
        (tx_,) = recurring.generate_for_month(self.user, 2026, 4)
        self.assertEqual(tx_.date, date(2026, 4, 30))

    def test_yearly_started_on_feb_29(self):
        self.make(frequency=RecurringExpense.YEARLY, day_of_month=29, start_date=date(2024, 2, 29))
        (tx_,) = recurring.generate_for_month(self.user, 2025, 2)
        self.assertEqual(tx_.date, date(2025, 2, 28))

    def test_december_to_january(self):
        self.make(day_of_month=31, start_date=date(2026, 12, 31))
        self.assertEqual(recurring.generate_for_month(self.user, 2026, 11), [])
        self.assertEqual(recurring.generate_for_month(self.user, 2026, 12)[0].date, date(2026, 12, 31))
        self.assertEqual(recurring.generate_for_month(self.user, 2027, 1)[0].date, date(2027, 1, 31))

    def test_end_date_on_due_day_is_inclusive(self):
        self.make(name='Inclui', day_of_month=10, end_date=date(2026, 3, 10))
        self.make(name='Exclui', day_of_month=10, end_date=date(2026, 3, 9))
        self.assertEqual([e.name for e, _ in recurring.pending_for_month(self.user, 2026, 3)], ['Inclui'])

    def test_idempotent_per_month(self):
        self.make()
        self.assertEqual(len(recurring.generate_for_month(self.user, 2026, 3)), 1)
        self.assertEqual(len(recurring.generate_for_month(self.user, 2026, 4)), 1)
        self.assertEqual(recurring.generate_for_month(self.user, 2026, 3), [])
        self.assertEqual(recurring.generate_for_month(self.user, 2026, 4), [])
        self.assertEqual(Transaction.objects.count(), 2)

    def test_editing_generated_date_does_not_duplicate(self):
        self.make()
        (tx_,) = recurring.generate_for_month(self.user, 2026, 3)
        tx_.date = date(2026, 4, 2)  # user moves the payment to the next month
        tx_.save()
        self.assertEqual(recurring.generate_for_month(self.user, 2026, 3), [])
        self.assertEqual(len(recurring.generate_for_month(self.user, 2026, 4)), 1)
        self.assertEqual(Transaction.objects.count(), 2)

    def test_regenerates_after_generated_transaction_is_deleted(self):
        self.make()
        recurring.generate_for_month(self.user, 2026, 3)[0].delete()
        self.assertEqual(len(recurring.generate_for_month(self.user, 2026, 3)), 1)

    def test_duplicate_period_is_rejected_by_database(self):
        expense = self.make()
        recurring.generate_for_month(self.user, 2026, 3)
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            Transaction.objects.create(
                user=self.user,
                date=date(2026, 3, 5),
                amount=D('1'),
                category=self.gym,
                recurring=expense,
                recurring_period=date(2026, 3, 1),
            )

    def test_fixed_expenses_next_month_across_year(self):
        self.make(name='Seguro', frequency=RecurringExpense.YEARLY, amount=D('120'), start_date=date(2025, 1, 10))
        self.make(name='Telemóvel', amount=D('15'), start_date=date(2025, 1, 1))
        self.make(name='Inativa', active=False, start_date=date(2025, 1, 1))
        self.make(name='Acabou', start_date=date(2025, 1, 1), end_date=date(2026, 12, 31))
        self.make(name='Anual noutro mês', frequency=RecurringExpense.YEARLY, start_date=date(2025, 6, 1))
        items, total = stats.fixed_expenses_for_month(self.user, 2027, 1)
        self.assertEqual(sorted(e.name for e in items), ['Seguro', 'Telemóvel'])
        self.assertEqual(total, D('135'))

    def test_fixed_expenses_with_nothing(self):
        self.assertEqual(stats.fixed_expenses_for_month(self.user, 2026, 3), ([], D('0')))


class StatsEdgeTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.salary = Category.objects.get(user=self.user, name='Salário')
        self.market = Category.objects.get(user=self.user, name='Supermercado')
        self.etf = InvestmentAsset.objects.create(user=self.user, name='ETF')

    def move(self, day, amount, kind=InvestmentMovement.BUY, asset=None):
        return InvestmentMovement.objects.create(
            user=self.user, date=day, asset=asset or self.etf, amount=D(amount), kind=kind
        )

    def test_monthly_series_across_year(self):
        tx(date(2025, 11, 30), '100', self.market)
        tx(date(2025, 12, 1), '1000', self.salary)
        tx(date(2026, 1, 31), '50', self.market)
        tx(date(2026, 2, 1), '999', self.market)  # after the window
        tx(date(2025, 10, 31), '999', self.market)  # before the window
        nov, dec, jan = stats.monthly_series(self.user, 2026, 1, 3)
        self.assertEqual([(r['year'], r['month']) for r in (nov, dec, jan)], [(2025, 11), (2025, 12), (2026, 1)])
        self.assertEqual(nov['expense'], D('100'))
        self.assertEqual(dec['income'], D('1000'))
        self.assertEqual(dec['savings_rate'], D('100.0'))
        self.assertEqual(jan['expense'], D('50'))
        self.assertEqual(jan['saved'], D('-50'))

    def test_empty_month(self):
        (row,) = stats.monthly_series(self.user, 2026, 3, 1)
        self.assertEqual(
            (row['income'], row['expense'], row['invested'], row['balance']), (D('0'), D('0'), D('0'), D('0'))
        )
        self.assertIsNone(row['savings_rate'])
        self.assertEqual(stats.expenses_by_category(self.user, 2026, 3), [])
        self.assertEqual(stats.category_comparison(self.user, 2026, 3), [])
        self.assertEqual(stats.cumulative_invested(self.user, 2026, 3, 3), [D('0')] * 3)
        car = stats.car_stats(self.user, 2026, 3)
        self.assertEqual((car['trips'], car['fuel_cost'], car['toll_cost'], car['total']), (0, 0, 0, 0))
        self.assertIsNone(car['avg_price'])
        self.assertIsNone(car['consumption'])

    def test_negative_savings_rate(self):
        tx(date(2026, 3, 1), '1000', self.salary)
        tx(date(2026, 3, 2), '1500', self.market)
        (row,) = stats.monthly_series(self.user, 2026, 3, 1)
        self.assertEqual(row['savings_rate'], D('-50.0'))

    def test_category_comparison_in_january(self):
        for month in (10, 11, 12):
            tx(date(2025, month, 15), '100', self.market)
        tx(date(2025, 1, 15), '60', self.market)  # 12 months back, only in avg12
        tx(date(2024, 12, 15), '1000', self.market)  # 13 months back, ignored
        tx(date(2026, 2, 1), '1000', self.market)  # after the month, ignored
        tx(date(2026, 1, 20), '130', self.market)
        tx(date(2025, 12, 1), '5000', self.salary)  # income is not compared
        (row,) = stats.category_comparison(self.user, 2026, 1)
        self.assertEqual(row['current'], D('130'))
        self.assertEqual(row['previous'], D('100'))
        self.assertEqual(row['avg3'], D('100.00'))
        self.assertEqual(row['avg6'], D('50.00'))
        self.assertEqual(row['avg12'], D('30.00'))
        self.assertTrue(row['alert'])

    def test_alert_is_strictly_above_ratio(self):
        for month in (1, 2, 3):
            tx(date(2026, month, 10), '100', self.market)
        tx(date(2026, 4, 10), '120', self.market)
        (row,) = stats.category_comparison(self.user, 2026, 4)
        self.assertFalse(row['alert'])

    def test_category_only_in_history_and_new_category(self):
        fuel = Category.objects.get(user=self.user, name='Combustível')
        tx(date(2026, 2, 10), '80', fuel)  # history only
        tx(date(2026, 3, 10), '40', self.market)  # new this month, no history
        rows = {r['name']: r for r in stats.category_comparison(self.user, 2026, 3)}
        self.assertEqual(rows['Combustível']['current'], D('0'))
        self.assertEqual(rows['Combustível']['change_pct'], D('-100.0'))
        self.assertFalse(rows['Combustível']['alert'])
        self.assertIsNone(rows['Supermercado']['change_pct'])  # previous month is zero
        self.assertFalse(rows['Supermercado']['alert'])  # no history, no alert

    def test_sells_above_buys_in_month(self):
        tx(date(2026, 3, 1), '1000', self.salary)
        tx(date(2026, 3, 2), '200', self.market)
        self.move(date(2026, 3, 5), '100')
        self.move(date(2026, 3, 6), '300', InvestmentMovement.SELL)
        (row,) = stats.monthly_series(self.user, 2026, 3, 1)
        self.assertEqual(row['invested'], D('-200'))
        self.assertEqual(row['balance'], D('1000'))
        self.assertEqual(stats.cumulative_invested(self.user, 2026, 3, 1), [D('-200')])

    def test_summary_when_sold_more_than_bought_without_valuation(self):
        self.move(date(2026, 1, 1), '100')
        self.move(date(2026, 2, 1), '150', InvestmentMovement.SELL)
        (row,) = stats.investments_summary(self.user)
        self.assertEqual(row['invested'], D('-50'))
        self.assertIsNone(row['value'])
        self.assertIsNone(row['gain'])
        self.assertIsNone(row['gain_pct'])

    def test_summary_when_fully_sold_at_break_even_has_no_pct(self):
        self.move(date(2026, 1, 1), '100')
        self.move(date(2026, 2, 1), '100', InvestmentMovement.SELL)
        InvestmentValuation.objects.create(user=self.user, date=date(2026, 2, 1), asset=self.etf, value=D('50'))
        (row,) = stats.investments_summary(self.user)
        self.assertEqual(row['invested'], D('0'))
        self.assertEqual(row['gain'], D('50'))
        self.assertIsNone(row['gain_pct'])  # no division by zero

    def test_gain_pct_sign_when_sold_more_than_bought(self):
        """BUG: bought 1000, sold everything for 1500 (valuation 0). The gain is +500 but gain_pct is -100 %."""
        self.move(date(2026, 1, 1), '1000')
        self.move(date(2026, 6, 1), '1500', InvestmentMovement.SELL)
        InvestmentValuation.objects.create(user=self.user, date=date(2026, 6, 1), asset=self.etf, value=D('0'))
        (row,) = stats.investments_summary(self.user)
        self.assertEqual(row['gain'], D('500'))
        self.assertTrue(
            row['gain_pct'] is None or row['gain_pct'] > 0,
            f'gain is positive but gain_pct is {row["gain_pct"]}',
        )

    def test_asset_without_movements(self):
        InvestmentAsset.objects.create(user=self.user, name='Vazio')
        rows = {r['asset'].name: r for r in stats.investments_summary(self.user)}
        self.assertEqual(rows['Vazio']['invested'], D('0'))
        self.assertIsNone(rows['Vazio']['value'])
        self.assertEqual(stats.investments_by_kind(list(rows.values())), {'ETF': D('0')})


class CarEdgeTests(OwnedTestCase):
    def fuel(self, day, liters, km=None, full=True, price='1.700'):
        return FuelEntry.objects.create(
            user=self.user, date=day, liters=D(liters), price_per_liter=D(price), odometer_km=km, full_tank=full
        )

    def consumption(self):
        return stats.fuel_consumption(FuelEntry.objects.order_by('date', 'id'))

    def test_fuel_without_km_counts_cost_but_no_consumption(self):
        self.fuel(date(2026, 3, 1), '40')
        self.fuel(date(2026, 3, 15), '30')
        result = stats.car_stats(self.user, 2026, 3)
        self.assertEqual(result['fuel_cost'], D('119.00'))
        self.assertEqual(result['litres'], D('70'))
        self.assertEqual(result['fills'], 2)
        self.assertEqual(result['avg_price'], D('1.700'))
        self.assertIsNone(result['consumption'])
        self.assertEqual(result['consumption_by_entry'], {})

    def test_full_tank_without_km_between_two_readings(self):
        self.fuel(date(2026, 3, 1), '40', km=10000)
        self.fuel(date(2026, 3, 10), '20')  # full, no km: litres still count
        last = self.fuel(date(2026, 3, 20), '10', km=10600)
        self.assertEqual(self.consumption(), {last.id: D('5.00')})  # 30 L / 600 km

    def test_only_partial_fills(self):
        self.fuel(date(2026, 3, 1), '20', km=10000, full=False)
        self.fuel(date(2026, 3, 10), '20', km=10400, full=False)
        self.assertEqual(self.consumption(), {})

    def test_odometer_going_backwards_is_skipped(self):
        self.fuel(date(2026, 3, 1), '40', km=10000)
        wrong = self.fuel(date(2026, 3, 10), '30', km=9000)
        after = self.fuel(date(2026, 3, 20), '25', km=9500)
        result = self.consumption()
        self.assertNotIn(wrong.id, result)
        self.assertEqual(result[after.id], D('5.00'))  # restarts from the lower reading

    def test_same_odometer_has_no_value(self):
        self.fuel(date(2026, 3, 1), '40', km=10000)
        same = self.fuel(date(2026, 3, 2), '1', km=10000)
        self.assertNotIn(same.id, self.consumption())

    def test_consumption_uses_fill_from_previous_year(self):
        self.fuel(date(2025, 12, 28), '40', km=10000)
        self.fuel(date(2026, 1, 20), '35', km=10500)
        self.assertEqual(stats.car_stats(self.user, 2026, 1)['consumption'], D('7.00'))
        self.assertIsNone(stats.car_stats(self.user, 2025, 12)['consumption'])

    def test_trips_without_fuel(self):
        TollEntry.objects.create(user=self.user, date=date(2026, 3, 2), amount=D('3.00'))
        for day in (2, 3, 4):
            CommuteDay.objects.create(user=self.user, date=date(2026, 3, day))
        CommuteDay.objects.create(user=self.user, date=date(2026, 4, 1))  # other month
        result = stats.car_stats(self.user, 2026, 3)
        self.assertEqual(result['trips'], 3)
        self.assertEqual(result['cost_per_trip'], D('1.00'))
        self.assertIsNone(result['avg_price'])

    def test_linked_transactions_in_monthly_totals(self):
        self.fuel(date(2026, 3, 1), '10')  # 17.00
        TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2.50'))
        (row,) = stats.monthly_series(self.user, 2026, 3, 1)
        self.assertEqual(row['expense'], D('19.50'))
        names = {r['category__name'] for r in stats.expenses_by_category(self.user, 2026, 3)}
        self.assertEqual(names, {'Combustível', 'Portagens'})

    def test_changing_date_moves_transaction_to_other_month(self):
        entry = self.fuel(date(2026, 3, 31), '10')
        entry.date = date(2026, 4, 1)
        entry.save()
        self.assertEqual(Transaction.objects.get().date, date(2026, 4, 1))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_toll_description_defaults(self):
        toll = TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('1.20'))
        self.assertEqual(toll.transaction.description, 'Portagem')
        self.assertEqual(toll.transaction.category.name, 'Portagens')

    def test_queryset_delete_removes_linked_transactions(self):
        self.fuel(date(2026, 3, 1), '10')
        self.fuel(date(2026, 3, 2), '10')
        TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2.00'))
        FuelEntry.objects.all().delete()
        TollEntry.objects.all().delete()
        self.assertFalse(Transaction.objects.exists())

    def test_linked_transaction_cannot_be_deleted_directly(self):
        self.fuel(date(2026, 3, 1), '10')
        TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2.00'))
        with self.assertRaises(RestrictedError):
            Transaction.objects.all().delete()  # e.g. via admin
        self.assertEqual(Transaction.objects.count(), 2)


class ViewEdgeTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.market = Category.objects.get(user=self.user, name='Supermercado')
        self.salary = Category.objects.get(user=self.user, name='Salário')
        self.gym = Category.objects.get(user=self.user, name='Ginásio')

    def assert_rejected(self, url_name, data, model, field=None):
        before = model.objects.count()
        response = self.client.post(reverse(url_name), data)
        self.assertEqual(response.status_code, 200, f'{url_name} {data}')
        self.assertEqual(model.objects.count(), before)
        if field:
            self.assertIn(field, response.context['form'].errors)
        return response

    # --- Dashboard / month navigation ---

    def test_dashboard_empty_month(self):
        response = self.client.get(reverse('finance:dashboard') + '?m=2026-03')
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual(ctx['current']['income'], D('0'))
        self.assertIsNone(ctx['current']['savings_rate'])
        self.assertEqual(ctx['comparison'], [])
        self.assertEqual(ctx['alerts'], [])
        self.assertEqual(ctx['pending_recurring'], [])
        self.assertEqual(ctx['fixed_next_count'], 0)
        self.assertIsNone(ctx['car']['cost_per_trip'])
        self.assertEqual(ctx['chart_data']['income'], [0.0] * 12)
        self.assertEqual(ctx['chart_data']['savingsRate'], [None] * 12)

    def test_dashboard_year_transitions(self):
        RecurringExpense.objects.create(
            user=self.user,
            name='Seguro',
            amount=D('120'),
            category=self.gym,
            frequency=RecurringExpense.YEARLY,
            start_date=date(2025, 1, 5),
        )
        dec = self.client.get(reverse('finance:dashboard') + '?m=2026-12').context
        self.assertEqual((dec['prev_param'], dec['next_param']), ('2026-11', '2027-01'))
        self.assertEqual(dec['next_month_label'], 'janeiro 2027')
        self.assertEqual(dec['fixed_next_total'], D('120'))
        jan = self.client.get(reverse('finance:dashboard') + '?m=2026-01').context
        self.assertEqual((jan['prev_param'], jan['next_param']), ('2025-12', '2026-02'))
        self.assertEqual(jan['chart_data']['labels'][0], 'fev 25')
        self.assertEqual(jan['chart_data']['labels'][-1], 'jan 26')
        self.assertEqual(len(jan['pending_recurring']), 1)

    def test_invalid_month_param_falls_back_to_today(self):
        today = date.today()
        expected = f'{today.year}-{today.month:02d}'
        for value in ('2026-13', '2026-00', '1999-12', '2101-01', '2026-03-01', '2026', 'abc-de', ''):
            with self.subTest(m=value):
                response = self.client.get(reverse('finance:dashboard'), {'m': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['month_param'], expected)

    def test_month_param_limits_accepted(self):
        for value, expected in (('2000-01', '2000-01'), ('2100-12', '2100-12'), ('2026-3', '2026-03')):
            with self.subTest(m=value):
                response = self.client.get(reverse('finance:dashboard'), {'m': value})
                self.assertEqual(response.context['month_param'], expected)

    # --- Recurring generation button ---

    def test_generate_recurring_requires_post(self):
        self.assertEqual(self.client.get(reverse('finance:generate_recurring')).status_code, 405)

    def test_generate_recurring_requires_login(self):
        RecurringExpense.objects.create(
            user=self.user, name='X', amount=D('1'), category=self.gym, start_date=date(2026, 1, 1)
        )
        self.client.logout()
        response = self.client.post(reverse('finance:generate_recurring') + '?m=2026-03')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('login')))
        self.assertFalse(Transaction.objects.exists())

    def test_generate_recurring_twice_and_redirect(self):
        RecurringExpense.objects.create(
            user=self.user, name='X', amount=D('1'), category=self.gym, start_date=date(2026, 1, 1)
        )
        url = reverse('finance:generate_recurring') + '?m=2026-03'
        response = self.client.post(url)
        self.assertRedirects(response, reverse('finance:dashboard') + '?m=2026-03')
        self.client.post(url)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_generate_recurring_invalid_month_uses_current(self):
        RecurringExpense.objects.create(
            user=self.user, name='X', amount=D('1'), category=self.gym, start_date=date(2020, 1, 1)
        )
        self.client.post(reverse('finance:generate_recurring') + '?m=lixo')
        today = date.today()
        self.assertEqual(Transaction.objects.get().date, date(today.year, today.month, 1))

    # --- Transactions category filter ---

    def test_transactions_category_filter(self):
        tx(date(2026, 3, 1), '1000', self.salary)
        tx(date(2026, 3, 2), '40', self.market)
        tx(date(2026, 3, 3), '25', self.market)
        tx(date(2026, 2, 3), '99', self.market)  # other month
        url = reverse('finance:transactions')
        ctx = self.client.get(url, {'m': '2026-03', 'category': self.market.pk}).context
        self.assertEqual([t.amount for t in ctx['transactions']], [D('25'), D('40')])
        self.assertEqual(ctx['expense_total'], D('65'))
        self.assertEqual(ctx['income_total'], D('0'))
        ctx = self.client.get(url, {'m': '2026-03'}).context
        self.assertEqual(len(ctx['transactions']), 3)
        self.assertEqual(ctx['income_total'], D('1000'))

    def test_transactions_filter_unknown_or_invalid_category(self):
        tx(date(2026, 3, 2), '40', self.market)
        url = reverse('finance:transactions')
        response = self.client.get(url, {'m': '2026-03', 'category': '999999'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['transactions']), 0)
        for value in ('abc', '-1', '1.5', ''):
            with self.subTest(category=value):
                response = self.client.get(url, {'m': '2026-03', 'category': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context['transactions']), 1)

    def test_transactions_filter_unicode_digit(self):
        """BUG: '²'.isdigit() is True but int('²') fails, so the filter raises ValueError (HTTP 500)."""
        response = self.client.get(reverse('finance:transactions'), {'category': '²'})
        self.assertEqual(response.status_code, 200)

    # --- Invalid forms ---

    def test_invalid_transaction_forms(self):
        base = {'date': '2026-03-05', 'amount': '10', 'category': self.market.pk, 'description': ''}
        cases = [
            ({'amount': '0'}, 'amount'),
            ({'amount': '-5'}, 'amount'),
            ({'amount': 'abc'}, 'amount'),
            ({'amount': '12345678901'}, 'amount'),
            ({'amount': '1.234'}, 'amount'),
            ({'date': '2026-02-30'}, 'date'),
            ({'date': ''}, 'date'),
            ({'category': ''}, 'category'),
            ({'category': '999999'}, 'category'),
            ({'description': 'x' * 121}, 'description'),
        ]
        for change, field in cases:
            with self.subTest(change=change):
                self.assert_rejected('finance:transaction_create', {**base, **change}, Transaction, field)

    def test_invalid_recurring_forms(self):
        base = {
            'name': 'X',
            'amount': '10',
            'category': self.gym.pk,
            'frequency': 'monthly',
            'day_of_month': 1,
            'start_date': '2026-03-01',
            'end_date': '',
            'active': 'on',
        }
        cases = [
            ({'day_of_month': 0}, 'day_of_month'),
            ({'day_of_month': 32}, 'day_of_month'),
            ({'frequency': 'weekly'}, 'frequency'),
            ({'amount': '0'}, 'amount'),
            ({'start_date': ''}, 'start_date'),
            ({'name': ''}, 'name'),
        ]
        for change, field in cases:
            with self.subTest(change=change):
                self.assert_rejected('finance:recurring_create', {**base, **change}, RecurringExpense, field)
        # Same start and end date is fine.
        response = self.client.post(reverse('finance:recurring_create'), {**base, 'end_date': '2026-03-01'})
        self.assertRedirects(response, reverse('finance:recurring'))

    def test_recurring_update_end_before_start(self):
        item = RecurringExpense.objects.create(
            user=self.user, name='X', amount=D('1'), category=self.gym, start_date=date(2026, 3, 1)
        )
        response = self.client.post(
            reverse('finance:recurring_update', args=[item.pk]),
            {
                'name': 'X',
                'amount': '1',
                'category': self.gym.pk,
                'frequency': 'monthly',
                'day_of_month': 1,
                'start_date': '2026-03-01',
                'end_date': '2026-02-28',
            },
        )
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertIsNone(item.end_date)

    def test_duplicate_category_is_a_form_error(self):
        data = {'name': 'Supermercado', 'kind': 'expense', 'color': '#000000', 'icon': ''}
        self.assert_rejected('finance:category_create', data, Category)
        # Same name with the other kind is allowed.
        response = self.client.post(reverse('finance:category_create'), {**data, 'kind': 'income'})
        self.assertRedirects(response, reverse('finance:categories'))

    def test_invalid_category_kind_and_color(self):
        data = {'name': 'Nova', 'kind': 'expense', 'color': '#000000', 'icon': ''}
        self.assert_rejected('finance:category_create', {**data, 'kind': 'other'}, Category, 'kind')
        for color in ('red', '#12345', '#12345g', '"><b>'):
            with self.subTest(color=color):
                self.assert_rejected('finance:category_create', {**data, 'color': color}, Category, 'color')
        response = self.client.post(reverse('finance:category_create'), {**data, 'color': '#AbCdEf'})
        self.assertRedirects(response, reverse('finance:categories'))

    def test_invalid_investment_forms(self):
        asset = InvestmentAsset.objects.create(user=self.user, name='ETF')
        mv = {'date': '2026-03-01', 'asset': asset.pk, 'kind': 'buy', 'amount': '100', 'note': ''}
        for change, field in (({'amount': '0'}, 'amount'), ({'kind': 'hold'}, 'kind'), ({'asset': ''}, 'asset')):
            with self.subTest(change=change):
                self.assert_rejected('finance:movement_create', {**mv, **change}, InvestmentMovement, field)
        val = {'date': '2026-03-01', 'asset': asset.pk, 'value': '-1'}
        self.assert_rejected('finance:valuation_create', val, InvestmentValuation, 'value')
        # A valuation of zero is allowed (asset fully sold or worthless).
        response = self.client.post(reverse('finance:valuation_create'), {**val, 'value': '0'})
        self.assertRedirects(response, reverse('finance:investments'))
        self.assert_rejected('finance:asset_create', {'name': 'ETF', 'kind': 'etf'}, InvestmentAsset, 'name')

    def test_invalid_car_forms(self):
        fuel = {
            'date': '2026-03-01',
            'amount': '68',
            'price_per_liter': '1.7',
            'odometer_km': '',
            'full_tank': 'on',
            'station': '',
        }
        cases = [
            ({'amount': '0'}, 'amount'),
            ({'amount': '-1'}, 'amount'),
            ({'amount': ''}, 'amount'),
            ({'price_per_liter': '0'}, 'price_per_liter'),
            ({'odometer_km': '-5'}, 'odometer_km'),
            ({'odometer_km': '12.5'}, 'odometer_km'),
        ]
        for change, field in cases:
            with self.subTest(change=change):
                self.assert_rejected('finance:fuel_create', {**fuel, **change}, FuelEntry, field)
        self.assertFalse(Transaction.objects.exists())  # no orphan transaction from a rejected form
        toll = {'date': '2026-03-01', 'amount': '0', 'description': ''}
        self.assert_rejected('finance:toll_create', toll, TollEntry, 'amount')
        self.assertFalse(Transaction.objects.exists())

    # --- Fuel through the views keeps the transaction in sync ---

    def test_fuel_create_update_delete_via_views(self):
        data = {
            'date': '2026-03-01',
            'amount': '68',
            'price_per_liter': '1.7',
            'odometer_km': '',
            'full_tank': 'on',
            'station': 'Galp',
        }
        response = self.client.post(reverse('finance:fuel_create'), data)
        self.assertRedirects(response, reverse('finance:car'))
        entry = FuelEntry.objects.get()
        self.assertEqual(entry.transaction.amount, D('68.00'))
        self.assertTrue(entry.transaction.description.startswith('Abastecimento 40'))
        self.assertTrue(entry.transaction.description.endswith('L (Galp)'))

        self.client.post(
            reverse('finance:fuel_update', args=[entry.pk]), {**data, 'amount': '17', 'date': '2026-04-02'}
        )
        (linked,) = Transaction.objects.all()
        self.assertEqual((linked.amount, linked.date), (D('17.00'), date(2026, 4, 2)))

        self.client.post(reverse('finance:fuel_delete', args=[entry.pk]))
        self.assertFalse(FuelEntry.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    def test_fuel_without_km_on_car_page(self):
        FuelEntry.objects.create(user=self.user, date=date(2026, 3, 1), liters=D('30'), price_per_liter=D('1.7'))
        response = self.client.get(reverse('finance:car'), {'m': '2026-03'})
        self.assertEqual(response.status_code, 200)
        (entry,) = response.context['fuel']
        self.assertIsNone(entry.consumption)

    # --- Commute toggle ---

    def test_toggle_commute_requires_post_and_redirects_to_month(self):
        url = reverse('finance:toggle_commute')
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {'date': '2026-12-31'})
        self.assertRedirects(response, reverse('finance:car') + '?m=2026-12')
        response = self.client.post(url, {})
        self.assertRedirects(response, reverse('finance:car'))
        self.assertEqual(CommuteDay.objects.count(), 1)

    def test_car_page_month_across_year(self):
        response = self.client.get(reverse('finance:car'), {'m': '2026-01'})
        self.assertEqual(response.context['prev_param'], '2025-12')
        in_month = [d['date'] for week in response.context['weeks'] for d in week if d['in_month']]
        self.assertEqual((in_month[0], in_month[-1], len(in_month)), (date(2026, 1, 1), date(2026, 1, 31), 31))

    # --- Investments page with sells above buys ---

    def test_investments_page_with_sells_above_buys(self):
        asset = InvestmentAsset.objects.create(user=self.user, name='ETF')
        InvestmentMovement.objects.create(user=self.user, date=date(2026, 1, 1), asset=asset, amount=D('100'))
        InvestmentMovement.objects.create(
            user=self.user, date=date(2026, 2, 1), asset=asset, amount=D('300'), kind=InvestmentMovement.SELL
        )
        response = self.client.get(reverse('finance:investments'), {'m': '2026-02'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_invested'], D('-200'))
        self.assertIsNone(response.context['total_value'])
        self.assertEqual(response.context['chart_data']['cumulative'][-2:], [100.0, -200.0])
        self.assertEqual(self.client.get(reverse('finance:dashboard'), {'m': '2026-02'}).status_code, 200)
