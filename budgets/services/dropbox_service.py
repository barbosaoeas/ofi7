from dataclasses import dataclass
import posixpath

from django.conf import settings

import requests


class DropboxServiceError(Exception):
    pass


class DropboxConfigurationError(DropboxServiceError):
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

    def __init__(
        self,
        *,
        access_token=None,
        input_path=None,
        processed_path=None,
        error_path=None,
        session=None,
        enabled=None,
    ):
        self.enabled = settings.DROPBOX_CILIA_ENABLED if enabled is None else enabled
        self.access_token = (settings.DROPBOX_ACCESS_TOKEN if access_token is None else access_token).strip()
        self.input_path = self._normalize_path(
            settings.DROPBOX_CILIA_INPUT_PATH if input_path is None else input_path
        )
        self.processed_path = self._normalize_path(
            settings.DROPBOX_CILIA_PROCESSED_PATH if processed_path is None else processed_path
        )
        self.error_path = self._normalize_path(
            settings.DROPBOX_CILIA_ERROR_PATH if error_path is None else error_path
        )
        self.session = session or requests.Session()

    def ensure_configured(self):
        if not self.enabled:
            raise DropboxConfigurationError(
                'A integração Dropbox está desabilitada. Defina DROPBOX_CILIA_ENABLED=true para usar a sincronização.'
            )
        if not self.access_token:
            raise DropboxConfigurationError(
                'DROPBOX_ACCESS_TOKEN não configurado. Informe o token da app do Dropbox antes de rodar a sincronização.'
            )

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
        response = self.session.post(
            f'{self.CONTENT_BASE}/files/download',
            headers={
                'Authorization': f'Bearer {self.access_token}',
                'Dropbox-API-Arg': self._json_dump({'path': normalized_path}),
            },
            timeout=60,
        )
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

    def _post_json(self, url, payload):
        response = self.session.post(
            url,
            headers={
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=60,
        )
        self._raise_for_error(response, action='comunicar com o Dropbox')
        try:
            return response.json()
        except ValueError as exc:
            raise DropboxServiceError('Resposta inválida recebida do Dropbox.') from exc

    def _raise_for_error(self, response, *, action):
        if response.ok:
            return
        details = ''
        try:
            data = response.json()
            details = data.get('error_summary') or ''
        except ValueError:
            details = (response.text or '').strip()
        message = f'Não foi possível {action}.'
        if details:
            message = f'{message} {details}'
        raise DropboxServiceError(message)

    def _normalize_path(self, path):
        raw = (path or '').strip()
        if not raw:
            return '/'
        normalized = raw if raw.startswith('/') else f'/{raw}'
        return posixpath.normpath(normalized)

    def _json_dump(self, payload):
        import json

        return json.dumps(payload, ensure_ascii=True)
