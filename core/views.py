from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import TemplateView

from users.models import CustomUser


class RoleRequiredMixin:
    allowed_roles = None

    def dispatch(self, request, *args, **kwargs):
        roles = self.allowed_roles
        if roles is None:
            return super().dispatch(request, *args, **kwargs)

        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            if getattr(user, 'is_superuser', False):
                return super().dispatch(request, *args, **kwargs)
            if getattr(user, 'role', None) in roles:
                return super().dispatch(request, *args, **kwargs)

        messages.error(request, 'Sem permissão para acessar esta página.')
        if user and getattr(user, 'is_authenticated', False) and getattr(user, 'role', None) == CustomUser.Role.VISUAL:
            return redirect('budgets:kanban_today')
        return redirect('core:dashboard')


class PublicIndexView(TemplateView):
    template_name = 'core/public_index.html'


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'core/dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False) and getattr(user, 'role', None) == CustomUser.Role.VISUAL:
            return redirect('budgets:kanban_today')
        return super().dispatch(request, *args, **kwargs)
