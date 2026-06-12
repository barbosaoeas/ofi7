from .models import SystemSettings


def system_settings(request):
    try:
        settings_obj = SystemSettings.get_solo()
    except Exception:
        settings_obj = None
    return {'system_settings': settings_obj}

