"""Adversarial multi-account tests: B must never see, reference or alter A's data."""

import json
import re
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.db.models import RestrictedError
from django.test import Client, TestCase
from django.urls import URLPattern, reverse

from finance import urls as finance_urls
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

MONTH = date(2026, 3, 1)
SECRETS = (
    'Categoria-A',
    '#abcdef',
    'Segredo-A',
    'Recorrente-A',
    'Ativo-A',
    'Posto-A',
    'Portagem-A',
    'Nota-A',
    '777,77',
    '777.77',
    '543,21',
    '543.21',
    '612,34',
    '612.34',
    '888,88',
    '888.88',
    '61,23',
    '61.23',
    '33,33',
    '33.33',
)


def chart_data(response):
    match = re.search(
        r'<script id="chart-data" type="application/json">(.*?)</script>', response.content.decode(), re.S
    )
    assert match, 'chart-data script not found'
    return json.loads(match.group(1))


def flatten_numbers(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from flatten_numbers(v)
    elif isinstance(value, list):
        for v in value:
            yield from flatten_numbers(v)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield value


class TwoAccountsBase(TestCase):
    def setUp(self):
        self.a = User.objects.create_user('alice', password='pw-alice-123')
        self.b = User.objects.create_user('bruno', password='pw-bruno-123')
        self.a_cat = Category.objects.create(user=self.a, name='Categoria-A', kind='expense', color='#abcdef')
        self.a_income = Category.objects.get(user=self.a, name='Salário')
        self.a_assets = []
        for day in (date(2026, 3, 5), date.today()):
            Transaction.objects.create(
                user=self.a, date=day, amount=D('777.77'), category=self.a_cat, description='Segredo-A'
            )
            Transaction.objects.create(user=self.a, date=day, amount=D('543.21'), category=self.a_income)
        self.a_recurring = RecurringExpense.objects.create(
            user=self.a, name='Recorrente-A', amount=D('33.33'), category=self.a_cat, start_date=date(2025, 1, 1)
        )
        self.a_asset = InvestmentAsset.objects.create(user=self.a, name='Ativo-A', kind='crypto')
        for day in (date(2026, 3, 1), date.today()):
            InvestmentMovement.objects.create(
                user=self.a, date=day, asset=self.a_asset, amount=D('612.34'), note='Nota-A'
            )
        InvestmentValuation.objects.create(user=self.a, date=date(2026, 3, 1), asset=self.a_asset, value=D('888.88'))
        self.a_fuel = FuelEntry.objects.create(
            user=self.a, date=date(2026, 3, 2), amount=D('61.23'), price_per_liter=D('1.7'), station='Posto-A'
        )
        self.a_toll = TollEntry.objects.create(
            user=self.a, date=date(2026, 3, 3), amount=D('9.87'), description='Portagem-A'
        )
        CommuteDay.objects.create(user=self.a, date=date(2026, 3, 4))

        # B's own records, so every <pk> URL can be exercised with an id B is allowed to open.
        self.b_cat = Category.objects.get(user=self.b, name='Supermercado')
        self.b_tx = Transaction.objects.create(user=self.b, date=date(2026, 3, 6), amount=D('5'), category=self.b_cat)
        self.b_recurring = RecurringExpense.objects.create(
            user=self.b, name='Rec-B', amount=D('10'), category=self.b_cat, start_date=date(2026, 1, 1)
        )
        self.b_asset = InvestmentAsset.objects.create(user=self.b, name='Ativo-B')
        self.b_movement = InvestmentMovement.objects.create(
            user=self.b, date=date(2026, 3, 1), asset=self.b_asset, amount=D('1')
        )
        self.b_valuation = InvestmentValuation.objects.create(
            user=self.b, date=date(2026, 3, 1), asset=self.b_asset, value=D('2')
        )
        self.b_fuel = FuelEntry.objects.create(user=self.b, date=date(2026, 3, 2), amount=D('40'))
        self.b_toll = TollEntry.objects.create(user=self.b, date=date(2026, 3, 3), amount=D('1.1'))
        self.client.force_login(self.b)

    def assert_no_secrets(self, response, where):
        html = response.content.decode()
        for secret in SECRETS:
            self.assertNotIn(secret, html, f'{where} leaks {secret!r}')


class AllUrlsSweepTests(TwoAccountsBase):
    def own_pk(self, name):
        prefix = name.rsplit('_', 1)[0]
        return {
            'transaction': self.b_tx,
            'category': self.b_cat,
            'recurring': self.b_recurring,
            'asset': self.b_asset,
            'movement': self.b_movement,
            'valuation': self.b_valuation,
            'fuel': self.b_fuel,
            'toll': self.b_toll,
        }[prefix].pk

    def test_every_finance_url_as_b_shows_nothing_of_a(self):
        patterns = [p for p in finance_urls.urlpatterns if isinstance(p, URLPattern)]
        self.assertGreaterEqual(len(patterns), 30)
        # A's ids in the query string must not make anything of A appear either.
        query = {'m': '2026-03', 'category': self.a_cat.pk, 'asset': self.a_asset.pk}
        for pattern in patterns:
            name = f'finance:{pattern.name}'
            kwargs = {'pk': self.own_pk(pattern.name)} if 'pk' in pattern.pattern.converters else {}
            url = reverse(name, kwargs=kwargs)
            for month in ('2026-03', None):
                params = dict(query) if month else {k: v for k, v in query.items() if k != 'm'}
                with self.subTest(url=url, month=month):
                    response = self.client.get(url, params, follow=True)
                    self.assertIn(response.status_code, (200, 405), url)
                    self.assert_no_secrets(response, url)

    def test_post_only_views_as_b_show_nothing_of_a(self):
        for url, data in (
            (reverse('finance:generate_recurring') + '?m=2026-03', {}),
            (reverse('finance:toggle_commute'), {'date': '2026-03-04'}),
        ):
            with self.subTest(url=url):
                response = self.client.post(url, data, follow=True)
                self.assertEqual(response.status_code, 200)
                self.assert_no_secrets(response, url)
        self.assertFalse(Transaction.objects.filter(recurring=self.a_recurring).exists())
        self.assertEqual(CommuteDay.objects.filter(user=self.a).count(), 1)

    def test_sweep_detects_a_data_when_logged_in_as_a(self):
        # Control: the secrets really are rendered for their owner, so the sweep above is meaningful.
        self.client.force_login(self.a)
        html = self.client.get(reverse('finance:transactions'), {'m': '2026-03'}).content.decode()
        self.assertIn('Segredo-A', html)
        self.assertIn('777,77', html)

    def test_anonymous_gets_login_redirect_everywhere(self):
        anon = Client()
        for pattern in finance_urls.urlpatterns:
            kwargs = {'pk': self.a_tx_pk()} if 'pk' in pattern.pattern.converters else {}
            url = reverse(f'finance:{pattern.name}', kwargs=kwargs)
            with self.subTest(url=url):
                response = anon.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response['Location'].startswith(reverse('login')))

    def a_tx_pk(self):
        return Transaction.objects.filter(user=self.a).first().pk


class PrefillTests(TwoAccountsBase):
    def test_foreign_ids_in_create_query_are_not_prefilled(self):
        cases = [
            ('transaction_create', 'category', self.a_cat.pk),
            ('recurring_create', 'category', self.a_cat.pk),
            ('movement_create', 'asset', self.a_asset.pk),
            ('valuation_create', 'asset', self.a_asset.pk),
        ]
        for name, field, pk in cases:
            with self.subTest(view=name):
                response = self.client.get(reverse(f'finance:{name}'), {field: pk})
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertNotIn(f'value="{pk}" selected', html)
                self.assertNotIn(f'<option value="{pk}"', html)
                self.assert_no_secrets(response, name)
                choices = response.context['form'].fields[field].queryset
                self.assertFalse(choices.filter(pk=pk).exists())

    def test_own_ids_in_create_query_are_prefilled(self):
        response = self.client.get(reverse('finance:movement_create'), {'asset': self.b_asset.pk})
        self.assertContains(response, f'<option value="{self.b_asset.pk}" selected>')

    def test_garbage_ids_in_create_query_do_not_crash(self):
        for value in ('abc', '-1', '99999999999999999999999', '1 OR 1=1', ''):
            with self.subTest(value=value):
                response = self.client.get(reverse('finance:transaction_create'), {'category': value})
                self.assertEqual(response.status_code, 200)


class ForeignKeySwapOnEditTests(TwoAccountsBase):
    def test_editing_own_record_to_point_at_a_record_is_rejected(self):
        cases = [
            (
                'transaction_update',
                self.b_tx,
                {'date': '2026-03-06', 'amount': '5', 'category': self.a_cat.pk},
                'category',
            ),
            (
                'recurring_update',
                self.b_recurring,
                {
                    'name': 'Rec-B',
                    'amount': '10',
                    'category': self.a_cat.pk,
                    'frequency': 'monthly',
                    'day_of_month': 1,
                    'start_date': '2026-01-01',
                    'active': 'on',
                },
                'category',
            ),
            (
                'movement_update',
                self.b_movement,
                {'date': '2026-03-01', 'asset': self.a_asset.pk, 'kind': 'buy', 'amount': '1'},
                'asset',
            ),
            (
                'valuation_update',
                self.b_valuation,
                {'date': '2026-03-01', 'asset': self.a_asset.pk, 'value': '2'},
                'asset',
            ),
        ]
        for name, obj, data, field in cases:
            with self.subTest(view=name):
                before = getattr(obj, f'{field}_id')
                response = self.client.post(reverse(f'finance:{name}', args=[obj.pk]), data)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['form'].errors)
                obj.refresh_from_db()
                self.assertEqual(getattr(obj, f'{field}_id'), before)

    def test_posted_user_field_is_ignored(self):
        response = self.client.post(
            reverse('finance:transaction_create'),
            {'date': '2026-03-07', 'amount': '3', 'category': self.b_cat.pk, 'user': self.a.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Transaction.objects.get(amount=D('3')).user, self.b)
        response = self.client.post(
            reverse('finance:transaction_update', args=[self.b_tx.pk]),
            {'date': '2026-03-06', 'amount': '6', 'category': self.b_cat.pk, 'user': self.a.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.b_tx.refresh_from_db()
        self.assertEqual((self.b_tx.user, self.b_tx.amount), (self.b, D('6')))


class ChartDataTests(TwoAccountsBase):
    def test_dashboard_charts_have_no_a_values(self):
        # B's own March data: Supermercado 5, fuel 40, toll 1.1 and a 1 € investment; A has none of these values.
        march = chart_data(self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}))
        self.assertEqual(sorted(march['categories']['labels']), ['Combustível', 'Portagens', 'Supermercado'])
        self.assertEqual(sorted(march['categories']['values']), [1.1, 5.0, 40.0])
        self.assertEqual(march['expense'][-1], 46.1)
        self.assertEqual(march['invested'][-1], 1.0)
        self.assertEqual(march['cumulativeInvested'][-1], 1.0)
        for params in ({'m': '2026-03'}, {}):
            with self.subTest(params=params):
                data = chart_data(self.client.get(reverse('finance:dashboard'), params))
                self.assertNotIn('Categoria-A', data['categories']['labels'])
                self.assertTrue(all(v in (0, None) for v in data['income']), data['income'])
                self.assertTrue(all(v in (0, 1.0, None) for v in data['invested']), data['invested'])
                self.assertTrue(all(v in (0, 1.0, None) for v in data['cumulativeInvested']))
                for number in flatten_numbers(data):
                    self.assertNotIn(number, (777.77, 543.21, 612.34, 1224.68, 61.23, 9.87))

    def test_investment_charts_have_no_a_values(self):
        for params in ({'m': '2026-03'}, {}):
            with self.subTest(params=params):
                data = chart_data(self.client.get(reverse('finance:investments'), params))
                self.assertNotIn('Cripto', data['kinds']['labels'])
                for number in flatten_numbers(data):
                    self.assertNotIn(number, (612.34, 1224.68, 888.88))

    def test_control_a_sees_own_chart_values(self):
        self.client.force_login(self.a)
        data = chart_data(self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}))
        self.assertIn('Categoria-A', data['categories']['labels'])
        self.assertIn(612.34, data['invested'])


class CommuteSameDayTests(TwoAccountsBase):
    def test_both_accounts_mark_and_unmark_same_day_independently(self):
        day = '2026-03-04'  # A already drove that day
        url = reverse('finance:toggle_commute')
        self.client.post(url, {'date': day})
        self.assertEqual(CommuteDay.objects.filter(date=day).count(), 2)
        car = self.client.get(reverse('finance:car'), {'m': '2026-03'})
        self.assertEqual(car.context['stats']['trips'], 1)
        self.client.post(url, {'date': day})  # B unmarks
        self.assertFalse(CommuteDay.objects.filter(user=self.b, date=day).exists())
        self.assertTrue(CommuteDay.objects.filter(user=self.a, date=day).exists())
        a_client = Client()
        a_client.force_login(self.a)
        a_client.post(url, {'date': '2026-03-09'})
        self.client.post(url, {'date': '2026-03-09'})
        self.assertEqual(CommuteDay.objects.filter(date='2026-03-09').count(), 2)
        self.assertEqual(a_client.get(reverse('finance:car'), {'m': '2026-03'}).context['stats']['trips'], 2)


class RestrictTests(TwoAccountsBase):
    def test_deleting_category_in_use_shows_message_and_keeps_everything(self):
        rec_only = Category.objects.get(user=self.b, name='Ginásio')
        RecurringExpense.objects.create(
            user=self.b, name='Gym', amount=D('20'), category=rec_only, start_date=date(2026, 1, 1)
        )
        fuel_cat = self.b_fuel.transaction.category
        for cat in (self.b_cat, rec_only, fuel_cat):
            with self.subTest(category=cat.name):
                response = self.client.post(reverse('finance:category_delete', args=[cat.pk]), follow=True)
                self.assertRedirects(response, reverse('finance:categories'))
                self.assertContains(response, 'Não é possível apagar')
                self.assertTrue(Category.objects.filter(pk=cat.pk).exists())
        self.assertTrue(Transaction.objects.filter(pk=self.b_tx.pk).exists())
        self.assertTrue(FuelEntry.objects.filter(pk=self.b_fuel.pk).exists())

    def test_deleting_asset_with_movements_is_refused(self):
        response = self.client.post(reverse('finance:asset_delete', args=[self.b_asset.pk]), follow=True)
        self.assertContains(response, 'Não é possível apagar')
        self.assertTrue(InvestmentAsset.objects.filter(pk=self.b_asset.pk).exists())
        self.assertTrue(InvestmentValuation.objects.filter(pk=self.b_valuation.pk).exists())

    def test_deleting_unused_category_works(self):
        unused = Category.objects.get(user=self.b, name='Lazer')
        response = self.client.post(reverse('finance:category_delete', args=[unused.pk]), follow=True)
        self.assertContains(response, 'Apagado.')
        self.assertFalse(Category.objects.filter(pk=unused.pk).exists())

    def test_deleting_linked_transaction_directly_raises(self):
        for entry in (self.b_fuel, self.b_toll):
            with self.subTest(entry=type(entry).__name__):
                with self.assertRaises(RestrictedError):
                    entry.transaction.delete()
                self.assertTrue(Transaction.objects.filter(pk=entry.transaction_id).exists())

    def test_deleting_linked_transaction_via_view_goes_to_car_page(self):
        url = reverse('finance:transaction_delete', args=[self.b_fuel.transaction_id])
        response = self.client.post(url)
        self.assertRedirects(response, reverse('finance:car'))
        self.assertTrue(Transaction.objects.filter(pk=self.b_fuel.transaction_id).exists())

    def test_deleting_fuel_entry_removes_its_transaction(self):
        tx_id = self.b_fuel.transaction_id
        self.client.post(reverse('finance:fuel_delete', args=[self.b_fuel.pk]))
        self.assertFalse(Transaction.objects.filter(pk=tx_id).exists())


class RedirectSafetyTests(TestCase):
    EVIL = ('https://evil.com', '//evil.com', 'https:evil.com', '/\\evil.com', 'javascript:alert(1)', '\\\\evil.com')

    def test_register_ignores_malicious_next(self):
        for i, nxt in enumerate(self.EVIL):
            with self.subTest(next=nxt):
                client = Client()
                response = client.post(
                    reverse('register') + '?next=' + nxt,
                    {
                        'username': f'novo{i}',
                        'password1': 'Uma-Palavra-Longa-42',
                        'password2': 'Uma-Palavra-Longa-42',
                        'next': nxt,
                    },
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response['Location'], reverse('finance:dashboard'))

    def test_login_ignores_malicious_next(self):
        User.objects.create_user('alice', password='pw-alice-123')
        for nxt in self.EVIL:
            with self.subTest(next=nxt):
                client = Client()
                response = client.post(
                    reverse('login') + '?next=' + nxt,
                    {'username': 'alice', 'password': 'pw-alice-123', 'next': nxt},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response['Location'], reverse('finance:dashboard'))

    def test_login_keeps_safe_next(self):
        User.objects.create_user('alice', password='pw-alice-123')
        response = self.client.post(
            reverse('login'), {'username': 'alice', 'password': 'pw-alice-123', 'next': reverse('finance:car')}
        )
        self.assertEqual(response['Location'], reverse('finance:car'))

    def test_login_page_escapes_next(self):
        response = self.client.get(reverse('login'), {'next': '"><script>alert(1)</script>'})
        self.assertNotContains(response, '<script>alert(1)</script>')


class RegisterLifecycleTests(TestCase):
    PASSWORD = 'Uma-Palavra-Longa-42'

    def test_register_logout_login(self):
        response = self.client.post(
            reverse('register'), {'username': 'carla', 'password1': self.PASSWORD, 'password2': self.PASSWORD}
        )
        self.assertRedirects(response, reverse('finance:dashboard'))
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        response = self.client.get(reverse('finance:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('login')))
        bad = self.client.post(reverse('login'), {'username': 'carla', 'password': 'errada'})
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, 'Utilizador ou palavra-passe errados')
        response = self.client.post(reverse('login'), {'username': 'carla', 'password': self.PASSWORD})
        self.assertRedirects(response, reverse('finance:dashboard'))
        dashboard = self.client.get(reverse('finance:dashboard'))
        self.assertEqual(dashboard.context['user'].username, 'carla')
        self.assertEqual(dashboard.context['current']['expense'], 0)

    def test_register_with_real_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        data = {'username': 'dora', 'password1': self.PASSWORD, 'password2': self.PASSWORD}
        self.assertEqual(client.post(reverse('register'), data).status_code, 403)
        page = client.get(reverse('register'))
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        response = client.post(reverse('register'), {**data, 'csrfmiddlewaretoken': token})
        self.assertRedirects(response, reverse('finance:dashboard'))
        self.assertTrue(User.objects.filter(username='dora').exists())

    def test_username_differing_only_in_case_is_rejected(self):
        User.objects.create_user('Eva', password='x')
        response = self.client.post(
            reverse('register'), {'username': 'eva', 'password1': self.PASSWORD, 'password2': self.PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('username', response.context['form'].errors)

    def test_new_account_cannot_become_staff_via_post(self):
        self.client.post(
            reverse('register'),
            {
                'username': 'fred',
                'password1': self.PASSWORD,
                'password2': self.PASSWORD,
                'is_staff': 'on',
                'is_superuser': 'on',
            },
        )
        user = User.objects.get(username='fred')
        self.assertFalse(user.is_staff or user.is_superuser)
