from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.generic import TemplateView

from finance.accounts import LoginView, register

service_worker = TemplateView.as_view(template_name='finance/sw.js', content_type='application/javascript')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('register/', register, name='register'),
    # Served from the root so the service worker controls the whole site.
    path('sw.js', login_not_required(service_worker), name='service_worker'),
    path('', include('finance.urls')),
]
