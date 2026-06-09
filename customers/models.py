from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=255)
    document_cpf_cnpj = models.CharField(max_length=20, unique=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


class Vehicle(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='vehicles')
    plate = models.CharField(max_length=10, unique=True)
    model = models.CharField(max_length=80)
    brand = models.CharField(max_length=80)
    color = models.CharField(max_length=50, blank=True)
    year = models.CharField(max_length=9, blank=True)
    image_url = models.URLField(blank=True)
    image_file = models.FileField(upload_to='uploads/vehicles/', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('plate',)

    def __str__(self):
        return self.plate
