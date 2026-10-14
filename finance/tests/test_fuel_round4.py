from datetime import date
from decimal import Decimal as D

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.urls import reverse

from finance.models import Category, FuelEntry, Transaction
from finance.tests.helpers import OwnedTestCase

BEFORE = [('finance', '0003_linked_protect_recurring_period')]
AFTER = [('finance', '0004_fuel_amount_paid')]


class FuelAmountMigrationTests(TransactionTestCase):
    """0004 copies the linked transaction's amount (fallback: liters × price, HALF_UP) and keeps the links."""

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        # Rows created here have no owner (no users exist), which 0006 refuses; clear them before migrating forward.
        with connection.cursor() as cursor:
            for table in ('finance_fuelentry', 'finance_transaction', 'finance_category'):
                cursor.execute(f'DELETE FROM {table}')
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_amount_copied_from_transaction(self):
        old = self.migrate(BEFORE)
        OldCategory = old.get_model('finance', 'Category')
        OldTransaction = old.get_model('finance', 'Transaction')
        OldFuel = old.get_model('finance', 'FuelEntry')
        cat, _ = OldCategory.objects.get_or_create(name='Combustível', kind='expense')
        rows = [
            # (liters, price, linked transaction amount = expected amount)
            (D('1.00'), D('1.005'), D('1.00')),  # copied from the transaction, not recomputed
            (D('3.00'), D('1.675'), D('5.03')),
            (D('35.52'), D('1.689'), D('59.99')),
            (D('40.00'), D('1.500'), D('60.00')),
        ]
        ids = []
        for i, (liters, price, tx_amount) in enumerate(rows):
            tx = OldTransaction.objects.create(
                date=date(2026, 1, i + 1), amount=tx_amount, category=cat, description='Abastecimento'
            )
            # bulk_create: the historical model inherits the mixin's save(), which needs the real methods.
            (entry,) = OldFuel.objects.bulk_create(
                [OldFuel(date=date(2026, 1, i + 1), liters=liters, price_per_liter=price, transaction=tx)]
            )
            ids.append((entry.pk, tx.pk))

        new = self.migrate(AFTER)
        NewFuel = new.get_model('finance', 'FuelEntry')
        for (pk, tx_pk), (liters, price, expected) in zip(ids, rows, strict=True):
            entry = NewFuel.objects.get(pk=pk)
            self.assertEqual(entry.amount, expected, (liters, price))
            self.assertEqual(entry.liters, liters)
            self.assertEqual(entry.price_per_liter, price)
            self.assertEqual(entry.transaction_id, tx_pk)
        self.assertFalse(NewFuel._meta.get_field('amount').null)

    def test_amount_computed_half_up_without_transaction(self):
        old = self.migrate(BEFORE)
        OldFuel = old.get_model('finance', 'FuelEntry')
        (entry,) = OldFuel.objects.bulk_create(
            [OldFuel(date=date(2026, 1, 1), liters=D('1.00'), price_per_liter=D('1.005'))]
        )
        new = self.migrate(AFTER)
        self.assertEqual(new.get_model('finance', 'FuelEntry').objects.get(pk=entry.pk).amount, D('1.01'))

    def test_migration_with_no_rows(self):
        self.migrate(BEFORE)
        new = self.migrate(AFTER)
        self.assertEqual(new.get_model('finance', 'FuelEntry').objects.count(), 0)


class FuelEditViewTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.entry = FuelEntry.objects.create(
            user=self.user,
            date=date(2026, 3, 1),
            amount=D('60'),
            price_per_liter=D('1.5'),
            odometer_km=1000,
            station='Galp',
        )
        self.url = reverse('finance:fuel_update', args=[self.entry.pk])

    def post(self, **changes):
        data = {
            'date': '2026-03-01',
            'amount': '60,00',
            'price_per_liter': '1,500',
            'odometer_km': '1000',
            'full_tank': 'on',
            'station': 'Galp',
        }
        data.update(changes)
        return self.client.post(self.url, data)

    def test_form_shows_amount_and_price_not_liters(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('id="id_amount"', html)
        self.assertIn('id="id_price_per_liter"', html)
        self.assertNotIn('id="id_liters"', html)
        self.assertIn('≈', html)

    def test_edit_price_recomputes_liters(self):
        response = self.post(price_per_liter='1,600')
        self.assertRedirects(response, reverse('finance:car'), fetch_redirect_response=False)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.price_per_liter, D('1.600'))
        self.assertEqual(self.entry.liters, D('37.50'))
        self.assertEqual(self.entry.transaction.amount, D('60.00'))
        self.assertEqual(self.entry.transaction.description, 'Abastecimento 37,50 L (Galp)')

    def test_edit_amount_updates_transaction_and_liters(self):
        tx_pk = self.entry.transaction_id
        self.post(amount='45,00')
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, D('45.00'))
        self.assertEqual(self.entry.liters, D('30.00'))
        self.assertEqual(self.entry.transaction_id, tx_pk)
        self.assertEqual(Transaction.objects.get(pk=tx_pk).amount, D('45.00'))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_edit_clearing_price_makes_liters_unknown(self):
        self.post(price_per_liter='')
        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.price_per_liter)
        self.assertIsNone(self.entry.liters)
        self.assertEqual(self.entry.transaction.description, 'Abastecimento (Galp)')

    def test_edit_rounds_liters_half_up(self):
        self.post(amount='1,01', price_per_liter='2,000')  # 0.505 L -> 0.51
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.liters, D('0.51'))

    def test_edit_rejects_invalid_values(self):
        for changes in ({'amount': ''}, {'amount': '0'}, {'amount': '-5'}, {'price_per_liter': '0'}, {'amount': 'abc'}):
            response = self.post(**changes)
            self.assertEqual(response.status_code, 200, changes)
            self.assertTrue(response.context['form'].errors, changes)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, D('60.00'))
        self.assertEqual(self.entry.liters, D('40.00'))


class CarPageUnpricedTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse('finance:car') + '?m=2026-03'

    def test_mixed_priced_and_unpriced(self):
        FuelEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('60'), price_per_liter=D('1.5'))
        FuelEntry.objects.create(user=self.user, date=date(2026, 3, 5), amount=D('30'), station='BP')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Abastecimento · BP', html)
        self.assertIn('L*', html)
        self.assertIn('Os litros e o €/L só contam os abastecimentos com preço', html)
        self.assertFalse(response.context['stats']['litres_complete'])

    def test_only_unpriced(self):
        FuelEntry.objects.create(user=self.user, date=date(2026, 3, 5), amount=D('30'))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Abastecimento', html)
        self.assertIsNone(response.context['stats']['avg_price'])
        # No litres shown, so no asterisk and no footnote referring to it.
        self.assertNotIn('Os litros e o €/L só contam os abastecimentos com preço', html)

    def test_all_priced_has_no_asterisk(self):
        FuelEntry.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('60'), price_per_liter=D('1.5'))
        html = self.client.get(self.url).content.decode()
        self.assertNotIn('L*', html)
        self.assertNotIn('só contam os abastecimentos com preço', html)


class LayoutTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_dashboard_has_bottom_add_and_desktop_fab(self):
        html = self.client.get(reverse('finance:dashboard')).content.decode()
        add_url = reverse('finance:transaction_create')
        self.assertIn(f'href="{add_url}" class="nav-add"', html)
        self.assertIn('class="btn btn-primary fab d-none d-lg-flex"', html)

    def test_category_table_is_responsive(self):
        market = Category.objects.create(user=self.user, name='Mercado X', kind=Category.EXPENSE)
        Transaction.objects.create(user=self.user, date=date(2026, 3, 10), amount=D('10'), category=market)
        response = self.client.get(reverse('finance:dashboard') + '?m=2026-03')
        html = response.content.decode()
        self.assertTrue(response.context['comparison'])
        self.assertIn('table-responsive', html)
        self.assertIn('<th class="text-end d-none d-sm-table-cell">Variação</th>', html)
        self.assertIn('<th class="text-end d-none d-md-table-cell">Média 6m</th>', html)
        self.assertIn('<th class="text-end d-none d-md-table-cell">Média 12m</th>', html)
        self.assertIn('small text-body-secondary d-sm-none', html)
