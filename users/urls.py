from django.contrib.auth import views as auth_views
from django.urls import path

from .forms import CustomAuthenticationForm
from .views import (
    CollaboratorCreateView,
    CollaboratorDeleteView,
    CollaboratorListView,
    CollaboratorResetPasswordView,
    CollaboratorToggleActiveView,
    CollaboratorUpdateView,
    CustomLoginView,
    CustomPasswordChangeDoneView,
    CustomPasswordChangeView,
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
        CustomPasswordChangeView.as_view(),
        name='password_change',
    ),
    path(
        'trocar-senha/concluido/',
        CustomPasswordChangeDoneView.as_view(),
        name='password_change_done',
    ),
    path('cadastro/', RegisterView.as_view(), name='register'),
    path('colaboradores/', CollaboratorListView.as_view(), name='collaborator_list'),
    path('colaboradores/novo/', CollaboratorCreateView.as_view(), name='collaborator_create'),
    path('colaboradores/<int:pk>/editar/', CollaboratorUpdateView.as_view(), name='collaborator_update'),
    path('colaboradores/<int:pk>/resetar-senha/', CollaboratorResetPasswordView.as_view(), name='collaborator_reset_password'),
    path('colaboradores/<int:pk>/alternar-ativo/', CollaboratorToggleActiveView.as_view(), name='collaborator_toggle_active'),
    path('colaboradores/<int:pk>/excluir/', CollaboratorDeleteView.as_view(), name='collaborator_delete'),
]
