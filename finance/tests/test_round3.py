from datetime import date
from decimal import Decimal as D
from unittest import mock

from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import IntegrityError, OperationalError
from django.urls import reverse

from finance.admin import CategoryAdmin
from finance.forms import CategoryForm
from finance.models import Category, RecurringExpense, Transaction
from finance.tests.helpers import OwnedTestCase


class GenerateRecurringErrorTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse('finance:generate_recurring') + '?m=2026-03'

    def _post_with_error(self, exc):
        with mock.patch('finance.views.recurring.generate_for_month', side_effect=exc) as patched:
            response = self.client.post(self.url)
        patched.assert_called_once_with(self.user, 2026, 3)
        return response

    def test_integrity_error_redirects_with_message(self):
        response = self._post_with_error(IntegrityError('UNIQUE constraint failed'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('finance:dashboard') + '?m=2026-03')
        texts = [m.message for m in get_messages(response.wsgi_request)]
        self.assertEqual(texts, ['Os movimentos recorrentes deste mês já estavam a ser lançados.'])

    def test_operational_error_redirects_with_message(self):
        response = self._post_with_error(OperationalError('database is locked'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('finance:dashboard') + '?m=2026-03')
        texts = [m.message for m in get_messages(response.wsgi_request)]
        self.assertEqual(len(texts), 1)
        self.assertIn('já estavam a ser lançados', texts[0])

    def test_other_errors_are_not_swallowed(self):
        with mock.patch('finance.views.recurring.generate_for_month', side_effect=ValueError('boom')):
            with self.assertRaises(ValueError):
                self.client.post(self.url)

    def test_success_message_still_shown(self):
        with mock.patch('finance.views.recurring.generate_for_month', return_value=[object(), object()]):
            response = self.client.post(self.url)
        texts = [m.message for m in get_messages(response.wsgi_request)]
        self.assertEqual(texts, ['2 movimento(s) recorrente(s) lançado(s).'])


class CategoryKindWithRecurringTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.category = Category.objects.create(user=self.user, name='QA Ginásio', kind=Category.EXPENSE)
        RecurringExpense.objects.create(
            user=self.user, name='QA Ginásio', amount=D('30'), category=self.category, start_date=date(2026, 1, 1)
        )

    def _form(self, kind, name='QA Ginásio'):
        data = {'name': name, 'kind': kind, 'color': '#123456', 'icon': ''}
        return CategoryForm(data, instance=Category.objects.get(pk=self.category.pk))

    def test_kind_change_blocked_when_used_by_recurring_only(self):
        self.assertFalse(Transaction.objects.filter(category=self.category).exists())
        form = self._form(Category.INCOME)
        self.assertFalse(form.is_valid())
        self.assertIn('kind', form.errors)
        self.category.refresh_from_db()
        self.assertEqual(self.category.kind, Category.EXPENSE)

    def test_other_fields_can_change_when_used_by_recurring(self):
        form = self._form(Category.EXPENSE, name='QA Fitness')
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'QA Fitness')

    def test_kind_change_allowed_when_unused(self):
        unused = Category.objects.create(user=self.user, name='QA Livre', kind=Category.EXPENSE)
        data = {'name': 'QA Livre', 'kind': Category.INCOME, 'color': '#123456', 'icon': ''}
        form = CategoryForm(data, instance=unused)
        self.assertTrue(form.is_valid(), form.errors)

    def test_new_category_any_kind_valid(self):
        form = CategoryForm({'name': 'QA Nova', 'kind': Category.INCOME, 'color': '#123456', 'icon': ''})
        self.assertTrue(form.is_valid(), form.errors)


class CategoryAdminKindTests(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser('root', password='pw-for-tests'))
        self.used = Category.objects.create(user=self.user, name='QA Super', kind=Category.EXPENSE)
        Transaction.objects.create(user=self.user, date=date(2026, 3, 1), amount=D('10'), category=self.used)
        self.recurring_only = Category.objects.create(user=self.user, name='QA Seguro', kind=Category.EXPENSE)
        RecurringExpense.objects.create(
            user=self.user, name='QA Seguro', amount=D('20'), category=self.recurring_only, start_date=date(2026, 1, 1)
        )
        self.unused = Category.objects.create(user=self.user, name='QA Vazia', kind=Category.EXPENSE)

    def _post(self, category, kind):
        url = reverse('admin:finance_category_change', args=[category.pk])
        data = {
            'user': self.user.pk,
            'name': category.name,
            'kind': kind,
            'color': '#123456',
            'icon': '',
            '_save': 'Save',
        }
        return self.client.post(url, data)

    def test_admin_uses_category_form(self):
        self.assertIsInstance(site._registry[Category], CategoryAdmin)
        # The admin form is CategoryForm plus the owner field.
        self.assertTrue(issubclass(CategoryAdmin.form, CategoryForm))

    def test_admin_rejects_kind_change_with_transactions(self):
        response = self._post(self.used, Category.INCOME)
        self.assertEqual(response.status_code, 200)
        self.assertIn('kind', response.context['adminform'].form.errors)
        self.used.refresh_from_db()
        self.assertEqual(self.used.kind, Category.EXPENSE)

    def test_admin_rejects_kind_change_with_recurring(self):
        response = self._post(self.recurring_only, Category.INCOME)
        self.assertEqual(response.status_code, 200)
        self.assertIn('kind', response.context['adminform'].form.errors)
        self.recurring_only.refresh_from_db()
        self.assertEqual(self.recurring_only.kind, Category.EXPENSE)

    def test_admin_allows_kind_change_when_unused(self):
        response = self._post(self.unused, Category.INCOME)
        self.assertEqual(response.status_code, 302)
        self.unused.refresh_from_db()
        self.assertEqual(self.unused.kind, Category.INCOME)

    def test_admin_add_category(self):
        url = reverse('admin:finance_category_add')
        data = {
            'user': self.user.pk,
            'name': 'QA Salário',
            'kind': Category.INCOME,
            'color': '#123456',
            'icon': '',
            '_save': 'Save',
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertTrue(Category.objects.filter(name='QA Salário', kind=Category.INCOME).exists())
