from django.urls import path

from .views import DashboardView, PublicIndexView, SystemSettingsView

app_name = 'core'

urlpatterns = [
    path('', PublicIndexView.as_view(), name='public_index'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('configuracao/', SystemSettingsView.as_view(), name='system_settings'),
]
