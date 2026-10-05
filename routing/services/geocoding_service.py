"""
Geocoding service to resolve US location strings to geographic coordinates.
"""

import logging
from typing import Optional, Tuple
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Bounding box for the United States (including AK and HI)
US_MIN_LAT = 18.0
US_MAX_LAT = 72.0
US_MIN_LON = -180.0
US_MAX_LON = -65.0


class GeocodingError(Exception):
    """Base exception for geocoding failures."""


class InvalidLocationError(GeocodingError):
    """Raised when location cannot be resolved or is not in the USA."""


class GeocodingService:
    """Service to geocode address/city/state strings to coordinates."""

    def __init__(self, timeout: int = 5):
        self.timeout = timeout
        self.user_agent = getattr(settings, "GEOCODING_USER_AGENT", "SpotterFuelRouteOptimizer/1.0")
        self._offline_cities = self._load_offline_cities()

    def _load_offline_cities(self) -> dict:
        import json, os
        path = os.path.join(settings.BASE_DIR, "data", "us_cities.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def geocode(self, query: str) -> Tuple[float, float, str]:
        """
        Geocode a US location query string.

        Returns:
            Tuple[latitude, longitude, display_name]

        Raises:
            InvalidLocationError: If location is not found or not in USA.
            GeocodingError: If external geocoding service fails.
        """
        cleaned_query = query.strip()
        if not cleaned_query:
            raise InvalidLocationError("Location query cannot be empty.")

        import hashlib, re
        query_hash = hashlib.sha256(cleaned_query.lower().encode("utf-8")).hexdigest()
        cache_key = f"geocode:v1:{query_hash}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        # Fast path: check offline city/state dataset
        offline_res = self._geocode_offline(cleaned_query)
        if offline_res:
            cache.set(cache_key, offline_res, timeout=86400 * 7)
            return offline_res

        # Attempt geocoding via Nominatim with US filter
        result = self._geocode_nominatim(cleaned_query)
        if not result:
            result = self._geocode_photon(cleaned_query)

        if not result:
            raise InvalidLocationError(f"Could not resolve '{query}' to a valid US location.")

        lat, lon, display_name = result
        self._validate_us_bounds(lat, lon, query)

        # Cache valid result for 7 days
        cache.set(cache_key, (lat, lon, display_name), timeout=86400 * 7)
        return lat, lon, display_name

    def _geocode_offline(self, query: str) -> Optional[Tuple[float, float, str]]:
        """Resolves standard 'City, State' formats instantly using bundled offline dataset."""
        if not self._offline_cities:
            return None

        import re
        parts = [p.strip() for p in query.split(",") if p.strip()]
        if len(parts) >= 2:
            city = parts[0].strip().lower()
            state = parts[1].strip()[:2].upper()
            clean_city = re.sub(r"[^a-z0-9]", "", city)

            for key in (f"{city},{state}", f"{clean_city},{state}"):
                if key in self._offline_cities:
                    coords = self._offline_cities[key]
                    return float(coords[0]), float(coords[1]), f"{parts[0]}, {state}, USA"
        return None

    def _geocode_photon(self, query: str) -> Optional[Tuple[float, float, str]]:
        """Query Photon geocoding API."""
        url = "https://photon.komoot.io/api/"
        params = {
            "q": query,
            "limit": 1,
        }
        headers = {"User-Agent": self.user_agent}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                return None
            data = resp.json()
            features = data.get("features", [])
            if not features:
                return None

            feature = features[0]
            props = feature.get("properties", {})
            country_code = props.get("countrycode", "").upper()
            if country_code and country_code != "US":
                # Check country property as well
                country = props.get("country", "").upper()
                if "UNITED STATES" not in country and "USA" not in country:
                    return None

            coords = feature.get("geometry", {}).get("coordinates", [])
            if len(coords) < 2:
                return None

            lon, lat = float(coords[0]), float(coords[1])
            name_parts = [props.get("name"), props.get("city"), props.get("state"), props.get("country")]
            display_name = ", ".join(p for p in name_parts if p) or query
            return lat, lon, display_name
        except Exception as e:
            logger.warning("Photon geocoding error for '%s': %s", query, e)
            return None

    def _geocode_nominatim(self, query: str) -> Optional[Tuple[float, float, str]]:
        """Query OSM Nominatim geocoding API with US country restriction."""
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": query,
            "format": "json",
            "countrycodes": "us",
            "limit": 1,
            "addressdetails": 1,
        }
        headers = {"User-Agent": self.user_agent}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data:
                return None

            item = data[0]
            lat = float(item["lat"])
            lon = float(item["lon"])
            display_name = item.get("display_name", query)
            return lat, lon, display_name
        except Exception as e:
            logger.warning("Nominatim geocoding error for '%s': %s", query, e)
            return None

    def _validate_us_bounds(self, lat: float, lon: float, query: str) -> None:
        """Ensure coordinate falls within broader US bounds."""
        if not (US_MIN_LAT <= lat <= US_MAX_LAT and US_MIN_LON <= lon <= US_MAX_LON):
            raise InvalidLocationError(
                f"Location '{query}' resolved outside the United States ({lat:.4f}, {lon:.4f})."
            )
