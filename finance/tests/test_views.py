from datetime import date
from decimal import Decimal as D

from django.urls import reverse

from finance.models import Category, CommuteDay, FuelEntry, InvestmentAsset, RecurringExpense, Transaction
from finance.tests.helpers import OwnedTestCase


class ViewTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(reverse('finance:dashboard'))
        self.assertRedirects(response, f'{reverse("login")}?next=/')

    def test_service_worker_is_public(self):
        self.client.logout()
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript')

    def test_pages_render(self):
        market = Category.objects.get(user=self.user, name='Supermercado')
        tx = Transaction.objects.create(user=self.user, date=date.today(), amount=D('10'), category=market)
        fuel = FuelEntry.objects.create(user=self.user, date=date.today(), liters=D('30'), price_per_liter=D('1.7'))
        asset = InvestmentAsset.objects.create(user=self.user, name='ETF')
        urls = [
            reverse('finance:dashboard'),
            reverse('finance:dashboard') + '?m=2025-01',
            reverse('finance:dashboard') + '?m=lixo',
            reverse('finance:transactions'),
            reverse('finance:transactions') + f'?category={market.pk}',
            reverse('finance:categories'),
            reverse('finance:recurring'),
            reverse('finance:investments'),
            reverse('finance:car'),
            reverse('finance:transaction_create'),
            reverse('finance:transaction_update', args=[tx.pk]),
            reverse('finance:fuel_update', args=[fuel.pk]),
            reverse('finance:asset_update', args=[asset.pk]),
            reverse('finance:category_create'),
            reverse('finance:recurring_create'),
            reverse('finance:movement_create'),
            reverse('finance:valuation_create'),
            reverse('finance:toll_create'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_create_transaction(self):
        market = Category.objects.get(user=self.user, name='Supermercado')
        response = self.client.post(
            reverse('finance:transaction_create'),
            {'date': '2026-03-05', 'amount': '42.50', 'category': market.pk, 'description': 'Compras'},
        )
        self.assertRedirects(response, reverse('finance:transactions'))
        self.assertEqual(Transaction.objects.get().amount, D('42.50'))

    def test_linked_transaction_cannot_be_edited_directly(self):
        fuel = FuelEntry.objects.create(user=self.user, date=date.today(), liters=D('30'), price_per_liter=D('1.7'))
        response = self.client.get(reverse('finance:transaction_update', args=[fuel.transaction_id]))
        self.assertRedirects(response, reverse('finance:car'))
        response = self.client.post(reverse('finance:transaction_delete', args=[fuel.transaction_id]))
        self.assertTrue(Transaction.objects.filter(pk=fuel.transaction_id).exists())

    def test_toggle_commute(self):
        url = reverse('finance:toggle_commute')
        self.client.post(url, {'date': '2026-03-02'})
        self.assertTrue(CommuteDay.objects.filter(date=date(2026, 3, 2)).exists())
        self.client.post(url, {'date': '2026-03-02'})
        self.assertFalse(CommuteDay.objects.exists())
        response = self.client.post(url, {'date': 'not-a-date'})
        self.assertEqual(response.status_code, 302)

    def test_generate_recurring_button(self):
        RecurringExpense.objects.create(
            user=self.user,
            name='Telemóvel',
            amount=D('15'),
            category=Category.objects.get(user=self.user, name='Telemóvel'),
            start_date=date(2026, 1, 1),
        )
        self.client.post(reverse('finance:generate_recurring') + '?m=2026-03')
        self.assertEqual(Transaction.objects.get().date, date(2026, 3, 1))

    def test_category_in_use_cannot_be_deleted(self):
        market = Category.objects.get(user=self.user, name='Supermercado')
        Transaction.objects.create(user=self.user, date=date.today(), amount=D('10'), category=market)
        response = self.client.post(reverse('finance:category_delete', args=[market.pk]))
        self.assertRedirects(response, reverse('finance:categories'))
        self.assertTrue(Category.objects.filter(pk=market.pk).exists())

    def test_recurring_end_before_start_is_rejected(self):
        response = self.client.post(
            reverse('finance:recurring_create'),
            {
                'name': 'X',
                'amount': '10',
                'category': Category.objects.get(user=self.user, name='Ginásio').pk,
                'frequency': 'monthly',
                'day_of_month': 1,
                'start_date': '2026-03-01',
                'end_date': '2026-01-01',
                'active': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RecurringExpense.objects.exists())
