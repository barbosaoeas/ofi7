from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from users.models import CustomUser
from .models import SystemSettings


class RoleRequiredMixin(LoginRequiredMixin):
    """Mixin que SEMPRE exige login e, se allowed_roles for definido, exige a role.

    Herda de LoginRequiredMixin para garantir autenticação SEMPRE (mesmo que
    allowed_roles seja None ou alguém inverta a ordem na classe filha).
    Se allowed_roles for None: permite QUALQUER usuário autenticado.
    Se allowed_roles for setado: exige superuser OU uma das roles listadas.
    Usuários VISUAL logados mas sem permissão caem no kanban de hoje.
    Usuários não logados são redirecionados para a tela de login (via LoginRequiredMixin).
    """
    allowed_roles = None

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if getattr(response, 'status_code', None) == 302:
            return response

        roles = self.allowed_roles
        if roles is None:
            return response

        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            if getattr(user, 'is_superuser', False):
                return response
            if getattr(user, 'role', None) in roles:
                return response

        messages.error(request, 'Sem permissão para acessar esta página.')
        if (
            user
            and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == CustomUser.Role.VISUAL
        ):
            return redirect('budgets:kanban_today')
        return redirect('core:dashboard')


class PublicIndexView(TemplateView):
    template_name = 'core/public_index.html'

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)


class DashboardView(RoleRequiredMixin, TemplateView):
    template_name = 'core/dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if (
            user
            and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == CustomUser.Role.VISUAL
        ):
            return redirect('budgets:kanban_today')
        return super().dispatch(request, *args, **kwargs)


class SystemSettingsView(RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get(self, request):
        settings_obj = SystemSettings.get_solo()
        return render(request, 'core/system_settings.html', {'settings': settings_obj})

    def post(self, request):
        settings_obj = SystemSettings.get_solo()

        settings_obj.name = (request.POST.get('name') or '').strip()
        settings_obj.address = (request.POST.get('address') or '').strip()
        settings_obj.phone = (request.POST.get('phone') or '').strip()
        settings_obj.primary_color = (request.POST.get('primary_color') or '').strip()
        settings_obj.secondary_color = (request.POST.get('secondary_color') or '').strip()

        logo = request.FILES.get('logo')
        if logo:
            settings_obj.logo = logo

        if not settings_obj.name:
            settings_obj.name = 'Controle Oficina'
        if not settings_obj.primary_color:
            settings_obj.primary_color = '#D4AF37'
        if not settings_obj.secondary_color:
            settings_obj.secondary_color = '#AA882C'

        settings_obj.updated_at = timezone.now()
        settings_obj.save()
        messages.success(request, 'Configuração salva.')
        return redirect('core:system_settings')
