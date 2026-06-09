from django.contrib.auth import views as auth_views
from django.urls import path

from .forms import CustomAuthenticationForm, SimplePasswordChangeForm
from .views import (
    CollaboratorCreateView,
    CollaboratorDeleteView,
    CollaboratorListView,
    CollaboratorResetPasswordView,
    CollaboratorUpdateView,
    CustomLoginView,
    RegisterView,
)

app_name = 'users'

urlpatterns = [
    path(
        'login/',
        CustomLoginView.as_view(authentication_form=CustomAuthenticationForm),
        name='login',
    ),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path(
        'trocar-senha/',
        auth_views.PasswordChangeView.as_view(
            form_class=SimplePasswordChangeForm,
            template_name='users/password_change.html',
            success_url='/usuarios/trocar-senha/concluido/',
        ),
        name='password_change',
    ),
    path(
        'trocar-senha/concluido/',
        auth_views.PasswordChangeDoneView.as_view(template_name='users/password_change_done.html'),
        name='password_change_done',
    ),
    path('cadastro/', RegisterView.as_view(), name='register'),
    path('colaboradores/', CollaboratorListView.as_view(), name='collaborator_list'),
    path('colaboradores/novo/', CollaboratorCreateView.as_view(), name='collaborator_create'),
    path('colaboradores/<int:pk>/editar/', CollaboratorUpdateView.as_view(), name='collaborator_update'),
    path('colaboradores/<int:pk>/resetar-senha/', CollaboratorResetPasswordView.as_view(), name='collaborator_reset_password'),
    path('colaboradores/<int:pk>/excluir/', CollaboratorDeleteView.as_view(), name='collaborator_delete'),
]
