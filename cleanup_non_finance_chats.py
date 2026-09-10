import os
import sys
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'controle_oficina.settings')

import django
django.setup()

from budgets.models import WhatsAppFinanceQueueItem


def _clean_text(v):
    import re
    if not isinstance(v, str):
        return v
    v = v.strip()
    v = re.sub(r'^[`"\']+|[`"\']+$', '', v)
    v = v.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    v = re.sub(r'\s+', ' ', v).strip()
    v = re.sub(r'^[`"\']+|[`"\']+$', '', v)
    return v


def _parse_allow_list(raw):
    import re
    if not raw:
        return []
    out = []
    for part in re.split(r'[,;|\n]+', raw):
        cleaned = _clean_text(part)
        if cleaned:
            out.append(cleaned)
    return out


def _is_allowed(chat_id, chat_name, allowed_ids, allowed_names):
    if not allowed_ids and not allowed_names:
        return True, True
    for needle in allowed_ids:
        if needle and needle in (chat_id or ''):
            return True, True
    lowered = (chat_name or '').lower()
    for needle in allowed_names:
        if needle and needle.lower() in lowered:
            return True, True
    return False, False


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--allowed-ids', type=str, default='', help='chat IDs permitidos (virgula). Ex: 120363040785758584@g.us,xyz@g.us')
    ap.add_argument('--allowed-names', type=str, default='', help='nomes de chat permitidos (substring). Ex: Financeiro,financeiro')
    ap.add_argument('--apply', action='store_true', default=False, help='Aplicar (por default e DRY RUN).')
    ap.add_argument('--mark-status', type=str, default='IGNORED', help='Status a marcar (PENDING/CONFIRMED/REJECTED/DUPLICATE/IGNORED)')
    ap.add_argument('--limit', type=int, default=50, help='Quantos itens inspecionar (mais recentes primeiro)')
    args = ap.parse_args()

    allowed_ids = _parse_allow_list(args.allowed_ids or '')
    allowed_names = _parse_allow_list(args.allowed_names or '')

    if not allowed_ids and not allowed_names:
        print('ERROR: informe --allowed-ids=... e/ou --allowed-names=... (senao tudo fica permitido)')
        sys.exit(1)

    qs = WhatsAppFinanceQueueItem.objects.all().order_by('-created_at', '-id')[:args.limit]
    pend_list = []
    already = 0
    allowed_count = 0
    for qi in qs:
        ok, _ = _is_allowed(qi.chat_id or '', qi.chat_name or '', allowed_ids, allowed_names)
        if ok:
            allowed_count += 1
            continue
        if qi.status == args.mark_status:
            already += 1
            continue
        pend_list.append(qi)

    print(f'=== Inspecionados ultimos {len(qs)} items ===')
    print(f'  - autorizados       : {allowed_count}')
    print(f'  - nao-autorizados (ja marcados {args.mark_status}): {already}')
    print(f'  - nao-autorizados (PENDENTES de acao) : {len(pend_list)}')
    print()

    if not pend_list:
        print('Nada para fazer.')
        return

    print(f'Itens PENDENTES (vamos marcar como {args.mark_status}):')
    print(f"{'pk':>4}  {'created':<17}  {'status_old':<9}  sender  {'chat_id':<38.38s}  chat_name")
    print('-' * 140)
    for qi in pend_list:
        created = qi.created_at.strftime('%d/%m %H:%M') if qi.created_at else '?'
        print(
            f"{qi.pk:>4}  {created:<17}  {qi.status:<9}  "
            f"{(qi.sender_name or qi.sender_phone or '?')[:10]:<10.10s}  "
            f"{str(qi.chat_id or ''):<38.38s}  "
            f"{str(qi.chat_name or '')}"
        )
    print()
    if not args.apply:
        print('>>> DRY RUN. Para APLICAR, rode novamente com a flag --apply')
        return
    from django.utils import timezone
    now = timezone.now()
    stamp = now.strftime('%d/%m %H:%M')
    pks = []
    for qi in pend_list:
        qi.status = args.mark_status
        old_notes = qi.review_notes or ''
        block = (
            f"\n=== LIMPEZA chat nao autorizado {stamp} ===\n"
            f"Motivo: chat_id={qi.chat_id or '(vazio)'} / chat_name={qi.chat_name or '(vazio)'} fora da lista permitida.\n"
            f"allowed_ids: {','.join(allowed_ids) or '-'}\n"
            f"allowed_names: {','.join(allowed_names) or '-'}\n"
        )
        qi.review_notes = old_notes + block
        qi.save(update_fields=['status', 'review_notes', 'updated_at'])
        pks.append(qi.pk)
    print(f'>>> APLICADO: {len(pks)} itens marcados como {args.mark_status}. pks={pks}')


if __name__ == '__main__':
    main()
