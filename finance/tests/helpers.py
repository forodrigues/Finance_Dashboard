from django.contrib.auth.models import User
from django.test import TestCase


class OwnedTestCase(TestCase):
    """Every test gets `self.user`, the owner of the records it creates (with the default categories)."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('me', password='pw-for-tests')
