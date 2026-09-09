from django.urls import reverse
from django.utils import timezone

from users.models import Collaborator, CustomUser, TimeClockDay

from .models import SystemSettings


def system_settings(request):
    try:
        settings_obj = SystemSettings.get_solo()
    except Exception:
        settings_obj = None
    return {'system_settings': settings_obj}


def timeclock_nav(request):
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False) or getattr(user, 'is_superuser', False):
        return {}
    if getattr(user, 'role', None) not in (CustomUser.Role.OPERATIONAL, CustomUser.Role.ESTIMATOR):
        return {}
    collaborator = Collaborator.objects.filter(
        email__iexact=(getattr(user, 'email', '') or '').strip(),
        is_active=True,
        function__in=(Collaborator.Function.OPERATIONAL, Collaborator.Function.ESTIMATOR),
    ).first()
    if collaborator is None:
        return {}

    today = timezone.localdate()
    day = TimeClockDay.objects.filter(collaborator=collaborator, date=today).first()
    label = 'Registrar Entrada'
    color = 'bg-gradient-to-r from-[#D4AF37] to-[#AA882C] text-[#0D0D0D]'
    disabled = False
    if day and day.entry_at and not day.exit_at:
        label = 'Registrar Saída'
        color = 'bg-gradient-to-r from-[#22c55e] to-[#16a34a] text-[#0D0D0D]'
    if day and day.exit_at:
        label = 'Jornada Concluída'
        color = 'border border-[#404040] bg-[#262626] text-[#737373]'
        disabled = True
    return {
        'timeclock_nav': {
            'label': label,
            'class': color,
            'disabled': disabled,
            'url': reverse('core:timeclock_ponto'),
        }
    }

