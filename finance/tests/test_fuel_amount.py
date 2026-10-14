from datetime import date
from decimal import Decimal as D

from finance.forms import FuelEntryForm
from finance.models import FuelEntry
from finance.services import stats
from finance.tests.helpers import OwnedTestCase


class FuelAmountPaidTests(OwnedTestCase):
    def fuel(self, day, amount, price=None, km=None, full=True):
        return FuelEntry.objects.create(
            user=self.user,
            date=day,
            amount=D(amount),
            price_per_liter=D(price) if price else None,
            odometer_km=km,
            full_tank=full,
        )

    def test_liters_derived_from_amount_and_price(self):
        entry = self.fuel(date(2026, 3, 1), '60', '1.689')
        self.assertEqual(entry.liters, D('35.52'))
        self.assertEqual(entry.transaction.amount, D('60'))
        self.assertEqual(entry.transaction.description, 'Abastecimento 35,52 L')

    def test_without_price_liters_unknown(self):
        entry = self.fuel(date(2026, 3, 1), '60')
        self.assertIsNone(entry.liters)
        self.assertEqual(entry.transaction.amount, D('60'))
        self.assertEqual(entry.transaction.description, 'Abastecimento')

    def test_removing_price_clears_liters(self):
        entry = self.fuel(date(2026, 3, 1), '60', '1.5')
        entry.price_per_liter = None
        entry.save()
        self.assertIsNone(entry.liters)

    def test_form_requires_amount_only(self):
        form = FuelEntryForm({'date': '2026-03-01', 'amount': '45,20', 'full_tank': 'on'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn('amount', FuelEntryForm({'date': '2026-03-01', 'full_tank': 'on'}).errors)

    def test_consumption_skipped_when_interval_has_unknown_liters(self):
        self.fuel(date(2026, 3, 1), '60', '1.5', km=10000)
        self.fuel(date(2026, 3, 10), '20', full=False)  # no price -> litres unknown
        last = self.fuel(date(2026, 3, 20), '45', '1.5', km=10500)
        after = self.fuel(date(2026, 3, 30), '45', '1.5', km=11000)
        result = stats.fuel_consumption(FuelEntry.objects.order_by('date', 'id'))
        self.assertNotIn(last.id, result)
        self.assertEqual(result[after.id], D('6.00'))  # 30 L / 500 km

    def test_car_stats_with_unpriced_fill(self):
        self.fuel(date(2026, 3, 1), '60', '1.5')  # 40 L
        self.fuel(date(2026, 3, 10), '30')  # unknown litres
        result = stats.car_stats(self.user, 2026, 3)
        self.assertEqual(result['fuel_cost'], D('90'))
        self.assertEqual(result['litres'], D('40.00'))
        self.assertEqual(result['avg_price'], D('1.500'))
        self.assertFalse(result['litres_complete'])


class FuelLimitsTests(OwnedTestCase):
    def test_price_too_low_for_amount_is_rejected(self):
        form = FuelEntryForm({'date': '2026-03-01', 'amount': '6000', 'price_per_liter': '0,5', 'full_tank': 'on'})
        self.assertIn('price_per_liter', form.errors)
        self.assertFalse(FuelEntry.objects.exists())

    def test_largest_storable_liters_is_accepted(self):
        form = FuelEntryForm({'date': '2026-03-01', 'amount': '9999,99', 'price_per_liter': '1', 'full_tank': 'on'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_compute_liters_without_amount_or_liters(self):
        entry = FuelEntry(date=date(2026, 3, 1), price_per_liter=D('1.5'))
        entry.compute_liters()  # must not raise
        self.assertIsNone(entry.liters)
