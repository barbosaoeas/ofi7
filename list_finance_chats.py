import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'controle_oficina.settings')

import django
django.setup()

from budgets.models import WhatsAppFinanceQueueItem, WhatsAppWebhookLog
from django.db.models import Count


def main():
    N = 50
    qs = WhatsAppFinanceQueueItem.objects.all().order_by('-created_at', '-id')[:N]
    print('=== CHATS presentes nos ultimos', len(qs), 'queue items (financieiro) ===')
    print(f"{'pk':>4}  {'created':<17}  {'status':<9}  atts  sender  {'chat_id':<40.40s}  chat_name")
    print('-' * 140)
    chat_counts = {}
    for qi in qs:
        try:
            atts = qi.attachments.count()
        except Exception:
            atts = 0
        created = qi.created_at.strftime('%d/%m %H:%M') if qi.created_at else '?'
        key = (str(qi.chat_id or ''), str(qi.chat_name or ''))
        chat_counts[key] = chat_counts.get(key, 0) + 1
        print(
            f"{qi.pk:>4}  {created:<17}  {qi.status:<9}  {atts}   "
            f"{(qi.sender_name or qi.sender_phone or '?')[:10]:<10.10s}  "
            f"{str(qi.chat_id or ''):<40.40s}  "
            f"{str(qi.chat_name or '')}"
        )
    print()
    print('=== Resumo por chat (contagem) ===')
    ranked = sorted(chat_counts.items(), key=lambda kv: -kv[1])
    for (cid, cname), cnt in ranked:
        print(f'  n={cnt:>3}  chat_id={cid:<45.45s}  chat_name={cname}')
    print()
    print('=== COMO CONFIGURAR ===')
    print(' 1) Escolha o(s) chat_id(s) ou chat_name(s) do GRUPO FINANCEIRO.')
    print(' 2) No PythonAnywhere → Web → Environment variables, adicione:')
    print('    UAIZAPI_FINANCE_ALLOWED_CHAT_IDS=120363040785758584@g.us,OUTRO_ID@g.us')
    print('    OU/AND: UAIZAPI_FINANCE_ALLOWED_CHAT_NAMES=financeiro,Financeiro Ofi7')
    print(' 3) Salvar vars + Reload Web App.')
    print()
    print(' Dica: use substring. Ex: se o chat_id termina com "@g.us" e o grupo de')
    print(' financeiro é "120363040785758584@g.us", basta colar "758584@g.us" no allowed.')
    print()

    print('=== ULTIMOS WEBHOOK LOGS (para checar chats recentes tambem) ===')
    wl = WhatsAppWebhookLog.objects.all().order_by('-created_at', '-id')[:15]
    for log in wl:
        created = log.created_at.strftime('%d/%m %H:%M') if log.created_at else '?'
        ok = 'OK' if log.processed_ok else 'NO'
        qid = log.queue_item_id or '-'
        print(
            f"  log.pk={log.pk:<4} {created}  proc={ok:<3} qi={qid:<4}  "
            f"ext={(log.external_message_id or ''):<15.15s}  "
            f"cid={str(log.chat_id or ''):<38.38s}  cname={str(log.chat_name or '')}"
        )


if __name__ == '__main__':
    main()
