import calendar
from datetime import date

from django.contrib import messages
from django.db import IntegrityError, OperationalError
from django.db.models import ProtectedError, RestrictedError, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, UpdateView

from . import forms
from .models import (
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
from .services import recurring, stats
from .services.dates import add_months, month_bounds

MONTH_ABBR = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
MONTH_NAMES = [
    'janeiro',
    'fevereiro',
    'março',
    'abril',
    'maio',
    'junho',
    'julho',
    'agosto',
    'setembro',
    'outubro',
    'novembro',
    'dezembro',
]


def selected_month(request):
    """Read ?m=YYYY-MM, falling back to the current month."""
    try:
        y, m = (int(part) for part in request.GET.get('m', '').split('-'))
        if 1 <= m <= 12 and 2000 <= y <= 2100:
            return y, m
    except ValueError:
        pass
    today = date.today()
    return today.year, today.month


def month_context(year, month):
    prev_y, prev_m = add_months(year, month, -1)
    next_y, next_m = add_months(year, month, 1)
    return {
        'year': year,
        'month': month,
        'month_param': f'{year}-{month:02d}',
        'month_label': f'{MONTH_NAMES[month - 1]} {year}',
        'prev_param': f'{prev_y}-{prev_m:02d}',
        'next_param': f'{next_y}-{next_m:02d}',
    }


def _label(y, m):
    return f'{MONTH_ABBR[m - 1]} {str(y)[2:]}'


def _float(value):
    return float(value) if value is not None else None


# --- Dashboard ---------------------------------------------------------------------------------------------


def dashboard(request):
    user = request.user
    year, month = selected_month(request)
    series = stats.monthly_series(user, year, month, 12)
    current, previous = series[-1], series[-2]
    by_category = stats.expenses_by_category(user, year, month)
    comparison = stats.category_comparison(user, year, month)
    next_y, next_m = add_months(year, month, 1)
    fixed_items, fixed_total = stats.fixed_expenses_for_month(user, next_y, next_m)
    income_items, income_total = stats.recurring_income_for_month(user, next_y, next_m)
    investments = stats.investments_summary(user)
    cumulative = stats.cumulative_invested(user, year, month, 12)

    chart_data = {
        'labels': [_label(r['year'], r['month']) for r in series],
        'income': [_float(r['income']) for r in series],
        'expense': [_float(r['expense']) for r in series],
        'invested': [_float(r['invested']) for r in series],
        'savingsRate': [_float(r['savings_rate']) for r in series],
        'cumulativeInvested': [_float(v) for v in cumulative],
        'categories': {
            'labels': [r['category__name'] for r in by_category],
            'values': [_float(r['total']) for r in by_category],
            'colors': [r['category__color'] for r in by_category],
        },
    }
    context = {
        **month_context(year, month),
        'current': current,
        'previous': previous,
        'top_categories': [r for r in comparison if r['current'] > 0][:5],
        'alerts': [r for r in comparison if r['alert']],
        'comparison': comparison,
        'pending_recurring': recurring.pending_for_month(user, year, month),
        'fixed_next_total': fixed_total,
        'fixed_next_count': len(fixed_items),
        'income_next_total': income_total,
        'income_next_count': len(income_items),
        'next_month_label': f'{MONTH_NAMES[next_m - 1]} {next_y}',
        'investments_total': sum((r['invested'] for r in investments), stats.ZERO),
        'car': stats.car_stats(user, year, month),
        'chart_data': chart_data,
    }
    return render(request, 'finance/dashboard.html', context)


@require_POST
def generate_recurring(request):
    year, month = selected_month(request)
    try:
        created = recurring.generate_for_month(request.user, year, month)
    except (IntegrityError, OperationalError):
        # A concurrent request (double tap, second tab) is generating the same month.
        messages.info(request, 'Os movimentos recorrentes deste mês já estavam a ser lançados.')
    else:
        messages.success(request, f'{len(created)} movimento(s) recorrente(s) lançado(s).')
    return redirect(f'{reverse("finance:dashboard")}?m={year}-{month:02d}')


# --- Generic CRUD ------------------------------------------------------------------------------------------


class OwnedMixin:
    """Limits object lookups to the logged-in user's records; anything else is a 404."""

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)


class FormPageMixin(OwnedMixin):
    template_name = 'finance/form.html'
    title = ''
    delete_url_name = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = self.title
        ctx['cancel_url'] = self.success_url
        if self.object and self.object.pk and self.delete_url_name:
            ctx['delete_url'] = reverse(self.delete_url_name, args=[self.object.pk])
        return ctx

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'user': self.request.user}

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault('date', date.today())
        for field in ('category', 'asset'):
            if field in self.request.GET:
                initial[field] = self.request.GET[field]
        return initial

    def form_valid(self, form):
        messages.success(self.request, 'Guardado.')
        return super().form_valid(form)


class ConfirmDeleteView(OwnedMixin, DeleteView):
    template_name = 'finance/confirm_delete.html'

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
        except (ProtectedError, RestrictedError):
            messages.error(self.request, 'Não é possível apagar: há registos que dependem deste.')
            return redirect(self.get_success_url())
        messages.success(self.request, 'Apagado.')
        return response


def crud(prefix, model, form_class, success_url, create_title, update_title):
    """Build create/update/delete views for a model; URL names are `finance:<prefix>_<action>`."""
    success = reverse_lazy(success_url)
    create = type(
        f'{model.__name__}Create',
        (FormPageMixin, CreateView),
        {'model': model, 'form_class': form_class, 'success_url': success, 'title': create_title},
    )
    update = type(
        f'{model.__name__}Update',
        (FormPageMixin, UpdateView),
        {
            'model': model,
            'form_class': form_class,
            'success_url': success,
            'title': update_title,
            'delete_url_name': f'finance:{prefix}_delete',
        },
    )
    delete = type(f'{model.__name__}Delete', (ConfirmDeleteView,), {'model': model, 'success_url': success})
    return create.as_view(), update.as_view(), delete.as_view()


category_create, category_update, category_delete = crud(
    'category', Category, forms.CategoryForm, 'finance:categories', 'Nova categoria', 'Editar categoria'
)
recurring_create, recurring_update, recurring_delete = crud(
    'recurring',
    RecurringExpense,
    forms.RecurringExpenseForm,
    'finance:recurring',
    'Novo movimento recorrente',
    'Editar movimento recorrente',
)
asset_create, asset_update, asset_delete = crud(
    'asset', InvestmentAsset, forms.InvestmentAssetForm, 'finance:investments', 'Novo ativo', 'Editar ativo'
)
movement_create, movement_update, movement_delete = crud(
    'movement',
    InvestmentMovement,
    forms.InvestmentMovementForm,
    'finance:investments',
    'Novo movimento',
    'Editar movimento',
)
valuation_create, valuation_update, valuation_delete = crud(
    'valuation',
    InvestmentValuation,
    forms.InvestmentValuationForm,
    'finance:investments',
    'Registar valor atual',
    'Editar valor atual',
)
fuel_create, fuel_update, fuel_delete = crud(
    'fuel', FuelEntry, forms.FuelEntryForm, 'finance:car', 'Novo abastecimento', 'Editar abastecimento'
)
toll_create, toll_update, toll_delete = crud(
    'toll', TollEntry, forms.TollEntryForm, 'finance:car', 'Nova portagem', 'Editar portagem'
)


class LinkedTransactionGuard:
    """Fuel and toll transactions are managed from the car page so they stay in sync."""

    def dispatch(self, request, *args, **kwargs):
        tx = get_object_or_404(Transaction, pk=kwargs['pk'], user=request.user)
        if hasattr(tx, 'fuelentry') or hasattr(tx, 'tollentry'):
            messages.info(request, 'Esta despesa vem da página Carro. Edita-a lá.')
            return redirect('finance:car')
        return super().dispatch(request, *args, **kwargs)


class TransactionCreate(FormPageMixin, CreateView):
    model = Transaction
    form_class = forms.TransactionForm
    title = 'Nova transação'
    success_url = reverse_lazy('finance:transactions')


class TransactionUpdate(LinkedTransactionGuard, FormPageMixin, UpdateView):
    model = Transaction
    form_class = forms.TransactionForm
    title = 'Editar transação'
    success_url = reverse_lazy('finance:transactions')
    delete_url_name = 'finance:transaction_delete'


class TransactionDelete(LinkedTransactionGuard, ConfirmDeleteView):
    model = Transaction
    success_url = reverse_lazy('finance:transactions')


# --- Lists -------------------------------------------------------------------------------------------------


def transactions(request):
    year, month = selected_month(request)
    first, last = month_bounds(year, month)
    qs = Transaction.objects.filter(user=request.user, date__range=(first, last)).select_related(
        'category', 'fuelentry', 'tollentry'
    )
    category_id = request.GET.get('category', '')
    # Bounded length keeps the id inside SQLite's 64-bit integer range.
    if category_id.isascii() and category_id.isdigit() and len(category_id) <= 18:
        qs = qs.filter(category_id=category_id)
    totals = {row['category__kind']: row['total'] for row in qs.values('category__kind').annotate(total=Sum('amount'))}
    return render(
        request,
        'finance/transactions.html',
        {
            **month_context(year, month),
            'transactions': qs,
            'categories': Category.objects.filter(user=request.user),
            'category_id': category_id,
            'income_total': totals.get(Category.INCOME, stats.ZERO),
            'expense_total': totals.get(Category.EXPENSE, stats.ZERO),
        },
    )


def categories(request):
    return render(request, 'finance/categories.html', {'categories': Category.objects.filter(user=request.user)})


def recurring_list(request):
    items = RecurringExpense.objects.filter(user=request.user).select_related('category')
    today = date.today()
    current = [r for r in items if r.active and not (r.end_date and r.end_date < today)]
    return render(
        request,
        'finance/recurring.html',
        {
            'items': items,
            'monthly_total': sum(
                (r.monthly_amount for r in current if r.category.kind == Category.EXPENSE), stats.ZERO
            ),
            'monthly_income': sum(
                (r.monthly_amount for r in current if r.category.kind == Category.INCOME), stats.ZERO
            ),
        },
    )


def investments(request):
    year, month = selected_month(request)
    summary = stats.investments_summary(request.user)
    cumulative = stats.cumulative_invested(request.user, year, month, 12)
    by_kind = stats.investments_by_kind(summary)
    labels = [_label(*add_months(year, month, -i)) for i in range(11, -1, -1)]
    total_invested = sum((r['invested'] for r in summary), stats.ZERO)
    valued = [r for r in summary if r['value'] is not None]
    return render(
        request,
        'finance/investments.html',
        {
            'summary': summary,
            'total_invested': total_invested,
            'total_value': sum((r['value'] for r in valued), stats.ZERO) if valued else None,
            'total_gain': sum((r['gain'] for r in valued), stats.ZERO) if valued else None,
            'movements': InvestmentMovement.objects.filter(user=request.user).select_related('asset')[:50],
            'valuations': InvestmentValuation.objects.filter(user=request.user).select_related('asset')[:20],
            'chart_data': {
                'labels': labels,
                'cumulative': [_float(v) for v in cumulative],
                'kinds': {'labels': list(by_kind), 'values': [_float(v) for v in by_kind.values()]},
            },
        },
    )


# --- Car ---------------------------------------------------------------------------------------------------


def car(request):
    year, month = selected_month(request)
    first, last = month_bounds(year, month)
    user = request.user
    driven = set(CommuteDay.objects.filter(user=user, date__range=(first, last)).values_list('date', flat=True))
    weeks = [
        [{'date': d, 'in_month': d.month == month, 'driven': d in driven} for d in week]
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    ]
    car_stats = stats.car_stats(user, year, month)
    fuel = list(FuelEntry.objects.filter(user=user, date__range=(first, last)))
    for entry in fuel:
        entry.shown_liters, entry.liters_estimated = car_stats['liters_by_entry'].get(entry.id, (None, False))
        entry.km_since = car_stats['km_by_entry'].get(entry.id)
    return render(
        request,
        'finance/car.html',
        {
            **month_context(year, month),
            'weeks': weeks,
            'weekdays': ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'],
            'today': date.today(),
            'stats': car_stats,
            'fuel': fuel,
            'tolls': TollEntry.objects.filter(user=user, date__range=(first, last)),
        },
    )


@require_POST
def toggle_commute(request):
    try:
        day = date.fromisoformat(request.POST.get('date', ''))
    except ValueError:
        messages.error(request, 'Data inválida.')
        return redirect('finance:car')
    deleted, _ = CommuteDay.objects.filter(user=request.user, date=day).delete()
    if not deleted:
        try:
            CommuteDay.objects.create(user=request.user, date=day)
        except IntegrityError:
            pass  # a concurrent double tap already marked it
    return redirect(f'{reverse("finance:car")}?m={day.year}-{day.month:02d}')
