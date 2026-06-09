from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class CustomUserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', 'MANAGER')

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email=email, password=password, **extra_fields)


class CustomUser(AbstractUser):
    class Role(models.TextChoices):
        MANAGER = 'MANAGER', _('Gerente')
        FINANCE = 'FINANCE', _('Financeiro')
        ESTIMATOR = 'ESTIMATOR', _('Orçamentista')
        OPERATIONAL = 'OPERATIONAL', _('Operacional')
        VISUAL = 'VISUAL', _('Visual')

    username = None
    email = models.EmailField(_('email address'), unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OPERATIONAL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def __str__(self):
        return self.email


class Collaborator(models.Model):
    class Function(models.TextChoices):
        MANAGER = 'MANAGER', _('Gerente')
        FINANCE = 'FINANCE', _('Financeiro')
        ESTIMATOR = 'ESTIMATOR', _('Orçamentista')
        OPERATIONAL = 'OPERATIONAL', _('Operacional')
        VISUAL = 'VISUAL', _('Visual')

    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, null=True, unique=True)
    phone = models.CharField(max_length=40, blank=True)
    cpf = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    function = models.CharField(max_length=20, choices=Function.choices, default=Function.OPERATIONAL)
    hire_date = models.DateField(null=True, blank=True)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    image_url = models.URLField(blank=True)
    image_file = models.FileField(upload_to='uploads/collaborators/', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name
