"""Recurring income (e.g. salary) alongside recurring expenses."""

from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.urls import reverse

from finance.models import Category, RecurringExpense, Transaction
from finance.services import recurring, stats
from finance.templatetags.finance_tags import eur
from finance.tests.helpers import OwnedTestCase


class RecurringIncomeBase(OwnedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.salary_cat = Category.objects.get(user=self.user, name='Salário')
        self.other_income_cat = Category.objects.get(user=self.user, name='Outros rendimentos')
        self.gym_cat = Category.objects.get(user=self.user, name='Ginásio')

    def make(self, name, amount, category, user=None, **kwargs):
        kwargs.setdefault('start_date', date(2026, 1, 1))
        return RecurringExpense.objects.create(
            user=user or self.user, name=name, amount=D(amount), category=category, **kwargs
        )

    def generate(self, month):
        return self.client.post(reverse('finance:generate_recurring') + f'?m={month}')

    def messages_of(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class YearlyIncomeTests(RecurringIncomeBase):
    def setUp(self):
        super().setUp()
        # Christmas bonus paid on 15 December each year.
        self.bonus = self.make(
            'Subsídio de Natal',
            '1200',
            self.other_income_cat,
            frequency=RecurringExpense.YEARLY,
            day_of_month=15,
            start_date=date(2025, 12, 1),
        )

    def test_due_only_in_december(self):
        for month in range(1, 12):
            with self.subTest(month=month):
                self.assertIsNone(recurring.due_date(self.bonus, 2026, month))
        self.assertEqual(recurring.due_date(self.bonus, 2026, 12), date(2026, 12, 15))
        self.assertEqual(recurring.due_date(self.bonus, 2025, 12), date(2025, 12, 15))

    def test_launch_in_november_creates_nothing(self):
        response = self.generate('2026-11')
        self.assertFalse(Transaction.objects.exists())
        self.assertIn('0 movimento(s) recorrente(s) lançado(s).', self.messages_of(response))

    def test_launch_in_december_creates_income(self):
        self.generate('2026-12')
        tx = Transaction.objects.get()
        self.assertEqual(
            (tx.date, tx.amount, tx.category, tx.recurring_period, tx.description),
            (date(2026, 12, 15), D('1200'), self.other_income_cat, date(2026, 12, 1), 'Subsídio de Natal'),
        )
        current = self.client.get(reverse('finance:dashboard'), {'m': '2026-12'}).context['current']
        self.assertEqual((current['income'], current['expense']), (D('1200'), D('0')))

    def test_forecast_in_november_shows_full_bonus_for_december(self):
        ctx = self.client.get(reverse('finance:dashboard'), {'m': '2026-11'}).context
        self.assertEqual((ctx['income_next_total'], ctx['income_next_count']), (D('1200'), 1))
        self.assertEqual((ctx['fixed_next_total'], ctx['fixed_next_count']), (D('0'), 0))
        self.assertEqual(ctx['next_month_label'], 'dezembro 2026')
        # And nothing forecast for January.
        ctx = self.client.get(reverse('finance:dashboard'), {'m': '2026-12'}).context
        self.assertEqual((ctx['income_next_total'], ctx['income_next_count']), (D('0'), 0))

    def test_recurring_page_counts_yearly_income_divided_by_12(self):
        ctx = self.client.get(reverse('finance:recurring')).context
        self.assertEqual(ctx['monthly_income'], D('100.00'))
        self.assertEqual(ctx['monthly_total'], D('0'))


class SalaryOnDay31Tests(RecurringIncomeBase):
    def setUp(self):
        super().setUp()
        self.salary = self.make('Salário', '1450', self.salary_cat, day_of_month=31)

    def test_due_dates_clamp_to_month_end(self):
        cases = [
            ((2026, 1), date(2026, 1, 31)),
            ((2026, 2), date(2026, 2, 28)),
            ((2028, 2), date(2028, 2, 29)),  # leap year
            ((2026, 4), date(2026, 4, 30)),
        ]
        for (y, m), expected in cases:
            with self.subTest(month=(y, m)):
                self.assertEqual(recurring.due_date(self.salary, y, m), expected)

    def test_launch_in_february(self):
        self.generate('2026-02')
        tx = Transaction.objects.get(recurring=self.salary)
        self.assertEqual((tx.date, tx.recurring_period, tx.amount), (date(2026, 2, 28), date(2026, 2, 1), D('1450')))

    def test_launch_in_leap_february(self):
        self.generate('2028-02')
        self.assertEqual(Transaction.objects.get(recurring=self.salary).date, date(2028, 2, 29))

    def test_starting_after_the_clamped_day_is_not_due(self):
        # Starts on 1 March: nothing in February, due on 31 March.
        late = self.make('Salário novo', '1500', self.salary_cat, day_of_month=31, start_date=date(2026, 3, 1))
        self.assertIsNone(recurring.due_date(late, 2026, 2))
        self.assertEqual(recurring.due_date(late, 2026, 3), date(2026, 3, 31))

    def test_forecast_for_february(self):
        ctx = self.client.get(reverse('finance:dashboard'), {'m': '2026-01'}).context
        self.assertEqual((ctx['income_next_total'], ctx['income_next_count']), (D('1450'), 1))


class LaunchTwiceTests(RecurringIncomeBase):
    def setUp(self):
        super().setUp()
        self.salary = self.make('Salário', '1450', self.salary_cat, day_of_month=25)
        self.gym = self.make('Ginásio', '35', self.gym_cat)

    def test_second_launch_creates_nothing(self):
        first = self.generate('2026-03')
        self.assertIn('2 movimento(s) recorrente(s) lançado(s).', self.messages_of(first))
        second = self.generate('2026-03')
        self.assertIn('0 movimento(s) recorrente(s) lançado(s).', self.messages_of(second))
        self.assertEqual(Transaction.objects.filter(recurring=self.salary).count(), 1)
        self.assertEqual(Transaction.objects.filter(recurring=self.gym).count(), 1)
        self.assertEqual(recurring.pending_for_month(self.user, 2026, 3), [])

    def test_service_is_idempotent(self):
        self.assertEqual(len(recurring.generate_for_month(self.user, 2026, 3)), 2)
        self.assertEqual(recurring.generate_for_month(self.user, 2026, 3), [])
        self.assertEqual(Transaction.objects.count(), 2)

    def test_moving_the_salary_date_does_not_duplicate(self):
        self.generate('2026-03')
        tx = Transaction.objects.get(recurring=self.salary)
        tx.date = date(2026, 4, 1)  # paid late, moved into April
        tx.save()
        self.generate('2026-03')
        self.assertEqual(Transaction.objects.filter(recurring=self.salary).count(), 1)
        # April still gets its own salary.
        self.generate('2026-04')
        self.assertEqual(Transaction.objects.filter(recurring=self.salary).count(), 2)

    def test_redirect_keeps_month(self):
        response = self.generate('2026-03')
        self.assertRedirects(response, reverse('finance:dashboard') + '?m=2026-03')

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse('finance:generate_recurring') + '?m=2026-03')
        self.assertEqual(response.status_code, 405)
        self.assertFalse(Transaction.objects.exists())

    def test_pending_alert_lists_salary_until_launched(self):
        html = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}).content.decode()
        self.assertIn('2 movimento(s) recorrente(s) por lançar', html)
        self.assertIn('Salário', html)
        self.generate('2026-03')
        ctx = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}).context
        self.assertEqual(ctx['pending_recurring'], [])


class SavingsAndComparisonTests(RecurringIncomeBase):
    def setUp(self):
        super().setUp()
        self.make('Salário', '1000', self.salary_cat, day_of_month=25)
        self.make('Ginásio', '50', self.gym_cat)
        self.market = Category.objects.get(user=self.user, name='Supermercado')
        Transaction.objects.create(user=self.user, date=date(2026, 3, 10), amount=D('150'), category=self.market)

    def test_savings_rate_uses_launched_salary(self):
        before = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}).context['current']
        self.assertIsNone(before['savings_rate'])  # no income yet
        self.generate('2026-03')
        current = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}).context['current']
        self.assertEqual((current['income'], current['expense']), (D('1000'), D('200')))
        self.assertEqual(current['saved'], D('800'))
        self.assertEqual(current['savings_rate'], D('80.0'))
        html = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}).content.decode()
        self.assertIn('poupança 80,0 %', html)

    def test_salary_is_not_an_expense_category(self):
        self.generate('2026-03')
        comparison = stats.category_comparison(self.user, 2026, 3)
        names = {r['name']: r['current'] for r in comparison}
        self.assertEqual(names, {'Supermercado': D('150'), 'Ginásio': D('50')})
        self.assertNotIn('?', names)
        by_category = stats.expenses_by_category(self.user, 2026, 3)
        self.assertNotIn('Salário', [r['category__name'] for r in by_category])

    def test_salary_history_does_not_raise_expense_alerts(self):
        for m in ('2026-01', '2026-02', '2026-03'):
            self.generate(m)
        comparison = stats.category_comparison(self.user, 2026, 3)
        gym = next(r for r in comparison if r['name'] == 'Ginásio')
        self.assertEqual((gym['current'], gym['previous'], gym['avg3']), (D('50'), D('50'), D('33.33')))
        self.assertNotIn('Salário', [r['name'] for r in comparison])
        ctx = self.client.get(reverse('finance:dashboard'), {'m': '2026-03'}).context
        self.assertNotIn('Salário', [r['name'] for r in ctx['comparison']])
        self.assertNotIn('Salário', ctx['chart_data']['categories']['labels'])
        self.assertEqual(ctx['chart_data']['income'][-3:], [1000.0, 1000.0, 1000.0])


class OtherUserTests(RecurringIncomeBase):
    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user('other', password='pw-for-tests')
        other_salary_cat = Category.objects.get(user=self.other, name='Salário')
        self.other_salary = self.make('Salário do outro', '9999', other_salary_cat, user=self.other, day_of_month=25)
        self.mine = self.make('Salário', '1450', self.salary_cat, day_of_month=25)

    def test_other_users_salary_is_not_pending_or_launched(self):
        self.assertEqual([e for e, _ in recurring.pending_for_month(self.user, 2026, 3)], [self.mine])
        self.generate('2026-03')
        self.assertFalse(Transaction.objects.filter(recurring=self.other_salary).exists())
        self.assertFalse(Transaction.objects.filter(user=self.other).exists())
        self.assertEqual(Transaction.objects.get().recurring, self.mine)

    def test_other_users_salary_is_not_in_forecast_or_totals(self):
        ctx = self.client.get(reverse('finance:dashboard'), {'m': '2026-02'}).context
        self.assertEqual((ctx['income_next_total'], ctx['income_next_count']), (D('1450'), 1))
        html = self.client.get(reverse('finance:dashboard'), {'m': '2026-02'}).content.decode()
        self.assertNotIn('Salário do outro', html)
        response = self.client.get(reverse('finance:recurring'))
        self.assertEqual(response.context['monthly_income'], D('1450'))
        self.assertEqual(list(response.context['items']), [self.mine])
        self.assertNotIn('Salário do outro', response.content.decode())

    def test_other_users_launch_does_not_touch_mine(self):
        self.client.force_login(self.other)
        self.generate('2026-03')
        self.assertEqual(Transaction.objects.get().recurring, self.other_salary)
        self.assertFalse(Transaction.objects.filter(recurring=self.mine).exists())

    def test_cannot_edit_or_see_other_users_salary(self):
        for name in ('recurring_update', 'recurring_delete'):
            with self.subTest(name=name):
                url = reverse(f'finance:{name}', args=[self.other_salary.pk])
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_form_rejects_other_users_income_category(self):
        other_cat = Category.objects.get(user=self.other, name='Salário')
        data = {
            'name': 'Forjado',
            'amount': '10',
            'category': other_cat.pk,
            'frequency': 'monthly',
            'day_of_month': 1,
            'start_date': '2026-01-01',
            'active': 'on',
        }
        response = self.client.post(reverse('finance:recurring_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('category', response.context['form'].errors)
        self.assertFalse(RecurringExpense.objects.filter(name='Forjado').exists())
        # The category list only offers my categories.
        form = self.client.get(reverse('finance:recurring_create')).context['form']
        self.assertEqual(set(form.fields['category'].queryset.values_list('user', flat=True)), {self.user.pk})


class CategoryKindChangeTests(RecurringIncomeBase):
    def setUp(self):
        super().setUp()
        self.salary = self.make('Salário', '1450', self.salary_cat, day_of_month=25)

    def post_kind(self, category, kind):
        data = {'name': category.name, 'kind': kind, 'color': category.color, 'icon': category.icon}
        return self.client.post(reverse('finance:category_update', args=[category.pk]), data)

    def test_kind_change_blocked_by_recurring_income_without_transactions(self):
        self.assertFalse(Transaction.objects.exists())
        response = self.post_kind(self.salary_cat, Category.EXPENSE)
        self.assertEqual(response.status_code, 200)
        self.assertIn('kind', response.context['form'].errors)
        self.salary_cat.refresh_from_db()
        self.assertEqual(self.salary_cat.kind, Category.INCOME)

    def test_kind_change_blocked_after_launch(self):
        self.generate('2026-03')
        response = self.post_kind(self.salary_cat, Category.EXPENSE)
        self.assertIn('kind', response.context['form'].errors)
        self.salary_cat.refresh_from_db()
        self.assertEqual(self.salary_cat.kind, Category.INCOME)

    def test_inactive_recurring_still_blocks(self):
        self.salary.active = False
        self.salary.save()
        response = self.post_kind(self.salary_cat, Category.EXPENSE)
        self.assertIn('kind', response.context['form'].errors)

    def test_same_kind_edit_still_allowed(self):
        data = {'name': 'Ordenado', 'kind': Category.INCOME, 'color': '#198754', 'icon': 'briefcase'}
        response = self.client.post(reverse('finance:category_update', args=[self.salary_cat.pk]), data)
        self.assertRedirects(response, reverse('finance:categories'))
        self.salary_cat.refresh_from_db()
        self.assertEqual(self.salary_cat.name, 'Ordenado')

    def test_unused_income_category_can_change_kind(self):
        response = self.post_kind(self.other_income_cat, Category.EXPENSE)
        self.assertRedirects(response, reverse('finance:categories'))
        self.other_income_cat.refresh_from_db()
        self.assertEqual(self.other_income_cat.kind, Category.EXPENSE)

    def test_category_used_by_recurring_income_cannot_be_deleted(self):
        self.client.post(reverse('finance:category_delete', args=[self.salary_cat.pk]))
        self.assertTrue(Category.objects.filter(pk=self.salary_cat.pk).exists())


class PagesShowIncomeTests(RecurringIncomeBase):
    def setUp(self):
        super().setUp()
        self.make('Salário', '1450', self.salary_cat, day_of_month=25)
        self.make(
            'Subsídio de férias',
            '1200',
            self.other_income_cat,
            frequency=RecurringExpense.YEARLY,
            day_of_month=15,
            start_date=date(2026, 6, 1),
        )
        self.make('Ginásio', '35', self.gym_cat)
        self.make('Seguro', '240', self.gym_cat, frequency=RecurringExpense.YEARLY, start_date=date(2026, 6, 1))
        # Not counted: inactive, and already ended.
        self.make('Salário antigo', '900', self.salary_cat, active=False)
        self.make('Biscate', '300', self.other_income_cat, end_date=date(2020, 1, 31), start_date=date(2020, 1, 1))

    def test_recurring_page_totals(self):
        ctx = self.client.get(reverse('finance:recurring')).context
        self.assertEqual(ctx['monthly_income'], D('1550.00'))  # 1450 + 1200/12
        self.assertEqual(ctx['monthly_total'], D('55.00'))  # 35 + 240/12

    def test_recurring_page_html(self):
        html = self.client.get(reverse('finance:recurring')).content.decode()
        self.assertIn('Rendimento fixo mensal', html)
        self.assertIn('Custo fixo mensal', html)
        self.assertIn(eur(D('1550')), html)
        self.assertIn(eur(D('55')), html)
        self.assertIn('+' + eur(D('1450')), html)
        self.assertIn('+' + eur(D('1200')), html)
        self.assertIn('−' + eur(D('35')), html)
        self.assertNotIn('+' + eur(D('35')), html)
        self.assertNotIn('−' + eur(D('1450')), html)

    def test_dashboard_forecast_for_june(self):
        response = self.client.get(reverse('finance:dashboard'), {'m': '2026-05'})
        ctx = response.context
        self.assertEqual((ctx['income_next_total'], ctx['income_next_count']), (D('2650'), 2))
        self.assertEqual((ctx['fixed_next_total'], ctx['fixed_next_count']), (D('275'), 2))
        html = response.content.decode()
        self.assertIn('Previsto para junho 2026', html)
        self.assertIn('Rendimento fixo', html)
        self.assertIn('Despesas fixas', html)
        self.assertIn(eur(D('2650')), html)
        self.assertIn(eur(D('275')), html)

    def test_dashboard_forecast_for_july(self):
        ctx = self.client.get(reverse('finance:dashboard'), {'m': '2026-06'}).context
        self.assertEqual((ctx['income_next_total'], ctx['income_next_count']), (D('1450'), 1))
        self.assertEqual((ctx['fixed_next_total'], ctx['fixed_next_count']), (D('35'), 1))

    def test_dashboard_forecast_year_rollover(self):
        ctx = self.client.get(reverse('finance:dashboard'), {'m': '2026-12'}).context
        self.assertEqual(ctx['next_month_label'], 'janeiro 2027')
        self.assertEqual(ctx['income_next_total'], D('1450'))

    def test_launch_june_creates_income_and_expenses(self):
        self.generate('2026-06')
        txs = Transaction.objects.filter(date__month=6).select_related('category')
        got = sorted((t.description, t.amount, t.category.kind) for t in txs)
        self.assertEqual(
            got,
            [
                ('Ginásio', D('35'), Category.EXPENSE),
                ('Salário', D('1450'), Category.INCOME),
                ('Seguro', D('240'), Category.EXPENSE),
                ('Subsídio de férias', D('1200'), Category.INCOME),
            ],
        )

    def test_transactions_page_totals_after_launch(self):
        self.generate('2026-06')
        ctx = self.client.get(reverse('finance:transactions'), {'m': '2026-06'}).context
        self.assertEqual((ctx['income_total'], ctx['expense_total']), (D('2650'), D('275')))

    def test_form_labels_and_invalid_input(self):
        html = self.client.get(reverse('finance:recurring_create')).content.decode()
        self.assertIn('Salário (rendimento)', html)
        self.assertIn('Outros rendimentos (rendimento)', html)
        self.assertIn('Ginásio (despesa)', html)
        base = {
            'name': 'Salário 2',
            'amount': '1450',
            'category': self.salary_cat.pk,
            'frequency': 'monthly',
            'day_of_month': 31,
            'start_date': '2026-01-01',
            'active': 'on',
        }
        bad = [
            ({'amount': '0'}, 'amount'),
            ({'amount': '-5'}, 'amount'),
            ({'day_of_month': 32}, 'day_of_month'),
            ({'day_of_month': 0}, 'day_of_month'),
            ({'end_date': '2025-12-31'}, 'end_date'),
            ({'category': 999999}, 'category'),
        ]
        for override, field in bad:
            with self.subTest(override=override):
                response = self.client.post(reverse('finance:recurring_create'), {**base, **override})
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['form'].errors)
        self.assertFalse(RecurringExpense.objects.filter(name='Salário 2').exists())
        response = self.client.post(reverse('finance:recurring_create'), {**base, 'amount': '1450,50'})
        self.assertRedirects(response, reverse('finance:recurring'))
        self.assertEqual(RecurringExpense.objects.get(name='Salário 2').amount, D('1450.50'))
