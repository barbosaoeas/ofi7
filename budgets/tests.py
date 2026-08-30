import hashlib
import hmac
import json
from datetime import date, datetime, time as dt_time
from decimal import Decimal

from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer, Vehicle
from users.models import Collaborator, CustomUser

from .models import (
    BankAccount,
    Budget,
    BudgetPhoto,
    CashCategory,
    CashMovement,
    CommissionLine,
    Piece,
    Supplier,
    WhatsAppFinanceQueueItem,
    WhatsAppWebhookLog,
    WorkOrder,
    WorkOrderTask,
)
from .cilia_parser import extract_service_lines
from .views import capped_work_delta_seconds, parse_xml_created_at


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
            reverse('budgets:finance_dashboard'),
            reverse('budgets:finance_whatsapp_queue'),
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
            'budgets:kanban_today',
            'budgets:vehicle_entry_kanban',
            'budgets:commission_open_list',
            'budgets:report_pieces',
        ]
        blocked = [
            'budgets:finance_dashboard',
            'budgets:finance_whatsapp_queue',
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
            'budgets:finance_dashboard',
            'budgets:finance_whatsapp_queue',
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
            'budgets:finance_dashboard',
            'budgets:finance_whatsapp_queue',
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


class PendingPartsBlockTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.operational_user = CustomUser.objects.create_user(
            email='op-block@test.com',
            password=self.password,
            role=CustomUser.Role.OPERATIONAL,
        )
        self.collaborator = Collaborator.objects.create(
            name='Operador',
            email=self.operational_user.email,
            function=Collaborator.Function.OPERATIONAL,
        )

    def test_cannot_start_task_when_shop_parts_pending(self):
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
        )

        response = self.client.post(reverse('budgets:kanban_task_start', kwargs={'pk': task.pk}), follow=True)
        self.assertEqual(response.status_code, 200)

        task.refresh_from_db()
        self.assertNotEqual(task.status, WorkOrderTask.Status.RUNNING)


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
        CashMovement.objects.create(
            description='Recebimento de peca',
            amount=Decimal('150.00'),
            direction=CashMovement.Direction.IN,
            source=CashMovement.Source.PARTS_SALE,
            customer=self.customer,
            bank_account=self.bank_account,
            category=self.category_in,
            due_date=date(2026, 6, 10),
        )
        CashMovement.objects.create(
            description='Despesa da empresa',
            amount=Decimal('90.00'),
            direction=CashMovement.Direction.OUT,
            source=CashMovement.Source.COMPANY,
            supplier=self.supplier,
            bank_account=self.bank_account,
            category=self.category_out,
            due_date=date(2026, 6, 10),
        )
        response = self.client.get(reverse('budgets:finance_dashboard') + f'?direction=IN&source={self.category_in.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['movements']), 1)
        self.assertEqual(response.context['movements'][0].category_id, self.category_in.id)


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


@override_settings(ZAP_WEBHOOK_SECRET='segredo-teste')
class WhatsAppIntegrationTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='manager-zap@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.finance_collab = Collaborator.objects.create(
            name='Financeiro Zap',
            email='manager-zap@test.com',
            phone='5511988887777',
            function=Collaborator.Function.FINANCE,
            is_active=True,
        )
        self.bank_account = BankAccount.objects.create(
            bank_name='Banco Zap',
            account_name='Conta Financeiro',
        )
        self.category_in = CashCategory.objects.create(
            name='Recebimento WhatsApp',
            direction=CashMovement.Direction.IN,
        )
        self.customer = Customer.objects.create(name='Cliente Zap', document_cpf_cnpj='777')
        self.vehicle = Vehicle.objects.create(customer=self.customer, plate='ZAP1234', brand='Marca', model='Modelo')
        self.budget = Budget.objects.create(
            customer=self.customer,
            vehicle=self.vehicle,
            cilia_number=435,
            status=Budget.Status.AUTHORIZED,
            customer_type=Budget.CustomerType.PARTICULAR,
        )

    def _signed_post(self, payload, signature_header='HTTP_X_HMAC_SHA256'):
        raw = json.dumps(payload).encode('utf-8')
        digest = hmac.new(b'segredo-teste', raw, hashlib.sha256).hexdigest()
        return self.client.post(
            reverse('zap_webhook'),
            data=raw,
            content_type='application/json',
            **{signature_header: digest},
        )

    def test_webhook_creates_pending_queue_item_for_authorized_phone(self):
        payload = {
            'event': 'message',
            'data': {
                'messageId': 'abc-1',
                'from': '5511988887777',
                'senderName': 'Financeiro',
                'chatId': '5511988887777@c.us',
                'body': '/pix 500 orcamento 435 cliente fulano',
            },
        }
        response = self._signed_post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WhatsAppFinanceQueueItem.objects.count(), 1)
        item = WhatsAppFinanceQueueItem.objects.first()
        self.assertEqual(item.status, WhatsAppFinanceQueueItem.Status.PENDING)
        self.assertEqual(item.budget, self.budget)
        self.assertEqual(item.customer, self.customer)
        self.assertEqual(item.amount, Decimal('500'))
        self.assertTrue(item.parsed_ok)
        self.assertEqual(WhatsAppWebhookLog.objects.count(), 1)
        self.assertTrue(WhatsAppWebhookLog.objects.first().processed_ok)

    def test_webhook_accepts_zap_signature_header(self):
        payload = {
            'event': 'message.received',
            'data': {
                'messageId': 'abc-zap-1',
                'from': '5511988887777',
                'senderName': 'Financeiro',
                'chatId': '120363000000000000@g.us',
                'body': '/pix 500 orcamento 435 cliente fulano',
            },
        }
        response = self._signed_post(payload, signature_header='HTTP_X_ZAP_SIGNATURE')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WhatsAppFinanceQueueItem.objects.count(), 1)
        self.assertTrue(WhatsAppWebhookLog.objects.first().processed_ok)

    def test_webhook_ignores_unauthorized_phone(self):
        payload = {
            'event': 'message',
            'data': {
                'messageId': 'abc-2',
                'from': '5511977776666',
                'senderName': 'Intruso',
                'body': '/pix 500 orcamento 435',
            },
        }
        response = self._signed_post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WhatsAppFinanceQueueItem.objects.count(), 0)
        self.assertEqual(WhatsAppWebhookLog.objects.count(), 1)
        self.assertIn('não autorizado', WhatsAppWebhookLog.objects.first().error_message.lower())

    def test_webhook_blocks_duplicate_message(self):
        payload = {
            'event': 'message',
            'data': {
                'messageId': 'abc-3',
                'from': '5511988887777',
                'senderName': 'Financeiro',
                'body': '/pix 500 orcamento 435 cliente fulano',
            },
        }
        first = self._signed_post(payload)
        second = self._signed_post(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(WhatsAppFinanceQueueItem.objects.count(), 1)
        self.assertEqual(WhatsAppWebhookLog.objects.count(), 2)

    def test_finance_can_confirm_queue_item_into_cash_movement(self):
        item = WhatsAppFinanceQueueItem.objects.create(
            duplicate_key='dup-confirm',
            sender_phone='5511988887777',
            sender_name='Financeiro',
            message_text='/pix 500 orcamento 435 cliente fulano',
            normalized_text='/pix 500 orcamento 435 cliente fulano',
            command_name='PIX',
            parsed_ok=True,
            direction=CashMovement.Direction.IN,
            amount=Decimal('500'),
            description='Recebimento do cliente',
            launch_date=timezone.localdate(),
            due_date=timezone.localdate(),
            collaborator=self.finance_collab,
            budget=self.budget,
            customer=self.customer,
            status=WhatsAppFinanceQueueItem.Status.PENDING,
        )
        self.client.login(email=self.manager.email, password=self.password)
        response = self.client.post(
            reverse('budgets:finance_whatsapp_queue'),
            {
                'action': 'confirm_item',
                'item_id': str(item.id),
                'description': 'Recebimento do cliente',
                'direction': 'IN',
                'amount': '500.00',
                'launch_date': timezone.localdate().isoformat(),
                'due_date': timezone.localdate().isoformat(),
                'bank_account_id': str(self.bank_account.id),
                'category_id': str(self.category_in.id),
                'budget_id': str(self.budget.id),
                'customer_id': str(self.customer.id),
                'supplier_id': '',
                'review_notes': 'Conferido pela gerência.',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, WhatsAppFinanceQueueItem.Status.CONFIRMED)
        self.assertIsNotNone(item.confirmed_movement_id)
        self.assertEqual(CashMovement.objects.count(), 1)


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
