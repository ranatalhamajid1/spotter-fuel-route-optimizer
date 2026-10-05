from decimal import Decimal
from unittest.mock import patch
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from routing.models import FuelStation
from routing.services.routing_service import RoutingServiceError, RoutingTimeoutError


class RoutePlanAPITest(APITestCase):
    """End-to-end integration tests for POST /api/route/ endpoint."""

    def setUp(self):
        self.url = reverse("routing:route-plan")

    def test_validation_missing_fields(self):
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", resp.data)

    def test_validation_empty_strings(self):
        resp = self.client.post(self.url, {"start": "   ", "finish": ""}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validation_same_start_and_finish(self):
        resp = self.client.post(
            self.url,
            {"start": "Chicago, IL", "finish": "chicago, il"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("finish", str(resp.data))

    @patch("routing.services.geocoding_service.GeocodingService.geocode")
    @patch("routing.services.routing_service.RoutingService.get_route")
    def test_short_route_success(self, mock_route, mock_geocode):
        # NYC to Philadelphia (~95 miles)
        mock_geocode.side_effect = [
            (40.7128, -74.0060, "New York, NY, USA"),
            (39.9526, -75.1652, "Philadelphia, PA, USA"),
        ]
        mock_route.return_value = {
            "distance_miles": 95.0,
            "duration_minutes": 110.0,
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [-74.0060, 40.7128],
                    [-75.1652, 39.9526],
                ],
            },
        }

        resp = self.client.post(
            self.url,
            {"start": "New York, NY", "finish": "Philadelphia, PA"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data

        self.assertEqual(data["route"]["distance_miles"], 95.0)
        self.assertEqual(len(data["fuel_plan"]["stops"]), 0)
        self.assertEqual(data["fuel_plan"]["total_fuel_consumed_gallons"], 9.5)
        self.assertEqual(data["fuel_plan"]["total_fuel_purchased_gallons"], 0.0)
        self.assertEqual(data["fuel_plan"]["total_fuel_cost"], 0.0)

    @patch("routing.services.geocoding_service.GeocodingService.geocode")
    @patch("routing.services.routing_service.RoutingService.get_route")
    def test_long_route_with_fuel_stops(self, mock_route, mock_geocode):
        mock_geocode.side_effect = [
            (40.0, -90.0, "Start City, IL"),
            (40.0, -70.0, "Finish City, PA"),
        ]
        mock_route.return_value = {
            "distance_miles": 1040.0,
            "duration_minutes": 1000.0,
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [-90.0, 40.0],
                    [-85.0, 40.0],
                    [-80.0, 40.0],
                    [-75.0, 40.0],
                    [-70.0, 40.0],
                ],
            },
        }

        # Create stations
        FuelStation.objects.create(
            opis_id=501,
            name="Midway Station 1",
            city="Midway 1",
            state="IN",
            retail_price=Decimal("3.15"),
            latitude=40.01,
            longitude=-84.0,
        )
        FuelStation.objects.create(
            opis_id=502,
            name="Midway Station 2",
            city="Midway 2",
            state="PA",
            retail_price=Decimal("3.25"),
            latitude=40.01,
            longitude=-76.0,
        )

        resp = self.client.post(
            self.url,
            {"start": "Start City, IL", "finish": "Finish City, PA"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data

        self.assertEqual(data["route"]["distance_miles"], 1040.0)
        stops = data["fuel_plan"]["stops"]
        self.assertGreaterEqual(len(stops), 1)

        # Check structure of each stop
        first_stop = stops[0]
        self.assertIn("station_id", first_stop)
        self.assertIn("name", first_stop)
        self.assertIn("price_per_gallon", first_stop)
        self.assertIn("distance_from_start_miles", first_stop)
        self.assertIn("fuel_purchased_gallons", first_stop)
        self.assertIn("cost", first_stop)

        # Check total costs match sum of stops
        total_cost = data["fuel_plan"]["total_fuel_cost"]
        expected_cost = sum(s["cost"] for s in stops)
        self.assertAlmostEqual(total_cost, expected_cost, places=2)

        # Fuel accounting identity: initial + purchased == consumed + ending
        fp = data["fuel_plan"]
        self.assertAlmostEqual(
            fp["starting_fuel_gallons"] + fp["total_fuel_purchased_gallons"],
            fp["total_fuel_consumed_gallons"] + fp["ending_fuel_gallons"],
            places=1,
        )
        for stop in stops:
            self.assertAlmostEqual(
                stop["fuel_on_arrival_gallons"] + stop["fuel_purchased_gallons"],
                stop["fuel_after_purchase_gallons"],
                places=1,
            )
            self.assertLessEqual(stop["fuel_after_purchase_gallons"], 50.0)
            self.assertLessEqual(stop["distance_from_previous_stop_miles"], 500.0)
        self.assertLessEqual(fp["distance_from_last_stop_to_destination_miles"], 500.0)

    @patch("routing.services.geocoding_service.GeocodingService.geocode")
    @patch("routing.services.routing_service.RoutingService.get_route")
    def test_routing_timeout_handling(self, mock_route, mock_geocode):
        mock_geocode.side_effect = [
            (40.7128, -74.0060, "New York, NY"),
            (34.0522, -118.2437, "Los Angeles, CA"),
        ]
        mock_route.side_effect = RoutingTimeoutError("Timed out")

        resp = self.client.post(
            self.url,
            {"start": "New York, NY", "finish": "Los Angeles, CA"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_504_GATEWAY_TIMEOUT)
        self.assertEqual(resp.data["error"], "Routing Gateway Timeout")

    @patch("routing.services.geocoding_service.GeocodingService.geocode")
    @patch("routing.services.routing_service.RoutingService.get_route")
    def test_routing_service_error_handling(self, mock_route, mock_geocode):
        mock_geocode.side_effect = [
            (40.7128, -74.0060, "New York, NY"),
            (34.0522, -118.2437, "Los Angeles, CA"),
        ]
        mock_route.side_effect = RoutingServiceError("Service unavailable")

        resp = self.client.post(
            self.url,
            {"start": "New York, NY", "finish": "Los Angeles, CA"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(resp.data["error"], "Bad Gateway")
