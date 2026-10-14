from .models import Category

DEFAULT_CATEGORIES = [
    ('Salário', Category.INCOME, '#198754', 'briefcase'),
    ('Outros rendimentos', Category.INCOME, '#20c997', 'plus-circle'),
    ('Casa / renda', Category.EXPENSE, '#6f42c1', 'house'),
    ('Supermercado', Category.EXPENSE, '#fd7e14', 'cart'),
    ('Telemóvel', Category.EXPENSE, '#0dcaf0', 'phone'),
    ('Ginásio', Category.EXPENSE, '#d63384', 'heart-pulse'),
    ('Combustível', Category.EXPENSE, '#dc3545', 'fuel-pump'),
    ('Portagens', Category.EXPENSE, '#ffc107', 'sign-turn-right'),
    ('Restaurantes', Category.EXPENSE, '#0d6efd', 'cup-hot'),
    ('Lazer', Category.EXPENSE, '#6610f2', 'controller'),
    ('Saúde', Category.EXPENSE, '#8bc34a', 'capsule'),
    ('Subscrições', Category.EXPENSE, '#adb5bd', 'play-btn'),
    ('Outros', Category.EXPENSE, '#6c757d', 'three-dots'),
]


def create_default_categories(user):
    for name, kind, color, icon in DEFAULT_CATEGORIES:
        Category.objects.get_or_create(user=user, name=name, kind=kind, defaults={'color': color, 'icon': icon})
