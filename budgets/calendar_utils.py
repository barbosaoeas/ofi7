from datetime import date, datetime, time as dt_time, timedelta
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from django.utils import timezone


KANBAN_CUTOFF_TIME = dt_time(17, 48)

WORKDAY_WEEKDAYS = (0, 1, 2, 3, 4, 5)


def get_local_today() -> date:
    return timezone.localdate()


def get_local_now() -> datetime:
    return timezone.localtime(timezone.now())


def next_weekday_including_saturday(from_date: date) -> date:
    cursor = from_date + timedelta(days=1)
    while cursor.weekday() not in WORKDAY_WEEKDAYS:
        cursor += timedelta(days=1)
    return cursor


def capped_work_delta_seconds(
    last_started_at: Optional[datetime],
    now: datetime,
    allow_overtime: bool,
) -> Tuple[int, Optional[datetime]]:
    if last_started_at is None:
        return 0, None

    last_local = timezone.localtime(last_started_at)
    now_local = timezone.localtime(now)

    if allow_overtime:
        effective_end = now_local
    else:
        tz = timezone.get_current_timezone()
        started_day = last_local.date()
        cutoff_dt = timezone.make_aware(
            datetime.combine(started_day, KANBAN_CUTOFF_TIME), tz
        )
        if last_local >= cutoff_dt:
            effective_end = last_local
        elif now_local.date() == started_day:
            effective_end = now_local if now_local <= cutoff_dt else cutoff_dt
        else:
            effective_end = cutoff_dt

    delta = int((effective_end - last_local).total_seconds())
    return max(delta, 0), effective_end


SECONDS_PER_HOUR = Decimal(3600)


def seconds_to_hours_decimal(seconds: int) -> Decimal:
    if seconds <= 0:
        return Decimal("0.00")
    return (Decimal(seconds) / SECONDS_PER_HOUR).quantize(Decimal("0.01"))


@dataclass
class PartSummary:
    name: str
    expected_arrival_date: Optional[date]


@dataclass
class WorkOrderStatusSummary:
    is_blocked: bool
    has_pending_shop_parts: bool
    allow_repair_without_parts: bool
    blocked_parts: List[PartSummary]
    total_tasks: int
    done_tasks: int
    open_tasks: int
    progress_percent: int
    active_task: Optional[object]
    state_code: str
    state_label: str
    state_priority: int
    section_label: str
    start_display: Optional[str]


def budget_has_pending_shop_parts(budget) -> bool:
    if not budget or not getattr(budget, "id", None):
        return False
    from .models import Piece

    return Piece.objects.filter(
        budget_id=budget.id,
        provider_type=Piece.ProviderType.SHOP,
        arrived=False,
        arrival_date__isnull=True,
    ).exists()


def budget_get_pending_shop_parts(budget) -> List[PartSummary]:
    if not budget or not getattr(budget, "id", None):
        return []
    from .models import Piece

    rows = (
        Piece.objects.filter(
            budget_id=budget.id,
            provider_type=Piece.ProviderType.SHOP,
            arrived=False,
            arrival_date__isnull=True,
        )
        .only("name", "expected_arrival_date")
        .order_by("name")
    )
    return [PartSummary(name=r.name, expected_arrival_date=r.expected_arrival_date) for r in rows]


def seconds_to_hms(seconds: int) -> str:
    seconds = max(seconds or 0, 0)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h and m:
        return f"{h:d}h {m:02d}m"
    if h:
        return f"{h:d}h"
    return f"{m:d}m"


def compute_work_order_status(work_order, now: Optional[datetime] = None) -> WorkOrderStatusSummary:
    budget = getattr(work_order, "budget", None)
    now = now or timezone.localtime(timezone.now())

    has_pending_shop_parts = budget_has_pending_shop_parts(budget)
    allow_repair_without_parts = bool(getattr(budget, "allow_repair_without_parts", False)) if budget else False
    blocked_parts = budget_get_pending_shop_parts(budget) if has_pending_shop_parts else []

    is_blocked = (
        has_pending_shop_parts
        and not allow_repair_without_parts
        and bool(budget)
        and getattr(budget, "status", "") == BudgetStatus_AUTHORIZED
    )

    tasks = list(work_order.tasks.all()) if work_order and work_order.pk else []
    total_tasks = len(tasks)
    done_tasks = sum(1 for t in tasks if getattr(t, "status", None) == TaskStatus_DONE)
    open_tasks = total_tasks - done_tasks
    progress_percent = int(100 * done_tasks / total_tasks) if total_tasks else 0

    running_tasks = [t for t in tasks if getattr(t, "status", None) == TaskStatus_RUNNING]
    paused_tasks = [t for t in tasks if getattr(t, "status", None) == TaskStatus_PAUSED]
    scheduled_tasks = [t for t in tasks if getattr(t, "status", None) == TaskStatus_SCHEDULED]

    active_task = None
    if running_tasks:
        active_task = running_tasks[0]
        state_code = "A"
        state_label = "Em andamento"
        state_priority = 3
    elif paused_tasks:
        active_task = paused_tasks[0]
        state_code = "P"
        state_label = "Pausado"
        state_priority = 3
    elif scheduled_tasks:
        active_task = scheduled_tasks[0]
        state_code = "N"
        state_label = "Não iniciado"
        state_priority = 3
    elif total_tasks == 0:
        active_task = None
        state_code = "N"
        state_label = "Sem tarefas cadastradas"
        state_priority = 3
    else:
        active_task = None
        state_code = "C"
        state_label = "Concluída"
        state_priority = 3

    if has_pending_shop_parts and allow_repair_without_parts:
        state_priority = 2
        state_code = "L"
        state_label = "Liberado sem peças"

    if is_blocked:
        state_priority = 1
        state_code = "B"
        state_label = "Aguardando peças"

    section_label = "-"
    start_display = None
    task = active_task
    if state_code == "B":
        section_label = "Aguardando peças"
        start_display = None
    elif state_code == "L":
        section_label = "Liberado (sem peças)"
        first_scheduled = scheduled_tasks[0] if scheduled_tasks else None
        if first_scheduled and getattr(first_scheduled, "scheduled_date", None):
            start_display = "Prev. " + first_scheduled.scheduled_date.strftime("%d/%m")
    elif state_code == "C":
        section_label = "Concluída"
        start_display = "✓"
    elif state_code == "N" and total_tasks == 0:
        section_label = "Sem tarefas"
        start_display = None
    elif task is not None:
        descr = getattr(task, "description", None) or (f"Tarefa #{task.id}" if getattr(task, "id", None) else "Tarefa")
        section_label = str(descr).strip() or "-"
        t_status = getattr(task, "status", None)
        if t_status in (TaskStatus_RUNNING, TaskStatus_PAUSED):
            lst = getattr(task, "last_started_at", None)
            if lst is not None:
                start_display = timezone.localtime(lst).strftime("%d/%m %H:%M")
        elif t_status == TaskStatus_SCHEDULED:
            sd = getattr(task, "scheduled_date", None)
            if sd is not None:
                start_display = "Prev. " + sd.strftime("%d/%m")

    return WorkOrderStatusSummary(
        is_blocked=is_blocked,
        has_pending_shop_parts=has_pending_shop_parts,
        allow_repair_without_parts=allow_repair_without_parts,
        blocked_parts=blocked_parts,
        total_tasks=total_tasks,
        done_tasks=done_tasks,
        open_tasks=open_tasks,
        progress_percent=progress_percent,
        active_task=active_task,
        state_code=state_code,
        state_label=state_label,
        state_priority=state_priority,
        section_label=section_label,
        start_display=start_display,
    )


TaskStatus_SCHEDULED = "SCHEDULED"
TaskStatus_RUNNING = "RUNNING"
TaskStatus_PAUSED = "PAUSED"
TaskStatus_DONE = "DONE"
BudgetStatus_AUTHORIZED = "AUTHORIZED"


def get_elapsed_seconds_for_display(task, now: Optional[datetime] = None) -> int:
    if task is None:
        return 0
    status = getattr(task, "status", None)
    now = now or timezone.localtime(timezone.now())
    last_started_at = getattr(task, "last_started_at", None)

    if status == TaskStatus_RUNNING and last_started_at:
        delta, _ = capped_work_delta_seconds(
            last_started_at, now, bool(getattr(task, "allow_overtime", False))
        )
        return (getattr(task, "elapsed_seconds", 0) or 0) + delta
    return int(getattr(task, "elapsed_seconds", 0) or 0)


def simplify_task_status_label(status: str) -> str:
    if status == TaskStatus_SCHEDULED:
        return "S · Não iniciado"
    if status == TaskStatus_RUNNING:
        return "R · Em andamento"
    if status == TaskStatus_PAUSED:
        return "P · Pausado"
    if status == TaskStatus_DONE:
        return "C · Concluído"
    return status or ""


@dataclass
class BudgetPerformanceRow:
    budget_id: int
    display_number: str
    customer_name: str
    vehicle_plate: str
    approved_at: Optional[datetime]
    approved_date: Optional[date]
    repair_start_date: Optional[date]
    expected_delivery_date: Optional[date]
    delivered_at: Optional[datetime]
    is_delivered: bool
    delivery_days_late: int
    idle_before_start_days: int
    process_days_since_start: int
    total_days_since_approved: int
    delivery_bucket_label: str
    delivery_bucket_code: str
    total_planned_hours: float
    total_actual_hours: float
    hours_overrun: float
    has_pending_shop_parts: bool
    allow_repair_without_parts: bool
    pending_shop_parts_count: int
    open_tasks_count: int
    running_tasks_count: int
    done_tasks_count: int
    total_tasks_count: int
    total_task_elapsed_seconds: int
    idle_days_since_approved: int
    probable_causes: List[str]
    # [NOVO - DADOS FINANCEIROS para IA calcular R$ de receita parada / impacto]
    total_amount: float
    shop_parts_total: float
    services_total: float
    labor_total: float
    discount_total: float
    markup_total: float
    late_days: int
    vehicle_model: str


@dataclass
class PerformanceReport:
    total: int
    on_time: List[BudgetPerformanceRow]
    late_1d: List[BudgetPerformanceRow]
    late_2d: List[BudgetPerformanceRow]
    late_3d: List[BudgetPerformanceRow]
    late_4d: List[BudgetPerformanceRow]
    late_5d: List[BudgetPerformanceRow]
    late_plus_5d: List[BudgetPerformanceRow]
    open_with_expected: List[BudgetPerformanceRow]
    no_promise_date: List[BudgetPerformanceRow]
    rows_by_id: Dict[int, BudgetPerformanceRow]
    kpi_on_time_pct: float
    kpi_late_pct: float
    kpi_avg_days_late: float
    kpi_total_overrun_hours: float
    kpi_avg_overrun_hours_late_only: float
    kpi_avg_idle_before_start_days: float
    kpi_total_with_approved_date: int
    # [NOVO - KPIs FINANCEIROS para IA calcular R$ de receita parada]
    kpi_valor_total_carteira: float        # Soma R$ TODOS orcamentos (total_amount)
    kpi_valor_no_prazo: float               # Soma R$ on_time
    kpi_valor_atrasado: float               # Soma R$ todos buckets late_1d ate late_plus_5d
    kpi_valor_atrasado_1d: float
    kpi_valor_atrasado_2d: float
    kpi_valor_atrasado_3d: float
    kpi_valor_atrasado_4d: float
    kpi_valor_atrasado_5d: float
    kpi_valor_atrasado_plus_5d: float       # CRITICO - +5 dias
    kpi_valor_sem_prazo: float              # Soma R$ no_promise_date
    kpi_mao_obra_total_atrasado: float      # Soma R$ labor_total dos atrasados
    kpi_pecas_total_atrasado: float         # Soma R$ shop_parts dos atrasados
    kpi_servicos_total_atrasado: float      # Soma R$ services_total dos atrasados
    # Helper: atraso ponderado (R$ * dias atrasado = "R$·dia")
    kpi_valor_dia_atraso_ponderado: float   # soma (total_amount * dias_late) p/ atrasados

    @property
    def on_time_count(self):
        return len(self.on_time)

    @property
    def late_count(self):
        return len(self.late_1d) + len(self.late_2d) + len(self.late_3d) + len(self.late_4d) + len(self.late_5d) + len(self.late_plus_5d)


def _calculate_probable_causes(r: BudgetPerformanceRow, now: datetime) -> List[str]:
    causes = []
    if r.has_pending_shop_parts and not r.allow_repair_without_parts:
        causes.append(f"⛔ Aguardando {r.pending_shop_parts_count} peça(s) da oficina (sem bypass liberado)")
    elif r.has_pending_shop_parts and r.allow_repair_without_parts:
        causes.append(f"⚠️ {r.pending_shop_parts_count} peça(s) pendente(s) (bypass liberado)")
    if r.idle_before_start_days >= 3:
        causes.append(f"⏸️ Ficou {r.idle_before_start_days} dias PARADO entre aprovação e início do serviço (gargalo de entrada / programação)")
    if r.approved_date and not r.repair_start_date and r.total_days_since_approved >= 4 and not r.is_delivered:
        causes.append(f"🛑 Aprovado há {r.total_days_since_approved} dias e NÃO INICIOU NENHUMA tarefa (reparo não começou)")
    if r.repair_start_date and r.process_days_since_start > 10 and not r.is_delivered and r.done_tasks_count < max(r.total_tasks_count, 1):
        causes.append(f"🐢 Reparo em andamento há {r.process_days_since_start} dias (acima de 10 dias corridos) — investigar gargalos no processo interno")
    if r.hours_overrun > 0.5:
        causes.append(f"⏱️ Estouro de horas: {r.hours_overrun:.2f}h acima do planejado")
    if r.total_tasks_count > 0 and r.done_tasks_count == 0 and r.idle_days_since_approved >= 2:
        causes.append(f"🛑 OS parada há {r.idle_days_since_approved} dias desde autorização (0% iniciado)")
    if r.running_tasks_count == 0 and r.open_tasks_count > 0 and r.is_delivered is False:
        causes.append("💤 Nenhuma tarefa em andamento (todas abertas paradas/pendentes)")
    if r.total_tasks_count > 0 and r.done_tasks_count < r.total_tasks_count and r.is_delivered is False and r.delivery_days_late > 0:
        causes.append(f"📅 Entrega prevista ultrapassada em {r.delivery_days_late} dia(s) e ainda não concluída")
    if not causes:
        causes.append("✅ Sem indícios óbvios no sistema (sugestão: verificar conversa / particularidades do serviço)")
    return causes


def compute_performance_report(
    budgets: Iterable,
    now: Optional[datetime] = None,
) -> PerformanceReport:
    from .models import WorkOrder  # noqa: avoid circular import top-level

    now = now or timezone.localtime(timezone.now())
    today = now.date()

    budget_ids = [b.pk for b in budgets if getattr(b, "pk", None)]
    work_orders_by_budget: Dict[int, List] = {}
    if budget_ids:
        for wo in (
            WorkOrder.objects.filter(budget_id__in=budget_ids)
            .prefetch_related("tasks")
            .select_related("budget")
        ):
            work_orders_by_budget.setdefault(wo.budget_id, []).append(wo)

    on_time: List[BudgetPerformanceRow] = []
    late_1d: List[BudgetPerformanceRow] = []
    late_2d: List[BudgetPerformanceRow] = []
    late_3d: List[BudgetPerformanceRow] = []
    late_4d: List[BudgetPerformanceRow] = []
    late_5d: List[BudgetPerformanceRow] = []
    late_plus_5d: List[BudgetPerformanceRow] = []
    open_with_expected: List[BudgetPerformanceRow] = []
    no_promise_date: List[BudgetPerformanceRow] = []
    rows_by_id: Dict[int, BudgetPerformanceRow] = {}
    total = 0
    total_with_approved = 0
    sum_days_late = 0
    count_late = 0
    sum_overrun = 0.0
    sum_overrun_late_only = 0.0
    count_overrun_late_only = 0
    sum_idle_before_start = 0
    count_idle_before_start = 0

    for b in budgets:
        expected = getattr(b, "expected_delivery_date", None)
        delivered_dt = getattr(b, "delivered_at", None)
        is_delivered = bool(delivered_dt)
        delivered_date = timezone.localtime(delivered_dt).date() if delivered_dt else None
        approved_at = getattr(b, "approved_at", None)
        approved_date = timezone.localtime(approved_at).date() if approved_at else None
        repair_start = getattr(b, "repair_start_date", None)

        has_any_anchor = bool(expected or delivered_dt or approved_at)
        if not has_any_anchor:
            continue

        total += 1
        if approved_date:
            total_with_approved += 1

        days_late = 0
        if expected:
            cmp_date = delivered_date or today
            if cmp_date > expected:
                days_late = (cmp_date - expected).days

        sum_days_late += days_late
        if days_late > 0:
            count_late += 1

        idle_before = 0
        if approved_date and repair_start and repair_start > approved_date:
            idle_before = (repair_start - approved_date).days
        if idle_before > 0:
            sum_idle_before_start += idle_before
            count_idle_before_start += 1

        if repair_start:
            cmp_end = delivered_date or today
            process_days = (cmp_end - repair_start).days
            if process_days < 0:
                process_days = 0
        else:
            process_days = 0

        total_days_since_apv = 0
        if approved_date and not is_delivered:
            total_days_since_apv = (today - approved_date).days
        elif approved_date and is_delivered and delivered_date:
            total_days_since_apv = (delivered_date - approved_date).days

        pending_parts_count = 0
        has_pending_parts = budget_has_pending_shop_parts(b)
        if has_pending_parts:
            pending_parts_count = len(budget_get_pending_shop_parts(b))

        total_planned_hours = 0.0
        total_actual_hours = 0.0
        elapsed_seconds = 0
        task_status_counts = {"SCHEDULED": 0, "RUNNING": 0, "PAUSED": 0, "DONE": 0}
        total_tasks = 0
        for wo in work_orders_by_budget.get(b.pk, []):
            for t in wo.tasks.all():
                total_tasks += 1
                st = getattr(t, "status", None)
                if st in task_status_counts:
                    task_status_counts[st] += 1
                planned = float(getattr(t, "planned_hours", 0) or 0)
                actual = float(getattr(t, "actual_hours", 0) or 0)
                total_planned_hours += planned
                total_actual_hours += actual
                elapsed_seconds += int(getattr(t, "elapsed_seconds", 0) or 0)

        open_tasks = (task_status_counts["SCHEDULED"] + task_status_counts["RUNNING"] + task_status_counts["PAUSED"])
        done_tasks = task_status_counts["DONE"]
        hours_from_elapsed = elapsed_seconds / 3600.0
        effective_actual = max(total_actual_hours, hours_from_elapsed)
        overrun = round(effective_actual - total_planned_hours, 2)
        sum_overrun += max(overrun, 0)
        if days_late > 0 and overrun > 0:
            sum_overrun_late_only += overrun
            count_overrun_late_only += 1

        idle_days = 0
        if approved_at and done_tasks == 0:
            cmp = repair_start or approved_date
            if cmp:
                idle_days = max((today - cmp).days, 0)

        # [NOVO - Dados financeiros + modelo veiculo]
        total_amount_f = float(getattr(b, "total_amount", 0) or 0)
        shop_parts_f = float(getattr(b, "shop_parts_total", 0) or 0)
        services_f = float(getattr(b, "services_total", 0) or 0)
        labor_f = float(getattr(b, "labor_total", 0) or 0)
        discount_f = float(getattr(b, "discount_total", 0) or 0)
        markup_f = float(getattr(b, "markup_total", 0) or 0)
        vehicle_obj = getattr(b, "vehicle", None)
        vehicle_model_f = str(getattr(vehicle_obj, "model", "") or "") if vehicle_obj else ""

        row = BudgetPerformanceRow(
            budget_id=b.pk,
            display_number=str(getattr(b, "display_number", b.pk)),
            customer_name=str(getattr(getattr(b, "customer", None), "name", "-")) if getattr(b, "customer", None) else "-",
            vehicle_plate=str(getattr(vehicle_obj, "plate", "-") or "-") if vehicle_obj else "-",
            vehicle_model=vehicle_model_f,
            approved_at=approved_at,
            approved_date=approved_date,
            repair_start_date=repair_start,
            expected_delivery_date=expected,
            delivered_at=delivered_dt,
            is_delivered=is_delivered,
            delivery_days_late=days_late,
            idle_before_start_days=idle_before,
            process_days_since_start=process_days,
            total_days_since_approved=total_days_since_apv,
            delivery_bucket_label="",
            delivery_bucket_code="",
            total_planned_hours=round(total_planned_hours, 2),
            total_actual_hours=round(effective_actual, 2),
            hours_overrun=overrun,
            has_pending_shop_parts=has_pending_parts,
            allow_repair_without_parts=bool(getattr(b, "allow_repair_without_parts", False)),
            pending_shop_parts_count=pending_parts_count,
            open_tasks_count=open_tasks,
            running_tasks_count=task_status_counts["RUNNING"],
            done_tasks_count=done_tasks,
            total_tasks_count=total_tasks,
            total_task_elapsed_seconds=elapsed_seconds,
            idle_days_since_approved=idle_days,
            probable_causes=[],
            # [NOVO - financeiro]
            total_amount=round(total_amount_f, 2),
            shop_parts_total=round(shop_parts_f, 2),
            services_total=round(services_f, 2),
            labor_total=round(labor_f, 2),
            discount_total=round(discount_f, 2),
            markup_total=round(markup_f, 2),
            late_days=days_late,
        )
        row.probable_causes = _calculate_probable_causes(row, now)

        if not expected and approved_date and not is_delivered:
            row.delivery_bucket_code = "NO_PROMISE"
            row.delivery_bucket_label = "Sem prazo contratado (aprovado)"
            no_promise_date.append(row)
        elif is_delivered or (expected and expected < today):
            if days_late <= 0:
                row.delivery_bucket_code = "ON_TIME"
                row.delivery_bucket_label = "No prazo"
                on_time.append(row)
            elif days_late == 1:
                row.delivery_bucket_code = "LATE_1D"
                row.delivery_bucket_label = "Atrasado 1 dia"
                late_1d.append(row)
            elif days_late == 2:
                row.delivery_bucket_code = "LATE_2D"
                row.delivery_bucket_label = "Atrasado 2 dias"
                late_2d.append(row)
            elif days_late == 3:
                row.delivery_bucket_code = "LATE_3D"
                row.delivery_bucket_label = "Atrasado 3 dias"
                late_3d.append(row)
            elif days_late == 4:
                row.delivery_bucket_code = "LATE_4D"
                row.delivery_bucket_label = "Atrasado 4 dias"
                late_4d.append(row)
            elif days_late == 5:
                row.delivery_bucket_code = "LATE_5D"
                row.delivery_bucket_label = "Atrasado 5 dias"
                late_5d.append(row)
            else:
                row.delivery_bucket_code = "LATE_PLUS_5D"
                row.delivery_bucket_label = f"Atrasado {days_late} dias (+5)"
                late_plus_5d.append(row)
        else:
            row.delivery_bucket_code = "OPEN_WITH_EXPECTED"
            row.delivery_bucket_label = "Em aberto / prev. entrega"
            open_with_expected.append(row)

        rows_by_id[b.pk] = row

    kpi_on_time_pct = round((len(on_time) * 100.0) / total, 1) if total else 0.0
    kpi_late_pct = round((count_late * 100.0) / total, 1) if total else 0.0
    kpi_avg_days_late = round(sum_days_late / count_late, 1) if count_late else 0.0
    kpi_total_overrun_hours = round(sum_overrun, 2)
    kpi_avg_overrun_hours_late_only = round(sum_overrun_late_only / count_overrun_late_only, 2) if count_overrun_late_only else 0.0
    kpi_avg_idle_before_start = round(sum_idle_before_start / count_idle_before_start, 1) if count_idle_before_start else 0.0

    # [NOVO - KPIs FINANCEIROS]
    def _soma_valor(lista: List[BudgetPerformanceRow], campo: str = "total_amount") -> float:
        return round(sum(float(getattr(r, campo, 0) or 0) for r in lista), 2)

    kpi_valor_no_prazo = _soma_valor(on_time)
    kpi_valor_atrasado_1d = _soma_valor(late_1d)
    kpi_valor_atrasado_2d = _soma_valor(late_2d)
    kpi_valor_atrasado_3d = _soma_valor(late_3d)
    kpi_valor_atrasado_4d = _soma_valor(late_4d)
    kpi_valor_atrasado_5d = _soma_valor(late_5d)
    kpi_valor_atrasado_plus_5d = _soma_valor(late_plus_5d)
    kpi_valor_sem_prazo = _soma_valor(no_promise_date)
    kpi_valor_atrasado = round(
        kpi_valor_atrasado_1d + kpi_valor_atrasado_2d + kpi_valor_atrasado_3d +
        kpi_valor_atrasado_4d + kpi_valor_atrasado_5d + kpi_valor_atrasado_plus_5d, 2
    )
    # Soma total carteira (todos orcamentos analisados)
    todas_listas = on_time + late_1d + late_2d + late_3d + late_4d + late_5d + late_plus_5d + no_promise_date
    kpi_valor_total_carteira = _soma_valor(todas_listas)
    # Soma componentes dos atrasados
    atrasados_lista = late_1d + late_2d + late_3d + late_4d + late_5d + late_plus_5d
    kpi_mao_obra_total_atrasado = _soma_valor(atrasados_lista, "labor_total")
    kpi_pecas_total_atrasado = _soma_valor(atrasados_lista, "shop_parts_total")
    kpi_servicos_total_atrasado = _soma_valor(atrasados_lista, "services_total")
    # Métrica PONDERADA (R$·dias): quanto mais caro + mais atrasado = MAIOR impacto
    valor_dia_ponderado = 0.0
    for r in atrasados_lista:
        if r.late_days > 0:
            valor_dia_ponderado += float(r.total_amount or 0) * float(r.late_days)
    kpi_valor_dia_atraso_ponderado = round(valor_dia_ponderado, 2)

    return PerformanceReport(
        total=total,
        on_time=on_time,
        late_1d=late_1d,
        late_2d=late_2d,
        late_3d=late_3d,
        late_4d=late_4d,
        late_5d=late_5d,
        late_plus_5d=late_plus_5d,
        open_with_expected=open_with_expected,
        no_promise_date=no_promise_date,
        rows_by_id=rows_by_id,
        kpi_on_time_pct=kpi_on_time_pct,
        kpi_late_pct=kpi_late_pct,
        kpi_avg_days_late=kpi_avg_days_late,
        kpi_total_overrun_hours=kpi_total_overrun_hours,
        kpi_avg_overrun_hours_late_only=kpi_avg_overrun_hours_late_only,
        kpi_avg_idle_before_start_days=kpi_avg_idle_before_start,
        kpi_total_with_approved_date=total_with_approved,
        # [NOVO - KPIs FINANCEIROS]
        kpi_valor_total_carteira=kpi_valor_total_carteira,
        kpi_valor_no_prazo=kpi_valor_no_prazo,
        kpi_valor_atrasado=kpi_valor_atrasado,
        kpi_valor_atrasado_1d=kpi_valor_atrasado_1d,
        kpi_valor_atrasado_2d=kpi_valor_atrasado_2d,
        kpi_valor_atrasado_3d=kpi_valor_atrasado_3d,
        kpi_valor_atrasado_4d=kpi_valor_atrasado_4d,
        kpi_valor_atrasado_5d=kpi_valor_atrasado_5d,
        kpi_valor_atrasado_plus_5d=kpi_valor_atrasado_plus_5d,
        kpi_valor_sem_prazo=kpi_valor_sem_prazo,
        kpi_mao_obra_total_atrasado=kpi_mao_obra_total_atrasado,
        kpi_pecas_total_atrasado=kpi_pecas_total_atrasado,
        kpi_servicos_total_atrasado=kpi_servicos_total_atrasado,
        kpi_valor_dia_atraso_ponderado=kpi_valor_dia_atraso_ponderado,
    )


def performance_report_to_text(rep: PerformanceReport, now: Optional[datetime] = None) -> str:
    now = now or timezone.localtime(timezone.now())
    def fmt_brl(v: float) -> str:
        """Formata R$ em BRL, mesmo com valores pequenos. Sempre 2 casas, separador milhar pt-BR."""
        try:
            fv = float(v or 0.0)
        except Exception:
            fv = 0.0
        # Formata 2 casas decimais, substitui separador para pt-BR
        s = f"{fv:,.2f}"
        return s.replace(",", "_SEP_").replace(".", ",").replace("_SEP_", ".")

    lines: List[str] = []
    lines.append("RELATÓRIO DE DESEMPENHO — OFICINA DE FUNILARIA")
    lines.append("Gerado em: " + now.strftime("%d/%m/%Y %H:%M"))
    lines.append("=" * 80)
    lines.append(f"TOTAL DE ORÇAMENTOS ANALISADOS: {rep.total}")
    lines.append(f"  - Com data de aprovação: {rep.kpi_total_with_approved_date}")
    lines.append(f"  - No prazo: {rep.on_time_count} ({rep.kpi_on_time_pct}%)")
    lines.append(f"  - Atrasados: {rep.late_count} ({rep.kpi_late_pct}%)")
    lines.append(f"  - Média de dias de atraso (apenas atrasados): {rep.kpi_avg_days_late}")
    lines.append(f"  - Média de dias PARADO entre aprovação e início: {rep.kpi_avg_idle_before_start_days}d")
    lines.append(f"  - Estouro TOTAL de horas: {rep.kpi_total_overrun_hours:.2f}h")
    if rep.late_count:
        lines.append(f"  - Média de estouro de horas (apenas atrasados): {rep.kpi_avg_overrun_hours_late_only:.2f}h")
    lines.append("")
    # ========================================================================
    # [NOVO - BLOCO FINANCEIRO, a IA NAO tem mais desculpa para NAO saber o R$]
    # ========================================================================
    lines.append("-" * 80)
    lines.append("RESUMO FINANCEIRO (VALORES R$ REAIS DO SISTEMA, NAO ESTIMADOS):")
    lines.append(f"  * VALOR TOTAL DA CARTEIRA (todos orcamentos): R$ {fmt_brl(rep.kpi_valor_total_carteira)}")
    lines.append(f"  * VALOR TOTAL NO PRAZO:                 R$ {fmt_brl(rep.kpi_valor_no_prazo)}")
    lines.append(f"  * VALOR TOTAL ATRASADO (receita imobilizada/parada): R$ {fmt_brl(rep.kpi_valor_atrasado)}  <- ATENCAO: este e o valor que a oficina DEIXOU DE RECEBER (ou esta esperando receber) por atrasos.")
    lines.append(f"  * VALOR SEM PRAZO DEFINIDO (risco oculto):   R$ {fmt_brl(rep.kpi_valor_sem_prazo)}")
    lines.append(f"  * IMPACTO PONDERADO (R$·dias):               R$ {fmt_brl(rep.kpi_valor_dia_atraso_ponderado)}  <- (valor_orcamento * dias_atrasado) por cada OS atrasada. Quanto MAIOR, PIOR o impacto financeiro total.")
    lines.append("")
    lines.append("COMPOSICAO DOS ATRASADOS (onde esta o dinheiro parado):")
    lines.append(f"  * Mao de Obra (R$ labor_total atrasado):     R$ {fmt_brl(rep.kpi_mao_obra_total_atrasado)}")
    lines.append(f"  * PECAS SHOP (R$ shop_parts_total atrasado): R$ {fmt_brl(rep.kpi_pecas_total_atrasado)}")
    lines.append(f"  * Servicos (R$ services_total atrasado):     R$ {fmt_brl(rep.kpi_servicos_total_atrasado)}")
    lines.append("")
    lines.append("DISTRIBUIÇÃO FINANCEIRA POR FAIXA DE ATRASO (R$ atrelados a cada grupo):")
    lines.append(f"  - No prazo........: {rep.on_time_count} OS | R$ {fmt_brl(rep.kpi_valor_no_prazo)}")
    lines.append(f"  - Atrasado 1 dia...: {len(rep.late_1d)} OS | R$ {fmt_brl(rep.kpi_valor_atrasado_1d)}")
    lines.append(f"  - Atrasado 2 dias..: {len(rep.late_2d)} OS | R$ {fmt_brl(rep.kpi_valor_atrasado_2d)}")
    lines.append(f"  - Atrasado 3 dias..: {len(rep.late_3d)} OS | R$ {fmt_brl(rep.kpi_valor_atrasado_3d)}")
    lines.append(f"  - Atrasado 4 dias..: {len(rep.late_4d)} OS | R$ {fmt_brl(rep.kpi_valor_atrasado_4d)}")
    lines.append(f"  - Atrasado 5 dias..: {len(rep.late_5d)} OS | R$ {fmt_brl(rep.kpi_valor_atrasado_5d)}")
    lines.append(f"  - Atrasado +5 dias.: {len(rep.late_plus_5d)} OS | R$ {fmt_brl(rep.kpi_valor_atrasado_plus_5d)}  <- GRUPO MAIS CRITICO DE TODOS.")
    lines.append(f"  - Em aberto/prev...: {len(rep.open_with_expected)} OS")
    lines.append(f"  - Sem prazo/aprov..: {len(rep.no_promise_date)} OS | R$ {fmt_brl(rep.kpi_valor_sem_prazo)}")
    lines.append("")

    def list_bucket(title: str, rows: List[BudgetPerformanceRow]):
        if not rows:
            return
        lines.append("-" * 80)
        valor_bucket = fmt_brl(sum(float(r.total_amount or 0) for r in rows))
        lines.append(f"[{title.upper()}] — {len(rows)} caso(s) · VALOR TOTAL FAIXA R$ {valor_bucket}:")
        for r in rows:
            modelo_veic = (r.vehicle_model or "").strip()
            placa_e_modelo = r.vehicle_plate + (f" ({modelo_veic})" if modelo_veic else "")
            lines.append(
                f"  OS #{r.display_number} | VALOR REPARO: R$ {fmt_brl(r.total_amount)} | "
                f"{r.customer_name} | {placa_e_modelo} | "
                f"Aprov: {r.approved_date.strftime('%d/%m/%Y') if r.approved_date else '-'} | "
                f"Inicio: {r.repair_start_date.strftime('%d/%m/%Y') if r.repair_start_date else '-'} | "
                f"Prev: {r.expected_delivery_date.strftime('%d/%m/%Y') if r.expected_delivery_date else '-'} | "
                f"{'Entregue: ' + timezone.localtime(r.delivered_at).strftime('%d/%m/%Y %H:%M') if r.delivered_at else 'AINDA NÃO ENTREGUE'} | "
                f"Parado(apv→inicio): {r.idle_before_start_days}d | "
                f"Atraso: {r.delivery_days_late}d | "
                f"Horas: {r.total_actual_hours:.2f}h / {r.total_planned_hours:.2f}h (overrun {r.hours_overrun:+.2f}h) | "
                f"Tarefas: {r.done_tasks_count}/{r.total_tasks_count} | "
                f"Peças pend SHOP: {r.pending_shop_parts_count} (bypass={'SIM' if r.allow_repair_without_parts else 'NÃO'})"
            )
            if r.probable_causes:
                lines.append(f"    -> Causas prováveis:")
                for c in r.probable_causes:
                    lines.append(f"       * {c}")

    list_bucket("Sem prazo contratado (aprovado / análise tempo parado)", rep.no_promise_date)
    list_bucket("No prazo", rep.on_time)
    list_bucket("Atrasado 1 dia", rep.late_1d)
    list_bucket("Atrasado 2 dias", rep.late_2d)
    list_bucket("Atrasado 3 dias", rep.late_3d)
    list_bucket("Atrasado 4 dias", rep.late_4d)
    list_bucket("Atrasado 5 dias", rep.late_5d)
    list_bucket("Atrasado +5 dias", rep.late_plus_5d)
    list_bucket("Em aberto / com entrega prevista", rep.open_with_expected)
    lines.append("=" * 80)
    # [NOVO - ULTIMA LINHA, instrucao para a IA NAO ERRAR:]
    lines.append("INSTRUCAO PARA A IA: Use os valores REAIS em BRL acima (R$) para responder perguntas financeiras. Nao invente valores. NAO converta para USD. Mantenha sempre R$ (REAL BRASILEIRO).")
    return "\n".join(lines)

