import re
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import RequestFactory
from django.urls import reverse

from finance.forms import CategoryForm, FuelEntryForm, TransactionForm
from finance.models import (
    FUEL_CATEGORY,
    Category,
    FuelEntry,
    InvestmentAsset,
    InvestmentMovement,
    InvestmentValuation,
    RecurringExpense,
    TollEntry,
    Transaction,
)
from finance.services import stats
from finance.tests.helpers import OwnedTestCase


class LinkedSaveTests(OwnedTestCase):
    def test_failed_entry_save_rolls_back_transaction(self):
        toll = TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2.35'))
        count = Transaction.objects.count()
        duplicate = TollEntry(pk=toll.pk, user=self.user, date=date(2026, 3, 2), amount=D('9.99'))
        with self.assertRaises(IntegrityError):
            duplicate.save(force_insert=True)
        self.assertEqual(Transaction.objects.count(), count)

    def test_first_fuel_entry_uses_default_category(self):
        Category.objects.filter(name=FUEL_CATEGORY).delete()
        entry = FuelEntry.objects.create(
            user=self.user, date=date(2026, 3, 1), liters=D('10'), price_per_liter=D('1.5')
        )
        self.assertEqual(entry.transaction.category.name, FUEL_CATEGORY)
        self.assertEqual(entry.transaction.category.kind, Category.EXPENSE)

    def test_renamed_toll_category_is_reused_and_not_mixed_with_fuel(self):
        first = TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2'))
        toll_category = first.transaction.category
        toll_category.name = 'Via Verde'
        toll_category.save()
        fuel = FuelEntry.objects.create(user=self.user, date=date(2026, 3, 5), liters=D('10'), price_per_liter=D('1.5'))
        second = TollEntry.objects.create(user=self.user, date=date(2026, 3, 9), amount=D('3'))
        self.assertEqual(second.transaction.category, toll_category)
        self.assertNotEqual(fuel.transaction.category, toll_category)

    def test_editing_entry_updates_same_transaction(self):
        entry = TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2'))
        tx_id, count = entry.transaction_id, Transaction.objects.count()
        entry.amount = D('4.10')
        entry.date = date(2026, 4, 2)
        entry.save()
        tx = Transaction.objects.get(pk=tx_id)
        self.assertEqual((tx.amount, tx.date), (D('4.10'), date(2026, 4, 2)))
        self.assertEqual(Transaction.objects.count(), count)


class LinkedDeleteSignalTests(OwnedTestCase):
    def test_deleting_stale_instance_twice_does_not_fail(self):
        entry = TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2'))
        stale = TollEntry.objects.get(pk=entry.pk)
        tx_id = entry.transaction_id
        entry.delete()
        stale.delete()  # transaction is already gone; filter(pk=...).delete() must not raise
        self.assertFalse(Transaction.objects.filter(pk=tx_id).exists())

    def test_entry_without_transaction_leaves_others_alone(self):
        other = TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2'))
        entry = TollEntry.objects.create(user=self.user, date=date(2026, 3, 2), amount=D('3'))
        orphan_tx = entry.transaction_id
        TollEntry.objects.filter(pk=entry.pk).update(transaction=None)
        TollEntry.objects.get(pk=entry.pk).delete()
        self.assertTrue(Transaction.objects.filter(pk=other.transaction_id).exists())
        self.assertTrue(Transaction.objects.filter(pk=orphan_tx).exists())


class AdminLinkedTransactionTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser('root', password='pw-for-tests')
        self.client.force_login(self.admin)
        self.market = Category.objects.get(user=self.user, name='Supermercado')
        self.linked = TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2.35')).transaction
        self.plain = Transaction.objects.create(
            user=self.user, date=date(2026, 3, 1), amount=D('10'), category=self.market
        )

    def _post_data(self, tx):
        return {
            'user': self.user.pk,
            'date': '2026-03-05',
            'amount': '99',
            'category': self.market.pk,
            'description': 'x',
        }

    def test_permissions(self):
        model_admin = site._registry[Transaction]
        request = RequestFactory().get('/')
        request.user = self.admin
        self.assertFalse(model_admin.has_change_permission(request, self.linked))
        self.assertTrue(model_admin.has_change_permission(request, self.plain))
        self.assertTrue(model_admin.has_change_permission(request, None))

    def test_linked_change_page_is_read_only(self):
        url = reverse('admin:finance_transaction_change', args=[self.linked.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="_save"')
        response = self.client.post(url, self._post_data(self.linked))
        self.assertEqual(response.status_code, 403)
        self.linked.refresh_from_db()
        self.assertEqual(self.linked.amount, D('2.35'))

    def test_linked_cannot_be_deleted_in_admin(self):
        url = reverse('admin:finance_transaction_delete', args=[self.linked.pk])
        response = self.client.post(url, {'post': 'yes'})
        self.assertEqual(response.status_code, 200)  # shown as protected (RESTRICT) instead of deleted
        self.assertTrue(response.context['protected'])
        self.assertTrue(Transaction.objects.filter(pk=self.linked.pk).exists())

    def test_plain_transaction_still_editable(self):
        url = reverse('admin:finance_transaction_change', args=[self.plain.pk])
        response = self.client.post(url, self._post_data(self.plain))
        self.assertEqual(response.status_code, 302)
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.amount, D('99'))

    def test_changelist_renders(self):
        self.assertEqual(self.client.get(reverse('admin:finance_transaction_changelist')).status_code, 200)


class RecurringConstraintTests(OwnedTestCase):
    def test_manual_transactions_without_recurring_are_not_unique(self):
        market = Category.objects.get(user=self.user, name='Supermercado')
        for _ in range(3):
            Transaction.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('1'), category=market)
        self.assertEqual(Transaction.objects.filter(recurring__isnull=True).count(), 3)

    def test_same_recurring_in_different_periods(self):
        gym = Category.objects.get(user=self.user, name='Ginásio')
        expense = RecurringExpense.objects.create(
            user=self.user, name='G', amount=D('35'), category=gym, start_date=date(2026, 1, 1)
        )
        for month in (1, 2):
            Transaction.objects.create(
                user=self.user,
                date=date(2026, month, 1),
                amount=D('35'),
                category=gym,
                recurring=expense,
                recurring_period=date(2026, month, 1),
            )
        self.assertEqual(expense.transactions.count(), 2)


class CategoryFormRound2Tests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.market = Category.objects.get(user=self.user, name='Supermercado')

    def _data(self, **overrides):
        data = {'name': self.market.name, 'kind': self.market.kind, 'color': '#fd7e14', 'icon': ''}
        data.update(overrides)
        return data

    def test_kind_change_allowed_without_transactions(self):
        form = CategoryForm(self._data(kind='income'), instance=self.market)
        self.assertTrue(form.is_valid(), form.errors)

    def test_other_fields_editable_with_transactions(self):
        Transaction.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('10'), category=self.market)
        form = CategoryForm(self._data(name='Mercearia', color='#ABCDEF'), instance=self.market)
        self.assertTrue(form.is_valid(), form.errors)

    def test_kind_change_rejected_through_view(self):
        self.client.force_login(self.user)
        Transaction.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('10'), category=self.market)
        url = reverse('finance:category_update', args=[self.market.pk])
        response = self.client.post(url, self._data(kind='income'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('kind', response.context['form'].errors)
        self.market.refresh_from_db()
        self.assertEqual(self.market.kind, Category.EXPENSE)

    def test_color_validation(self):
        for color, ok in [('#ABCDEF', True), ('#a1b2c3', True), ('#fff', False), ('abcdef', False), ('#gggggg', False)]:
            with self.subTest(color=color):
                form = CategoryForm({'name': 'Nova', 'kind': 'expense', 'color': color})
                self.assertEqual(form.is_valid(), ok, form.errors)


class DecimalInputTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.market = Category.objects.get(user=self.user, name='Supermercado')

    def _fuel(self, price):
        return FuelEntryForm({'date': '2026-03-01', 'amount': '30', 'price_per_liter': price, 'full_tank': 'on'})

    def test_fuel_price_with_three_decimals(self):
        for raw in ('1,759', '1.759'):
            with self.subTest(raw=raw):
                form = self._fuel(raw)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['price_per_liter'], D('1.759'))

    def test_invalid_decimals(self):
        for raw in ('1.234,56', '1,7591', 'abc', '-1', '0'):
            with self.subTest(raw=raw):
                self.assertIn('price_per_liter', self._fuel(raw).errors)
        self.assertTrue(self._fuel('').is_valid())  # price per litre is optional

    def test_widget_is_text_with_decimal_keyboard(self):
        html = str(TransactionForm()['amount'])
        self.assertIn('type="text"', html)
        self.assertIn('inputmode="decimal"', html)
        self.assertIn('form-control', html)

    def test_edit_renders_comma_and_resubmits_unchanged(self):
        tx = Transaction.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('12.50'), category=self.market)
        url = reverse('finance:transaction_update', args=[tx.pk])
        self.assertContains(self.client.get(url), 'value="12,50"')
        data = {'date': '2026-03-01', 'amount': '12,50', 'category': self.market.pk, 'description': ''}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        tx.refresh_from_db()
        self.assertEqual(tx.amount, D('12.50'))

    def test_fuel_create_view_with_commas(self):
        data = {'date': '2026-03-01', 'amount': '72,86', 'price_per_liter': '1,799', 'full_tank': 'on', 'station': ''}
        self.assertEqual(self.client.post(reverse('finance:fuel_create'), data).status_code, 302)
        entry = FuelEntry.objects.get()
        self.assertEqual(entry.liters, D('40.50'))  # 72.86 / 1.799 = 40.5003
        self.assertEqual(entry.transaction.amount, D('72.86'))


class RoundingTests(OwnedTestCase):
    def test_exact_half_rounds_up_in_transaction_and_stats(self):
        # 0.50 * 1.010 = 0.505; banker's rounding would give 0.50.
        entry = FuelEntry.objects.create(
            user=self.user, date=date(2026, 3, 1), liters=D('0.50'), price_per_liter=D('1.010')
        )
        self.assertEqual(entry.transaction.amount, D('0.51'))
        self.assertEqual(stats.car_stats(self.user, 2026, 3)['fuel_cost'], D('0.51'))


class FixedCostTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.gym = Category.objects.get(user=self.user, name='Ginásio')

    def _expense(self, name, amount, **kwargs):
        kwargs.setdefault('start_date', date(2020, 1, 1))
        return RecurringExpense.objects.create(user=self.user, name=name, amount=D(amount), category=self.gym, **kwargs)

    def test_next_month_forecast_excludes_ended_and_inactive(self):
        self._expense('Ativa', '10', day_of_month=5)
        self._expense('Terminada', '20', end_date=date(2026, 3, 31))
        self._expense('Inativa', '30', active=False)
        self._expense('Termina depois do dia', '40', day_of_month=5, end_date=date(2026, 4, 10))
        self._expense('Termina antes do dia', '50', day_of_month=20, end_date=date(2026, 4, 10))
        items, total = stats.fixed_expenses_for_month(self.user, 2026, 4)
        self.assertEqual(total, D('50'))
        self.assertEqual({e.name for e in items}, {'Ativa', 'Termina depois do dia'})

    def test_dashboard_uses_forecast(self):
        self.client.force_login(self.user)
        self._expense('Ativa', '10')
        self._expense('Terminada', '20', end_date=date(2026, 3, 31))
        response = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'})
        self.assertEqual(response.context['fixed_next_total'], D('10'))
        self.assertEqual(response.context['fixed_next_count'], 1)

    def test_recurring_page_counts_expense_ending_today(self):
        self.client.force_login(self.user)
        today = date.today()
        self._expense('Hoje', '10', end_date=today)
        self._expense('Ontem', '20', end_date=today - timedelta(days=1))
        self._expense('Inativa', '30', active=False)
        response = self.client.get(reverse('finance:recurring'))
        self.assertEqual(response.context['monthly_total'], D('10'))


class GainPctTests(OwnedTestCase):
    def test_normal_gain_pct(self):
        asset = InvestmentAsset.objects.create(user=self.user, name='VWCE')
        InvestmentMovement.objects.create(user=self.user, date=date(2026, 1, 1), asset=asset, amount=D('100'))
        InvestmentValuation.objects.create(user=self.user, date=date(2026, 2, 1), asset=asset, value=D('110'))
        (row,) = stats.investments_summary(self.user)
        self.assertEqual((row['gain'], row['gain_pct']), (D('10'), D('10.0')))

    def test_valuation_without_movements(self):
        self.client.force_login(self.user)
        asset = InvestmentAsset.objects.create(user=self.user, name='Oferta')
        InvestmentValuation.objects.create(user=self.user, date=date(2026, 2, 1), asset=asset, value=D('50'))
        (row,) = stats.investments_summary(self.user)
        self.assertEqual(row['gain'], D('50'))
        self.assertIsNone(row['gain_pct'])
        self.assertEqual(self.client.get(reverse('finance:investments')).status_code, 200)


class CategoryFilterTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        market = Category.objects.get(user=self.user, name='Supermercado')
        Transaction.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('10'), category=market)
        Transaction.objects.create(
            user=self.user,
            date=date(2026, 3, 2),
            amount=D('5'),
            category=Category.objects.get(user=self.user, name='Ginásio'),
        )

    def _get(self, value):
        return self.client.get(reverse('finance:transactions'), {'m': '2026-03', 'category': value})

    def test_non_ascii_or_malformed_values_show_everything(self):
        for value in ('²', '١', ' 1', '-1', '1.0', '1e3', ''):
            with self.subTest(value=value):
                response = self._get(value)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context['transactions']), 2)

    def test_out_of_range_id_does_not_crash(self):
        response = self._get('9' * 30)
        self.assertEqual(response.status_code, 200)


class BaseTemplateTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _html(self, name):
        return self.client.get(reverse(name)).content.decode()

    def test_cdn_resources_have_sri(self):
        for name in ('finance:dashboard', 'finance:investments', 'finance:car'):
            tags = re.findall(r'<(?:script|link)[^>]+https://cdn[^>]*>', self._html(name))
            self.assertTrue(tags, name)
            for tag in tags:
                with self.subTest(page=name, tag=tag[:80]):
                    self.assertRegex(tag, r'integrity="sha384-[A-Za-z0-9+/=]{64}"')
                    self.assertIn('crossorigin="anonymous"', tag)

    def test_double_submit_guard_present(self):
        html = self._html('finance:car')
        self.assertIn("addEventListener('submit'", html)
        self.assertIn('dataset.submitting', html)
        self.assertIn("addEventListener('pageshow'", html)

    def test_has_fab_only_on_pages_with_fab(self):
        for name, expected in [('finance:dashboard', True), ('finance:transactions', True), ('finance:car', False)]:
            with self.subTest(page=name):
                self.assertEqual('<body class="has-fab">' in self._html(name), expected)
