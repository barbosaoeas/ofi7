from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.core.exceptions import ValidationError

from .models import Collaborator, CustomUser


class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ('email', 'role')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].label = 'E-mail'
        self.fields['email'].widget.attrs.update(
            {
                'placeholder': 'seu@email.com',
                'autocomplete': 'email',
            }
        )
        self.fields['password1'].label = 'Senha'
        self.fields['password2'].label = 'Confirmar senha'


class CustomAuthenticationForm(AuthenticationForm):
    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request=request, *args, **kwargs)
        self.fields['username'].label = 'E-mail'
        self.fields['username'].widget = forms.EmailInput(
            attrs={
                'autocomplete': 'email',
                'placeholder': 'seu@email.com',
            }
        )
        self.fields['password'].label = 'Senha'
        self.fields['password'].widget.attrs.update({'autocomplete': 'current-password'})


class SimplePasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = getattr(self, 'user', None)
        is_visual = (
            user
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == 'VISUAL'
        )
        if is_visual:
            self.fields['new_password1'].help_text = (
                'Perfil Visual (Smart TV). Senha pode ser simples para fácil digitação (ex: tv1234).'
            )
        else:
            self.fields['new_password1'].help_text = (
                'Use pelo menos 8 caracteres. Não pode ser só números, não pode ser igual ao e-mail e não pode ser uma senha comum (ex: 12345678, senha). '
                '(Perfil Visual permite senha mais simples.)'
            )

    def clean_new_password1(self):
        password = (self.cleaned_data.get('new_password1') or '').strip()
        user = getattr(self, 'user', None)
        is_visual = (
            user
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == 'VISUAL'
        )
        if is_visual:
            if not password:
                raise ValidationError('Informe uma senha para o perfil Visual.')
            return password
        if len(password) < 8:
            raise ValidationError('Use pelo menos 8 caracteres na senha.')
        if password.isdigit():
            raise ValidationError('A senha não pode ser composta apenas por números. Adicione letras e/ou símbolos.')
        return password


class CollaboratorForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True
        self.fields['email'].label = 'E-mail de login'
        self.fields['email'].widget.attrs.update(
            {
                'placeholder': 'funcionario@empresa.com',
                'autocomplete': 'email',
            }
        )

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        return email or None

    def clean(self):
        cleaned = super().clean()
        function = cleaned.get('function')
        if function in (Collaborator.Function.MANAGER, Collaborator.Function.FINANCE):
            cleaned['commission_percent'] = 0
        return cleaned

    class Meta:
        model = Collaborator
        fields = (
            'name',
            'email',
            'phone',
            'cpf',
            'address',
            'function',
            'hire_date',
            'commission_percent',
            'image_file',
            'image_url',
        )
