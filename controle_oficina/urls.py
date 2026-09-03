'''
URL configuration for controle_oficina project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
'''

from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path

from budgets.views import UaizapiWebhookView, ZapWebhookView

urlpatterns = [
    path('admin/', admin.site.urls),

    re_path(r'^webhooks/uaizapi/?$', UaizapiWebhookView.as_view(), name='uaizapi_webhook'),
    re_path(r'^webhooks/zap/?$', ZapWebhookView.as_view(), name='zap_webhook'),

    path('', include('core.urls')),
    path('usuarios/', include('users.urls')),
    path('clientes/', include('customers.urls')),
    path('orcamentos/', include('budgets.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)