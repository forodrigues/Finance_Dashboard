from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from .forms import CategoryForm
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


class OwnerConstraintsForm(forms.ModelForm):
    """Checks per-user unique constraints even when `user` is read-only and therefore not a form field."""

    def _post_clean(self):
        super()._post_clean()
        if 'user' in self.fields:
            return  # adding: `user` is a form field, so Django already validated these constraints
        model = type(self.instance)
        for constraint in model._meta.constraints:
            fields = [name for name in getattr(constraint, 'fields', ()) if name in self.fields]
            if 'user' in getattr(constraint, 'fields', ()) and fields:
                try:
                    constraint.validate(model, self.instance)
                except ValidationError as error:
                    # Show it on the one editable field involved (e.g. the asset name), else at the top.
                    self.add_error(fields[0] if len(fields) == 1 else None, error.messages)


class OwnedAdmin(admin.ModelAdmin):
    """Shows each record's owner; the owner is fixed once saved, and related choices name their owner."""

    form = OwnerConstraintsForm
    list_filter = ['user']

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        return [*readonly, 'user'] if obj else readonly

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if field is not None and hasattr(db_field.related_model, 'user'):
            field.queryset = field.queryset.select_related('user')
            field.label_from_instance = lambda obj: f'{obj} ({obj.user})'
        return field


class CategoryAdminForm(CategoryForm):
    class Meta(CategoryForm.Meta):
        fields = ['user', *CategoryForm.Meta.fields]


@admin.register(Category)
class CategoryAdmin(OwnedAdmin):
    form = CategoryAdminForm
    list_display = ['name', 'kind', 'color', 'user']
    list_filter = ['user', 'kind']


@admin.register(Transaction)
class TransactionAdmin(OwnedAdmin):
    list_display = ['date', 'category', 'amount', 'description', 'user']
    list_filter = ['user', 'category__kind']
    date_hierarchy = 'date'

    @staticmethod
    def _is_linked(obj):
        # Fuel and toll transactions are managed from their entries.
        return obj is not None and (hasattr(obj, 'fuelentry') or hasattr(obj, 'tollentry'))

    def has_change_permission(self, request, obj=None):
        return not self._is_linked(obj) and super().has_change_permission(request, obj)

    # No delete override: RESTRICT already blocks deleting a linked transaction on its own (the admin lists it as
    # protected), while still letting it go when its fuel/toll entry or its owner is deleted.


@admin.register(RecurringExpense)
class RecurringExpenseAdmin(OwnedAdmin):
    list_display = ['name', 'amount', 'frequency', 'day_of_month', 'active', 'user']
    list_filter = ['user']


@admin.register(InvestmentMovement)
class InvestmentMovementAdmin(OwnedAdmin):
    list_display = ['date', 'asset', 'kind', 'amount', 'user']
    list_filter = ['user', 'kind']


@admin.register(FuelEntry)
class FuelEntryAdmin(OwnedAdmin):
    list_display = ['date', 'amount', 'price_per_liter', 'liters', 'odometer_km', 'full_tank', 'user']
    list_filter = ['user']


@admin.register(TollEntry)
class TollEntryAdmin(OwnedAdmin):
    list_display = ['date', 'amount', 'description', 'user']
    list_filter = ['user']


@admin.register(InvestmentAsset)
class InvestmentAssetAdmin(OwnedAdmin):
    list_display = ['name', 'kind', 'user']
    list_filter = ['user', 'kind']


@admin.register(InvestmentValuation)
class InvestmentValuationAdmin(OwnedAdmin):
    list_display = ['date', 'asset', 'value', 'user']


@admin.register(CommuteDay)
class CommuteDayAdmin(OwnedAdmin):
    list_display = ['date', 'user']
