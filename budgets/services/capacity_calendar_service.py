import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Sum

from budgets.models import Budget, WorkOrder, WorkOrderTask
from users.models import Collaborator


PT_BR_MONTHS = (
    '',
    'Janeiro',
    'Fevereiro',
    'Marco',
    'Abril',
    'Maio',
    'Junho',
    'Julho',
    'Agosto',
    'Setembro',
    'Outubro',
    'Novembro',
    'Dezembro',
)

PT_BR_WEEKDAYS = ('Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sab', 'Dom')
STANDARD_OPERATIONAL_HOURS_PER_DAY = Decimal('8.00')


def get_daily_workshop_capacity():
    operational_collaborators = Collaborator.objects.filter(
        function=Collaborator.Function.OPERATIONAL
    ).count()
    daily_capacity_hours = (
        Decimal(operational_collaborators) * STANDARD_OPERATIONAL_HOURS_PER_DAY
    ).quantize(Decimal('0.01'))
    return {
        'operational_collaborators': operational_collaborators,
        'daily_capacity_hours': daily_capacity_hours,
    }


def _get_capacity_status(used_hours, available_hours):
    if available_hours <= Decimal('0'):
        return 'over_capacity' if used_hours > Decimal('0') else 'empty'

    occupancy_percent = (used_hours / available_hours) * Decimal('100')
    if occupancy_percent > Decimal('100'):
        return 'over_capacity'
    if occupancy_percent >= Decimal('90'):
        return 'critical'
    if occupancy_percent >= Decimal('71'):
        return 'warning'
    if used_hours > Decimal('0'):
        return 'ok'
    return 'empty'


def _get_occupancy_percent(used_hours, available_hours):
    if available_hours <= Decimal('0'):
        return Decimal('100.00') if used_hours > Decimal('0') else Decimal('0.00')
    return ((used_hours / available_hours) * Decimal('100')).quantize(Decimal('0.01'))


def _get_capacity_tasks_queryset(start_date, end_date):
    return (
        WorkOrderTask.objects.select_related(
            'work_order__budget__customer',
            'work_order__budget__vehicle',
            'collaborator',
        )
        .filter(
            scheduled_date__gte=start_date,
            scheduled_date__lte=end_date,
            planned_hours__gt=Decimal('0'),
            work_order__status=WorkOrder.Status.OPEN,
            work_order__budget__status=Budget.Status.AUTHORIZED,
            work_order__budget__delivered_at__isnull=True,
        )
        .exclude(status=WorkOrderTask.Status.DONE)
        .order_by('scheduled_date', 'order', 'id')
    )


def _get_approved_work_orders_hours():
    approved_tasks_queryset = (
        WorkOrderTask.objects.select_related(
            'work_order__budget__customer',
            'work_order__budget__vehicle',
        )
        .filter(
            planned_hours__gt=Decimal('0'),
            work_order__status=WorkOrder.Status.OPEN,
            work_order__budget__status=Budget.Status.AUTHORIZED,
            work_order__budget__delivered_at__isnull=True,
        )
        .exclude(status=WorkOrderTask.Status.DONE)
    )

    work_order_scheduled_hours = {
        row['work_order_id']: (row['scheduled_hours'] or Decimal('0.00')).quantize(Decimal('0.01'))
        for row in approved_tasks_queryset.filter(scheduled_date__isnull=False).values('work_order_id').annotate(
            scheduled_hours=Sum('planned_hours')
        )
    }

    work_orders = []
    total_hours = Decimal('0.00')
    missing_schedule_count = 0
    high_load_count = 0
    daily_capacity_hours = get_daily_workshop_capacity()['daily_capacity_hours']

    for row in approved_tasks_queryset.values(
        'work_order_id',
        'work_order__budget_id',
        'work_order__budget__cilia_number',
        'work_order__budget__customer__name',
        'work_order__budget__vehicle__plate',
    ).annotate(
        total_planned_hours=Sum('planned_hours'),
        task_count=Count('id'),
    ).order_by('-total_planned_hours', 'work_order__budget__cilia_number', 'work_order_id'):
        work_order_hours = (row['total_planned_hours'] or Decimal('0.00')).quantize(Decimal('0.01'))
        scheduled_hours = work_order_scheduled_hours.get(row['work_order_id'], Decimal('0.00'))
        unscheduled_hours = (work_order_hours - scheduled_hours).quantize(Decimal('0.01'))
        has_missing_schedule = unscheduled_hours > Decimal('0.00')
        is_high_load = daily_capacity_hours > Decimal('0.00') and work_order_hours >= daily_capacity_hours
        total_hours += work_order_hours
        if has_missing_schedule:
            missing_schedule_count += 1
        if is_high_load:
            high_load_count += 1
        budget_number = row['work_order__budget__cilia_number'] or row['work_order__budget_id']
        work_orders.append(
            {
                'work_order_id': row['work_order_id'],
                'budget_id': row['work_order__budget_id'],
                'display_number': str(budget_number),
                'customer_name': row['work_order__budget__customer__name'],
                'vehicle_plate': row['work_order__budget__vehicle__plate'],
                'task_count': row['task_count'],
                'total_planned_hours': work_order_hours,
                'scheduled_hours': scheduled_hours,
                'unscheduled_hours': unscheduled_hours,
                'has_missing_schedule': has_missing_schedule,
                'is_high_load': is_high_load,
            }
        )

    work_order_count = len(work_orders)
    average_hours = Decimal('0.00')
    if work_order_count > 0:
        average_hours = (total_hours / Decimal(work_order_count)).quantize(Decimal('0.01'))

    return {
        'approved_work_orders': work_orders,
        'approved_work_orders_count': work_order_count,
        'approved_total_hours': total_hours.quantize(Decimal('0.01')),
        'approved_average_hours': average_hours,
        'approved_missing_schedule_count': missing_schedule_count,
        'approved_high_load_count': high_load_count,
    }


def _build_approved_hours_projection(total_hours, daily_capacity_hours, start_date):
    projection_days = []
    remaining_hours = total_hours.quantize(Decimal('0.01'))

    if remaining_hours <= Decimal('0.00'):
        return {
            'start_date': start_date,
            'last_date': start_date,
            'days': projection_days,
            'days_count': 0,
            'remaining_hours': Decimal('0.00'),
        }

    if daily_capacity_hours <= Decimal('0.00'):
        return {
            'start_date': start_date,
            'last_date': start_date,
            'days': [
                {
                    'date': start_date,
                    'allocated_hours': remaining_hours,
                    'available_hours': daily_capacity_hours,
                    'remaining_after_day': remaining_hours,
                    'occupancy_percent': Decimal('100.00'),
                    'status': 'over_capacity',
                }
            ],
            'days_count': 1,
            'remaining_hours': remaining_hours,
        }

    current_date = start_date
    while remaining_hours > Decimal('0.00'):
        allocated_hours = min(remaining_hours, daily_capacity_hours).quantize(Decimal('0.01'))
        remaining_hours = (remaining_hours - allocated_hours).quantize(Decimal('0.01'))
        projection_days.append(
            {
                'date': current_date,
                'allocated_hours': allocated_hours,
                'available_hours': daily_capacity_hours,
                'remaining_after_day': remaining_hours,
                'occupancy_percent': _get_occupancy_percent(allocated_hours, daily_capacity_hours),
                'status': _get_capacity_status(allocated_hours, daily_capacity_hours),
            }
        )
        current_date += timedelta(days=1)

    return {
        'start_date': start_date,
        'last_date': projection_days[-1]['date'] if projection_days else start_date,
        'days': projection_days,
        'days_count': len(projection_days),
        'remaining_hours': remaining_hours,
    }


def build_workshop_capacity_month(year, month, selected_date=None):
    calendar_builder = calendar.Calendar(firstweekday=0)
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])

    capacity = get_daily_workshop_capacity()
    daily_capacity_hours = capacity['daily_capacity_hours']

    tasks_queryset = _get_capacity_tasks_queryset(month_start, month_end)
    daily_usage = {
        row['scheduled_date']: (row['used_hours'] or Decimal('0.00')).quantize(Decimal('0.01'))
        for row in tasks_queryset.values('scheduled_date').annotate(
            used_hours=Sum('planned_hours'),
            task_count=Count('id'),
        )
    }
    daily_counts = {
        row['scheduled_date']: row['task_count']
        for row in tasks_queryset.values('scheduled_date').annotate(task_count=Count('id'))
    }

    tasks_by_day = defaultdict(list)
    for task in tasks_queryset:
        tasks_by_day[task.scheduled_date].append(
            {
                'id': task.id,
                'activity': task.get_activity_display(),
                'description': task.description or (task.service.name if task.service else '-'),
                'planned_hours': task.planned_hours,
                'status': task.get_status_display(),
                'collaborator_name': task.collaborator.name if task.collaborator else '-',
                'work_order_id': task.work_order_id,
                'work_order_number': task.work_order.budget.display_number,
                'budget_id': task.work_order.budget_id,
                'customer_name': task.work_order.budget.customer.name,
                'vehicle_plate': task.work_order.budget.vehicle.plate,
            }
        )

    weeks = []
    month_days = 0
    overloaded_days = 0

    for week in calendar_builder.monthdatescalendar(year, month):
        week_days = []
        for day in week:
            in_month = day.month == month
            used_hours = daily_usage.get(day, Decimal('0.00')) if in_month else Decimal('0.00')
            task_count = daily_counts.get(day, 0) if in_month else 0
            occupancy_percent = _get_occupancy_percent(used_hours, daily_capacity_hours)
            status = _get_capacity_status(used_hours, daily_capacity_hours)

            if in_month:
                month_days += 1
                if status in ('critical', 'over_capacity'):
                    overloaded_days += 1

            week_days.append(
                {
                    'date': day,
                    'day_number': day.day,
                    'in_month': in_month,
                    'used_hours': used_hours,
                    'available_hours': daily_capacity_hours,
                    'occupancy_percent': occupancy_percent,
                    'status': status,
                    'task_count': task_count,
                    'is_selected': bool(selected_date and selected_date == day),
                }
            )
        weeks.append(week_days)

    previous_month_year = year
    previous_month = month - 1
    if previous_month == 0:
        previous_month = 12
        previous_month_year -= 1

    next_month_year = year
    next_month = month + 1
    if next_month == 13:
        next_month = 1
        next_month_year += 1

    selected_day = None
    if selected_date and month_start <= selected_date <= month_end:
        selected_tasks = tasks_by_day.get(selected_date, [])
        selected_used_hours = daily_usage.get(selected_date, Decimal('0.00'))
        selected_day = {
            'date': selected_date,
            'used_hours': selected_used_hours,
            'available_hours': daily_capacity_hours,
            'occupancy_percent': _get_occupancy_percent(selected_used_hours, daily_capacity_hours),
            'status': _get_capacity_status(selected_used_hours, daily_capacity_hours),
            'task_count': len(selected_tasks),
            'tasks': selected_tasks,
        }

    approved_hours_summary = _get_approved_work_orders_hours()
    approved_projection = _build_approved_hours_projection(
        total_hours=approved_hours_summary['approved_total_hours'],
        daily_capacity_hours=daily_capacity_hours,
        start_date=date.today(),
    )

    return {
        'year': year,
        'month': month,
        'month_label': f'{PT_BR_MONTHS[month]} {year}',
        'weekdays': PT_BR_WEEKDAYS,
        'weeks': weeks,
        'month_days': month_days,
        'operational_collaborators': capacity['operational_collaborators'],
        'daily_capacity_hours': daily_capacity_hours,
        'overloaded_days': overloaded_days,
        'previous_month': previous_month,
        'previous_month_year': previous_month_year,
        'next_month': next_month,
        'next_month_year': next_month_year,
        'selected_day': selected_day,
        'approved_work_orders': approved_hours_summary['approved_work_orders'],
        'approved_work_orders_count': approved_hours_summary['approved_work_orders_count'],
        'approved_total_hours': approved_hours_summary['approved_total_hours'],
        'approved_average_hours': approved_hours_summary['approved_average_hours'],
        'approved_missing_schedule_count': approved_hours_summary['approved_missing_schedule_count'],
        'approved_high_load_count': approved_hours_summary['approved_high_load_count'],
        'approved_projection': approved_projection,
    }
