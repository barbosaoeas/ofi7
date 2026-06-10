from datetime import date, datetime, time as dt_time
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer, Vehicle
from users.models import Collaborator, CustomUser

from .models import Budget, CommissionLine, WorkOrder, WorkOrderTask
from .cilia_parser import extract_service_lines
from .views import capped_work_delta_seconds


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
            reverse('budgets:budget_list'),
            reverse('budgets:budget_open_list'),
            reverse('budgets:finance_dashboard'),
            reverse('budgets:workorder_list'),
            reverse('budgets:kanban_today'),
            reverse('budgets:vehicle_entry_kanban'),
            reverse('budgets:commission_open_list'),
            reverse('budgets:report_pieces'),
            reverse('customers:customer_list'),
            reverse('users:collaborator_list'),
            reverse('budgets:service_catalog_list'),
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
            'budgets:workorder_list',
            'customers:customer_list',
            'users:collaborator_list',
            'budgets:service_catalog_list',
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
            'budgets:workorder_list',
            'budgets:vehicle_entry_kanban',
            'budgets:report_pieces',
            'customers:customer_list',
            'users:collaborator_list',
            'budgets:service_catalog_list',
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
            'budgets:workorder_list',
            'budgets:vehicle_entry_kanban',
            'budgets:commission_open_list',
            'budgets:report_pieces',
            'customers:customer_list',
            'users:collaborator_list',
            'budgets:service_catalog_list',
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
