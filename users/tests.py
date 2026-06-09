from django.test import TestCase
from django.urls import reverse

from .models import Collaborator, CustomUser
from .views import sync_collaborator_login


class UserFlowsTests(TestCase):
    def test_visual_dashboard_redirects_to_kanban(self):
        user = CustomUser.objects.create_user(email='visual-flow@test.com', password='111111', role=CustomUser.Role.VISUAL)
        self.client.login(email=user.email, password='111111')
        r = self.client.get(reverse('core:dashboard'))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers.get('Location', '').endswith(reverse('budgets:kanban_today')))

    def test_sync_collaborator_login_creates_user(self):
        collaborator = Collaborator.objects.create(
            name='TV',
            email='tv@test.com',
            function=Collaborator.Function.VISUAL,
        )
        sync_collaborator_login(collaborator)
        self.assertTrue(CustomUser.objects.filter(email__iexact=collaborator.email).exists())
