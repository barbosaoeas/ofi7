from base64 import standard_b64encode, urlsafe_b64encode
from dataclasses import dataclass
import hashlib
import json
import os
import posixpath
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from django.conf import settings

import requests


class DropboxServiceError(Exception):
    pass


class DropboxConfigurationError(DropboxServiceError):
    pass


class DropboxRefreshError(DropboxServiceError):
    pass


@dataclass(frozen=True)
class DropboxEntry:
    id: str
    name: str
    path_display: str
    path_lower: str
    size: int = 0


class DropboxService:
    API_BASE = 'https://api.dropboxapi.com/2'
    CONTENT_BASE = 'https://content.dropboxapi.com/2'
    TOKEN_URL = 'https://api.dropboxapi.com/oauth2/token'
    PKCE_REDIRECT_URI = 'https://www.dropbox.com/1/oauth2/dashboard'

    def __init__(
        self,
        *,
        access_token=None,
        refresh_token=None,
        app_key=None,
        app_secret=None,
        input_path=None,
        processed_path=None,
        error_path=None,
        session=None,
        enabled=None,
        token_state_file=None,
    ):
        self.enabled = settings.DROPBOX_CILIA_ENABLED if enabled is None else enabled
        self.app_key = (settings.DROPBOX_APP_KEY if app_key is None else app_key or '').strip()
        self.app_secret = (
            settings.DROPBOX_APP_SECRET if app_secret is None else app_secret or ''
        ).strip()
        self.refresh_token = (
            settings.DROPBOX_REFRESH_TOKEN if refresh_token is None else refresh_token or ''
        ).strip()
        self.access_token = (settings.DROPBOX_ACCESS_TOKEN if access_token is None else access_token or '').strip()
        self.input_path = self._normalize_path(
            settings.DROPBOX_CILIA_INPUT_PATH if input_path is None else input_path
        )
        self.processed_path = self._normalize_path(
            settings.DROPBOX_CILIA_PROCESSED_PATH if processed_path is None else processed_path
        )
        self.error_path = self._normalize_path(
            settings.DROPBOX_CILIA_ERROR_PATH if error_path is None else error_path
        )
        self.token_state_file = (
            settings.DROPBOX_TOKEN_STATE_FILE if token_state_file is None else token_state_file or ''
        ).strip()
        self.session = session or requests.Session()
        self._access_token_lock = False
        self._max_refresh_attempts = 2

    # ---------------------------
    # Config + refresh token logic
    # ---------------------------

    def _can_refresh(self) -> bool:
        return bool(self.refresh_token and self.app_key)

    def ensure_configured(self):
        if not self.enabled:
            raise DropboxConfigurationError(
                'A integração Dropbox está desabilitada. Defina DROPBOX_CILIA_ENABLED=true para usar a sincronização.'
            )
        has_legacy = bool(self.access_token)
        has_refresh_flow = self._can_refresh()
        if not has_legacy and not has_refresh_flow:
            raise DropboxConfigurationError(
                'Nenhuma credencial Dropbox configurada. '
                'Configure DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY (recomendado, renova automaticamente) '
                'ou DROPBOX_ACCESS_TOKEN (modo legado).'
            )

    def diagnostics(self) -> Dict[str, Any]:
        def _mask(v, show=4):
            s = str(v or '')
            if len(s) <= show * 2:
                return '*' * len(s)
            return s[:show] + '…' + s[-show:] + f' (tam {len(s)})'

        has_legacy = bool(self.access_token)
        has_refresh_flow = self._can_refresh()
        state = self._load_state() or {}
        state_has_token = bool(state.get('access_token'))
        expires_at = state.get('expires_at')
        if expires_at and isinstance(expires_at, (int, float)):
            from datetime import datetime, timezone
            try:
                exp_dt = datetime.fromtimestamp(float(expires_at), tz=timezone.utc)
                expires_desc = exp_dt.isoformat()
                if float(expires_at) < time.time():
                    expires_desc += ' (EXPIRADO)'
                else:
                    segundos = max(0, int(float(expires_at) - time.time()))
                    horas, seg = divmod(segundos, 3600)
                    minutos, _ = divmod(seg, 60)
                    expires_desc += f' (faltam ~{horas}h{minutos}m)'
            except Exception:
                expires_desc = str(expires_at)
        elif state_has_token:
            expires_desc = 'sem informacao'
        else:
            expires_desc = 'nao salvo ainda'
        return {
            'enabled': self.enabled,
            'DROPBOX_APP_KEY': ('CONFIGURADO' if self.app_key else 'AUSENTE') + (f' ({_mask(self.app_key)})' if self.app_key else ''),
            'DROPBOX_APP_SECRET': ('CONFIGURADO' if self.app_secret else 'AUSENTE') + (f' ({_mask(self.app_secret, show=2)})' if self.app_secret else ''),
            'DROPBOX_REFRESH_TOKEN': ('CONFIGURADO' if self.refresh_token else 'AUSENTE') + (f' {_mask(self.refresh_token, show=6)}' if self.refresh_token else ''),
            'DROPBOX_ACCESS_TOKEN (env)': ('CONFIGURADO' if has_legacy else 'AUSENTE') + (f' {_mask(self.access_token, show=6)}' if has_legacy else ''),
            'DROPBOX_TOKEN_STATE_FILE': self.token_state_file or '(nao definido)',
            'state.estado_token_salvo': ('access_token salvo: ' + _mask(state['access_token'], show=6)) if state_has_token else 'nao salvo',
            'state.expires_at': expires_desc,
            'modo_auto_refresh': 'ATIVO' if has_refresh_flow else 'INATIVO (modo legado access_token; expira ~4h)',
            'input_path': self.input_path,
            'processed_path': self.processed_path,
            'error_path': self.error_path,
            'can_refresh_dropbox': has_refresh_flow,
        }

    def _token_state_path(self) -> Optional[str]:
        path = (self.token_state_file or '').strip()
        if not path:
            return None
        return path

    def _load_state(self) -> Dict[str, Any]:
        path = self._token_state_path()
        if not path:
            return {}
        try:
            if not os.path.exists(path):
                return {}
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
        return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        path = self._token_state_path()
        if not path:
            return
        try:
            directory = os.path.dirname(path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(state, f)
            except Exception:
                try:
                    os.chmod(path, 0o600)
                except Exception:
                    pass
                return
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
        except Exception:
            return

    def _refresh_access_token(self) -> None:
        if not self._can_refresh():
            raise DropboxRefreshError(
                'Não foi possível renovar o token do Dropbox (DROPBOX_REFRESH_TOKEN e DROPBOX_APP_KEY ausentes).'
            )

        basic_token = standard_b64encode(f'{self.app_key}:{self.app_secret or ""}'.encode('utf-8')).decode(
            'ascii'
        )
        headers = {
            'Authorization': f'Basic {basic_token}',
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
        }
        resp = self.session.post(self.TOKEN_URL, headers=headers, data=payload, timeout=30)
        if resp.status_code != 200:
            details = ''
            try:
                details = (resp.json() or {}).get('error_description') or resp.text or ''
            except Exception:
                details = resp.text or ''
            raise DropboxRefreshError(
                f'Falha ao renovar token do Dropbox (HTTP {resp.status_code}). {details}'
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise DropboxRefreshError('Resposta inválida do Dropbox durante renovação de token.') from exc
        new_token = (data.get('access_token') or '').strip()
        new_refresh = (data.get('refresh_token') or '').strip()
        if not new_token:
            raise DropboxRefreshError('Dropbox não retornou um novo access_token na renovação.')
        expires_in = int(data.get('expires_in') or 14400)
        self.access_token = new_token
        if new_refresh:
            self.refresh_token = new_refresh
        new_state = {
            'access_token': new_token,
            'refresh_token': self.refresh_token,
            'expires_at': int(time.time()) + int(expires_in) - 60,
            'updated_at': int(time.time()),
        }
        self._save_state(new_state)

    def _ensure_valid_access_token(self, force=False) -> None:
        if self._access_token_lock:
            return
        self._access_token_lock = True
        try:
            if not self._can_refresh():
                if not self.access_token:
                    raise DropboxRefreshError('Dropbox sem credenciais válidas.')
                return
            state = self._load_state() or {}
            if not self.access_token and state.get('access_token'):
                self.access_token = str(state['access_token']).strip()
            if not self.refresh_token and state.get('refresh_token'):
                self.refresh_token = str(state['refresh_token']).strip()
            expires_at = state.get('expires_at')
            needs = force
            if not needs and expires_at and isinstance(expires_at, (int, float)):
                if time.time() >= float(expires_at):
                    needs = True
            if not needs and not self.access_token:
                needs = True
            if needs:
                self._refresh_access_token()
        finally:
            self._access_token_lock = False

    # ---------------------------
    # File operations
    # ---------------------------

    def list_input_xml_files(self):
        entries = self.list_folder(self.input_path)
        xml_entries = []
        for entry in entries:
            tag = entry.get('.tag')
            name = (entry.get('name') or '').strip()
            if tag != 'file' or not name.lower().endswith('.xml'):
                continue
            xml_entries.append(
                DropboxEntry(
                    id=entry.get('id', ''),
                    name=name,
                    path_display=entry.get('path_display') or name,
                    path_lower=entry.get('path_lower') or name.lower(),
                    size=int(entry.get('size') or 0),
                )
            )
        return xml_entries

    def list_folder(self, path):
        self.ensure_configured()
        url = f'{self.API_BASE}/files/list_folder'
        payload = {
            'path': self._normalize_path(path),
            'recursive': False,
            'include_deleted': False,
        }
        data = self._post_json(url, payload)
        entries = list(data.get('entries', []))
        cursor = data.get('cursor')
        while data.get('has_more') and cursor:
            data = self._post_json(
                f'{self.API_BASE}/files/list_folder/continue',
                {'cursor': cursor},
            )
            entries.extend(data.get('entries', []))
            cursor = data.get('cursor')
        return entries

    def download_file(self, path):
        self.ensure_configured()
        normalized_path = self._normalize_path(path)

        def do_request():
            self._ensure_valid_access_token()
            return self.session.post(
                f'{self.CONTENT_BASE}/files/download',
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Dropbox-API-Arg': self._json_dump({'path': normalized_path}),
                },
                timeout=60,
            )

        response = self._request_with_refresh(do_request)
        self._raise_for_error(response, action='baixar arquivo do Dropbox')
        return response.content

    def move_file(self, from_path, to_path):
        self.ensure_configured()
        payload = {
            'from_path': self._normalize_path(from_path),
            'to_path': self._normalize_path(to_path),
            'autorename': True,
            'allow_shared_folder': True,
        }
        return self._post_json(f'{self.API_BASE}/files/move_v2', payload)

    def build_destination_path(self, base_path, file_name):
        normalized_base = self._normalize_path(base_path)
        clean_name = (file_name or '').strip().lstrip('/')
        return posixpath.join(normalized_base, clean_name)

    # ---------------------------
    # Low-level request helpers
    # ---------------------------

    def _post_json(self, url, payload):
        def do_request():
            self._ensure_valid_access_token()
            return self.session.post(
                url,
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=60,
            )

        response = self._request_with_refresh(do_request)
        self._raise_for_error(response, action='comunicar com o Dropbox')
        try:
            return response.json()
        except ValueError as exc:
            raise DropboxServiceError('Resposta inválida recebida do Dropbox.') from exc

    def _request_with_refresh(self, do_request):
        last_error: Optional[Exception] = None
        for attempt in range(self._max_refresh_attempts):
            try:
                if attempt == 0:
                    self._ensure_valid_access_token(force=False)
                else:
                    self._ensure_valid_access_token(force=True)
                response = do_request()
            except DropboxRefreshError as exc:
                last_error = exc
                continue
            token_error, _ = self._detect_token_error(response)
            if token_error and self._can_refresh():
                last_error = DropboxRefreshError(
                    f'Tentativa {attempt + 1} detectou token expirado/invalido; tentando renovar.'
                )
                continue
            return response
        if last_error is not None:
            raise last_error
        raise DropboxServiceError('Falha repetida ao acessar Dropbox após renovação de token.')

    def _detect_token_error(self, response) -> (bool, str):
        status = getattr(response, 'status_code', None)
        if status == 401:
            return True, 'HTTP 401 Unauthorized'
        if status == 200:
            return False, ''
        summary = ''
        try:
            data = response.json()
            if isinstance(data, dict):
                summary = str(data.get('error_summary') or '')
                if not summary:
                    err = data.get('error')
                    if isinstance(err, dict):
                        summary = str(err.get('.tag') or '')
        except Exception:
            summary = ''
        text = (summary or '').lower()
        token_keywords = (
            'expired_access_token',
            'invalid_access_token',
            'access_denied',
            'invalid_token',
            'auth_error',
            'authentication_error',
        )
        if any(k in text for k in token_keywords):
            return True, summary
        return False, summary

    def _raise_for_error(self, response, *, action):
        if response.ok:
            return
        token_error, summary = self._detect_token_error(response)
        details = summary
        if not details:
            try:
                data = response.json()
                details = data.get('error_summary') or ''
            except ValueError:
                details = (response.text or '').strip()
        message = f'Não foi possível {action}.'
        if token_error:
            can_refresh = self._can_refresh()
            missing = []
            if not self.app_key:
                missing.append('DROPBOX_APP_KEY')
            if not self.refresh_token:
                missing.append('DROPBOX_REFRESH_TOKEN')
            if can_refresh:
                hint = (
                    ' Houve tentativa de renovação mas o refresh também falhou. '
                    'Verifique se DROPBOX_APP_KEY, DROPBOX_APP_SECRET e DROPBOX_REFRESH_TOKEN estão corretos.'
                )
            else:
                miss = ', '.join(missing) if missing else 'variáveis de renovação automática'
                hint = (
                    f' Atualmente {miss} não foram configurados no arquivo de segredos — você está usando apenas o '
                    f'DROPBOX_ACCESS_TOKEN legado que expira a cada ~4h. Volte a configurar o setup_dropbox_refresh_token '
                    f'para gerar DROPBOX_REFRESH_TOKEN e evitar este erro no futuro. Como correção de emergência, rode '
                    f'update_dropbox_token e cole um access_token novo temporário.'
                )
            message = f'{message} Erro de autenticação/Token expirado ({summary or response.status_code}).{hint}'
        elif details:
            message = f'{message} {details}'
        raise DropboxServiceError(message)

    # ---------------------------
    # Misc
    # ---------------------------

    def _normalize_path(self, path):
        raw = (path or '').strip()
        if not raw:
            return '/'
        normalized = raw if raw.startswith('/') else f'/{raw}'
        return posixpath.normpath(normalized)

    @staticmethod
    def _json_dump(payload):
        return json.dumps(payload, ensure_ascii=True)

    # ---------------------------
    # Helpers para gerar refresh token via PKCE (Management Command)
    # ---------------------------

    @classmethod
    def pkce_generate_pair(cls):
        code_verifier = secrets.token_urlsafe(96)
        digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
        code_challenge = urlsafe_b64encode(digest).decode('ascii').rstrip('=')
        return code_verifier, code_challenge

    @classmethod
    def build_oauth_auth_url(cls, app_key: str, *, offline: bool = True) -> str:
        params = {
            'client_id': app_key,
            'response_type': 'code',
            'redirect_uri': cls.PKCE_REDIRECT_URI,
        }
        if offline:
            params['token_access_type'] = 'offline'
        return 'https://www.dropbox.com/oauth2/authorize?' + urlencode(params)

    @classmethod
    def build_pkce_auth_url(cls, app_key: str, code_challenge: str) -> str:
        params = {
            'client_id': app_key,
            'response_type': 'code',
            'token_access_type': 'offline',
            'redirect_uri': cls.PKCE_REDIRECT_URI,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        }
        return 'https://www.dropbox.com/oauth2/authorize?' + urlencode(params)

    def exchange_oauth_code(self, code: str):
        if not self.app_secret:
            raise DropboxRefreshError('Método OAuth requer DROPBOX_APP_SECRET.')
        basic_token = standard_b64encode(
            f'{self.app_key}:{self.app_secret}'.encode('utf-8')
        ).decode('ascii')
        headers = {
            'Authorization': f'Basic {basic_token}',
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.PKCE_REDIRECT_URI,
        }
        resp = self.session.post(self.TOKEN_URL, headers=headers, data=payload, timeout=30)
        if resp.status_code != 200:
            details = ''
            try:
                details = (resp.json() or {}).get('error_description') or resp.text or ''
            except Exception:
                details = resp.text or ''
            raise DropboxRefreshError(
                f'Falha ao trocar codigo OAuth por refresh_token (HTTP {resp.status_code}). {details}'
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise DropboxRefreshError('Resposta inválida do Dropbox durante troca de código OAuth.') from exc
        return {
            'access_token': (data.get('access_token') or '').strip(),
            'refresh_token': (data.get('refresh_token') or '').strip(),
            'expires_in': int(data.get('expires_in') or 14400),
            'token_type': (data.get('token_type') or '').strip(),
            'scope': (data.get('scope') or '').strip(),
        }

    def exchange_pkce_code(self, code: str, code_verifier: str):
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        payload = {
            'grant_type': 'authorization_code',
            'client_id': self.app_key,
            'code': code,
            'redirect_uri': self.PKCE_REDIRECT_URI,
            'code_verifier': code_verifier,
        }
        if self.app_secret:
            basic_token = standard_b64encode(
                f'{self.app_key}:{self.app_secret}'.encode('utf-8')
            ).decode('ascii')
            headers['Authorization'] = f'Basic {basic_token}'
        resp = self.session.post(self.TOKEN_URL, headers=headers, data=payload, timeout=30)
        if resp.status_code != 200:
            details = ''
            try:
                details = (resp.json() or {}).get('error_description') or resp.text or ''
            except Exception:
                details = resp.text or ''
            raise DropboxRefreshError(
                f'Falha ao trocar código PKCE por refresh_token (HTTP {resp.status_code}). {details}'
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise DropboxRefreshError('Resposta inválida do Dropbox durante troca de código PKCE.') from exc
        return {
            'access_token': (data.get('access_token') or '').strip(),
            'refresh_token': (data.get('refresh_token') or '').strip(),
            'expires_in': int(data.get('expires_in') or 14400),
            'token_type': (data.get('token_type') or '').strip(),
            'scope': (data.get('scope') or '').strip(),
        }
