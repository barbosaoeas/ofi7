from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Exists, OuterRef
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, UpdateView
from calendar import monthrange
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re
from uuid import uuid4
from xml.etree import ElementTree
from django.db.models import Q

from customers.models import Customer, Vehicle
from core.views import RoleRequiredMixin
from users.models import Collaborator, CustomUser

from .cilia_parser import extract_service_lines, extract_tag_names, parse_cilia_xml
from .forms import AdministrativeClosureForm, BankAccountForm, CiliaXMLUploadForm, FinanceXMLUploadForm, PieceForm, ServiceCatalogForm, SupplierForm, ThirdPartyServiceForm
from .models import BankAccount, Budget, BudgetPhoto, CashCategory, CashMovement, CommissionLine, Piece, ServiceCatalog, Supplier, ThirdPartyService, WorkOrder, WorkOrderTask, XMLImportJob
from .services.cilia_import_service import CiliaImportDuplicateError, CiliaImportError, CiliaImportValidationError, import_cilia_xml_bytes


KANBAN_CUTOFF_TIME = dt_time(17, 48)
WORK_ORDER_ACTIVITY_SEQUENCE = [
    WorkOrderTask.Activity.DISMANTLING,
    WorkOrderTask.Activity.BODYWORK,
    WorkOrderTask.Activity.PREPARATION,
    WorkOrderTask.Activity.PAINTING,
    WorkOrderTask.Activity.ASSEMBLY,
    WorkOrderTask.Activity.POLISHING,
    WorkOrderTask.Activity.DELIVERY_PREP,
]


def get_activity_predecessors(activity):
    try:
        index = WORK_ORDER_ACTIVITY_SEQUENCE.index(activity)
    except ValueError:
        return []
    return WORK_ORDER_ACTIVITY_SEQUENCE[:index]


def get_task_dependency_key(task):
    if task is None:
        return ''

    description = (getattr(task, 'description', '') or '').strip()
    if not description:
        return ''

    code_match = re.search(r'\((?:[^)]*?)(\d{4,})(?:[^)]*?)\)', description)
    if code_match:
        return f'code:{code_match.group(1)}'

    normalized = _normalize_lookup_key(description)
    prefixes = (
        'desmontagem-',
        'funilaria-',
        'preparacao-',
        'preparacao-para-entrega-',
        'prep-entrega-',
        'pintura-',
        'montagem-',
        'polimento-',
    )
    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    return normalized


def get_task_sequence_blockers(task):
    predecessors = get_activity_predecessors(task.activity)
    if not predecessors:
        return []

    dependency_key = get_task_dependency_key(task)
    predecessor_qs = WorkOrderTask.objects.filter(
        work_order_id=task.work_order_id,
        activity__in=predecessors,
    )
    if dependency_key:
        matching_ids = [
            candidate.id
            for candidate in predecessor_qs.only('id', 'description')
            if get_task_dependency_key(candidate) == dependency_key
        ]
        predecessor_qs = predecessor_qs.filter(id__in=matching_ids)

    if not dependency_key:
        predecessor_qs = WorkOrderTask.objects.filter(
            work_order_id=task.work_order_id,
            activity__in=predecessors,
        )

    pending_activities = list(
        predecessor_qs
        .exclude(status=WorkOrderTask.Status.DONE)
        .values_list('activity', flat=True)
        .distinct()
    )
    label_map = dict(WorkOrderTask.Activity.choices)
    ordered_pending = [activity for activity in predecessors if activity in pending_activities]
    return [label_map.get(activity, activity) for activity in ordered_pending]


def get_task_sequence_block_message(task):
    blockers = get_task_sequence_blockers(task)
    if not blockers:
        return ''
    if len(blockers) == 1:
        return f'Conclua primeiro {blockers[0]}.'
    return 'Conclua primeiro: ' + ', '.join(blockers) + '.'


def budget_has_pending_shop_parts(budget):
    if not budget or not getattr(budget, 'id', None):
        return False
    return Piece.objects.filter(
        budget_id=budget.id,
        provider_type=Piece.ProviderType.SHOP,
        arrived=False,
        arrival_date__isnull=True,
    ).exists()


def task_has_blocking_pending_shop_parts(task):
    if task is None:
        return False

    budget = getattr(getattr(task, 'work_order', None), 'budget', None)
    if not budget or not getattr(budget, 'id', None):
        return False

    pending_parts = list(
        Piece.objects.filter(
            budget_id=budget.id,
            provider_type=Piece.ProviderType.SHOP,
            arrived=False,
            arrival_date__isnull=True,
        ).only('name')
    )
    if not pending_parts:
        return False

    task_key = get_task_dependency_key(task)
    if not task_key:
        return True

    for part in pending_parts:
        part_key = _normalize_lookup_key(getattr(part, 'name', '') or '')
        if not part_key:
            continue
        if task_key == part_key or part_key in task_key or task_key in part_key:
            return True
    return False


def _normalize_text(value):
    return (value or '').strip()


def _normalize_lookup_key(value):
    return slugify(_normalize_text(value))


def _parse_xml_text(element, tag_name):
    child = element.find(tag_name)
    if child is None:
        return ''
    return ''.join(child.itertext()).strip()


def _parse_xml_int(element, tag_name):
    raw = _parse_xml_text(element, tag_name)
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _parse_xml_date(element, tag_name):
    raw = _parse_xml_text(element, tag_name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except Exception:
        return None


def _parse_xml_bool(element, tag_name, default=False):
    raw = _parse_xml_text(element, tag_name).lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'sim')


def _parse_xml_decimal(element, tag_name):
    raw = _parse_xml_text(element, tag_name)
    if not raw:
        return None
    raw = raw.replace('R$', '').strip().replace(' ', '')
    if ',' in raw and '.' in raw:
        raw = raw.replace('.', '').replace(',', '.')
    elif ',' in raw:
        raw = raw.replace(',', '.')
    try:
        return Decimal(raw).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def recalculate_budget_total(budget):
    base_total = Decimal('0')
    xml = budget.source_xml or ''
    if xml:
        try:
            _, _, _, parsed_total_amount, _, _, _ = parse_cilia_xml(xml.encode('utf-8', errors='replace'))
            base_total = parsed_total_amount
        except Exception:
            base_total = budget.total_amount
    else:
        base_total = budget.total_amount

    budget.total_amount = base_total + get_budget_extra_third_party_total(budget)
    budget.save(update_fields=['total_amount'])


def third_party_identity(description, amount):
    normalized_description = (description or '').strip().lower()
    normalized_amount = (amount or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return normalized_description, normalized_amount


def normalize_service_description(description):
    return (description or '').strip().lower()


def is_office_managed_service(description):
    desc = normalize_service_description(description)
    office_keywords = (
        'lavagem',
        'lavacao',
        'lavação',
        'polimento',
    )
    return any(keyword in desc for keyword in office_keywords)


def third_party_service_is_shop(service):
    if service is None:
        return False
    return bool(getattr(service, 'is_shop_service', False) or is_office_managed_service(getattr(service, 'description', '')))


def service_description_is_shop(description, explicit_shop=False):
    return bool(explicit_shop or is_office_managed_service(description))


def office_managed_service_activity(description, explicit_shop=False):
    desc = normalize_service_description(description)
    if 'polimento' in desc:
        return WorkOrderTask.Activity.POLISHING
    if 'lavagem' in desc or 'lavacao' in desc or 'lavação' in desc:
        return WorkOrderTask.Activity.DELIVERY_PREP
    if service_description_is_shop(description, explicit_shop):
        if 'martelinho' in desc:
            return WorkOrderTask.Activity.BODYWORK
        return WorkOrderTask.Activity.BODYWORK
    return None


def map_third_party_status_to_task_status(status):
    if status == ThirdPartyService.Status.DONE:
        return WorkOrderTask.Status.DONE
    if status == ThirdPartyService.Status.IN_PROGRESS:
        return WorkOrderTask.Status.RUNNING
    return WorkOrderTask.Status.SCHEDULED


def get_budget_service_lines(budget):
    xml = (getattr(budget, 'source_xml', None) or '').strip()
    if not xml:
        return []
    try:
        return extract_service_lines(xml.encode('utf-8', errors='replace'))
    except Exception:
        return []


def get_budget_xml_manual_services_map(budget):
    manual_services = {}
    for line in get_budget_service_lines(budget):
        manual_amount = line.get('manual_amount', Decimal('0')) or Decimal('0')
        description = line.get('description') or ''
        if manual_amount <= 0 or not description:
            continue
        manual_services[normalize_service_description(description)] = line
    return manual_services


def get_budget_extra_third_party_total(budget):
    xml_manual_services = get_budget_xml_manual_services_map(budget)
    total = Decimal('0')
    for service in budget.third_party_services.all().only('description', 'amount'):
        if normalize_service_description(service.description) in xml_manual_services:
            continue
        total += service.amount or Decimal('0')
    return total


def get_visible_third_party_services(budget):
    xml_manual_services = get_budget_xml_manual_services_map(budget)
    services = list(
        budget.third_party_services.select_related('supplier').all()
    )
    visible = []
    grouped = {}
    for service in services:
        description_key = normalize_service_description(service.description)
        grouped.setdefault(description_key, []).append(service)

    for description_key, items in grouped.items():
        expected_line = xml_manual_services.get(description_key)
        if expected_line is None:
            visible.extend(items)
            continue
        expected_amount = (expected_line.get('total_amount') or Decimal('0')).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP,
        )
        exact_matches = [
            item for item in items
            if (item.amount or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) == expected_amount
        ]
        selected_pool = exact_matches or items
        visible.append(
            sorted(
                selected_pool,
                key=lambda item: (
                    1 if item.status == ThirdPartyService.Status.DONE else 0,
                    1 if item.supplier_id else 0,
                    item.id,
                ),
            )[-1]
        )

    visible = sorted(
        visible,
        key=lambda item: (
            item.status,
            item.scheduled_date or date.max,
            item.id,
        ),
    )
    for item in visible:
        item.effective_is_shop_service = third_party_service_is_shop(item)
    return visible


def get_external_visible_third_party_services(budget):
    return [service for service in get_visible_third_party_services(budget) if not third_party_service_is_shop(service)]


def sync_office_managed_service_tasks(work_order):
    budget = getattr(work_order, 'budget', None)
    if budget is None:
        return

    service_lines = get_budget_service_lines(budget)

    current_order = work_order.tasks.order_by('-order').values_list('order', flat=True).first() or 0
    third_party_by_description = {}
    for service in budget.third_party_services.all():
        description_key = normalize_service_description(service.description)
        current = third_party_by_description.get(description_key)
        if third_party_service_is_shop(service) and (current is None or service.id > current.id):
            third_party_by_description[description_key] = service

    for line in service_lines:
        description = (line.get('description') or '').strip()
        manual_amount = line.get('manual_amount', Decimal('0')) or Decimal('0')
        linked_service = third_party_by_description.get(normalize_service_description(description))
        explicit_shop = linked_service is not None and bool(getattr(linked_service, 'is_shop_service', False))
        activity = office_managed_service_activity(description, explicit_shop=explicit_shop)
        if not description or manual_amount <= 0 or activity is None:
            continue

        matching_task = work_order.tasks.filter(
            activity=activity,
            description__iexact=description,
        ).first()
        placeholder_task = None
        if matching_task is None:
            placeholder_task = work_order.tasks.filter(
                activity=activity,
                description='',
            ).first()

        linked_service = third_party_by_description.get(normalize_service_description(description))
        task = matching_task or placeholder_task
        update_fields = []
        if task is None:
            current_order += 10
            task = WorkOrderTask.objects.create(
                work_order=work_order,
                activity=activity,
                description=description,
                planned_amount=manual_amount,
                order=current_order,
                scheduled_date=getattr(linked_service, 'scheduled_date', None) if linked_service is not None else None,
                status=map_third_party_status_to_task_status(getattr(linked_service, 'status', None)) if linked_service is not None else WorkOrderTask.Status.SCHEDULED,
                completed_at=getattr(linked_service, 'completed_at', None) if linked_service is not None else None,
            )
            continue

        if task.description != description:
            task.description = description
            update_fields.append('description')
        if task.planned_amount != manual_amount:
            task.planned_amount = manual_amount
            update_fields.append('planned_amount')
        if linked_service is not None:
            if task.scheduled_date != linked_service.scheduled_date:
                task.scheduled_date = linked_service.scheduled_date
                update_fields.append('scheduled_date')
            mapped_status = map_third_party_status_to_task_status(linked_service.status)
            if task.status == WorkOrderTask.Status.SCHEDULED and mapped_status != task.status:
                task.status = mapped_status
                update_fields.append('status')
            if linked_service.status == ThirdPartyService.Status.DONE and task.completed_at != linked_service.completed_at:
                task.completed_at = linked_service.completed_at
                update_fields.append('completed_at')
        if update_fields:
            task.save(update_fields=sorted(set(update_fields)))

    for service in budget.third_party_services.all():
        if not third_party_service_is_shop(service):
            continue
        description = (service.description or '').strip()
        if not description:
            continue
        description_key = normalize_service_description(description)
        if description_key in {
            normalize_service_description(line.get('description'))
            for line in service_lines
            if line.get('description')
        }:
            continue
        activity = office_managed_service_activity(description, explicit_shop=True)
        if activity is None:
            continue
        task = work_order.tasks.filter(activity=activity, description__iexact=description).first()
        if task is None:
            current_order += 10
            WorkOrderTask.objects.create(
                work_order=work_order,
                activity=activity,
                description=description,
                planned_amount=service.amount or Decimal('0'),
                order=current_order,
                scheduled_date=service.scheduled_date,
                status=map_third_party_status_to_task_status(service.status),
                completed_at=service.completed_at,
            )


def sync_xml_third_party_services(budget):
    xml = (getattr(budget, 'source_xml', None) or '').strip()
    if not xml:
        return 0

    try:
        lines = extract_service_lines(xml.encode('utf-8', errors='replace'))
    except Exception:
        return 0

    third_party_lines = [line for line in lines if line.get('is_third_party')]
    if not third_party_lines:
        return 0

    existing_keys = {
        third_party_identity(service.description, service.amount)
        for service in ThirdPartyService.objects.filter(budget_id=budget.id).only('description', 'amount')
    }

    created = 0
    for line in third_party_lines:
        description = (line.get('description') or '').strip()
        amount = line.get('total_amount', Decimal('0')) or Decimal('0')
        if not description or amount <= 0:
            continue
        identity = third_party_identity(description, amount)
        if identity in existing_keys:
            continue
        ThirdPartyService.objects.create(
            budget=budget,
            description=description,
            amount=amount,
            status=ThirdPartyService.Status.SCHEDULED,
            is_shop_service=is_office_managed_service(description),
        )
        existing_keys.add(identity)
        created += 1

    if created:
        recalculate_budget_total(budget)
    return created


def pending_budget_finance_session_key(budget_id):
    return f'pending_budget_finance_{budget_id}'


def serialize_pending_budget_data(cleaned_data):
    payload = {}
    for field in (
        'status',
        'refusal_reason_code',
        'refusal_reason',
        'entry_date',
        'repair_start_date',
        'expected_delivery_date',
        'allow_repair_without_parts',
    ):
        value = cleaned_data.get(field)
        if isinstance(value, date):
            payload[field] = value.isoformat()
        elif isinstance(value, bool):
            payload[field] = value
        else:
            payload[field] = value or ''
    return payload


def deserialize_pending_budget_data(payload):
    data = dict(payload or {})
    for field in ('entry_date', 'repair_start_date', 'expected_delivery_date'):
        raw = (data.get(field) or '').strip()
        if not raw:
            data[field] = None
            continue
        try:
            data[field] = date.fromisoformat(raw)
        except ValueError:
            data[field] = None
    data['allow_repair_without_parts'] = bool(data.get('allow_repair_without_parts'))
    data['status'] = data.get('status') or ''
    data['refusal_reason_code'] = data.get('refusal_reason_code') or ''
    data['refusal_reason'] = data.get('refusal_reason') or ''
    return data


def ensure_work_order_for_budget(budget):
    if budget.status != Budget.Status.AUTHORIZED or WorkOrder.objects.filter(budget=budget).exists():
        return

    xml = budget.source_xml or ''
    vehicle_image_url = ''
    if getattr(budget.vehicle, 'image_file', None):
        try:
            if budget.vehicle.image_file:
                vehicle_image_url = budget.vehicle.image_file.url
        except Exception:
            vehicle_image_url = ''
    if not vehicle_image_url:
        vehicle_image_url = budget.vehicle.image_url or ''

    work_order = WorkOrder.objects.create(
        budget=budget,
        vehicle_image_url=vehicle_image_url,
        created_at=budget.created_at,
    )
    if xml:
        try:
            lines = extract_service_lines(xml.encode('utf-8', errors='replace'))
        except Exception:
            lines = []
    else:
        lines = []

    services = list(ServiceCatalog.objects.all().only('id', 'name'))
    services = [s for s in services if (s.name or '').strip()]
    services.sort(key=lambda s: len((s.name or '').strip()), reverse=True)

    def match_service(description):
        d = (description or '').strip().lower()
        if not d:
            return None
        for s in services:
            n = (s.name or '').strip().lower()
            if n and n in d:
                return s
        return None

    order = 0
    activity_specs = [
        (WorkOrderTask.Activity.DISMANTLING, 'desmontagem_hours', 'desmontagem_amount'),
        (WorkOrderTask.Activity.BODYWORK, 'funilaria_hours', 'funilaria_amount'),
        (WorkOrderTask.Activity.PREPARATION, 'preparacao_hours', 'preparacao_amount'),
        (WorkOrderTask.Activity.PAINTING, 'pintura_hours', 'pintura_amount'),
        (WorkOrderTask.Activity.ASSEMBLY, 'montagem_hours', 'montagem_amount'),
    ]

    for activity, hours_key, amount_key in activity_specs:
        for s in [x for x in lines if not x.get('is_third_party')]:
            hours = s.get(hours_key, Decimal('0'))
            amount = s.get(amount_key, Decimal('0'))
            if hours and hours > 0:
                order += 10
                code = s.get('code') or ''
                desc = s.get('description') or ''
                task_desc = desc
                if code:
                    task_desc = f'{desc} (Cód: {code})'
                matched_service = match_service(task_desc)
                WorkOrderTask.objects.create(
                    work_order=work_order,
                    activity=activity,
                    service=matched_service,
                    description=task_desc,
                    planned_hours=hours,
                    planned_amount=amount,
                    order=order,
                )

    order += 10
    WorkOrderTask.objects.create(work_order=work_order, activity=WorkOrderTask.Activity.POLISHING, order=order)
    order += 10
    WorkOrderTask.objects.create(work_order=work_order, activity=WorkOrderTask.Activity.DELIVERY_PREP, order=order)
    sync_office_managed_service_tasks(work_order)


def budget_delivery_kind(budget):
    movement_sources = set(
        CashMovement.objects.filter(budget=budget).values_list('source', flat=True)
    )
    if movement_sources.intersection({CashMovement.Source.INSURERS, CashMovement.Source.INSURER}):
        return 'SEGURADORA'
    return 'PARTICULAR'


def budget_delivery_allows_future_insurer_receivables(open_in_movements, reference_date=None):
    if not open_in_movements:
        return False

    today = reference_date or timezone.localdate()
    insurer_sources = {CashMovement.Source.INSURERS, CashMovement.Source.INSURER}
    return all(
        movement.source in insurer_sources
        and movement.due_date is not None
        and movement.due_date >= today
        for movement in open_in_movements
    )


def get_budget_work_order(budget):
    try:
        return budget.work_order
    except WorkOrder.DoesNotExist:
        return None


def budget_delivery_status(budget):
    work_order = get_budget_work_order(budget)

    task_total = 0
    task_done = 0
    if work_order is not None:
        task_total = work_order.tasks.count()
        task_done = work_order.tasks.filter(status=WorkOrderTask.Status.DONE).count()

    visible_third_party_services = get_external_visible_third_party_services(budget)
    third_total = len(visible_third_party_services)
    third_done = len([service for service in visible_third_party_services if service.status == ThirdPartyService.Status.DONE])

    open_in_movements = list(
        CashMovement.objects.filter(
            budget=budget,
            direction=CashMovement.Direction.IN,
            is_realized=False,
        ).order_by('due_date', 'created_at', 'id')
    )
    open_amount = sum([movement.amount for movement in open_in_movements], Decimal('0'))
    allows_future_insurer_receivables = budget_delivery_allows_future_insurer_receivables(open_in_movements)
    realized_amount = sum(
        [
            movement.amount
            for movement in CashMovement.objects.filter(
                budget=budget,
                direction=CashMovement.Direction.IN,
                is_realized=True,
            ).only('amount')
        ],
        Decimal('0'),
    )

    blockers = []
    if budget.status != Budget.Status.AUTHORIZED:
        blockers.append('O orçamento precisa estar Autorizado.')
    if budget.is_delivered:
        blockers.append('O veículo já foi entregue.')
    if work_order is None:
        blockers.append('A OS ainda não foi criada.')
    if task_total == 0:
        blockers.append('Não há tarefas cadastradas na OS.')
    elif task_done < task_total:
        blockers.append('Existem tarefas internas pendentes.')
    if third_done < third_total:
        blockers.append('Existem serviços de terceiros pendentes.')
    if not CashMovement.objects.filter(budget=budget, direction=CashMovement.Direction.IN).exists():
        blockers.append('O financeiro do orçamento ainda não foi registrado.')
    elif open_amount > Decimal('0') and not allows_future_insurer_receivables:
        blockers.append('Existem pendências financeiras em aberto.')

    return {
        'kind': budget_delivery_kind(budget),
        'work_order': work_order,
        'task_total': task_total,
        'task_done': task_done,
        'task_pending': max(task_total - task_done, 0),
        'third_total': third_total,
        'third_done': third_done,
        'third_pending': max(third_total - third_done, 0),
        'finance_open_movements': open_in_movements,
        'finance_open_amount': open_amount,
        'finance_realized_amount': realized_amount,
        'allows_future_insurer_receivables': allows_future_insurer_receivables,
        'finance_note': (
            'Recebimento da seguradora previsto para depois da entrega.'
            if open_amount > Decimal('0') and allows_future_insurer_receivables
            else ''
        ),
        'can_deliver': len(blockers) == 0,
        'blockers': blockers,
    }


def budget_administrative_closure_status(budget, user=None):
    work_order = get_budget_work_order(budget)
    user_is_manager = bool(
        user and (
            getattr(user, 'is_superuser', False)
            or getattr(user, 'role', None) == CustomUser.Role.MANAGER
        )
    )
    has_started_tasks = False
    if work_order is not None:
        has_started_tasks = work_order.tasks.filter(
            status__in=(
                WorkOrderTask.Status.RUNNING,
                WorkOrderTask.Status.PAUSED,
                WorkOrderTask.Status.DONE,
            )
        ).exists()
    open_in_movements = list(
        CashMovement.objects.filter(
            budget=budget,
            direction=CashMovement.Direction.IN,
            is_realized=False,
        ).order_by('due_date', 'created_at', 'id')
    )
    open_amount = sum([movement.amount for movement in open_in_movements], Decimal('0'))
    allows_future_insurer_receivables = budget_delivery_allows_future_insurer_receivables(open_in_movements)

    blockers = []
    if not user_is_manager:
        blockers.append('A finalização administrativa é exclusiva para gerente.')
    if budget.status != Budget.Status.AUTHORIZED:
        blockers.append('O orçamento precisa estar Autorizado.')
    if budget.is_delivered:
        blockers.append('O veículo já foi entregue.')
    if getattr(budget, 'administrative_closure', False):
        blockers.append('Este orçamento já foi marcado para finalização administrativa.')
    if work_order is None:
        blockers.append('A OS ainda não foi criada.')
    elif has_started_tasks:
        blockers.append('A OS já foi iniciada no operacional.')
    if not CashMovement.objects.filter(budget=budget, direction=CashMovement.Direction.IN).exists():
        blockers.append('O financeiro do orçamento ainda não foi registrado.')
    elif open_amount > Decimal('0') and not allows_future_insurer_receivables:
        blockers.append('Existem pendências financeiras em aberto.')

    return {
        'work_order': work_order,
        'user_is_manager': user_is_manager,
        'has_started_tasks': has_started_tasks,
        'finance_registered': CashMovement.objects.filter(
            budget=budget,
            direction=CashMovement.Direction.IN,
        ).exists(),
        'finance_open_amount': open_amount,
        'allows_future_insurer_receivables': allows_future_insurer_receivables,
        'can_administratively_close': len(blockers) == 0,
        'suggested_delivery_date': budget.expected_delivery_date or timezone.localdate(),
        'blockers': blockers,
    }


def get_third_party_expense_category():
    category, _ = CashCategory.objects.get_or_create(
        name='Serviços terceirizados OS',
        defaults={
            'direction': CashMovement.Direction.OUT,
            'group': CashCategory.ExpenseGroup.OPERATIONAL,
            'is_active': True,
        },
    )
    changed = False
    if category.direction != CashMovement.Direction.OUT:
        category.direction = CashMovement.Direction.OUT
        changed = True
    if category.group != CashCategory.ExpenseGroup.OPERATIONAL:
        category.group = CashCategory.ExpenseGroup.OPERATIONAL
        changed = True
    if not category.is_active:
        category.is_active = True
        changed = True
    if changed:
        category.save(update_fields=['direction', 'group', 'is_active'])
    return category


def sync_third_party_expense(service):
    if service is None:
        return None

    movement = service.expense_movement
    should_have_expense = (
        not third_party_service_is_shop(service)
        and service.status == ThirdPartyService.Status.DONE
        and (service.amount or Decimal('0')) > 0
    )

    if not should_have_expense:
        if movement is not None and not movement.is_realized:
            movement.delete()
        if service.expense_movement_id is not None:
            service.expense_movement = None
            service.save(update_fields=['expense_movement'])
        return None

    category = get_third_party_expense_category()
    due_date = service.scheduled_date or timezone.localdate()
    description = f'Orçamento #{service.budget.display_number} - Terceiro: {service.description}'

    if movement is None:
        movement = CashMovement.objects.create(
            budget=service.budget,
            supplier=service.supplier,
            category=category,
            direction=CashMovement.Direction.OUT,
            source=CashMovement.Source.COMPANY,
            description=description,
            amount=service.amount,
            launch_date=timezone.localdate(),
            due_date=due_date,
            is_realized=False,
        )
        service.expense_movement = movement
        service.save(update_fields=['expense_movement'])
        return movement

    movement.supplier = service.supplier
    movement.category = category
    movement.description = description
    movement.amount = service.amount
    movement.due_date = due_date
    movement.direction = CashMovement.Direction.OUT
    movement.source = CashMovement.Source.COMPANY
    movement.save(
        update_fields=[
            'supplier',
            'category',
            'description',
            'amount',
            'due_date',
            'direction',
            'source',
        ]
    )
    return movement


def after_third_party_service_saved(service):
    sync_third_party_expense(service)
    recalculate_budget_total(service.budget)
    try:
        work_order = service.budget.work_order
    except WorkOrder.DoesNotExist:
        work_order = None
    if work_order is not None:
        sync_office_managed_service_tasks(work_order)


def sync_shop_service_from_task(task):
    if task is None or getattr(task, 'work_order_id', None) is None:
        return None

    budget = getattr(getattr(task, 'work_order', None), 'budget', None)
    if budget is None:
        return None

    description = (getattr(task, 'description', '') or '').strip()
    if not description:
        return None

    service = (
        budget.third_party_services
        .filter(description__iexact=description)
        .order_by('-id')
        .first()
    )
    if service is None:
        return None

    if not third_party_service_is_shop(service):
        return None

    update_fields = []
    if service.scheduled_date != task.scheduled_date:
        service.scheduled_date = task.scheduled_date
        update_fields.append('scheduled_date')

    mapped_status = service.status
    if task.status == WorkOrderTask.Status.DONE:
        mapped_status = ThirdPartyService.Status.DONE
    elif task.status == WorkOrderTask.Status.RUNNING:
        mapped_status = ThirdPartyService.Status.IN_PROGRESS
    elif task.status in (WorkOrderTask.Status.SCHEDULED, WorkOrderTask.Status.PAUSED):
        mapped_status = ThirdPartyService.Status.SCHEDULED

    if service.status != mapped_status:
        service.status = mapped_status
        update_fields.append('status')

    completed_at = task.completed_at if task.status == WorkOrderTask.Status.DONE else None
    if service.completed_at != completed_at:
        service.completed_at = completed_at
        update_fields.append('completed_at')

    if not getattr(service, 'is_shop_service', False):
        service.is_shop_service = True
        update_fields.append('is_shop_service')

    if update_fields:
        service.save(update_fields=sorted(set(update_fields)))
    return service


def annotate_service_lines_completion(budget, service_lines):
    try:
        work_order = budget.work_order
    except WorkOrder.DoesNotExist:
        return service_lines

    tasks = list(work_order.tasks.only('activity', 'description', 'status'))
    activity_specs = [
        ('desmontagem_hours', WorkOrderTask.Activity.DISMANTLING, 'Desmontagem'),
        ('funilaria_hours', WorkOrderTask.Activity.BODYWORK, 'Funilaria'),
        ('preparacao_hours', WorkOrderTask.Activity.PREPARATION, 'Preparação'),
        ('pintura_hours', WorkOrderTask.Activity.PAINTING, 'Pintura'),
        ('montagem_hours', WorkOrderTask.Activity.ASSEMBLY, 'Montagem'),
    ]

    for line in service_lines:
        description = (line.get('description') or '').strip().lower()
        code = (line.get('code') or '').strip().lower()
        completion = []

        for hours_key, activity, label in activity_specs:
            hours = line.get(hours_key, Decimal('0')) or Decimal('0')
            if not hours or hours <= 0:
                continue

            matched_task = None
            for task in tasks:
                if task.activity != activity:
                    continue
                task_description = (task.description or '').strip().lower()
                if code and code in task_description and description and description in task_description:
                    matched_task = task
                    break
                if description and description in task_description:
                    matched_task = task
                    break

            completion.append(
                {
                    'label': label,
                    'done': bool(matched_task and matched_task.status == WorkOrderTask.Status.DONE),
                }
            )

        line['completion_items'] = completion
        if not completion:
            manual_activity = office_managed_service_activity(line.get('description'))
            if manual_activity is not None:
                manual_task = None
                description = (line.get('description') or '').strip().lower()
                for task in tasks:
                    if task.activity != manual_activity:
                        continue
                    if description and description in (task.description or '').strip().lower():
                        manual_task = task
                        break
                completion = [
                    {
                        'label': dict(WorkOrderTask.Activity.choices).get(manual_activity, 'Tarefa'),
                        'done': bool(manual_task and manual_task.status == WorkOrderTask.Status.DONE),
                    }
                ]
                line['completion_items'] = completion
        line['is_completed'] = bool(completion) and all(item['done'] for item in completion)
        line['completion_label'] = 'Concluído' if line['is_completed'] else 'Pendente'

    return service_lines


def parse_xml_created_at(xml_bytes):
    try:
        root = ElementTree.fromstring(xml_bytes)
    except Exception:
        return None

    candidates = {
        'data_orcamento',
        'dataorcamento',
        'data_criacao',
        'datacriacao',
        'data_criado',
        'datacriado',
        'data_emissao',
        'dataemissao',
        'dt_orcamento',
        'dtorcamento',
        'dt_criacao',
        'dtcriacao',
    }

    def parse_raw(raw):
        text = (raw or '').strip()
        if not text:
            return None
        text = text.replace('Z', '+00:00')

        try:
            dt = datetime.fromisoformat(text)
            if isinstance(dt, datetime):
                return dt
        except Exception:
            dt = None

        parts = text.split()
        date_part = parts[0] if parts else ''
        time_part = parts[1] if len(parts) > 1 else ''

        if '/' in date_part:
            try:
                d = date.fromisoformat('-'.join(reversed(date_part.split('/'))))
                if time_part:
                    try:
                        hhmmss = time_part.split(':')
                        hh = int(hhmmss[0])
                        mm = int(hhmmss[1]) if len(hhmmss) > 1 else 0
                        ss = int(hhmmss[2]) if len(hhmmss) > 2 else 0
                        return datetime(d.year, d.month, d.day, hh, mm, ss)
                    except Exception:
                        return datetime(d.year, d.month, d.day)
                return datetime(d.year, d.month, d.day)
            except Exception:
                return None
        return None

    def iter_candidates():
        for el in root.iter():
            if el is None or el.tag is None:
                continue
            tag = str(el.tag).split('}')[-1].lower()
            yield tag, el

    for tag, el in iter_candidates():
        if tag not in candidates:
            continue

        raw = ''.join(el.itertext()).strip()
        raw = raw or el.attrib.get('value', '')
        dt = parse_raw(raw)
        if dt is None:
            continue

        tz = timezone.get_current_timezone()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, tz)
        dt_local = timezone.localtime(dt, tz)
        if dt_local.year < 2000 or dt_local.year > (timezone.localdate().year + 1):
            continue
        return dt_local

    for tag, el in iter_candidates():
        if 'data' not in tag and not tag.startswith('dt'):
            continue
        if not ('orc' in tag or 'cria' in tag or 'emiss' in tag):
            continue
        if tag in candidates:
            continue

        raw = ''.join(el.itertext()).strip()
        raw = raw or el.attrib.get('value', '')
        dt = parse_raw(raw)
        if dt is None:
            continue

        tz = timezone.get_current_timezone()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, tz)
        dt_local = timezone.localtime(dt, tz)
        if dt_local.year < 2000 or dt_local.year > (timezone.localdate().year + 1):
            continue
        return dt_local

    for tag, el in iter_candidates():
        if el is None or el.tag is None:
            continue
        if tag in candidates:
            continue
        if tag == 'data':
            continue

    return None


def parse_xml_insurer_name(xml_bytes):
    try:
        root = ElementTree.fromstring(xml_bytes)
    except Exception:
        return ''

    explicit_tags = {
        'seguradora',
        'nome_seguradora',
        'nomeseguradora',
        'seguradora_nome',
        'seguradoranome',
        'companhia_seguros',
        'companhiaseguros',
        'nome_companhia',
        'nomecompanhia',
        'cia_seguros',
        'ciaseguros',
        'associacao',
        'associação',
        'nome_associacao',
        'nomeassociacao',
    }
    partial_tags = ('segur', 'companh', 'cia_', 'associ')

    def normalize_candidate(value):
        text = (value or '').strip().strip('-').strip()
        if not text:
            return ''
        if text.isdigit():
            return ''
        lowered = text.lower()
        if lowered in {'sim', 'nao', 'não', 'true', 'false'}:
            return ''
        return text

    for el in root.iter():
        if el is None or el.tag is None:
            continue
        tag = str(el.tag).split('}')[-1].lower()
        if tag not in explicit_tags and not any(part in tag for part in partial_tags):
            continue
        candidate = normalize_candidate(''.join(el.itertext()).strip() or el.attrib.get('value', ''))
        if candidate:
            return candidate
    return ''


def add_months(base_date, months):
    month_index = (base_date.month - 1) + months
    year = base_date.year + (month_index // 12)
    month = (month_index % 12) + 1
    day = min(base_date.day, monthrange(year, month)[1])
    return date(year, month, day)


def capped_work_delta_seconds(last_started_at, now, allow_overtime):
    if last_started_at is None:
        return 0, None

    last_local = timezone.localtime(last_started_at)
    now_local = timezone.localtime(now)

    if allow_overtime:
        effective_end = now_local
    else:
        tz = timezone.get_current_timezone()
        started_day = last_local.date()
        cutoff_dt = timezone.make_aware(datetime.combine(started_day, KANBAN_CUTOFF_TIME), tz)
        if last_local >= cutoff_dt:
            effective_end = last_local
        elif now_local.date() == started_day:
            effective_end = now_local if now_local <= cutoff_dt else cutoff_dt
        else:
            effective_end = cutoff_dt

    delta = int((effective_end - last_local).total_seconds())
    return max(delta, 0), effective_end


class BudgetListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = Budget
    template_name = 'budgets/budget_list.html'
    context_object_name = 'budgets'
    paginate_by = 25
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def _delivery_filter(self):
        delivery_filter = (self.request.GET.get('delivery') or '').strip().lower()
        if delivery_filter not in ('approved', 'delivered'):
            delivery_filter = 'approved'
        return delivery_filter

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related('customer', 'vehicle')
            .filter(status=Budget.Status.AUTHORIZED)
        )
        if self._delivery_filter() == 'delivered':
            queryset = queryset.filter(delivered_at__isnull=False).order_by('-delivered_at', '-approved_at', '-created_at')
        else:
            queryset = queryset.filter(delivered_at__isnull=True).order_by('-approved_at', '-created_at')
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        authorized_budgets = Budget.objects.filter(status=Budget.Status.AUTHORIZED)
        context['delivery_filter'] = self._delivery_filter()
        context['approved_count'] = authorized_budgets.filter(delivered_at__isnull=True).count()
        context['delivered_count'] = authorized_budgets.filter(delivered_at__isnull=False).count()
        q = self.request.GET.copy()
        q.pop('page', None)
        context['current_query_without_page'] = q.urlencode()
        return context


class BudgetOpenListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = Budget
    template_name = 'budgets/budget_open_list.html'
    context_object_name = 'budgets'
    paginate_by = 50
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related('customer', 'vehicle')
            .filter(status=Budget.Status.PENDING)
            .order_by('created_at')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        for b in context.get('budgets', []):
            created_date = getattr(getattr(b, 'created_at', None), 'date', lambda: None)()
            if created_date:
                b.days_waiting = max((today - created_date).days, 0)
            else:
                b.days_waiting = 0
        context['today'] = today
        context['total_open_budgets'] = self.get_queryset().count()
        return context


class FinanceDashboardView(LoginRequiredMixin, RoleRequiredMixin, View):
    template_name = 'budgets/finance_dashboard.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def _month_range(self, base_date):
        start = date(base_date.year, base_date.month, 1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
        return start, end

    def _parse_date(self, value, default_date):
        raw = (value or '').strip()
        if not raw:
            return default_date
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return default_date

    def _parse_money(self, value):
        raw = (value or '').strip()
        if not raw:
            return Decimal('0')
        raw = raw.replace('R$', '').strip()
        raw = raw.replace(' ', '')
        if ',' in raw and '.' in raw:
            raw = raw.replace('.', '').replace(',', '.')
        elif ',' in raw:
            raw = raw.replace(',', '.')
        return Decimal(raw)

    def _parse_positive_int(self, value, default=1, minimum=1, maximum=60):
        raw = (value or '').strip()
        try:
            parsed = int(raw)
        except Exception:
            parsed = default
        if parsed < minimum:
            parsed = minimum
        if parsed > maximum:
            parsed = maximum
        return parsed

    def _source_aliases(self):
        return {
            CashMovement.Source.CUSTOMER: CashMovement.Source.PARTICULAR,
            CashMovement.Source.INSURER: CashMovement.Source.INSURERS,
            CashMovement.Source.OTHER: CashMovement.Source.COMPANY,
        }

    def _allowed_sources_by_direction(self):
        return {
            CashMovement.Direction.IN: {
                CashMovement.Source.PARTICULAR,
                CashMovement.Source.INSURERS,
                CashMovement.Source.COMPANY,
                CashMovement.Source.PARTS_SALE,
                CashMovement.Source.LOANS,
            },
            CashMovement.Direction.OUT: {
                CashMovement.Source.COMPANY,
                CashMovement.Source.LOANS,
            },
        }

    def _source_options(self):
        return [
            {
                'value': CashMovement.Source.PARTICULAR,
                'label': 'Particular',
                'directions': 'IN',
            },
            {
                'value': CashMovement.Source.INSURERS,
                'label': 'Seguradoras',
                'directions': 'IN',
            },
            {
                'value': CashMovement.Source.COMPANY,
                'label': 'Empresa',
                'directions': 'IN,OUT',
            },
            {
                'value': CashMovement.Source.PARTS_SALE,
                'label': 'Venda de pecas',
                'directions': 'IN',
            },
            {
                'value': CashMovement.Source.LOANS,
                'label': 'Emprestimos',
                'directions': 'IN,OUT',
            },
        ]

    def _normalize_source(self, source, direction=''):
        source = self._source_aliases().get(source, source)
        all_sources = {item['value'] for item in self._source_options()}
        if direction:
            valid_sources = self._allowed_sources_by_direction().get(direction, set())
        else:
            valid_sources = all_sources
        return source if source in valid_sources else ''

    def _source_filter_values(self, source):
        if source == CashMovement.Source.PARTICULAR:
            return [CashMovement.Source.PARTICULAR, CashMovement.Source.CUSTOMER]
        if source == CashMovement.Source.INSURERS:
            return [CashMovement.Source.INSURERS, CashMovement.Source.INSURER]
        if source == CashMovement.Source.COMPANY:
            return [CashMovement.Source.COMPANY, CashMovement.Source.OTHER]
        return [source] if source else []

    def _default_source_for_direction(self, direction):
        if direction == CashMovement.Direction.IN:
            return CashMovement.Source.PARTICULAR
        return CashMovement.Source.COMPANY

    def _category_display(self, category):
        if not category:
            return ''
        parts = [category.get_direction_display()]
        if category.direction == CashMovement.Direction.OUT and category.group:
            parts.append(category.get_group_display())
        parts.append(category.name)
        return ' · '.join([part for part in parts if part])

    def _origin_options(self, categories):
        items = []
        for category in categories:
            items.append(
                {
                    'value': category.id,
                    'label': self._category_display(category),
                    'direction': category.direction,
                }
            )
        return items

    def _get_filters(self, request):
        today = timezone.localdate()
        default_start, default_end = self._month_range(today)
        start = self._parse_date(request.GET.get('start'), default_start)
        end = self._parse_date(request.GET.get('end'), default_end)
        if end < start:
            start, end = end, start
        direction = (request.GET.get('direction') or '').strip().upper()
        if direction not in ('IN', 'OUT'):
            direction = ''
        realized = (request.GET.get('realized') or '').strip().lower()
        if realized not in ('0', '1'):
            realized = ''
        try:
            origin_category_id = int((request.GET.get('source') or '').strip() or 0) or None
        except ValueError:
            origin_category_id = None
        return {
            'today': today,
            'start': start,
            'end': end,
            'direction': direction,
            'realized': realized,
            'origin_category_id': origin_category_id,
        }

    def _build_context(self, request, f):
        qs = CashMovement.objects.select_related(
            'budget',
            'customer',
            'category',
            'bank_account',
            'supplier',
            'budget__customer',
            'budget__vehicle',
        ).all()
        qs = qs.filter(due_date__gte=f['start'], due_date__lte=f['end'])
        if f['direction']:
            qs = qs.filter(direction=f['direction'])
        if f['realized'] == '1':
            qs = qs.filter(is_realized=True)
        if f['realized'] == '0':
            qs = qs.filter(is_realized=False)
        if f['origin_category_id']:
            qs = qs.filter(category_id=f['origin_category_id'])
        qs = qs.order_by('due_date', 'id')

        movements = list(qs)
        expected_in = sum([m.amount for m in movements if m.direction == CashMovement.Direction.IN], Decimal('0'))
        expected_out = sum([m.amount for m in movements if m.direction == CashMovement.Direction.OUT], Decimal('0'))
        realized_in = sum(
            [m.amount for m in movements if m.direction == CashMovement.Direction.IN and m.is_realized],
            Decimal('0'),
        )
        realized_out = sum(
            [m.amount for m in movements if m.direction == CashMovement.Direction.OUT and m.is_realized],
            Decimal('0'),
        )
        open_in = sum(
            [m.amount for m in movements if m.direction == CashMovement.Direction.IN and not m.is_realized],
            Decimal('0'),
        )
        open_out = sum(
            [m.amount for m in movements if m.direction == CashMovement.Direction.OUT and not m.is_realized],
            Decimal('0'),
        )
        overdue_open = sum(
            [m.amount for m in movements if not m.is_realized and m.due_date and m.due_date < f['today']],
            Decimal('0'),
        )

        categories = list(CashCategory.objects.order_by('direction', 'group', 'name'))
        origin_options = self._origin_options(categories)
        bank_accounts = list(BankAccount.objects.filter(is_active=True).order_by('bank_name', 'account_name'))
        suppliers = list(Supplier.objects.filter(is_active=True).order_by('name'))
        customers = list(Customer.objects.order_by('name'))
        edit_id_raw = (request.GET.get('edit') or '').strip()
        try:
            edit_id = int(edit_id_raw) if edit_id_raw else None
        except ValueError:
            edit_id = None
        edit_movement = None
        if edit_id:
            edit_movement = (
                CashMovement.objects.select_related('budget', 'customer', 'category', 'bank_account', 'supplier', 'budget__customer', 'budget__vehicle')
                .filter(id=edit_id)
                .first()
            )

        current_query = request.get_full_path()
        q = request.GET.copy()
        if 'edit' in q:
            q.pop('edit', None)
        qs_no_edit = q.urlencode()
        current_query_no_edit = request.path
        if qs_no_edit:
            current_query_no_edit = f'{request.path}?{qs_no_edit}'

        context = {
            'movements': movements,
            'categories': categories,
            'origin_options': origin_options,
            'bank_accounts': bank_accounts,
            'suppliers': suppliers,
            'customers': customers,
            'expense_groups': list(CashCategory.ExpenseGroup.choices),
            'filters': f,
            'edit_movement': edit_movement,
            'current_query': current_query,
            'current_query_no_edit': current_query_no_edit,
            'expected_in': expected_in,
            'expected_out': expected_out,
            'expected_net': expected_in - expected_out,
            'realized_in': realized_in,
            'realized_out': realized_out,
            'realized_net': realized_in - realized_out,
            'open_in': open_in,
            'open_out': open_out,
            'overdue_open': overdue_open,
            'movement_count': len(movements),
        }
        return context

    def get(self, request):
        f = self._get_filters(request)
        return render(request, self.template_name, self._build_context(request, f))

    def post(self, request):
        action = (request.POST.get('action') or '').strip()
        next_url = (request.POST.get('next') or '').strip()
        if not next_url.startswith('/'):
            next_url = ''

        if action == 'create_category':
            name = (request.POST.get('name') or '').strip()
            direction = (request.POST.get('direction') or '').strip().upper()
            if direction not in (CashMovement.Direction.IN, CashMovement.Direction.OUT):
                direction = CashMovement.Direction.OUT
            group = (request.POST.get('group') or '').strip().upper()
            if direction == CashMovement.Direction.OUT:
                allowed_groups = {c[0] for c in CashCategory.ExpenseGroup.choices}
                if group not in allowed_groups:
                    group = ''
            else:
                group = ''
            if not name:
                messages.error(request, 'Informe o nome do tipo.')
            else:
                try:
                    CashCategory.objects.create(name=name, direction=direction, group=group)
                    messages.success(request, 'Tipo cadastrado.')
                except Exception:
                    messages.error(request, 'Não foi possível cadastrar o tipo (nome duplicado?).')
            return redirect(next_url or 'budgets:finance_dashboard')

        if action == 'delete_category':
            raw_id = (request.POST.get('category_id') or '').strip()
            try:
                category_id = int(raw_id)
            except ValueError:
                category_id = None
            if not category_id:
                messages.error(request, 'Tipo inválido.')
                return redirect(next_url or 'budgets:finance_dashboard')
            CashCategory.objects.filter(id=category_id).delete()
            messages.success(request, 'Tipo removido.')
            return redirect(next_url or 'budgets:finance_dashboard')

        if action in ('create_movement', 'update_movement'):
            movement = None
            if action == 'update_movement':
                raw_id = (request.POST.get('movement_id') or '').strip()
                try:
                    movement_id = int(raw_id)
                except ValueError:
                    movement_id = None
                if not movement_id:
                    messages.error(request, 'Lançamento inválido.')
                    return redirect(next_url or 'budgets:finance_dashboard')
                movement = CashMovement.objects.filter(id=movement_id).first()
                if movement is None:
                    messages.error(request, 'Lançamento não encontrado.')
                    return redirect(next_url or 'budgets:finance_dashboard')

            direction = (request.POST.get('direction') or '').strip().upper()
            if direction not in (CashMovement.Direction.IN, CashMovement.Direction.OUT):
                direction = CashMovement.Direction.OUT
            source = (request.POST.get('source') or '').strip().upper()
            if not source:
                source = self._default_source_for_direction(direction)
            source = self._normalize_source(source, direction)
            if not source:
                messages.error(request, 'Selecione uma origem compatível com a direção do lançamento.')
                return redirect(next_url or 'budgets:finance_dashboard')
            description = (request.POST.get('description') or '').strip()
            launch_date = self._parse_date(request.POST.get('launch_date'), timezone.localdate())
            due_date = self._parse_date(request.POST.get('due_date'), timezone.localdate())
            is_realized = (request.POST.get('is_realized') or '').strip().lower() in ('1', 'true', 'on', 'yes')
            customer_id_raw = (request.POST.get('customer_id') or '').strip()
            bank_account_id_raw = (request.POST.get('bank_account_id') or '').strip()
            supplier_id_raw = (request.POST.get('supplier_id') or '').strip()
            category_id_raw = (request.POST.get('category_id') or '').strip()
            try:
                customer_id = int(customer_id_raw) if customer_id_raw else None
            except ValueError:
                customer_id = None
            try:
                bank_account_id = int(bank_account_id_raw) if bank_account_id_raw else None
            except ValueError:
                bank_account_id = None
            try:
                supplier_id = int(supplier_id_raw) if supplier_id_raw else None
            except ValueError:
                supplier_id = None
            try:
                category_id = int(category_id_raw) if category_id_raw else None
            except ValueError:
                category_id = None

            customer = None
            if customer_id:
                customer = Customer.objects.filter(id=customer_id).first()
                if customer is None:
                    messages.error(request, 'Cliente inválido.')
                    return redirect(next_url or 'budgets:finance_dashboard')
            bank_account = None
            if not bank_account_id:
                messages.error(request, 'Selecione o banco/conta do lançamento.')
                return redirect(next_url or 'budgets:finance_dashboard')
            bank_account = BankAccount.objects.filter(id=bank_account_id, is_active=True).first()
            if bank_account is None:
                messages.error(request, 'Banco/conta inválido.')
                return redirect(next_url or 'budgets:finance_dashboard')

            supplier = None
            if supplier_id:
                supplier = Supplier.objects.filter(id=supplier_id).first()
                if supplier is None:
                    messages.error(request, 'Fornecedor inválido.')
                    return redirect(next_url or 'budgets:finance_dashboard')

            if direction == CashMovement.Direction.IN:
                supplier = None
                if customer is None:
                    messages.error(request, 'Selecione o cliente para a entrada manual.')
                    return redirect(next_url or 'budgets:finance_dashboard')
            else:
                customer = None

            if category_id:
                category = CashCategory.objects.filter(id=category_id).first()
                if category is None:
                    messages.error(request, 'Tipo inválido.')
                    return redirect(next_url or 'budgets:finance_dashboard')
                if (category.direction or '').upper() != direction:
                    messages.error(request, 'Selecione um tipo compatível com Entrada/Saída.')
                    return redirect(next_url or 'budgets:finance_dashboard')

            try:
                amount = self._parse_money(request.POST.get('amount'))
            except Exception:
                amount = None

            if amount is None or amount <= 0:
                messages.error(request, 'Informe um valor válido.')
                return redirect(next_url or 'budgets:finance_dashboard')

            recurrence_total = self._parse_positive_int(request.POST.get('recurrence_total'), default=1)
            split_entry = (request.POST.get('split_entry') or '').strip().lower() in ('1', 'true', 'on', 'yes')
            entry_amount = Decimal('0')
            if split_entry:
                try:
                    entry_amount = self._parse_money(request.POST.get('entry_amount'))
                except Exception:
                    entry_amount = None
                if direction != CashMovement.Direction.IN:
                    messages.error(request, 'Entrada com saldo futuro está disponível apenas para lançamentos de entrada.')
                    return redirect(next_url or 'budgets:finance_dashboard')
                if recurrence_total > 1:
                    messages.error(request, 'Use recorrência ou entrada com saldo futuro. Os dois juntos não podem ser lançados agora.')
                    return redirect(next_url or 'budgets:finance_dashboard')
                if entry_amount is None or entry_amount <= 0 or entry_amount >= amount:
                    messages.error(request, 'A entrada precisa ser maior que zero e menor que o valor total.')
                    return redirect(next_url or 'budgets:finance_dashboard')
                balance_due_date = self._parse_date(request.POST.get('balance_due_date'), add_months(due_date, 1))

            if movement is None:
                if split_entry:
                    balance_amount = (amount - entry_amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    CashMovement.objects.create(
                        direction=direction,
                        source=source,
                        customer=customer,
                        bank_account=bank_account,
                        supplier=supplier,
                        category_id=category_id,
                        description=(description or 'Entrada manual').strip(),
                        amount=entry_amount,
                        launch_date=launch_date,
                        due_date=due_date,
                        is_realized=is_realized,
                        realized_at=timezone.now() if is_realized else None,
                    )
                    CashMovement.objects.create(
                        direction=direction,
                        source=source,
                        customer=customer,
                        bank_account=bank_account,
                        supplier=supplier,
                        category_id=category_id,
                        description=f'{(description or "Entrada manual").strip()} - Saldo',
                        amount=balance_amount,
                        launch_date=launch_date,
                        due_date=balance_due_date,
                        is_realized=False,
                        realized_at=None,
                    )
                    messages.success(request, 'Entrada e saldo lançados com sucesso.')
                    return redirect(next_url or 'budgets:finance_dashboard')

                recurrence_group = uuid4().hex if recurrence_total > 1 else ''
                for idx in range(recurrence_total):
                    CashMovement.objects.create(
                        direction=direction,
                        source=source,
                        customer=customer,
                        bank_account=bank_account,
                        supplier=supplier,
                        category_id=category_id,
                        description=description,
                        amount=amount,
                        launch_date=launch_date,
                        due_date=add_months(due_date, idx),
                        is_realized=is_realized if idx == 0 else False,
                        realized_at=timezone.now() if is_realized and idx == 0 else None,
                        recurrence_group=recurrence_group,
                        recurrence_index=idx + 1,
                        recurrence_total=recurrence_total,
                    )
                if recurrence_total > 1:
                    messages.success(request, f'Lançamento recorrente criado com {recurrence_total} meses.')
                else:
                    messages.success(request, 'Lançamento criado.')
                return redirect(next_url or 'budgets:finance_dashboard')

            movement.direction = direction
            movement.source = source
            movement.customer = customer
            movement.bank_account = bank_account
            movement.supplier = supplier
            movement.category_id = category_id
            movement.description = description
            movement.amount = amount
            movement.launch_date = launch_date
            movement.due_date = due_date
            movement.is_realized = is_realized
            movement.realized_at = timezone.now() if is_realized else None
            movement.save(
                update_fields=[
                    'direction',
                    'source',
                    'customer',
                    'bank_account',
                    'supplier',
                    'category',
                    'description',
                    'amount',
                    'launch_date',
                    'due_date',
                    'is_realized',
                    'realized_at',
                ]
            )
            messages.success(request, 'Lançamento atualizado.')
            return redirect(next_url or 'budgets:finance_dashboard')

        if action == 'toggle_movement_realized':
            raw_id = (request.POST.get('movement_id') or '').strip()
            try:
                movement_id = int(raw_id)
            except ValueError:
                movement_id = None
            if not movement_id:
                messages.error(request, 'Lançamento inválido.')
                return redirect(next_url or 'budgets:finance_dashboard')
            movement = CashMovement.objects.filter(id=movement_id).first()
            if movement is None:
                messages.error(request, 'Lançamento não encontrado.')
                return redirect(next_url or 'budgets:finance_dashboard')
            movement.is_realized = not bool(movement.is_realized)
            movement.realized_at = timezone.now() if movement.is_realized else None
            movement.save(update_fields=['is_realized', 'realized_at'])
            messages.success(request, 'Status atualizado.')
            return redirect(next_url or 'budgets:finance_dashboard')

        if action == 'delete_movement':
            raw_id = (request.POST.get('movement_id') or '').strip()
            try:
                movement_id = int(raw_id)
            except ValueError:
                movement_id = None
            if not movement_id:
                messages.error(request, 'Lançamento inválido.')
                return redirect(next_url or 'budgets:finance_dashboard')
            deleted = CashMovement.objects.filter(id=movement_id).delete()[0]
            if deleted:
                messages.success(request, 'Lançamento removido.')
            else:
                messages.error(request, 'Lançamento não encontrado.')
            return redirect(next_url or 'budgets:finance_dashboard')

        messages.error(request, 'Ação inválida.')
        return redirect(next_url or 'budgets:finance_dashboard')


class FinanceXMLTemplateDownloadView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get(self, request):
        bank_accounts = list(BankAccount.objects.filter(is_active=True).order_by('bank_name', 'account_name')[:5])
        categories = list(CashCategory.objects.filter(is_active=True).order_by('direction', 'group', 'name')[:8])
        customers = list(Customer.objects.order_by('name')[:5])
        suppliers = list(Supplier.objects.filter(is_active=True).order_by('name')[:5])

        root = ElementTree.Element('financeiro')
        metadata = ElementTree.SubElement(root, 'metadata')
        ElementTree.SubElement(metadata, 'gerado_em').text = timezone.now().isoformat()
        ElementTree.SubElement(metadata, 'observacao').text = (
            'Use um item por lancamento. Preencha ids ou nomes de referencia existentes na base.'
        )

        referencias = ElementTree.SubElement(root, 'referencias')
        bancos_el = ElementTree.SubElement(referencias, 'contas_bancarias')
        for bank in bank_accounts:
            item = ElementTree.SubElement(bancos_el, 'conta')
            ElementTree.SubElement(item, 'id').text = str(bank.id)
            ElementTree.SubElement(item, 'banco').text = bank.bank_name
            ElementTree.SubElement(item, 'conta_nome').text = bank.account_name

        categorias_el = ElementTree.SubElement(referencias, 'categorias')
        for category in categories:
            item = ElementTree.SubElement(categorias_el, 'categoria')
            ElementTree.SubElement(item, 'id').text = str(category.id)
            ElementTree.SubElement(item, 'nome').text = category.name
            ElementTree.SubElement(item, 'direcao').text = category.direction

        clientes_el = ElementTree.SubElement(referencias, 'clientes')
        for customer in customers:
            item = ElementTree.SubElement(clientes_el, 'cliente')
            ElementTree.SubElement(item, 'id').text = str(customer.id)
            ElementTree.SubElement(item, 'nome').text = customer.name

        fornecedores_el = ElementTree.SubElement(referencias, 'fornecedores')
        for supplier in suppliers:
            item = ElementTree.SubElement(fornecedores_el, 'fornecedor')
            ElementTree.SubElement(item, 'id').text = str(supplier.id)
            ElementTree.SubElement(item, 'nome').text = supplier.name

        movimentos = ElementTree.SubElement(root, 'movimentos')
        movimento = ElementTree.SubElement(movimentos, 'movimento')
        ElementTree.SubElement(movimento, 'descricao').text = 'Exemplo de lancamento'
        ElementTree.SubElement(movimento, 'valor').text = '1500.00'
        ElementTree.SubElement(movimento, 'direcao').text = 'IN'
        ElementTree.SubElement(movimento, 'origem').text = 'PARTICULAR'
        ElementTree.SubElement(movimento, 'data_lancamento').text = timezone.localdate().isoformat()
        ElementTree.SubElement(movimento, 'data_vencimento').text = timezone.localdate().isoformat()
        ElementTree.SubElement(movimento, 'realizado').text = 'false'
        ElementTree.SubElement(movimento, 'conta_bancaria_id').text = str(bank_accounts[0].id) if bank_accounts else ''
        ElementTree.SubElement(movimento, 'categoria_id').text = str(categories[0].id) if categories else ''
        ElementTree.SubElement(movimento, 'cliente_id').text = str(customers[0].id) if customers else ''
        ElementTree.SubElement(movimento, 'fornecedor_id').text = ''
        ElementTree.SubElement(movimento, 'orcamento_id').text = ''

        response = HttpResponse(
            ElementTree.tostring(root, encoding='utf-8', xml_declaration=True),
            content_type='application/xml; charset=utf-8',
        )
        response['Content-Disposition'] = 'attachment; filename="modelo-financeiro.xml"'
        return response


class FinanceXMLImportView(LoginRequiredMixin, RoleRequiredMixin, FormView):
    template_name = 'budgets/finance_import_xml.html'
    form_class = FinanceXMLUploadForm
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def _source_aliases(self):
        return {
            CashMovement.Source.CUSTOMER: CashMovement.Source.PARTICULAR,
            CashMovement.Source.INSURER: CashMovement.Source.INSURERS,
            CashMovement.Source.OTHER: CashMovement.Source.COMPANY,
        }

    def _allowed_sources_by_direction(self):
        return {
            CashMovement.Direction.IN: {
                CashMovement.Source.PARTICULAR,
                CashMovement.Source.INSURERS,
                CashMovement.Source.COMPANY,
                CashMovement.Source.PARTS_SALE,
                CashMovement.Source.LOANS,
            },
            CashMovement.Direction.OUT: {
                CashMovement.Source.COMPANY,
                CashMovement.Source.LOANS,
            },
        }

    def _normalize_source(self, source, direction=''):
        source = self._source_aliases().get(source, source)
        if direction:
            valid_sources = self._allowed_sources_by_direction().get(direction, set())
        else:
            valid_sources = {
                CashMovement.Source.PARTICULAR,
                CashMovement.Source.INSURERS,
                CashMovement.Source.COMPANY,
                CashMovement.Source.PARTS_SALE,
                CashMovement.Source.LOANS,
            }
        return source if source in valid_sources else ''

    def _default_source_for_direction(self, direction):
        if direction == CashMovement.Direction.IN:
            return CashMovement.Source.PARTICULAR
        return CashMovement.Source.COMPANY

    def _lookup_maps(self):
        return {
            'bank_by_id': {obj.id: obj for obj in BankAccount.objects.filter(is_active=True)},
            'bank_by_name': {
                _normalize_lookup_key(f'{obj.bank_name} {obj.account_name}'): obj
                for obj in BankAccount.objects.filter(is_active=True)
            },
            'category_by_id': {obj.id: obj for obj in CashCategory.objects.filter(is_active=True)},
            'category_by_name': {_normalize_lookup_key(obj.name): obj for obj in CashCategory.objects.filter(is_active=True)},
            'customer_by_id': {obj.id: obj for obj in Customer.objects.all()},
            'customer_by_name': {_normalize_lookup_key(obj.name): obj for obj in Customer.objects.all()},
            'supplier_by_id': {obj.id: obj for obj in Supplier.objects.filter(is_active=True)},
            'supplier_by_name': {_normalize_lookup_key(obj.name): obj for obj in Supplier.objects.filter(is_active=True)},
            'budget_by_id': {obj.id: obj for obj in Budget.objects.all()},
        }

    def _resolve_related(self, element, maps, index):
        bank = None
        bank_id = _parse_xml_int(element, 'conta_bancaria_id')
        if bank_id is not None:
            bank = maps['bank_by_id'].get(bank_id)
        if bank is None:
            bank_name = _parse_xml_text(element, 'conta_bancaria_nome')
            if bank_name:
                bank = maps['bank_by_name'].get(_normalize_lookup_key(bank_name))
        if bank is None:
            raise forms.ValidationError(f'Movimento {index}: conta bancária não encontrada.')

        category = None
        category_id = _parse_xml_int(element, 'categoria_id')
        if category_id is not None:
            category = maps['category_by_id'].get(category_id)
        if category is None:
            category_name = _parse_xml_text(element, 'categoria_nome')
            if category_name:
                category = maps['category_by_name'].get(_normalize_lookup_key(category_name))

        customer = None
        customer_id = _parse_xml_int(element, 'cliente_id')
        if customer_id is not None:
            customer = maps['customer_by_id'].get(customer_id)
        if customer is None:
            customer_name = _parse_xml_text(element, 'cliente_nome')
            if customer_name:
                customer = maps['customer_by_name'].get(_normalize_lookup_key(customer_name))

        supplier = None
        supplier_id = _parse_xml_int(element, 'fornecedor_id')
        if supplier_id is not None:
            supplier = maps['supplier_by_id'].get(supplier_id)
        if supplier is None:
            supplier_name = _parse_xml_text(element, 'fornecedor_nome')
            if supplier_name:
                supplier = maps['supplier_by_name'].get(_normalize_lookup_key(supplier_name))

        budget = None
        budget_id = _parse_xml_int(element, 'orcamento_id')
        if budget_id is not None:
            budget = maps['budget_by_id'].get(budget_id)

        return bank, category, customer, supplier, budget

    def form_valid(self, form):
        xml_bytes = form.cleaned_data['xml_file'].read()
        try:
            root = ElementTree.fromstring(xml_bytes)
        except ElementTree.ParseError:
            form.add_error('xml_file', 'Não foi possível ler o XML financeiro.')
            return self.form_invalid(form)

        if str(root.tag).split('}')[-1].lower() != 'financeiro':
            form.add_error('xml_file', 'XML fora do padrão do financeiro.')
            return self.form_invalid(form)

        movement_nodes = root.findall('./movimentos/movimento')
        if not movement_nodes:
            form.add_error('xml_file', 'Nenhum movimento encontrado para importar.')
            return self.form_invalid(form)

        maps = self._lookup_maps()
        rows = []
        try:
            for index, node in enumerate(movement_nodes, start=1):
                description = _normalize_text(_parse_xml_text(node, 'descricao'))
                if not description:
                    raise forms.ValidationError(f'Movimento {index}: descrição é obrigatória.')

                amount = _parse_xml_decimal(node, 'valor')
                if amount is None or amount <= 0:
                    raise forms.ValidationError(f'Movimento {index}: valor inválido.')

                direction = _normalize_text(_parse_xml_text(node, 'direcao')).upper()
                if direction not in (CashMovement.Direction.IN, CashMovement.Direction.OUT):
                    raise forms.ValidationError(f'Movimento {index}: direção deve ser IN ou OUT.')

                source = _normalize_text(_parse_xml_text(node, 'origem')).upper() or self._default_source_for_direction(direction)
                source = self._normalize_source(source, direction)
                if not source:
                    raise forms.ValidationError(f'Movimento {index}: origem inválida para a direção informada.')

                launch_date = _parse_xml_date(node, 'data_lancamento') or timezone.localdate()
                due_date = _parse_xml_date(node, 'data_vencimento') or launch_date
                is_realized = _parse_xml_bool(node, 'realizado', default=False)

                bank, category, customer, supplier, budget = self._resolve_related(node, maps, index)

                if category and (category.direction or '').upper() != direction:
                    raise forms.ValidationError(f'Movimento {index}: categoria incompatível com a direção.')

                if direction == CashMovement.Direction.IN:
                    supplier = None
                    if customer is None:
                        raise forms.ValidationError(f'Movimento {index}: cliente é obrigatório para entrada.')
                else:
                    customer = None

                rows.append(
                    {
                        'description': description,
                        'amount': amount,
                        'direction': direction,
                        'source': source,
                        'launch_date': launch_date,
                        'due_date': due_date,
                        'is_realized': is_realized,
                        'realized_at': timezone.now() if is_realized else None,
                        'bank_account': bank,
                        'category': category,
                        'customer': customer,
                        'supplier': supplier,
                        'budget': budget,
                    }
                )
        except forms.ValidationError as exc:
            form.add_error('xml_file', exc.message)
            return self.form_invalid(form)

        with transaction.atomic():
            for row in rows:
                CashMovement.objects.create(**row)

        messages.success(request=self.request, message=f'{len(rows)} lançamento(s) importado(s) no financeiro.')
        return redirect('budgets:finance_dashboard')


class FinanceInsightsView(FinanceDashboardView):
    template_name = 'budgets/finance_insights.html'

    def get(self, request):
        today = timezone.localdate()
        start_month = date(today.year, today.month, 1)
        end_month = add_months(start_month, 1) - timedelta(days=1)

        range_key = (request.GET.get('range') or '').strip().lower()
        if range_key not in ('month', '3m', '12m'):
            range_key = 'month'
        direction = (request.GET.get('direction') or '').strip().upper()
        if direction not in (CashMovement.Direction.IN, CashMovement.Direction.OUT):
            direction = ''
        source = (request.GET.get('source') or '').strip().upper()
        source = self._normalize_source(source, direction)
        months_total = 1
        if range_key == '3m':
            months_total = 3
        if range_key == '12m':
            months_total = 12

        month_starts = [add_months(start_month, offset) for offset in range(-(months_total - 1), 1)]
        range_start = month_starts[0]
        range_end = end_month

        movements = list(
            CashMovement.objects.select_related('category')
            .filter(due_date__gte=range_start, due_date__lte=range_end)
            .order_by('due_date', 'id')
        )
        if direction:
            movements = [m for m in movements if m.direction == direction]
        if source:
            allowed_sources = set(self._source_filter_values(source))
            movements = [m for m in movements if m.source in allowed_sources]

        month_labels = [m.strftime('%b/%Y') for m in month_starts]
        expected_in_series = []
        expected_out_series = []
        realized_in_series = []
        realized_out_series = []

        for month_start in month_starts:
            month_end = add_months(month_start, 1) - timedelta(days=1)
            month_items = [m for m in movements if m.due_date and month_start <= m.due_date <= month_end]
            expected_in_series.append(float(sum([m.amount for m in month_items if m.direction == CashMovement.Direction.IN], Decimal('0'))))
            expected_out_series.append(float(sum([m.amount for m in month_items if m.direction == CashMovement.Direction.OUT], Decimal('0'))))
            realized_in_series.append(
                float(
                    sum(
                        [m.amount for m in month_items if m.direction == CashMovement.Direction.IN and m.is_realized],
                        Decimal('0'),
                    )
                )
            )
            realized_out_series.append(
                float(
                    sum(
                        [m.amount for m in month_items if m.direction == CashMovement.Direction.OUT and m.is_realized],
                        Decimal('0'),
                    )
                )
            )

        category_totals = {}
        for movement in movements:
            if movement.direction != CashMovement.Direction.OUT:
                continue
            label = movement.category.name if movement.category else 'Sem tipo'
            category_totals[label] = category_totals.get(label, Decimal('0')) + (movement.amount or Decimal('0'))
        top_categories = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)[:8]

        source_labels_map = {
            CashMovement.Source.PARTICULAR: 'Particular',
            CashMovement.Source.INSURERS: 'Seguradoras',
            CashMovement.Source.COMPANY: 'Empresa',
            CashMovement.Source.PARTS_SALE: 'Venda de pecas',
            CashMovement.Source.LOANS: 'Emprestimos',
        }
        profile_totals = {label: Decimal('0') for label in source_labels_map.values()}
        for movement in movements:
            if movement.direction != CashMovement.Direction.IN:
                continue
            normalized_source = self._normalize_source((movement.source or '').upper())
            label = source_labels_map.get(normalized_source)
            if not label:
                continue
            profile_totals[label] += movement.amount or Decimal('0')
        profile_comparison = [(label, value) for label, value in profile_totals.items() if value > 0]

        open_amount = sum([m.amount for m in movements if not m.is_realized], Decimal('0'))
        realized_amount = sum([m.amount for m in movements if m.is_realized], Decimal('0'))
        receivable_open = sum(
            [m.amount for m in movements if m.direction == CashMovement.Direction.IN and not m.is_realized],
            Decimal('0'),
        )
        payable_open = sum(
            [m.amount for m in movements if m.direction == CashMovement.Direction.OUT and not m.is_realized],
            Decimal('0'),
        )
        overdue_total = sum(
            [m.amount for m in movements if not m.is_realized and m.due_date and m.due_date < today],
            Decimal('0'),
        )

        month_movements = [m for m in movements if m.due_date and start_month <= m.due_date <= end_month]
        weekly_labels = []
        weekly_expected_in = []
        weekly_expected_out = []
        weekly_realized_in = []
        weekly_realized_out = []
        for week_index in range(1, 6):
            week_start = start_month + timedelta(days=(week_index - 1) * 7)
            week_end = min(start_month + timedelta(days=(week_index * 7) - 1), end_month)
            if week_start > end_month:
                continue
            weekly_labels.append(f'Semana {week_index}')
            week_items = [m for m in month_movements if week_start <= m.due_date <= week_end]
            weekly_expected_in.append(
                float(sum([m.amount for m in week_items if m.direction == CashMovement.Direction.IN], Decimal('0')))
            )
            weekly_expected_out.append(
                float(sum([m.amount for m in week_items if m.direction == CashMovement.Direction.OUT], Decimal('0')))
            )
            weekly_realized_in.append(
                float(
                    sum(
                        [m.amount for m in week_items if m.direction == CashMovement.Direction.IN and m.is_realized],
                        Decimal('0'),
                    )
                )
            )
            weekly_realized_out.append(
                float(
                    sum(
                        [m.amount for m in week_items if m.direction == CashMovement.Direction.OUT and m.is_realized],
                        Decimal('0'),
                    )
                )
            )

        overdue_qs = (
            CashMovement.objects.select_related('category')
            .filter(is_realized=False, due_date__isnull=False, due_date__lt=today)
            .order_by('due_date', 'id')
        )
        if direction:
            overdue_qs = overdue_qs.filter(direction=direction)
        if source:
            overdue_qs = overdue_qs.filter(source__in=self._source_filter_values(source))
        overdue_category_totals = {}
        for movement in overdue_qs.iterator():
            label = movement.category.name if movement.category else 'Sem tipo'
            overdue_category_totals[label] = overdue_category_totals.get(label, Decimal('0')) + (movement.amount or Decimal('0'))
        overdue_top_categories = sorted(overdue_category_totals.items(), key=lambda item: item[1], reverse=True)[:8]
        overdue_items = list(overdue_qs[:10])

        insurer_ranking_map = {}
        budget_qs = (
            Budget.objects.filter(status=Budget.Status.AUTHORIZED)
            .exclude(source_xml='')
            .only('source_xml', 'approved_at', 'created_at', 'total_amount')
        )
        for budget in budget_qs.iterator():
            budget_date = None
            if budget.approved_at:
                budget_date = timezone.localtime(budget.approved_at).date() if timezone.is_aware(budget.approved_at) else budget.approved_at.date()
            elif budget.created_at:
                budget_date = timezone.localtime(budget.created_at).date() if timezone.is_aware(budget.created_at) else budget.created_at.date()
            if not budget_date or budget_date < range_start or budget_date > range_end:
                continue
            insurer_name = parse_xml_insurer_name((budget.source_xml or '').encode('utf-8', errors='replace'))
            if not insurer_name:
                continue
            data = insurer_ranking_map.setdefault(
                insurer_name,
                {
                    'name': insurer_name,
                    'budget_count': 0,
                    'approved_total': Decimal('0'),
                },
            )
            data['budget_count'] += 1
            data['approved_total'] += budget.total_amount or Decimal('0')
        insurer_ranking = sorted(
            insurer_ranking_map.values(),
            key=lambda item: (item['budget_count'], item['approved_total']),
            reverse=True,
        )[:8]

        context = {
            'today': today,
            'range_start': range_start,
            'range_end': range_end,
            'range_key': range_key,
            'filters': {
                'direction': direction,
                'source': source,
            },
            'source_options': self._source_options(),
            'month_labels': month_labels,
            'expected_in_series': expected_in_series,
            'expected_out_series': expected_out_series,
            'realized_in_series': realized_in_series,
            'realized_out_series': realized_out_series,
            'category_labels': [name for name, _ in top_categories],
            'category_values': [float(value) for _, value in top_categories],
            'profile_labels': [name for name, _ in profile_comparison],
            'profile_values': [float(value) for _, value in profile_comparison],
            'weekly_labels': weekly_labels,
            'weekly_expected_in': weekly_expected_in,
            'weekly_expected_out': weekly_expected_out,
            'weekly_realized_in': weekly_realized_in,
            'weekly_realized_out': weekly_realized_out,
            'overdue_items': overdue_items,
            'overdue_category_labels': [name for name, _ in overdue_top_categories],
            'overdue_category_values': [float(value) for _, value in overdue_top_categories],
            'insurer_labels': [item['name'] for item in insurer_ranking],
            'insurer_budget_counts': [item['budget_count'] for item in insurer_ranking],
            'insurer_amount_values': [float(item['approved_total']) for item in insurer_ranking],
            'insurer_ranking': insurer_ranking,
            'status_labels': ['Em aberto', 'Realizado'],
            'status_values': [float(open_amount), float(realized_amount)],
            'receivable_open': receivable_open,
            'payable_open': payable_open,
            'overdue_total': overdue_total,
            'projected_balance': receivable_open - payable_open,
        }
        return render(request, self.template_name, context)


class VehicleEntryKanbanView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def get(self, request):
        today = timezone.localdate()
        raw = (request.GET.get('date') or '').strip()
        start = today
        if raw:
            try:
                start = date.fromisoformat(raw)
            except ValueError:
                start = today

        end = start + timedelta(days=6)

        base = (
            Budget.objects.select_related('customer', 'vehicle')
            .filter(status=Budget.Status.AUTHORIZED)
            .exclude(repair_start_date__isnull=False, repair_start_date__lte=today)
            .order_by('entry_date', 'created_at')
        )

        overdue = base.filter(entry_date__lt=start)
        range_qs = base.filter(entry_date__gte=start, entry_date__lte=end)
        future = base.filter(entry_date__gt=end)
        no_date = base.filter(entry_date__isnull=True)

        overdue_list = list(overdue)
        range_list = list(range_qs)
        future_list = list(future)
        no_date_list = list(no_date)

        columns = []
        if overdue_list:
            columns.append(
                {
                    'key': 'OVERDUE',
                    'label': 'Atrasado',
                    'budgets': overdue_list,
                }
            )

        for i in range(7):
            day = start + timedelta(days=i)
            columns.append(
                {
                    'key': day.isoformat(),
                    'label': day.strftime('%d/%m/%Y'),
                    'budgets': [b for b in range_list if b.entry_date == day],
                }
            )

        if future_list:
            columns.append(
                {
                    'key': 'FUTURE',
                    'label': 'Futuro',
                    'budgets': future_list,
                }
            )

        if no_date_list:
            columns.append(
                {
                    'key': 'NO_DATE',
                    'label': 'Sem data',
                    'budgets': no_date_list,
                }
            )

        return render(
            request,
            'budgets/vehicle_entry_kanban.html',
            {
                'today': today,
                'selected_date': start,
                'end_date': end,
                'columns': columns,
            },
        )


class WorkOrderListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = WorkOrder
    template_name = 'budgets/workorder_list.html'
    context_object_name = 'work_orders'
    paginate_by = 25
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related('budget', 'budget__customer', 'budget__vehicle')
        )


class WorkOrderKanbanTodayView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = WorkOrderTask
    template_name = 'budgets/kanban_today.html'
    context_object_name = 'tasks'
    allowed_roles = (
        CustomUser.Role.MANAGER,
        CustomUser.Role.FINANCE,
        CustomUser.Role.ESTIMATOR,
        CustomUser.Role.OPERATIONAL,
        CustomUser.Role.VISUAL,
    )

    def _next_workday(self, day):
        next_day = day + timedelta(days=1)
        while next_day.weekday() >= 5:
            next_day = next_day + timedelta(days=1)
        return next_day

    def _auto_pause_end_of_day(self):
        now = timezone.localtime(timezone.now())
        cutoff = KANBAN_CUTOFF_TIME
        today = now.date()
        is_after_cutoff = now.time() >= cutoff
        is_sunday = today.weekday() == 6
        reschedule_today = today if not is_sunday else self._next_workday(today)
        tomorrow = self._next_workday(today)

        running_tasks = (
            WorkOrderTask.objects.select_related('collaborator')
            .filter(status=WorkOrderTask.Status.RUNNING)
            .filter(allow_overtime=False)
            .exclude(last_started_at__isnull=True)
        )

        if not running_tasks.exists():
            return

        for task in running_tasks:
            last = timezone.localtime(task.last_started_at) if task.last_started_at else None
            if last is None:
                continue

            started_day = last.date()
            if started_day == today:
                if not is_after_cutoff:
                    continue
                reschedule_date = tomorrow
            else:
                reschedule_date = reschedule_today

            delta, effective_end = capped_work_delta_seconds(task.last_started_at, now, task.allow_overtime)
            task.elapsed_seconds = int(task.elapsed_seconds or 0) + delta
            task.last_started_at = None
            task.status = WorkOrderTask.Status.PAUSED
            task.scheduled_date = reschedule_date
            task.actual_hours = (Decimal(task.elapsed_seconds) / Decimal('3600')).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP,
            )
            task.save(
                update_fields=[
                    'elapsed_seconds',
                    'last_started_at',
                    'status',
                    'scheduled_date',
                    'actual_hours',
                ]
            )

    def dispatch(self, request, *args, **kwargs):
        self._auto_pause_end_of_day()
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        today = timezone.localdate()
        raw = (self.request.GET.get('date') or '').strip()
        selected = today
        if raw:
            try:
                selected = date.fromisoformat(raw)
            except ValueError:
                selected = today
        is_workday = selected.weekday() < 6
        q = Q(status=WorkOrderTask.Status.RUNNING)
        if is_workday:
            q = q | Q(scheduled_date=selected)
        return (
            super()
            .get_queryset()
            .select_related(
                'work_order',
                'work_order__budget',
                'work_order__budget__vehicle',
                'collaborator',
            )
            .filter(q)
            .filter(Q(work_order__budget__entry_date__isnull=True) | Q(work_order__budget__entry_date__lte=selected))
            .exclude(status=WorkOrderTask.Status.DONE)
            .order_by('activity', 'order', 'id')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        raw = (self.request.GET.get('date') or '').strip()
        selected = today
        if raw:
            try:
                selected = date.fromisoformat(raw)
            except ValueError:
                selected = today
        context['today'] = today
        context['selected_date'] = selected
        is_workday = selected.weekday() < 6
        tasks_by_activity = {}
        now = timezone.now()
        task_items = list(context.get('tasks', []))
        for task in task_items:
            task.is_patio = False
            try:
                planned_seconds = int((task.planned_hours or 0) * Decimal('3600'))
            except Exception:
                planned_seconds = 0
            task.planned_seconds = max(planned_seconds, 0)
            extra = 0
            if task.status == WorkOrderTask.Status.RUNNING and task.last_started_at:
                extra = int((now - task.last_started_at).total_seconds())
            task.display_elapsed_seconds = int(task.elapsed_seconds or 0) + max(extra, 0)
            task.is_overdue = bool(task.planned_seconds and task.display_elapsed_seconds > task.planned_seconds)
            blockers = get_task_sequence_blockers(task)
            task.sequence_blockers = blockers
            task.sequence_block_message = (
                f'Conclua primeiro {blockers[0]}.'
                if len(blockers) == 1
                else ('Conclua primeiro: ' + ', '.join(blockers) + '.') if blockers else ''
            )
            tasks_by_activity.setdefault(task.activity, []).append(task)

        busy_work_order_ids = set(
            WorkOrderTask.objects.filter(status=WorkOrderTask.Status.RUNNING).values_list('work_order_id', flat=True)
        )
        if is_workday:
            busy_work_order_ids |= set(
                WorkOrderTask.objects.filter(status=WorkOrderTask.Status.PAUSED).values_list('work_order_id', flat=True)
            )
            busy_work_order_ids |= set(
                WorkOrderTask.objects.filter(scheduled_date=selected)
                .exclude(status=WorkOrderTask.Status.DONE)
                .values_list('work_order_id', flat=True)
            )

        patio_work_orders = (
            WorkOrder.objects.select_related('budget', 'budget__vehicle', 'budget__customer')
            .filter(budget__status=Budget.Status.AUTHORIZED)
            .filter(budget__delivered_at__isnull=True)
            .exclude(id__in=list(busy_work_order_ids))
            .annotate(
                has_pending_tasks=Exists(
                    WorkOrderTask.objects.filter(
                        work_order_id=OuterRef('id'),
                    ).exclude(status=WorkOrderTask.Status.DONE)
                ),
                has_any_tasks=Exists(
                    WorkOrderTask.objects.filter(
                        work_order_id=OuterRef('id'),
                    )
                ),
                has_late_parts=Exists(
                    Piece.objects.filter(
                        budget_id=OuterRef('budget_id'),
                        arrived=False,
                        expected_arrival_date__isnull=False,
                        expected_arrival_date__lt=today,
                    )
                )
            )
            .filter(Q(has_pending_tasks=True) | Q(has_any_tasks=False))
            .order_by('-created_at')
        )
        patio_cards = []
        for wo in patio_work_orders:
            patio_cards.append(
                {
                    'id': f'patio-{wo.id}',
                    'is_patio': True,
                    'status': WorkOrderTask.Status.SCHEDULED,
                    'work_order_id': wo.id,
                    'work_order': wo,
                    'description': 'Aguardando início',
                    'patio_has_late_parts': bool(getattr(wo, 'has_late_parts', False)),
                    'collaborator': None,
                    'collaborator_id': None,
                    'planned_hours': None,
                    'elapsed_seconds': 0,
                    'last_started_at': None,
                    'planned_seconds': 0,
                    'is_overdue': False,
                    'allow_overtime': False,
                }
            )

        context['columns'] = [
            {'key': 'PATIO', 'label': 'Pátio', 'tasks': patio_cards},
            {
                'key': WorkOrderTask.Activity.DISMANTLING,
                'label': 'Desmontagem',
                'tasks': tasks_by_activity.get(WorkOrderTask.Activity.DISMANTLING, []),
            },
            {
                'key': WorkOrderTask.Activity.BODYWORK,
                'label': 'Funilaria',
                'tasks': tasks_by_activity.get(WorkOrderTask.Activity.BODYWORK, []),
            },
            {
                'key': WorkOrderTask.Activity.PREPARATION,
                'label': 'Preparação',
                'tasks': tasks_by_activity.get(WorkOrderTask.Activity.PREPARATION, []),
            },
            {
                'key': WorkOrderTask.Activity.PAINTING,
                'label': 'Pintura',
                'tasks': tasks_by_activity.get(WorkOrderTask.Activity.PAINTING, []),
            },
            {
                'key': WorkOrderTask.Activity.ASSEMBLY,
                'label': 'Montagem',
                'tasks': tasks_by_activity.get(WorkOrderTask.Activity.ASSEMBLY, []),
            },
            {
                'key': WorkOrderTask.Activity.POLISHING,
                'label': 'Polimento',
                'tasks': tasks_by_activity.get(WorkOrderTask.Activity.POLISHING, []),
            },
            {
                'key': WorkOrderTask.Activity.DELIVERY_PREP,
                'label': 'Prep Entrega',
                'tasks': tasks_by_activity.get(WorkOrderTask.Activity.DELIVERY_PREP, []),
            },
        ]
        return context


class WorkOrderTaskStartView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.OPERATIONAL)

    def post(self, request, pk):
        task = WorkOrderTask.objects.select_related('collaborator', 'work_order', 'work_order__budget').filter(pk=pk).first()
        if task is None:
            raise Http404('Tarefa não encontrada.')

        today = timezone.localdate()
        entry_date = getattr(getattr(task.work_order, 'budget', None), 'entry_date', None)
        if entry_date and today < entry_date:
            messages.error(request, f'Veículo com entrada agendada para {entry_date.strftime("%d/%m/%Y")}. Não é possível iniciar antes da entrada.')
            next_url = (request.POST.get('next') or '').strip()
            if next_url:
                return redirect(next_url)
            return redirect('budgets:kanban_today')
        if task.scheduled_date and task.scheduled_date > today:
            messages.error(request, 'Esta tarefa está programada para uma data futura e não pode ser iniciada ainda.')
            next_url = (request.POST.get('next') or '').strip()
            if next_url:
                return redirect(next_url)
            return redirect('budgets:kanban_today')

        if task.status == WorkOrderTask.Status.DONE:
            messages.error(request, 'Tarefa já concluída.')
            return redirect('budgets:kanban_today')

        if task.collaborator_id is None:
            messages.error(request, 'Selecione um colaborador antes de iniciar.')
            return redirect('budgets:kanban_today')

        sequence_block_message = get_task_sequence_block_message(task)
        if sequence_block_message:
            messages.error(request, sequence_block_message)
            next_url = (request.POST.get('next') or '').strip()
            if next_url:
                return redirect(next_url)
            return redirect('budgets:kanban_today')

        has_running = WorkOrderTask.objects.filter(
            collaborator_id=task.collaborator_id,
            status=WorkOrderTask.Status.RUNNING,
        ).exclude(pk=task.pk).exists()
        if has_running:
            messages.error(request, 'Este colaborador já possui uma tarefa em andamento. Pause a atual antes de iniciar outra.')
            return redirect('budgets:kanban_today')

        now = timezone.now()
        now_local = timezone.localtime(now)
        if now_local.time() >= KANBAN_CUTOFF_TIME and not bool(task.allow_overtime):
            messages.error(request, 'Após 17:48 só é possível iniciar com Extra liberado.')
            next_url = (request.POST.get('next') or '').strip()
            if next_url:
                return redirect(next_url)
            return redirect('budgets:kanban_today')
        update_fields = []

        if task.started_at is None:
            task.started_at = now
            update_fields.append('started_at')

        if task.last_started_at is None:
            task.last_started_at = now
            update_fields.append('last_started_at')

        task.status = WorkOrderTask.Status.RUNNING
        update_fields.append('status')

        if update_fields:
            task.save(update_fields=update_fields)
            sync_shop_service_from_task(task)
        messages.success(request, 'Tarefa iniciada.')
        next_url = (request.POST.get('next') or '').strip()
        if next_url:
            return redirect(next_url)
        return redirect('budgets:kanban_today')


class WorkOrderTaskPauseView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.OPERATIONAL)

    def post(self, request, pk):
        task = WorkOrderTask.objects.select_related('work_order', 'work_order__budget').filter(pk=pk).first()
        if task is None:
            raise Http404('Tarefa não encontrada.')

        today = timezone.localdate()
        entry_date = getattr(getattr(task.work_order, 'budget', None), 'entry_date', None)
        if entry_date and today < entry_date:
            messages.error(request, f'Veículo com entrada agendada para {entry_date.strftime("%d/%m/%Y")}. Não é possível pausar antes da entrada.')
            next_url = (request.POST.get('next') or '').strip()
            if next_url:
                return redirect(next_url)
            return redirect('budgets:kanban_today')

        if task.status != WorkOrderTask.Status.RUNNING:
            messages.error(request, 'Só é possível pausar uma tarefa em andamento.')
            return redirect('budgets:kanban_today')

        if task.last_started_at is None:
            messages.error(request, 'Tarefa sem início registrado.')
            return redirect('budgets:kanban_today')

        now = timezone.now()
        delta, _ = capped_work_delta_seconds(task.last_started_at, now, task.allow_overtime)
        task.elapsed_seconds = int(task.elapsed_seconds or 0) + delta
        task.last_started_at = None
        task.status = WorkOrderTask.Status.PAUSED

        hours = (Decimal(task.elapsed_seconds) / Decimal('3600')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        task.actual_hours = hours

        task.save(update_fields=['elapsed_seconds', 'last_started_at', 'status', 'actual_hours'])
        sync_shop_service_from_task(task)
        messages.success(request, 'Tarefa pausada.')
        next_url = (request.POST.get('next') or '').strip()
        if next_url:
            return redirect(next_url)
        return redirect('budgets:kanban_today')


class WorkOrderTaskFinishView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.OPERATIONAL)

    def post(self, request, pk):
        task = WorkOrderTask.objects.select_related('work_order', 'work_order__budget', 'collaborator', 'service').filter(pk=pk).first()
        if task is None:
            raise Http404('Tarefa não encontrada.')

        today = timezone.localdate()
        entry_date = getattr(getattr(task.work_order, 'budget', None), 'entry_date', None)
        if entry_date and today < entry_date:
            messages.error(request, f'Veículo com entrada agendada para {entry_date.strftime("%d/%m/%Y")}. Não é possível finalizar antes da entrada.')
            next_url = (request.POST.get('next') or '').strip()
            if next_url:
                return redirect(next_url)
            return redirect('budgets:kanban_today')

        if task.status == WorkOrderTask.Status.DONE:
            messages.error(request, 'Tarefa já concluída.')
            return redirect('budgets:kanban_today')

        if task.status != WorkOrderTask.Status.RUNNING:
            messages.error(request, 'Para finalizar, a tarefa precisa estar em andamento.')
            next_url = (request.POST.get('next') or '').strip()
            if next_url:
                return redirect(next_url)
            return redirect('budgets:kanban_today')

        now = timezone.now()
        delta, effective_end = capped_work_delta_seconds(task.last_started_at, now, task.allow_overtime)
        elapsed_seconds = int(task.elapsed_seconds or 0) + delta

        task.elapsed_seconds = elapsed_seconds
        task.last_started_at = None
        task.completed_at = effective_end or now
        task.status = WorkOrderTask.Status.DONE

        hours = (Decimal(task.elapsed_seconds) / Decimal('3600')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        task.actual_hours = hours

        task.save(update_fields=['elapsed_seconds', 'last_started_at', 'completed_at', 'status', 'actual_hours'])
        sync_shop_service_from_task(task)
        if task.collaborator_id and not CommissionLine.objects.filter(task=task, collaborator_id=task.collaborator_id).exists():
            percent = Decimal('0')
            base_amount = task.planned_amount or Decimal('0')
            commission_amount = Decimal('0')

            collaborator = task.collaborator
            if collaborator and collaborator.function in (Collaborator.Function.MANAGER, Collaborator.Function.FINANCE):
                percent = Decimal('0')
                commission_amount = Decimal('0')
            else:
                service = task.service
                if service and service.commission_mode == ServiceCatalog.CommissionMode.PERCENT:
                    percent = Decimal(service.commission_value or 0)
                    commission_amount = (base_amount * (percent / Decimal('100'))).quantize(
                        Decimal('0.01'),
                        rounding=ROUND_HALF_UP,
                    )
                elif service and service.commission_mode == ServiceCatalog.CommissionMode.FIXED:
                    percent = Decimal('0')
                    commission_amount = Decimal(service.commission_value or 0).quantize(
                        Decimal('0.01'),
                        rounding=ROUND_HALF_UP,
                    )
                else:
                    percent = Decimal(collaborator.commission_percent or 0) if collaborator else Decimal('0')
                    commission_amount = (base_amount * (percent / Decimal('100'))).quantize(
                        Decimal('0.01'),
                        rounding=ROUND_HALF_UP,
                    )

            if commission_amount > 0:
                CommissionLine.objects.create(
                    task=task,
                    collaborator=task.collaborator,
                    percent=percent,
                    base_amount=base_amount,
                    commission_amount=commission_amount,
                )
        messages.success(request, 'Tarefa finalizada.')
        next_url = (request.POST.get('next') or '').strip()
        if next_url:
            return redirect(next_url)
        return redirect('budgets:kanban_today')


class WorkOrderTaskToggleOvertimeView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def post(self, request, pk):
        task = WorkOrderTask.objects.filter(pk=pk).only('id', 'allow_overtime').first()
        if task is None:
            raise Http404('Tarefa não encontrada.')

        value = (request.POST.get('allow_overtime') or '').strip().lower()
        task.allow_overtime = value in ('1', 'true', 'on', 'yes')
        task.save(update_fields=['allow_overtime'])
        messages.success(request, 'Extra atualizado.')

        next_url = (request.POST.get('next') or '').strip()
        if next_url:
            return redirect(next_url)
        return redirect('budgets:kanban_today')


class CommissionOpenListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = CommissionLine
    template_name = 'budgets/commission_open_list.html'
    context_object_name = 'commissions'
    paginate_by = 50
    allowed_roles = (
        CustomUser.Role.MANAGER,
        CustomUser.Role.FINANCE,
        CustomUser.Role.ESTIMATOR,
        CustomUser.Role.OPERATIONAL,
    )

    def _logged_collaborator(self):
        user = getattr(self.request, 'user', None)
        email = (getattr(user, 'email', '') or '').strip()
        if not email:
            return None
        return Collaborator.objects.filter(email__iexact=email).only('id', 'name', 'email').first()

    def get_queryset(self):
        today = timezone.localdate()
        first_day = today.replace(day=1)
        next_month = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_day = next_month - timedelta(days=1)

        date_from_raw = (self.request.GET.get('date_from') or '').strip()
        date_to_raw = (self.request.GET.get('date_to') or '').strip()
        date_from = first_day
        date_to = last_day
        if date_from_raw:
            try:
                date_from = date.fromisoformat(date_from_raw)
            except ValueError:
                date_from = first_day
        if date_to_raw:
            try:
                date_to = date.fromisoformat(date_to_raw)
            except ValueError:
                date_to = last_day
        if date_to < date_from:
            date_to = date_from
        show_all = (self.request.GET.get('show_all') or '').strip().lower() in ('1', 'true', 'on', 'yes')

        qs = (
            super()
            .get_queryset()
            .select_related('task', 'task__service', 'task__work_order', 'task__work_order__budget', 'collaborator')
        )
        if not show_all:
            qs = qs.filter(is_paid=False)
        qs = qs.filter(task__completed_at__date__gte=date_from, task__completed_at__date__lte=date_to)
        collaborator_id = (self.request.GET.get('collaborator_id') or '').strip()
        user = getattr(self.request, 'user', None)
        if getattr(user, 'role', None) == CustomUser.Role.OPERATIONAL and not getattr(user, 'is_superuser', False):
            logged_collaborator = self._logged_collaborator()
            if logged_collaborator is None:
                return qs.none()
            collaborator_id = str(logged_collaborator.id)
        if collaborator_id:
            qs = qs.filter(collaborator_id=collaborator_id)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        first_day = today.replace(day=1)
        next_month = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_day = next_month - timedelta(days=1)

        date_from_raw = (self.request.GET.get('date_from') or '').strip()
        date_to_raw = (self.request.GET.get('date_to') or '').strip()
        date_from = first_day
        date_to = last_day
        if date_from_raw:
            try:
                date_from = date.fromisoformat(date_from_raw)
            except ValueError:
                date_from = first_day
        if date_to_raw:
            try:
                date_to = date.fromisoformat(date_to_raw)
            except ValueError:
                date_to = last_day
        if date_to < date_from:
            date_to = date_from

        show_all = (self.request.GET.get('show_all') or '').strip().lower() in ('1', 'true', 'on', 'yes')

        user = getattr(self.request, 'user', None)
        restrict_to_own_commissions = (
            getattr(user, 'role', None) == CustomUser.Role.OPERATIONAL
            and not getattr(user, 'is_superuser', False)
        )
        logged_collaborator = self._logged_collaborator() if restrict_to_own_commissions else None

        selected_collaborator_id = (self.request.GET.get('collaborator_id') or '').strip()
        if logged_collaborator is not None:
            selected_collaborator_id = str(logged_collaborator.id)
        selected_collaborator = None
        if selected_collaborator_id:
            selected_collaborator = Collaborator.objects.filter(pk=selected_collaborator_id).only('id', 'name').first()
            if selected_collaborator is None:
                selected_collaborator_id = ''

        total = Decimal('0')
        for line in context.get('commissions', []):
            total += line.commission_amount or Decimal('0')
        context['total_open_commission'] = total
        if logged_collaborator is not None:
            context['collaborators'] = [logged_collaborator]
            context['selected_collaborator'] = logged_collaborator
        else:
            context['collaborators'] = Collaborator.objects.all().only('id', 'name').order_by('name')
            context['selected_collaborator'] = selected_collaborator
        context['selected_collaborator_id'] = selected_collaborator_id
        context['date_from'] = date_from
        context['date_to'] = date_to
        context['show_all'] = show_all
        context['restrict_to_own_commissions'] = restrict_to_own_commissions
        context['now'] = timezone.localtime(timezone.now())
        return context


class CommissionTogglePaidView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def post(self, request, pk):
        line = CommissionLine.objects.select_related('task', 'task__work_order').filter(pk=pk).first()
        if line is None:
            raise Http404('Comissão não encontrada.')

        value = (request.POST.get('is_paid') or '').strip().lower()
        line.is_paid = value in ('1', 'true', 'on', 'yes')
        line.paid_at = timezone.now() if line.is_paid else None
        line.save(update_fields=['is_paid', 'paid_at'])
        messages.success(request, 'Comissão atualizada.')

        next_url = (request.POST.get('next') or '').strip()
        if next_url:
            return redirect(next_url)
        return redirect('budgets:commission_open_list')


class PiecesStatusReportView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def get(self, request):
        today = timezone.localdate()
        raw = (request.GET.get('date') or '').strip()
        selected = today
        if raw:
            try:
                selected = date.fromisoformat(raw)
            except ValueError:
                selected = today

        budgets = (
            Budget.objects.filter(pieces__isnull=False)
            .distinct()
            .select_related('customer', 'vehicle')
            .prefetch_related('pieces')
            .order_by('-created_at')
        )

        rows = []
        for b in budgets:
            pieces = list(b.pieces.all())
            total = len(pieces)
            arrived = 0
            late = 0
            not_bought = 0
            pending = 0

            piece_rows = []
            for p in pieces:
                is_arrived = bool(p.arrived or p.arrival_date)
                if is_arrived:
                    arrived += 1
                else:
                    pending += 1

                is_late = bool((not is_arrived) and p.expected_arrival_date and p.expected_arrival_date < selected)
                if is_late:
                    late += 1

                is_not_bought = bool(p.provider_type == Piece.ProviderType.SHOP and not p.purchase_date)
                if is_not_bought:
                    not_bought += 1

                if is_arrived:
                    status_label = 'Chegou'
                    status_color = 'border-[#2F855A] bg-[#063018] text-[#BBF7D0]'
                elif is_late:
                    days_late = (selected - p.expected_arrival_date).days if p.expected_arrival_date else 0
                    if days_late == 1:
                        status_label = 'Atrasada (1 dia)'
                    else:
                        status_label = f'Atrasada ({days_late} dias)'
                    status_color = 'border-[#7F1D1D] bg-[#3B0A0A] text-[#FECACA]'
                elif is_not_bought:
                    status_label = 'Não comprada'
                    status_color = 'border-[#B45309] bg-[#2A1E06] text-[#FDE68A]'
                else:
                    status_label = 'Aguardando'
                    status_color = 'border-[#404040] bg-[#262626] text-[#E5E7EB]'

                piece_rows.append(
                    {
                        'id': p.id,
                        'name': p.name,
                        'provider': p.get_provider_type_display(),
                        'purchase_date': p.purchase_date,
                        'expected_arrival_date': p.expected_arrival_date,
                        'arrival_date': p.arrival_date,
                        'arrived': p.arrived,
                        'status_label': status_label,
                        'status_color': status_color,
                    }
                )

            rows.append(
                {
                    'budget': b,
                    'pieces': piece_rows,
                    'total': total,
                    'arrived': arrived,
                    'pending': pending,
                    'late': late,
                    'not_bought': not_bought,
                }
            )

        context = {
            'today': today,
            'selected_date': selected,
            'rows': rows,
        }
        return render(request, 'budgets/report_pieces.html', context)


class WorkOrderDetailView(LoginRequiredMixin, RoleRequiredMixin, DetailView):
    model = WorkOrder
    template_name = 'budgets/workorder_detail.html'
    context_object_name = 'work_order'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related('budget', 'budget__customer', 'budget__vehicle')
            .prefetch_related('tasks', 'tasks__collaborator')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sync_xml_third_party_services(self.object.budget)
        sync_office_managed_service_tasks(self.object)
        tasks_open = self.object.tasks.exclude(status=WorkOrderTask.Status.DONE).select_related('collaborator')
        tasks_done = self.object.tasks.filter(status=WorkOrderTask.Status.DONE).select_related('collaborator')
        visible_third_party = get_visible_third_party_services(self.object.budget)
        third_party_open = [service for service in visible_third_party if service.status != ThirdPartyService.Status.DONE]
        third_party_done = [service for service in visible_third_party if service.status == ThirdPartyService.Status.DONE]

        selected_collaborator_id = (self.request.GET.get('collaborator_id') or '').strip()
        selected_collaborator = None
        if selected_collaborator_id:
            selected_collaborator = Collaborator.objects.filter(
                pk=selected_collaborator_id,
                function=Collaborator.Function.OPERATIONAL,
            ).only('id', 'name').first()
            if selected_collaborator is not None:
                tasks_open = tasks_open.filter(collaborator_id=selected_collaborator.id)
                tasks_done = tasks_done.filter(collaborator_id=selected_collaborator.id)
            else:
                selected_collaborator_id = ''

        context['tasks_open'] = tasks_open
        context['tasks_done'] = tasks_done
        context['tasks_open_count'] = tasks_open.count()
        context['tasks_done_count'] = tasks_done.count()
        context['selected_collaborator_id'] = selected_collaborator_id
        context['selected_collaborator'] = selected_collaborator
        context['collaborators_operational'] = Collaborator.objects.filter(
            function=Collaborator.Function.OPERATIONAL
        ).only('id', 'name')
        context['task_status_choices'] = WorkOrderTask.Status.choices
        context['third_party_open'] = third_party_open
        context['third_party_done'] = third_party_done
        context['third_party_open_count'] = len(third_party_open)
        context['third_party_done_count'] = len(third_party_done)
        context['suppliers_active'] = Supplier.objects.filter(is_active=True).only('id', 'name')
        context['third_party_status_choices'] = ThirdPartyService.Status.choices
        context['today'] = timezone.localdate()
        return context


def get_operational_collaborator(collaborator_id):
    collaborator_id = (collaborator_id or '').strip()
    if not collaborator_id:
        return None
    return Collaborator.objects.filter(
        pk=collaborator_id,
        function=Collaborator.Function.OPERATIONAL,
    ).first()


class WorkOrderTaskScheduleView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def post(self, request, pk):
        task = WorkOrderTask.objects.select_related('work_order', 'work_order__budget').filter(pk=pk).first()
        if task is None:
            raise Http404('Tarefa não encontrada.')

        collaborator_id = (request.POST.get('collaborator_id') or '').strip()
        service_id = (request.POST.get('service_id') or '').strip()
        scheduled_date_raw = (request.POST.get('scheduled_date') or '').strip()
        planned_amount_raw = (request.POST.get('planned_amount') or '').strip()
        actual_hours_raw = (request.POST.get('actual_hours') or '').strip()
        status = (request.POST.get('status') or '').strip()

        update_fields = []
        had_error = False

        if collaborator_id:
            collaborator = get_operational_collaborator(collaborator_id)
            if collaborator is None:
                had_error = True
                messages.error(request, 'Colaborador inválido.')
            else:
                task.collaborator = collaborator
                update_fields.append('collaborator')
        else:
            task.collaborator = None
            update_fields.append('collaborator')

        if service_id:
            service = ServiceCatalog.objects.filter(pk=service_id).only('id').first()
            if service is None:
                had_error = True
                messages.error(request, 'Serviço inválido.')
            else:
                task.service = service
                update_fields.append('service')

        if scheduled_date_raw:
            try:
                scheduled = date.fromisoformat(scheduled_date_raw)
                entry_date = getattr(getattr(task.work_order, 'budget', None), 'entry_date', None)
                if entry_date and scheduled < entry_date:
                    had_error = True
                    messages.error(
                        request,
                        f'Veículo com entrada agendada para {entry_date.strftime("%d/%m/%Y")}. Não é possível programar antes da entrada.',
                    )
                else:
                    task.scheduled_date = scheduled
                    update_fields.append('scheduled_date')
            except ValueError:
                had_error = True
                messages.error(request, 'Data inválida.')
        else:
            task.scheduled_date = None
            update_fields.append('scheduled_date')

        if planned_amount_raw:
            try:
                parsed = Decimal(planned_amount_raw.replace(',', '.'))
                if parsed < 0:
                    raise ValueError()
                task.planned_amount = parsed
                update_fields.append('planned_amount')
            except Exception:
                had_error = True
                messages.error(request, 'Valor (R$) inválido.')

        if actual_hours_raw:
            try:
                parsed = Decimal(actual_hours_raw.replace(',', '.'))
                if parsed < 0:
                    raise ValueError()
                task.actual_hours = parsed
                update_fields.append('actual_hours')
            except Exception:
                had_error = True
                messages.error(request, 'Horas (real) inválida.')

        if status:
            valid_status = dict(WorkOrderTask.Status.choices)
            if status not in valid_status:
                had_error = True
                messages.error(request, 'Status inválido.')
            else:
                if status == WorkOrderTask.Status.RUNNING:
                    sequence_block_message = get_task_sequence_block_message(task)
                    if sequence_block_message:
                        had_error = True
                        messages.error(request, sequence_block_message)
                task.status = status
                update_fields.append('status')

        if update_fields and not had_error:
            task.save(update_fields=sorted(set(update_fields)))
            sync_shop_service_from_task(task)
            messages.success(request, 'Agendamento salvo.')

        return redirect('budgets:workorder_detail', pk=task.work_order_id)


class WorkOrderTaskBulkAssignView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def post(self, request, pk):
        work_order = WorkOrder.objects.filter(pk=pk).first()
        if work_order is None:
            raise Http404('OS não encontrada.')

        next_url = (request.POST.get('next') or '').strip()
        redirect_url = next_url or reverse('budgets:workorder_detail', kwargs={'pk': work_order.pk})

        collaborator = get_operational_collaborator(request.POST.get('bulk_collaborator_id'))
        if collaborator is None:
            messages.error(request, 'Selecione um colaborador operacional válido para a atribuição em lote.')
            return redirect(redirect_url)

        raw_task_ids = (request.POST.get('task_ids') or '').strip()
        selected_task_ids = []
        for raw_task_id in raw_task_ids.split(','):
            raw_task_id = raw_task_id.strip()
            if raw_task_id.isdigit():
                selected_task_ids.append(int(raw_task_id))
        selected_task_ids = list(dict.fromkeys(selected_task_ids))

        if not selected_task_ids:
            messages.error(request, 'Selecione ao menos uma tarefa para atribuição em lote.')
            return redirect(redirect_url)

        tasks = list(
            WorkOrderTask.objects.filter(
                work_order_id=work_order.pk,
                pk__in=selected_task_ids,
            ).exclude(status=WorkOrderTask.Status.DONE)
        )
        if not tasks:
            messages.error(request, 'Nenhuma tarefa elegível foi encontrada para esta atribuição em lote.')
            return redirect(redirect_url)

        updated_count = 0
        for task in tasks:
            if task.collaborator_id == collaborator.id:
                continue
            task.collaborator = collaborator
            task.save(update_fields=['collaborator'])
            sync_shop_service_from_task(task)
            updated_count += 1

        ignored_count = len(selected_task_ids) - len(tasks)
        if updated_count:
            messages.success(
                request,
                f'{updated_count} tarefa(s) atualizada(s) para {collaborator.name}.',
            )
        else:
            messages.info(
                request,
                f'As {len(tasks)} tarefa(s) elegíveis já estavam atribuídas para {collaborator.name}.',
            )

        if ignored_count > 0:
            messages.info(
                request,
                f'{ignored_count} tarefa(s) foram ignoradas por não pertencerem a esta OS ou já estarem concluídas.',
            )

        return redirect(redirect_url)


class PieceCreateView(LoginRequiredMixin, RoleRequiredMixin, CreateView):
    model = Piece
    form_class = PieceForm
    template_name = 'budgets/piece_form.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def dispatch(self, request, *args, **kwargs):
        self.budget = Budget.objects.filter(pk=kwargs.get('budget_pk')).first()
        if self.budget is None:
            raise Http404('Orçamento não encontrado.')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.budget = self.budget
        if form.cleaned_data.get('provider_type') != Piece.ProviderType.SHOP:
            form.instance.cost_price = Decimal('0')
            form.instance.profit_percent = Decimal('0')
        arrival_date = form.cleaned_data.get('arrival_date')
        arrived = bool(form.cleaned_data.get('arrived'))
        if arrival_date:
            form.instance.arrived = True
        else:
            form.instance.arrived = arrived
        response = super().form_valid(form)
        messages.success(self.request, 'Peça salva.')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['budget'] = self.budget
        context['is_edit'] = False
        return context

    def get_success_url(self):
        return reverse('budgets:budget_detail', kwargs={'pk': self.budget.pk})


class PieceUpdateView(LoginRequiredMixin, RoleRequiredMixin, UpdateView):
    model = Piece
    form_class = PieceForm
    template_name = 'budgets/piece_form.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def get_queryset(self):
        return super().get_queryset().select_related('budget')

    def form_valid(self, form):
        if form.cleaned_data.get('provider_type') != Piece.ProviderType.SHOP:
            form.instance.cost_price = Decimal('0')
            form.instance.profit_percent = Decimal('0')
        arrival_date = form.cleaned_data.get('arrival_date')
        arrived = bool(form.cleaned_data.get('arrived'))
        if arrival_date:
            form.instance.arrived = True
        else:
            form.instance.arrived = arrived
        response = super().form_valid(form)
        messages.success(self.request, 'Peça salva.')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['budget'] = self.object.budget
        context['is_edit'] = True
        return context

    def get_success_url(self):
        return reverse('budgets:budget_detail', kwargs={'pk': self.object.budget_id})


class PieceDeleteView(LoginRequiredMixin, RoleRequiredMixin, DeleteView):
    model = Piece
    template_name = 'budgets/piece_confirm_delete.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def get_queryset(self):
        return super().get_queryset().select_related('budget')

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        response = super().delete(request, *args, **kwargs)
        messages.success(request, 'Peça removida.')
        return response

    def get_success_url(self):
        return reverse('budgets:budget_detail', kwargs={'pk': self.object.budget_id})


class ServiceCatalogListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = ServiceCatalog
    template_name = 'budgets/service_catalog_list.html'
    context_object_name = 'services'
    paginate_by = 50
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)


class ServiceCatalogCreateView(LoginRequiredMixin, RoleRequiredMixin, CreateView):
    model = ServiceCatalog
    form_class = ServiceCatalogForm
    template_name = 'budgets/service_catalog_form.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_success_url(self):
        return reverse('budgets:service_catalog_list')


class ServiceCatalogUpdateView(LoginRequiredMixin, RoleRequiredMixin, UpdateView):
    model = ServiceCatalog
    form_class = ServiceCatalogForm
    template_name = 'budgets/service_catalog_form.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_success_url(self):
        return reverse('budgets:service_catalog_list')


class ServiceCatalogDeleteView(LoginRequiredMixin, RoleRequiredMixin, DeleteView):
    model = ServiceCatalog
    template_name = 'budgets/service_catalog_confirm_delete.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_success_url(self):
        return reverse('budgets:service_catalog_list')


class BankAccountListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = BankAccount
    template_name = 'budgets/bank_account_list.html'
    context_object_name = 'bank_accounts'
    paginate_by = 25
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)


class BankAccountCreateView(LoginRequiredMixin, RoleRequiredMixin, CreateView):
    model = BankAccount
    form_class = BankAccountForm
    template_name = 'budgets/bank_account_form.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_success_url(self):
        return reverse('budgets:bank_account_list')


class BankAccountUpdateView(LoginRequiredMixin, RoleRequiredMixin, UpdateView):
    model = BankAccount
    form_class = BankAccountForm
    template_name = 'budgets/bank_account_form.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_success_url(self):
        return reverse('budgets:bank_account_list')


class BankAccountDeleteView(LoginRequiredMixin, RoleRequiredMixin, DeleteView):
    model = BankAccount
    template_name = 'budgets/bank_account_confirm_delete.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def post(self, request, *args, **kwargs):
        try:
            self.object = self.get_object()
        except Http404:
            messages.error(request, 'Conta bancária não encontrada.')
            return redirect('budgets:bank_account_list')

        try:
            self.object.delete()
        except ProtectedError:
            messages.error(request, 'Esta conta bancária está vinculada a lançamentos e não pode ser excluída.')
            return redirect('budgets:bank_account_list')

        messages.success(request, 'Conta bancária removida.')
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('budgets:bank_account_list')


class SupplierListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = Supplier
    template_name = 'budgets/supplier_list.html'
    context_object_name = 'suppliers'
    paginate_by = 25
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)


class SupplierCreateView(LoginRequiredMixin, RoleRequiredMixin, CreateView):
    model = Supplier
    form_class = SupplierForm
    template_name = 'budgets/supplier_form.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_success_url(self):
        return reverse('budgets:supplier_list')


class SupplierUpdateView(LoginRequiredMixin, RoleRequiredMixin, UpdateView):
    model = Supplier
    form_class = SupplierForm
    template_name = 'budgets/supplier_form.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_success_url(self):
        return reverse('budgets:supplier_list')


class SupplierDeleteView(LoginRequiredMixin, RoleRequiredMixin, DeleteView):
    model = Supplier
    template_name = 'budgets/supplier_confirm_delete.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_success_url(self):
        return reverse('budgets:supplier_list')


class BudgetDetailView(LoginRequiredMixin, RoleRequiredMixin, DetailView):
    model = Budget
    template_name = 'budgets/budget_detail.html'
    context_object_name = 'budget'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related('customer', 'vehicle')
            .prefetch_related('pieces', 'third_party_services', 'photos')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        work_order = get_budget_work_order(self.object)
        if work_order is not None:
            sync_xml_third_party_services(self.object)
            sync_office_managed_service_tasks(work_order)
        context['pieces_parts'] = self.object.pieces.all()
        context['photos'] = self.object.photos.all()
        context['third_party_form'] = ThirdPartyServiceForm()
        context['today'] = timezone.localdate()
        context['work_order'] = work_order
        context['delivery_status'] = budget_delivery_status(self.object)
        user_role = getattr(getattr(self.request, 'user', None), 'role', None)
        context['can_manage_delivery'] = user_role in (
            CustomUser.Role.MANAGER,
            CustomUser.Role.FINANCE,
        )
        context['can_access_finance'] = user_role in (
            CustomUser.Role.MANAGER,
            CustomUser.Role.FINANCE,
        )
        context['administrative_closure_status'] = budget_administrative_closure_status(
            self.object,
            self.request.user,
        )
        context['administrative_closure_form'] = AdministrativeClosureForm(
            initial={
                'delivery_date': context['administrative_closure_status']['suggested_delivery_date'],
                'confirm_no_commission': True,
            }
        )
        context['administrative_closure_open'] = (self.request.GET.get('admin_close') or '').strip() == '1'
        finance_open_movements = context['delivery_status'].get('finance_open_movements') or []
        context['delivery_finance_url'] = ''
        if finance_open_movements and context['can_access_finance']:
            context['delivery_finance_url'] = (
                reverse('budgets:finance_dashboard') + f'?edit={finance_open_movements[0].id}'
            )

        visible_third_party = get_visible_third_party_services(self.object)
        manual_third_party = [
            {'description': s.description, 'total_amount': s.amount}
            for s in visible_third_party
        ]
        manual_third_party_total = sum([s['total_amount'] for s in manual_third_party], Decimal('0'))
        manual_keys = {
            third_party_identity(s['description'], s['total_amount'])
            for s in manual_third_party
        }

        xml = self.object.source_xml or ''
        if xml:
            try:
                service_lines = extract_service_lines(xml.encode('utf-8', errors='replace'))
                third_party_xml = [s for s in service_lines if s.get('is_third_party') and not is_office_managed_service(s.get('description'))]
                context['service_lines'] = annotate_service_lines_completion(
                    self.object,
                    [
                        s for s in service_lines
                        if not s.get('is_third_party') or is_office_managed_service(s.get('description'))
                    ],
                )
                missing_third_party_xml = [
                    s for s in third_party_xml
                    if third_party_identity(s.get('description'), s.get('total_amount', Decimal('0'))) not in manual_keys
                ]
                third_party_xml_total = sum([s.get('total_amount', Decimal('0')) for s in missing_third_party_xml], Decimal('0'))
                context['third_party_services'] = manual_third_party + missing_third_party_xml
                context['third_party_services_total'] = manual_third_party_total + third_party_xml_total
            except Exception:
                context['service_lines'] = []
                context['third_party_services'] = manual_third_party
                context['third_party_services_total'] = manual_third_party_total
        else:
            context['service_lines'] = []
            context['third_party_services'] = manual_third_party
            context['third_party_services_total'] = manual_third_party_total
        return context


class BudgetDeliverView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def post(self, request, pk):
        budget = Budget.objects.select_related('customer', 'vehicle').filter(pk=pk).first()
        if budget is None:
            raise Http404('Orçamento não encontrado.')

        if budget.is_delivered:
            messages.info(request, 'Este veículo já foi entregue.')
            return redirect('budgets:budget_detail', pk=budget.pk)

        status = budget_delivery_status(budget)
        if not status['can_deliver']:
            blocker_text = ' '.join(status['blockers'])
            messages.error(request, f'Não foi possível entregar o veículo. {blocker_text}')
            return redirect('budgets:budget_detail', pk=budget.pk)

        budget.delivered_at = timezone.now()
        budget.delivered_by = request.user
        budget.save(update_fields=['delivered_at', 'delivered_by'])
        messages.success(request, 'Veículo entregue com sucesso.')
        return redirect('budgets:budget_detail', pk=budget.pk)


class BudgetAdministrativeCloseView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER,)

    def post(self, request, pk):
        budget = Budget.objects.select_related('customer', 'vehicle').filter(pk=pk).first()
        if budget is None:
            raise Http404('Orçamento não encontrado.')

        if budget.is_delivered:
            messages.info(request, 'Este veículo já foi entregue.')
            return redirect('budgets:budget_detail', pk=budget.pk)

        closure_status = budget_administrative_closure_status(budget, request.user)
        if not closure_status['can_administratively_close']:
            blocker_text = ' '.join(closure_status['blockers'])
            messages.error(request, f'Não foi possível finalizar administrativamente. {blocker_text}')
            return redirect(reverse('budgets:budget_detail', kwargs={'pk': budget.pk}) + '?admin_close=1')

        form = AdministrativeClosureForm(request.POST)
        if not form.is_valid():
            error_text = ' '.join(
                [
                    error
                    for errors in form.errors.values()
                    for error in errors
                ]
            )
            messages.error(request, f'Não foi possível finalizar administrativamente. {error_text}')
            return redirect(reverse('budgets:budget_detail', kwargs={'pk': budget.pk}) + '?admin_close=1')

        delivery_date = form.cleaned_data['delivery_date']
        delivery_datetime = timezone.make_aware(
            datetime.combine(delivery_date, dt_time(12, 0))
        )
        work_order = closure_status['work_order']

        with transaction.atomic():
            budget.delivered_at = delivery_datetime
            budget.delivered_by = request.user
            budget.administrative_closure = True
            budget.administrative_closed_at = timezone.now()
            budget.administrative_closed_by = request.user
            budget.administrative_closure_reason = form.cleaned_data['reason']
            budget.save(
                update_fields=[
                    'delivered_at',
                    'delivered_by',
                    'administrative_closure',
                    'administrative_closed_at',
                    'administrative_closed_by',
                    'administrative_closure_reason',
                ]
            )
            if work_order is not None and work_order.status != WorkOrder.Status.CLOSED:
                work_order.status = WorkOrder.Status.CLOSED
                work_order.save(update_fields=['status'])

        messages.success(request, 'Finalização administrativa registrada com sucesso. Comissão não foi gerada.')
        return redirect('budgets:budget_detail', pk=budget.pk)


class BudgetPhotoCreateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def post(self, request, pk):
        budget = Budget.objects.filter(pk=pk).only('id').first()
        if budget is None:
            raise Http404('Orçamento não encontrado.')

        image_file = request.FILES.get('image_file')
        caption = (request.POST.get('caption') or '').strip()
        if image_file is None:
            messages.error(request, 'Selecione uma foto para enviar.')
            return redirect('budgets:budget_detail', pk=budget.pk)

        BudgetPhoto.objects.create(
            budget=budget,
            image_file=image_file,
            caption=caption,
        )
        messages.success(request, 'Foto do orçamento salva.')
        return redirect('budgets:budget_detail', pk=budget.pk)


class BudgetPhotoDeleteView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def post(self, request, pk):
        photo = BudgetPhoto.objects.select_related('budget').filter(pk=pk).first()
        if photo is None:
            raise Http404('Foto não encontrada.')

        budget_id = photo.budget_id
        photo.delete()
        messages.success(request, 'Foto removida.')
        return redirect('budgets:budget_detail', pk=budget_id)


class BudgetUpdateView(LoginRequiredMixin, RoleRequiredMixin, UpdateView):
    model = Budget
    template_name = 'budgets/budget_form.html'
    fields = (
        'status',
        'refusal_reason_code',
        'refusal_reason',
        'entry_date',
        'repair_start_date',
        'expected_delivery_date',
        'allow_repair_without_parts',
    )
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def get_queryset(self):
        return super().get_queryset().select_related('customer', 'vehicle')

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        self._old_status = self.object.status
        return super().post(request, *args, **kwargs)

    def _compute_total_amount(self):
        xml = self.object.source_xml or ''
        base_total = self.object.total_amount
        if xml:
            try:
                _, _, _, parsed_total_amount, _, _, _ = parse_cilia_xml(xml.encode('utf-8', errors='replace'))
                if parsed_total_amount > 0:
                    base_total = parsed_total_amount
            except Exception:
                base_total = self.object.total_amount

        return base_total + get_budget_extra_third_party_total(self.object)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['computed_total_amount'] = self._compute_total_amount()
        context['today'] = timezone.localdate()
        context['bank_accounts'] = BankAccount.objects.filter(is_active=True).order_by('bank_name', 'account_name')
        pending_finance = self.request.session.get(pending_budget_finance_session_key(self.object.pk))
        context['pending_budget_finance'] = pending_finance
        can_access_finance = bool(
            getattr(self.request, 'user', None)
            and getattr(self.request.user, 'role', None)
            in (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)
        )
        needs_finance = bool(
            can_access_finance
            and (self.object.status == Budget.Status.AUTHORIZED or pending_finance)
            and not CashMovement.objects.filter(budget=self.object).exists()
        )
        context['needs_finance'] = needs_finance
        show_finance_modal = (self.request.GET.get('finance') or '').strip() == '1'
        show_finance_modal = bool(
            can_access_finance
            and show_finance_modal
            and (self.object.status == Budget.Status.AUTHORIZED or pending_finance)
            and not CashMovement.objects.filter(budget=self.object).exists()
        )
        context['show_finance_modal'] = show_finance_modal
        pending_data = deserialize_pending_budget_data(pending_finance) if pending_finance else {}
        context['finance_default_due_date'] = (
            pending_data.get('expected_delivery_date')
            or pending_data.get('entry_date')
            or self.object.expected_delivery_date
            or self.object.entry_date
            or timezone.localdate()
        )
        return context

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.GET.get('discard_finance') == '1':
            request.session.pop(pending_budget_finance_session_key(self.object.pk), None)
            return redirect('budgets:budget_update', pk=self.object.pk)
        return super().get(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        pending_payload = self.request.session.get(pending_budget_finance_session_key(self.object.pk))
        if self.request.method == 'GET' and pending_payload:
            pending_data = deserialize_pending_budget_data(pending_payload)
            for field, value in pending_data.items():
                form.initial[field] = value
        return form

    def form_valid(self, form):
        status = form.cleaned_data.get('status')
        refusal_reason_code = (form.cleaned_data.get('refusal_reason_code') or '').strip()
        refusal_reason = (form.cleaned_data.get('refusal_reason') or '').strip()

        if status == Budget.Status.NOT_APPROVED:
            if not refusal_reason_code and not refusal_reason:
                form.add_error('refusal_reason', 'Informe o motivo da recusa.')
                return self.form_invalid(form)

        if status == Budget.Status.AUTHORIZED:
            if not form.cleaned_data.get('entry_date'):
                form.add_error('entry_date', 'Informe a data de entrada do veículo.')
                messages.error(self.request, 'Para aprovar, informe a data de entrada do veículo.')
            allow_repair_without_parts = bool(form.cleaned_data.get('allow_repair_without_parts'))

            try:
                has_pending_shop_parts = budget_has_pending_shop_parts(self.object)
            except Exception:
                has_pending_shop_parts = False

            if has_pending_shop_parts and form.cleaned_data.get('repair_start_date') and not allow_repair_without_parts:
                messages.warning(
                    self.request,
                    'Existem peças da oficina pendentes. O orçamento pode ser aprovado, mas o reparo não pode ser iniciado '
                    'até marcar as peças como chegaram ou liberar para seguir sem as peças.',
                )
                form.instance.repair_start_date = None

            if form.errors:
                return self.form_invalid(form)

            if not CashMovement.objects.filter(budget=self.object).exists():
                self.request.session[pending_budget_finance_session_key(self.object.pk)] = serialize_pending_budget_data(
                    form.cleaned_data
                )
                messages.info(self.request, 'Confirme o financeiro para concluir a aprovação do orçamento.')
                return redirect(f"{reverse('budgets:budget_update', kwargs={'pk': self.object.pk})}?finance=1")

        transitioned_to_authorized = (
            getattr(self, '_old_status', None) != Budget.Status.AUTHORIZED and status == Budget.Status.AUTHORIZED
        )
        if status == Budget.Status.AUTHORIZED:
            if transitioned_to_authorized or not getattr(self.object, 'approved_at', None):
                form.instance.approved_at = timezone.now()
        else:
            form.instance.approved_at = None
        response = super().form_valid(form)
        if transitioned_to_authorized:
            messages.success(self.request, 'Orçamento aprovado.')
        else:
            messages.success(self.request, 'Orçamento salvo.')

        try:
            has_pending_shop_parts_after = budget_has_pending_shop_parts(self.object)
        except Exception:
            has_pending_shop_parts_after = False
        if self.object.status == Budget.Status.AUTHORIZED and has_pending_shop_parts_after and not self.object.allow_repair_without_parts:
            messages.warning(
                self.request,
                'Atenção: orçamento aprovado com peças da oficina pendentes. O reparo fica bloqueado até as peças chegarem '
                'ou até liberar "seguir sem as peças".',
            )
        if self.object.status == Budget.Status.AUTHORIZED:
            ensure_work_order_for_budget(self.object)

        finance_missing = (
            self.object.status == Budget.Status.AUTHORIZED
            and not CashMovement.objects.filter(budget=self.object).exists()
        )
        if finance_missing:
            user = getattr(self.request, 'user', None)
            if user and getattr(user, 'role', None) in (
                CustomUser.Role.MANAGER,
                CustomUser.Role.FINANCE,
                CustomUser.Role.ESTIMATOR,
            ):
                url = reverse('budgets:budget_update', kwargs={'pk': self.object.pk})
                return redirect(f'{url}?finance=1')

        return response

    def get_success_url(self):
        return reverse('budgets:budget_detail', kwargs={'pk': self.object.pk})


class BudgetFinanceCreateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def post(self, request, pk):
        budget = Budget.objects.select_related('customer', 'vehicle').filter(pk=pk).first()
        if budget is None:
            raise Http404('Orçamento não encontrado.')

        pending_payload = request.session.get(pending_budget_finance_session_key(budget.pk))
        pending_data = deserialize_pending_budget_data(pending_payload) if pending_payload else None

        if budget.status != Budget.Status.AUTHORIZED and not pending_data:
            messages.error(request, 'O orçamento precisa estar Autorizado para registrar o financeiro.')
            return redirect('budgets:budget_update', pk=budget.pk)

        if CashMovement.objects.filter(budget=budget).exists():
            messages.error(request, 'Financeiro deste orçamento já foi registrado.')
            return redirect('budgets:budget_update', pk=budget.pk)

        kind = (request.POST.get('kind') or '').strip().upper()
        total = budget.total_amount or Decimal('0')
        today = timezone.localdate()
        bank_account_id_raw = (request.POST.get('bank_account_id') or '').strip()
        try:
            bank_account_id = int(bank_account_id_raw) if bank_account_id_raw else None
        except ValueError:
            bank_account_id = None
        bank_account = None
        if not bank_account_id:
            messages.error(request, 'Selecione o banco/conta para os lançamentos deste orçamento.')
            return redirect(f'{reverse("budgets:budget_update", kwargs={"pk": budget.pk})}?finance=1')
        bank_account = BankAccount.objects.filter(id=bank_account_id, is_active=True).first()
        if bank_account is None:
            messages.error(request, 'Banco/conta inválido.')
            return redirect(f'{reverse("budgets:budget_update", kwargs={"pk": budget.pk})}?finance=1')

        def parse_money(value):
            raw = (value or '').strip()
            if not raw:
                return Decimal('0')
            raw = raw.replace('R$', '').strip()
            raw = raw.replace(' ', '')
            if ',' in raw and '.' in raw:
                raw = raw.replace('.', '').replace(',', '.')
            elif ',' in raw:
                raw = raw.replace(',', '.')
            return Decimal(raw)

        def parse_date(value, default_date):
            raw = (value or '').strip()
            if not raw:
                return default_date
            return date.fromisoformat(raw)

        try:
            with transaction.atomic():
                if pending_data:
                    old_status = budget.status
                    budget.status = pending_data.get('status') or budget.status
                    budget.refusal_reason_code = pending_data.get('refusal_reason_code') or ''
                    budget.refusal_reason = pending_data.get('refusal_reason') or ''
                    budget.entry_date = pending_data.get('entry_date')
                    budget.repair_start_date = pending_data.get('repair_start_date')
                    budget.expected_delivery_date = pending_data.get('expected_delivery_date')
                    budget.allow_repair_without_parts = bool(pending_data.get('allow_repair_without_parts'))
                    if budget.status == Budget.Status.AUTHORIZED:
                        if old_status != Budget.Status.AUTHORIZED or not budget.approved_at:
                            budget.approved_at = timezone.now()
                    else:
                        budget.approved_at = None
                    budget.save()
                    if budget.status != Budget.Status.AUTHORIZED:
                        raise ValueError('O orçamento precisa estar Autorizado para registrar o financeiro.')
                    ensure_work_order_for_budget(budget)

                if kind == 'PARTICULAR':
                    entry_amount = parse_money(request.POST.get('entry_amount'))
                    entry_due = parse_date(request.POST.get('entry_due_date'), today)
                    is_received = (request.POST.get('entry_received') or '').strip().lower() in ('1', 'true', 'on', 'yes')

                    if entry_amount < 0:
                        raise ValueError('Valor de entrada inválido.')
                    if entry_amount > total:
                        raise ValueError('A entrada não pode ser maior que o total.')

                    remainder = (total - entry_amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    if entry_amount > 0:
                        CashMovement.objects.create(
                            budget=budget,
                            customer=budget.customer,
                            bank_account=bank_account,
                            direction=CashMovement.Direction.IN,
                            source=CashMovement.Source.PARTICULAR,
                            description=f'Orçamento #{budget.display_number} - Entrada',
                            amount=entry_amount,
                            launch_date=today,
                            due_date=entry_due,
                            is_realized=is_received,
                            realized_at=timezone.now() if is_received else None,
                        )
                    if remainder > 0:
                        due = parse_date(
                            request.POST.get('remainder_due_date'),
                            budget.expected_delivery_date or budget.entry_date or today,
                        )
                        CashMovement.objects.create(
                            budget=budget,
                            customer=budget.customer,
                            bank_account=bank_account,
                            direction=CashMovement.Direction.IN,
                            source=CashMovement.Source.PARTICULAR,
                            description=f'Orçamento #{budget.display_number} - Saldo',
                            amount=remainder,
                            launch_date=today,
                            due_date=due,
                            is_realized=False,
                        )
                elif kind == 'SEGURADORA':
                    franchise_amount = parse_money(request.POST.get('franchise_amount'))
                    franchise_due = parse_date(request.POST.get('franchise_due_date'), today)
                    franchise_received = (request.POST.get('franchise_received') or '').strip().lower() in (
                        '1',
                        'true',
                        'on',
                        'yes',
                    )
                    insurer_due = parse_date(
                        request.POST.get('insurer_due_date'),
                        budget.expected_delivery_date or budget.entry_date or today,
                    )

                    if franchise_amount < 0:
                        raise ValueError('Valor de franquia inválido.')
                    if franchise_amount > total:
                        raise ValueError('A franquia não pode ser maior que o total.')

                    insurer_amount = (total - franchise_amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

                    if franchise_amount > 0:
                        CashMovement.objects.create(
                            budget=budget,
                            customer=budget.customer,
                            bank_account=bank_account,
                            direction=CashMovement.Direction.IN,
                            source=CashMovement.Source.PARTICULAR,
                            description=f'Orçamento #{budget.display_number} - Franquia',
                            amount=franchise_amount,
                            launch_date=today,
                            due_date=franchise_due,
                            is_realized=franchise_received,
                            realized_at=timezone.now() if franchise_received else None,
                        )
                    if insurer_amount > 0:
                        CashMovement.objects.create(
                            budget=budget,
                            customer=budget.customer,
                            bank_account=bank_account,
                            direction=CashMovement.Direction.IN,
                            source=CashMovement.Source.INSURERS,
                            description=f'Orçamento #{budget.display_number} - Seguradora',
                            amount=insurer_amount,
                            launch_date=today,
                            due_date=insurer_due,
                            is_realized=False,
                        )
                else:
                    raise ValueError('Tipo inválido.')
        except Exception as exc:
            messages.error(request, str(exc) or 'Não foi possível registrar o financeiro.')
            return redirect(f'{reverse("budgets:budget_update", kwargs={"pk": budget.pk})}?finance=1')

        request.session.pop(pending_budget_finance_session_key(budget.pk), None)
        messages.success(request, 'Financeiro registrado.')
        return redirect('budgets:budget_detail', pk=budget.pk)


class BudgetDeleteView(LoginRequiredMixin, RoleRequiredMixin, DeleteView):
    model = Budget
    template_name = 'budgets/budget_confirm_delete.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def post(self, request, *args, **kwargs):
        try:
            self.object = self.get_object()
        except Http404:
            messages.error(request, 'Orçamento não encontrado.')
            return redirect('budgets:budget_list')

        try:
            self.object.delete()
        except ProtectedError:
            messages.error(
                request,
                'Este orçamento possui vínculos e não pode ser excluído. Remova antes os registros financeiros ou complementos relacionados.',
            )
            return redirect('budgets:budget_detail', pk=self.object.pk)

        messages.success(request, 'Orçamento removido.')
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('budgets:budget_list')


class BudgetXMLDownloadView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def get(self, request, cilia_number):
        budget = Budget.objects.filter(cilia_number=cilia_number).only('cilia_number', 'source_xml').first()
        if budget is None:
            raise Http404('Orçamento não encontrado.')
        if not budget.source_xml:
            raise Http404('Orçamento sem XML salvo.')

        filename = f'orcamento-{budget.cilia_number}.xml'
        response = HttpResponse(budget.source_xml, content_type='application/xml; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename=\"{filename}\"'
        return response


class ThirdPartyServiceCreateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def post(self, request, pk):
        budget = Budget.objects.filter(pk=pk).only('id', 'source_xml', 'total_amount').first()
        if budget is None:
            raise Http404('Orçamento não encontrado.')

        next_url = (request.POST.get('next') or '').strip()
        if not next_url.startswith('/'):
            next_url = ''

        form = ThirdPartyServiceForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Não foi possível salvar o serviço terceiro.')
            if next_url:
                return redirect(next_url)
            return redirect('budgets:budget_detail', pk=budget.pk)

        status = form.cleaned_data['status']
        completed_at = timezone.now() if status == ThirdPartyService.Status.DONE else None

        service = ThirdPartyService.objects.create(
            budget=budget,
            supplier_id=form.cleaned_data.get('supplier_id'),
            description=form.cleaned_data['description'],
            amount=form.cleaned_data['amount'],
            scheduled_date=form.cleaned_data.get('scheduled_date'),
            status=status,
            completed_at=completed_at,
            is_shop_service=bool(form.cleaned_data.get('is_shop_service')),
        )

        after_third_party_service_saved(service)
        messages.success(request, 'Serviço terceiro salvo.')
        if next_url:
            return redirect(next_url)
        return redirect('budgets:budget_detail', pk=budget.pk)


class ThirdPartyServiceUpdateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def post(self, request, pk):
        service = ThirdPartyService.objects.select_related('budget').filter(pk=pk).first()
        if service is None:
            raise Http404('Serviço terceiro não encontrado.')

        next_url = (request.POST.get('next') or '').strip()
        if not next_url.startswith('/'):
            next_url = ''

        form = ThirdPartyServiceForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Não foi possível atualizar o serviço terceiro.')
            if next_url:
                return redirect(next_url)
            return redirect('budgets:budget_detail', pk=service.budget_id)

        service.supplier_id = form.cleaned_data.get('supplier_id')
        service.description = form.cleaned_data['description']
        service.amount = form.cleaned_data['amount']
        service.scheduled_date = form.cleaned_data.get('scheduled_date')
        service.status = form.cleaned_data['status']
        service.is_shop_service = bool(form.cleaned_data.get('is_shop_service'))
        service.completed_at = timezone.now() if service.status == ThirdPartyService.Status.DONE else None
        service.save(
            update_fields=[
                'supplier',
                'description',
                'amount',
                'scheduled_date',
                'status',
                'is_shop_service',
                'completed_at',
            ]
        )
        after_third_party_service_saved(service)
        messages.success(request, 'Serviço terceiro atualizado.')
        if next_url:
            return redirect(next_url)
        return redirect('budgets:budget_detail', pk=service.budget_id)


class ThirdPartyServiceFinishView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def post(self, request, pk):
        service = ThirdPartyService.objects.select_related('budget').filter(pk=pk).first()
        if service is None:
            raise Http404('Serviço terceiro não encontrado.')

        next_url = (request.POST.get('next') or '').strip()
        if not next_url.startswith('/'):
            next_url = ''

        service.status = ThirdPartyService.Status.DONE
        service.completed_at = timezone.now()
        service.save(update_fields=['status', 'completed_at'])
        after_third_party_service_saved(service)
        messages.success(request, 'Serviço terceiro finalizado.')
        if next_url:
            return redirect(next_url)
        return redirect('budgets:budget_detail', pk=service.budget_id)


class CiliaXMLImportView(LoginRequiredMixin, RoleRequiredMixin, FormView):
    template_name = 'budgets/import_xml.html'
    form_class = CiliaXMLUploadForm
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def form_valid(self, form):
        xml_file = form.cleaned_data['xml_file']
        xml_bytes = xml_file.read()
        import_job = XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.MANUAL,
            file_name=xml_file.name,
            status=XMLImportJob.Status.PENDING,
        )

        try:
            result = import_cilia_xml_bytes(
                xml_bytes=xml_bytes,
                job=import_job,
            )
        except CiliaImportDuplicateError as exc:
            form.add_error('xml_file', str(exc))
            return self.form_invalid(form)
        except CiliaImportValidationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        except CiliaImportError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        if not result.parsed_customer_document:
            messages.warning(
                self.request,
                'XML sem CPF/CNPJ do cliente. Cadastro temporário criado (edite o cliente e informe o documento).',
            )

        messages.success(self.request, f'Orçamento importado com sucesso (ID: {result.budget.pk}).')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('budgets:budget_list')


class XMLImportJobListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = XMLImportJob
    template_name = 'budgets/import_job_list.html'
    context_object_name = 'jobs'
    paginate_by = 25
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def _status_filter(self):
        status = (self.request.GET.get('status') or '').strip().upper()
        valid_statuses = {choice[0] for choice in XMLImportJob.Status.choices}
        if status in valid_statuses:
            return status
        return ''

    def _provider_filter(self):
        provider = (self.request.GET.get('provider') or '').strip().upper()
        valid_providers = {choice[0] for choice in XMLImportJob.Provider.choices}
        if provider in valid_providers:
            return provider
        return ''

    def _search_term(self):
        return (self.request.GET.get('search') or '').strip()

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related('budget', 'budget__customer', 'budget__vehicle')
        )

        status = self._status_filter()
        if status:
            queryset = queryset.filter(status=status)

        provider = self._provider_filter()
        if provider:
            queryset = queryset.filter(provider=provider)

        search = self._search_term()
        if search:
            search_query = (
                Q(file_name__icontains=search)
                | Q(error_message__icontains=search)
                | Q(budget__customer__name__icontains=search)
                | Q(budget__vehicle__plate__icontains=search)
            )
            if search.isdigit():
                search_query |= Q(cilia_number=int(search))
            queryset = queryset.filter(search_query)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_jobs = XMLImportJob.objects.all()
        context['status_filter'] = self._status_filter()
        context['provider_filter'] = self._provider_filter()
        context['search_filter'] = self._search_term()
        context['status_choices'] = XMLImportJob.Status.choices
        context['provider_choices'] = XMLImportJob.Provider.choices
        context['job_counts'] = {
            'total': all_jobs.count(),
            'pending': all_jobs.filter(status=XMLImportJob.Status.PENDING).count(),
            'processing': all_jobs.filter(status=XMLImportJob.Status.PROCESSING).count(),
            'imported': all_jobs.filter(status=XMLImportJob.Status.IMPORTED).count(),
            'duplicate': all_jobs.filter(status=XMLImportJob.Status.DUPLICATE).count(),
            'error': all_jobs.filter(status=XMLImportJob.Status.ERROR).count(),
        }
        query = self.request.GET.copy()
        query.pop('page', None)
        context['current_query_without_page'] = query.urlencode()
        return context


class XMLImportJobDetailView(LoginRequiredMixin, RoleRequiredMixin, DetailView):
    model = XMLImportJob
    template_name = 'budgets/import_job_detail.html'
    context_object_name = 'job'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related('budget', 'budget__customer', 'budget__vehicle')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job = context['job']
        context['can_reprocess'] = bool(
            job.raw_xml and job.status in (XMLImportJob.Status.ERROR, XMLImportJob.Status.DUPLICATE)
        )
        context['can_delete_duplicate'] = job.status == XMLImportJob.Status.DUPLICATE
        return context


class XMLImportJobReprocessView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def post(self, request, pk):
        job = (
            XMLImportJob.objects
            .select_related('budget', 'budget__customer', 'budget__vehicle')
            .filter(pk=pk)
            .first()
        )
        if job is None:
            raise Http404('Importação não encontrada.')

        if not (job.raw_xml or '').strip():
            messages.error(request, 'Este job não possui o XML bruto salvo para reprocessamento.')
            return redirect('budgets:import_job_detail', pk=job.pk)

        try:
            result = import_cilia_xml_bytes(
                xml_bytes=job.raw_xml.encode('utf-8', errors='replace'),
                job=job,
            )
        except CiliaImportDuplicateError as exc:
            messages.error(request, f'Importação continua duplicada. {exc}')
            return redirect('budgets:import_job_detail', pk=job.pk)
        except CiliaImportValidationError as exc:
            messages.error(request, f'Não foi possível reprocessar o XML. {exc}')
            return redirect('budgets:import_job_detail', pk=job.pk)
        except CiliaImportError as exc:
            messages.error(request, f'Não foi possível reprocessar o XML. {exc}')
            return redirect('budgets:import_job_detail', pk=job.pk)

        if not result.parsed_customer_document:
            messages.warning(
                request,
                'XML sem CPF/CNPJ do cliente. Cadastro temporário criado (edite o cliente e informe o documento).',
            )

        messages.success(
            request,
            f'Importação reprocessada com sucesso para o orçamento #{result.budget.display_number}.',
        )
        return redirect('budgets:import_job_detail', pk=job.pk)


class XMLImportJobDeleteDuplicateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def post(self, request, pk):
        job = XMLImportJob.objects.filter(pk=pk).first()
        if job is None:
            raise Http404('Importação não encontrada.')

        if job.status != XMLImportJob.Status.DUPLICATE:
            messages.error(request, 'Somente registros duplicados podem ser excluídos por esta ação.')
            return redirect('budgets:import_job_detail', pk=job.pk)

        file_name = job.file_name
        job.delete()
        messages.success(request, f'Registro duplicado "{file_name}" excluído da listagem.')

        next_url = (request.POST.get('next') or '').strip()
        if next_url:
            return redirect(next_url)
        return redirect('budgets:import_job_list')
