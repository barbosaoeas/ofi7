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
    def clean_new_password1(self):
        password = (self.cleaned_data.get('new_password1') or '').strip()
        if not password.isdigit():
            raise ValidationError('Use somente numeros na senha.')
        if len(password) < 6:
            raise ValidationError('Use pelo menos 6 numeros.')
        if len(password) > 8:
            raise ValidationError('Use no maximo 8 numeros.')
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
