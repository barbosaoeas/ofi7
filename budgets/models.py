from django.db import models

from customers.models import Customer, Vehicle


class Budget(models.Model):
    class RefusalReasonCode(models.TextChoices):
        TOO_EXPENSIVE = 'TOO_EXPENSIVE', 'Achou caro'
        NO_MONEY = 'NO_MONEY', 'Não tem dinheiro para fazer o serviço'
        OTHER_SHOP = 'OTHER_SHOP', 'Vai procurar outra oficina'
        CLAIM_PROOF = 'CLAIM_PROOF', 'Orçamento só para comprovação de sinistro'
        INSURANCE = 'INSURANCE', 'Vai acionar o seguro'
        SOLD_VEHICLE = 'SOLD_VEHICLE', 'Vendeu o veículo'
        NO_RESPONSE = 'NO_RESPONSE', 'Sem retorno do cliente'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Aguardando Resposta'
        AUTHORIZED = 'AUTHORIZED', 'Autorizada'
        NOT_APPROVED = 'NOT_APPROVED', 'Não Aprovada'

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='budgets')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='budgets')
    cilia_number = models.PositiveIntegerField(unique=True, null=True, blank=True)
    cilia_version = models.PositiveIntegerField(null=True, blank=True)
    parent_budget = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='complements',
    )
    complement_sequence = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    refusal_reason_code = models.CharField(max_length=40, choices=RefusalReasonCode.choices, blank=True)
    refusal_reason = models.CharField(max_length=255, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    entry_date = models.DateField(null=True, blank=True)
    repair_start_date = models.DateField(null=True, blank=True)
    expected_delivery_date = models.DateField(null=True, blank=True)
    allow_repair_without_parts = models.BooleanField(default=False)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shop_parts_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    services_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    labor_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    markup_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    source_xml = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    @property
    def display_number(self):
        if self.parent_budget and self.parent_budget.cilia_number and self.complement_sequence:
            return f'{self.parent_budget.cilia_number}.{self.complement_sequence}'
        if self.cilia_number:
            return str(self.cilia_number)
        return str(self.pk)

    def __str__(self):
        if self.parent_budget and self.parent_budget.cilia_number and self.complement_sequence:
            return f'Orçamento #{self.parent_budget.cilia_number}.{self.complement_sequence}'
        if self.cilia_number:
            return f'Orçamento #{self.cilia_number}'
        return f'Orçamento #{self.pk}'


class Piece(models.Model):
    class ProviderType(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Cliente'
        INSURER = 'INSURER', 'Seguradora'
        SHOP = 'SHOP', 'Oficina'

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name='pieces')
    name = models.CharField(max_length=255)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    profit_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    provider_type = models.CharField(max_length=20, choices=ProviderType.choices, default=ProviderType.SHOP)
    purchase_date = models.DateField(null=True, blank=True)
    expected_arrival_date = models.DateField(null=True, blank=True)
    arrived = models.BooleanField(default=False)
    arrival_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    @property
    def sale_price(self):
        if self.provider_type != self.ProviderType.SHOP:
            return None
        try:
            return self.cost_price * (1 + (self.profit_percent / 100))
        except Exception:
            return self.cost_price


class ThirdPartyService(models.Model):
    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name='third_party_services')
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return self.description


class BudgetPhoto(models.Model):
    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name='photos')
    image_file = models.FileField(upload_to='uploads/budgets/')
    caption = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at', '-id')

    @property
    def image_url(self):
        try:
            if self.image_file:
                return self.image_file.url
        except Exception:
            pass
        return ''

    def __str__(self):
        label = self.caption or f'Foto {self.pk}'
        return f'{self.budget} - {label}'


class ServiceCatalog(models.Model):
    class CommissionMode(models.TextChoices):
        NONE = 'NONE', 'Sem comissão'
        PERCENT = 'PERCENT', '% sobre valor'
        FIXED = 'FIXED', 'Valor fixo'

    name = models.CharField(max_length=120, unique=True)
    default_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    commission_mode = models.CharField(max_length=10, choices=CommissionMode.choices, default=CommissionMode.NONE)
    commission_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


class WorkOrder(models.Model):
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Aberta'
        CLOSED = 'CLOSED', 'Fechada'

    budget = models.OneToOneField(Budget, on_delete=models.CASCADE, related_name='work_order')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    vehicle_image_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'OS #{self.budget.display_number}'


class WorkOrderTask(models.Model):
    class Activity(models.TextChoices):
        DISMANTLING = 'DISMANTLING', 'Desmontagem'
        BODYWORK = 'BODYWORK', 'Funilaria'
        PREPARATION = 'PREPARATION', 'Preparação'
        PAINTING = 'PAINTING', 'Pintura'
        ASSEMBLY = 'ASSEMBLY', 'Montagem'
        POLISHING = 'POLISHING', 'Polimento'
        DELIVERY_PREP = 'DELIVERY_PREP', 'Prep Entrega'

    class Status(models.TextChoices):
        SCHEDULED = 'SCHEDULED', 'Agendado'
        RUNNING = 'RUNNING', 'Em andamento'
        PAUSED = 'PAUSED', 'Pausado'
        DONE = 'DONE', 'Concluído'

    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='tasks')
    activity = models.CharField(max_length=20, choices=Activity.choices)
    service = models.ForeignKey(ServiceCatalog, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    description = models.CharField(max_length=255, blank=True)
    collaborator = models.ForeignKey('users.Collaborator', on_delete=models.PROTECT, null=True, blank=True)
    scheduled_date = models.DateField(null=True, blank=True)
    planned_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    planned_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    actual_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    elapsed_seconds = models.PositiveIntegerField(default=0)
    allow_overtime = models.BooleanField(default=False)
    last_started_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('order', 'id')

    def __str__(self):
        return f'{self.get_activity_display()} - {self.work_order}'


class CommissionLine(models.Model):
    task = models.ForeignKey(WorkOrderTask, on_delete=models.CASCADE, related_name='commissions')
    collaborator = models.ForeignKey('users.Collaborator', on_delete=models.PROTECT)
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    base_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'Comissão - {self.collaborator} - {self.task}'


class CashMovement(models.Model):
    class Direction(models.TextChoices):
        IN = 'IN', 'Entrada'
        OUT = 'OUT', 'Saída'

    class Source(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Cliente'
        INSURER = 'INSURER', 'Seguradora'
        OTHER = 'OTHER', 'Outro'

    category = models.ForeignKey(
        'CashCategory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movements',
    )
    budget = models.ForeignKey(Budget, on_delete=models.PROTECT, null=True, blank=True, related_name='cash_movements')
    direction = models.CharField(max_length=10, choices=Direction.choices, default=Direction.IN)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.OTHER)
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)
    is_realized = models.BooleanField(default=False)
    realized_at = models.DateTimeField(null=True, blank=True)
    recurrence_group = models.CharField(max_length=36, blank=True)
    recurrence_index = models.PositiveIntegerField(default=1)
    recurrence_total = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('due_date', 'created_at')

    def __str__(self):
        if self.budget_id:
            return f'Caixa - {self.get_direction_display()} - Orçamento #{self.budget.display_number}'
        return f'Caixa - {self.get_direction_display()}'


class CashCategory(models.Model):
    class ExpenseGroup(models.TextChoices):
        OPERATIONAL = 'OPERATIONAL', 'Operacional'
        ADMIN = 'ADMIN', 'Administrativo'

    direction = models.CharField(max_length=10, choices=CashMovement.Direction.choices, default=CashMovement.Direction.OUT)
    group = models.CharField(max_length=20, choices=ExpenseGroup.choices, blank=True)
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('direction', 'group', 'name')

    def __str__(self):
        return self.name
