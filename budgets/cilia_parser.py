from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from xml.etree import ElementTree


@dataclass(frozen=True)
class ParsedCustomer:
    name: str
    document_cpf_cnpj: str
    phone: str = ''
    email: str = ''


@dataclass(frozen=True)
class ParsedVehicle:
    plate: str
    model: str
    brand: str
    color: str = ''
    year: str = ''
    image_url: str = ''


@dataclass(frozen=True)
class ParsedPiece:
    name: str
    cost_price: Decimal = Decimal('0')
    provider_type: str = 'SHOP'


def _normalize_text(value):
    if value is None:
        return ''
    return str(value).strip()


def _element_text(element):
    if element is None:
        return ''
    return _normalize_text(''.join(element.itertext()))


def _element_value(element):
    if element is None:
        return ''

    text = _element_text(element)
    if text:
        return text

    for key in ('value', 'numero', 'documento'):
        if key in element.attrib:
            return _normalize_text(element.attrib.get(key))

    for value in element.attrib.values():
        normalized = _normalize_text(value)
        if normalized:
            return normalized

    return ''


def _digits_only(value):
    return re.sub(r'\D+', '', _normalize_text(value))


def _normalize_document(value):
    digits = _digits_only(value)
    if len(digits) in {11, 14}:
        return digits
    return _normalize_text(value)


def _normalize_plate(value):
    raw = _normalize_text(value).upper()
    return re.sub(r'[^A-Z0-9]+', '', raw)


def _parse_decimal(value):
    raw = _normalize_text(value)
    if not raw:
        return Decimal('0')

    raw = raw.replace('R$', '').strip()
    raw = raw.replace(' ', '')
    if ',' in raw and '.' in raw:
        last_comma = raw.rfind(',')
        last_dot = raw.rfind('.')
        decimal_sep = ',' if last_comma > last_dot else '.'
        thousand_sep = '.' if decimal_sep == ',' else ','
        raw = raw.replace(thousand_sep, '')
        raw = raw.replace(decimal_sep, '.')
    elif ',' in raw:
        raw = raw.replace('.', '')
        raw = raw.replace(',', '.')
    elif '.' in raw:
        parts = raw.split('.')
        if len(parts) == 2 and len(parts[1]) in {1, 2}:
            pass
        elif len(parts) > 2 and all(len(p) == 3 for p in parts[1:]):
            raw = ''.join(parts)
        elif len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            raw = ''.join(parts)
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return Decimal('0')


def _find_total_amount(root):
    def _find_best_decimal(tag_candidates):
        candidates = {t.lower() for t in tag_candidates}
        best = Decimal('0')
        for el in root.iter():
            if el.tag is None:
                continue
            tag = str(el.tag).split('}')[-1].lower()
            if tag not in candidates:
                continue

            value = _parse_decimal(_find_first_text(el, ['valor']))
            if value > best:
                best = value

            value = _parse_decimal(_element_value(el))
            if value > best:
                best = value

        return best

    def _rates():
        return {
            'labor': _parse_decimal(_find_first_text(root, ['valor_hora_mao_de_obra', 'valor_hora_maodeobra'])),
            'repair': _parse_decimal(_find_first_text(root, ['valor_hora_reparacao', 'valor_hora_reparo'])),
            'paint': _parse_decimal(_find_first_text(root, ['valor_hora_pintura'])),
            'paint_tricoat': _parse_decimal(_find_first_text(root, ['valor_hora_pintura_tricoat'])),
        }

    discount_total = _find_best_decimal(['desconto'])
    markup_total = _find_best_decimal(['majoracao'])

    def _apply_adjustments(base_total):
        adjusted = base_total - discount_total + markup_total
        if adjusted < 0:
            return Decimal('0')
        return adjusted

    explicit_total = _find_best_decimal(['valor_total_liquido_geral', 'valor_total_geral'])
    if explicit_total <= 0:
        explicit_total = _find_best_decimal(['valor_total'])

    if explicit_total > 0:
        shop_parts_total = _find_best_decimal(
            [
                'valor_pecas_pela_oficina',
                'valor_fornecimento_oficina',
                'valor_liquido_pecas',
                'valor_bruto_pecas',
            ]
        )
        services_total = _find_best_decimal(['valor_servico_manual'])
        labor_total = _find_best_decimal(['valor_liquido_mao_de_obra', 'valor_mao_de_obra'])

        computed_total = shop_parts_total + services_total + labor_total
        if computed_total <= 0:
            services_total = explicit_total
        elif computed_total != explicit_total:
            services_total += explicit_total - computed_total
            if services_total < 0:
                services_total = Decimal('0')

        return (
            explicit_total,
            {
                'shop_parts_total': shop_parts_total,
                'services_total': services_total,
                'labor_total': labor_total,
                'discount_total': discount_total,
                'markup_total': markup_total,
            },
        )

    itens_el = _find_first_element(root, ['itens_orcamento', 'itensorcamento', 'itens'])
    item_elements = []
    if itens_el is not None:
        item_elements = [el for el in itens_el.iter() if str(el.tag).split('}')[-1].lower() == 'item']
    else:
        item_elements = [el for el in root.iter() if str(el.tag).split('}')[-1].lower() == 'item']

    rates = _rates()
    shop_parts_total = Decimal('0')
    services_total = Decimal('0')
    labor_total = Decimal('0')
    for item_el in item_elements:
        tipo_item = _find_first_text(item_el, ['tipo_item'])
        tipo_peca = _find_first_text(item_el, ['tipo_peca'])
        fornecimento = _find_first_text(item_el, ['fornecimento'])

        quantity = _parse_decimal(_find_first_text(item_el, ['quantidade']))
        if quantity <= 0:
            quantity = Decimal('1')

        raw_price_liq = _find_first_text(item_el, ['preco_liquido'])
        raw_price = _find_first_text(item_el, ['preco', 'valor'])
        price_liq = _parse_decimal(raw_price_liq)
        unit_price = _parse_decimal(raw_price)
        price_line_total = price_liq if price_liq > 0 else unit_price * quantity

        is_piece = False
        if 'peca' in _normalize_text(tipo_item).lower() or 'peça' in _normalize_text(tipo_item).lower():
            is_piece = True
        if tipo_peca:
            is_piece = True

        provider_type = _parse_provider_type(fornecimento)
        include_item_price = False
        if is_piece:
            include_item_price = provider_type == 'SHOP'
        else:
            include_item_price = True

        if include_item_price and price_line_total > 0:
            if is_piece:
                shop_parts_total += price_line_total
            else:
                services_total += price_line_total

        hours_ri = _parse_decimal(_find_first_text(item_el, ['hora_remocao_instalacao']))
        hours_rep = _parse_decimal(_find_first_text(item_el, ['hora_reparacao']))
        hours_paint = _parse_decimal(_find_first_text(item_el, ['hora_pintura']))

        should_compute_labor = True
        if not is_piece and price_line_total > 0:
            should_compute_labor = False

        if should_compute_labor:
            if hours_ri > 0 and rates['labor'] > 0:
                labor_total += hours_ri * rates['labor']
            if hours_rep > 0 and rates['repair'] > 0:
                labor_total += hours_rep * rates['repair']
            if hours_paint > 0:
                paint_rate = rates['paint_tricoat'] if rates['paint_tricoat'] > 0 else rates['paint']
                if paint_rate > 0:
                    labor_total += hours_paint * paint_rate

    computed_total = shop_parts_total + services_total + labor_total
    if computed_total > 0:
        return (
            _apply_adjustments(computed_total),
            {
                'shop_parts_total': shop_parts_total,
                'services_total': services_total,
                'labor_total': labor_total,
                'discount_total': discount_total,
                'markup_total': markup_total,
            },
        )

    resumo_el = _find_first_element(root, ['resumo_geral'])
    totais_el = None
    if resumo_el is not None:
        totais_el = _find_first_element(resumo_el, ['totais_em_impacto', 'totais'])
    if totais_el is None:
        totais_el = _find_first_element(root, ['totais_em_impacto', 'totais'])

    if totais_el is not None:
        totals_total = Decimal('0')
        for child in list(totais_el):
            if child is None or child.tag is None:
                continue
            child_tag = str(child.tag).split('}')[-1].lower()
            if child_tag in {'valor', 'tempo'}:
                continue
            value = _parse_decimal(_find_first_text(child, ['valor']))
            if value > 0:
                totals_total += value

        manual = _parse_decimal(_find_first_text(totais_el, ['valor_servico_manual']))
        if manual > 0:
            totals_total += manual

        if totals_total > 0:
            return (
                _apply_adjustments(totals_total),
                {
                    'shop_parts_total': Decimal('0'),
                    'services_total': totals_total,
                    'labor_total': Decimal('0'),
                    'discount_total': discount_total,
                    'markup_total': markup_total,
                },
            )

    return (
        Decimal('0'),
        {
            'shop_parts_total': Decimal('0'),
            'services_total': Decimal('0'),
            'labor_total': Decimal('0'),
            'discount_total': discount_total,
            'markup_total': markup_total,
        },
    )


def _find_first_text(root, tag_candidates):
    candidates = {t.lower() for t in tag_candidates}
    for el in root.iter():
        if el.tag is None:
            continue
        tag = str(el.tag).split('}')[-1].lower()
        if tag in candidates:
            text = _element_value(el)
            if text:
                return text
    return ''


def extract_tag_names(xml_bytes, limit=80):
    root = ElementTree.fromstring(xml_bytes)
    tags = []
    seen = set()
    for el in root.iter():
        if el.tag is None:
            continue
        tag = str(el.tag).split('}')[-1]
        if not tag:
            continue
        normalized = tag.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        tags.append(tag)
        if len(tags) >= limit:
            break
    return tags


def _find_first_element(root, tag_candidates):
    candidates = {t.lower() for t in tag_candidates}
    for el in root.iter():
        if el.tag is None:
            continue
        tag = str(el.tag).split('}')[-1].lower()
        if tag in candidates:
            return el
    return None


def _find_document_anywhere(root):
    preferred_tags = {'cpf', 'cnpj', 'cpfcnpj', 'documento', 'document'}

    best = ''
    for el in root.iter():
        if el.tag is None:
            continue
        tag = str(el.tag).split('}')[-1].lower()
        text_candidate = _normalize_document(_element_value(el))
        if tag in preferred_tags and _digits_only(text_candidate):
            digits = _digits_only(text_candidate)
            if len(digits) in {11, 14}:
                return digits
            best = best or digits

        for attr_key, attr_value in el.attrib.items():
            key = _normalize_text(attr_key).lower()
            if 'cpf' in key or 'cnpj' in key or 'document' in key:
                digits = _digits_only(attr_value)
                if len(digits) in {11, 14}:
                    return digits
                best = best or digits

        for attr_value in el.attrib.values():
            digits = _digits_only(attr_value)
            if len(digits) in {11, 14}:
                return digits
            best = best or digits

    if best:
        return best
    return ''


def _parse_provider_type(value):
    raw = _normalize_text(value).lower()
    if not raw:
        return 'INSURER'
    if raw in {'-', '—', 'n/a'}:
        return 'INSURER'
    if raw in {'c', 'cli'}:
        return 'CUSTOMER'
    if raw in {'s', 'seg'}:
        return 'INSURER'
    if raw in {'o', 'of'}:
        return 'SHOP'
    if 'cliente' in raw:
        return 'CUSTOMER'
    if 'segur' in raw:
        return 'INSURER'
    if 'oficina' in raw:
        return 'SHOP'
    return 'INSURER'


def _is_truthy(value):
    raw = _normalize_text(value).lower()
    if raw in {'true', 'sim', 's', 'yes', 'y', '1'}:
        return True
    if raw in {'false', 'nao', 'não', 'n', 'no', '0'}:
        return False
    return _parse_decimal(raw) > 0


def extract_service_lines(xml_bytes):
    root = ElementTree.fromstring(xml_bytes)

    labor_rate = _parse_decimal(_find_first_text(root, ['valor_hora_mao_de_obra', 'valor_hora_maodeobra']))
    repair_rate = _parse_decimal(_find_first_text(root, ['valor_hora_reparacao', 'valor_hora_reparo']))
    paint_rate = _parse_decimal(_find_first_text(root, ['valor_hora_pintura_tricoat']))
    if paint_rate <= 0:
        paint_rate = _parse_decimal(_find_first_text(root, ['valor_hora_pintura']))

    itens_el = _find_first_element(root, ['itens_orcamento', 'itensorcamento', 'itens'])
    if itens_el is not None:
        item_elements = [el for el in itens_el.iter() if str(el.tag).split('}')[-1].lower() == 'item']
    else:
        item_elements = [el for el in root.iter() if str(el.tag).split('}')[-1].lower() == 'item']

    service_lines = []
    for item_el in item_elements:
        tipo_item = _find_first_text(item_el, ['tipo_item'])
        tipo_peca = _find_first_text(item_el, ['tipo_peca'])
        descricao = _find_first_text(item_el, ['descricao', 'nome'])
        codigo = _find_first_text(item_el, ['codigo'])
        troca = _is_truthy(_find_first_text(item_el, ['troca']))

        hours_ri = _parse_decimal(_find_first_text(item_el, ['hora_remocao_instalacao']))
        hours_rep = _parse_decimal(_find_first_text(item_el, ['hora_reparacao']))
        hours_paint = _parse_decimal(_find_first_text(item_el, ['hora_pintura']))

        ri_flag_raw = _find_first_text(item_el, ['remocao_instalacao', 'remocao_e_instalacao'])
        rep_flag_raw = _find_first_text(item_el, ['reparacao', 'reparo'])
        paint_flag_raw = _find_first_text(item_el, ['pintura'])

        if ri_flag_raw and not _is_truthy(ri_flag_raw):
            hours_ri = Decimal('0')
        if rep_flag_raw and not _is_truthy(rep_flag_raw):
            hours_rep = Decimal('0')
        if paint_flag_raw and not _is_truthy(paint_flag_raw):
            hours_paint = Decimal('0')

        quantity = _parse_decimal(_find_first_text(item_el, ['quantidade']))
        if quantity <= 0:
            quantity = Decimal('1')

        raw_price_liq = _find_first_text(item_el, ['preco_liquido'])
        raw_price = _find_first_text(item_el, ['preco', 'valor'])
        price_liq = _parse_decimal(raw_price_liq)
        unit_price = _parse_decimal(raw_price)
        price_line_total = price_liq if price_liq > 0 else unit_price * quantity

        is_piece = False
        if 'peca' in _normalize_text(tipo_item).lower() or 'peça' in _normalize_text(tipo_item).lower():
            is_piece = True
        if tipo_peca:
            is_piece = True

        is_replacement_piece = is_piece and troca
        if is_replacement_piece:
            continue

        is_service = (not is_piece) or (hours_ri > 0) or (hours_rep > 0) or (hours_paint > 0)
        if not is_service:
            continue

        is_manual_service = not is_piece and price_line_total > 0
        half = Decimal('2')
        desmontagem_ratio = Decimal('0.40')
        montagem_ratio = Decimal('0.60')
        desmontagem_hours = hours_ri * desmontagem_ratio if hours_ri > 0 else Decimal('0')
        montagem_hours = hours_ri * montagem_ratio if hours_ri > 0 else Decimal('0')
        funilaria_hours = hours_rep
        preparacao_hours = hours_paint / half if hours_paint > 0 else Decimal('0')
        pintura_hours = hours_paint / half if hours_paint > 0 else Decimal('0')

        desmontagem_amount = desmontagem_hours * labor_rate if desmontagem_hours > 0 and labor_rate > 0 else Decimal('0')
        montagem_amount = montagem_hours * labor_rate if montagem_hours > 0 and labor_rate > 0 else Decimal('0')
        funilaria_amount = funilaria_hours * repair_rate if funilaria_hours > 0 and repair_rate > 0 else Decimal('0')
        preparacao_amount = preparacao_hours * paint_rate if preparacao_hours > 0 and paint_rate > 0 else Decimal('0')
        pintura_amount = pintura_hours * paint_rate if pintura_hours > 0 and paint_rate > 0 else Decimal('0')

        computed_total = desmontagem_amount + montagem_amount + funilaria_amount + preparacao_amount + pintura_amount
        manual_total = price_line_total if is_manual_service else Decimal('0')

        is_third_party = False
        if manual_total > 0 and computed_total <= 0:
            inclusao_manual = _is_truthy(_find_first_text(item_el, ['inclusao_manual', 'inclusaomanual']))
            desc_norm = _normalize_text(descricao).lower()
            third_party_keywords = (
                'lavagem',
                'lavacao',
                'lavação',
                'mecanica',
                'mecânica',
                'mecanico',
                'mecânico',
                'refrigeracao',
                'refrigeração',
                'refrigeraçao',
                'refrig',
                'borracharia',
                'borracheiro',
                'borrach',
                'alinhamento',
                'balanceamento',
                'eletrica',
                'elétrica',
                'eletric',
                'vidro',
                'vidraçaria',
                'vidracaria',
            )
            if inclusao_manual or any(k in desc_norm for k in third_party_keywords):
                is_third_party = True

        service_lines.append(
            {
                'description': descricao,
                'code': codigo,
                'hours_ri': hours_ri,
                'hours_rep': hours_rep,
                'hours_paint': hours_paint,
                'desmontagem_hours': desmontagem_hours,
                'funilaria_hours': funilaria_hours,
                'preparacao_hours': preparacao_hours,
                'pintura_hours': pintura_hours,
                'montagem_hours': montagem_hours,
                'desmontagem_amount': desmontagem_amount,
                'funilaria_amount': funilaria_amount,
                'preparacao_amount': preparacao_amount,
                'pintura_amount': pintura_amount,
                'montagem_amount': montagem_amount,
                'manual_amount': manual_total,
                'total_amount': computed_total + manual_total,
                'is_third_party': is_third_party,
            }
        )

    return service_lines


def parse_cilia_xml(xml_bytes):
    root = ElementTree.fromstring(xml_bytes)
    cilia_number = _digits_only(_find_first_text(root, ['numero_orcamento'])) or ''
    cilia_version = _digits_only(_find_first_text(root, ['versao_orcamento'])) or ''
    cilia_number_value = int(cilia_number) if cilia_number else None
    cilia_version_value = int(cilia_version) if cilia_version else None

    cliente_el = _find_first_element(root, ['cliente'])
    customer_name = _find_first_text(cliente_el or root, ['nomecliente', 'nome', 'razao', 'razaosocial'])
    customer_document = _normalize_document(
        _find_first_text(cliente_el or root, ['cpf', 'cnpj', 'cpfcnpj', 'documento', 'document'])
    )
    if not _digits_only(customer_document):
        customer_document = _find_document_anywhere(root)
    ddd = _find_first_text(cliente_el or root, ['ddd'])
    phone = _find_first_text(cliente_el or root, ['telefone', 'fone', 'celular', 'phone'])
    customer_phone = ''.join([p for p in [ddd, phone] if p])
    customer_email = _find_first_text(cliente_el or root, ['email', 'e-mail'])

    veiculo_el = _find_first_element(root, ['veiculo'])
    plate = _normalize_plate(_find_first_text(veiculo_el or root, ['placa', 'plate']))
    brand = _find_first_text(veiculo_el or root, ['marca', 'brand'])
    model = _find_first_text(veiculo_el or root, ['modelo', 'model', 'nome_veiculo'])
    color = _find_first_text(veiculo_el or root, ['cor', 'color'])
    year = _find_first_text(veiculo_el or root, ['ano', 'year'])

    pieces = []
    itens_el = _find_first_element(root, ['itens_orcamento', 'itensorcamento', 'itens'])
    item_elements = []
    if itens_el is not None:
        item_elements = [el for el in itens_el.iter() if str(el.tag).split('}')[-1].lower() == 'item']
    else:
        item_elements = [el for el in root.iter() if str(el.tag).split('}')[-1].lower() == 'item']

    for item_el in item_elements:
        tipo_item = _find_first_text(item_el, ['tipo_item'])
        tipo_peca = _find_first_text(item_el, ['tipo_peca'])
        fornecimento = _find_first_text(item_el, ['fornecimento'])
        descricao = _find_first_text(item_el, ['descricao', 'nome'])
        troca = _find_first_text(item_el, ['troca'])

        is_piece = False
        if 'peca' in _normalize_text(tipo_item).lower() or 'peça' in _normalize_text(tipo_item).lower():
            is_piece = True
        if tipo_peca:
            is_piece = True
        is_replacement_piece = _is_truthy(troca)

        if not is_piece or not is_replacement_piece:
            continue

        if descricao:
            provider_type = _parse_provider_type(fornecimento)
            raw_price_liq = _find_first_text(item_el, ['preco_liquido'])
            raw_price = _find_first_text(item_el, ['preco', 'valor'])
            price_liq = _parse_decimal(raw_price_liq)
            quantity = _parse_decimal(_find_first_text(item_el, ['quantidade']))
            if quantity <= 0:
                quantity = Decimal('1')
            unit_price = _parse_decimal(raw_price)
            price_line_total = price_liq if price_liq > 0 else unit_price * quantity
            pieces.append(
                ParsedPiece(
                    name=descricao,
                    cost_price=price_line_total,
                    provider_type=provider_type,
                )
            )

    customer = ParsedCustomer(
        name=customer_name or 'Cliente',
        document_cpf_cnpj=customer_document,
        phone=customer_phone,
        email=customer_email,
    )

    vehicle = ParsedVehicle(
        plate=plate,
        brand=brand or 'Marca',
        model=model or 'Modelo',
        color=color,
        year=year,
    )

    total_amount, breakdown = _find_total_amount(root)

    return customer, vehicle, pieces, total_amount, breakdown, cilia_number_value, cilia_version_value
