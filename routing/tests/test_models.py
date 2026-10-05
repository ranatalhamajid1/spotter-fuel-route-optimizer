from decimal import Decimal
from django.test import TestCase
from routing.models import FuelStation


class FuelStationModelTest(TestCase):
    """Unit tests for the FuelStation model."""

    def setUp(self):
        self.station = FuelStation.objects.create(
            opis_id=1001,
            name="Test Travel Center",
            address="100 Interstate Way",
            city="Columbus",
            state="OH",
            rack_id=50,
            retail_price=Decimal("3.299"),
            latitude=39.9612,
            longitude=-82.9988,
        )

    def test_fuel_station_creation(self):
        self.assertEqual(self.station.opis_id, 1001)
        self.assertEqual(self.station.name, "Test Travel Center")
        self.assertEqual(self.station.retail_price, Decimal("3.299"))
        self.assertEqual(self.station.latitude, 39.9612)
        self.assertEqual(self.station.longitude, -82.9988)

    def test_fuel_station_str(self):
        self.assertIn("Test Travel Center", str(self.station))
        self.assertIn("Columbus, OH", str(self.station))
        self.assertIn("$3.299", str(self.station))
