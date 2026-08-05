from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer, Vehicle
from users.models import Collaborator, CustomUser

from .models import BankAccount, Budget, BudgetPhoto, CashCategory, CashMovement, CommissionLine, Piece, Supplier, ThirdPartyService, WorkOrder, WorkOrderTask, XMLImportJob
from .cilia_parser import extract_service_lines
from .forms import ThirdPartyServiceForm
from .services.cilia_import_service import CiliaImportDuplicateError, import_cilia_xml_bytes
from .services.dropbox_service import DropboxEntry, DropboxService
from .views import budget_administrative_closure_status, budget_delivery_status, capped_work_delta_seconds, get_visible_third_party_services, parse_xml_created_at


class SmokePermissionsTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(email='manager@test.com', password=self.password, role=CustomUser.Role.MANAGER)
        self.finance = CustomUser.objects.create_user(email='finance@test.com', password=self.password, role=CustomUser.Role.FINANCE)
        self.estimator = CustomUser.objects.create_user(email='estimator@test.com', password=self.password, role=CustomUser.Role.ESTIMATOR)
        self.operational = CustomUser.objects.create_user(
            email='operational@test.com',
            password=self.password,
            role=CustomUser.Role.OPERATIONAL,
        )
        self.visual = CustomUser.objects.create_user(email='visual@test.com', password=self.password, role=CustomUser.Role.VISUAL)

    def _assert_redirect_to(self, response, view_name):
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers.get('Location', '').endswith(reverse(view_name)))

    def test_manager_access_smoke(self):
        self.client.login(email=self.manager.email, password=self.password)
        urls = [
            reverse('core:dashboard'),
            reverse('core:system_settings'),
            reverse('budgets:budget_list'),
            reverse('budgets:budget_open_list'),
            reverse('budgets:import_job_list'),
            reverse('budgets:finance_dashboard'),
            reverse('budgets:finance_insights'),
            reverse('budgets:workorder_list'),
            reverse('budgets:kanban_today'),
            reverse('budgets:vehicle_entry_kanban'),
            reverse('budgets:commission_open_list'),
            reverse('budgets:report_pieces'),
            reverse('customers:customer_list'),
            reverse('users:collaborator_list'),
            reverse('budgets:service_catalog_list'),
            reverse('budgets:bank_account_list'),
            reverse('budgets:supplier_list'),
        ]
        for url in urls:
            r = self.client.get(url)
            self.assertNotEqual(r.status_code, 403)
            self.assertNotEqual(r.status_code, 404)

    def test_estimator_permissions_smoke(self):
        self.client.login(email=self.estimator.email, password=self.password)
        allowed = [
            'budgets:budget_list',
            'budgets:budget_open_list',
            'budgets:import_job_list',
            'budgets:kanban_today',
            'budgets:vehicle_entry_kanban',
            'budgets:commission_open_list',
            'budgets:report_pieces',
        ]
        blocked = [
            'budgets:finance_dashboard',
            'budgets:finance_insights',
            'budgets:workorder_list',
            'customers:customer_list',
            'users:collaborator_list',
            'budgets:service_catalog_list',
            'budgets:bank_account_list',
            'budgets:supplier_list',
            'core:system_settings',
        ]
        for name in allowed:
            r = self.client.get(reverse(name))
            self.assertNotEqual(r.status_code, 403)
            self.assertNotEqual(r.status_code, 404)
        for name in blocked:
            r = self.client.get(reverse(name))
            self._assert_redirect_to(r, 'core:dashboard')

    def test_operational_permissions_smoke(self):
        self.client.login(email=self.operational.email, password=self.password)
        allowed = [
            'budgets:kanban_today',
            'budgets:commission_open_list',
        ]
        blocked = [
            'budgets:budget_list',
            'budgets:budget_open_list',
            'budgets:import_job_list',
            'budgets:finance_dashboard',
            'budgets:finance_insights',
            'budgets:workorder_list',
            'budgets:vehicle_entry_kanban',
            'budgets:report_pieces',
            'customers:customer_list',
            'users:collaborator_list',
            'budgets:service_catalog_list',
            'budgets:bank_account_list',
            'budgets:supplier_list',
            'core:system_settings',
        ]
        for name in allowed:
            r = self.client.get(reverse(name))
            self.assertNotEqual(r.status_code, 403)
            self.assertNotEqual(r.status_code, 404)
        for name in blocked:
            r = self.client.get(reverse(name))
            self._assert_redirect_to(r, 'core:dashboard')

    def test_visual_permissions_smoke(self):
        self.client.login(email=self.visual.email, password=self.password)
        allowed = ['budgets:kanban_today']
        blocked = [
            'core:dashboard',
            'budgets:budget_list',
            'budgets:budget_open_list',
            'budgets:import_job_list',
            'budgets:finance_dashboard',
            'budgets:finance_insights',
            'budgets:workorder_list',
            'budgets:vehicle_entry_kanban',
            'budgets:commission_open_list',
            'budgets:report_pieces',
            'customers:customer_list',
            'users:collaborator_list',
            'budgets:service_catalog_list',
            'budgets:bank_account_list',
            'budgets:supplier_list',
            'core:system_settings',
        ]
        for name in allowed:
            r = self.client.get(reverse(name))
            self.assertNotEqual(r.status_code, 403)
            self.assertNotEqual(r.status_code, 404)
        for name in blocked:
            r = self.client.get(reverse(name))
            self._assert_redirect_to(r, 'budgets:kanban_today')

    def test_budget_list_shows_only_authorized(self):
        self.client.login(email=self.manager.email, password=self.password)
        customer = Customer.objects.create(name='Cliente Lista', document_cpf_cnpj='999')
        vehicle = Vehicle.objects.create(customer=customer, plate='BBB1B11', brand='Marca', model='Modelo')
        approved = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            cilia_number=9001,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
        )
        Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            cilia_number=9002,
            status=Budget.Status.PENDING,
        )
        response = self.client.get(reverse('budgets:budget_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{approved.display_number}')
        self.assertNotContains(response, '#9002')

    def test_budget_list_hides_delivered_budgets_by_default(self):
        self.client.login(email=self.manager.email, password=self.password)
        customer = Customer.objects.create(name='Cliente Lista Entrega', document_cpf_cnpj='998')
        vehicle = Vehicle.objects.create(customer=customer, plate='CCC1D22', brand='Marca', model='Modelo')
        active_budget = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            cilia_number=9101,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
        )
        delivered_budget = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            cilia_number=9102,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
            delivered_at=timezone.now(),
        )

        response = self.client.get(reverse('budgets:budget_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{active_budget.display_number}')
        self.assertNotContains(response, f'#{delivered_budget.display_number}')
        self.assertContains(response, 'Aprovados (1)')
        self.assertContains(response, 'Entregues (1)')

    def test_budget_list_can_filter_delivered_budgets(self):
        self.client.login(email=self.manager.email, password=self.password)
        customer = Customer.objects.create(name='Cliente Lista Filtro', document_cpf_cnpj='997')
        vehicle = Vehicle.objects.create(customer=customer, plate='DDD1E33', brand='Marca', model='Modelo')
        active_budget = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            cilia_number=9201,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
        )
        delivered_budget = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            cilia_number=9202,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
            delivered_at=timezone.now(),
        )

        response = self.client.get(reverse('budgets:budget_list') + '?delivery=delivered')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{delivered_budget.display_number}')
        self.assertNotContains(response, f'#{active_budget.display_number}')
        self.assertContains(response, 'Entregue')


class CiliaParserTests(TestCase):
    def test_replacement_piece_with_paint_hours_keeps_labor_line(self):
        xml = """
        <orcamento>
          <padrao_mao_de_obra>
            <valor_hora_mao_de_obra>120.0000</valor_hora_mao_de_obra>
            <valor_hora_reparacao>120.0000</valor_hora_reparacao>
            <valor_hora_pintura>120.0000</valor_hora_pintura>
          </padrao_mao_de_obra>
          <itens_orcamento>
            <item>
              <tipo_item>Peca</tipo_item>
              <codigo>ABC123</codigo>
              <nome>PORTA TRASEIRA ESQ</nome>
              <tipo_peca>Genuina</tipo_peca>
              <troca>true</troca>
              <remocao_instalacao>true</remocao_instalacao>
              <pintura>true</pintura>
              <reparacao>false</reparacao>
              <hora_remocao_instalacao>1.00</hora_remocao_instalacao>
              <hora_reparacao>0.00</hora_reparacao>
              <hora_pintura>7.00</hora_pintura>
              <preco>1090.0000</preco>
              <preco_liquido>1090.0000</preco_liquido>
            </item>
          </itens_orcamento>
        </orcamento>
        """
        lines = extract_service_lines(xml.encode('utf-8'))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['description'], 'PORTA TRASEIRA ESQ')
        self.assertEqual(lines[0]['pintura_hours'], Decimal('3.50'))
        self.assertEqual(lines[0]['preparacao_hours'], Decimal('3.50'))
        self.assertFalse(lines[0]['is_third_party'])


class CiliaImportServiceTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='import-manager@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )

    def _build_cilia_xml(self, number='123456', plate='ABC1D23', document='12345678901'):
        return f"""<?xml version="1.0" encoding="utf-8"?>
<orcamento>
  <numero_orcamento>{number}</numero_orcamento>
  <versao_orcamento>1</versao_orcamento>
  <data_orcamento>2026-07-24 10:30:00</data_orcamento>
  <cliente>
    <nome>Cliente Importado</nome>
    <cpf>{document}</cpf>
    <telefone>99999999</telefone>
    <email>cliente@importado.com</email>
  </cliente>
  <veiculo>
    <placa>{plate}</placa>
    <marca>Fiat</marca>
    <modelo>Argo</modelo>
    <cor>Branco</cor>
    <ano>2024</ano>
  </veiculo>
  <itens_orcamento>
    <item>
      <tipo_item>Peca</tipo_item>
      <tipo_peca>Genuina</tipo_peca>
      <nome>PORTA DIANTEIRA</nome>
      <troca>true</troca>
      <preco>500.00</preco>
      <quantidade>1</quantidade>
    </item>
  </itens_orcamento>
</orcamento>
""".encode('utf-8')

    def test_import_cilia_service_creates_budget_and_marks_job_imported(self):
        job = XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.MANUAL,
            file_name='orcamento-123456.xml',
        )

        result = import_cilia_xml_bytes(
            xml_bytes=self._build_cilia_xml(),
            job=job,
        )

        job.refresh_from_db()
        self.assertEqual(result.budget.cilia_number, 123456)
        self.assertEqual(result.budget.total_amount, Decimal('500.00'))
        self.assertEqual(result.budget.vehicle.plate, 'ABC1D23')
        self.assertEqual(result.budget.customer.document_cpf_cnpj, '12345678901')
        self.assertEqual(Piece.objects.filter(budget=result.budget).count(), 1)
        self.assertEqual(job.status, XMLImportJob.Status.IMPORTED)
        self.assertEqual(job.budget_id, result.budget.id)
        self.assertEqual(job.cilia_number, 123456)
        self.assertTrue(bool(job.file_hash))
        self.assertIsNotNone(job.processed_at)

    def test_import_cilia_service_marks_duplicate_job(self):
        customer = Customer.objects.create(name='Cliente Dup', document_cpf_cnpj='55555555555')
        vehicle = Vehicle.objects.create(customer=customer, plate='XYZ1A23', brand='Fiat', model='Mobi')
        Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            cilia_number=123456,
        )
        job = XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.MANUAL,
            file_name='orcamento-duplicado.xml',
        )

        with self.assertRaises(CiliaImportDuplicateError):
            import_cilia_xml_bytes(
                xml_bytes=self._build_cilia_xml(number='123456', plate='XYZ1A23', document='55555555555'),
                job=job,
            )

        job.refresh_from_db()
        self.assertEqual(job.status, XMLImportJob.Status.DUPLICATE)
        self.assertEqual(job.cilia_number, 123456)
        self.assertIn('já foi importado', job.error_message)

    def test_manual_import_view_records_manual_job(self):
        self.client.login(email=self.manager.email, password=self.password)

        response = self.client.post(
            reverse('budgets:import_xml'),
            {
                'xml_file': SimpleUploadedFile(
                    'orcamento-manual.xml',
                    self._build_cilia_xml(number='654321', plate='QWE1R23', document='98765432100'),
                    content_type='application/xml',
                )
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        job = XMLImportJob.objects.get(file_name='orcamento-manual.xml')
        self.assertEqual(job.provider, XMLImportJob.Provider.MANUAL)
        self.assertEqual(job.status, XMLImportJob.Status.IMPORTED)
        self.assertEqual(job.cilia_number, 654321)
        self.assertIsNotNone(job.budget)
        self.assertTrue(Budget.objects.filter(cilia_number=654321).exists())


@override_settings(
    DROPBOX_CILIA_ENABLED=True,
    DROPBOX_ACCESS_TOKEN='test-token',
    DROPBOX_CILIA_INPUT_PATH='/xml-cilia/entrada',
    DROPBOX_CILIA_PROCESSED_PATH='/xml-cilia/processados',
    DROPBOX_CILIA_ERROR_PATH='/xml-cilia/erro',
)
class DropboxServiceTests(TestCase):
    def test_list_input_xml_files_filters_only_xml(self):
        first_response = Mock()
        first_response.ok = True
        first_response.json.return_value = {
            'entries': [
                {
                    '.tag': 'file',
                    'id': 'id:xml-1',
                    'name': 'orcamento-1.xml',
                    'path_display': '/xml-cilia/entrada/orcamento-1.xml',
                    'path_lower': '/xml-cilia/entrada/orcamento-1.xml',
                    'size': 120,
                },
                {
                    '.tag': 'file',
                    'id': 'id:txt-1',
                    'name': 'anotacao.txt',
                    'path_display': '/xml-cilia/entrada/anotacao.txt',
                    'path_lower': '/xml-cilia/entrada/anotacao.txt',
                    'size': 40,
                },
            ],
            'cursor': 'cursor-1',
            'has_more': True,
        }
        second_response = Mock()
        second_response.ok = True
        second_response.json.return_value = {
            'entries': [
                {
                    '.tag': 'file',
                    'id': 'id:xml-2',
                    'name': 'orcamento-2.XML',
                    'path_display': '/xml-cilia/entrada/orcamento-2.XML',
                    'path_lower': '/xml-cilia/entrada/orcamento-2.xml',
                    'size': 220,
                }
            ],
            'cursor': 'cursor-2',
            'has_more': False,
        }
        session = Mock()
        session.post.side_effect = [first_response, second_response]

        service = DropboxService(session=session)
        files = service.list_input_xml_files()

        self.assertEqual([entry.name for entry in files], ['orcamento-1.xml', 'orcamento-2.XML'])
        self.assertEqual(files[0].id, 'id:xml-1')
        self.assertEqual(files[1].path_display, '/xml-cilia/entrada/orcamento-2.XML')


class FakeDropboxService:
    def __init__(self, entries, downloads):
        self.entries = entries
        self.downloads = downloads
        self.processed_path = '/xml-cilia/processados'
        self.error_path = '/xml-cilia/erro'
        self.moves = []

    def list_input_xml_files(self):
        return self.entries

    def download_file(self, path):
        return self.downloads[path]

    def move_file(self, from_path, to_path):
        self.moves.append((from_path, to_path))
        return {'metadata': {'path_display': to_path}}

    def build_destination_path(self, base_path, file_name):
        return f'{base_path}/{file_name}'


class DropboxSyncCommandTests(TestCase):
    def _build_cilia_xml(self, number='777001', plate='SYN1C23', document='12312312312'):
        return f"""<?xml version="1.0" encoding="utf-8"?>
<orcamento>
  <numero_orcamento>{number}</numero_orcamento>
  <versao_orcamento>1</versao_orcamento>
  <cliente>
    <nome>Cliente Sync</nome>
    <cpf>{document}</cpf>
  </cliente>
  <veiculo>
    <placa>{plate}</placa>
    <marca>VW</marca>
    <modelo>Polo</modelo>
  </veiculo>
  <itens_orcamento>
    <item>
      <tipo_item>Peca</tipo_item>
      <tipo_peca>Genuina</tipo_peca>
      <nome>PARA-CHOQUE</nome>
      <troca>true</troca>
      <preco>350.00</preco>
      <quantidade>1</quantidade>
    </item>
  </itens_orcamento>
</orcamento>
""".encode('utf-8')

    def test_sync_cilia_dropbox_imports_and_moves_to_processed(self):
        entry = DropboxEntry(
            id='id:dropbox-1',
            name='orcamento-sync.xml',
            path_display='/xml-cilia/entrada/orcamento-sync.xml',
            path_lower='/xml-cilia/entrada/orcamento-sync.xml',
            size=100,
        )
        fake_service = FakeDropboxService(
            [entry],
            {entry.path_display: self._build_cilia_xml(number='777001', plate='SYN1C23', document='12312312312')},
        )

        output = StringIO()
        with patch('budgets.management.commands.sync_cilia_dropbox.DropboxService', return_value=fake_service):
            call_command('sync_cilia_dropbox', stdout=output)

        job = XMLImportJob.objects.get(external_file_id='id:dropbox-1')
        self.assertEqual(job.provider, XMLImportJob.Provider.DROPBOX)
        self.assertEqual(job.status, XMLImportJob.Status.IMPORTED)
        self.assertEqual(job.cilia_number, 777001)
        self.assertTrue(Budget.objects.filter(cilia_number=777001).exists())
        self.assertEqual(
            fake_service.moves,
            [('/xml-cilia/entrada/orcamento-sync.xml', '/xml-cilia/processados/orcamento-sync.xml')],
        )

    def test_sync_cilia_dropbox_moves_duplicate_to_error(self):
        customer = Customer.objects.create(name='Cliente Duplicado', document_cpf_cnpj='99999999999')
        vehicle = Vehicle.objects.create(customer=customer, plate='DUP1C23', brand='VW', model='Polo')
        Budget.objects.create(customer=customer, vehicle=vehicle, cilia_number=888001)

        entry = DropboxEntry(
            id='id:dropbox-dup',
            name='orcamento-duplicado.xml',
            path_display='/xml-cilia/entrada/orcamento-duplicado.xml',
            path_lower='/xml-cilia/entrada/orcamento-duplicado.xml',
            size=100,
        )
        fake_service = FakeDropboxService(
            [entry],
            {entry.path_display: self._build_cilia_xml(number='888001', plate='DUP1C23', document='99999999999')},
        )

        output = StringIO()
        with patch('budgets.management.commands.sync_cilia_dropbox.DropboxService', return_value=fake_service):
            call_command('sync_cilia_dropbox', stdout=output)

        job = XMLImportJob.objects.get(external_file_id='id:dropbox-dup')
        self.assertEqual(job.status, XMLImportJob.Status.DUPLICATE)
        self.assertEqual(job.cilia_number, 888001)
        self.assertIn('já foi importado', job.error_message)
        self.assertEqual(
            fake_service.moves,
            [('/xml-cilia/entrada/orcamento-duplicado.xml', '/xml-cilia/erro/orcamento-duplicado.xml')],
        )


class XMLImportJobViewsTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='manager-import-job@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.estimator = CustomUser.objects.create_user(
            email='estimator-import-job@test.com',
            password=self.password,
            role=CustomUser.Role.ESTIMATOR,
        )
        self.operational = CustomUser.objects.create_user(
            email='operational-import-job@test.com',
            password=self.password,
            role=CustomUser.Role.OPERATIONAL,
        )
        self.customer = Customer.objects.create(name='Cliente Import Job', document_cpf_cnpj='45454545454')
        self.vehicle = Vehicle.objects.create(customer=self.customer, plate='IMP1J23', brand='Ford', model='Ka')
        self.budget = Budget.objects.create(customer=self.customer, vehicle=self.vehicle, cilia_number=990001)

    def _build_cilia_xml(self, number='990123', plate='IMP9J23', document='12121212121'):
        return f"""<?xml version="1.0" encoding="utf-8"?>
<orcamento>
  <numero_orcamento>{number}</numero_orcamento>
  <versao_orcamento>1</versao_orcamento>
  <cliente>
    <nome>Cliente Reprocessado</nome>
    <cpf>{document}</cpf>
    <telefone>88888888</telefone>
    <email>cliente@reprocessado.com</email>
  </cliente>
  <veiculo>
    <placa>{plate}</placa>
    <marca>Chevrolet</marca>
    <modelo>Onix</modelo>
  </veiculo>
  <itens_orcamento>
    <item>
      <tipo_item>Peca</tipo_item>
      <tipo_peca>Genuina</tipo_peca>
      <nome>CAPO</nome>
      <troca>true</troca>
      <preco>650.00</preco>
      <quantidade>1</quantidade>
    </item>
  </itens_orcamento>
</orcamento>
""".encode('utf-8')

    def test_import_job_list_filters_by_status(self):
        self.client.login(email=self.manager.email, password=self.password)
        XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.DROPBOX,
            file_name='erro.xml',
            status=XMLImportJob.Status.ERROR,
            error_message='Falha no XML',
        )
        XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.MANUAL,
            file_name='sucesso.xml',
            status=XMLImportJob.Status.IMPORTED,
            budget=self.budget,
            cilia_number=self.budget.cilia_number,
        )

        response = self.client.get(reverse('budgets:import_job_list') + '?status=ERROR')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'erro.xml')
        self.assertNotContains(response, 'sucesso.xml')
        self.assertContains(response, 'Com erro')

    def test_import_job_detail_shows_reprocess_button(self):
        self.client.login(email=self.estimator.email, password=self.password)
        job = XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.DROPBOX,
            file_name='duplicado.xml',
            status=XMLImportJob.Status.DUPLICATE,
            error_message='Este XML já foi importado.',
            raw_xml=self._build_cilia_xml(number='990222', plate='IMP2J23', document='45454545454').decode('utf-8'),
        )

        response = self.client.get(reverse('budgets:import_job_detail', kwargs={'pk': job.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reprocessar importação')
        self.assertContains(response, 'Excluir duplicado')
        self.assertContains(response, 'Este XML já foi importado.')
        self.assertContains(response, 'duplicado.xml')

    def test_import_job_reprocess_imports_job_successfully(self):
        self.client.login(email=self.manager.email, password=self.password)
        job = XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.DROPBOX,
            file_name='reprocessar.xml',
            status=XMLImportJob.Status.ERROR,
            error_message='Falha anterior',
            raw_xml=self._build_cilia_xml(number='990333', plate='IMP3J23', document='56565656565').decode('utf-8'),
        )

        response = self.client.post(
            reverse('budgets:import_job_reprocess', kwargs={'pk': job.pk}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, XMLImportJob.Status.IMPORTED)
        self.assertTrue(Budget.objects.filter(cilia_number=990333).exists())
        self.assertContains(response, 'Importação reprocessada com sucesso')

    def test_import_job_reprocess_without_raw_xml_keeps_job_unchanged(self):
        self.client.login(email=self.manager.email, password=self.password)
        job = XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.DROPBOX,
            file_name='sem-xml.xml',
            status=XMLImportJob.Status.ERROR,
            error_message='Sem XML salvo',
        )

        response = self.client.post(
            reverse('budgets:import_job_reprocess', kwargs={'pk': job.pk}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, XMLImportJob.Status.ERROR)
        self.assertContains(response, 'não possui o XML bruto salvo')

    def test_import_job_delete_duplicate_removes_only_duplicate_record(self):
        self.client.login(email=self.manager.email, password=self.password)
        job = XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.DROPBOX,
            file_name='duplicado-excluir.xml',
            status=XMLImportJob.Status.DUPLICATE,
            error_message='Este XML já foi importado.',
            raw_xml=self._build_cilia_xml(number='990444', plate='IMP4J23', document='67676767676').decode('utf-8'),
        )

        response = self.client.post(
            reverse('budgets:import_job_delete_duplicate', kwargs={'pk': job.pk}),
            {'next': reverse('budgets:import_job_list')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(XMLImportJob.objects.filter(pk=job.pk).exists())
        self.assertContains(response, 'Registro duplicado')

    def test_import_job_delete_duplicate_blocks_non_duplicate_job(self):
        self.client.login(email=self.manager.email, password=self.password)
        job = XMLImportJob.objects.create(
            provider=XMLImportJob.Provider.DROPBOX,
            file_name='importado.xml',
            status=XMLImportJob.Status.IMPORTED,
            budget=self.budget,
            cilia_number=self.budget.cilia_number,
        )

        response = self.client.post(
            reverse('budgets:import_job_delete_duplicate', kwargs={'pk': job.pk}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(XMLImportJob.objects.filter(pk=job.pk).exists())
        self.assertContains(response, 'Somente registros duplicados podem ser excluídos')

    def test_import_job_list_blocks_operational_user(self):
        self.client.login(email=self.operational.email, password=self.password)

        response = self.client.get(reverse('budgets:import_job_list'))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith(reverse('core:dashboard')))


class PendingPartsBlockTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.operational_user = CustomUser.objects.create_user(
            email='op-block@test.com',
            password=self.password,
            role=CustomUser.Role.OPERATIONAL,
        )
        self.manager_user = CustomUser.objects.create_user(
            email='manager-block@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.collaborator = Collaborator.objects.create(
            name='Operador',
            email=self.operational_user.email,
            function=Collaborator.Function.OPERATIONAL,
        )

    def test_can_start_task_even_when_shop_parts_are_pending(self):
        self.client.login(email=self.operational_user.email, password=self.password)
        customer = Customer.objects.create(name='Cliente', document_cpf_cnpj='123')
        vehicle = Vehicle.objects.create(customer=customer, plate='CCC1C11', brand='Marca', model='Modelo')
        budget = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            status=Budget.Status.AUTHORIZED,
            entry_date=timezone.localdate(),
            allow_repair_without_parts=False,
        )
        Piece.objects.create(
            budget=budget,
            name='PARA-CHOQUE',
            provider_type=Piece.ProviderType.SHOP,
            arrived=False,
        )
        work_order = WorkOrder.objects.create(budget=budget)
        task = WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            collaborator=self.collaborator,
            status=WorkOrderTask.Status.SCHEDULED,
            allow_overtime=True,
        )

        response = self.client.post(reverse('budgets:kanban_task_start', kwargs={'pk': task.pk}), follow=True)
        self.assertEqual(response.status_code, 200)

        task.refresh_from_db()
        self.assertEqual(task.status, WorkOrderTask.Status.RUNNING)

    def test_can_start_task_when_pending_piece_is_for_another_item(self):
        self.client.login(email=self.operational_user.email, password=self.password)
        customer = Customer.objects.create(name='Cliente Item', document_cpf_cnpj='124')
        vehicle = Vehicle.objects.create(customer=customer, plate='CCD1C12', brand='Marca', model='Modelo')
        budget = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            status=Budget.Status.AUTHORIZED,
            entry_date=timezone.localdate(),
            allow_repair_without_parts=False,
        )
        Piece.objects.create(
            budget=budget,
            name='ESPELHO RETROVISOR ESQ',
            provider_type=Piece.ProviderType.SHOP,
            arrived=False,
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='PORTA DIANTEIRA ESQ',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        task = WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.PREPARATION,
            description='PORTA DIANTEIRA ESQ',
            collaborator=self.collaborator,
            status=WorkOrderTask.Status.SCHEDULED,
            scheduled_date=timezone.localdate(),
            allow_overtime=True,
        )

        response = self.client.post(reverse('budgets:kanban_task_start', kwargs={'pk': task.pk}), follow=True)
        self.assertEqual(response.status_code, 200)

        task.refresh_from_db()
        self.assertEqual(task.status, WorkOrderTask.Status.RUNNING)

    def test_schedule_view_can_set_running_even_when_shop_parts_are_pending(self):
        self.client.login(email=self.manager_user.email, password=self.password)
        customer = Customer.objects.create(name='Cliente Item Agenda', document_cpf_cnpj='125')
        vehicle = Vehicle.objects.create(customer=customer, plate='CCE1C13', brand='Marca', model='Modelo')
        budget = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            status=Budget.Status.AUTHORIZED,
            entry_date=timezone.localdate(),
            allow_repair_without_parts=False,
        )
        Piece.objects.create(
            budget=budget,
            name='PARALAMA DIANT ESQ',
            provider_type=Piece.ProviderType.SHOP,
            arrived=False,
        )
        work_order = WorkOrder.objects.create(budget=budget)
        task = WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='PARALAMA DIANT ESQ',
            collaborator=self.collaborator,
            status=WorkOrderTask.Status.SCHEDULED,
            scheduled_date=timezone.localdate(),
            allow_overtime=True,
        )

        response = self.client.post(
            reverse('budgets:workorder_task_schedule', kwargs={'pk': task.pk}),
            {
                'collaborator_id': str(self.collaborator.id),
                'scheduled_date': timezone.localdate().isoformat(),
                'status': WorkOrderTask.Status.RUNNING,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, WorkOrderTask.Status.RUNNING)


class WorkOrderSequenceBlockTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.operational_user = CustomUser.objects.create_user(
            email='op-sequence@test.com',
            password=self.password,
            role=CustomUser.Role.OPERATIONAL,
        )
        self.manager = CustomUser.objects.create_user(
            email='manager-sequence@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.collaborator = Collaborator.objects.create(
            name='Operador Sequencia',
            email=self.operational_user.email,
            function=Collaborator.Function.OPERATIONAL,
        )
        self.customer = Customer.objects.create(name='Cliente Sequencia', document_cpf_cnpj='456')
        self.vehicle = Vehicle.objects.create(customer=self.customer, plate='SEQ1A23', brand='Marca', model='Modelo')
        self.budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            entry_date=timezone.localdate(),
        )
        self.work_order = WorkOrder.objects.create(budget=self.budget)
        self.dismantling = WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.DISMANTLING,
            description='Desmontagem lateral',
            collaborator=self.collaborator,
            status=WorkOrderTask.Status.SCHEDULED,
            scheduled_date=timezone.localdate(),
        )
        self.bodywork = WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='Funilaria lateral',
            collaborator=self.collaborator,
            status=WorkOrderTask.Status.SCHEDULED,
            scheduled_date=timezone.localdate(),
        )

    def test_cannot_start_task_when_predecessor_not_done(self):
        self.client.login(email=self.operational_user.email, password=self.password)
        response = self.client.post(reverse('budgets:kanban_task_start', kwargs={'pk': self.bodywork.pk}), follow=True)
        self.assertEqual(response.status_code, 200)
        self.bodywork.refresh_from_db()
        self.assertEqual(self.bodywork.status, WorkOrderTask.Status.SCHEDULED)
        messages = list(response.context['messages'])
        self.assertTrue(any('Conclua primeiro Desmontagem.' in str(message) for message in messages))

    def test_schedule_view_cannot_set_running_when_predecessor_not_done(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.post(
            reverse('budgets:workorder_task_schedule', kwargs={'pk': self.bodywork.pk}),
            {
                'collaborator_id': str(self.collaborator.id),
                'scheduled_date': timezone.localdate().isoformat(),
                'status': WorkOrderTask.Status.RUNNING,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.bodywork.refresh_from_db()
        self.assertEqual(self.bodywork.status, WorkOrderTask.Status.SCHEDULED)
        messages = list(response.context['messages'])
        self.assertTrue(any('Conclua primeiro Desmontagem.' in str(message) for message in messages))

    def test_kanban_shows_block_message_and_disables_start(self):
        self.client.login(email=self.operational_user.email, password=self.password)
        response = self.client.get(reverse('budgets:kanban_today'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Conclua primeiro Desmontagem.')
        self.assertContains(response, 'pointer-events-none', html=False)

    def test_can_start_preparation_when_same_item_predecessor_is_done(self):
        self.client.login(email=self.operational_user.email, password=self.password)
        other_bodywork = WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='Funilaria outra lateral',
            collaborator=self.collaborator,
            status=WorkOrderTask.Status.SCHEDULED,
            scheduled_date=timezone.localdate(),
        )
        preparation = WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.PREPARATION,
            description='Preparação lateral',
            collaborator=self.collaborator,
            status=WorkOrderTask.Status.SCHEDULED,
            scheduled_date=timezone.localdate(),
            allow_overtime=True,
        )
        self.dismantling.status = WorkOrderTask.Status.DONE
        self.dismantling.completed_at = timezone.now()
        self.dismantling.save(update_fields=['status', 'completed_at'])
        self.bodywork.description = 'Funilaria lateral'
        self.bodywork.status = WorkOrderTask.Status.DONE
        self.bodywork.completed_at = timezone.now()
        self.bodywork.save(update_fields=['description', 'status', 'completed_at'])
        other_bodywork.refresh_from_db()

        response = self.client.post(reverse('budgets:kanban_task_start', kwargs={'pk': preparation.pk}), follow=True)
        self.assertEqual(response.status_code, 200)
        preparation.refresh_from_db()
        self.assertEqual(preparation.status, WorkOrderTask.Status.RUNNING)

    def test_kanban_does_not_block_item_when_only_other_piece_is_pending(self):
        self.client.login(email=self.operational_user.email, password=self.password)
        self.dismantling.status = WorkOrderTask.Status.DONE
        self.dismantling.completed_at = timezone.now()
        self.dismantling.save(update_fields=['status', 'completed_at'])
        self.bodywork.description = 'LATERAL EXTERNA ESQ (Cód: 26355939)'
        self.bodywork.status = WorkOrderTask.Status.DONE
        self.bodywork.completed_at = timezone.now()
        self.bodywork.save(update_fields=['description', 'status', 'completed_at'])
        WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='PORTA TRASEIRA ESQ (Cód: 52184897)',
            collaborator=self.collaborator,
            status=WorkOrderTask.Status.SCHEDULED,
            scheduled_date=timezone.localdate(),
        )
        WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.PREPARATION,
            description='LATERAL EXTERNA ESQ (Cód: 26355939)',
            collaborator=self.collaborator,
            status=WorkOrderTask.Status.SCHEDULED,
            scheduled_date=timezone.localdate(),
        )

        response = self.client.get(reverse('budgets:kanban_today'))
        self.assertEqual(response.status_code, 200)
        prep_column = next(col for col in response.context['columns'] if col['key'] == WorkOrderTask.Activity.PREPARATION)
        lateral_task = next(task for task in prep_column['tasks'] if task.description == 'LATERAL EXTERNA ESQ (Cód: 26355939)')
        self.assertEqual(lateral_task.sequence_block_message, '')

    def test_kanban_patio_does_not_show_delivered_work_order(self):
        self.client.login(email=self.operational_user.email, password=self.password)
        self.dismantling.status = WorkOrderTask.Status.DONE
        self.dismantling.completed_at = timezone.now()
        self.dismantling.save(update_fields=['status', 'completed_at'])
        self.bodywork.status = WorkOrderTask.Status.DONE
        self.bodywork.completed_at = timezone.now()
        self.bodywork.save(update_fields=['status', 'completed_at'])
        self.budget.delivered_at = timezone.now()
        self.budget.delivered_by = self.operational_user
        self.budget.save(update_fields=['delivered_at', 'delivered_by'])

        response = self.client.get(reverse('budgets:kanban_today'))
        self.assertEqual(response.status_code, 200)
        patio_column = next(col for col in response.context['columns'] if col['key'] == 'PATIO')
        patio_ids = [task['work_order_id'] for task in patio_column['tasks']]
        self.assertNotIn(self.work_order.id, patio_ids)


class XMLCreatedAtTests(TestCase):
    def test_parse_xml_created_at_iso_date(self):
        xml = b"<orcamento><data_orcamento>2024-02-10</data_orcamento></orcamento>"
        dt = parse_xml_created_at(xml)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.date().isoformat(), "2024-02-10")


class FinanceMovementTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='finance-manager@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.category_in = CashCategory.objects.create(
            name='Recebimento avulso',
            direction=CashMovement.Direction.IN,
        )
        self.bank_account = BankAccount.objects.create(
            bank_name='Banco Teste',
            account_name='Conta Principal',
        )
        self.customer = Customer.objects.create(name='Cliente Financeiro', document_cpf_cnpj='111')
        self.supplier = Supplier.objects.create(
            name='Fornecedor Teste',
            kind=Supplier.Kind.BOTH,
        )
        self.category_out = CashCategory.objects.create(
            name='Aluguel',
            direction=CashMovement.Direction.OUT,
            group=CashCategory.ExpenseGroup.ADMIN,
        )

    def test_create_recurring_monthly_movements(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.post(
            reverse('budgets:finance_dashboard'),
            {
                'action': 'create_movement',
                'description': 'Aluguel loja',
                'amount': '1000.00',
                'launch_date': '2026-06-01',
                'due_date': '2026-06-10',
                'direction': 'OUT',
                'source': 'COMPANY',
                'bank_account_id': str(self.bank_account.id),
                'supplier_id': str(self.supplier.id),
                'category_id': str(self.category_out.id),
                'recurrence_total': '3',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CashMovement.objects.count(), 3)
        dates = list(CashMovement.objects.order_by('due_date').values_list('due_date', flat=True))
        self.assertEqual([d.isoformat() for d in dates], ['2026-06-10', '2026-07-10', '2026-08-10'])
        self.assertTrue(CashMovement.objects.filter(bank_account=self.bank_account, supplier=self.supplier).exists())
        self.assertTrue(CashMovement.objects.filter(launch_date=date(2026, 6, 1)).exists())

    def test_create_manual_entry_with_balance_forward(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.post(
            reverse('budgets:finance_dashboard'),
            {
                'action': 'create_movement',
                'description': 'Recebimento cliente',
                'amount': '900.00',
                'launch_date': '2026-06-03',
                'due_date': '2026-06-10',
                'direction': 'IN',
                'source': 'PARTICULAR',
                'customer_id': str(self.customer.id),
                'bank_account_id': str(self.bank_account.id),
                'category_id': str(self.category_in.id),
                'is_realized': 'on',
                'split_entry': 'on',
                'entry_amount': '300.00',
                'balance_due_date': '2026-07-05',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CashMovement.objects.count(), 2)
        first = CashMovement.objects.order_by('due_date', 'id').first()
        second = CashMovement.objects.order_by('due_date', 'id').last()
        self.assertEqual(first.amount, Decimal('300.00'))
        self.assertTrue(first.is_realized)
        self.assertEqual(second.amount, Decimal('600.00'))
        self.assertFalse(second.is_realized)
        self.assertEqual(first.customer, self.customer)
        self.assertEqual(first.launch_date.isoformat(), '2026-06-03')
        self.assertEqual(second.due_date.isoformat(), '2026-07-05')

    def test_manual_entry_requires_customer(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.post(
            reverse('budgets:finance_dashboard'),
            {
                'action': 'create_movement',
                'description': 'Recebimento sem cliente',
                'amount': '100.00',
                'launch_date': '2026-06-03',
                'due_date': '2026-06-10',
                'direction': 'IN',
                'source': 'PARTICULAR',
                'bank_account_id': str(self.bank_account.id),
                'category_id': str(self.category_in.id),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_out_movement_rejects_entry_only_source(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.post(
            reverse('budgets:finance_dashboard'),
            {
                'action': 'create_movement',
                'description': 'Saida invalida',
                'amount': '100.00',
                'launch_date': '2026-06-03',
                'due_date': '2026-06-10',
                'direction': 'OUT',
                'source': 'PARTICULAR',
                'bank_account_id': str(self.bank_account.id),
                'supplier_id': str(self.supplier.id),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_dashboard_filter_by_origin_category_respects_direction(self):
        self.client.login(email=self.manager.email, password=self.password)
        today = timezone.localdate()
        CashMovement.objects.create(
            description='Recebimento de peca',
            amount=Decimal('150.00'),
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTS_SALE,
            customer=self.customer,
            bank_account=self.bank_account,
            category=self.category_in,
            due_date=today,
        )
        CashMovement.objects.create(
            description='Despesa da empresa',
            amount=Decimal('90.00'),
            direction=CashMovement.Direction.OUT,
            source=CashMovement.Source.COMPANY,
            supplier=self.supplier,
            bank_account=self.bank_account,
            category=self.category_out,
            due_date=today,
        )
        response = self.client.get(reverse('budgets:finance_dashboard') + f'?direction=IN&source={self.category_in.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['movements']), 1)
        self.assertEqual(response.context['movements'][0].category_id, self.category_in.id)

    def test_finance_xml_import_creates_cash_movements(self):
        self.client.login(email=self.manager.email, password=self.password)
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<financeiro>
  <movimentos>
    <movimento>
      <descricao>Recebimento importado</descricao>
      <valor>450.00</valor>
      <direcao>IN</direcao>
      <origem>PARTICULAR</origem>
      <data_lancamento>2026-06-05</data_lancamento>
      <data_vencimento>2026-06-10</data_vencimento>
      <realizado>true</realizado>
      <conta_bancaria_id>{self.bank_account.id}</conta_bancaria_id>
      <categoria_id>{self.category_in.id}</categoria_id>
      <cliente_id>{self.customer.id}</cliente_id>
    </movimento>
    <movimento>
      <descricao>Despesa importada</descricao>
      <valor>120.50</valor>
      <direcao>OUT</direcao>
      <origem>COMPANY</origem>
      <data_lancamento>2026-06-06</data_lancamento>
      <data_vencimento>2026-06-12</data_vencimento>
      <realizado>false</realizado>
      <conta_bancaria_id>{self.bank_account.id}</conta_bancaria_id>
      <categoria_id>{self.category_out.id}</categoria_id>
      <fornecedor_id>{self.supplier.id}</fornecedor_id>
    </movimento>
  </movimentos>
</financeiro>
"""
        response = self.client.post(
            reverse('budgets:finance_import_xml'),
            {'xml_file': SimpleUploadedFile('financeiro.xml', xml.encode('utf-8'), content_type='application/xml')},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CashMovement.objects.count(), 2)
        self.assertTrue(CashMovement.objects.filter(description='Recebimento importado', customer=self.customer).exists())
        self.assertTrue(CashMovement.objects.filter(description='Despesa importada', supplier=self.supplier).exists())

    def test_finance_xml_import_is_atomic_on_invalid_row(self):
        self.client.login(email=self.manager.email, password=self.password)
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<financeiro>
  <movimentos>
    <movimento>
      <descricao>Recebimento ok</descricao>
      <valor>300.00</valor>
      <direcao>IN</direcao>
      <origem>PARTICULAR</origem>
      <data_lancamento>2026-06-05</data_lancamento>
      <data_vencimento>2026-06-10</data_vencimento>
      <conta_bancaria_id>{self.bank_account.id}</conta_bancaria_id>
      <categoria_id>{self.category_in.id}</categoria_id>
      <cliente_id>{self.customer.id}</cliente_id>
    </movimento>
    <movimento>
      <descricao>Sem conta</descricao>
      <valor>99.00</valor>
      <direcao>OUT</direcao>
      <origem>COMPANY</origem>
      <data_lancamento>2026-06-06</data_lancamento>
      <data_vencimento>2026-06-12</data_vencimento>
      <categoria_id>{self.category_out.id}</categoria_id>
    </movimento>
  </movimentos>
</financeiro>
"""
        response = self.client.post(
            reverse('budgets:finance_import_xml'),
            {'xml_file': SimpleUploadedFile('financeiro.xml', xml.encode('utf-8'), content_type='application/xml')},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'conta bancária não encontrada', html=False)
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_finance_xml_template_download(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.get(reverse('budgets:finance_export_xml_template'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml; charset=utf-8')
        self.assertIn('modelo-financeiro.xml', response['Content-Disposition'])
        self.assertIn('<financeiro>', response.content.decode('utf-8'))


class FinanceInsightsTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='dash-manager@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.category_in = CashCategory.objects.create(
            name='Recebimento dash',
            direction=CashMovement.Direction.IN,
        )
        self.customer = Customer.objects.create(name='Cliente Dash', document_cpf_cnpj='222')
        self.vehicle = Vehicle.objects.create(
            customer=self.customer,
            plate='ABC1234',
            model='Onix',
            brand='Chevrolet',
        )

    def test_finance_insights_default_month(self):
        self.client.login(email=self.manager.email, password=self.password)
        r = self.client.get(reverse('budgets:finance_insights'))
        self.assertEqual(r.status_code, 200)

    def test_finance_insights_range_12m(self):
        self.client.login(email=self.manager.email, password=self.password)
        r = self.client.get(reverse('budgets:finance_insights') + '?range=12m')
        self.assertEqual(r.status_code, 200)

    def test_finance_insights_filter_by_source(self):
        self.client.login(email=self.manager.email, password=self.password)
        CashMovement.objects.create(
            description='Venda de peca no dash',
            amount=Decimal('250.00'),
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTS_SALE,
            category=self.category_in,
            due_date=timezone.localdate(),
        )
        r = self.client.get(reverse('budgets:finance_insights') + '?range=month&direction=IN&source=PARTS_SALE')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['filters']['source'], 'PARTS_SALE')

    def test_finance_insights_insurer_ranking_from_xml(self):
        self.client.login(email=self.manager.email, password=self.password)
        Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
            total_amount=Decimal('1800.00'),
            source_xml='<orcamento><seguradora>Porto Seguro</seguradora></orcamento>',
        )
        r = self.client.get(reverse('budgets:finance_insights') + '?range=month')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Porto Seguro', r.context['insurer_labels'])


class ThirdPartyWorkOrderFlowTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='third-party-os@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.customer = Customer.objects.create(name='Cliente OS', document_cpf_cnpj='333')
        self.vehicle = Vehicle.objects.create(
            customer=self.customer,
            plate='XYZ1234',
            model='HB20',
            brand='Hyundai',
        )
        self.budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1000.00'),
        )
        self.work_order = WorkOrder.objects.create(budget=self.budget)
        self.supplier = Supplier.objects.create(
            name='Terceiro Externo',
            kind=Supplier.Kind.SERVICE,
        )

    def test_workorder_detail_shows_third_party_section(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.get(reverse('budgets:workorder_detail', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Serviços de Terceiros')
        self.assertContains(response, 'Terceiro Externo')

    def test_create_and_finish_third_party_service_from_workorder(self):
        self.client.login(email=self.manager.email, password=self.password)
        workorder_url = reverse('budgets:workorder_detail', kwargs={'pk': self.work_order.pk})
        response = self.client.post(
            reverse('budgets:third_party_create', kwargs={'pk': self.budget.pk}),
            {
                'description': 'Alinhamento terceirizado',
                'amount': '150.00',
                'supplier_id': str(self.supplier.id),
                'scheduled_date': '2026-06-15',
                'next': workorder_url,
            },
        )
        self.assertEqual(response.status_code, 302)
        service = ThirdPartyService.objects.get()
        self.assertEqual(service.status, ThirdPartyService.Status.SCHEDULED)
        self.assertEqual(service.supplier, self.supplier)
        self.assertEqual(service.scheduled_date.isoformat(), '2026-06-15')

        response = self.client.post(
            reverse('budgets:third_party_finish', kwargs={'pk': service.pk}),
            {'next': workorder_url},
        )
        self.assertEqual(response.status_code, 302)
        service.refresh_from_db()
        self.assertEqual(service.status, ThirdPartyService.Status.DONE)
        self.assertIsNotNone(service.completed_at)
        self.assertIsNotNone(service.expense_movement)
        self.assertEqual(service.expense_movement.direction, CashMovement.Direction.OUT)
        self.assertEqual(service.expense_movement.supplier, self.supplier)

    def test_workorder_detail_syncs_xml_third_party_services_once(self):
        self.client.login(email=self.manager.email, password=self.password)
        self.budget.source_xml = """
        <orcamento>
          <itens_orcamento>
            <item>
              <tipo_item>Servico</tipo_item>
              <descricao>Lavagem externa</descricao>
              <preco>80.00</preco>
              <fornecimento>Terceiro</fornecimento>
            </item>
          </itens_orcamento>
        </orcamento>
        """
        self.budget.save(update_fields=['source_xml'])

        url = reverse('budgets:workorder_detail', kwargs={'pk': self.work_order.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ThirdPartyService.objects.filter(budget=self.budget).count(), 1)
        service = ThirdPartyService.objects.get(budget=self.budget)
        self.assertTrue(service.is_shop_service)
        self.assertTrue(
            WorkOrderTask.objects.filter(
                work_order=self.work_order,
                activity=WorkOrderTask.Activity.DELIVERY_PREP,
                description='Lavagem externa',
            ).exists()
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ThirdPartyService.objects.filter(budget=self.budget).count(), 1)

    def test_schedule_shop_service_task_persists_date_after_workorder_reload(self):
        self.client.login(email=self.manager.email, password=self.password)
        self.budget.source_xml = """
        <orcamento>
          <itens_orcamento>
            <item>
              <tipo_item>Servico</tipo_item>
              <descricao>Lavagem externa</descricao>
              <preco>80.00</preco>
              <fornecimento>Terceiro</fornecimento>
            </item>
          </itens_orcamento>
        </orcamento>
        """
        self.budget.save(update_fields=['source_xml'])
        service = ThirdPartyService.objects.create(
            budget=self.budget,
            description='Lavagem externa',
            amount=Decimal('80.00'),
            status=ThirdPartyService.Status.SCHEDULED,
            is_shop_service=True,
        )
        task = WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.DELIVERY_PREP,
            description='Lavagem externa',
            status=WorkOrderTask.Status.SCHEDULED,
        )

        response = self.client.post(
            reverse('budgets:workorder_task_schedule', kwargs={'pk': task.pk}),
            {
                'collaborator_id': '',
                'service_id': '',
                'scheduled_date': '2026-06-20',
                'planned_amount': '',
                'actual_hours': '',
                'status': '',
            },
        )
        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        service.refresh_from_db()
        self.assertEqual(task.scheduled_date, date(2026, 6, 20))
        self.assertEqual(service.scheduled_date, date(2026, 6, 20))

        response = self.client.get(reverse('budgets:workorder_detail', kwargs={'pk': self.work_order.pk}))
        self.assertEqual(response.status_code, 200)

        task.refresh_from_db()
        service.refresh_from_db()
        self.assertEqual(task.scheduled_date, date(2026, 6, 20))
        self.assertEqual(service.scheduled_date, date(2026, 6, 20))

    def test_create_shop_service_from_workorder_creates_internal_task(self):
        self.client.login(email=self.manager.email, password=self.password)
        workorder_url = reverse('budgets:workorder_detail', kwargs={'pk': self.work_order.pk})
        response = self.client.post(
            reverse('budgets:third_party_create', kwargs={'pk': self.budget.pk}),
            {
                'description': 'Martelinho interno',
                'amount': '210.00',
                'supplier_id': '',
                'scheduled_date': '2026-06-16',
                'is_shop_service': 'on',
                'next': workorder_url,
            },
        )
        self.assertEqual(response.status_code, 302)
        service = ThirdPartyService.objects.get(description='Martelinho interno')
        self.assertTrue(service.is_shop_service)
        self.assertIsNone(service.expense_movement)
        self.assertTrue(
            WorkOrderTask.objects.filter(
                work_order=self.work_order,
                activity=WorkOrderTask.Activity.BODYWORK,
                description='Martelinho interno',
            ).exists()
        )

    def test_visible_third_party_services_hide_buggy_duplicates(self):
        self.budget.source_xml = """
        <orcamento>
          <itens_orcamento>
            <item>
              <tipo_item>Servico</tipo_item>
              <descricao>MARTELINHO DE OURO</descricao>
              <preco>390.00</preco>
              <fornecimento>Terceiro</fornecimento>
            </item>
          </itens_orcamento>
        </orcamento>
        """
        self.budget.save(update_fields=['source_xml'])
        ThirdPartyService.objects.create(
            budget=self.budget,
            description='MARTELINHO DE OURO',
            amount=Decimal('39000.00'),
            status=ThirdPartyService.Status.SCHEDULED,
        )
        ThirdPartyService.objects.create(
            budget=self.budget,
            description='MARTELINHO DE OURO',
            amount=Decimal('390.00'),
            status=ThirdPartyService.Status.SCHEDULED,
        )

        visible = get_visible_third_party_services(self.budget)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0].amount, Decimal('390.00'))
        self.assertFalse(visible[0].effective_is_shop_service)


class WorkOrderBulkAssignTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='workorder-bulk@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.customer = Customer.objects.create(name='Cliente Lote', document_cpf_cnpj='67676767676')
        self.vehicle = Vehicle.objects.create(
            customer=self.customer,
            plate='BUL1A23',
            model='Argo',
            brand='Fiat',
        )
        self.budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1800.00'),
        )
        self.work_order = WorkOrder.objects.create(budget=self.budget)
        self.other_work_order = WorkOrder.objects.create(
            budget=Budget.objects.create(
                customer=self.customer,
                vehicle=Vehicle.objects.create(
                    customer=self.customer,
                    plate='BUL9Z99',
                    model='Uno',
                    brand='Fiat',
                ),
                status=Budget.Status.AUTHORIZED,
                total_amount=Decimal('900.00'),
            )
        )
        self.collaborator_a = Collaborator.objects.create(
            name='Leonardo',
            email='leonardo-bulk@test.com',
            function=Collaborator.Function.OPERATIONAL,
        )
        self.collaborator_b = Collaborator.objects.create(
            name='Gustavo',
            email='gustavo-bulk@test.com',
            function=Collaborator.Function.OPERATIONAL,
        )
        self.task_one = WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='PORTA DIANTEIRA',
            status=WorkOrderTask.Status.SCHEDULED,
        )
        self.task_two = WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.PREPARATION,
            description='LATERAL ESQ',
            status=WorkOrderTask.Status.PAUSED,
        )
        self.task_done = WorkOrderTask.objects.create(
            work_order=self.work_order,
            activity=WorkOrderTask.Activity.PAINTING,
            description='PARALAMA',
            status=WorkOrderTask.Status.DONE,
        )
        self.other_task = WorkOrderTask.objects.create(
            work_order=self.other_work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='CAPO',
            status=WorkOrderTask.Status.SCHEDULED,
        )

    def test_workorder_detail_shows_bulk_assignment_controls(self):
        self.client.login(email=self.manager.email, password=self.password)

        response = self.client.get(reverse('budgets:workorder_detail', kwargs={'pk': self.work_order.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aplicar nos marcados')
        self.assertContains(response, 'Selecionar todas')

    def test_workorder_detail_disables_checkbox_for_task_with_collaborator(self):
        self.client.login(email=self.manager.email, password=self.password)
        self.task_two.collaborator = self.collaborator_a
        self.task_two.save(update_fields=['collaborator'])

        response = self.client.get(reverse('budgets:workorder_detail', kwargs={'pk': self.work_order.pk}))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(f"value='{self.task_two.id}'", content)
        self.assertIn("data-bulk-task-checkbox='1'", content)
        self.assertIn('disabled', content)

    def test_bulk_assign_updates_selected_open_tasks(self):
        self.client.login(email=self.manager.email, password=self.password)

        response = self.client.post(
            reverse('budgets:workorder_task_bulk_assign', kwargs={'pk': self.work_order.pk}),
            {
                'bulk_collaborator_id': str(self.collaborator_a.id),
                'task_ids': f'{self.task_one.id},{self.task_two.id}',
                'next': reverse('budgets:workorder_detail', kwargs={'pk': self.work_order.pk}),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.task_one.refresh_from_db()
        self.task_two.refresh_from_db()
        self.assertEqual(self.task_one.collaborator_id, self.collaborator_a.id)
        self.assertEqual(self.task_two.collaborator_id, self.collaborator_a.id)
        self.assertContains(response, '2 tarefa(s) atualizada(s)')

    def test_bulk_assign_ignores_done_tasks_and_other_work_orders(self):
        self.client.login(email=self.manager.email, password=self.password)

        response = self.client.post(
            reverse('budgets:workorder_task_bulk_assign', kwargs={'pk': self.work_order.pk}),
            {
                'bulk_collaborator_id': str(self.collaborator_b.id),
                'task_ids': f'{self.task_one.id},{self.task_done.id},{self.other_task.id}',
                'next': reverse('budgets:workorder_detail', kwargs={'pk': self.work_order.pk}),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.task_one.refresh_from_db()
        self.task_done.refresh_from_db()
        self.other_task.refresh_from_db()
        self.assertEqual(self.task_one.collaborator_id, self.collaborator_b.id)
        self.assertIsNone(self.task_done.collaborator_id)
        self.assertIsNone(self.other_task.collaborator_id)
        self.assertContains(response, '2 tarefa(s) foram ignoradas')

    def test_bulk_assign_requires_valid_operational_collaborator(self):
        self.client.login(email=self.manager.email, password=self.password)

        response = self.client.post(
            reverse('budgets:workorder_task_bulk_assign', kwargs={'pk': self.work_order.pk}),
            {
                'bulk_collaborator_id': '999999',
                'task_ids': str(self.task_one.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.task_one.refresh_from_db()
        self.assertIsNone(self.task_one.collaborator_id)
        self.assertContains(response, 'Selecione um colaborador operacional válido')


class ThirdPartyServiceFormTests(TestCase):
    def test_clean_amount_accepts_dot_decimal(self):
        form = ThirdPartyServiceForm(
            data={
                'description': 'Martelinho',
                'amount': '390.00',
                'status': ThirdPartyService.Status.SCHEDULED,
            }
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['amount'], Decimal('390.00'))


class BudgetFinancePromptTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='budget-finance@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.customer = Customer.objects.create(name='Cliente Finance Prompt', document_cpf_cnpj='444')
        self.vehicle = Vehicle.objects.create(
            customer=self.customer,
            plate='FIN1234',
            model='Gol',
            brand='VW',
        )

    def test_authorized_budget_without_finance_redirects_to_finance_modal(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.PENDING,
            total_amount=Decimal('900.00'),
        )
        response = self.client.post(
            reverse('budgets:budget_update', kwargs={'pk': budget.pk}),
            {
                'status': Budget.Status.AUTHORIZED,
                'entry_date': '2026-06-22',
                'repair_start_date': '',
                'expected_delivery_date': '',
                'refusal_reason_code': '',
                'refusal_reason': '',
                'allow_repair_without_parts': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers.get('Location', '').endswith(
                reverse('budgets:budget_update', kwargs={'pk': budget.pk}) + '?finance=1'
            )
        )
        budget.refresh_from_db()
        self.assertEqual(budget.status, Budget.Status.PENDING)
        self.assertIsNone(budget.approved_at)
        self.assertFalse(WorkOrder.objects.filter(budget=budget).exists())

    def test_finance_confirmation_completes_authorization(self):
        self.client.login(email=self.manager.email, password=self.password)
        bank_account = BankAccount.objects.create(
            bank_name='Banco Finance Prompt',
            account_name='Conta Finance Prompt',
        )
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.PENDING,
            total_amount=Decimal('900.00'),
        )
        response = self.client.post(
            reverse('budgets:budget_update', kwargs={'pk': budget.pk}),
            {
                'status': Budget.Status.AUTHORIZED,
                'entry_date': '2026-06-22',
                'repair_start_date': '',
                'expected_delivery_date': '2026-06-25',
                'refusal_reason_code': '',
                'refusal_reason': '',
                'allow_repair_without_parts': '',
            },
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse('budgets:budget_finance_create', kwargs={'pk': budget.pk}),
            {
                'kind': 'PARTICULAR',
                'bank_account_id': str(bank_account.id),
                'entry_amount': '100.00',
                'entry_due_date': '2026-06-22',
                'remainder_due_date': '2026-06-25',
            },
        )
        self.assertEqual(response.status_code, 302)
        budget.refresh_from_db()
        self.assertEqual(budget.status, Budget.Status.AUTHORIZED)
        self.assertEqual(budget.entry_date, date(2026, 6, 22))
        self.assertEqual(budget.expected_delivery_date, date(2026, 6, 25))
        self.assertIsNotNone(budget.approved_at)
        self.assertEqual(CashMovement.objects.filter(budget=budget).count(), 2)
        self.assertTrue(WorkOrder.objects.filter(budget=budget).exists())

    def test_already_authorized_budget_without_finance_still_redirects_to_finance_modal(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
            entry_date=date(2026, 6, 22),
            total_amount=Decimal('1200.00'),
        )
        response = self.client.post(
            reverse('budgets:budget_update', kwargs={'pk': budget.pk}),
            {
                'status': Budget.Status.AUTHORIZED,
                'entry_date': '2026-06-22',
                'repair_start_date': '',
                'expected_delivery_date': '',
                'refusal_reason_code': '',
                'refusal_reason': '',
                'allow_repair_without_parts': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers.get('Location', '').endswith(
                reverse('budgets:budget_update', kwargs={'pk': budget.pk}) + '?finance=1'
            )
        )


class BudgetDeleteTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='budget-delete@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.customer = Customer.objects.create(name='Cliente Delete', document_cpf_cnpj='555')
        self.vehicle = Vehicle.objects.create(
            customer=self.customer,
            plate='DEL1234',
            model='Uno',
            brand='Fiat',
        )

    def test_delete_budget_in_use_shows_message(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('700.00'),
        )
        category = CashCategory.objects.create(
            name='Recebimento delete budget',
            direction=CashMovement.Direction.IN,
        )
        bank_account = BankAccount.objects.create(
            bank_name='Banco Delete Budget',
            account_name='Conta Delete Budget',
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            bank_account=bank_account,
            category=category,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTICULAR,
            amount=Decimal('700.00'),
            due_date=date(2026, 6, 22),
        )

        response = self.client.post(
            reverse('budgets:budget_delete', kwargs={'pk': budget.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Este orçamento possui vínculos e não pode ser excluído.')
        self.assertTrue(Budget.objects.filter(pk=budget.pk).exists())


class BudgetDeliveryStatusTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Cliente Entrega', document_cpf_cnpj='666')
        self.vehicle = Vehicle.objects.create(
            customer=self.customer,
            plate='ENT1234',
            model='Onix',
            brand='Chevrolet',
        )
        self.bank_account = BankAccount.objects.create(
            bank_name='Banco Entrega',
            account_name='Conta Entrega',
        )

    def test_budget_delivery_status_blocks_pending_items(self):
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1000.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='Funilaria lateral',
            status=WorkOrderTask.Status.SCHEDULED,
        )
        ThirdPartyService.objects.create(
            budget=budget,
            description='Martelinho externo',
            amount=Decimal('150.00'),
            status=ThirdPartyService.Status.SCHEDULED,
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            bank_account=self.bank_account,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTICULAR,
            description='Orçamento #1 - Saldo',
            amount=Decimal('1000.00'),
            due_date=date(2026, 6, 30),
            is_realized=False,
        )

        status = budget_delivery_status(budget)
        self.assertFalse(status['can_deliver'])
        self.assertIn('Existem tarefas internas pendentes.', status['blockers'])
        self.assertIn('Existem serviços de terceiros pendentes.', status['blockers'])
        self.assertIn('Existem pendências financeiras em aberto.', status['blockers'])
        self.assertEqual(status['finance_open_amount'], Decimal('1000.00'))
        self.assertEqual(status['kind'], 'PARTICULAR')

    def test_budget_delivery_status_allows_delivery_when_ready(self):
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('2800.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='Funilaria lateral',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        ThirdPartyService.objects.create(
            budget=budget,
            description='Martelinho externo',
            amount=Decimal('80.00'),
            status=ThirdPartyService.Status.DONE,
            completed_at=timezone.now(),
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            bank_account=self.bank_account,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.INSURERS,
            description='Orçamento #1 - Seguradora',
            amount=Decimal('2800.00'),
            due_date=date(2026, 7, 10),
            is_realized=True,
        )

        status = budget_delivery_status(budget)
        self.assertTrue(status['can_deliver'])
        self.assertEqual(status['blockers'], [])
        self.assertEqual(status['task_done'], 1)
        self.assertEqual(status['third_done'], 1)
        self.assertEqual(status['kind'], 'SEGURADORA')

    def test_budget_delivery_status_allows_future_open_receivable_for_insurer(self):
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('2800.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.DELIVERY_PREP,
            description='Lavagem final',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            bank_account=self.bank_account,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.INSURERS,
            description='Orçamento #1 - Seguradora',
            amount=Decimal('2800.00'),
            due_date=timezone.localdate() + timedelta(days=7),
            is_realized=False,
        )

        status = budget_delivery_status(budget)
        self.assertTrue(status['can_deliver'])
        self.assertEqual(status['blockers'], [])
        self.assertEqual(status['finance_open_amount'], Decimal('2800.00'))
        self.assertTrue(status['allows_future_insurer_receivables'])
        self.assertIn('seguradora', status['finance_note'].lower())


class BudgetDetailCompletionUiTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='budget-detail-ui@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.finance = CustomUser.objects.create_user(
            email='budget-detail-ui-finance@test.com',
            password=self.password,
            role=CustomUser.Role.FINANCE,
        )
        self.customer = Customer.objects.create(name='Cliente UI Entrega', document_cpf_cnpj='777')
        self.vehicle = Vehicle.objects.create(
            customer=self.customer,
            plate='UIX1234',
            model='Argo',
            brand='Fiat',
        )

    def test_budget_detail_shows_completion_badges_for_service_lines(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('800.00'),
            source_xml="""
            <orcamento>
              <padrao_mao_de_obra>
                <valor_hora_mao_de_obra>120.0000</valor_hora_mao_de_obra>
                <valor_hora_reparacao>120.0000</valor_hora_reparacao>
                <valor_hora_pintura>120.0000</valor_hora_pintura>
              </padrao_mao_de_obra>
              <itens_orcamento>
                <item>
                  <tipo_item>Servico</tipo_item>
                  <descricao>FUNILARIA LATERAL</descricao>
                  <reparacao>true</reparacao>
                  <hora_reparacao>2.00</hora_reparacao>
                </item>
                <item>
                  <tipo_item>Servico</tipo_item>
                  <descricao>PINTURA CAPO</descricao>
                  <pintura>true</pintura>
                  <hora_pintura>4.00</hora_pintura>
                </item>
              </itens_orcamento>
            </orcamento>
            """,
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='FUNILARIA LATERAL',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.PREPARATION,
            description='PINTURA CAPO',
            status=WorkOrderTask.Status.SCHEDULED,
        )
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.PAINTING,
            description='PINTURA CAPO',
            status=WorkOrderTask.Status.SCHEDULED,
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTICULAR,
            amount=Decimal('800.00'),
            due_date=date(2026, 6, 30),
            is_realized=False,
        )

        response = self.client.get(reverse('budgets:budget_detail', kwargs={'pk': budget.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FUNILARIA LATERAL')
        self.assertContains(response, 'PINTURA CAPO')
        self.assertContains(response, 'Concluído')
        self.assertContains(response, 'Pendente')
        self.assertContains(response, 'Entrega bloqueada')
        self.assertContains(response, 'Entregar veículo')
        self.assertContains(response, 'disabled')
        movement = CashMovement.objects.get(budget=budget)
        self.assertContains(response, reverse('budgets:finance_dashboard') + f'?edit={movement.id}')

    def test_budget_detail_enables_delivery_button_when_ready(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('950.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.DELIVERY_PREP,
            description='Lavagem final',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTICULAR,
            amount=Decimal('950.00'),
            due_date=date(2026, 6, 30),
            is_realized=True,
        )

        response = self.client.get(reverse('budgets:budget_detail', kwargs={'pk': budget.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pronto para entrega')
        self.assertContains(response, 'Entregar veículo')
        self.assertContains(response, reverse('budgets:budget_deliver', kwargs={'pk': budget.pk}))
        self.assertContains(response, 'Confirmar entrega do veículo?')

    def test_budget_detail_enables_delivery_button_for_future_insurer_receivable(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1500.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.DELIVERY_PREP,
            description='Lavagem final',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.INSURERS,
            amount=Decimal('1500.00'),
            due_date=timezone.localdate() + timedelta(days=5),
            is_realized=False,
        )

        response = self.client.get(reverse('budgets:budget_detail', kwargs={'pk': budget.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pronto para entrega')
        self.assertContains(response, 'Entregar veículo')
        self.assertContains(response, 'Recebimento da seguradora previsto para depois da entrega.')

    def test_budget_detail_shows_administrative_closure_button_for_manager_when_os_not_started(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1500.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='PORTA ESQ',
            status=WorkOrderTask.Status.SCHEDULED,
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTICULAR,
            amount=Decimal('1500.00'),
            due_date=date(2026, 6, 30),
            is_realized=True,
        )

        response = self.client.get(reverse('budgets:budget_detail', kwargs={'pk': budget.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Finalização administrativa')
        self.assertContains(response, 'Use apenas para orçamentos ativos ainda não iniciados no operacional.')
        status = budget_administrative_closure_status(budget, self.manager)
        self.assertTrue(status['can_administratively_close'])

    def test_budget_detail_hides_administrative_closure_button_for_finance(self):
        self.client.login(email=self.finance.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1500.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='PORTA ESQ',
            status=WorkOrderTask.Status.SCHEDULED,
        )

        response = self.client.get(reverse('budgets:budget_detail', kwargs={'pk': budget.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Finalização administrativa')
        status = budget_administrative_closure_status(budget, self.finance)
        self.assertFalse(status['can_administratively_close'])

    def test_budget_detail_hides_administrative_closure_button_when_os_already_started(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1500.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='PORTA ESQ',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )

        response = self.client.get(reverse('budgets:budget_detail', kwargs={'pk': budget.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Finalização administrativa')
        status = budget_administrative_closure_status(budget, self.manager)
        self.assertFalse(status['can_administratively_close'])


class BudgetDeliverViewTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='budget-deliver@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.customer = Customer.objects.create(name='Cliente Entrega View', document_cpf_cnpj='888')
        self.vehicle = Vehicle.objects.create(
            customer=self.customer,
            plate='DLV1234',
            model='Cronos',
            brand='Fiat',
        )
        self.bank_account = BankAccount.objects.create(
            bank_name='Banco Entrega View',
            account_name='Conta Entrega View',
        )

    def test_budget_deliver_marks_budget_as_delivered_when_ready(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1200.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.DELIVERY_PREP,
            description='Lavagem final',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            bank_account=self.bank_account,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTICULAR,
            amount=Decimal('1200.00'),
            due_date=date(2026, 6, 30),
            is_realized=True,
        )

        response = self.client.post(reverse('budgets:budget_deliver', kwargs={'pk': budget.pk}), follow=True)
        self.assertEqual(response.status_code, 200)
        budget.refresh_from_db()
        self.assertIsNotNone(budget.delivered_at)
        self.assertEqual(budget.delivered_by, self.manager)
        self.assertContains(response, 'Veículo entregue com sucesso.')

    def test_budget_deliver_blocks_when_finance_is_still_open(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1200.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.DELIVERY_PREP,
            description='Lavagem final',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            bank_account=self.bank_account,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTICULAR,
            amount=Decimal('1200.00'),
            due_date=date(2026, 6, 30),
            is_realized=False,
        )

        response = self.client.post(reverse('budgets:budget_deliver', kwargs={'pk': budget.pk}), follow=True)
        self.assertEqual(response.status_code, 200)
        budget.refresh_from_db()
        self.assertIsNone(budget.delivered_at)
        self.assertContains(response, 'Não foi possível entregar o veículo.')
        self.assertContains(response, 'Existem pendências financeiras em aberto.')

    def test_budget_deliver_allows_future_open_receivable_for_insurer(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1200.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.DELIVERY_PREP,
            description='Lavagem final',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            bank_account=self.bank_account,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.INSURERS,
            amount=Decimal('1200.00'),
            due_date=timezone.localdate() + timedelta(days=10),
            is_realized=False,
        )

        response = self.client.post(reverse('budgets:budget_deliver', kwargs={'pk': budget.pk}), follow=True)
        self.assertEqual(response.status_code, 200)
        budget.refresh_from_db()
        self.assertIsNotNone(budget.delivered_at)
        self.assertEqual(budget.delivered_by, self.manager)
        self.assertContains(response, 'Veículo entregue com sucesso.')


class BudgetAdministrativeCloseViewTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='budget-admin-close@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.finance = CustomUser.objects.create_user(
            email='budget-admin-close-finance@test.com',
            password=self.password,
            role=CustomUser.Role.FINANCE,
        )
        self.customer = Customer.objects.create(name='Cliente Fechamento Adm', document_cpf_cnpj='999')
        self.vehicle = Vehicle.objects.create(
            customer=self.customer,
            plate='ADM1234',
            model='Pulse',
            brand='Fiat',
        )
        self.bank_account = BankAccount.objects.create(
            bank_name='Banco Adm',
            account_name='Conta Adm',
        )

    def test_budget_administrative_close_marks_budget_and_work_order(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1800.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='PORTA ESQ',
            status=WorkOrderTask.Status.SCHEDULED,
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            bank_account=self.bank_account,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTICULAR,
            amount=Decimal('1800.00'),
            due_date=date(2026, 6, 30),
            is_realized=True,
        )

        response = self.client.post(
            reverse('budgets:budget_administrative_close', kwargs={'pk': budget.pk}),
            data={
                'delivery_date': '2026-06-15',
                'reason': 'Veículo já entregue antes da implantação.',
                'confirm_no_commission': '1',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        budget.refresh_from_db()
        work_order.refresh_from_db()
        self.assertTrue(budget.administrative_closure)
        self.assertEqual(budget.administrative_closed_by, self.manager)
        self.assertEqual(budget.administrative_closure_reason, 'Veículo já entregue antes da implantação.')
        self.assertEqual(budget.delivered_by, self.manager)
        self.assertEqual(budget.delivered_at.date(), date(2026, 6, 15))
        self.assertEqual(work_order.status, WorkOrder.Status.CLOSED)
        self.assertEqual(CommissionLine.objects.count(), 0)
        self.assertContains(response, 'Finalização administrativa registrada com sucesso. Comissão não foi gerada.')

    def test_budget_administrative_close_blocks_when_finance_is_missing(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1800.00'),
        )
        WorkOrder.objects.create(budget=budget)

        response = self.client.post(
            reverse('budgets:budget_administrative_close', kwargs={'pk': budget.pk}),
            data={
                'delivery_date': '2026-06-15',
                'reason': 'Veículo já entregue antes da implantação.',
                'confirm_no_commission': '1',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        budget.refresh_from_db()
        self.assertFalse(budget.administrative_closure)
        self.assertIsNone(budget.delivered_at)
        self.assertContains(response, 'Não foi possível finalizar administrativamente.')
        self.assertContains(response, 'O financeiro do orçamento ainda não foi registrado.')

    def test_budget_administrative_close_blocks_when_work_order_has_started(self):
        self.client.login(email=self.manager.email, password=self.password)
        budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            status=Budget.Status.AUTHORIZED,
            total_amount=Decimal('1800.00'),
        )
        work_order = WorkOrder.objects.create(budget=budget)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='PORTA ESQ',
            status=WorkOrderTask.Status.DONE,
            completed_at=timezone.now(),
        )
        CashMovement.objects.create(
            budget=budget,
            customer=self.customer,
            bank_account=self.bank_account,
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTICULAR,
            amount=Decimal('1800.00'),
            due_date=date(2026, 6, 30),
            is_realized=True,
        )

        response = self.client.post(
            reverse('budgets:budget_administrative_close', kwargs={'pk': budget.pk}),
            data={
                'delivery_date': '2026-06-15',
                'reason': 'Veículo já entregue antes da implantação.',
                'confirm_no_commission': '1',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        budget.refresh_from_db()
        self.assertFalse(budget.administrative_closure)
        self.assertIsNone(budget.delivered_at)
        self.assertContains(response, 'Não foi possível finalizar administrativamente.')
        self.assertContains(response, 'A OS já foi iniciada no operacional.')


class BankAccountDeleteTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='bank-delete@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.bank_account = BankAccount.objects.create(
            bank_name='Banco Delete',
            account_name='Conta Teste',
        )
        self.category_out = CashCategory.objects.create(
            name='Despesa teste',
            direction=CashMovement.Direction.OUT,
            group=CashCategory.ExpenseGroup.ADMIN,
        )

    def test_delete_bank_account_missing_redirects_instead_of_404(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.post(reverse('budgets:bank_account_delete', kwargs={'pk': 9999}), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Conta bancária não encontrada.')

    def test_delete_bank_account_in_use_shows_message(self):
        self.client.login(email=self.manager.email, password=self.password)
        CashMovement.objects.create(
            description='Saida protegida',
            amount=Decimal('150.00'),
            direction=CashMovement.Direction.OUT,
            source=CashMovement.Source.COMPANY,
            bank_account=self.bank_account,
            category=self.category_out,
            due_date=date(2026, 6, 10),
        )
        response = self.client.post(
            reverse('budgets:bank_account_delete', kwargs={'pk': self.bank_account.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Esta conta bancária está vinculada a lançamentos e não pode ser excluída.')
        self.assertTrue(BankAccount.objects.filter(pk=self.bank_account.pk).exists())


class BankAccountFormTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='bank-form@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )

    def test_create_bank_account_prevents_duplicate(self):
        self.client.login(email=self.manager.email, password=self.password)
        BankAccount.objects.create(
            bank_name='Banco XPTO',
            account_name='Conta Principal',
            branch='1234',
            account_number='99999-0',
        )
        response = self.client.post(
            reverse('budgets:bank_account_create'),
            {
                'bank_name': 'banco xpto',
                'account_name': 'conta principal',
                'branch': '1234',
                'account_number': '99999-0',
                'account_type': BankAccount.AccountType.CHECKING,
                'pix_key': '',
                'initial_balance': '0',
                'initial_balance_date': '',
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Já existe uma conta bancária com esses mesmos dados.')
        self.assertEqual(BankAccount.objects.count(), 1)

    def test_bank_account_list_has_back_button(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.get(reverse('budgets:bank_account_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Voltar')

    def test_supplier_form_uses_standard_masks(self):
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.get(reverse('budgets:supplier_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-mask='doc'", html=False)
        self.assertContains(response, "data-mask='phone'", html=False)


class BudgetPhotoTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='photo-manager@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.customer = Customer.objects.create(name='Cliente Foto', document_cpf_cnpj='555')
        self.vehicle = Vehicle.objects.create(customer=self.customer, plate='FFF1F11', brand='Marca', model='Modelo')
        self.budget = Budget.objects.create(customer=self.customer, vehicle=self.vehicle, status=Budget.Status.PENDING)

    def test_budget_photo_upload(self):
        self.client.login(email=self.manager.email, password=self.password)
        upload = SimpleUploadedFile('orcamento.jpg', b'filecontent', content_type='image/jpeg')
        response = self.client.post(
            reverse('budgets:budget_photo_create', kwargs={'pk': self.budget.pk}),
            {'caption': 'Lateral', 'image_file': upload},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BudgetPhoto.objects.filter(budget=self.budget).count(), 1)


class CommissionConfidentialityTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.op_user = CustomUser.objects.create_user(
            email='op1@test.com',
            password=self.password,
            role=CustomUser.Role.OPERATIONAL,
        )
        self.manager = CustomUser.objects.create_user(
            email='manager2@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )

        self.op_collab = Collaborator.objects.create(
            name='Leonardo',
            email=self.op_user.email,
            function=Collaborator.Function.OPERATIONAL,
        )
        self.other_collab = Collaborator.objects.create(
            name='Mini mini',
            email='op2@test.com',
            function=Collaborator.Function.OPERATIONAL,
        )

        customer = Customer.objects.create(name='Cliente', document_cpf_cnpj='123')
        vehicle = Vehicle.objects.create(customer=customer, plate='AAA0A00', brand='X', model='Y')
        budget = Budget.objects.create(customer=customer, vehicle=vehicle, cilia_number=536)
        work_order = WorkOrder.objects.create(budget=budget)
        self.task1 = WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            collaborator=self.op_collab,
            planned_amount=Decimal('100.00'),
            elapsed_seconds=1200,
            status=WorkOrderTask.Status.DONE,
        )
        self.task2 = WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            collaborator=self.other_collab,
            planned_amount=Decimal('100.00'),
            elapsed_seconds=1200,
            status=WorkOrderTask.Status.DONE,
        )
        CommissionLine.objects.create(
            task=self.task1,
            collaborator=self.op_collab,
            percent=Decimal('10.00'),
            base_amount=Decimal('100.00'),
            commission_amount=Decimal('10.00'),
            is_paid=False,
        )
        CommissionLine.objects.create(
            task=self.task2,
            collaborator=self.other_collab,
            percent=Decimal('10.00'),
            base_amount=Decimal('100.00'),
            commission_amount=Decimal('10.00'),
            is_paid=False,
        )

    def test_operational_sees_only_own_commissions(self):
        self.client.login(email=self.op_user.email, password=self.password)
        r = self.client.get(reverse('budgets:commission_open_list'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.op_collab.name)
        self.assertNotContains(r, self.other_collab.name)

    def test_operational_cannot_force_other_collaborator_filter(self):
        self.client.login(email=self.op_user.email, password=self.password)
        r = self.client.get(reverse('budgets:commission_open_list') + f'?collaborator_id={self.other_collab.id}')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.op_collab.name)
        self.assertNotContains(r, self.other_collab.name)

    def test_manager_can_see_multiple_collaborators(self):
        self.client.login(email=self.manager.email, password=self.password)
        r = self.client.get(reverse('budgets:commission_open_list') + '?show_all=1')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.op_collab.name)
        self.assertContains(r, self.other_collab.name)


class TimeCappingTests(TestCase):
    def test_capped_delta_stops_at_cutoff(self):
        tz = timezone.get_current_timezone()
        started_day = date(2026, 6, 8)
        last = timezone.make_aware(datetime.combine(started_day, dt_time(16, 0)), tz)
        now = timezone.make_aware(datetime.combine(started_day, dt_time(19, 0)), tz)
        delta, effective_end = capped_work_delta_seconds(last, now, allow_overtime=False)
        self.assertEqual(delta, (1 * 3600) + (48 * 60))
        self.assertEqual(timezone.localtime(effective_end).time(), dt_time(17, 48))
