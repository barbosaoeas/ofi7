from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import OperationalError, transaction
from django.db.models import Exists, OuterRef
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, UpdateView
from calendar import monthrange
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import time
from uuid import uuid4
from xml.etree import ElementTree
from django.db.models import Q

from customers.models import Customer, Vehicle
from core.views import RoleRequiredMixin
from users.models import Collaborator, CustomUser

from .cilia_parser import extract_service_lines, extract_tag_names, parse_cilia_xml
from .forms import CiliaXMLUploadForm, PieceForm, ServiceCatalogForm, ThirdPartyServiceForm
from .models import Budget, BudgetPhoto, CashCategory, CashMovement, CommissionLine, Piece, ServiceCatalog, ThirdPartyService, WorkOrder, WorkOrderTask


KANBAN_CUTOFF_TIME = dt_time(17, 48)


def budget_has_pending_shop_parts(budget):
    if not budget or not getattr(budget, 'id', None):
        return False
    return Piece.objects.filter(
        budget_id=budget.id,
        provider_type=Piece.ProviderType.SHOP,
        arrived=False,
        arrival_date__isnull=True,
    ).exists()


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

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related('customer', 'vehicle')
            .filter(status=Budget.Status.AUTHORIZED)
            .order_by('-approved_at', '-created_at')
        )


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
        category_id = (request.GET.get('category') or '').strip()
        try:
            category_id_int = int(category_id) if category_id else None
        except ValueError:
            category_id_int = None
        return {
            'today': today,
            'start': start,
            'end': end,
            'direction': direction,
            'realized': realized,
            'category_id': category_id_int,
        }

    def _build_context(self, request, f):
        qs = CashMovement.objects.select_related('budget', 'category', 'budget__customer', 'budget__vehicle').all()
        qs = qs.filter(due_date__gte=f['start'], due_date__lte=f['end'])
        if f['direction']:
            qs = qs.filter(direction=f['direction'])
        if f['realized'] == '1':
            qs = qs.filter(is_realized=True)
        if f['realized'] == '0':
            qs = qs.filter(is_realized=False)
        if f['category_id']:
            qs = qs.filter(category_id=f['category_id'])
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
        edit_id_raw = (request.GET.get('edit') or '').strip()
        try:
            edit_id = int(edit_id_raw) if edit_id_raw else None
        except ValueError:
            edit_id = None
        edit_movement = None
        if edit_id:
            edit_movement = (
                CashMovement.objects.select_related('budget', 'category', 'budget__customer', 'budget__vehicle')
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
            if source not in (CashMovement.Source.CUSTOMER, CashMovement.Source.INSURER, CashMovement.Source.OTHER):
                source = CashMovement.Source.OTHER
            description = (request.POST.get('description') or '').strip()
            due_date = self._parse_date(request.POST.get('due_date'), timezone.localdate())
            is_realized = (request.POST.get('is_realized') or '').strip().lower() in ('1', 'true', 'on', 'yes')
            category_id_raw = (request.POST.get('category_id') or '').strip()
            try:
                category_id = int(category_id_raw) if category_id_raw else None
            except ValueError:
                category_id = None

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
                        category_id=category_id,
                        description=(description or 'Entrada manual').strip(),
                        amount=entry_amount,
                        due_date=due_date,
                        is_realized=is_realized,
                        realized_at=timezone.now() if is_realized else None,
                    )
                    CashMovement.objects.create(
                        direction=direction,
                        source=source,
                        category_id=category_id,
                        description=f'{(description or "Entrada manual").strip()} - Saldo',
                        amount=balance_amount,
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
                        category_id=category_id,
                        description=description,
                        amount=amount,
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
            movement.category_id = category_id
            movement.description = description
            movement.amount = amount
            movement.due_date = due_date
            movement.is_realized = is_realized
            movement.realized_at = timezone.now() if is_realized else None
            movement.save(
                update_fields=[
                    'direction',
                    'source',
                    'category',
                    'description',
                    'amount',
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


class FinanceInsightsView(FinanceDashboardView):
    template_name = 'budgets/finance_insights.html'

    def get(self, request):
        today = timezone.localdate()
        start_month = date(today.year, today.month, 1)
        end_month = add_months(start_month, 1) - timedelta(days=1)

        range_key = (request.GET.get('range') or '').strip().lower()
        if range_key not in ('month', '3m', '12m'):
            range_key = 'month'
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
        overdue_category_totals = {}
        for movement in overdue_qs.iterator():
            label = movement.category.name if movement.category else 'Sem tipo'
            overdue_category_totals[label] = overdue_category_totals.get(label, Decimal('0')) + (movement.amount or Decimal('0'))
        overdue_top_categories = sorted(overdue_category_totals.items(), key=lambda item: item[1], reverse=True)[:8]
        overdue_items = list(overdue_qs[:10])

        context = {
            'today': today,
            'range_start': range_start,
            'range_end': range_end,
            'range_key': range_key,
            'month_labels': month_labels,
            'expected_in_series': expected_in_series,
            'expected_out_series': expected_out_series,
            'realized_in_series': realized_in_series,
            'realized_out_series': realized_out_series,
            'category_labels': [name for name, _ in top_categories],
            'category_values': [float(value) for _, value in top_categories],
            'weekly_labels': weekly_labels,
            'weekly_expected_in': weekly_expected_in,
            'weekly_expected_out': weekly_expected_out,
            'weekly_realized_in': weekly_realized_in,
            'weekly_realized_out': weekly_realized_out,
            'overdue_items': overdue_items,
            'overdue_category_labels': [name for name, _ in overdue_top_categories],
            'overdue_category_values': [float(value) for _, value in overdue_top_categories],
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
        for task in context.get('tasks', []):
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
            .exclude(id__in=list(busy_work_order_ids))
            .annotate(
                has_late_parts=Exists(
                    Piece.objects.filter(
                        budget_id=OuterRef('budget_id'),
                        arrived=False,
                        expected_arrival_date__isnull=False,
                        expected_arrival_date__lt=today,
                    )
                )
            )
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

        budget = getattr(getattr(task, 'work_order', None), 'budget', None)
        if budget and not bool(getattr(budget, 'allow_repair_without_parts', False)):
            has_pending_shop_parts = budget_has_pending_shop_parts(budget)
            if has_pending_shop_parts:
                messages.warning(
                    request,
                    'Existem peças da oficina pendentes neste orçamento. O reparo está bloqueado até as peças chegarem '
                    'ou até liberar "seguir sem as peças".',
                )
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
        tasks_open = self.object.tasks.exclude(status=WorkOrderTask.Status.DONE).select_related('collaborator')
        tasks_done = self.object.tasks.filter(status=WorkOrderTask.Status.DONE).select_related('collaborator')

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
        return context


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
            collaborator = Collaborator.objects.filter(
                pk=collaborator_id,
                function=Collaborator.Function.OPERATIONAL,
            ).first()
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
                    budget = getattr(getattr(task, 'work_order', None), 'budget', None)
                    if budget and not bool(getattr(budget, 'allow_repair_without_parts', False)) and budget_has_pending_shop_parts(budget):
                        had_error = True
                        messages.error(
                            request,
                            'Existem peças da oficina pendentes neste orçamento. O reparo está bloqueado até as peças chegarem '
                            'ou até liberar "seguir sem as peças".',
                        )
                task.status = status
                update_fields.append('status')

        if update_fields and not had_error:
            task.save(update_fields=sorted(set(update_fields)))
            messages.success(request, 'Agendamento salvo.')

        return redirect('budgets:workorder_detail', pk=task.work_order_id)


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
        context['pieces_parts'] = self.object.pieces.all()
        context['photos'] = self.object.photos.all()
        context['third_party_form'] = ThirdPartyServiceForm()
        context['today'] = timezone.localdate()
        try:
            context['work_order'] = self.object.work_order
        except WorkOrder.DoesNotExist:
            context['work_order'] = None

        manual_third_party = [
            {'description': s.description, 'total_amount': s.amount}
            for s in self.object.third_party_services.all()
        ]
        manual_third_party_total = sum([s['total_amount'] for s in manual_third_party], Decimal('0'))

        xml = self.object.source_xml or ''
        if xml:
            try:
                service_lines = extract_service_lines(xml.encode('utf-8', errors='replace'))
                third_party_xml = [s for s in service_lines if s.get('is_third_party')]
                context['service_lines'] = [s for s in service_lines if not s.get('is_third_party')]
                third_party_xml_total = sum([s.get('total_amount', Decimal('0')) for s in third_party_xml], Decimal('0'))
                context['third_party_services'] = manual_third_party + third_party_xml
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

        third_party_total = sum(
            [s.amount for s in self.object.third_party_services.all().only('amount')],
            Decimal('0'),
        )
        return base_total + third_party_total

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['computed_total_amount'] = self._compute_total_amount()
        context['today'] = timezone.localdate()
        can_access_finance = bool(
            getattr(self.request, 'user', None)
            and getattr(self.request.user, 'role', None)
            in (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)
        )
        needs_finance = bool(
            can_access_finance
            and self.object.status == Budget.Status.AUTHORIZED
            and not CashMovement.objects.filter(budget=self.object).exists()
        )
        context['needs_finance'] = needs_finance
        show_finance_modal = (self.request.GET.get('finance') or '').strip() == '1'
        show_finance_modal = bool(
            can_access_finance
            and show_finance_modal
            and self.object.status == Budget.Status.AUTHORIZED
            and not CashMovement.objects.filter(budget=self.object).exists()
        )
        context['show_finance_modal'] = show_finance_modal
        context['finance_default_due_date'] = self.object.expected_delivery_date or self.object.entry_date or timezone.localdate()
        return context

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
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
        if self.object.status == Budget.Status.AUTHORIZED and not WorkOrder.objects.filter(budget=self.object).exists():
            xml = self.object.source_xml or ''
            vehicle_image_url = ''
            if getattr(self.object.vehicle, 'image_file', None):
                try:
                    if self.object.vehicle.image_file:
                        vehicle_image_url = self.object.vehicle.image_file.url
                except Exception:
                    vehicle_image_url = ''
            if not vehicle_image_url:
                vehicle_image_url = self.object.vehicle.image_url or ''
            work_order = WorkOrder.objects.create(
                budget=self.object,
                vehicle_image_url=vehicle_image_url,
                created_at=self.object.created_at,
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

        if transitioned_to_authorized and not CashMovement.objects.filter(budget=self.object).exists():
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

        if budget.status != Budget.Status.AUTHORIZED:
            messages.error(request, 'O orçamento precisa estar Autorizado para registrar o financeiro.')
            return redirect('budgets:budget_update', pk=budget.pk)

        if CashMovement.objects.filter(budget=budget).exists():
            messages.error(request, 'Financeiro deste orçamento já foi registrado.')
            return redirect('budgets:budget_update', pk=budget.pk)

        kind = (request.POST.get('kind') or '').strip().upper()
        total = budget.total_amount or Decimal('0')
        today = timezone.localdate()

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
                            direction=CashMovement.Direction.IN,
                            source=CashMovement.Source.CUSTOMER,
                            description=f'Orçamento #{budget.display_number} - Entrada',
                            amount=entry_amount,
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
                            direction=CashMovement.Direction.IN,
                            source=CashMovement.Source.CUSTOMER,
                            description=f'Orçamento #{budget.display_number} - Saldo',
                            amount=remainder,
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
                            direction=CashMovement.Direction.IN,
                            source=CashMovement.Source.CUSTOMER,
                            description=f'Orçamento #{budget.display_number} - Franquia',
                            amount=franchise_amount,
                            due_date=franchise_due,
                            is_realized=franchise_received,
                            realized_at=timezone.now() if franchise_received else None,
                        )
                    if insurer_amount > 0:
                        CashMovement.objects.create(
                            budget=budget,
                            direction=CashMovement.Direction.IN,
                            source=CashMovement.Source.INSURER,
                            description=f'Orçamento #{budget.display_number} - Seguradora',
                            amount=insurer_amount,
                            due_date=insurer_due,
                            is_realized=False,
                        )
                else:
                    raise ValueError('Tipo inválido.')
        except Exception as exc:
            messages.error(request, str(exc) or 'Não foi possível registrar o financeiro.')
            return redirect(f'{reverse("budgets:budget_update", kwargs={"pk": budget.pk})}?finance=1')

        messages.success(request, 'Financeiro registrado.')
        return redirect('budgets:budget_detail', pk=budget.pk)


class BudgetDeleteView(LoginRequiredMixin, RoleRequiredMixin, DeleteView):
    model = Budget
    template_name = 'budgets/budget_confirm_delete.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

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

        form = ThirdPartyServiceForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Não foi possível salvar o serviço terceiro.')
            return redirect('budgets:budget_detail', pk=budget.pk)

        ThirdPartyService.objects.create(
            budget=budget,
            description=form.cleaned_data['description'],
            amount=form.cleaned_data['amount'],
        )

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

        third_party_total = sum(
            [s.amount for s in ThirdPartyService.objects.filter(budget_id=budget.id).only('amount')],
            Decimal('0'),
        )
        budget.total_amount = base_total + third_party_total
        budget.save(update_fields=['total_amount'])
        messages.success(request, 'Serviço terceiro salvo.')
        return redirect('budgets:budget_detail', pk=budget.pk)


class CiliaXMLImportView(LoginRequiredMixin, RoleRequiredMixin, FormView):
    template_name = 'budgets/import_xml.html'
    form_class = CiliaXMLUploadForm
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.ESTIMATOR)

    def _xml_debug_summary(self, xml_bytes):
        try:
            root = ElementTree.fromstring(xml_bytes)
        except ElementTree.ParseError:
            return 'XML inválido'

        cpf_values = []
        for el in root.iter():
            tag = str(el.tag).split('}')[-1].lower() if el.tag else ''
            if tag != 'cpf':
                continue
            raw = ''.join(el.itertext()).strip()
            raw = raw or el.attrib.get('value', '')
            cpf_values.append(raw.strip())

        plate_values = []
        for el in root.iter():
            tag = str(el.tag).split('}')[-1].lower() if el.tag else ''
            if tag != 'placa':
                continue
            raw = ''.join(el.itertext()).strip()
            raw = raw or el.attrib.get('value', '')
            plate_values.append(raw.strip())

        cpf_status = 'não encontrado'
        if cpf_values:
            cpf_status = 'encontrado vazio'
            for v in cpf_values:
                digits = ''.join([c for c in v if c.isdigit()])
                if digits:
                    cpf_status = f'encontrado ({len(digits)} dígitos)'
                    break

        plate_status = 'não encontrado'
        if plate_values:
            plate_status = 'encontrado vazio'
            for v in plate_values:
                normalized = ''.join([c for c in v.upper() if c.isalnum()])
                if normalized:
                    plate_status = f'encontrado ({len(normalized)} chars)'
                    break

        return f'Debug: cpf={cpf_status}; placa={plate_status}'

    def form_valid(self, form):
        xml_file = form.cleaned_data['xml_file']
        xml_bytes = xml_file.read()
        xml_created_at = parse_xml_created_at(xml_bytes)

        try:
            (
                parsed_customer,
                parsed_vehicle,
                parsed_pieces,
                parsed_total_amount,
                breakdown,
                cilia_number,
                cilia_version,
            ) = parse_cilia_xml(xml_bytes)
        except ElementTree.ParseError:
            form.add_error('xml_file', 'Não foi possível ler o XML. Verifique o arquivo.')
            return self.form_invalid(form)
        except Exception:
            form.add_error('xml_file', 'Erro ao processar o XML. Tente novamente.')
            return self.form_invalid(form)

        if cilia_number and Budget.objects.filter(cilia_number=cilia_number).exists():
            form.add_error('xml_file', f'Este XML já foi importado (Orçamento #{cilia_number}).')
            return self.form_invalid(form)

        if not parsed_vehicle.plate:
            tags = []
            try:
                tags = extract_tag_names(xml_bytes)
            except Exception:
                tags = []
            details = 'XML sem placa do veículo.'
            details = f'{details} {self._xml_debug_summary(xml_bytes)}'
            if tags:
                details = f'{details} Tags detectadas: {", ".join(tags)}'
            form.add_error(None, details)
            return self.form_invalid(form)

        budget = None
        for attempt in range(6):
            try:
                with transaction.atomic():
                    customer = None
                    vehicle = Vehicle.objects.filter(plate=parsed_vehicle.plate).select_related('customer').first()

                    if parsed_customer.document_cpf_cnpj:
                        customer, _ = Customer.objects.get_or_create(
                            document_cpf_cnpj=parsed_customer.document_cpf_cnpj,
                            defaults={
                                'name': parsed_customer.name,
                                'phone': parsed_customer.phone,
                                'email': parsed_customer.email,
                            },
                        )
                    else:
                        if vehicle is not None:
                            customer = vehicle.customer
                        else:
                            customer = Customer.objects.create(
                                name=parsed_customer.name,
                                document_cpf_cnpj=f'TEMP-{uuid4().hex[:12]}',
                                phone=parsed_customer.phone,
                                email=parsed_customer.email,
                            )

                    if vehicle is None:
                        vehicle = Vehicle.objects.create(
                            customer=customer,
                            plate=parsed_vehicle.plate,
                            brand=parsed_vehicle.brand,
                            model=parsed_vehicle.model,
                            color=parsed_vehicle.color,
                            year=parsed_vehicle.year,
                            image_url=parsed_vehicle.image_url,
                        )
                    else:
                        if parsed_customer.document_cpf_cnpj and vehicle.customer_id != customer.id:
                            vehicle.customer = customer
                            vehicle.save(update_fields=['customer'])

                    if cilia_number:
                        budget = Budget.objects.filter(cilia_number=cilia_number).select_for_update().first()
                        if budget:
                            budget.customer = customer
                            budget.vehicle = vehicle
                            budget.cilia_version = cilia_version
                            budget.pieces.all().delete()
                        else:
                            budget = Budget.objects.create(
                                customer=customer,
                                vehicle=vehicle,
                                cilia_number=cilia_number,
                                cilia_version=cilia_version,
                            )
                    else:
                        budget = Budget.objects.create(customer=customer, vehicle=vehicle)
                    budget.source_xml = xml_bytes.decode('utf-8', errors='replace')

                    total = Decimal('0')
                    for p in parsed_pieces:
                        Piece.objects.create(
                            budget=budget,
                            name=p.name,
                            cost_price=p.cost_price,
                            provider_type=p.provider_type,
                        )
                        total += p.cost_price

                    third_party_total = sum(
                        [s.amount for s in budget.third_party_services.all().only('amount')],
                        Decimal('0'),
                    )
                    budget.total_amount = (parsed_total_amount if parsed_total_amount > 0 else total) + third_party_total
                    budget.shop_parts_total = breakdown.get('shop_parts_total', Decimal('0'))
                    budget.services_total = breakdown.get('services_total', Decimal('0'))
                    budget.labor_total = breakdown.get('labor_total', Decimal('0'))
                    budget.discount_total = breakdown.get('discount_total', Decimal('0'))
                    budget.markup_total = breakdown.get('markup_total', Decimal('0'))
                    budget.save(
                        update_fields=[
                            'customer',
                            'vehicle',
                            'cilia_number',
                            'cilia_version',
                            'total_amount',
                            'shop_parts_total',
                            'services_total',
                            'labor_total',
                            'discount_total',
                            'markup_total',
                            'source_xml',
                        ]
                    )
                    if xml_created_at is not None:
                        budget.created_at = xml_created_at
                        budget.save(update_fields=['created_at'])
                break
            except OperationalError as exc:
                if 'locked' not in str(exc).lower():
                    raise
                if attempt >= 5:
                    form.add_error(None, 'Banco de dados ocupado (SQLite). Tente novamente em alguns segundos.')
                    return self.form_invalid(form)
                time.sleep(0.2 * (attempt + 1))

        if not parsed_customer.document_cpf_cnpj:
            messages.warning(
                self.request,
                'XML sem CPF/CNPJ do cliente. Cadastro temporário criado (edite o cliente e informe o documento).',
            )
        if budget is None:
            form.add_error(None, 'Não foi possível concluir a importação. Tente novamente.')
            return self.form_invalid(form)

        messages.success(self.request, f'Orçamento importado com sucesso (ID: {budget.pk}).')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('budgets:budget_list')
