from datetime import date
from decimal import Decimal as D

from django.db.models import RestrictedError
from django.urls import reverse

from finance.forms import CategoryForm, TransactionForm
from finance.models import Category, FuelEntry, RecurringExpense, TollEntry, Transaction
from finance.services import recurring
from finance.tests.helpers import OwnedTestCase


class LinkedTransactionIntegrityTests(OwnedTestCase):
    def test_linked_transaction_is_protected(self):
        toll = TollEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('2.35'))
        with self.assertRaises(RestrictedError):
            toll.transaction.delete()

    def test_renamed_fuel_category_is_reused(self):
        first = FuelEntry.objects.create(
            user=self.user, date=date(2026, 3, 1), liters=D('30'), price_per_liter=D('1.7')
        )
        category = first.transaction.category
        category.name = 'Gasolina'
        category.save()
        second = FuelEntry.objects.create(
            user=self.user, date=date(2026, 3, 9), liters=D('30'), price_per_liter=D('1.7')
        )
        self.assertEqual(second.transaction.category, category)
        self.assertFalse(Category.objects.filter(name='Combustível').exists())

    def test_fuel_total_rounds_half_up(self):
        entry = FuelEntry(date=date(2026, 3, 1), liters=D('1.00'), price_per_liter=D('1.005'))
        entry.compute_liters()
        self.assertEqual(entry.total, D('1.01'))


class RecurringPeriodTests(OwnedTestCase):
    def test_moving_generated_transaction_does_not_regenerate(self):
        RecurringExpense.objects.create(
            user=self.user,
            name='Ginásio',
            amount=D('35'),
            category=Category.objects.get(user=self.user, name='Ginásio'),
            start_date=date(2026, 1, 1),
        )
        (tx,) = recurring.generate_for_month(self.user, 2026, 3)
        tx.date = date(2026, 4, 2)
        tx.save()
        self.assertEqual(recurring.pending_for_month(self.user, 2026, 3), [])
        self.assertEqual(len(recurring.pending_for_month(self.user, 2026, 4)), 1)


class FormTests(OwnedTestCase):
    def test_category_kind_locked_when_in_use(self):
        market = Category.objects.get(user=self.user, name='Supermercado')
        Transaction.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('10'), category=market)
        form = CategoryForm({'name': 'Supermercado', 'kind': 'income', 'color': '#fd7e14'}, instance=market)
        self.assertIn('kind', form.errors)

    def test_category_color_must_be_hex(self):
        form = CategoryForm({'name': 'X', 'kind': 'expense', 'color': 'red;x'})
        self.assertIn('color', form.errors)

    def test_decimal_accepts_comma_and_dot(self):
        market = Category.objects.get(user=self.user, name='Supermercado')
        for raw in ('12,50', '12.50'):
            with self.subTest(raw=raw):
                form = TransactionForm({'date': '2026-03-01', 'amount': raw, 'category': market.pk})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['amount'], D('12.50'))


class RecurringPageTests(OwnedTestCase):
    def test_fixed_total_excludes_ended(self):
        self.client.force_login(self.user)
        gym = Category.objects.get(user=self.user, name='Ginásio')
        RecurringExpense.objects.create(
            user=self.user, name='A', amount=D('10'), category=gym, start_date=date(2020, 1, 1)
        )
        RecurringExpense.objects.create(
            user=self.user,
            name='B',
            amount=D('99'),
            category=gym,
            start_date=date(2020, 1, 1),
            end_date=date(2021, 1, 1),
        )
        response = self.client.get(reverse('finance:recurring'))
        self.assertEqual(response.context['monthly_total'], D('10'))


class Round3Tests(OwnedTestCase):
    def test_kind_locked_when_used_by_recurring(self):
        gym = Category.objects.get(user=self.user, name='Ginásio')
        RecurringExpense.objects.create(
            user=self.user, name='G', amount=D('35'), category=gym, start_date=date(2026, 1, 1)
        )
        form = CategoryForm({'name': 'Ginásio', 'kind': 'income', 'color': '#d63384'}, instance=gym)
        self.assertIn('kind', form.errors)

    def test_new_category_form_is_valid(self):
        self.assertTrue(CategoryForm({'name': 'Nova', 'kind': 'income', 'color': '#123456'}).is_valid())
