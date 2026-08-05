from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import Budget, WorkOrder, WorkOrderTask
from customers.models import Customer, Vehicle
from users.models import Collaborator, CustomUser


class DashboardCapacityViewTests(TestCase):
    def setUp(self):
        self.password = '111111'
        self.manager = CustomUser.objects.create_user(
            email='manager-dashboard@test.com',
            password=self.password,
            role=CustomUser.Role.MANAGER,
        )
        self.client.login(email=self.manager.email, password=self.password)

    def _create_budget_with_task(
        self,
        *,
        cilia_number,
        scheduled_date,
        planned_hours,
        budget_status=Budget.Status.AUTHORIZED,
        task_status=WorkOrderTask.Status.SCHEDULED,
        delivered=False,
        description='Tarefa',
    ):
        customer = Customer.objects.create(
            name=f'Cliente {cilia_number}',
            document_cpf_cnpj=str(cilia_number),
        )
        vehicle = Vehicle.objects.create(
            customer=customer,
            plate=f'ABC{cilia_number % 10}D{cilia_number % 10}{cilia_number % 10}',
            brand='Marca',
            model='Modelo',
        )
        budget = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            cilia_number=cilia_number,
            status=budget_status,
            approved_at=timezone.now() if budget_status == Budget.Status.AUTHORIZED else None,
            delivered_at=timezone.now() if delivered else None,
        )
        work_order = WorkOrder.objects.create(budget=budget, status=WorkOrder.Status.OPEN)
        return WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description=description,
            scheduled_date=scheduled_date,
            planned_hours=planned_hours,
            status=task_status,
        )

    def test_dashboard_shows_capacity_based_on_operational_collaborators(self):
        today = timezone.localdate()
        Collaborator.objects.create(name='Operacional 1', function=Collaborator.Function.OPERATIONAL)
        Collaborator.objects.create(name='Operacional 2', function=Collaborator.Function.OPERATIONAL)
        Collaborator.objects.create(name='Financeiro', function=Collaborator.Function.FINANCE)

        task = self._create_budget_with_task(
            cilia_number=7001,
            scheduled_date=today,
            planned_hours=Decimal('8.00'),
            description='Funilaria lateral',
        )
        self._create_budget_with_task(
            cilia_number=7002,
            scheduled_date=today,
            planned_hours=Decimal('3.00'),
            task_status=WorkOrderTask.Status.DONE,
            description='Nao deve contar',
        )
        self._create_budget_with_task(
            cilia_number=7003,
            scheduled_date=today,
            planned_hours=Decimal('2.00'),
            budget_status=Budget.Status.PENDING,
            description='Nao autorizado',
        )
        self._create_budget_with_task(
            cilia_number=7004,
            scheduled_date=today,
            planned_hours=Decimal('4.00'),
            delivered=True,
            description='Ja entregue',
        )
        self._create_budget_with_task(
            cilia_number=7005,
            scheduled_date=None,
            planned_hours=Decimal('5.00'),
            description='Tarefa sem agenda individual',
        )

        response = self.client.get(reverse('core:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '2')
        self.assertContains(response, '16,00h')
        self.assertContains(response, '8,00h')
        self.assertContains(response, '50% ocupado')
        self.assertContains(response, '1')
        self.assertContains(response, 'HH total aprovado')
        self.assertContains(response, 'HH médio por OS')
        self.assertContains(response, 'Funilaria lateral')
        self.assertContains(response, 'HH por OS Aprovada')
        self.assertContains(response, f'OS #{task.work_order.budget.display_number}')
        self.assertNotContains(response, 'Nao deve contar')
        self.assertNotContains(response, 'Nao autorizado')
        self.assertNotContains(response, 'Ja entregue')
        self.assertNotContains(response, 'Tarefa sem agenda individual')

    def test_dashboard_shows_total_hours_per_approved_work_order(self):
        today = timezone.localdate()
        Collaborator.objects.create(name='Operacional 1', function=Collaborator.Function.OPERATIONAL)

        customer_one = Customer.objects.create(name='Cliente 1', document_cpf_cnpj='9001')
        vehicle_one = Vehicle.objects.create(customer=customer_one, plate='AAA1A11', brand='Marca', model='Modelo')
        budget_one = Budget.objects.create(
            customer=customer_one,
            vehicle=vehicle_one,
            cilia_number=7201,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
        )
        work_order_one = WorkOrder.objects.create(budget=budget_one, status=WorkOrder.Status.OPEN)
        WorkOrderTask.objects.create(
            work_order=work_order_one,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='Tarefa 1',
            scheduled_date=today,
            planned_hours=Decimal('5.00'),
            status=WorkOrderTask.Status.SCHEDULED,
        )
        WorkOrderTask.objects.create(
            work_order=work_order_one,
            activity=WorkOrderTask.Activity.PAINTING,
            description='Tarefa 2',
            scheduled_date=today,
            planned_hours=Decimal('7.00'),
            status=WorkOrderTask.Status.SCHEDULED,
        )

        customer_two = Customer.objects.create(name='Cliente 2', document_cpf_cnpj='9002')
        vehicle_two = Vehicle.objects.create(customer=customer_two, plate='BBB2B22', brand='Marca', model='Modelo')
        budget_two = Budget.objects.create(
            customer=customer_two,
            vehicle=vehicle_two,
            cilia_number=7202,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
        )
        work_order_two = WorkOrder.objects.create(budget=budget_two, status=WorkOrder.Status.OPEN)
        WorkOrderTask.objects.create(
            work_order=work_order_two,
            activity=WorkOrderTask.Activity.ASSEMBLY,
            description='Tarefa 3',
            scheduled_date=today + timedelta(days=1),
            planned_hours=Decimal('4.00'),
            status=WorkOrderTask.Status.SCHEDULED,
        )

        response = self.client.get(reverse('core:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '2')
        self.assertContains(response, '16,00h')
        self.assertContains(response, '8,00h')
        self.assertContains(response, '12,00h')
        self.assertContains(response, '4,00h')
        self.assertContains(response, 'Cliente 1')
        self.assertContains(response, 'Cliente 2')
        self.assertContains(response, 'OS #7201')
        self.assertContains(response, 'OS #7202')

    def test_dashboard_can_show_selected_day_from_querystring(self):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        Collaborator.objects.create(name='Operacional 1', function=Collaborator.Function.OPERATIONAL)

        self._create_budget_with_task(
            cilia_number=7101,
            scheduled_date=today,
            planned_hours=Decimal('4.00'),
            description='Hoje',
        )
        tomorrow_task = self._create_budget_with_task(
            cilia_number=7102,
            scheduled_date=tomorrow,
            planned_hours=Decimal('6.00'),
            description='Amanha',
        )

        response = self.client.get(
            reverse('core:dashboard')
            + f'?month={tomorrow.month}&year={tomorrow.year}&selected_date={tomorrow.isoformat()}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Detalhe do dia {tomorrow.strftime("%d/%m/%Y")}')
        self.assertContains(response, 'Amanha')
        self.assertContains(response, f'OS #{tomorrow_task.work_order.budget.display_number}')
        self.assertNotContains(response, '>Hoje<')

    def test_dashboard_flags_high_load_and_missing_schedule_on_approved_work_orders(self):
        today = timezone.localdate()
        Collaborator.objects.create(name='Operacional 1', function=Collaborator.Function.OPERATIONAL)

        customer_one = Customer.objects.create(name='Cliente Carga', document_cpf_cnpj='9301')
        vehicle_one = Vehicle.objects.create(customer=customer_one, plate='CCC3C33', brand='Marca', model='Modelo')
        budget_one = Budget.objects.create(
            customer=customer_one,
            vehicle=vehicle_one,
            cilia_number=7301,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
        )
        work_order_one = WorkOrder.objects.create(budget=budget_one, status=WorkOrder.Status.OPEN)
        WorkOrderTask.objects.create(
            work_order=work_order_one,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='Carga fechada',
            scheduled_date=today,
            planned_hours=Decimal('8.00'),
            status=WorkOrderTask.Status.SCHEDULED,
        )

        customer_two = Customer.objects.create(name='Cliente Agenda', document_cpf_cnpj='9302')
        vehicle_two = Vehicle.objects.create(customer=customer_two, plate='DDD4D44', brand='Marca', model='Modelo')
        budget_two = Budget.objects.create(
            customer=customer_two,
            vehicle=vehicle_two,
            cilia_number=7302,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
        )
        work_order_two = WorkOrder.objects.create(budget=budget_two, status=WorkOrder.Status.OPEN)
        WorkOrderTask.objects.create(
            work_order=work_order_two,
            activity=WorkOrderTask.Activity.PREPARATION,
            description='Ja com data',
            scheduled_date=today,
            planned_hours=Decimal('3.00'),
            status=WorkOrderTask.Status.SCHEDULED,
        )
        WorkOrderTask.objects.create(
            work_order=work_order_two,
            activity=WorkOrderTask.Activity.PAINTING,
            description='Ainda sem data',
            scheduled_date=None,
            planned_hours=Decimal('2.00'),
            status=WorkOrderTask.Status.SCHEDULED,
        )

        response = self.client.get(reverse('core:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'OS sem agenda completa')
        self.assertContains(response, 'OS com carga alta')
        self.assertContains(response, 'Sem agenda completa')
        self.assertContains(response, 'Carga alta')
        self.assertContains(response, '3,00h')
        self.assertContains(response, '2,00h')
        self.assertContains(response, '8,00h')

    def test_dashboard_projects_approved_hours_from_today_until_consumed(self):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        Collaborator.objects.create(name='Operacional 1', function=Collaborator.Function.OPERATIONAL)

        customer = Customer.objects.create(name='Cliente Projecao', document_cpf_cnpj='9401')
        vehicle = Vehicle.objects.create(customer=customer, plate='EEE5E55', brand='Marca', model='Modelo')
        budget = Budget.objects.create(
            customer=customer,
            vehicle=vehicle,
            cilia_number=7401,
            status=Budget.Status.AUTHORIZED,
            approved_at=timezone.now(),
        )
        work_order = WorkOrder.objects.create(budget=budget, status=WorkOrder.Status.OPEN)
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.BODYWORK,
            description='Carga 1',
            scheduled_date=None,
            planned_hours=Decimal('7.00'),
            status=WorkOrderTask.Status.SCHEDULED,
        )
        WorkOrderTask.objects.create(
            work_order=work_order,
            activity=WorkOrderTask.Activity.PAINTING,
            description='Carga 2',
            scheduled_date=None,
            planned_hours=Decimal('5.00'),
            status=WorkOrderTask.Status.SCHEDULED,
        )

        response = self.client.get(reverse('core:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Projeção da Capacidade a Partir de Hoje')
        self.assertContains(response, f'Início: {today.strftime("%d/%m/%Y")}')
        self.assertContains(response, f'Fim previsto: {tomorrow.strftime("%d/%m/%Y")}')
        self.assertContains(response, 'Dias ocupados')
        self.assertContains(response, '2')
        self.assertContains(response, '12,00h')
        self.assertContains(response, '8,00h')
        self.assertContains(response, '4,00h')
