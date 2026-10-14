from django import forms

from .models import (
    Category,
    FuelEntry,
    InvestmentAsset,
    InvestmentMovement,
    InvestmentValuation,
    RecurringExpense,
    TollEntry,
    Transaction,
)


class DateInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, **kwargs):
        super().__init__(format='%Y-%m-%d', **kwargs)


class StyledModelForm(forms.ModelForm):
    """Adds Bootstrap classes and mobile-friendly inputs to every field.

    With `user`, the new record is owned by that user and every related-record choice (category, asset) is
    limited to that user's records, so a forged POST cannot point at someone else's data.
    """

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None:
            if self.instance.user_id is None:
                self.instance.user = user
            for field in self.fields.values():
                if isinstance(field, forms.ModelChoiceField) and hasattr(field.queryset.model, 'user'):
                    field.queryset = field.queryset.filter(user=user)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'form-check-input')
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault('class', 'form-select')
            else:
                widget.attrs.setdefault('class', 'form-control')
            if isinstance(field, forms.DecimalField):
                # Text input accepts both "12,50" and "12.50" (number inputs reject the comma on some phones).
                field.localize = True
                field.widget = forms.TextInput(attrs={'class': 'form-control', 'inputmode': 'decimal'})
                field.widget.is_localized = True


class CategoryForm(StyledModelForm):
    class Meta:
        model = Category
        fields = ['name', 'kind', 'color', 'icon']
        widgets = {'color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'})}

    def clean(self):
        cleaned = super().clean()
        owner = self.user or cleaned.get('user') or getattr(self.instance, 'user', None)
        name, kind = cleaned.get('name'), cleaned.get('kind')
        if owner and name and kind:
            clash = Category.objects.filter(user=owner, name=name, kind=kind).exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error('name', 'Já tens uma categoria com este nome e tipo.')
        return cleaned

    def clean_kind(self):
        kind = self.cleaned_data['kind']
        if (
            self.instance.pk
            and kind != self.instance.kind
            and (self.instance.transactions.exists() or self.instance.recurringexpense_set.exists())
        ):
            raise forms.ValidationError(
                'Esta categoria já tem transações ou recorrentes; mudar o tipo alteraria o histórico.'
            )
        return kind


class TransactionForm(StyledModelForm):
    class Meta:
        model = Transaction
        fields = ['date', 'amount', 'category', 'description']
        widgets = {'date': DateInput()}


class RecurringExpenseForm(StyledModelForm):
    class Meta:
        model = RecurringExpense
        fields = ['name', 'amount', 'category', 'frequency', 'day_of_month', 'start_date', 'end_date', 'active']
        widgets = {'start_date': DateInput(), 'end_date': DateInput()}
        help_texts = {'start_date': 'Nos anuais, o mês de início é o mês em que o movimento é cobrado ou recebido.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Income and expense categories share the list, so say which is which (e.g. "Salário (rendimento)").
        self.fields['category'].label_from_instance = lambda c: f'{c.name} ({c.get_kind_display().lower()})'

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'A data de fim tem de ser depois da data de início.')
        return cleaned


class InvestmentAssetForm(StyledModelForm):
    class Meta:
        model = InvestmentAsset
        fields = ['name', 'kind']

    def clean_name(self):
        name = self.cleaned_data['name']
        owner = self.user or getattr(self.instance, 'user', None)
        if owner and InvestmentAsset.objects.filter(user=owner, name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Já tens um ativo com este nome.')
        return name


class InvestmentMovementForm(StyledModelForm):
    class Meta:
        model = InvestmentMovement
        fields = ['date', 'asset', 'kind', 'amount', 'note']
        widgets = {'date': DateInput()}


class InvestmentValuationForm(StyledModelForm):
    class Meta:
        model = InvestmentValuation
        fields = ['date', 'asset', 'value']
        widgets = {'date': DateInput()}


class FuelEntryForm(StyledModelForm):
    class Meta:
        model = FuelEntry
        fields = ['date', 'amount', 'price_per_liter', 'odometer_km', 'full_tank', 'station']
        widgets = {'date': DateInput()}
        help_texts = {
            'price_per_liter': 'Opcional. Com o preço, a app calcula os litros.',
            'odometer_km': 'Opcional. Com os km, o preço e o depósito atestado, a app calcula o consumo (L/100km).',
        }


class TollEntryForm(StyledModelForm):
    class Meta:
        model = TollEntry
        fields = ['date', 'amount', 'description']
        widgets = {'date': DateInput()}
