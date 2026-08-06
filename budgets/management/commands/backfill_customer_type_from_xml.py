from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from budgets.cilia_parser import _infer_customer_type
from budgets.models import Budget


class Command(BaseCommand):
    help = (
        'Preenche customer_type em orçamentos antigos a partir do XML original '
        '(campo source_xml) ou reprocessando a detecção via documento. '
        'Não sobrescreve orçamentos que já possuem customer_type definido.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Apenas simula, não grava nada no banco.',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Sobrescreve também orçamentos que já têm customer_type.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options.get('dry_run')
        overwrite = options.get('overwrite')

        qs = Budget.objects.select_related('customer').order_by('id')
        if not overwrite:
            qs = qs.filter(customer_type__isnull=True)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING(
                'Nenhum orçamento encontrado para processar.'
            ))
            return

        self.stdout.write(f'Processando {total} orçamento(s)...')
        if dry_run:
            self.stdout.write(self.style.WARNING('(MODO SIMULAÇÃO --dry-run)'))

        processed = 0
        updated = 0
        already = 0
        errors = 0

        for budget in qs.iterator():
            processed += 1
            try:
                inferred = None

                # Caminho 1: reprocessa o XML salvo, se existir
                if budget.source_xml:
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(budget.source_xml.encode('utf-8') if isinstance(budget.source_xml, str) else budget.source_xml)
                        doc = (budget.customer.document_cpf_cnpj if budget.customer else '') or ''
                        inferred = _infer_customer_type(root, doc)
                    except Exception:
                        inferred = None

                # Caminho 2: fallback por documento se XML não ajudou
                if not inferred and budget.customer and budget.customer.document_cpf_cnpj:
                    digits = ''.join(ch for ch in budget.customer.document_cpf_cnpj if ch.isdigit())
                    if len(digits) == 14:
                        inferred = 'COMPANY'
                    elif digits:
                        inferred = 'PARTICULAR'

                if not inferred:
                    continue

                if budget.customer_type and not overwrite:
                    already += 1
                    continue

                if budget.customer_type == inferred:
                    already += 1
                    continue

                if not dry_run:
                    budget.customer_type = inferred
                    budget.save(update_fields=['customer_type'])

                updated += 1
                label = dict(Budget.CustomerType.choices).get(inferred, inferred)
                self.stdout.write(
                    f'  [#{budget.display_number or budget.id}] {budget.customer.name if budget.customer else "?"}'
                    f' -> {label}'
                    + (self.style.SUCCESS(' (OK)') if not dry_run else self.style.WARNING(' (simulado)'))
                )
            except Exception as exc:
                errors += 1
                self.stderr.write(
                    self.style.ERROR(
                        f'  Erro orçamento #{budget.display_number or budget.id}: {exc}'
                    )
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Concluído.'))
        self.stdout.write(f'  Processados: {processed}')
        self.stdout.write(f'  Atualizados: {updated}' + (self.style.WARNING(' (dry-run)') if dry_run else ''))
        if already:
            self.stdout.write(f'  Já corretos / ignorados: {already}')
        if errors:
            self.stdout.write(self.style.ERROR(f'  Com erros: {errors}'))
