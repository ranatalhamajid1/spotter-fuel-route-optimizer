import json
import os
import tempfile
from decimal import Decimal
from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from routing.models import FuelStation


class ImportFuelPricesCommandTest(TestCase):
    """Tests for the import_fuel_prices management command."""

    def setUp(self):
        # Create temp city coords reference
        self.coords_file = tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False)
        json.dump(
            {
                "columbus,oh": [39.9612, -82.9988],
                "cleveland,oh": [41.4993, -81.6944],
                "indianapolis,in": [39.7684, -86.1581],
            },
            self.coords_file,
        )
        self.coords_file.close()

        # Create temp CSV file
        self.csv_file = tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False)
        self.csv_file.write(
            "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
            "101,Station A,100 Road,Columbus,OH,10,3.50\n"
            "101,Station A Cheaper,100 Road,Columbus,OH,10,3.20\n"  # Duplicate OPIS ID, cheaper
            "102,Station B,200 Way,Cleveland,OH,20,3.80\n"
            "103,Station Canada,10 Border,Toronto,ON,30,4.50\n"     # Canadian record, should be skipped
            "104,Station Invalid Price,300 Lane,Indianapolis,IN,40,INVALID\n" # Invalid price, skip
            "105,Station Unknown City,400 Ave,Atlantis,XX,50,3.00\n" # Unknown coords, skip
        )
        self.csv_file.flush()
        self.csv_file.close()

    def tearDown(self):
        if os.path.exists(self.coords_file.name):
            os.unlink(self.coords_file.name)
        if os.path.exists(self.csv_file.name):
            os.unlink(self.csv_file.name)

    def test_import_fuel_prices_execution(self):
        out = StringIO()
        call_command(
            "import_fuel_prices",
            self.csv_file.name,
            coords_file=self.coords_file.name,
            stdout=out,
        )
        output = out.getvalue()

        self.assertIn("Total CSV rows read:       6", output)
        self.assertIn("Unique stations in DB:     2", output)
        self.assertIn("Skipped (Canadian):        1", output)
        self.assertIn("Skipped (Invalid price):   1", output)

        # Check DB records
        stations = FuelStation.objects.all()
        self.assertEqual(stations.count(), 2)

        # Check deduplication picked cheaper price (3.20 vs 3.50)
        station_101 = FuelStation.objects.get(opis_id=101)
        self.assertEqual(station_101.retail_price, Decimal("3.20"))
        self.assertEqual(station_101.city, "Columbus")
        self.assertAlmostEqual(station_101.latitude, 39.9612)
        self.assertAlmostEqual(station_101.longitude, -82.9988)

        station_102 = FuelStation.objects.get(opis_id=102)
        self.assertEqual(station_102.retail_price, Decimal("3.80"))
