"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
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
from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView

from config.health import health_check, liveness

urlpatterns = [
    path('health/', health_check, name='health'),
    path('health/live/', liveness, name='liveness'),
    path('favicon.ico', RedirectView.as_view(url='/static/images/favicon.png', permanent=True)),
    path('', include('pages.urls')),
    path('admin/', admin.site.urls),
    path('chatbot/', include('chatbot.urls')),
    path('diagnosis/', include('diagnosis.urls')),
    path('question/', include('question.urls')),
    path('user/', include('user.urls')),
    path('analytics/', include('analytics.urls')),
]
