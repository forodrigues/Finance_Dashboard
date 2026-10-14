from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from finance.defaults import DEFAULT_CATEGORIES
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


class RegisterTests(TestCase):
    url = reverse('register')

    def post(self, username='nova', p1='Uma-Palavra-Longa-42', p2=None):
        return self.client.post(self.url, {'username': username, 'password1': p1, 'password2': p2 or p1})

    def test_page_renders_and_login_links_to_it(self):
        self.assertContains(self.client.get(self.url), 'Criar conta')
        self.assertContains(self.client.get(reverse('login')), self.url)

    def test_register_creates_user_with_default_categories_and_logs_in(self):
        response = self.post()
        self.assertRedirects(response, reverse('finance:dashboard'))
        user = User.objects.get(username='nova')
        self.assertEqual(Category.objects.filter(user=user).count(), len(DEFAULT_CATEGORIES))
        self.assertEqual(self.client.get(reverse('finance:dashboard')).status_code, 200)

    def test_invalid_registrations_are_rejected(self):
        User.objects.create_user('existe', password='x')
        cases = [
            ({'username': 'existe'}, 'username'),
            ({'p1': '12345678'}, 'password2'),  # numeric / common
            ({'p1': 'Uma-Palavra-Longa-42', 'p2': 'Outra-Palavra-99'}, 'password2'),
            ({'p1': 'curta'}, 'password2'),
        ]
        for kwargs, field in cases:
            with self.subTest(kwargs=kwargs):
                response = self.post(**kwargs)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['form'].errors)
        self.assertEqual(User.objects.count(), 1)

    def test_logged_in_user_is_sent_to_dashboard(self):
        self.client.force_login(User.objects.create_user('me', password='x'))
        self.assertRedirects(self.client.get(self.url), reverse('finance:dashboard'))

    @override_settings(SIGNUP_ENABLED=False)
    def test_disabled_signup_is_404(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.post().status_code, 404)
        self.assertFalse(User.objects.exists())


class IsolationTests(TestCase):
    """User B must never see, change or reference user A's records."""

    def setUp(self):
        self.a = User.objects.create_user('a', password='x')
        self.b = User.objects.create_user('b', password='x')
        self.a_market = Category.objects.get(user=self.a, name='Supermercado')
        self.tx = Transaction.objects.create(
            user=self.a, date=date(2026, 3, 5), amount=D('77.77'), category=self.a_market, description='Segredo A'
        )
        self.recurring = RecurringExpense.objects.create(
            user=self.a, name='Ginásio A', amount=D('35'), category=self.a_market, start_date=date(2026, 1, 1)
        )
        self.asset = InvestmentAsset.objects.create(user=self.a, name='ETF de A')
        self.movement = InvestmentMovement.objects.create(
            user=self.a, date=date(2026, 3, 1), asset=self.asset, amount=D('500')
        )
        self.valuation = InvestmentValuation.objects.create(
            user=self.a, date=date(2026, 3, 1), asset=self.asset, value=D('600')
        )
        self.fuel = FuelEntry.objects.create(user=self.a, date=date(2026, 3, 2), amount=D('60'), station='Posto A')
        self.toll = TollEntry.objects.create(user=self.a, date=date(2026, 3, 3), amount=D('2.35'), description='A28 A')
        CommuteDay.objects.create(user=self.a, date=date(2026, 3, 2))
        self.client.force_login(self.b)

    def test_pages_show_nothing_of_a(self):
        for name in ('dashboard', 'transactions', 'recurring', 'investments', 'car', 'categories'):
            with self.subTest(page=name):
                response = self.client.get(reverse(f'finance:{name}'), {'m': '2026-03'})
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                for secret in ('Segredo A', 'Ginásio A', 'ETF de A', 'Posto A', 'A28 A', '77,77'):
                    self.assertNotIn(secret, html)
        dashboard = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}).context
        self.assertEqual(dashboard['current']['expense'], 0)
        self.assertEqual(dashboard['investments_total'], 0)
        self.assertEqual(dashboard['car']['trips'], 0)
        self.assertEqual(dashboard['pending_recurring'], [])

    def test_category_filter_with_a_category_id_shows_nothing(self):
        response = self.client.get(reverse('finance:transactions'), {'m': '2026-03', 'category': self.a_market.pk})
        self.assertEqual(list(response.context['transactions']), [])

    def test_edit_and_delete_of_a_records_are_404(self):
        objects = {
            'transaction': self.tx,
            'category': self.a_market,
            'recurring': self.recurring,
            'asset': self.asset,
            'movement': self.movement,
            'valuation': self.valuation,
            'fuel': self.fuel,
            'toll': self.toll,
        }
        for prefix, obj in objects.items():
            for action in ('update', 'delete'):
                with self.subTest(prefix=prefix, action=action):
                    url = reverse(f'finance:{prefix}_{action}', args=[obj.pk])
                    self.assertEqual(self.client.get(url).status_code, 404)
                    self.assertEqual(self.client.post(url, {}).status_code, 404)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.amount, D('77.77'))
        self.assertTrue(FuelEntry.objects.filter(pk=self.fuel.pk).exists())

    def test_forged_posts_cannot_use_a_records(self):
        cases = [
            ('transaction_create', {'date': '2026-03-01', 'amount': '1', 'category': self.a_market.pk}, 'category'),
            (
                'recurring_create',
                {
                    'name': 'X',
                    'amount': '1',
                    'category': self.a_market.pk,
                    'frequency': 'monthly',
                    'day_of_month': 1,
                    'start_date': '2026-01-01',
                },
                'category',
            ),
            ('movement_create', {'date': '2026-03-01', 'asset': self.asset.pk, 'kind': 'buy', 'amount': '1'}, 'asset'),
            ('valuation_create', {'date': '2026-03-01', 'asset': self.asset.pk, 'value': '1'}, 'asset'),
        ]
        for name, data, field in cases:
            with self.subTest(view=name):
                response = self.client.post(reverse(f'finance:{name}'), data)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['form'].errors)
        self.assertFalse(Transaction.objects.filter(user=self.b).exists())

    def test_choices_only_list_own_records(self):
        form = self.client.get(reverse('finance:transaction_create')).context['form']
        self.assertTrue(all(c.user_id == self.b.pk for c in form.fields['category'].queryset))
        form = self.client.get(reverse('finance:movement_create')).context['form']
        self.assertFalse(form.fields['asset'].queryset.exists())

    def test_b_records_are_owned_by_b(self):
        b_market = Category.objects.get(user=self.b, name='Supermercado')
        self.client.post(
            reverse('finance:transaction_create'), {'date': '2026-03-01', 'amount': '5', 'category': b_market.pk}
        )
        self.client.post(reverse('finance:fuel_create'), {'date': '2026-03-01', 'amount': '40', 'full_tank': 'on'})
        self.client.post(reverse('finance:toggle_commute'), {'date': '2026-03-02'})
        self.assertEqual(Transaction.objects.filter(user=self.b).count(), 2)  # manual + fuel
        fuel_tx = FuelEntry.objects.get(user=self.b).transaction
        self.assertEqual((fuel_tx.user, fuel_tx.category.user), (self.b, self.b))
        # Same day as A's commute: both exist, one per user.
        self.assertEqual(CommuteDay.objects.filter(date=date(2026, 3, 2)).count(), 2)

    def test_generate_recurring_only_for_self(self):
        self.client.post(reverse('finance:generate_recurring') + '?m=2026-03')
        self.assertFalse(Transaction.objects.filter(recurring=self.recurring).exists())

    def test_same_names_allowed_across_users_but_not_within(self):
        response = self.client.post(reverse('finance:asset_create'), {'name': 'ETF de A', 'kind': 'etf'})
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse('finance:asset_create'), {'name': 'ETF de A', 'kind': 'etf'})
        self.assertIn('name', response.context['form'].errors)
        response = self.client.post(
            reverse('finance:category_create'), {'name': 'Supermercado', 'kind': 'expense', 'color': '#123456'}
        )
        self.assertIn('name', response.context['form'].errors)

    def test_deleting_a_user_removes_their_data_only(self):
        self.a.delete()
        for model in (Transaction, Category, FuelEntry, TollEntry, InvestmentAsset, CommuteDay):
            self.assertFalse(model.objects.filter(user_id=self.a.pk).exists(), model.__name__)
        self.assertTrue(Category.objects.filter(user=self.b).exists())


class OwnerMigrationTests(TransactionTestCase):
    BEFORE = [('finance', '0005_owner_add')]
    AFTER = [('finance', '0006_owner_fill')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        with connection.cursor() as cursor:
            for table in ('finance_transaction', 'finance_category'):
                cursor.execute(f'DELETE FROM {table}')
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_existing_data_goes_to_first_superuser(self):
        old = self.migrate(self.BEFORE)
        OldUser = old.get_model('auth', 'User')
        plain = OldUser.objects.create(username='plain')
        owner = OldUser.objects.create(username='owner', is_superuser=True)
        later = OldUser.objects.create(username='later', is_superuser=True)
        Category = old.get_model('finance', 'Category')
        cat = Category.objects.create(name='X', kind='expense')
        old.get_model('finance', 'Transaction').objects.create(date=date(2026, 1, 1), amount=D('1'), category=cat)

        new = self.migrate(self.AFTER)
        NewCategory = new.get_model('finance', 'Category')
        NewTransaction = new.get_model('finance', 'Transaction')
        self.assertEqual(NewTransaction.objects.get().user_id, owner.pk)
        self.assertEqual(NewCategory.objects.get(name='X').user_id, owner.pk)
        for other in (plain, later):
            self.assertEqual(NewCategory.objects.filter(user_id=other.pk).count(), len(DEFAULT_CATEGORIES))

    def test_fresh_install_drops_ownerless_default_categories(self):
        old = self.migrate(self.BEFORE)
        old.get_model('finance', 'Category').objects.create(name='Salário', kind='income')
        new = self.migrate(self.AFTER)
        self.assertFalse(new.get_model('finance', 'Category').objects.exists())

    def test_data_without_any_user_stops_the_migration(self):
        old = self.migrate(self.BEFORE)
        cat = old.get_model('finance', 'Category').objects.create(name='X', kind='expense')
        old.get_model('finance', 'Transaction').objects.create(date=date(2026, 1, 1), amount=D('1'), category=cat)
        with self.assertRaisesMessage(RuntimeError, 'createsuperuser'):
            self.migrate(self.AFTER)
