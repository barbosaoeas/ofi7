from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import time
from uuid import uuid4
from xml.etree import ElementTree

from django.db import OperationalError, transaction
from django.utils import timezone

from customers.models import Customer, Vehicle

from ..cilia_parser import extract_service_lines, extract_tag_names, parse_cilia_xml
from ..models import Budget, Piece


class CiliaImportError(Exception):
    pass


class CiliaImportValidationError(CiliaImportError):
    pass


class CiliaImportDuplicateError(CiliaImportValidationError):
    pass


@dataclass(frozen=True)
class CiliaImportResult:
    budget: Budget
    customer: Customer
    vehicle: Vehicle
    parsed_customer_document: str
    cilia_number: int | None
    cilia_version: int | None
    file_hash: str
    xml_created_at: datetime | None


def normalize_service_description(description):
    return (description or '').strip().lower()


def get_budget_service_lines_from_xml(xml_text):
    xml = (xml_text or '').strip()
    if not xml:
        return []
    try:
        return extract_service_lines(xml.encode('utf-8', errors='replace'))
    except Exception:
        return []


def get_budget_xml_manual_services_map_from_xml(xml_text):
    manual_services = {}
    for line in get_budget_service_lines_from_xml(xml_text):
        manual_amount = line.get('manual_amount', Decimal('0')) or Decimal('0')
        description = line.get('description') or ''
        if manual_amount <= 0 or not description:
            continue
        manual_services[normalize_service_description(description)] = line
    return manual_services


def get_budget_extra_third_party_total_from_xml(budget, xml_text):
    xml_manual_services = get_budget_xml_manual_services_map_from_xml(xml_text)
    total = Decimal('0')
    for service in budget.third_party_services.all().only('description', 'amount'):
        if normalize_service_description(service.description) in xml_manual_services:
            continue
        total += service.amount or Decimal('0')
    return total


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
                parsed_date = date.fromisoformat('-'.join(reversed(date_part.split('/'))))
                if time_part:
                    try:
                        hhmmss = time_part.split(':')
                        hh = int(hhmmss[0])
                        mm = int(hhmmss[1]) if len(hhmmss) > 1 else 0
                        ss = int(hhmmss[2]) if len(hhmmss) > 2 else 0
                        return datetime(parsed_date.year, parsed_date.month, parsed_date.day, hh, mm, ss)
                    except Exception:
                        return datetime(parsed_date.year, parsed_date.month, parsed_date.day)
                return datetime(parsed_date.year, parsed_date.month, parsed_date.day)
            except Exception:
                return None
        return None

    def iter_candidates():
        for element in root.iter():
            if element is None or element.tag is None:
                continue
            tag = str(element.tag).split('}')[-1].lower()
            yield tag, element

    for tag, element in iter_candidates():
        if tag not in candidates:
            continue

        raw = ''.join(element.itertext()).strip()
        raw = raw or element.attrib.get('value', '')
        dt = parse_raw(raw)
        if dt is None:
            continue

        current_timezone = timezone.get_current_timezone()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, current_timezone)
        dt_local = timezone.localtime(dt, current_timezone)
        if dt_local.year < 2000 or dt_local.year > (timezone.localdate().year + 1):
            continue
        return dt_local

    for tag, element in iter_candidates():
        if 'data' not in tag and not tag.startswith('dt'):
            continue
        if not ('orc' in tag or 'cria' in tag or 'emiss' in tag):
            continue
        if tag in candidates:
            continue

        raw = ''.join(element.itertext()).strip()
        raw = raw or element.attrib.get('value', '')
        dt = parse_raw(raw)
        if dt is None:
            continue

        current_timezone = timezone.get_current_timezone()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, current_timezone)
        dt_local = timezone.localtime(dt, current_timezone)
        if dt_local.year < 2000 or dt_local.year > (timezone.localdate().year + 1):
            continue
        return dt_local

    return None


def xml_debug_summary(xml_bytes):
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return 'XML inválido'

    cpf_values = []
    for element in root.iter():
        tag = str(element.tag).split('}')[-1].lower() if element.tag else ''
        if tag != 'cpf':
            continue
        raw = ''.join(element.itertext()).strip()
        raw = raw or element.attrib.get('value', '')
        cpf_values.append(raw.strip())

    plate_values = []
    for element in root.iter():
        tag = str(element.tag).split('}')[-1].lower() if element.tag else ''
        if tag != 'placa':
            continue
        raw = ''.join(element.itertext()).strip()
        raw = raw or element.attrib.get('value', '')
        plate_values.append(raw.strip())

    cpf_status = 'não encontrado'
    if cpf_values:
        cpf_status = 'encontrado vazio'
        for value in cpf_values:
            digits = ''.join([character for character in value if character.isdigit()])
            if digits:
                cpf_status = f'encontrado ({len(digits)} dígitos)'
                break

    plate_status = 'não encontrado'
    if plate_values:
        plate_status = 'encontrado vazio'
        for value in plate_values:
            normalized = ''.join([character for character in value.upper() if character.isalnum()])
            if normalized:
                plate_status = f'encontrado ({len(normalized)} chars)'
                break

    return f'Debug: cpf={cpf_status}; placa={plate_status}'


def update_import_job(job, **changes):
    if job is None:
        return

    changed_fields = []
    for field, value in changes.items():
        setattr(job, field, value)
        changed_fields.append(field)
    if changed_fields:
        job.save(update_fields=changed_fields)


def import_cilia_xml_bytes(
    *,
    xml_bytes,
    job=None,
    allow_existing_cilia_number=False,
    lock_retry_attempts=6,
):
    xml_text = (xml_bytes or b'').decode('utf-8', errors='replace')
    file_hash = sha256(xml_bytes or b'').hexdigest()

    update_import_job(
        job,
        status='PROCESSING',
        file_hash=file_hash,
        raw_xml=xml_text,
        error_message='',
    )

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
            parsed_customer_type,
        ) = parse_cilia_xml(xml_bytes)
    except ElementTree.ParseError as exc:
        update_import_job(
            job,
            status='ERROR',
            error_message='Não foi possível ler o XML. Verifique o arquivo.',
            processed_at=timezone.now(),
        )
        raise CiliaImportValidationError('Não foi possível ler o XML. Verifique o arquivo.') from exc
    except Exception as exc:
        update_import_job(
            job,
            status='ERROR',
            error_message='Erro ao processar o XML. Tente novamente.',
            processed_at=timezone.now(),
        )
        raise CiliaImportError('Erro ao processar o XML. Tente novamente.') from exc

    if cilia_number and Budget.objects.filter(cilia_number=cilia_number).exists() and not allow_existing_cilia_number:
        message = f'Este XML já foi importado (Orçamento #{cilia_number}).'
        update_import_job(
            job,
            cilia_number=cilia_number,
            status='DUPLICATE',
            error_message=message,
            processed_at=timezone.now(),
        )
        raise CiliaImportDuplicateError(message)

    if not parsed_vehicle.plate:
        tags = []
        try:
            tags = extract_tag_names(xml_bytes)
        except Exception:
            tags = []
        details = 'XML sem placa do veículo.'
        details = f'{details} {xml_debug_summary(xml_bytes)}'
        if tags:
            details = f'{details} Tags detectadas: {", ".join(tags)}'
        update_import_job(
            job,
            cilia_number=cilia_number,
            status='ERROR',
            error_message=details,
            processed_at=timezone.now(),
        )
        raise CiliaImportValidationError(details)

    budget = None
    customer = None
    vehicle = None
    for attempt in range(lock_retry_attempts):
        try:
            with transaction.atomic():
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
                elif parsed_customer.document_cpf_cnpj and vehicle.customer_id != customer.id:
                    vehicle.customer = customer
                    vehicle.save(update_fields=['customer'])

                if cilia_number:
                    budget = Budget.objects.filter(cilia_number=cilia_number).select_for_update().first()
                    if budget is not None and not allow_existing_cilia_number:
                        raise CiliaImportDuplicateError(f'Este XML já foi importado (Orçamento #{cilia_number}).')
                    if budget is not None:
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

                budget.source_xml = xml_text

                total = Decimal('0')
                for piece in parsed_pieces:
                    Piece.objects.create(
                        budget=budget,
                        name=piece.name,
                        cost_price=piece.cost_price,
                        provider_type=piece.provider_type,
                    )
                    total += piece.cost_price

                budget.total_amount = (
                    (parsed_total_amount if parsed_total_amount > 0 else total)
                    + get_budget_extra_third_party_total_from_xml(budget, xml_text)
                )
                budget.shop_parts_total = breakdown.get('shop_parts_total', Decimal('0'))
                budget.services_total = breakdown.get('services_total', Decimal('0'))
                budget.labor_total = breakdown.get('labor_total', Decimal('0'))
                budget.discount_total = breakdown.get('discount_total', Decimal('0'))
                budget.markup_total = breakdown.get('markup_total', Decimal('0'))
                if not budget.customer_type and parsed_customer_type:
                    budget.customer_type = parsed_customer_type
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
                        'customer_type',
                    ]
                )
                if xml_created_at is not None:
                    budget.created_at = xml_created_at
                    budget.save(update_fields=['created_at'])
            break
        except CiliaImportDuplicateError as exc:
            update_import_job(
                job,
                cilia_number=cilia_number,
                status='DUPLICATE',
                error_message=str(exc),
                processed_at=timezone.now(),
            )
            raise
        except OperationalError as exc:
            if 'locked' not in str(exc).lower():
                update_import_job(
                    job,
                    cilia_number=cilia_number,
                    status='ERROR',
                    error_message='Erro ao processar o XML. Tente novamente.',
                    processed_at=timezone.now(),
                )
                raise CiliaImportError('Erro ao processar o XML. Tente novamente.') from exc
            if attempt >= (lock_retry_attempts - 1):
                message = 'Banco de dados ocupado (SQLite). Tente novamente em alguns segundos.'
                update_import_job(
                    job,
                    cilia_number=cilia_number,
                    status='ERROR',
                    error_message=message,
                    processed_at=timezone.now(),
                )
                raise CiliaImportError(message) from exc
            time.sleep(0.2 * (attempt + 1))

    if budget is None or customer is None or vehicle is None:
        update_import_job(
            job,
            cilia_number=cilia_number,
            status='ERROR',
            error_message='Não foi possível concluir a importação. Tente novamente.',
            processed_at=timezone.now(),
        )
        raise CiliaImportError('Não foi possível concluir a importação. Tente novamente.')

    update_import_job(
        job,
        budget=budget,
        cilia_number=cilia_number,
        status='IMPORTED',
        processed_at=timezone.now(),
    )

    return CiliaImportResult(
        budget=budget,
        customer=customer,
        vehicle=vehicle,
        parsed_customer_document=parsed_customer.document_cpf_cnpj,
        cilia_number=cilia_number,
        cilia_version=cilia_version,
        file_hash=file_hash,
        xml_created_at=xml_created_at,
    )
