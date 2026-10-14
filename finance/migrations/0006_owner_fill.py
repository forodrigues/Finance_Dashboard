from django.conf import settings
from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError

OWNED = [
    'Category',
    'Transaction',
    'RecurringExpense',
    'InvestmentAsset',
    'InvestmentMovement',
    'InvestmentValuation',
    'FuelEntry',
    'TollEntry',
    'CommuteDay',
]

# Same list as 0002 and finance/defaults.py, frozen here so the migration never changes.
DEFAULTS = [
    ('Salário', 'income', '#198754', 'briefcase'),
    ('Outros rendimentos', 'income', '#20c997', 'plus-circle'),
    ('Casa / renda', 'expense', '#6f42c1', 'house'),
    ('Supermercado', 'expense', '#fd7e14', 'cart'),
    ('Telemóvel', 'expense', '#0dcaf0', 'phone'),
    ('Ginásio', 'expense', '#d63384', 'heart-pulse'),
    ('Combustível', 'expense', '#dc3545', 'fuel-pump'),
    ('Portagens', 'expense', '#ffc107', 'sign-turn-right'),
    ('Restaurantes', 'expense', '#0d6efd', 'cup-hot'),
    ('Lazer', 'expense', '#6610f2', 'controller'),
    ('Saúde', 'expense', '#8bc34a', 'capsule'),
    ('Subscrições', 'expense', '#adb5bd', 'play-btn'),
    ('Outros', 'expense', '#6c757d', 'three-dots'),
]


def default_owner(User):
    return User.objects.filter(is_superuser=True).order_by('pk').first() or User.objects.order_by('pk').first()


def assign_owner(apps, schema_editor):
    """Give existing records to the first superuser (or first user); other users get the default categories."""
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    Category = apps.get_model('finance', 'Category')
    owner = default_owner(User)

    if owner is None:
        # Fresh install: only the categories from 0002 exist. They are recreated per user on sign-up.
        others = [m for m in OWNED if m != 'Category' and apps.get_model('finance', m).objects.exists()]
        if others:
            raise RuntimeError('Existem dados sem utilizador: cria um utilizador (createsuperuser) e volta a migrar.')
        Category.objects.filter(user__isnull=True).delete()
        return

    for name in OWNED:
        apps.get_model('finance', name).objects.filter(user__isnull=True).update(user=owner)

    for user in User.objects.exclude(pk=owner.pk):
        for cat_name, kind, color, icon in DEFAULTS:
            Category.objects.get_or_create(
                user=user, name=cat_name, kind=kind, defaults={'color': color, 'icon': icon}
            )


def refuse_unless_single_default_owner(apps, schema_editor):
    """Reverting drops the owner; migrating forward again would hand everything to `default_owner`.

    So only allow it when every record already belongs to that user: otherwise data from several accounts would be
    merged, or one account's data would move to another.
    """
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    owner = default_owner(User)
    owners = set()
    for name in OWNED:
        owners |= set(apps.get_model('finance', name).objects.order_by().values_list('user_id', flat=True).distinct())
    owners.discard(None)
    if owners - ({owner.pk} if owner else set()):
        raise IrreversibleError(
            'Não é possível reverter: há dados de outras contas além da do primeiro administrador. '
            'Apaga essas contas (ou os dados delas) antes de reverter.'
        )


class Migration(migrations.Migration):
    dependencies = [
        ('finance', '0005_owner_add'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(assign_owner, refuse_unless_single_default_owner)]
