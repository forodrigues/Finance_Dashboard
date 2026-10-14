from django.db import migrations

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


def create(apps, schema_editor):
    Category = apps.get_model('finance', 'Category')
    for name, kind, color, icon in DEFAULTS:
        Category.objects.get_or_create(name=name, kind=kind, defaults={'color': color, 'icon': icon})


class Migration(migrations.Migration):
    dependencies = [('finance', '0001_initial')]

    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
