from django.db import models


class SystemSettings(models.Model):
    name = models.CharField(max_length=120, blank=True)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    primary_color = models.CharField(max_length=20, blank=True, default='#D4AF37')
    secondary_color = models.CharField(max_length=20, blank=True, default='#AA882C')
    logo = models.FileField(upload_to='system/', blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração do sistema'
        verbose_name_plural = 'Configurações do sistema'

    @classmethod
    def get_solo(cls):
        obj = cls.objects.order_by('id').first()
        if obj is not None:
            return obj
        return cls.objects.create(name='Controle Oficina')

# Create your models here.
