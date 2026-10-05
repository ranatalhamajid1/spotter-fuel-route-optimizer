"""
Driving route service utilizing OSRM (Open Source Routing Machine) or OpenRouteService.
"""

import logging
from typing import Any, Dict, List, Tuple
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

METERS_TO_MILES = 0.000621371192


class RoutingServiceError(Exception):
    """Base exception for routing failures."""


class RoutingTimeoutError(RoutingServiceError):
    """Raised when the external routing service times out."""


class NoRouteFoundError(RoutingServiceError):
    """Raised when no driving route can be found between points."""


class RoutingService:
    """Service to fetch driving routes, distances, durations, and GeoJSON geometry."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.osrm_url = getattr(settings, "OSRM_SERVER_URL", "https://router.project-osrm.org")
        self.ors_key = getattr(settings, "ROUTING_API_KEY", "")

    def get_route(
        self,
        start_lat: float,
        start_lon: float,
        finish_lat: float,
        finish_lon: float,
    ) -> Dict[str, Any]:
        """
        Calculates driving route between start and finish coordinates.

        Returns:
            {
                "distance_miles": float,
                "duration_minutes": float,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat], ...]
                }
            }
        """
        cache_key = (
            f"route:v1:{round(start_lat, 4)},{round(start_lon, 4)}:"
            f"{round(finish_lat, 4)},{round(finish_lon, 4)}"
        )
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        # Prefer OpenRouteService if API key is configured, otherwise use OSRM
        if self.ors_key:
            try:
                result = self._get_ors_route(start_lat, start_lon, finish_lat, finish_lon)
                cache.set(cache_key, result, timeout=86400 * 7)
                return result
            except Exception as e:
                logger.warning("ORS routing failed (%s), falling back to OSRM.", e)

        result = self._get_osrm_route(start_lat, start_lon, finish_lat, finish_lon)
        cache.set(cache_key, result, timeout=86400 * 7)
        return result

    def _get_osrm_route(
        self,
        start_lat: float,
        start_lon: float,
        finish_lat: float,
        finish_lon: float,
    ) -> Dict[str, Any]:
        """
        Query OSRM public routing API.
        Note: OSRM expects coordinates in {lon},{lat} order.
        """
        url = (
            f"{self.osrm_url.rstrip('/')}/route/v1/driving/"
            f"{start_lon},{start_lat};{finish_lon},{finish_lat}"
        )
        params = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }

        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
        except requests.Timeout as exc:
            raise RoutingTimeoutError("Routing provider timed out.") from exc
        except requests.RequestException as exc:
            raise RoutingServiceError(f"Network error contacting routing provider: {exc}") from exc

        if resp.status_code != 200:
            raise RoutingServiceError(
                f"Routing provider returned HTTP {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise NoRouteFoundError("No driving route found between specified locations.")

        route = data["routes"][0]
        distance_meters = float(route.get("distance", 0.0))
        duration_seconds = float(route.get("duration", 0.0))
        geometry = route.get("geometry", {})

        coordinates = geometry.get("coordinates", [])
        if not coordinates:
            raise NoRouteFoundError("Routing provider returned empty route geometry.")

        return {
            "distance_miles": round(distance_meters * METERS_TO_MILES, 2),
            "duration_minutes": round(duration_seconds / 60.0, 1),
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates,
            },
        }

    def _get_ors_route(
        self,
        start_lat: float,
        start_lon: float,
        finish_lat: float,
        finish_lon: float,
    ) -> Dict[str, Any]:
        """Query OpenRouteService directions API."""
        url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
        headers = {
            "Authorization": self.ors_key,
            "Content-Type": "application/json",
        }
        body = {
            "coordinates": [
                [start_lon, start_lat],
                [finish_lon, finish_lat],
            ]
        }

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=self.timeout)
        except requests.Timeout as exc:
            raise RoutingTimeoutError("OpenRouteService timed out.") from exc
        except requests.RequestException as exc:
            raise RoutingServiceError(f"Network error contacting OpenRouteService: {exc}") from exc

        if resp.status_code != 200:
            raise RoutingServiceError(
                f"OpenRouteService returned HTTP {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        features = data.get("features", [])
        if not features:
            raise NoRouteFoundError("No route returned by OpenRouteService.")

        feat = features[0]
        summary = feat.get("properties", {}).get("summary", {})
        distance_meters = float(summary.get("distance", 0.0))
        duration_seconds = float(summary.get("duration", 0.0))
        geometry = feat.get("geometry", {})

        return {
            "distance_miles": round(distance_meters * METERS_TO_MILES, 2),
            "duration_minutes": round(duration_seconds / 60.0, 1),
            "geometry": geometry,
        }
