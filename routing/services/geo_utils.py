"""
Geospatial calculation utilities for route projection and distance measurement.
Features high-performance spatial grid indexing for route corridor queries.
"""

import math
from collections import defaultdict
from typing import Dict, List, Set, Tuple

EARTH_RADIUS_MILES = 3958.8


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on Earth in miles.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_MILES * c


def compute_cumulative_distances(coordinates: List[List[float]]) -> List[float]:
    """
    Given a list of GeoJSON coordinates [[lon, lat], ...], computes cumulative
    distance along the route in miles.
    """
    if not coordinates:
        return []

    cumulative = [0.0]
    total = 0.0
    for i in range(1, len(coordinates)):
        lon1, lat1 = coordinates[i - 1]
        lon2, lat2 = coordinates[i]
        d = haversine_distance(lat1, lon1, lat2, lon2)
        total += d
        cumulative.append(total)
    return cumulative


def project_point_to_segment(
    p_lat: float,
    p_lon: float,
    a_lat: float,
    a_lon: float,
    b_lat: float,
    b_lon: float,
) -> Tuple[float, float]:
    """
    Projects point P onto line segment AB.

    Returns:
        (perpendicular_distance_miles, t)
        where t in [0.0, 1.0] indicates the projection factor along segment AB.
    """
    mid_lat_rad = math.radians((a_lat + b_lat + p_lat) / 3.0)
    cos_mid = math.cos(mid_lat_rad)

    miles_per_deg_lat = 69.0
    miles_per_deg_lon = 69.0 * cos_mid

    ax = 0.0
    ay = 0.0
    bx = (b_lon - a_lon) * miles_per_deg_lon
    by = (b_lat - a_lat) * miles_per_deg_lat
    px = (p_lon - a_lon) * miles_per_deg_lon
    py = (p_lat - a_lat) * miles_per_deg_lat

    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 1e-9:
        dist = math.sqrt(px * px + py * py)
        return dist, 0.0

    t = (px * dx + py * dy) / seg_len_sq
    t_clamped = max(0.0, min(1.0, t))

    nx = ax + t_clamped * dx
    ny = ay + t_clamped * dy

    dist = math.sqrt((px - nx) ** 2 + (py - ny) ** 2)
    return dist, t_clamped


class RouteSpatialIndex:
    """
    Spatial hash grid index for rapid corridor and along-route projections.
    Partitions route segments into lat/lon grid cells for sub-millisecond lookups.
    """

    def __init__(self, coordinates: List[List[float]], cell_size_deg: float = 0.25):
        self.coordinates = coordinates
        self.cell_size = cell_size_deg
        self.cum_distances = compute_cumulative_distances(coordinates)
        self.grid: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        self._build_index()

    def _build_index(self) -> None:
        n = len(self.coordinates) - 1
        for i in range(n):
            a_lon, a_lat = self.coordinates[i]
            b_lon, b_lat = self.coordinates[i + 1]

            min_lat, max_lat = min(a_lat, b_lat), max(a_lat, b_lat)
            min_lon, max_lon = min(a_lon, b_lon), max(a_lon, b_lon)

            c_lat_start = int(math.floor(min_lat / self.cell_size))
            c_lat_end = int(math.floor(max_lat / self.cell_size))
            c_lon_start = int(math.floor(min_lon / self.cell_size))
            c_lon_end = int(math.floor(max_lon / self.cell_size))

            for clat in range(c_lat_start, c_lat_end + 1):
                for clon in range(c_lon_start, c_lon_end + 1):
                    self.grid[(clat, clon)].append(i)

    def project_station(
        self, station_lat: float, station_lon: float, max_corridor_miles: float = 15.0
    ) -> Tuple[float, float]:
        """
        Finds the perpendicular distance and along-route distance for a station.

        Returns:
            (along_route_distance_miles, perpendicular_distance_miles)
        """
        # Search radius in grid cells (1 deg lat ~= 69 miles)
        cell_radius = max(1, int(math.ceil((max_corridor_miles / 60.0) / self.cell_size)))

        c_lat = int(math.floor(station_lat / self.cell_size))
        c_lon = int(math.floor(station_lon / self.cell_size))

        candidate_segments: Set[int] = set()
        for dlat in range(-cell_radius, cell_radius + 1):
            for dlon in range(-cell_radius, cell_radius + 1):
                cell = (c_lat + dlat, c_lon + dlon)
                if cell in self.grid:
                    candidate_segments.update(self.grid[cell])

        if not candidate_segments:
            return 0.0, float("inf")

        min_perp_dist = float("inf")
        best_along_dist = 0.0

        for i in candidate_segments:
            a_lon, a_lat = self.coordinates[i]
            b_lon, b_lat = self.coordinates[i + 1]

            perp_dist, t = project_point_to_segment(
                station_lat, station_lon, a_lat, a_lon, b_lat, b_lon
            )

            if perp_dist < min_perp_dist:
                min_perp_dist = perp_dist
                seg_start_dist = self.cum_distances[i]
                seg_len = self.cum_distances[i + 1] - seg_start_dist
                best_along_dist = seg_start_dist + (t * seg_len)

        return best_along_dist, min_perp_dist


def project_station_to_route(
    station_lat: float,
    station_lon: float,
    route_coords: List[List[float]],
    cum_distances: List[float],
) -> Tuple[float, float]:
    """
    Convenience helper that uses RouteSpatialIndex for fast station projection.
    """
    spatial_index = RouteSpatialIndex(route_coords, cell_size_deg=0.25)
    return spatial_index.project_station(station_lat, station_lon)
