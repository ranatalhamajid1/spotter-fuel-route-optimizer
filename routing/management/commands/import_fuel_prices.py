"""
Management command to import fuel prices from CSV into the database.

Usage:
    python manage.py import_fuel_prices data/fuel-prices-for-be-assessment.csv
"""

import csv
import json
import os
import re
import sys
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from routing.models import FuelStation

CANADIAN_PROVINCES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"
}


class Command(BaseCommand):
    help = "Imports fuel price data from CSV into the database with offline city geolocation."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            type=str,
            help="Path to the fuel prices CSV file.",
        )
        parser.add_argument(
            "--coords-file",
            type=str,
            default=os.path.join("data", "us_cities.json"),
            help="Path to the offline US cities coordinates JSON reference file.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Batch size for database bulk operations.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing FuelStation records before import.",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_file"]
        coords_path = options["coords_file"]
        batch_size = options["batch_size"]
        clear_existing = options["clear"]

        if not os.path.exists(csv_path):
            raise CommandError(f"CSV file not found at: {csv_path}")

        city_coords = self._load_city_coordinates(coords_path)

        if clear_existing:
            self.stdout.write(self.style.WARNING("Clearing existing FuelStation records..."))
            FuelStation.objects.all().delete()

        start_time = time.time()
        self.stdout.write(self.style.NOTICE(f"Starting import from: {csv_path}"))

        total_rows = 0
        valid_rows = 0
        skipped_canadian = 0
        skipped_no_coords = 0
        skipped_invalid_price = 0
        skipped_invalid_id = 0

        # In-memory dictionary to aggregate and deduplicate by OPIS Truckstop ID.
        # If the same truckstop ID appears with multiple prices, we keep the lowest retail price.
        station_data_map: Dict[int, dict] = {}

        try:
            with open(csv_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                required_cols = {"OPIS Truckstop ID", "Truckstop Name", "City", "State", "Retail Price"}
                if not required_cols.issubset(set(reader.fieldnames or [])):
                    raise CommandError(
                        f"CSV is missing required columns. Required: {required_cols}. Found: {reader.fieldnames}"
                    )

                for row in reader:
                    total_rows += 1

                    # 1. Parse and validate OPIS ID
                    raw_id = row.get("OPIS Truckstop ID", "").strip()
                    try:
                        opis_id = int(raw_id)
                    except ValueError:
                        skipped_invalid_id += 1
                        continue

                    # 2. Parse and validate State & City
                    state = row.get("State", "").strip().upper()
                    city = row.get("City", "").strip()
                    if state in CANADIAN_PROVINCES:
                        skipped_canadian += 1
                        continue

                    if not city or not state:
                        skipped_no_coords += 1
                        continue

                    # 3. Parse and validate Retail Price
                    raw_price = row.get("Retail Price", "").strip()
                    try:
                        price = Decimal(raw_price)
                        if price <= 0:
                            skipped_invalid_price += 1
                            continue
                    except (InvalidOperation, ValueError):
                        skipped_invalid_price += 1
                        continue

                    # 4. Resolve Coordinates
                    coords = self._resolve_coordinates(city, state, city_coords)
                    if not coords:
                        skipped_no_coords += 1
                        continue

                    latitude, longitude = coords

                    # 5. Parse optional fields
                    name = row.get("Truckstop Name", "").strip() or f"Truck Stop #{opis_id}"
                    address = row.get("Address", "").strip()
                    raw_rack = row.get("Rack ID", "").strip()
                    rack_id = int(raw_rack) if raw_rack.isdigit() else None

                    # Deduplication strategy: if OPIS ID exists, keep the lowest retail price
                    if opis_id in station_data_map:
                        existing = station_data_map[opis_id]
                        if price < existing["retail_price"]:
                            existing["retail_price"] = price
                            existing["name"] = name
                            existing["address"] = address
                            existing["rack_id"] = rack_id
                    else:
                        station_data_map[opis_id] = {
                            "opis_id": opis_id,
                            "name": name,
                            "address": address,
                            "city": city,
                            "state": state,
                            "rack_id": rack_id,
                            "retail_price": price,
                            "latitude": latitude,
                            "longitude": longitude,
                        }
                    valid_rows += 1

        except Exception as e:
            raise CommandError(f"Error reading CSV file: {str(e)}") from e

        # Database batch upsert
        stations_to_create = []
        stations_to_update = []

        existing_stations = {s.opis_id: s for s in FuelStation.objects.all()}

        for opis_id, data in station_data_map.items():
            if opis_id in existing_stations:
                obj = existing_stations[opis_id]
                obj.name = data["name"]
                obj.address = data["address"]
                obj.city = data["city"]
                obj.state = data["state"]
                obj.rack_id = data["rack_id"]
                obj.retail_price = data["retail_price"]
                obj.latitude = data["latitude"]
                obj.longitude = data["longitude"]
                stations_to_update.append(obj)
            else:
                stations_to_create.append(FuelStation(**data))

        with transaction.atomic():
            if stations_to_create:
                FuelStation.objects.bulk_create(stations_to_create, batch_size=batch_size)
            if stations_to_update:
                FuelStation.objects.bulk_update(
                    stations_to_update,
                    fields=["name", "address", "city", "state", "rack_id", "retail_price", "latitude", "longitude"],
                    batch_size=batch_size,
                )

        elapsed = time.time() - start_time

        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("Fuel Price Import Summary:"))
        self.stdout.write(f"  Total CSV rows read:       {total_rows}")
        self.stdout.write(f"  Valid processed rows:      {valid_rows}")
        self.stdout.write(f"  Unique stations in DB:     {len(station_data_map)}")
        self.stdout.write(f"  Newly created stations:    {len(stations_to_create)}")
        self.stdout.write(f"  Updated existing stations: {len(stations_to_update)}")
        self.stdout.write(f"  Skipped (Canadian):        {skipped_canadian}")
        self.stdout.write(f"  Skipped (No coordinates):  {skipped_no_coords}")
        self.stdout.write(f"  Skipped (Invalid price):   {skipped_invalid_price}")
        self.stdout.write(f"  Skipped (Invalid ID):      {skipped_invalid_id}")
        self.stdout.write(f"  Execution time:            {elapsed:.2f} seconds")
        self.stdout.write(self.style.SUCCESS("=" * 60))

    def _load_city_coordinates(self, path: str) -> Dict[str, List[float]]:
        if not os.path.exists(path):
            self.stdout.write(
                self.style.WARNING(
                    f"Coordinates reference file not found at {path}. Station coordinates will be skipped."
                )
            )
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _resolve_coordinates(
        self, city: str, state: str, coords_map: Dict[str, List[float]]
    ) -> Optional[Tuple[float, float]]:
        city_lower = city.strip().lower()
        state_upper = state.strip().upper()
        state_lower = state.strip().lower()

        # Direct match with upper/lower state
        for s in (state_upper, state_lower):
            key = f"{city_lower},{s}"
            if key in coords_map:
                c = coords_map[key]
                return float(c[0]), float(c[1])

        # Normalized match (remove special characters/punctuation)
        clean_city = re.sub(r"[^a-z0-9]", "", city_lower)
        for s in (state_upper, state_lower):
            clean_key = f"{clean_city},{s}"
            if clean_key in coords_map:
                c = coords_map[clean_key]
                return float(c[0]), float(c[1])

        return None
