from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.urls import reverse

from finance.models import FuelEntry, Transaction
from finance.tests.helpers import OwnedTestCase

MONTH = '?m=2026-03'


class FuelLitersOverflowViewTests(OwnedTestCase):
    """Values whose amount / price does not fit FuelEntry.liters (max 9999.99) are rejected in the form."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse('finance:fuel_create')

    def post(self, url=None, **changes):
        data = {'date': '2026-03-01', 'amount': '60,00', 'price_per_liter': '1,500', 'full_tank': 'on'}
        data.update(changes)
        return self.client.post(url or self.url, data)

    def assert_pages_ok(self):
        for name in ('finance:car', 'finance:dashboard'):
            response = self.client.get(reverse(name) + MONTH)
            self.assertEqual(response.status_code, 200, name)

    def assert_rejected(self, response):
        self.assertEqual(response.status_code, 200)
        errors = response.context['form'].errors
        self.assertIn('price_per_liter', errors)
        self.assertIn('Preço por litro demasiado baixo', errors['price_per_liter'][0])

    def test_bug_a_low_price_rejected_and_nothing_created(self):
        response = self.post(amount='6000,00', price_per_liter='0,500')  # 12000 L
        self.assert_rejected(response)
        self.assertEqual(FuelEntry.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assert_pages_ok()

    def test_upper_limit_accepted(self):
        response = self.post(amount='9999,99', price_per_liter='1,000')
        self.assertRedirects(response, reverse('finance:car'), fetch_redirect_response=False)
        entry = FuelEntry.objects.get()
        self.assertEqual(entry.liters, D('9999.99'))
        self.assertEqual(entry.transaction.amount, D('9999.99'))
        self.assert_pages_ok()

    def test_just_above_limit_rejected(self):
        for amount, price in (('10000,00', '1,000'), ('9999,99', '0,999')):
            response = self.post(amount=amount, price_per_liter=price)
            self.assert_rejected(response)
        self.assertEqual(FuelEntry.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assert_pages_ok()

    def test_large_amount_without_price_is_accepted(self):
        response = self.post(amount='6000,00', price_per_liter='')
        self.assertRedirects(response, reverse('finance:car'), fetch_redirect_response=False)
        self.assertIsNone(FuelEntry.objects.get().liters)
        self.assert_pages_ok()

    def test_edit_to_overflow_rejected_and_entry_unchanged(self):
        entry = FuelEntry.objects.create(
            user=self.user, date=date(2026, 3, 1), amount=D('60'), price_per_liter=D('1.5')
        )
        url = reverse('finance:fuel_update', args=[entry.pk])
        self.assert_rejected(self.post(url, amount='6000,00', price_per_liter='0,500'))
        entry.refresh_from_db()
        self.assertEqual(entry.amount, D('60.00'))
        self.assertEqual(entry.liters, D('40.00'))
        self.assertEqual(entry.transaction.amount, D('60.00'))
        self.assert_pages_ok()


class FuelLitersOverflowAdminTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser('admin', password='pw-for-tests'))
        self.url = reverse('admin:finance_fuelentry_add')

    def post(self, amount, price):
        return self.client.post(
            self.url,
            {
                'user': self.user.pk,
                'date': '2026-03-01',
                'amount': amount,
                'price_per_liter': price,
                'full_tank': 'on',
                '_save': 'Save',
            },
        )

    def test_add_page_loads(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_low_price_rejected(self):
        for amount, price in (('6000.00', '0.500'), ('10000.00', '1.000')):
            response = self.post(amount, price)
            self.assertEqual(response.status_code, 200, (amount, price))
            self.assertIn('price_per_liter', response.context['adminform'].form.errors)
        self.assertEqual(FuelEntry.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_upper_limit_accepted(self):
        response = self.post('9999.99', '1.000')
        self.assertEqual(response.status_code, 302)
        entry = FuelEntry.objects.get()
        self.assertEqual(entry.liters, D('9999.99'))
        self.assertEqual(self.client.get(reverse('admin:finance_fuelentry_changelist')).status_code, 200)


class FuelModelGuardsTests(OwnedTestCase):
    def test_compute_liters_with_amount_none_does_not_crash(self):
        entry = FuelEntry(date=date(2026, 3, 1), price_per_liter=D('1.5'))
        entry.compute_liters()
        self.assertIsNone(entry.amount)
        self.assertIsNone(entry.liters)

    def test_compute_liters_without_price(self):
        entry = FuelEntry(date=date(2026, 3, 1), amount=D('30'))
        entry.compute_liters()
        self.assertIsNone(entry.liters)

    def test_clean_rejects_overflow_and_accepts_limit(self):
        with self.assertRaises(ValidationError) as ctx:
            FuelEntry(user=self.user, date=date(2026, 3, 1), amount=D('10000'), price_per_liter=D('1')).full_clean()
        self.assertIn('price_per_liter', ctx.exception.message_dict)
        FuelEntry(user=self.user, date=date(2026, 3, 1), amount=D('9999.99'), price_per_liter=D('1')).full_clean()

    def test_full_clean_with_missing_amount_reports_amount_only(self):
        with self.assertRaises(ValidationError) as ctx:
            FuelEntry(date=date(2026, 3, 1), price_per_liter=D('1.5')).full_clean()
        self.assertIn('amount', ctx.exception.message_dict)
        self.assertNotIn('price_per_liter', ctx.exception.message_dict)
