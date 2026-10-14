from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import UserCreationForm
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods


class LoginView(auth_views.LoginView):
    def get_context_data(self, **kwargs):
        # Read at request time so the setting (and test overrides) always apply.
        return {**super().get_context_data(**kwargs), 'signup_enabled': settings.SIGNUP_ENABLED}


class SignUpForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        fields = ['username']


@login_not_required
@require_http_methods(['GET', 'POST'])
def register(request):
    if not settings.SIGNUP_ENABLED:
        raise Http404
    if request.user.is_authenticated:
        return redirect('finance:dashboard')
    form = SignUpForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()  # the post_save signal gives the new account its default categories
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return redirect('finance:dashboard')
    return render(request, 'registration/register.html', {'form': form})
