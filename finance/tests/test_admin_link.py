from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class AdminLinkTests(TestCase):
    def test_admin_link_only_for_staff(self):
        admin_url = reverse('admin:index')
        self.client.force_login(User.objects.create_user('normal', password='x'))
        self.assertNotContains(self.client.get(reverse('finance:dashboard')), admin_url)
        self.client.force_login(User.objects.create_superuser('boss', password='x'))
        response = self.client.get(reverse('finance:dashboard'))
        self.assertContains(response, admin_url, count=2)  # desktop nav + phone "Mais" menu
        self.assertContains(response, 'Administração')
