import argparse
import json
import os
import sys
from pathlib import Path


def setup_django():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'controle_oficina.settings')
    import django
    django.setup()


def sniff(data: bytes) -> str:
    head = (data or b'')[:32]
    if not head:
        return ''
    if head.startswith(b'%PDF'):
        return 'application/pdf'
    if head.startswith(b'\xFF\xD8\xFF'):
        return 'image/jpeg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    s = head.decode('utf-8', errors='ignore').lstrip()
    if s.lower().startswith(('<!doctype html', '<html', '<?xml')):
        return 'text/html'
    return ''


def build_candidates(base_url, message_id, media_type_hint='', direct_path='', file_url='', chat_id=''):
    base = base_url.rstrip('/') if base_url else ''
    out = []
    if base and message_id:
        standard = [
            ('POST', f'{base}/message/mdownloadMedia', {'messageId': message_id}),
            ('POST', f'{base}/chat/download', {'messageId': message_id}),
            ('POST', f'{base}/chat/media/download', {'messageId': message_id}),
            ('POST', f'{base}/media/message', {'messageId': message_id}),
            ('POST', f'{base}/download/message', {'messageId': message_id}),
            ('GET', f'{base}/message/{message_id}/download', None),
            ('GET', f'{base}/download/message/{message_id}', None),
            ('GET', f'{base}/chat/message/{message_id}/download', None),
            ('GET', f'{base}/media/download/{message_id}', None),
        ]
        for method, url, body in standard:
            out.append({'method': method, 'url': url, 'body': body, 'note': 'standard'})
        variants = [
            ('POST', f'{base}/message/media', {'id': message_id}),
            ('POST', f'{base}/message/get', {'id': message_id}),
            ('POST', f'{base}/chat/message/get', {'id': message_id}),
            ('POST', f'{base}/instance/message/{message_id}/media', {}),
            ('GET', f'{base}/instance/message/{message_id}/media', None),
        ]
        if media_type_hint:
            variants += [
                ('POST', f'{base}/{media_type_hint}/download', {'messageId': message_id}),
            ]
        for method, url, body in variants:
            out.append({'method': method, 'url': url, 'body': body, 'note': 'variant'})
    if direct_path and base:
        out.append({
            'method': 'GET',
            'url': base + (direct_path if direct_path.startswith('/') else '/' + direct_path),
            'body': None,
            'note': 'directPath',
        })
    if file_url:
        out.append({'method': 'GET', 'url': file_url, 'body': None, 'note': 'directUrl'})
    return out


def http_call(method, url, headers, body=None, timeout=20, max_bytes=10 * 1024 * 1024):
    import urllib.request
    import urllib.error
    try:
        data = None
        hdrs = dict(headers)
        if method.upper() == 'POST' and body is not None:
            data = json.dumps(body).encode('utf-8')
            hdrs['Content-Type'] = 'application/json'
        hdrs['Accept'] = 'application/octet-stream, application/pdf, image/*, application/json'
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, 'status', 200)
            ct = (resp.headers.get('Content-Type') or '').strip()
            payload = resp.read(max_bytes + 1)
            return {
                'status': status,
                'content_type': ct,
                'bytes': len(payload),
                'head_hex': (payload[:16] or b'').hex(),
                'sniffed': sniff(payload[:32]),
                'preview_txt': (payload[:200] or b'').decode('utf-8', errors='ignore').replace('\x00', ''),
                'ok': len(payload) <= max_bytes and sniff(payload[:32]) in (
                    'application/pdf', 'image/jpeg', 'image/png', 'image/webp'
                ),
            }
    except urllib.error.HTTPError as e:
        tmp = b''
        try:
            tmp = e.read(4096) or b''
        except Exception:
            tmp = b''
        return {
            'status': getattr(e, 'code', None),
            'content_type': (e.headers.get('Content-Type') or '').strip() if getattr(e, 'headers', None) else '',
            'bytes': len(tmp),
            'head_hex': (tmp[:16] or b'').hex(),
            'sniffed': sniff(tmp[:32]),
            'preview_txt': (tmp[:500] or b'').decode('utf-8', errors='ignore').replace('\x00', ''),
            'ok': False,
        }
    except Exception as e:
        return {'status': None, 'error': f'{type(e).__name__}: {e}', 'ok': False}


def main():
    parser = argparse.ArgumentParser(description='Sonda para descobrir endpoint correto de download de midia UAZAPI.')
    parser.add_argument('--base-url', default='', help='Base URL da instancia UAZAPI. Ex: https://ofii7.uazapi.com')
    parser.add_argument('--token', default='', help='Token/api-key da instancia. Ex: df5f357e-...')
    parser.add_argument('--instance', default='', help='Nome da instancia (opcional). Ex: ofii7')
    parser.add_argument('--message-id', default='', help='ID de mensagem com anexo (external_message_id).')
    parser.add_argument('--queue-item-id', type=int, default=0, help='ID do WhatsAppFinanceQueueItem para pegar message_id/dados.')
    parser.add_argument('--media-type', default='', help='image ou pdf (opcional).')
    parser.add_argument('--direct-path', default='', help='Valor de message.content.directPath (opcional).')
    parser.add_argument('--file-url', default='', help='Valor de message.content.URL (opcional).')
    parser.add_argument('--chat-id', default='', help='chat_id (opcional).')
    parser.add_argument('--out', default='', help='Caminho para salvar resultado em JSON.')
    parser.add_argument('--save-ok', default='', help='Pasta para salvar binarios quando a sonda der OK (opcional).')
    args = parser.parse_args()

    base_url = (args.base_url or '').strip()
    token = (args.token or '').strip()
    instance = (args.instance or '').strip()
    message_id = (args.message_id or '').strip()
    media_type = (args.media_type or '').strip()
    direct_path = (args.direct_path or '').strip()
    file_url = (args.file_url or '').strip()
    chat_id = (args.chat_id or '').strip()

    setup_django()
    from django.conf import settings

    if not token:
        token = (
            getattr(settings, 'UAIZAPI_API_KEY', '')
            or getattr(settings, 'UAIZAPI_DOWNLOAD_TOKEN', '')
            or getattr(settings, 'UAIZAPI_WEBHOOK_TOKEN', '')
            or ''
        )
    if not instance:
        instance = getattr(settings, 'UAIZAPI_INSTANCE', '') or ''
    if not base_url and instance:
        base_url = f'https://{instance}.uazapi.com'

    if args.queue_item_id:
        from budgets.models import WhatsAppFinanceQueueItem
        item = WhatsAppFinanceQueueItem.objects.filter(pk=args.queue_item_id).first()
        if item:
            if not message_id:
                message_id = str(getattr(item, 'external_message_id', '') or '').strip()
            raw = getattr(item, 'raw_payload', None) or {}
            if isinstance(raw, dict):
                if not base_url:
                    base_url = str(raw.get('BaseUrl') or '').strip()
                if not instance:
                    instance = str(raw.get('instanceName') or '').strip()
                if not token:
                    token = str(raw.get('token') or '').strip()
                msg = raw.get('message') if isinstance(raw.get('message'), dict) else {}
                content = msg.get('content') if isinstance(msg.get('content'), dict) else {}
                if not direct_path:
                    direct_path = str(content.get('directPath') or '').strip()
                if not file_url:
                    file_url = str(content.get('URL') or '').strip()
                if not chat_id:
                    chat_id = str(msg.get('chatid') or (raw.get('chat') or {}).get('wa_chatid') or '').strip()
                mime = str(content.get('mimetype') or '').lower()
                if not media_type:
                    if mime.startswith('image/'):
                        media_type = 'image'
                    elif mime == 'application/pdf':
                        media_type = 'pdf'
    candidates = build_candidates(base_url, message_id, media_type, direct_path, file_url, chat_id)

    headers = {
        'User-Agent': 'ofi7-probe/1.0',
    }
    if token:
        headers['apikey'] = token
        headers['Authorization'] = f'Bearer {token}'
    if instance:
        headers['X-UAIZAPI-Instance'] = instance

    results = []
    ok_ids = []
    for idx, c in enumerate(candidates, start=1):
        res = http_call(c['method'], c['url'], headers, body=c.get('body'))
        entry = {
            'id': idx,
            'method': c['method'],
            'url': c['url'],
            'note': c.get('note') or '',
            **res,
        }
        results.append(entry)
        if entry.get('ok'):
            ok_ids.append(idx)

    out = {
        'probe': {
            'base_url': base_url,
            'instance': instance,
            'token_set': bool(token),
            'message_id': message_id,
            'media_type': media_type,
            'direct_path': direct_path,
            'chat_id': chat_id,
            'queue_item_id': args.queue_item_id or None,
        },
        'ok_ids': ok_ids,
        'results': results,
    }

    if args.save_ok and ok_ids:
        folder = Path(args.save_ok)
        folder.mkdir(parents=True, exist_ok=True)
        for idx in ok_ids:
            entry = next((r for r in results if r['id'] == idx), None)
            if not entry:
                continue
            ext = 'bin'
            if entry.get('sniffed') == 'application/pdf':
                ext = 'pdf'
            elif entry.get('sniffed') == 'image/jpeg':
                ext = 'jpg'
            elif entry.get('sniffed') == 'image/png':
                ext = 'png'
            elif entry.get('sniffed') == 'image/webp':
                ext = 'webp'
            # re-download para salvar
            method = entry['method']
            url = entry['url']
            body = None
            for c in candidates:
                if c['url'] == url and c['method'] == method:
                    body = c.get('body')
                    break
            r2 = http_call(method, url, headers, body=body)
            if r2.get('ok'):
                # we do not keep raw bytes in memory for save; skip for safety on probes
                out['probe'].setdefault('saved_samples', []).append({
                    'id': idx,
                    'filename': f'probe_{idx}.{ext}',
                    'sniffed': r2.get('sniffed'),
                    'bytes': r2.get('bytes'),
                })

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
