from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from core.views import RoleRequiredMixin
from .forms import CollaboratorForm, CustomUserCreationForm
from .models import Collaborator, CustomUser


DEFAULT_PASSWORD = '123456'


def sync_collaborator_login(collaborator, previous_email=None):
    login_email = (getattr(collaborator, 'email', '') or '').strip().lower()
    old_email = (previous_email or '').strip().lower()

    if not login_email:
        return

    user = None
    if old_email:
        user = CustomUser.objects.filter(email__iexact=old_email).first()

    if user is None:
        user = CustomUser.objects.filter(email__iexact=login_email).first()

    if user is not None:
        duplicate = CustomUser.objects.filter(email__iexact=login_email).exclude(pk=user.pk).exists()
        if duplicate:
            raise ValueError('Ja existe outro usuario com este e-mail de login.')
        update_fields = []
        if user.email != login_email:
            user.email = login_email
            update_fields.append('email')
        if user.role != collaborator.function:
            user.role = collaborator.function
            update_fields.append('role')
        if update_fields:
            user.save(update_fields=update_fields)
        return

    CustomUser.objects.create_user(
        email=login_email,
        password=DEFAULT_PASSWORD,
        role=collaborator.function,
    )


class CustomLoginView(LoginView):
    template_name = 'users/login.html'

    def get_success_url(self):
        user = getattr(self.request, 'user', None)
        if user and getattr(user, 'is_authenticated', False) and getattr(user, 'role', None) == CustomUser.Role.VISUAL:
            return reverse('budgets:kanban_today')
        return super().get_success_url()

    def form_valid(self, form):
        response = super().form_valid(form)
        user = getattr(self.request, 'user', None)
        if user and getattr(user, 'is_authenticated', False) and user.check_password(DEFAULT_PASSWORD):
            messages.warning(self.request, 'Sua senha está no padrão. Troque a senha para continuar.')
            return redirect('users:password_change')
        return response


class RegisterView(LoginRequiredMixin, RoleRequiredMixin, CreateView):
    template_name = 'users/register.html'
    form_class = CustomUserCreationForm
    success_url = reverse_lazy('users:collaborator_list')
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            user = self.object
            if user is not None:
                collaborator = Collaborator.objects.filter(email__iexact=user.email).first()
                if collaborator is None:
                    Collaborator.objects.create(
                        name=user.email,
                        email=user.email,
                        function=user.role,
                    )
                user.set_password(DEFAULT_PASSWORD)
                user.save(update_fields=['password'])
            return response


class CollaboratorListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = Collaborator
    template_name = 'users/collaborator_list.html'
    context_object_name = 'collaborators'
    paginate_by = 25
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)


class CollaboratorCreateView(LoginRequiredMixin, RoleRequiredMixin, CreateView):
    model = Collaborator
    form_class = CollaboratorForm
    template_name = 'users/collaborator_form.html'
    success_url = reverse_lazy('users:collaborator_list')
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def form_valid(self, form):
        try:
            with transaction.atomic():
                response = super().form_valid(form)
                sync_collaborator_login(self.object)
                return response
        except ValueError as exc:
            form.add_error('email', str(exc))
            return self.form_invalid(form)


class CollaboratorUpdateView(LoginRequiredMixin, RoleRequiredMixin, UpdateView):
    model = Collaborator
    form_class = CollaboratorForm
    template_name = 'users/collaborator_form.html'
    success_url = reverse_lazy('users:collaborator_list')
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def form_valid(self, form):
        previous_email = ''
        if self.object is not None:
            previous_email = (self.object.email or '').strip().lower()
        try:
            with transaction.atomic():
                response = super().form_valid(form)
                sync_collaborator_login(self.object, previous_email=previous_email)
                return response
        except ValueError as exc:
            form.add_error('email', str(exc))
            return self.form_invalid(form)


class CollaboratorDeleteView(LoginRequiredMixin, RoleRequiredMixin, DeleteView):
    model = Collaborator
    template_name = 'users/collaborator_confirm_delete.html'
    success_url = reverse_lazy('users:collaborator_list')
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)


class CollaboratorResetPasswordView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def post(self, request, pk):
        collaborator = Collaborator.objects.filter(pk=pk).first()
        if collaborator is None:
            messages.error(request, 'Colaborador não encontrado.')
            return redirect('users:collaborator_list')

        raw = (collaborator.email or '').strip()
        if not raw:
            messages.error(request, 'Colaborador sem e-mail vinculado para login.')
            return redirect('users:collaborator_list')

        user = CustomUser.objects.filter(email__iexact=raw).first()
        if user is None:
            messages.error(request, 'Usuário não encontrado para este colaborador.')
            return redirect('users:collaborator_list')

        user.set_password(DEFAULT_PASSWORD)
        user.save(update_fields=['password'])
        messages.success(request, f'Senha resetada para {DEFAULT_PASSWORD}.')
        return redirect('users:collaborator_list')
