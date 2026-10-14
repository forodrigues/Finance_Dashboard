from datetime import date

from django.db import transaction as db_transaction
from django.db.models import Q

from ..models import RecurringExpense, Transaction
from .dates import month_bounds


def due_date(expense, year, month):
    """Date the expense is due in the given month, or None if it is not due."""
    first, last = month_bounds(year, month)
    if expense.frequency == RecurringExpense.YEARLY and month != expense.start_date.month:
        return None
    due = date(year, month, min(expense.day_of_month, last.day))
    if due < expense.start_date or (expense.end_date and due > expense.end_date):
        return None
    return due


def pending_for_month(user, year, month):
    """The user's active recurring expenses due this month that have no transaction yet."""
    first, last = month_bounds(year, month)
    already = Transaction.objects.filter(user=user, recurring_period=first, recurring__isnull=False).values(
        'recurring_id'
    )
    candidates = (
        RecurringExpense.objects.filter(user=user, active=True, start_date__lte=last)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=first))
        .exclude(id__in=already)
        .select_related('category')
    )
    result = []
    for expense in candidates:
        due = due_date(expense, year, month)
        if due:
            result.append((expense, due))
    return result


@db_transaction.atomic
def generate_for_month(user, year, month):
    """Create the missing transactions for the month. Safe to call repeatedly."""
    created = []
    for expense, due in pending_for_month(user, year, month):
        created.append(
            Transaction.objects.create(
                user=user,
                date=due,
                amount=expense.amount,
                category=expense.category,
                description=expense.name,
                recurring=expense,
                recurring_period=due.replace(day=1),
            )
        )
    return created
