import os
import sys
from django.core.management.base import BaseCommand


ENV_FILE_DEFAULT_PATH = '/home/ofi7ipojuca/.secrets/oficina_env.sh'


class Command(BaseCommand):
    help = (
        'Atualiza o token do Dropbox no arquivo seguro e testa a conexao. '
        'Uso: python manage.py update_dropbox_token "SEU_NOVO_TOKEN" '
        '[--env-file /caminho/para/oficina_env.sh]'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'token',
            type=str,
            help='Novo access token do Dropbox (entre aspas)',
        )
        parser.add_argument(
            '--env-file',
            type=str,
            default=None,
            help='Caminho do arquivo .sh seguro. Padrao: ' + ENV_FILE_DEFAULT_PATH,
        )
        parser.add_argument(
            '--no-test',
            action='store_true',
            help='Nao testa a conexao com o Dropbox apos salvar',
        )

    def handle(self, *args, **options):
        token = options['token'].strip()
        env_file = options['env_file'] or os.environ.get(
            'OFICINA_ENV_SH', ENV_FILE_DEFAULT_PATH
        )
        no_test = options['no_test']

        if not token:
            self.stderr.write(self.style.ERROR('Token vazio. Informe o token como argumento.'))
            sys.exit(1)

        if not token.startswith('sl.'):
            self.stdout.write(
                self.style.WARNING(
                    'Aviso: o token nao comeca com "sl.". Verifique se ele foi colado certo.'
                )
            )

        content = (
            'export DROPBOX_CILIA_ENABLED=true\n'
            f'export DROPBOX_ACCESS_TOKEN={token}\n'
            'export DROPBOX_CILIA_INPUT_PATH=/xml-cilia/entrada\n'
            'export DROPBOX_CILIA_PROCESSED_PATH=/xml-cilia/processados\n'
            'export DROPBOX_CILIA_ERROR_PATH=/xml-cilia/erro\n'
        )

        try:
            env_dir = os.path.dirname(env_file)
            if env_dir and not os.path.exists(env_dir):
                os.makedirs(env_dir, exist_ok=True)

            with open(env_file, 'w', encoding='utf-8') as f:
                f.write(content)

            os.chmod(env_file, 0o600)
        except PermissionError as e:
            self.stderr.write(self.style.ERROR(f'Sem permissao para escrever em {env_file}: {e}'))
            sys.exit(2)
        except OSError as e:
            self.stderr.write(self.style.ERROR(f'Erro ao escrever arquivo {env_file}: {e}'))
            sys.exit(2)

        self.stdout.write(self.style.SUCCESS(f'Token salvo com sucesso em: {env_file}'))
        self.stdout.write(f'Tamanho do token: {len(token)} caracteres')
        self.stdout.write('')

        if no_test:
            self.stdout.write(self.style.WARNING('Teste de conexao pulado (--no-test).'))
            return

        self.stdout.write('Testando conexao com o Dropbox...')

        env = os.environ.copy()
        env['DROPBOX_CILIA_ENABLED'] = 'true'
        env['DROPBOX_ACCESS_TOKEN'] = token
        env['DROPBOX_CILIA_INPUT_PATH'] = '/xml-cilia/entrada'
        env['DROPBOX_CILIA_PROCESSED_PATH'] = '/xml-cilia/processados'
        env['DROPBOX_CILIA_ERROR_PATH'] = '/xml-cilia/erro'
        env['PYTHONUNBUFFERED'] = '1'

        cmd = [sys.executable, 'manage.py', 'sync_cilia_dropbox']
        import subprocess

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                cwd=os.getcwd(),
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            self.stderr.write(self.style.ERROR('Teste demorou muito (timeout).'))
            sys.exit(3)

        output = (result.stdout or '') + (result.stderr or '')

        if result.returncode == 0:
            self.stdout.write(self.style.SUCCESS('Conexao com Dropbox OK!'))
            for line in (result.stdout or '').splitlines():
                self.stdout.write('  ' + line)
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('Tudo certo. Pode agendar ou executar o sync normalmente.'))
        else:
            self.stderr.write(self.style.ERROR(f'Falha no teste (codigo {result.returncode}). Saida:'))
            for line in output.splitlines():
                self.stderr.write('  ' + line)
            sys.exit(result.returncode or 4)
