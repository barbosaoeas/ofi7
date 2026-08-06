import json
import os
import sys
import textwrap
from django.core.management.base import BaseCommand

from budgets.services.dropbox_service import DropboxService, DropboxRefreshError


TOKEN_ENV_HELPER = (
    'Copie os exports abaixo (App Key, App Secret e REFRESH_TOKEN) '
    'para o seu arquivo seguro (ex: /home/ofi7ipojuca/.secrets/oficina_env.sh).'
)


class Command(BaseCommand):
    help = (
        'Gera um refresh_token do Dropbox (nunca expira). Três métodos disponíveis: '
        '(A) Método recomendado — OAuth Authorization Code (App Console + URL); '
        '(B) Método manual — gera um Access Token pelo Generated Token no App Console '
        '(não precisa de refresh, mas se você ver "Generated access tokens" como "deprecated" use o A).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--method',
            choices=['oauth', 'pkce'],
            default='oauth',
            help='oauth = metodo simples sem PKCE (recomendado); pkce = metodo avancado sem app secret.',
        )
        parser.add_argument(
            '--step',
            choices=['1', '2', '3'],
            default=None,
            help="Step 1: gera URL de autorizacao. Step 2: troca codigo por token.",
        )
        parser.add_argument('--app-key', type=str, default=None, help='App Key do Dropbox')
        parser.add_argument('--app-secret', type=str, default=None, help='App Secret do Dropbox (obrigatorio para --method oauth)')
        parser.add_argument(
            '--code-verifier',
            type=str,
            default=None,
            help='Code verifier do Step 1 PKCE (apenas --method pkce --step 2).',
        )
        parser.add_argument(
            '--auth-code',
            type=str,
            default=None,
            help='Código retornado pelo Dropbox depois de clicar em "Allow".',
        )
        parser.add_argument(
            '--env-file',
            type=str,
            default=None,
            help='Caminho para escrever o arquivo .sh final (chmod 0600).',
        )
        parser.add_argument(
            '--no-test',
            action='store_true',
            help='Nao executa sync_cilia_dropbox de teste apos gerar o arquivo.',
        )

    def handle(self, *args, **options):
        method = options['method']
        step = options.get('step')
        if step is None:
            self.stdout.write(self.style.WARNING('Selecione --step 1 ou --step 2. --method default = oauth.'))
            self.stdout.write(self.style.WARNING('Exemplos: '))
            self.stdout.write(
                '  python manage.py setup_dropbox_refresh_token --method oauth --step 1 --app-key KEY --app-secret SECRET'
            )
            self.stdout.write(
                '  python manage.py setup_dropbox_refresh_token --method oauth --step 2 --app-key KEY --app-secret SECRET --auth-code CODIGO --env-file "d:\\temp\\oficina_env.sh"'
            )
            sys.exit(1)
        if method == 'pkce':
            self._pkce_flow(options)
        else:
            self._oauth_flow(options)

    # ============================
    # Método A: OAuth Code Simples
    # ============================
    def _oauth_flow(self, options):
        step = options['step']
        app_key = (options.get('app_key') or os.getenv('DROPBOX_APP_KEY') or '').strip()
        app_secret = (options.get('app_secret') or os.getenv('DROPBOX_APP_SECRET') or '').strip()
        if step == '1':
            if not app_key:
                self.stderr.write(self.style.ERROR('Informe --app-key.'))
                sys.exit(2)
            url = DropboxService.build_oauth_auth_url(app_key=app_key, offline=True)
            self.stdout.write(
                textwrap.dedent(
                    f'''\
                    ################################################################
                    #  STEP 1/2 - Autorização no Dropbox (Método OAuth simples)   #
                    ################################################################
                    App Key usado: {app_key}

                    Pré-requisito: no painel do Dropbox App Console, vá em:
                      Permissions -> marque files.content.read e files.content.write
                      OAuth2 -> Redirect URIs -> adicione e SALVE:
                        https://www.dropbox.com/1/oauth2/dashboard

                    1) Abra a URL abaixo no seu navegador e clique em "Allow":
                    {url}

                    2) Após permitir, você cai no dashboard do próprio Dropbox.
                       Pegue o parâmetro "?code=XXXXXXXX" da barra de endereço
                       (copia só o valor entre code= e o próximo & ou fim da URL).

                    3) Execute o Step 2 passando --auth-code "VALOR_COPIADO_ACIMA"
                       junto de --app-key e --app-secret.
                    ################################################################
                    '''
                )
            )
            return

        # Step 2
        auth_code = (options.get('auth_code') or '').strip()
        env_file = (options.get('env_file') or '').strip()
        no_test = bool(options.get('no_test'))
        missing = []
        if not app_key:
            missing.append('--app-key')
        if not app_secret:
            missing.append('--app-secret')
        if not auth_code:
            missing.append('--auth-code')
        if missing:
            self.stderr.write(self.style.ERROR(f'Faltando argumento(s): {", ".join(missing)}'))
            sys.exit(2)

        service = DropboxService(app_key=app_key, app_secret=app_secret)
        try:
            tokens = service.exchange_oauth_code(code=auth_code)
        except DropboxRefreshError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            sys.exit(3)
        self._finalize(tokens=tokens, app_key=app_key, app_secret=app_secret, env_file=env_file, no_test=no_test)

    # ============================
    # Método B: PKCE (sem App Secret)
    # ============================
    def _pkce_flow(self, options):
        step = options['step']
        app_key = (options.get('app_key') or os.getenv('DROPBOX_APP_KEY') or '').strip()
        app_secret = (options.get('app_secret') or os.getenv('DROPBOX_APP_SECRET') or '').strip()
        if step == '1':
            if not app_key:
                self.stderr.write(self.style.ERROR('Informe --app-key.'))
                sys.exit(2)
            code_verifier, code_challenge = DropboxService.pkce_generate_pair()
            auth_url = DropboxService.build_pkce_auth_url(app_key=app_key, code_challenge=code_challenge)
            self.stdout.write(
                textwrap.dedent(
                    f'''\
                    ################################################################
                    #  STEP 1/2 - Autorização PKCE no Dropbox                      #
                    ################################################################
                    Guarde com segurança o CODE_VERIFIER abaixo (necessário Step 2):
                    CODE_VERIFIER={code_verifier}

                    Pré-requisito: Redirect URI cadastrado no App Console:
                      https://www.dropbox.com/1/oauth2/dashboard

                    1) Abra a URL abaixo no seu navegador e clique em "Allow":
                    {auth_url}

                    2) Copie o parâmetro ?code=XXXXXXXX da URL final.

                    3) Step 2:
                       python manage.py setup_dropbox_refresh_token --method pkce --step 2 \
                         --app-key "{app_key}" \
                         --code-verifier "{code_verifier}" \
                         --auth-code "CODIGO_DA_URL"
                    ################################################################
                    '''
                )
            )
            return

        auth_code = (options.get('auth_code') or '').strip()
        code_verifier = (options.get('code_verifier') or '').strip()
        env_file = (options.get('env_file') or '').strip()
        no_test = bool(options.get('no_test'))
        missing = []
        if not app_key:
            missing.append('--app-key')
        if not code_verifier:
            missing.append('--code-verifier')
        if not auth_code:
            missing.append('--auth-code')
        if missing:
            self.stderr.write(self.style.ERROR(f'Faltando argumento(s): {", ".join(missing)}'))
            sys.exit(2)

        service = DropboxService(app_key=app_key, app_secret=app_secret)
        try:
            tokens = service.exchange_pkce_code(code=auth_code, code_verifier=code_verifier)
        except DropboxRefreshError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            sys.exit(3)
        self._finalize(tokens=tokens, app_key=app_key, app_secret=app_secret, env_file=env_file, no_test=no_test)

    # ============================
    # Finalização (qualquer método)
    # ============================
    def _finalize(self, *, tokens, app_key, app_secret, env_file, no_test):
        refresh_token = (tokens.get('refresh_token') or '').strip()
        access_token = (tokens.get('access_token') or '').strip()
        scope = (tokens.get('scope') or '').strip()
        if not refresh_token:
            self.stderr.write(
                self.style.ERROR(
                    'Dropbox não retornou refresh_token. Verifique se a URL de autorização '
                    'contém token_access_type=offline e se a permissão foi concedida.'
                )
            )
            sys.exit(4)
        self.stdout.write(
            textwrap.dedent(
                f'''\
                ################################################################
                #  TOKENS OBTIDOS COM SUCESSO                                  #
                ################################################################
                Scope       : {scope or '(vazio)'}
                Refresh Tk  : {refresh_token[:10]}… (tamanho {len(refresh_token)})
                Access Tk   : {access_token[:10]}… (tamanho {len(access_token)})
                ################################################################
                '''
            )
        )
        self.stdout.write(self.style.SUCCESS(TOKEN_ENV_HELPER))
        self.stdout.write('')

        content = (
            'export DROPBOX_CILIA_ENABLED=true\n'
            f'export DROPBOX_APP_KEY={app_key}\n'
            f'export DROPBOX_APP_SECRET={app_secret}\n'
            f'export DROPBOX_REFRESH_TOKEN={refresh_token}\n'
            f'export DROPBOX_ACCESS_TOKEN={access_token}\n'
            'export DROPBOX_CILIA_INPUT_PATH=/xml-cilia/entrada\n'
            'export DROPBOX_CILIA_PROCESSED_PATH=/xml-cilia/processados\n'
            'export DROPBOX_CILIA_ERROR_PATH=/xml-cilia/erro\n'
        )
        if not env_file:
            self.stdout.write(self.style.WARNING('Conteúdo sugerido para o .env / oficina_env.sh:'))
            self.stdout.write(content)
            return
        try:
            env_dir = os.path.dirname(env_file)
            if env_dir and not os.path.exists(env_dir):
                os.makedirs(env_dir, exist_ok=True)
            with open(env_file, 'w', encoding='utf-8') as f:
                f.write(content)
            os.chmod(env_file, 0o600)
        except OSError as exc:
            self.stderr.write(self.style.ERROR(f'Erro ao escrever {env_file}: {exc}'))
            sys.exit(5)
        self.stdout.write(self.style.SUCCESS(f'Arquivo salvo (chmod 600): {env_file}'))

        if no_test:
            return

        self.stdout.write('Executando sync_cilia_dropbox de teste com as novas credenciais...')
        import subprocess

        env = os.environ.copy()
        env['DROPBOX_CILIA_ENABLED'] = 'true'
        env['DROPBOX_APP_KEY'] = app_key
        env['DROPBOX_APP_SECRET'] = app_secret
        env['DROPBOX_REFRESH_TOKEN'] = refresh_token
        env['DROPBOX_ACCESS_TOKEN'] = access_token
        env['DROPBOX_CILIA_INPUT_PATH'] = '/xml-cilia/entrada'
        env['DROPBOX_CILIA_PROCESSED_PATH'] = '/xml-cilia/processados'
        env['DROPBOX_CILIA_ERROR_PATH'] = '/xml-cilia/erro'
        env['PYTHONUNBUFFERED'] = '1'
        try:
            result = subprocess.run(
                [sys.executable, 'manage.py', 'sync_cilia_dropbox'],
                capture_output=True,
                text=True,
                env=env,
                cwd=os.getcwd(),
                timeout=90,
            )
        except subprocess.TimeoutExpired:
            self.stderr.write(self.style.ERROR('Teste sync_cilia_dropbox timeout.'))
            sys.exit(6)
        if result.returncode == 0:
            self.stdout.write(self.style.SUCCESS('TESTE DO DROPBOX PASSOU. O REFRESH RENOVA AUTOMATICAMENTE.'))
            for line in (result.stdout or '').splitlines():
                self.stdout.write('  ' + line)
        else:
            self.stderr.write(self.style.ERROR(f'Teste falhou. Codigo: {result.returncode}'))
            out = ((result.stdout or '') + (result.stderr or '')).splitlines() or []
            for line in out[-80:]:
                self.stderr.write('  ' + line)
            sys.exit(7)
