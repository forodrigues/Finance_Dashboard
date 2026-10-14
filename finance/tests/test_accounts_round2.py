from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from finance.models import Category, FuelEntry, InvestmentAsset, InvestmentMovement, Transaction


class OwnerConsistencyTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user('a', password='x')
        self.b = User.objects.create_user('b', password='x')
        self.b_market = Category.objects.get(user=self.b, name='Supermercado')

    def test_model_clean_rejects_other_users_category_and_asset(self):
        tx = Transaction(user=self.a, date=date(2026, 3, 1), amount=D('1'), category=self.b_market)
        with self.assertRaises(ValidationError) as ctx:
            tx.full_clean()
        self.assertIn('category', ctx.exception.message_dict)
        asset = InvestmentAsset.objects.create(user=self.b, name='ETF B')
        movement = InvestmentMovement(user=self.a, date=date(2026, 3, 1), asset=asset, amount=D('1'))
        with self.assertRaises(ValidationError) as ctx:
            movement.full_clean()
        self.assertIn('asset', ctx.exception.message_dict)

    def test_changing_fuel_owner_moves_its_transaction(self):
        entry = FuelEntry.objects.create(user=self.a, date=date(2026, 3, 1), amount=D('50'))
        entry.user = self.b
        entry.save()
        entry.transaction.refresh_from_db()
        self.assertEqual(entry.transaction.user, self.b)
        self.assertEqual(entry.transaction.category.user, self.b)


class AdminOwnerTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('root', password='x')
        self.a = User.objects.create_user('a', password='x')
        self.b = User.objects.create_user('b', password='x')
        self.client.force_login(self.admin)

    def test_admin_rejects_cross_owner_category(self):
        b_market = Category.objects.get(user=self.b, name='Supermercado')
        data = {'user': self.a.pk, 'date': '2026-03-01', 'amount': '5', 'category': b_market.pk, 'description': ''}
        response = self.client.post(reverse('admin:finance_transaction_add'), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('category', response.context['adminform'].form.errors)
        self.assertFalse(Transaction.objects.exists())

    def test_owner_is_read_only_when_editing(self):
        category = Category.objects.get(user=self.a, name='Lazer')
        url = reverse('admin:finance_category_change', args=[category.pk])
        data = {'user': self.b.pk, 'name': 'Lazer', 'kind': 'expense', 'color': '#123456', 'icon': '', '_save': 'Save'}
        self.client.post(url, data)
        category.refresh_from_db()
        self.assertEqual(category.user, self.a)

    def test_choices_name_their_owner(self):
        html = self.client.get(reverse('admin:finance_transaction_add')).content.decode()
        self.assertIn('Supermercado (a)', html)
        self.assertIn('Supermercado (b)', html)

    def test_new_admin_pages_render(self):
        for name in ('investmentasset', 'investmentvaluation', 'commuteday'):
            with self.subTest(model=name):
                response = self.client.get(reverse(f'admin:finance_{name}_changelist'))
                self.assertEqual(response.status_code, 200)


class LoginSignupLinkTests(TestCase):
    @override_settings(SIGNUP_ENABLED=False)
    def test_link_hidden_when_signup_closed(self):
        self.assertNotContains(self.client.get(reverse('login')), reverse('register'))

    def test_link_shown_when_signup_open(self):
        self.assertContains(self.client.get(reverse('login')), reverse('register'))


class OwnerMigrationReverseTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)

    def tearDown(self):
        with connection.cursor() as cursor:
            for table in ('finance_transaction', 'finance_category'):
                cursor.execute(f'DELETE FROM {table}')
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_reverse_refused_with_several_accounts(self):
        User.objects.create_user('a', password='x')
        User.objects.create_user('b', password='x')
        with self.assertRaises(IrreversibleError):
            self.migrate([('finance', '0005_owner_add')])

    def test_reverse_allowed_with_single_account(self):
        User.objects.create_user('only', password='x')
        self.migrate([('finance', '0004_fuel_amount_paid')])


class RecurringIncomeTests(TestCase):
    def setUp(self):
        from finance.models import RecurringExpense

        self.user = User.objects.create_user('me', password='x')
        self.client.force_login(self.user)
        self.salary = RecurringExpense.objects.create(
            user=self.user,
            name='Salário',
            amount=D('1450'),
            category=Category.objects.get(user=self.user, name='Salário'),
            day_of_month=25,
            start_date=date(2026, 1, 1),
        )
        RecurringExpense.objects.create(
            user=self.user,
            name='Ginásio',
            amount=D('35'),
            category=Category.objects.get(user=self.user, name='Ginásio'),
            start_date=date(2026, 1, 1),
        )

    def test_salary_can_be_created_as_recurring(self):
        data = {
            'name': 'Subsídio',
            'amount': '100',
            'category': Category.objects.get(user=self.user, name='Outros rendimentos').pk,
            'frequency': 'monthly',
            'day_of_month': 1,
            'start_date': '2026-01-01',
            'active': 'on',
        }
        self.assertEqual(self.client.post(reverse('finance:recurring_create'), data).status_code, 302)

    def test_launch_creates_income_and_counts_in_dashboard(self):
        self.client.post(reverse('finance:generate_recurring') + '?m=2026-03')
        tx = Transaction.objects.get(recurring=self.salary)
        self.assertEqual((tx.date, tx.amount, tx.category.kind), (date(2026, 3, 25), D('1450'), Category.INCOME))
        current = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}).context['current']
        self.assertEqual((current['income'], current['expense']), (D('1450'), D('35')))

    def test_forecast_and_recurring_page_split_income_and_expenses(self):
        ctx = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}).context
        self.assertEqual((ctx['income_next_total'], ctx['fixed_next_total']), (D('1450'), D('35')))
        ctx = self.client.get(reverse('finance:recurring')).context
        self.assertEqual((ctx['monthly_income'], ctx['monthly_total']), (D('1450'), D('35')))

    def test_form_labels_say_income_or_expense(self):
        html = self.client.get(reverse('finance:recurring_create')).content.decode()
        self.assertIn('Salário (rendimento)', html)
        self.assertIn('Ginásio (despesa)', html)
