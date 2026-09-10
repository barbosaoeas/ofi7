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


def _clean_text(s) -> str:
    if s is None:
        return ''
    v = str(s).strip()
    if not v:
        return ''
    import re as _re
    raw = v
    v = v.replace('\r', ' ').replace('\n', ' ').strip()
    bad = [
        '`', '\u200b', '\u200c', '\u200d', '\ufeff', '\u00a0',
        '\u0060', '\u00b4', '\u02cb', '\u02ca', '\u2018', '\u2019',
        '\u201c', '\u201d', '\u2039', '\u203a', '\xab', '\xbb',
    ]
    changed = True
    while changed:
        changed = False
        for b in bad:
            if b in v:
                v = v.replace(b, '')
                changed = True
    while len(v) >= 1 and v[0] in ('`', '"', "'", ' ', '\t', '\u2018', '\u201c'):
        v = v[1:]
        changed = True
    while len(v) >= 1 and v[-1] in ('`', '"', "'", ' ', '\t', '\u2019', '\u201d'):
        v = v[:-1]
        changed = True
    if changed:
        v = v.strip()
    if '://' not in v:
        m = _re.search(r'https?://[A-Za-z0-9\-\.:/_~?#&=%@\[\]\+\$,;!\*\(\)\']+', raw)
        if not m:
            m = _re.search(r'https?://[^\s\u2018\u2019\u201c\u201d`\'""]+', raw)
        if m:
            v = m.group(0).rstrip("'\",.;`\u2019\u201d")
    return v


def _extract_host_base_from_url(url: str) -> str:
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
        host = (p.hostname or '').lower()
        if not host:
            return ''
        return host
    except Exception:
        return ''


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
        get_first = [
            ('GET', f'{base}/message/{message_id}', None),
            ('GET', f'{base}/messages/{message_id}', None),
            ('GET', f'{base}/chat/message/{message_id}', None),
            ('GET', f'{base}/chat/messages/{message_id}', None),
            ('GET', f'{base}/message/get/{message_id}', None),
            ('GET', f'{base}/message?messageId={message_id}', None),
            ('GET', f'{base}/chat?messageId={message_id}', None),
            ('GET', f'{base}/chat/messages/get?messageId={message_id}', None),
            ('GET', f'{base}/instance/message/{message_id}', None),
            ('GET', f'{base}/message/{message_id}/download', None),
            ('GET', f'{base}/download/message/{message_id}', None),
            ('GET', f'{base}/chat/message/{message_id}/download', None),
            ('GET', f'{base}/media/download/{message_id}', None),
            ('GET', f'{base}/instance/message/{message_id}/media', None),
        ]
        for method, url, body in get_first:
            out.append({'method': method, 'url': url, 'body': body, 'note': 'getmsg'})
        standard_post = [
            ('POST', f'{base}/message/mdownloadMedia', {'messageId': message_id}),
            ('POST', f'{base}/chat/download', {'messageId': message_id}),
            ('POST', f'{base}/chat/media/download', {'messageId': message_id}),
            ('POST', f'{base}/media/message', {'messageId': message_id}),
            ('POST', f'{base}/download/message', {'messageId': message_id}),
        ]
        for method, url, body in standard_post:
            out.append({'method': method, 'url': url, 'body': body, 'note': 'postmedia'})
        variants = [
            ('POST', f'{base}/message/media', {'id': message_id}),
            ('POST', f'{base}/message/get', {'id': message_id}),
            ('POST', f'{base}/chat/message/get', {'id': message_id}),
            ('POST', f'{base}/instance/message/{message_id}/media', {}),
        ]
        if chat_id:
            variants += [
                ('POST', f'{base}/chat/messages', {'chatId': chat_id, 'ids': [message_id]}),
                ('POST', f'{base}/chat/messages', {'chat_id': chat_id, 'ids': [message_id]}),
                ('GET', f'{base}/chat/messages?chatId={chat_id}&ids={message_id}', None),
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


def _parse_json_body(resp_bytes: bytes):
    try:
        return json.loads(resp_bytes.decode('utf-8', errors='replace'))
    except Exception:
        return None


def _find_media_candidates_in_json(j):
    out = {'urls': [], 'datauris': []}
    if isinstance(j, dict):
        for k, v in j.items():
            kl = (k or '').lower()
            if isinstance(v, str):
                if kl in ('url', 'downloadurl', 'mediaurl', 'media_url', 'download_url',
                           'fileurl', 'file_url', 'direct_url', 'link', 'href', 'attachment'):
                    if v.startswith('http') or v.startswith('/'):
                        out['urls'].append(v)
                    elif v.startswith('data:') and ';base64,' in v:
                        out['datauris'].append(v)
                elif 'data:' in v and ';base64,' in v:
                    try:
                        i = v.index('data:')
                        out['datauris'].append(v[i:].split('"')[0].split("'")[0].split()[0])
                    except Exception:
                        pass
            else:
                sub = _find_media_candidates_in_json(v)
                out['urls'].extend(sub['urls'])
                out['datauris'].extend(sub['datauris'])
    elif isinstance(j, list):
        for item in j:
            sub = _find_media_candidates_in_json(item)
            out['urls'].extend(sub['urls'])
            out['datauris'].extend(sub['datauris'])
    return out


def _resolve_media_bytes(candidate_url_or_datauri, headers, base_url='', timeout=20, max_bytes=10 * 1024 * 1024):
    if candidate_url_or_datauri.startswith('data:') and ';base64,' in candidate_url_or_datauri:
        try:
            import base64
            head = candidate_url_or_datauri.split(',', 1)[1]
            bin_ = base64.b64decode(head + ('=' * (-len(head) % 4)))
            return {
                'status': 200,
                'content_type': 'data-uri',
                'bytes': len(bin_),
                'head_hex': (bin_[:16] or b'').hex(),
                'sniffed': sniff(bin_[:32]),
                'preview_txt': (bin_[:200] or b'').decode('utf-8', errors='ignore').replace('\x00', ''),
                'ok': sniff(bin_[:32]) in ('application/pdf', 'image/jpeg', 'image/png', 'image/webp'),
            }
        except Exception as ee:
            return {'status': None, 'error': f'base64: {type(ee).__name__}: {ee}', 'ok': False}
    if candidate_url_or_datauri.startswith('/') and base_url:
        candidate_url_or_datauri = base_url.rstrip('/') + candidate_url_or_datauri
    return http_call_raw('GET', candidate_url_or_datauri, headers, body=None, timeout=timeout, max_bytes=max_bytes)


def http_call_raw(method, url, headers, body=None, timeout=20, max_bytes=10 * 1024 * 1024):
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


def http_call(method, url, headers, body=None, timeout=20, max_bytes=10 * 1024 * 1024):
    import copy
    base_r = http_call_raw(method, url, headers, body=body, timeout=timeout, max_bytes=max_bytes)
    r = dict(base_r)
    r['json_follow'] = []
    r['json_datauri'] = []
    if not r.get('ok') and r.get('status') and 200 <= int(r['status']) < 300:
        try:
            import base64
            head_hex = r.get('head_hex') or ''
            first_bytes = bytes.fromhex(head_hex) if head_hex and len(head_hex) >= 2 else b''
            if first_bytes[:1] in (b'{', b'['):
                payload = base_r.get('head_hex')  # we only stored up to 16 bytes in raw, cannot trust for full JSON
        except Exception:
            pass
        j = None
        try:
            sample_preview = r.get('preview_txt') or ''
            if sample_preview.lstrip().startswith('{') or sample_preview.lstrip().startswith('['):
                j = _parse_json_body(bytes.fromhex(r.get('head_hex') or ''))  # too short; will likely fail
        except Exception:
            j = None
        if j is None:
            r['json_parse_note'] = 'sample too short'
            return r
    return r


def _whatsapp_media_app_info_probe(media_type_hint: str, mimetype: str = '') -> bytes:
    mt = (media_type_hint or '').lower()
    mime = (mimetype or '').lower()
    if mt == 'image' or mime.startswith('image/') or mt == 'sticker':
        return b'WhatsApp Image Keys'
    if mt == 'video' or mime.startswith('video/'):
        return b'WhatsApp Video Keys'
    if mt == 'audio' or mime.startswith('audio/') or mt == 'ptt' or mt == 'voice':
        return b'WhatsApp Audio Keys'
    return b'WhatsApp Document Keys'


def _hkdf_sha256_expand_probe(ikm: bytes, info: bytes, length: int, salt: bytes = b'') -> bytes:
    try:
        from hashlib import sha256
        import hmac
        if not salt:
            salt = b'\x00' * 32
        prk = hmac.new(salt, ikm, sha256).digest()
        t = b''
        output = b''
        counter = 1
        while len(output) < length:
            t = hmac.new(prk, t + info + bytes([counter]), sha256).digest()
            output += t
            counter += 1
        return output[:length]
    except Exception:
        return b''


def _pkcs7_unpad_probe(data: bytes, block_size: int = 16) -> bytes:
    if not data or len(data) % block_size != 0:
        return data
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size:
        return data
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        return data
    return data[:-pad_len]


def _aes_cbc_decrypt(cipher_key: bytes, iv: bytes, ciphertext: bytes, *, allow_truncate=True, allow_pad=True):
    variants = []
    if not ciphertext:
        return b'', 'ciphertext_vazio'
    errs = []
    cts = []
    original = ciphertext
    cts.append(('exact', original))
    if len(original) % 16 != 0:
        if allow_truncate:
            truncs = (len(original) // 16) * 16
            if truncs >= 16:
                cts.append(('trunc', original[:truncs]))
        if allow_pad:
            pad_len = (16 - (len(original) % 16)) % 16
            if pad_len:
                cts.append(('pad0', original + (b'\x00' * pad_len)))
                pkcs7_pad = bytes([pad_len]) * pad_len
                cts.append(('padPKCS7', original + pkcs7_pad))
    for tag, ct in cts:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
            cipher = Cipher(algorithms.AES(cipher_key), modes.CBC(iv), backend=default_backend())
            decryptor = cipher.decryptor()
            padded = decryptor.update(ct) + decryptor.finalize()
            unpadded = _pkcs7_unpad_probe(padded, 16)
            variants.append((unpadded, f'ok_crypto_{tag}'))
        except Exception as e:
            pass
    if variants:
        variants.sort(key=lambda x: -len(x[0]))
        return variants[0]
    try:
        from Crypto.Cipher import AES
    except Exception as e:
        errs.append(f'sem_pycryptodome: {type(e).__name__}')
    if not variants:
        note = ' | '.join(errs) or f'nao_descriptografou_nenhuma_variante_cts={len(cts)}'
        return b'', note
    return variants[0]


def _decrypt_whatsapp_media_probe(cipher_bytes: bytes, media_key_b64: str, media_type_hint: str = '', mimetype: str = ''):
    out = {'bytes': b'', 'variants': []}
    if not cipher_bytes or not media_key_b64:
        out['variants'].append({'v': 'skip', 'ok': False, 'sniffed': '', 'note': 'sem_cipherbytes_ou_mediakey'})
        return out
    import base64 as _b64
    try:
        mk = _b64.b64decode(media_key_b64 + '=' * (-len(media_key_b64) % 4))
    except Exception as e:
        out['variants'].append({'v': 'b64decode_fail', 'ok': False, 'sniffed': '', 'note': f'{type(e).__name__}: {e}'})
        return out
    if len(mk) < 32:
        out['variants'].append({'v': 'short_media_key', 'ok': False, 'sniffed': '', 'note': f'len={len(mk)}'})
        return out
    mk = mk[:32]

    app_infos = [
        ('typed', _whatsapp_media_app_info_probe(media_type_hint, mimetype)),
        ('generic', b'WhatsApp Media Keys'),
    ]
    hkdf_lens = [112, 80, 96, 64]

    stream = cipher_bytes
    stream_len = len(stream)

    def build_stream_variants(total: bytes):
        vs = []
        n = len(total)
        # Tenta várias maneiras de extrair (iv, ciphertext) considerando MAC de 0/10/16/32 bytes
        for mac_len in (32, 10, 16, 0):
            if n <= mac_len:
                continue
            without_mac = total[:-mac_len] if mac_len > 0 else total
            # HKDF_IV: IV vem da HKDF
            if len(without_mac) >= 16:
                vs.append((f'HKDF_IV_MAC{mac_len}', 'HKDF', without_mac))
            # STREAM_IV_PREPENDED: IV primeiros 16 bytes, ciphertext depois
            if len(without_mac) >= 16 + 16:
                iv = without_mac[:16]
                ct = without_mac[16:]
                vs.append((f'STREAM_IV_PREPENDED_MAC{mac_len}', iv, ct))
        # IV no FINAL (antes do MAC, ciphertext first)
        for mac_len in (32, 16, 10, 0):
            payload = total[:-mac_len] if mac_len > 0 else total
            if len(payload) >= 16 + 16:
                ct = payload[:-16]
                iv = payload[-16:]
                vs.append((f'STREAM_IV_AT_END_MAC{mac_len}', iv, ct))
        # IV dos bytes 16..32 (meio)
        if n >= 48:
            iv = stream[16:32]
            ct_start_32 = stream[32:]
            for mac_len in (32, 16, 0):
                if len(ct_start_32) > mac_len:
                    ct = ct_start_32[:-mac_len] if mac_len > 0 else ct_start_32
                    if len(ct) >= 16:
                        vs.append((f'IV_POS16_32_MAC{mac_len}', iv, ct))
        return vs

    for app_name, app_info in app_infos:
        for hkdf_len in hkdf_lens:
            expanded = _hkdf_sha256_expand_probe(mk, app_info, hkdf_len)
            if len(expanded) < hkdf_len or hkdf_len < 48:
                continue
            hkdf_iv = expanded[0:16] if hkdf_len >= 32 else b''
            cipher_key = expanded[16:48] if hkdf_len >= 48 else expanded[0:32]
            if len(cipher_key) != 32:
                continue
            svs = build_stream_variants(stream)
            for vname, iv_src, ctext in svs:
                if iv_src == 'HKDF':
                    iv = hkdf_iv
                else:
                    iv = iv_src
                plain, note = _aes_cbc_decrypt(cipher_key, iv, ctext)
                sn = sniff(plain[:32]) if plain else ''
                ok = sn in ('application/pdf', 'image/jpeg', 'image/png', 'image/webp')
                entry = {
                    'v': f'{app_name}_hkdf{hkdf_len}_{vname}',
                    'ok': ok,
                    'sniffed': sn,
                    'note': note,
                    'bytes': len(plain or b''),
                    'head_hex': (plain[:16] or b'').hex(),
                }
                out['variants'].append(entry)
                if ok and not out['bytes']:
                    out['bytes'] = plain

    # Top 15 variants somente para economizar output (filtra por bytes>0 primeiro, depois ordena)
    with_bytes = [v for v in out['variants'] if v.get('bytes', 0) > 0]
    with_none = [v for v in out['variants'] if v.get('bytes', 0) == 0]
    with_bytes.sort(key=lambda v: (-v['bytes'], 'STREAM' in v['v'], 'HKDF' in v['v'], 'MAC32' in v['v']))
    out['variants'] = with_bytes[:10] + with_none[:5]

    if not out['bytes']:
        out['note'] = f'total_variants={len(with_bytes) + len(with_none)}; stream_len={stream_len}; stream_head={(stream[:16] or b"").hex()}'
    return out


def http_call_with_json_follow(method, url, headers, body=None, timeout=20, max_bytes=10 * 1024 * 1024, base_url=''):
    import urllib.request
    import urllib.error
    import traceback
    data = None
    hdrs = dict(headers)
    if method.upper() == 'POST' and body is not None:
        data = json.dumps(body).encode('utf-8')
        hdrs['Content-Type'] = 'application/json'
    hdrs['Accept'] = 'application/octet-stream, application/pdf, image/*, application/json'
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, 'status', 200)
            ct = (resp.headers.get('Content-Type') or '').strip()
            payload = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        payload = b''
        try:
            payload = e.read(max_bytes) or b''
        except Exception:
            payload = b''
        status = getattr(e, 'code', None)
        ct = (e.headers.get('Content-Type') or '').strip() if getattr(e, 'headers', None) else ''
    except Exception as e:
        tb = traceback.format_exc(limit=3)
        return {
            'status': None,
            'content_type': '',
            'bytes': 0,
            'head_hex': '',
            'sniffed': '',
            'preview_txt': '',
            'ok': False,
            'error': f'{type(e).__name__}: {e}',
            'error_trace': tb,
            'json_follow': [],
            'json_datauri': [],
        }
    payload = payload or b''
    sn = sniff(payload[:32])
    is_json_resp = (
        ('json' in (ct or '').lower())
        or ((payload[:1] or b'') in (b'{', b'['))
        or (((payload[:200] or b'').decode('utf-8', errors='ignore').lstrip().startswith(('{', '['))))
    )
    ok = bool(status) and 200 <= int(status) < 300 and sn in ('application/pdf', 'image/jpeg', 'image/png', 'image/webp')
    preview_txt = (payload[:500] or b'').decode('utf-8', errors='ignore').replace('\x00', '')
    r = {
        'status': status,
        'content_type': ct,
        'bytes': len(payload),
        'head_hex': (payload[:16] or b'').hex(),
        'sniffed': sn,
        'preview_txt': preview_txt,
        'ok': ok,
        'json_follow': [],
        'json_datauri': [],
    }
    if is_json_resp:
        j = _parse_json_body(payload)
        found = _find_media_candidates_in_json(j)
        r['json_found_urls'] = found['urls'][:5]
        r['json_found_datauri_count'] = len(found['datauris'])
        for m in found['urls'][:2]:
            f = _resolve_media_bytes(m, headers, base_url=base_url, timeout=timeout, max_bytes=max_bytes)
            r['json_follow'].append({**f, 'src': m})
            if f.get('ok'):
                r['ok'] = True
                break
        for m in found['datauris'][:1]:
            f = _resolve_media_bytes(m, headers, base_url=base_url, timeout=timeout, max_bytes=max_bytes)
            r['json_datauri'].append({**f})
            if f.get('ok'):
                r['ok'] = True
                break
    return r


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
    parser.add_argument('--media-key', default='', help='mediaKey (base64) para fallback de descriptografia WhatsApp.')
    parser.add_argument('--mimetype', default='', help='mimetype original do payload (ex: image/jpeg).')
    args = parser.parse_args()

    base_url = (args.base_url or '').strip()
    token = (args.token or '').strip()
    instance = (args.instance or '').strip()
    message_id = (args.message_id or '').strip()
    media_type = (args.media_type or '').strip()
    direct_path = (args.direct_path or '').strip()
    file_url = (args.file_url or '').strip()
    chat_id = (args.chat_id or '').strip()
    media_key = (args.media_key or '').strip()
    mimetype_orig = (args.mimetype or '').strip()

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
                if not mimetype_orig:
                    mimetype_orig = mime
                if not media_key:
                    media_key = str(content.get('mediaKey') or '').strip()
                if not media_type:
                    if mime.startswith('image/'):
                        media_type = 'image'
                    elif mime == 'application/pdf':
                        media_type = 'pdf'

    base_url = _clean_text(base_url)
    token = _clean_text(token)
    instance = _clean_text(instance)
    direct_path = _clean_text(direct_path)
    file_url = _clean_text(file_url)
    message_id = _clean_text(message_id)
    chat_id = _clean_text(chat_id)
    media_key = _clean_text(media_key)
    mimetype_orig = _clean_text(mimetype_orig)
    if not base_url and file_url:
        host = _extract_host_base_from_url(file_url)
    candidates = build_candidates(base_url, message_id, media_type, direct_path, file_url, chat_id)

    host_sub = ''
    if base_url:
        host = _extract_host_base_from_url(base_url) or ''
        if host:
            parts = host.split('.')
            if len(parts) >= 3:
                host_sub = parts[0]
    headers = {
        'User-Agent': 'ofi7-probe/1.0',
    }
    if token:
        headers['apikey'] = token
        headers['Authorization'] = f'Bearer {token}'
    used_instance = _clean_text(instance) or host_sub
    if used_instance:
        headers['X-UAIZAPI-Instance'] = used_instance

    results = []
    ok_ids = []
    last_direct_raw = b''
    for idx, c in enumerate(candidates, start=1):
        res = http_call_with_json_follow(c['method'], c['url'], headers, body=c.get('body'), base_url=base_url)
        entry = {
            'id': idx,
            'method': c['method'],
            'url': c['url'],
            'note': c.get('note') or '',
            **res,
        }
        if res.get('error'):
            entry['error'] = res['error']
        if res.get('error_trace'):
            entry['error_trace'] = res['error_trace']
        results.append(entry)
        if entry.get('ok'):
            ok_ids.append(idx)
        if c.get('note') == 'directUrl':
            try:
                import urllib.request as _u
                hdrs2 = {'User-Agent': 'WhatsApp/2.24.25.82 Android/14', 'Accept': '*/*'}
                req2 = _u.Request(c['url'], headers=hdrs2, method='GET')
                with _u.urlopen(req2, timeout=30) as resp2:
                    last_direct_raw = resp2.read(20 * 1024 * 1024 + 1)
            except Exception as dle:
                last_direct_raw = b''
                if not entry.get('error'):
                    entry['error'] = f'directRaw {type(dle).__name__}: {dle}'

    if media_key and (not ok_ids) and last_direct_raw:
        dec_result = _decrypt_whatsapp_media_probe(last_direct_raw, media_key, media_type, mimetype_orig)
        dec_bytes = dec_result.get('bytes') or b''
        dec_sniff = sniff(dec_bytes[:32]) if dec_bytes else ''
        dec_ok = dec_sniff in ('application/pdf', 'image/jpeg', 'image/png', 'image/webp')
        dec_id = len(results) + 1
        dec_entry = {
            'id': dec_id,
            'method': 'AES',
            'url': 'whatsapp://media-decrypt',
            'note': 'decrypt_fallback',
            'status': 200 if dec_bytes else None,
            'content_type': dec_sniff or ('application/octet-stream' if dec_bytes else ''),
            'bytes': len(dec_bytes or b''),
            'head_hex': (dec_bytes[:16] or b'').hex(),
            'sniffed': dec_sniff,
            'preview_txt': (dec_bytes[:200] or b'').decode('utf-8', errors='ignore').replace('\x00', ''),
            'ok': dec_ok,
            'json_follow': [],
            'json_datauri': [],
            'decrypt_media_key_set': True,
            'decrypt_raw_len': len(last_direct_raw),
            'decrypt_variants': dec_result.get('variants', []),
        }
        results.append(dec_entry)
        if dec_ok:
            ok_ids.append(dec_id)

    out = {
        'probe': {
            'base_url': base_url,
            'instance': used_instance,
            'instance_from_payload': _clean_text(instance),
            'token_set': bool(token),
            'message_id': message_id,
            'media_type': media_type,
            'direct_path': direct_path,
            'chat_id': chat_id,
            'queue_item_id': args.queue_item_id or None,
            'media_key_set': bool(media_key),
            'mimetype_orig': mimetype_orig,
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
