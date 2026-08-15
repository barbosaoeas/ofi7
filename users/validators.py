from django.contrib.auth.password_validation import (
    CommonPasswordValidator,
    MinimumLengthValidator,
    NumericPasswordValidator,
    UserAttributeSimilarityValidator,
)
from django.core.exceptions import ValidationError

from .models import CustomUser


class RoleBasedPasswordValidator:
    def __init__(self):
        self.user_attribute_validator = UserAttributeSimilarityValidator()
        self.min_length_validator = MinimumLengthValidator(min_length=8)
        self.common_password_validator = CommonPasswordValidator()
        self.numeric_password_validator = NumericPasswordValidator()

    def _is_visual(self, user):
        if user is None:
            return False
        if getattr(user, 'is_superuser', False):
            return False
        role = getattr(user, 'role', None)
        if callable(role):
            try:
                role = role()
            except Exception:
                role = None
        return role == CustomUser.Role.VISUAL

    def validate(self, password, user=None):
        if self._is_visual(user):
            return
        self.user_attribute_validator.validate(password, user)
        self.min_length_validator.validate(password, user)
        self.common_password_validator.validate(password, user)
        self.numeric_password_validator.validate(password, user)

    def get_help_text(self):
        return (
            'Sua senha deve conter pelo menos 8 caracteres, não ser muito similar ao seu e-mail, '
            'não pode ser uma senha comum e não pode ser totalmente numérica. '
            '(Usuários com perfil Visual podem usar senhas mais simples para a Smart TV.)'
        )

    def password_changed(self, password, user=None):
        for validator in (
            self.user_attribute_validator,
            self.min_length_validator,
            self.common_password_validator,
            self.numeric_password_validator,
        ):
            if hasattr(validator, 'password_changed'):
                try:
                    validator.password_changed(password, user)
                except NotImplementedError:
                    pass
