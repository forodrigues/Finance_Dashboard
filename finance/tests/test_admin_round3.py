"""Admin with several accounts: every page renders, cross-owner links are refused, owners stay fixed,
deleting a user with data works, and the owner migrations can be reversed only with a single account."""

from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
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

MODELS = [
    'category',
    'transaction',
    'recurringexpense',
    'investmentasset',
    'investmentmovement',
    'investmentvaluation',
    'fuelentry',
    'tollentry',
    'commuteday',
]


class AdminTwoAccountsBase(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser('root', password='x')
        self.a = User.objects.create_user('a', password='x')
        self.b = User.objects.create_user('b', password='x')
        self.client.force_login(self.root)
        self.objs = {}
        for user in (self.a, self.b):
            market = Category.objects.get(user=user, name='Supermercado')
            asset = InvestmentAsset.objects.create(user=user, name=f'ETF {user}')
            recurring = RecurringExpense.objects.create(
                user=user, name=f'Renda {user}', amount=D('500'), category=market, start_date=date(2026, 1, 1)
            )
            self.objs[user.username] = {
                'category': market,
                'asset': asset,
                'recurring': recurring,
                'transaction': Transaction.objects.create(
                    user=user, date=date(2026, 3, 1), amount=D('10'), category=market, description='compras'
                ),
                'movement': InvestmentMovement.objects.create(
                    user=user, date=date(2026, 3, 1), asset=asset, amount=D('100')
                ),
                'valuation': InvestmentValuation.objects.create(
                    user=user, date=date(2026, 3, 2), asset=asset, value=D('110')
                ),
                'fuel': FuelEntry.objects.create(
                    user=user, date=date(2026, 3, 3), amount=D('60'), price_per_liter=D('1.8')
                ),
                'toll': TollEntry.objects.create(user=user, date=date(2026, 3, 4), amount=D('2.5')),
                'commute': CommuteDay.objects.create(user=user, date=date(2026, 3, 5)),
            }

    def url(self, model, action, *args):
        return reverse(f'admin:finance_{model}_{action}', args=args)


class AdminPagesRenderTests(AdminTwoAccountsBase):
    def test_changelists_render_and_filter_by_user(self):
        for model in MODELS:
            with self.subTest(model=model):
                self.assertEqual(self.client.get(self.url(model, 'changelist')).status_code, 200)
                response = self.client.get(self.url(model, 'changelist'), {'user__id__exact': self.a.pk})
                self.assertEqual(response.status_code, 200)
                owners = {obj.user_id for obj in response.context['cl'].result_list}
                self.assertEqual(owners, {self.a.pk})

    def test_add_pages_render_with_owner_field(self):
        for model in MODELS:
            with self.subTest(model=model):
                response = self.client.get(self.url(model, 'add'))
                self.assertEqual(response.status_code, 200)
                self.assertIn('user', response.context['adminform'].form.fields)

    def test_change_pages_render_with_owner_read_only(self):
        a = self.objs['a']
        pks = {
            'category': a['category'].pk,
            'transaction': a['transaction'].pk,
            'recurringexpense': a['recurring'].pk,
            'investmentasset': a['asset'].pk,
            'investmentmovement': a['movement'].pk,
            'investmentvaluation': a['valuation'].pk,
            'fuelentry': a['fuel'].pk,
            'tollentry': a['toll'].pk,
            'commuteday': a['commute'].pk,
        }
        for model, pk in pks.items():
            with self.subTest(model=model):
                response = self.client.get(self.url(model, 'change', pk))
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('user', response.context['adminform'].form.fields)
                self.assertContains(response, '>a<')

    def test_linked_transaction_is_view_only(self):
        tx = self.objs['a']['fuel'].transaction
        response = self.client.get(self.url('transaction', 'change', tx.pk))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_change_permission'])
        # RESTRICT: the delete page lists it as protected and nothing is deleted.
        response = self.client.post(self.url('transaction', 'delete', tx.pk), {'post': 'yes'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['protected'])
        self.assertTrue(Transaction.objects.filter(pk=tx.pk).exists())

    def test_fk_choices_name_their_owner(self):
        html = self.client.get(self.url('investmentmovement', 'add')).content.decode()
        self.assertIn('ETF a (a)', html)
        self.assertIn('ETF b (b)', html)
        html = self.client.get(self.url('recurringexpense', 'add')).content.decode()
        self.assertIn('Supermercado (b)', html)
        self.assertIn('Salário (a)', html)  # recurring income (e.g. salary) is allowed


class AdminCrossOwnerAddTests(AdminTwoAccountsBase):
    def assert_rejected(self, model, data, field, queryset):
        before = queryset.count()
        response = self.client.post(self.url(model, 'add'), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(field, response.context['adminform'].form.errors)
        self.assertEqual(queryset.count(), before)

    def test_transaction_with_other_users_category(self):
        data = {'user': self.a.pk, 'date': '2026-03-10', 'amount': '5', 'category': self.objs['b']['category'].pk}
        self.assert_rejected('transaction', data, 'category', Transaction.objects)

    def test_transaction_with_other_users_recurring(self):
        data = {
            'user': self.a.pk,
            'date': '2026-03-10',
            'amount': '5',
            'category': self.objs['a']['category'].pk,
            'recurring': self.objs['b']['recurring'].pk,
        }
        self.assert_rejected('transaction', data, 'recurring', Transaction.objects)

    def test_recurring_with_other_users_category(self):
        data = {
            'user': self.a.pk,
            'name': 'Ginásio',
            'amount': '30',
            'category': self.objs['b']['category'].pk,
            'frequency': 'monthly',
            'day_of_month': '1',
            'start_date': '2026-01-01',
            'active': 'on',
        }
        self.assert_rejected('recurringexpense', data, 'category', RecurringExpense.objects)

    def test_movement_with_other_users_asset(self):
        data = {'user': self.a.pk, 'date': '2026-03-10', 'asset': self.objs['b']['asset'].pk, 'kind': 'buy'}
        data['amount'] = '10'
        self.assert_rejected('investmentmovement', data, 'asset', InvestmentMovement.objects)

    def test_valuation_with_other_users_asset(self):
        data = {'user': self.a.pk, 'date': '2026-03-10', 'asset': self.objs['b']['asset'].pk, 'value': '10'}
        self.assert_rejected('investmentvaluation', data, 'asset', InvestmentValuation.objects)

    def test_same_owner_add_works(self):
        data = {'user': self.b.pk, 'date': '2026-03-10', 'asset': self.objs['b']['asset'].pk, 'value': '10'}
        response = self.client.post(self.url('investmentvaluation', 'add'), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(InvestmentValuation.objects.filter(user=self.b, date=date(2026, 3, 10)).exists())

    def test_fuel_and_toll_added_for_b_get_bs_transaction_and_category(self):
        fuel = {'user': self.b.pk, 'date': '2026-03-10', 'amount': '40', 'price_per_liter': '2', 'station': 'X'}
        self.assertEqual(self.client.post(self.url('fuelentry', 'add'), fuel).status_code, 302)
        toll = {'user': self.b.pk, 'date': '2026-03-10', 'amount': '1.2', 'description': 'A1'}
        self.assertEqual(self.client.post(self.url('tollentry', 'add'), toll).status_code, 302)
        for entry in (FuelEntry.objects.get(date=date(2026, 3, 10)), TollEntry.objects.get(date=date(2026, 3, 10))):
            with self.subTest(entry=entry):
                self.assertEqual(entry.user, self.b)
                self.assertEqual(entry.transaction.user, self.b)
                self.assertEqual(entry.transaction.category.user, self.b)

    def test_add_without_owner_is_rejected_cleanly(self):
        data = {'date': '2026-03-10', 'amount': '5', 'category': self.objs['b']['category'].pk}
        self.assert_rejected('transaction', data, 'user', Transaction.objects)


class AdminEditTests(AdminTwoAccountsBase):
    def test_editing_to_other_users_fk_is_rejected_and_owner_kept(self):
        a, b = self.objs['a'], self.objs['b']
        cases = [
            (
                'transaction',
                a['transaction'],
                {'date': '2026-03-01', 'amount': '10', 'category': b['category'].pk},
                'category',
            ),
            (
                'recurringexpense',
                a['recurring'],
                {
                    'name': 'Renda a',
                    'amount': '500',
                    'category': b['category'].pk,
                    'frequency': 'monthly',
                    'day_of_month': '1',
                    'start_date': '2026-01-01',
                    'active': 'on',
                },
                'category',
            ),
            (
                'investmentmovement',
                a['movement'],
                {'date': '2026-03-01', 'asset': b['asset'].pk, 'kind': 'buy', 'amount': '100'},
                'asset',
            ),
            (
                'investmentvaluation',
                a['valuation'],
                {'date': '2026-03-02', 'asset': b['asset'].pk, 'value': '1'},
                'asset',
            ),
        ]
        for model, obj, data, field in cases:
            with self.subTest(model=model):
                data = {**data, 'user': self.b.pk}  # forged owner change is ignored too
                response = self.client.post(self.url(model, 'change', obj.pk), data)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['adminform'].form.errors)
                obj.refresh_from_db()
                self.assertEqual(obj.user, self.a)
                self.assertEqual(getattr(obj, f'{field}_id'), a[field].pk)

    def test_posted_user_is_ignored_on_valid_edit(self):
        a = self.objs['a']
        posts = {
            'fuelentry': (a['fuel'], {'date': '2026-03-03', 'amount': '70', 'price_per_liter': '2', 'full_tank': 'on'}),
            'tollentry': (a['toll'], {'date': '2026-03-04', 'amount': '3', 'description': 'A2'}),
            'commuteday': (a['commute'], {'date': '2026-03-06'}),
            'investmentasset': (a['asset'], {'name': 'ETF a2', 'kind': 'etf'}),
            'transaction': (a['transaction'], {'date': '2026-03-01', 'amount': '11', 'category': a['category'].pk}),
        }
        for model, (obj, data) in posts.items():
            with self.subTest(model=model):
                response = self.client.post(self.url(model, 'change', obj.pk), {**data, 'user': self.b.pk})
                self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context)
                obj.refresh_from_db()
                self.assertEqual(obj.user, self.a)
        fuel = FuelEntry.objects.get(pk=a['fuel'].pk)
        self.assertEqual(fuel.transaction.user, self.a)
        self.assertEqual(fuel.transaction.amount, D('70'))
        self.assertEqual(fuel.liters, D('35.00'))

    def test_renaming_asset_to_existing_name_of_same_owner_is_a_form_error(self):
        InvestmentAsset.objects.create(user=self.a, name='Outro')
        asset = self.objs['a']['asset']
        response = self.client.post(self.url('investmentasset', 'change', asset.pk), {'name': 'Outro', 'kind': 'etf'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('name', response.context['adminform'].form.errors)

    def test_moving_commute_day_onto_existing_day_is_a_form_error(self):
        CommuteDay.objects.create(user=self.a, date=date(2026, 3, 9))
        day = self.objs['a']['commute']
        response = self.client.post(self.url('commuteday', 'change', day.pk), {'date': '2026-03-09'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('date', response.context['adminform'].form.errors)

    def test_renaming_category_to_existing_name_of_same_owner_is_a_form_error(self):
        category = self.objs['a']['category']
        data = {'name': 'Lazer', 'kind': 'expense', 'color': '#123456', 'icon': ''}
        response = self.client.post(self.url('category', 'change', category.pk), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('name', response.context['adminform'].form.errors)

    def test_same_name_as_other_users_asset_is_allowed(self):
        asset = self.objs['a']['asset']
        response = self.client.post(self.url('investmentasset', 'change', asset.pk), {'name': 'ETF b', 'kind': 'etf'})
        self.assertEqual(response.status_code, 302)


class AdminDeleteTests(AdminTwoAccountsBase):
    def test_delete_user_with_data_through_admin(self):
        a = self.objs['a']
        # Generated recurring transaction and a second fuel entry so RESTRICT links are exercised.
        Transaction.objects.create(
            user=self.a,
            date=date(2026, 3, 1),
            amount=D('500'),
            category=a['category'],
            recurring=a['recurring'],
            recurring_period=date(2026, 3, 1),
        )
        FuelEntry.objects.create(user=self.a, date=date(2026, 3, 20), amount=D('30'))
        url = reverse('admin:auth_user_delete', args=[self.a.pk])
        confirm = self.client.get(url)
        self.assertEqual(confirm.status_code, 200)
        self.assertFalse(confirm.context['protected'])
        response = self.client.post(url, {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(username='a').exists())
        models = [
            Category,
            Transaction,
            RecurringExpense,
            InvestmentAsset,
            InvestmentMovement,
            InvestmentValuation,
            FuelEntry,
            TollEntry,
            CommuteDay,
        ]
        for model in models:
            with self.subTest(model=model.__name__):
                self.assertFalse(model.objects.filter(user_id=self.a.pk).exists())
                self.assertTrue(model.objects.filter(user=self.b).exists())
        # B's linked transactions are untouched.
        self.assertTrue(Transaction.objects.filter(pk=self.objs['b']['fuel'].transaction_id).exists())

    def test_bulk_delete_users_through_admin(self):
        response = self.client.post(
            reverse('admin:auth_user_changelist'),
            {'action': 'delete_selected', '_selected_action': [self.a.pk, self.b.pk], 'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(User.objects.values_list('username', flat=True)), ['root'])
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(FuelEntry.objects.exists())

    def test_bulk_delete_fuel_entries_removes_their_transactions(self):
        ids = list(FuelEntry.objects.values_list('pk', flat=True))
        tx_ids = list(FuelEntry.objects.values_list('transaction_id', flat=True))
        response = self.client.post(
            self.url('fuelentry', 'changelist'), {'action': 'delete_selected', '_selected_action': ids, 'post': 'yes'}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.filter(pk__in=tx_ids).exists())

    def test_bulk_delete_transactions_including_linked_is_refused(self):
        tx_ids = list(Transaction.objects.values_list('pk', flat=True))
        response = self.client.post(
            self.url('transaction', 'changelist'),
            {'action': 'delete_selected', '_selected_action': tx_ids, 'post': 'yes'},
        )
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(FuelEntry.objects.filter(transaction__isnull=False).count() == 2)
        for entry in FuelEntry.objects.all():
            self.assertTrue(Transaction.objects.filter(pk=entry.transaction_id).exists())

    def test_delete_category_in_use_shows_protected(self):
        response = self.client.get(self.url('category', 'delete', self.objs['a']['category'].pk))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['protected'])


class OwnerMigrationRoundTripTests(TransactionTestCase):
    """Reverse the owner migrations on the test database and migrate forward again."""

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)

    def leaf(self):
        return MigrationExecutor(connection).loader.graph.leaf_nodes()

    def tearDown(self):
        # Leave the schema at the latest migration for the following tests.
        self.migrate(self.leaf())

    def test_single_account_round_trip_keeps_data(self):
        user = User.objects.create_user('only', password='x')
        market = Category.objects.get(user=user, name='Supermercado')
        Transaction.objects.create(user=user, date=date(2026, 3, 1), amount=D('12'), category=market)
        FuelEntry.objects.create(user=user, date=date(2026, 3, 2), amount=D('50'), price_per_liter=D('2'))
        asset = InvestmentAsset.objects.create(user=user, name='ETF')
        InvestmentMovement.objects.create(user=user, date=date(2026, 3, 1), asset=asset, amount=D('100'))
        CommuteDay.objects.create(user=user, date=date(2026, 3, 3))
        counts = {m: m.objects.count() for m in (Category, Transaction, FuelEntry, InvestmentMovement, CommuteDay)}

        self.migrate([('finance', '0004_fuel_amount_paid')])
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM finance_transaction')
            self.assertEqual(cursor.fetchone()[0], counts[Transaction])
        self.migrate(self.leaf())

        for model, count in counts.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(model.objects.count(), count)
                self.assertFalse(model.objects.exclude(user=user).exists())
        fuel = FuelEntry.objects.get()
        self.assertEqual(fuel.transaction.user, user)
        self.assertEqual(fuel.liters, D('25.00'))

    def test_round_trip_never_moves_data_to_another_account(self):
        # The only owner of data is a normal user; the superuser has no categories. Going back and forth must not
        # hand that user's records to the superuser.
        owner = User.objects.create_user('owner', password='x')
        FuelEntry.objects.create(user=owner, date=date(2026, 3, 2), amount=D('50'))
        root = User.objects.create_superuser('root', password='x')
        Category.objects.filter(user=root).delete()
        try:
            self.migrate([('finance', '0004_fuel_amount_paid')])
        except IrreversibleError:
            return  # refusing is also acceptable
        self.migrate(self.leaf())
        self.assertEqual(FuelEntry.objects.get().user, owner)
        self.assertFalse(Transaction.objects.filter(user=root).exists())

    def test_two_accounts_reverse_refused_and_db_usable_after(self):
        a = User.objects.create_user('a', password='x')
        b = User.objects.create_user('b', password='x')
        FuelEntry.objects.create(user=a, date=date(2026, 3, 2), amount=D('50'))
        with self.assertRaises(IrreversibleError):
            self.migrate([('finance', '0004_fuel_amount_paid')])
        self.migrate(self.leaf())
        # Both accounts still have their data and new records can be written.
        self.assertEqual(Category.objects.filter(user=a).count(), Category.objects.filter(user=b).count())
        FuelEntry.objects.create(user=b, date=date(2026, 3, 3), amount=D('20'))
        self.assertEqual(Transaction.objects.filter(user=b).count(), 1)
        self.assertEqual(FuelEntry.objects.get(user=a).transaction.user, a)
