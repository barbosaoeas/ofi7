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
        PONTO = 'PONTO', _('Ponto (Totem)')
        TVKANBAN = 'TVKANBAN', _('TV Kanban')

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
        PONTO = 'PONTO', _('Ponto (Totem)')
        TVKANBAN = 'TVKANBAN', _('TV Kanban')

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
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)

    @property
    def photo_url(self):
        try:
            if self.image_file:
                return self.image_file.url
        except Exception:
            pass
        return self.image_url or ''

    def __str__(self):
        return self.name


class TimeClockDay(models.Model):
    collaborator = models.ForeignKey(Collaborator, on_delete=models.CASCADE, related_name='timeclock_days')
    date = models.DateField()
    entry_at = models.DateTimeField(null=True, blank=True)
    exit_at = models.DateTimeField(null=True, blank=True)
    lunch_out_at = models.DateTimeField(null=True, blank=True)
    lunch_in_at = models.DateTimeField(null=True, blank=True)
    minutes_late = models.IntegerField(default=0)
    minutes_extra = models.IntegerField(default=0)
    minutes_missing = models.IntegerField(default=0)
    minutes_worked = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (('collaborator', 'date'),)
        ordering = ('-date', 'collaborator__name', 'id')

    def __str__(self):
        return f'{self.collaborator} - {self.date}'
