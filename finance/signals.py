from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .defaults import create_default_categories
from .models import FuelEntry, TollEntry, Transaction


@receiver(post_delete, sender=FuelEntry)
@receiver(post_delete, sender=TollEntry)
def delete_linked_transaction(sender, instance, **kwargs):
    if instance.transaction_id:
        Transaction.objects.filter(pk=instance.transaction_id).delete()


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def give_new_user_default_categories(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        create_default_categories(instance)
