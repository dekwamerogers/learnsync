from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView


def health(request):
    """Minimal liveness probe for load balancers and container orchestration."""
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    path('health/', health),
    path('admin/', admin.site.urls),
    path('accounts/login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(next_page='/accounts/login/'), name='logout'),
    path('sp/', include('selfpaced.urls')),
    path('', RedirectView.as_view(url='/sp/', permanent=False)),
]
