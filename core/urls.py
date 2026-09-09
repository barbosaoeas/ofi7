from django.urls import path

from .views import (
    DashboardView,
    PublicIndexView,
    SystemSettingsView,
    TimeClockPontoView,
    TimeClockRegisterView,
    TimeClockReportView,
    TimeClockVisualTokenView,
    TimeClockVisualView,
)

app_name = 'core'

urlpatterns = [
    path('', PublicIndexView.as_view(), name='public_index'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('configuracao/', SystemSettingsView.as_view(), name='system_settings'),
    path('ponto/visual/', TimeClockVisualView.as_view(), name='timeclock_visual'),
    path('ponto/visual/token/', TimeClockVisualTokenView.as_view(), name='timeclock_visual_token'),
    path('ponto/', TimeClockPontoView.as_view(), name='timeclock_ponto'),
    path('ponto/registrar/', TimeClockRegisterView.as_view(), name='timeclock_register'),
    path('relatorios/ponto/', TimeClockReportView.as_view(), name='timeclock_report'),
]
