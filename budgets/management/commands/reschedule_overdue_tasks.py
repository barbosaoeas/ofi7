from datetime import date, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from budgets.calendar_utils import (
    KANBAN_CUTOFF_TIME,
    capped_work_delta_seconds,
    get_local_now,
    get_local_today,
    next_weekday_including_saturday,
    seconds_to_hours_decimal,
)
from budgets.models import Budget, WorkOrder, WorkOrderTask


WEEKDAY_NAMES = (
    'segunda-feira',
    'terça-feira',
    'quarta-feira',
    'quinta-feira',
    'sexta-feira',
    'sábado',
    'domingo',
)


class Command(BaseCommand):
    help = (
        'Reprograma tarefas WorkOrderTask nao concluidas do dia para o '
        'proximo dia util (inclui sabado). Deve ser executado apos o '
        f'expediente (KANBAN_CUTOFF_TIME {KANBAN_CUTOFF_TIME.strftime("%H:%M")}).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            help=(
                'Apenas exibe o que seria feito, NAO salva nenhuma '
                'alteracao no banco de dados.'
            ),
        )
        parser.add_argument(
            '--date',
            type=str,
            default='',
            dest='date',
            help=(
                'Executa considerando a data informada (YYYY-MM-DD) como '
                '"hoje" — util para testes retroativos.'
            ),
        )
        parser.add_argument(
            '--summary-only',
            action='store_true',
            dest='summary_only',
            help='Exibe apenas o resumo final (omitindo detalhes por tarefa).',
        )

    def _parse_cli_date(self, raw: str):
        if not raw:
            return None
        try:
            return datetime.strptime(raw.strip(), '%Y-%m-%d').date()
        except ValueError as exc:
            raise CommandError(
                f'--date invalido. Esperado YYYY-MM-DD. Recebido: {raw!r}'
            ) from exc

    def _build_elegible_qs(self, today: date):
        return (
            WorkOrderTask.objects.select_related(
                'work_order',
                'work_order__budget',
                'work_order__budget__customer',
                'work_order__budget__vehicle',
                'collaborator',
            )
            .filter(
                scheduled_date=today,
                scheduled_date__isnull=False,
            )
            .exclude(status=WorkOrderTask.Status.DONE)
            .filter(
                work_order__status=WorkOrder.Status.OPEN,
                work_order__budget__delivered_at__isnull=True,
                work_order__budget__status=Budget.Status.AUTHORIZED,
            )
            .order_by('collaborator__name', 'order', 'id')
        )

    def _format_task_line(self, task: WorkOrderTask) -> str:
        budget = task.work_order.budget
        display = budget.cilia_number or budget.id
        plate = budget.vehicle.plate if budget.vehicle_id else '---'
        customer = budget.customer.name if budget.customer_id else '---'
        colab = task.collaborator.name if task.collaborator_id else 'sem colaborador'
        return (
            f'Task #{task.id:>6} '
            f'| {task.get_activity_display():<11} '
            f'| OS #{display} '
            f'({plate} / {customer}) '
            f'| Colab: {colab} '
            f'| status: {task.get_status_display()}'
        )

    def _move_eligible(self, task: WorkOrderTask, next_day: date, dry_run: bool, now: datetime):
        changed = []
        reasons = []
        new_status = task.status
        truncated_seconds = 0

        if bool(task.allow_overtime) and task.status in {
            WorkOrderTask.Status.RUNNING,
            WorkOrderTask.Status.PAUSED,
        }:
            return False, 'MANTER (allow_overtime=True marcado para hora extra.)', [], 0

        if task.status == WorkOrderTask.Status.RUNNING:
            delta, _end_eff = capped_work_delta_seconds(
                last_started_at=task.last_started_at,
                now=now,
                allow_overtime=False,
            )
            if delta > 0:
                truncated_seconds = delta
                new_elapsed = (task.elapsed_seconds or 0) + delta
                changed.append(('elapsed_seconds', task.elapsed_seconds, new_elapsed))
                task.elapsed_seconds = new_elapsed
                actual_new = (
                    Decimal(str(task.actual_hours or 0))
                    + seconds_to_hours_decimal(delta)
                ).quantize(Decimal('0.01'))
                changed.append(('actual_hours', task.actual_hours, actual_new))
                task.actual_hours = actual_new
                reasons.append(
                    f'+{seconds_to_hours_decimal(delta)}h truncados em '
                    f'{KANBAN_CUTOFF_TIME.strftime("%H:%M")} (expediente encerrado)'
                )
            new_status = WorkOrderTask.Status.PAUSED
            task.last_started_at = None

        if task.status != WorkOrderTask.Status.SCHEDULED:
            if new_status != task.status:
                changed.append(('status', task.status, new_status))
                task.status = new_status

        if task.scheduled_date != next_day:
            changed.append(('scheduled_date', task.scheduled_date, next_day))
            task.scheduled_date = next_day

        if not changed:
            return False, 'nenhuma alteracao necessaria.', reasons, truncated_seconds

        if not dry_run:
            update_fields = sorted({c[0] for c in changed} | {'updated_at'})
            task.save(update_fields=update_fields)

        return True, 'OK', reasons + [f'{c[0]}: {c[1]} → {c[2]}' for c in changed], truncated_seconds

    def handle(self, *args, **options):
        dry_run = bool(options.get('dry_run'))
        summary_only = bool(options.get('summary_only'))
        cli_date = self._parse_cli_date(options.get('date') or '')

        now = get_local_now()
        today = cli_date or get_local_today()
        next_day = next_weekday_including_saturday(today)

        self.stdout.write('')
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                '========  RESCHEDULE_OVERDUE_TASKS ========'
            )
        )
        self.stdout.write(f'Horário now:       {now.strftime("%Y-%m-%d %H:%M:%S %Z")}')
        self.stdout.write(
            f'Dia de referência:  {today.isoformat()} ({WEEKDAY_NAMES[today.weekday()]})'
        )
        self.stdout.write(
            f'Próximo dia útil:   {next_day.isoformat()} ({WEEKDAY_NAMES[next_day.weekday()]})'
        )
        self.stdout.write(
            f'Expediente (cutoff):{KANBAN_CUTOFF_TIME.strftime("%H:%M")}'
        )
        self.stdout.write(
            f'DRY-RUN:            {"SIM (nao salva nada)" if dry_run else "NÃO (salva no banco)"}'
        )
        self.stdout.write(
            'Regras: status DIFERENTE de DONE com scheduled_date hoje, '
            'OS aberta, Budget autorizado/não-entregue. '
            'Tarefas RUNNING/PAUSED com allow_overtime=True são MANTIDAS.'
        )
        self.stdout.write('')

        qs = self._build_elegible_qs(today)
        total = qs.count()

        if total == 0:
            self.stdout.write(
                self.style.SUCCESS('Nenhuma tarefa elegível encontrada hoje. OK.')
            )
            return

        moved = 0
        kept_overtime = 0
        errors = 0
        paused_and_moved = 0
        cumulative_seconds = 0
        lines = []

        for idx, task in enumerate(qs, start=1):
            prefix = f'[{idx:>3}/{total}] '
            line = prefix + self._format_task_line(task)
            if not summary_only:
                self.stdout.write(line)
            try:
                with transaction.atomic():
                    ok, msg, details, seconds = self._move_eligible(
                        task=task,
                        next_day=next_day,
                        dry_run=dry_run,
                        now=now,
                    )
            except Exception as exc:
                errors += 1
                errmsg = f'  ✗ ERRO: {exc!r}'
                if not summary_only:
                    self.stdout.write(self.style.ERROR(errmsg))
                lines.append((line, 'ERRO', [errmsg], 0))
                continue

            lines.append((line, msg, details, seconds))
            if not ok and 'allow_overtime' in msg:
                kept_overtime += 1
            if not ok:
                if not summary_only:
                    self.stdout.write(self.style.WARNING(f'  → {msg}'))
                continue

            moved += 1
            if task.status == WorkOrderTask.Status.PAUSED:
                paused_and_moved += 1
            cumulative_seconds += seconds

            if not summary_only:
                if details:
                    for d in details:
                        self.stdout.write(self.style.WARNING(f'  · {d}'))
                self.stdout.write(
                    self.style.SUCCESS(f'  ✓ {msg}')
                )

        # =============== RESUMO ===============
        self.stdout.write('')
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                '================== RESUMO =================='
            )
        )
        self.stdout.write(f'Total elegíveis .......: {total}')
        self.stdout.write(
            f'  Movidas (reprogramadas): {self.style.SUCCESS(str(moved))}'
        )
        self.stdout.write(
            f'  Movidas + pausadas .....: {self.style.SUCCESS(str(paused_and_moved))}'
        )
        self.stdout.write(
            f'  Mantidas (hora extra) ..: {kept_overtime}'
        )
        self.stdout.write(f'  Erros ..................: {self.style.ERROR(str(errors)) if errors else "0"}')
        if cumulative_seconds:
            self.stdout.write(
                f'  Horas truncadas salvas .: '
                f'{seconds_to_hours_decimal(cumulative_seconds)}h ({cumulative_seconds}s)'
            )
        else:
            self.stdout.write(f'  Horas truncadas salvas .: 0h00')
        self.stdout.write('')
        if dry_run and moved > 0:
            self.stdout.write(
                self.style.WARNING(
                    '⚠️  DRY-RUN ATIVO: NENHUMA alteração foi salva no banco. '
                    'Re-rodar sem --dry-run para efetivar.'
                )
            )
        elif errors > 0:
            raise CommandError(f'{errors} erro(s) durante o reschedule. Ver log acima.')
