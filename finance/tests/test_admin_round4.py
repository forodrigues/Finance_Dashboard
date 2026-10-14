"""Admin edge cases with several accounts: bulk deletes mixing linked and plain records, duplicates on the add page
(per-user unique constraints), RESTRICT/SET_NULL/CASCADE from the admin, and the strict migration refusal."""

from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

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

from .test_admin_round3 import AdminTwoAccountsBase


def form_errors(response):
    form = response.context['adminform'].form
    return form, [message for messages in form.errors.values() for message in messages]


class AdminBulkDeleteMixedTests(AdminTwoAccountsBase):
    def bulk_delete(self, model, ids, confirm=True):
        data = {'action': 'delete_selected', '_selected_action': ids}
        if confirm:
            data['post'] = 'yes'
        return self.client.post(self.url(model, 'changelist'), data)

    def test_mixed_plain_and_linked_transactions_of_both_users_delete_nothing(self):
        plain = [self.objs['a']['transaction'].pk, self.objs['b']['transaction'].pk]
        linked = [self.objs['a']['fuel'].transaction_id, self.objs['b']['toll'].transaction_id]
        before = Transaction.objects.count()
        confirm = self.bulk_delete('transaction', plain + linked, confirm=False)
        self.assertEqual(confirm.status_code, 200)
        self.assertTrue(confirm.context['protected'])
        response = self.bulk_delete('transaction', plain + linked)
        self.assertIn(response.status_code, (200, 302))
        # All or nothing: the plain ones are kept too.
        self.assertEqual(Transaction.objects.count(), before)
        self.assertEqual(FuelEntry.objects.count(), 2)
        self.assertEqual(TollEntry.objects.count(), 2)

    def test_plain_and_recurring_transactions_of_both_users_are_deleted(self):
        generated = Transaction.objects.create(
            user=self.b,
            date=date(2026, 3, 1),
            amount=D('500'),
            category=self.objs['b']['category'],
            recurring=self.objs['b']['recurring'],
            recurring_period=date(2026, 3, 1),
        )
        ids = [self.objs['a']['transaction'].pk, self.objs['b']['transaction'].pk, generated.pk]
        confirm = self.bulk_delete('transaction', ids, confirm=False)
        self.assertEqual(confirm.status_code, 200)
        self.assertFalse(confirm.context['protected'])
        self.assertEqual(self.bulk_delete('transaction', ids).status_code, 302)
        self.assertFalse(Transaction.objects.filter(pk__in=ids).exists())
        self.assertTrue(RecurringExpense.objects.filter(pk=self.objs['b']['recurring'].pk).exists())
        # Linked fuel/toll transactions of both users are untouched.
        for entry in [*FuelEntry.objects.all(), *TollEntry.objects.all()]:
            self.assertTrue(Transaction.objects.filter(pk=entry.transaction_id).exists())

    def test_bulk_delete_fuel_of_one_user_and_toll_of_other(self):
        fuel_a, toll_b = self.objs['a']['fuel'], self.objs['b']['toll']
        self.assertEqual(self.bulk_delete('fuelentry', [fuel_a.pk]).status_code, 302)
        self.assertEqual(self.bulk_delete('tollentry', [toll_b.pk]).status_code, 302)
        self.assertFalse(Transaction.objects.filter(pk__in=[fuel_a.transaction_id, toll_b.transaction_id]).exists())
        self.assertTrue(Transaction.objects.filter(pk=self.objs['b']['fuel'].transaction_id).exists())
        self.assertTrue(Transaction.objects.filter(pk=self.objs['a']['toll'].transaction_id).exists())
        self.assertEqual(Transaction.objects.filter(user=self.a).count(), 2)  # plain + toll
        self.assertEqual(Transaction.objects.filter(user=self.b).count(), 2)  # plain + fuel

    def test_bulk_delete_all_fuel_and_toll_of_both_users(self):
        fuel_ids = list(FuelEntry.objects.values_list('pk', flat=True))
        toll_ids = list(TollEntry.objects.values_list('pk', flat=True))
        self.assertEqual(self.bulk_delete('fuelentry', fuel_ids).status_code, 302)
        self.assertEqual(self.bulk_delete('tollentry', toll_ids).status_code, 302)
        self.assertEqual(
            sorted(Transaction.objects.values_list('pk', flat=True)),
            sorted([self.objs['a']['transaction'].pk, self.objs['b']['transaction'].pk]),
        )
        # Plain transactions can now be bulk-deleted as well.
        ids = list(Transaction.objects.values_list('pk', flat=True))
        self.assertEqual(self.bulk_delete('transaction', ids).status_code, 302)
        self.assertFalse(Transaction.objects.exists())

    def test_bulk_delete_assets_mixed_in_use_and_unused_deletes_nothing(self):
        unused = InvestmentAsset.objects.create(user=self.b, name='Sem movimentos')
        ids = [self.objs['a']['asset'].pk, unused.pk]
        confirm = self.bulk_delete('investmentasset', ids, confirm=False)
        self.assertTrue(confirm.context['protected'])
        self.bulk_delete('investmentasset', ids)
        self.assertEqual(InvestmentAsset.objects.filter(pk__in=ids).count(), 2)

    def test_delete_asset_with_only_valuations_cascades(self):
        asset = InvestmentAsset.objects.create(user=self.a, name='SÃ³ avaliaÃ§Ãµes')
        InvestmentValuation.objects.create(user=self.a, date=date(2026, 3, 1), asset=asset, value=D('1'))
        response = self.client.post(self.url('investmentasset', 'delete', asset.pk), {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(InvestmentValuation.objects.filter(asset_id=asset.pk).exists())

    def test_delete_recurring_keeps_generated_transactions(self):
        recurring = self.objs['a']['recurring']
        generated = Transaction.objects.create(
            user=self.a,
            date=date(2026, 3, 1),
            amount=D('500'),
            category=self.objs['a']['category'],
            recurring=recurring,
            recurring_period=date(2026, 3, 1),
        )
        response = self.client.post(self.url('recurringexpense', 'delete', recurring.pk), {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        generated.refresh_from_db()
        self.assertIsNone(generated.recurring_id)
        self.assertEqual(generated.user, self.a)


class AdminAddDuplicateTests(AdminTwoAccountsBase):
    def test_duplicate_asset_for_same_user_is_a_single_form_error(self):
        before = InvestmentAsset.objects.count()
        data = {'user': self.a.pk, 'name': 'ETF a', 'kind': 'etf'}
        response = self.client.post(self.url('investmentasset', 'add'), data)
        self.assertEqual(response.status_code, 200)
        form, messages = form_errors(response)
        self.assertFalse(form.is_valid())
        self.assertEqual(len(messages), 1, messages)
        self.assertEqual(InvestmentAsset.objects.count(), before)

    def test_same_asset_name_for_other_user_on_add_is_allowed(self):
        data = {'user': self.b.pk, 'name': 'ETF a', 'kind': 'etf'}
        self.assertEqual(self.client.post(self.url('investmentasset', 'add'), data).status_code, 302)
        self.assertTrue(InvestmentAsset.objects.filter(user=self.b, name='ETF a').exists())

    def test_duplicate_category_for_same_user_on_add(self):
        before = Category.objects.count()
        data = {'user': self.a.pk, 'name': 'Supermercado', 'kind': 'expense', 'color': '#123456', 'icon': ''}
        response = self.client.post(self.url('category', 'add'), data)
        self.assertEqual(response.status_code, 200)
        _, messages = form_errors(response)
        self.assertEqual(len(messages), 1, messages)
        self.assertEqual(Category.objects.count(), before)

    def test_same_category_name_with_other_kind_on_add_is_allowed(self):
        data = {'user': self.a.pk, 'name': 'Supermercado', 'kind': 'income', 'color': '#123456', 'icon': ''}
        self.assertEqual(self.client.post(self.url('category', 'add'), data).status_code, 302)

    def test_duplicate_commute_day_for_same_user_on_add(self):
        before = CommuteDay.objects.count()
        response = self.client.post(self.url('commuteday', 'add'), {'user': self.a.pk, 'date': '2026-03-05'})
        self.assertEqual(response.status_code, 200)
        _, messages = form_errors(response)
        self.assertEqual(len(messages), 1, messages)
        self.assertEqual(CommuteDay.objects.count(), before)

    def test_same_commute_day_for_other_user_on_add_is_allowed(self):
        CommuteDay.objects.filter(user=self.b).delete()
        response = self.client.post(self.url('commuteday', 'add'), {'user': self.b.pk, 'date': '2026-03-05'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CommuteDay.objects.filter(date=date(2026, 3, 5)).count(), 2)

    def test_duplicate_asset_without_owner_on_add_reports_missing_owner(self):
        response = self.client.post(self.url('investmentasset', 'add'), {'name': 'ETF a', 'kind': 'etf'})
        self.assertEqual(response.status_code, 200)
        form, _ = form_errors(response)
        self.assertIn('user', form.errors)

    def test_transaction_with_own_recurring_on_add_is_accepted(self):
        a = self.objs['a']
        data = {
            'user': self.a.pk,
            'date': '2026-03-10',
            'amount': '5',
            'category': a['category'].pk,
            'recurring': a['recurring'].pk,
        }
        self.assertEqual(self.client.post(self.url('transaction', 'add'), data).status_code, 302)
        self.assertTrue(Transaction.objects.filter(recurring=a['recurring'], date=date(2026, 3, 10)).exists())


class TransactionCleanRecurringOwnerTests(AdminTwoAccountsBase):
    def test_full_clean_rejects_other_users_recurring(self):
        tx = Transaction(
            user=self.a,
            date=date(2026, 3, 10),
            amount=D('5'),
            category=self.objs['a']['category'],
            recurring=self.objs['b']['recurring'],
        )
        with self.assertRaises(ValidationError) as ctx:
            tx.full_clean()
        self.assertIn('recurring', ctx.exception.message_dict)
        self.assertNotIn('category', ctx.exception.message_dict)

    def test_full_clean_reports_category_and_recurring_together(self):
        tx = Transaction(
            user=self.a,
            date=date(2026, 3, 10),
            amount=D('5'),
            category=self.objs['b']['category'],
            recurring=self.objs['b']['recurring'],
        )
        with self.assertRaises(ValidationError) as ctx:
            tx.full_clean()
        self.assertEqual(set(ctx.exception.message_dict) & {'category', 'recurring'}, {'category', 'recurring'})


class OwnerMigrationStrictRefusalTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)

    def leaf(self):
        return MigrationExecutor(connection).loader.graph.leaf_nodes()

    def tearDown(self):
        self.migrate(self.leaf())

    def test_reverse_refused_when_only_a_normal_user_owns_data(self):
        owner = User.objects.create_user('owner', password='x')
        FuelEntry.objects.create(user=owner, date=date(2026, 3, 2), amount=D('50'))
        root = User.objects.create_superuser('root', password='x')
        Category.objects.filter(user=root).delete()
        with self.assertRaises(IrreversibleError):
            self.migrate([('finance', '0004_fuel_amount_paid')])
        self.migrate(self.leaf())
        self.assertEqual(FuelEntry.objects.get().user, owner)
        self.assertEqual(FuelEntry.objects.get().transaction.user, owner)
        self.assertFalse(Category.objects.filter(user=root).exists())

    def test_reverse_allowed_when_superuser_owns_all_data_even_with_other_empty_user(self):
        root = User.objects.create_superuser('root', password='x')
        FuelEntry.objects.create(user=root, date=date(2026, 3, 2), amount=D('50'))
        other = User.objects.create_user('other', password='x')
        Category.objects.filter(user=other).delete()
        self.migrate([('finance', '0004_fuel_amount_paid')])
        self.migrate(self.leaf())
        self.assertEqual(FuelEntry.objects.get().user, root)
        # Forward again gives the other user the default categories.
        self.assertEqual(Category.objects.filter(user=other).count(), Category.objects.filter(user=root).count())

    def test_reverse_refused_when_other_user_only_has_default_categories(self):
        root = User.objects.create_superuser('root', password='x')
        User.objects.create_user('other', password='x')
        InvestmentMovement.objects.create(
            user=root,
            date=date(2026, 3, 1),
            asset=InvestmentAsset.objects.create(user=root, name='ETF'),
            amount=D('1'),
        )
        with self.assertRaises(IrreversibleError):
            self.migrate([('finance', '0004_fuel_amount_paid')])
