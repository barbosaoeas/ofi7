from django.core.management.base import BaseCommand, CommandError

from budgets.models import XMLImportJob
from budgets.services.cilia_import_service import (
    CiliaImportDuplicateError,
    CiliaImportError,
    CiliaImportValidationError,
    import_cilia_xml_bytes,
)
from budgets.services.dropbox_service import DropboxConfigurationError, DropboxService, DropboxServiceError


class Command(BaseCommand):
    help = 'Sincroniza XMLs do Cilia a partir do Dropbox.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Limita a quantidade de arquivos processados nesta execução.',
        )

    def handle(self, *args, **options):
        limit = max(0, int(options.get('limit') or 0))
        service = DropboxService()

        try:
            entries = service.list_input_xml_files()
        except DropboxConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        except DropboxServiceError as exc:
            raise CommandError(str(exc)) from exc

        if limit:
            entries = entries[:limit]

        if not entries:
            self.stdout.write(self.style.WARNING('Nenhum XML novo encontrado na pasta de entrada do Dropbox.'))
            return

        imported_count = 0
        duplicate_count = 0
        error_count = 0
        skipped_count = 0

        for entry in entries:
            existing_job = XMLImportJob.objects.filter(external_file_id=entry.id).order_by('-id').first()
            if existing_job and existing_job.status in (
                XMLImportJob.Status.IMPORTED,
                XMLImportJob.Status.DUPLICATE,
            ):
                skipped_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f'Ignorado {entry.name}: arquivo já sincronizado anteriormente com status {existing_job.get_status_display()}.'
                    )
                )
                continue

            job = existing_job
            if job is None:
                job = XMLImportJob.objects.create(
                    provider=XMLImportJob.Provider.DROPBOX,
                    external_file_id=entry.id,
                    file_name=entry.name,
                    status=XMLImportJob.Status.PENDING,
                )
            else:
                job.provider = XMLImportJob.Provider.DROPBOX
                job.file_name = entry.name
                job.save(update_fields=['provider', 'file_name', 'updated_at'])

            try:
                xml_bytes = service.download_file(entry.path_display)
                import_cilia_xml_bytes(xml_bytes=xml_bytes, job=job)
                service.move_file(
                    entry.path_display,
                    service.build_destination_path(service.processed_path, entry.name),
                )
                imported_count += 1
                self.stdout.write(self.style.SUCCESS(f'Importado {entry.name} com sucesso.'))
            except CiliaImportDuplicateError as exc:
                service.move_file(
                    entry.path_display,
                    service.build_destination_path(service.error_path, entry.name),
                )
                duplicate_count += 1
                self.stdout.write(self.style.WARNING(f'Duplicado {entry.name}: {exc}'))
            except (CiliaImportValidationError, CiliaImportError, DropboxServiceError) as exc:
                if job.status != XMLImportJob.Status.ERROR:
                    job.status = XMLImportJob.Status.ERROR
                    job.error_message = str(exc)
                    job.save(update_fields=['status', 'error_message', 'updated_at'])
                try:
                    service.move_file(
                        entry.path_display,
                        service.build_destination_path(service.error_path, entry.name),
                    )
                except DropboxServiceError:
                    pass
                error_count += 1
                self.stdout.write(self.style.ERROR(f'Erro ao processar {entry.name}: {exc}'))

        self.stdout.write(
            self.style.SUCCESS(
                'Sincronização concluída. '
                f'Importados: {imported_count} | Duplicados: {duplicate_count} | Erros: {error_count} | Ignorados: {skipped_count}'
            )
        )
