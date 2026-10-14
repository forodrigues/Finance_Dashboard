from decimal import ROUND_HALF_UP, Decimal

import django.core.validators
from django.db import migrations, models


def fill_amount(apps, schema_editor):
    FuelEntry = apps.get_model('finance', 'FuelEntry')
    # update() rather than save(): the historical model inherits the mixin's save(), which needs real methods.
    # Prefer the linked transaction's amount, which is what the monthly totals already show.
    rows = FuelEntry.objects.values_list('pk', 'liters', 'price_per_liter', 'transaction__amount')
    for pk, liters, price, tx_amount in rows:
        amount = tx_amount or (liters * price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        FuelEntry.objects.filter(pk=pk).update(amount=amount)


class Migration(migrations.Migration):
    dependencies = [('finance', '0003_linked_protect_recurring_period')]

    operations = [
        migrations.AddField(
            model_name='fuelentry',
            name='amount',
            field=models.DecimalField(decimal_places=2, max_digits=8, null=True, verbose_name='valor pago (€)'),
        ),
        migrations.RunPython(fill_amount, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='fuelentry',
            name='amount',
            field=models.DecimalField(
                decimal_places=2,
                max_digits=8,
                validators=[django.core.validators.MinValueValidator(Decimal('0.01'))],
                verbose_name='valor pago (€)',
            ),
        ),
        migrations.AlterField(
            model_name='fuelentry',
            name='price_per_liter',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=6,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal('0.01'))],
                verbose_name='preço por litro (€/L)',
            ),
        ),
        migrations.AlterField(
            model_name='fuelentry',
            name='liters',
            field=models.DecimalField(
                blank=True, decimal_places=2, editable=False, max_digits=6, null=True, verbose_name='litros'
            ),
        ),
        migrations.AlterField(
            model_name='fuelentry',
            name='full_tank',
            field=models.BooleanField(default=True, verbose_name='atestei o depósito'),
        ),
    ]
