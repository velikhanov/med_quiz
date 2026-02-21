"""
URL configuration for med_quiz project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.views.generic import TemplateView
from django.views.generic.base import RedirectView

from apps.content.api import github_trigger_worker


urlpatterns = [
    path('admin/', admin.site.urls),
    path('bot/', include('apps.bot.urls')),
    path('api/trigger/', github_trigger_worker),
    path('', RedirectView.as_view(url='https://t.me/med_quiz_tr_bot', permanent=False)),

    path('favicon.ico', RedirectView.as_view(url='/static/favicon.ico', permanent=True)),
    path('apple-touch-icon.png', RedirectView.as_view(url='/static/favicon.ico', permanent=True)),
    path('apple-touch-icon-precomposed.png', RedirectView.as_view(url='/static/favicon.ico', permanent=True)),
    path('robots.txt', TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
