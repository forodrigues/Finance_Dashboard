from django.urls import path

from . import views

app_name = 'finance'


def crud_paths(prefix, create, update, delete):
    return [
        path(f'{prefix}/new/', create, name=f'{prefix}_create'),
        path(f'{prefix}/<int:pk>/edit/', update, name=f'{prefix}_update'),
        path(f'{prefix}/<int:pk>/delete/', delete, name=f'{prefix}_delete'),
    ]


urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('recurring/generate/', views.generate_recurring, name='generate_recurring'),
    path('transactions/', views.transactions, name='transactions'),
    path('transactions/new/', views.TransactionCreate.as_view(), name='transaction_create'),
    path('transactions/<int:pk>/edit/', views.TransactionUpdate.as_view(), name='transaction_update'),
    path('transactions/<int:pk>/delete/', views.TransactionDelete.as_view(), name='transaction_delete'),
    path('categories/', views.categories, name='categories'),
    path('recurring/', views.recurring_list, name='recurring'),
    path('investments/', views.investments, name='investments'),
    path('car/', views.car, name='car'),
    path('car/commute/toggle/', views.toggle_commute, name='toggle_commute'),
    *crud_paths('category', views.category_create, views.category_update, views.category_delete),
    *crud_paths('recurring', views.recurring_create, views.recurring_update, views.recurring_delete),
    *crud_paths('asset', views.asset_create, views.asset_update, views.asset_delete),
    *crud_paths('movement', views.movement_create, views.movement_update, views.movement_delete),
    *crud_paths('valuation', views.valuation_create, views.valuation_update, views.valuation_delete),
    *crud_paths('fuel', views.fuel_create, views.fuel_update, views.fuel_delete),
    *crud_paths('toll', views.toll_create, views.toll_update, views.toll_delete),
]
