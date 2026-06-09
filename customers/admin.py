from django.contrib import admin

from .models import Customer, Vehicle


class VehicleInline(admin.TabularInline):
    model = Vehicle
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    search_fields = ('name', 'document_cpf_cnpj')
    list_display = ('name', 'document_cpf_cnpj', 'phone', 'email', 'created_at')
    inlines = (VehicleInline,)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    search_fields = ('plate', 'model', 'brand')
    list_display = ('plate', 'customer', 'brand', 'model', 'year', 'created_at')
