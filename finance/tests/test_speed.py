from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class ServiceWorkerTests(TestCase):
    def test_service_worker_names_cache_after_static_files(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response['Content-Type'], 'application/javascript')
        body = response.content.decode()
        self.assertIn("const CACHE = 'finance-static-/static/finance/app.css", body)
        self.assertNotIn('{%', body)

    def test_pages_include_progress_bar(self):
        self.client.force_login(User.objects.create_user('me', password='x'))
        self.assertContains(self.client.get(reverse('finance:dashboard')), 'id="nav-progress"')
