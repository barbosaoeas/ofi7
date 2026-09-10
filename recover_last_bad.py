import os
import sys
import re
import json
import base64
import hashlib
import hmac
import urllib.request
import urllib.error
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'controle_oficina.settings')

import django
django.setup()

from budgets.models import (
    WhatsAppFinanceQueueItem,
    WhatsAppFinanceQueueAttachment,
    WhatsAppWebhookLog,
)


def _sniff_mimetype(data_bytes: bytes) -> str:
    head = (data_bytes or b'')[:32]
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
    head_str = (head or b'').decode('utf-8', errors='ignore').lstrip()
    if head_str.startswith('<!DOCTYPE html') or head_str.lower().startswith('<html'):
        return 'text/html'
    return ''


def _is_valid_media_bytes(data_bytes):
    if not data_bytes:
        return False, ''
    sniffed = _sniff_mimetype(data_bytes)
    if sniffed in ('application/pdf', 'image/jpeg', 'image/png', 'image/webp'):
        return True, sniffed
    return False, sniffed or ''


def _whatsapp_media_app_info(media_type_hint: str, mimetype: str = '') -> bytes:
    mt = (media_type_hint or '').lower()
    mime = (mimetype or '').lower()
    if mt == 'image' or mime.startswith('image/') or mt == 'sticker':
        return b'WhatsApp Image Keys'
    if mt == 'video' or mime.startswith('video/'):
        return b'WhatsApp Video Keys'
    if mt == 'audio' or mime.startswith('audio/') or mt == 'ptt' or mt == 'voice':
        return b'WhatsApp Audio Keys'
    return b'WhatsApp Document Keys'


def _hkdf_sha256_expand(ikm: bytes, info: bytes, length: int, salt: bytes = b'', *, skip_extract: bool = False) -> bytes:
    try:
        from hashlib import sha256
        if skip_extract or (salt is None or len(salt) == 0):
            prk = hmac.new(b'\x00' * 32, ikm, sha256).digest()
        else:
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


def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    if not data or len(data) % block_size != 0:
        return data
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size:
        return data
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        return data
    return data[:-pad_len]


def _aes_cbc_decrypt(cipher_key: bytes, iv: bytes, ciphertext: bytes):
    if not ciphertext:
        return b'', 'ciphertext_vazio'
    variants = []
    cts = []
    orig = ciphertext
    cts.append(('exact', orig))
    if len(orig) % 16 != 0:
        t = (len(orig) // 16) * 16
        if t >= 16:
            cts.append(('trunc', orig[:t]))
        p = (16 - (len(orig) % 16)) % 16
        if p:
            cts.append(('pad0', orig + b'\x00' * p))
            cts.append(('padPKCS7', orig + bytes([p]) * p))
    for tag, ct in cts:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
            cipher = Cipher(algorithms.AES(cipher_key), modes.CBC(iv), backend=default_backend())
            decryptor = cipher.decryptor()
            padded = decryptor.update(ct) + decryptor.finalize()
            unpadded = _pkcs7_unpad(padded, 16)
            variants.append((unpadded, f'ok_crypto_{tag}'))
        except Exception:
            pass
    try:
        from Crypto.Cipher import AES
        for tag, ct in cts:
            try:
                cipher = AES.new(cipher_key, AES.MODE_CBC, iv)
                padded = cipher.decrypt(ct)
                unpadded = _pkcs7_unpad(padded, 16)
                variants.append((unpadded, f'ok_pycrypto_{tag}'))
            except Exception:
                pass
    except Exception:
        pass
    if variants:
        variants.sort(key=lambda x: -len(x[0]))
        return variants[0]
    return b'', 'sem_desempacotamento'


def _decrypt_whatsapp_media(cipher_bytes: bytes, media_key_b64: str, media_type_hint: str = '', mimetype: str = '') -> dict:
    out = {'bytes': b'', 'variants': []}
    if not cipher_bytes or not media_key_b64:
        out['variants'].append({'v': 'skip', 'ok': False, 'note': 'sem_cipherbytes_ou_mediakey'})
        return out
    try:
        mk = base64.b64decode(media_key_b64 + '=' * (-len(media_key_b64) % 4))
    except Exception as e:
        out['variants'].append({'v': 'b64decode_fail', 'ok': False, 'note': f'{type(e).__name__}: {e}'})
        return out
    if len(mk) < 32:
        out['variants'].append({'v': 'short_media_key', 'ok': False, 'note': f'len={len(mk)}'})
        return out
    mk = mk[:32]
    stream = cipher_bytes
    L = len(stream)
    typed_app = _whatsapp_media_app_info(media_type_hint, mimetype)
    app_infos_ordered = [('typed', typed_app)]
    if typed_app != b'WhatsApp Media Keys':
        app_infos_ordered.append(('generic', b'WhatsApp Media Keys'))

    fast_combos = [
        (112, 0, 'HKDF'),
        (112, 32, 'HKDF'),
        (80, 0, 'HKDF'),
        (112, 0, 'STREAM_PRE'),
        (112, 32, 'STREAM_PRE'),
    ]

    def stream_split(mac_len, pos_tag, total):
        L = len(total)
        if L <= mac_len:
            return None
        wm = total[:-mac_len] if mac_len > 0 else total
        if pos_tag == 'HKDF':
            if len(wm) >= 16:
                return ('HKDF', wm)
            return None
        if pos_tag == 'STREAM_PRE':
            if len(wm) >= 32:
                return (wm[:16], wm[16:])
            return None
        if pos_tag == 'STREAM_AT_END':
            if len(wm) >= 32:
                return (wm[-16:], wm[:-16])
            return None
        return None

    combos_tried = set()

    def try_combo(app_name, app_info, hkdf_len, mac_len, pos_tag, *, hkdf_kw=None):
        nonlocal out
        hkdf_kw = hkdf_kw or {}
        key = (app_name, hkdf_len, mac_len, pos_tag, tuple(sorted(hkdf_kw.items())))
        if key in combos_tried:
            return
        combos_tried.add(key)
        if hkdf_len < 48:
            return
        expanded = _hkdf_sha256_expand(mk, app_info, hkdf_len, **hkdf_kw)
        if len(expanded) < hkdf_len:
            return
        hkdf_iv = expanded[0:16]
        cipher_key = expanded[16:48] if hkdf_len >= 48 else expanded[0:32]
        if len(cipher_key) != 32:
            return
        sp = stream_split(mac_len, pos_tag, stream)
        if sp is None:
            return
        iv_src, ctext = sp
        iv = hkdf_iv if iv_src == 'HKDF' else iv_src
        plain, note = _aes_cbc_decrypt(cipher_key, iv, ctext)
        sn = ''
        ok = False
        if plain:
            _ok, _sn = _is_valid_media_bytes(plain[:32])
            sn = _sn
            ok = _ok
        out['variants'].append({
            'v': f'{app_name}_hkdf{hkdf_len}_{pos_tag}_MAC{mac_len}' + ('_sk' if hkdf_kw.get('skip_extract') else ''),
            'ok': ok,
            'sniffed': sn,
            'note': note,
            'bytes': len(plain or b''),
            'head_hex': (plain[:16] or b'').hex(),
        })
        if ok and not out['bytes']:
            out['bytes'] = plain

    for app_name, app_info in app_infos_ordered:
        for hkdf_len, mac_len, pos_tag in fast_combos:
            for kw in ({}, {'skip_extract': True}):
                try_combo(app_name, app_info, hkdf_len, mac_len, pos_tag, hkdf_kw=kw)
                if out['bytes']:
                    return out

    all_hkdf = [112, 80, 96, 64]
    all_macs = [32, 10, 16, 0]
    all_pos = ['HKDF', 'STREAM_PRE', 'STREAM_AT_END']
    for app_name, app_info in app_infos_ordered:
        for hkdf_len in all_hkdf:
            for mac_len in all_macs:
                for pos_tag in all_pos:
                    for kw in ({}, {'skip_extract': True}):
                        try_combo(app_name, app_info, hkdf_len, mac_len, pos_tag, hkdf_kw=kw)
                        if out['bytes']:
                            return out

    if L >= 48:
        iv_mid = stream[16:32]
        rest = stream[32:]
        for app_name, app_info in app_infos_ordered:
            for hkdf_len in all_hkdf:
                if hkdf_len < 48:
                    continue
                for kw in ({}, {'skip_extract': True}):
                    expanded = _hkdf_sha256_expand(mk, app_info, hkdf_len, **kw)
                    if len(expanded) < hkdf_len:
                        continue
                    cipher_key = expanded[16:48] if hkdf_len >= 48 else expanded[0:32]
                    if len(cipher_key) != 32:
                        continue
                    for mac_len in (32, 16, 0):
                        if len(rest) <= mac_len:
                            continue
                        ct = rest[:-mac_len] if mac_len > 0 else rest
                        if len(ct) < 16:
                            continue
                        plain, note = _aes_cbc_decrypt(cipher_key, iv_mid, ct)
                        sn = ''
                        ok = False
                        if plain:
                            _ok, _sn = _is_valid_media_bytes(plain[:32])
                            sn = _sn
                            ok = _ok
                        out['variants'].append({
                            'v': f'{app_name}_hkdf{hkdf_len}_IVMID_MAC{mac_len}' + ('_sk' if kw.get('skip_extract') else ''),
                            'ok': ok,
                            'sniffed': sn,
                            'note': note,
                            'bytes': len(plain or b''),
                            'head_hex': (plain[:16] or b'').hex(),
                        })
                        if ok and not out['bytes']:
                            out['bytes'] = plain
                            return out
    return out


def _clean_text(v):
    if not isinstance(v, str):
        return v
    v = v.strip()
    v = re.sub(r'^[`"\']+|[`"\']+$', '', v)
    v = v.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    v = re.sub(r'\s+', ' ', v).strip()
    v = re.sub(r'^[`"\']+|[`"\']+$', '', v)
    return v


def _find_whatsapp_media_url(obj, max_depth=6):
    WA_MARKERS = ('mmg.whatsapp.net', 'whatsapp.net', 'web.whatsapp.com', 'cdn.whatsapp.net')
    URL_RE = re.compile(r'https?://[^\s`"\'<>)]+')
    stack = [(obj, 0)]
    seen = set()
    candidates = []
    while stack:
        cur, depth = stack.pop()
        if depth > max_depth:
            continue
        if isinstance(cur, dict):
            if id(cur) in seen:
                continue
            seen.add(id(cur))
            for k, v in cur.items():
                if isinstance(v, (dict, list)):
                    stack.append((v, depth + 1))
                elif isinstance(v, str):
                    cleaned = _clean_text(v)
                    if cleaned.startswith('http'):
                        score = 0
                        if any(m in cleaned for m in WA_MARKERS):
                            score += 1000
                        if str(k).lower() in ('url', 'directurl', 'mediaurl', 'downloadurl', 'link'):
                            score += 300
                        if str(k).lower() == 'url':
                            score += 50
                        candidates.append((score, cleaned))
                    for m in URL_RE.finditer(v):
                        url = m.group(0)
                        if url.endswith(('.', ',', ';', '"', "'", '`')):
                            url = url[:-1]
                        if url.startswith('http'):
                            score = 0
                            if any(marker in url for marker in WA_MARKERS):
                                score += 1000
                            if 'content' in str(k).lower():
                                score += 80
                            candidates.append((score, url))
        elif isinstance(cur, list):
            for x in cur:
                stack.append((x, depth + 1))
    if not candidates:
        return ''
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def _safe_dict(value):
    return value if isinstance(value, dict) else {}


def _get_nested(obj, path):
    current = obj
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return ''
        current = current.get(key)
    return current if current is not None else ''


def extract_from_payload(payload):
    if not payload:
        return None
    root = _safe_dict(payload)
    uaizapi_message = _safe_dict(root.get('message'))
    uaizapi_chat = _safe_dict(root.get('chat'))
    if not uaizapi_message and not uaizapi_chat:
        file_url = _find_whatsapp_media_url(root)
        if not file_url:
            return None
        return {
            'file_url': file_url,
            'mimetype': '',
            'media_key': '',
            'file_name': '',
            'media_type_hint': '',
            'direct_path': '',
        }
    content = _safe_dict(uaizapi_message.get('content'))
    mimetype = _clean_text(str(content.get('mimetype') or '').strip())
    file_url = _clean_text(str(content.get('URL') or '').strip())
    if not file_url:
        file_url = _find_whatsapp_media_url(root)
    file_name = _clean_text(str(content.get('fileName') or content.get('title') or '').strip())
    media_key = _clean_text(str(content.get('mediaKey') or content.get('media_key') or '').strip())
    direct_path = _clean_text(str(content.get('directPath') or '').strip())
    media_type_hint = _clean_text(str(content.get('mediaType') or uaizapi_message.get('mediaType') or '').strip())
    return {
        'file_url': file_url,
        'mimetype': mimetype,
        'media_key': media_key,
        'file_name': file_name,
        'media_type_hint': media_type_hint,
        'direct_path': direct_path,
    }


def download_blob(url, timeout=30):
    if not url:
        return b'', 'url_vazia'
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        is_whatsapp = 'whatsapp.net' in host or 'whatsapp.com' in host
        headers = {
            'User-Agent': 'WhatsApp/2.24.25.82 Android/14' if is_whatsapp else 'ofi7-webhook/1.0',
            'Accept': '*/*',
        }
        if is_whatsapp:
            headers['Accept-Encoding'] = 'identity'
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            return data, f'status={resp.status} bytes={len(data)}'
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b''
        return b'', f'HTTPError {e.code} body={len(body)}b'
    except Exception as e:
        return b'', f'{type(e).__name__}: {e}'


def get_queue_payload(queue_item):
    if queue_item.raw_payload and isinstance(queue_item.raw_payload, dict) and queue_item.raw_payload:
        ext = extract_from_payload(queue_item.raw_payload)
        if ext and ext.get('file_url'):
            return ext, 'queue_item.raw_payload'
    for wl in WhatsAppWebhookLog.objects.filter(queue_item=queue_item).order_by('-created_at', '-id'):
        if wl.raw_payload and isinstance(wl.raw_payload, dict) and wl.raw_payload:
            ext = extract_from_payload(wl.raw_payload)
            if ext and ext.get('file_url'):
                return ext, f'webhook_log.pk={wl.pk}'
    if queue_item.review_notes:
        synthetic = {'_notes': queue_item.review_notes}
        ext = extract_from_payload(synthetic)
        if ext and ext.get('file_url'):
            return ext, 'review_notes_scan'
    return None, None


def resolve_filename(info, mime):
    base = info.get('file_name') or ''
    base = re.sub(r'[^a-zA-Z0-9._-]+', '_', base).strip('_')
    if base:
        if '.' not in base:
            if mime == 'application/pdf':
                base += '.pdf'
            elif mime == 'image/png':
                base += '.png'
            elif mime == 'image/webp':
                base += '.webp'
            else:
                base += '.jpg'
        return base
    if mime == 'application/pdf':
        return 'documento.pdf'
    if mime == 'image/png':
        return 'imagem.png'
    if mime == 'image/webp':
        return 'imagem.webp'
    return 'imagem.jpg'


def recover_item(queue_item):
    info, src = get_queue_payload(queue_item)
    if not info or not info.get('file_url'):
        return {'status': 'SKIP', 'reason': 'sem_payload_ou_url', 'src': src}
    existing = WhatsAppFinanceQueueAttachment.objects.filter(queue_item=queue_item).first()
    if existing and existing.size_bytes:
        ok_ex, sn_ex = _is_valid_media_bytes(b'')
        try:
            existing.file.open('rb')
            head = existing.file.read(32)
            existing.file.close()
            ok_ex, sn_ex = _is_valid_media_bytes(head)
        except Exception:
            pass
        if ok_ex:
            return {'status': 'OK_JA_EXISTE', 'sniffed': sn_ex, 'bytes': existing.size_bytes}

    url = info['file_url']
    print(f'  [pk={queue_item.pk}] URL fonte ({src}): {url[:120]}...')

    blob, dl_note = download_blob(url)
    print(f'  [pk={queue_item.pk}] Download: {dl_note}')
    if not blob:
        return {'status': 'FAIL', 'reason': f'download_falhou: {dl_note}'}

    ok_direct, sn_direct = _is_valid_media_bytes(blob[:32])
    direct_mode = False
    variants_summary = ''
    final_bytes = b''
    sniffed_final = ''
    ok_final = False

    if ok_direct:
        final_bytes = blob
        sniffed_final = sn_direct
        ok_final = True
        direct_mode = True
        print(f'  [pk={queue_item.pk}] BLOB DIRETO JA VALIDO: sniffed={sn_direct} bytes={len(blob)} (pps/profile/nao-cripto)')
        variants_summary = f'direct_blob:{sn_direct}:::{len(blob)}'
    else:
        media_key = info.get('media_key') or ''
        if not media_key:
            head_hex_fail = blob[:16].hex()
            ok_mt = _sniff_mimetype(blob[:32])
            return {
                'status': 'SKIP_SEM_MEDIAKEY',
                'reason': f'blob invalido ({ok_mt or "desconhecido"} head={head_hex_fail}) e sem mediaKey no payload',
                'variants': '',
            }
        result = _decrypt_whatsapp_media(
            blob,
            media_key,
            media_type_hint=info.get('media_type_hint') or '',
            mimetype=info.get('mimetype') or '',
        )
        final_bytes = result['bytes']
        ok_final, sniffed_final = _is_valid_media_bytes(final_bytes[:32]) if final_bytes else (False, '')
        _vs = []
        for v in result['variants'][:12]:
            _vs.append(
                f"{v.get('v','?')}:"
                f"{v.get('sniffed') or v.get('ok') and 'ok' or 'x'}:"
                f"{v.get('note','')}:"
                f"{v.get('bytes',0)}"
            )
        variants_summary = ' | '.join(_vs)
        print(f'  [pk={queue_item.pk}] TDECRYPT: ok_final={ok_final} sniffed={sniffed_final} bytes={len(final_bytes or b"")}')
        print(f'    Variants (first 12): {variants_summary}')

        if not ok_final:
            return {
                'status': 'FAIL',
                'reason': 'sem_variante_valida',
                'variants': variants_summary,
            }

    final_mime = sniffed_final or (info.get('mimetype') if _sniff_mimetype(final_bytes[:32]) else '')
    filename = resolve_filename(info, final_mime)

    WhatsAppFinanceQueueAttachment.objects.filter(queue_item=queue_item).delete()

    from django.core.files.base import ContentFile
    att = WhatsAppFinanceQueueAttachment(
        queue_item=queue_item,
        mimetype=final_mime,
        original_name=filename,
        size_bytes=len(final_bytes),
        sha256=hashlib.sha256(final_bytes).hexdigest(),
    )
    att.file.save(filename, ContentFile(final_bytes), save=True)
    att.save()

    notes = queue_item.review_notes or ''
    stamp = datetime.now().strftime('%d/%m %H:%M')
    head_hex = final_bytes[:16].hex()
    fonte = 'recover_script_direct' if direct_mode else 'recover_script'
    block = (
        f"\n=== RECOVERED {stamp} ===\n"
        f"ANEXO_OK: sim  bytes={len(final_bytes)}  sniffed={final_mime}  fonte={fonte}\n"
        f"OBS: attachment.pk={att.pk} filename={filename} head_hex={head_hex}\n"
        f"URL_ORIG: {url}\n"
        f"MEDIA_KEY_PRESENTE: {'sim' if info.get('media_key') else 'nao'}  src_payload={src}\n"
    )
    queue_item.review_notes = notes + block
    queue_item.save(update_fields=['review_notes', 'updated_at'])

    return {
        'status': 'RECOVERED',
        'sniffed': final_mime,
        'bytes': len(final_bytes),
        'head_hex': head_hex,
        'att_pk': att.pk,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-list', type=int, default=20, help='Quantos itens mostrar na listagem inicial (padrao 20)')
    ap.add_argument('--limit', type=int, default=8, help='Quantos itens recuperar a partir do mais novo (padrao 8)')
    ap.add_argument('--pks', type=str, default='', help='Recuperar SOMENTE esses pks, separados por virgula. Ex: --pks=594,580,576')
    ap.add_argument('--skip-validated', action='store_true', default=False, help='Pula itens que ja tem anexo valido (padrao: pula de qualquer forma)')
    args = ap.parse_args()

    N = args.n_list
    items_all = list(WhatsAppFinanceQueueItem.objects.all().order_by('-created_at', '-id')[:N])
    print(f'=== Últimos {len(items_all)} queue items ===')
    items = items_all
    if args.pks:
        only = {int(x.strip()) for x in args.pks.split(',') if x.strip()}
        items = [it for it in items if it.pk in only]
        if len(items) != len(only):
            qs_extra = WhatsAppFinanceQueueItem.objects.filter(pk__in=only)
            found = {it.pk for it in items}
            for it in qs_extra:
                if it.pk not in found:
                    items.append(it)
            items.sort(key=lambda it: -it.pk)
    else:
        items = items_all[:args.limit]
    for it in items:
        try:
            att_count = it.attachments.count()
            first_att = it.attachments.first()
            size = first_att.size_bytes if first_att else None
            mime = (first_att.mimetype or '') if first_att else ''
        except Exception:
            att_count = 0
            size = None
            mime = ''
        created = it.created_at.strftime('%d/%m %H:%M') if it.created_at else '?'
        notes_preview = (it.review_notes or '').strip().replace('\n', ' ')[:120]
        print(
            f'  pk={it.pk:>4}  external={it.external_message_id or "":<20.20s}  '
            f'sender={(it.sender_name or it.sender_phone or "?"):<12.12s}  '
            f'created={created}  atts={att_count}  size={size}  mime={mime:<32.32s}  '
            f'notes={notes_preview!r}'
        )
    print()

    to_recover = items
    results = []
    for it in to_recover:
        print(f'--- pk={it.pk} sender={(it.sender_name or "?")} created={it.created_at} ---')
        try:
            r = recover_item(it)
        except Exception as e:
            r = {'status': 'EXCEPTION', 'reason': f'{type(e).__name__}: {e}'}
            import traceback
            traceback.print_exc()
        print(f'  RESULT: {r}')
        results.append((it.pk, r))
        print()

    print('=== RESUMO ===')
    for pk, r in results:
        print(f'  pk={pk:>4} -> {r.get("status"):>12s}  {r.get("sniffed") or r.get("reason") or ""}')


if __name__ == '__main__':
    main()
