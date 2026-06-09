from django.contrib import admin

from .models import (
    Budget,
    CashCategory,
    CashMovement,
    CommissionLine,
    Piece,
    ServiceCatalog,
    ThirdPartyService,
    WorkOrder,
    WorkOrderTask,
)


class PieceInline(admin.TabularInline):
    model = Piece
    extra = 0


class ThirdPartyServiceInline(admin.TabularInline):
    model = ThirdPartyService
    extra = 0


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'cilia_number',
        'cilia_version',
        'parent_budget',
        'complement_sequence',
        'status',
        'customer',
        'vehicle',
        'total_amount',
        'created_at',
    )
    list_filter = ('status',)
    search_fields = ('cilia_number', 'customer__name', 'customer__document_cpf_cnpj', 'vehicle__plate')
    inlines = (PieceInline, ThirdPartyServiceInline)


@admin.register(Piece)
class PieceAdmin(admin.ModelAdmin):
    list_display = ('name', 'budget', 'provider_type', 'arrived', 'cost_price', 'created_at')
    list_filter = ('provider_type', 'arrived')
    search_fields = ('name', 'budget__customer__name', 'budget__vehicle__plate')


@admin.register(ServiceCatalog)
class ServiceCatalogAdmin(admin.ModelAdmin):
    list_display = ('name', 'default_value', 'commission_mode', 'commission_value')
    search_fields = ('name',)


class WorkOrderTaskInline(admin.TabularInline):
    model = WorkOrderTask
    extra = 0


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'budget', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('budget__cilia_number', 'budget__customer__name', 'budget__vehicle__plate')
    inlines = (WorkOrderTaskInline,)


@admin.register(WorkOrderTask)
class WorkOrderTaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'work_order', 'activity', 'service', 'collaborator', 'scheduled_date', 'planned_hours', 'status')
    list_filter = ('activity', 'status', 'service')
    search_fields = ('work_order__budget__cilia_number', 'description', 'collaborator__name')


@admin.register(CommissionLine)
class CommissionLineAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'collaborator', 'percent', 'base_amount', 'commission_amount', 'created_at')
    search_fields = ('task__description', 'collaborator__name')


@admin.register(CashMovement)
class CashMovementAdmin(admin.ModelAdmin):
    list_display = ('id', 'direction', 'source', 'category', 'budget', 'amount', 'due_date', 'is_realized', 'created_at')
    list_filter = ('direction', 'source', 'is_realized')
    search_fields = ('description', 'budget__cilia_number', 'budget__customer__name', 'budget__vehicle__plate')


@admin.register(CashCategory)
class CashCategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'direction', 'group', 'name', 'is_active')
    list_filter = ('direction', 'is_active')
    search_fields = ('name',)
