import math
from django.test import SimpleTestCase
from routing.services.geo_utils import (
    compute_cumulative_distances,
    haversine_distance,
    project_point_to_segment,
    project_station_to_route,
)


class GeoUtilsTest(SimpleTestCase):
    """Unit tests for geometric and projection functions."""

    def test_haversine_distance_known_points(self):
        # New York (40.7128, -74.0060) to Philadelphia (39.9526, -75.1652) is ~80.5 miles
        dist = haversine_distance(40.7128, -74.0060, 39.9526, -75.1652)
        self.assertAlmostEqual(dist, 80.5, delta=5.0)

    def test_haversine_same_point(self):
        dist = haversine_distance(34.0522, -118.2437, 34.0522, -118.2437)
        self.assertEqual(dist, 0.0)

    def test_compute_cumulative_distances(self):
        coords = [
            [-74.0060, 40.7128],
            [-75.1652, 39.9526],
            [-77.0369, 38.9072],
        ]
        cum_dist = compute_cumulative_distances(coords)
        self.assertEqual(len(cum_dist), 3)
        self.assertEqual(cum_dist[0], 0.0)
        self.assertGreater(cum_dist[1], 0.0)
        self.assertGreater(cum_dist[2], cum_dist[1])

    def test_project_point_to_segment_on_line(self):
        # Segment along latitude 40.0 from lon -80.0 to -78.0
        # Point right in middle at lat 40.0, lon -79.0
        dist, t = project_point_to_segment(
            p_lat=40.0, p_lon=-79.0,
            a_lat=40.0, a_lon=-80.0,
            b_lat=40.0, b_lon=-78.0,
        )
        self.assertAlmostEqual(t, 0.5, places=2)
        self.assertAlmostEqual(dist, 0.0, places=1)

    def test_project_point_to_segment_perpendicular_distance(self):
        # Point offset by ~0.1 deg lat (~6.9 miles)
        dist, t = project_point_to_segment(
            p_lat=40.1, p_lon=-79.0,
            a_lat=40.0, a_lon=-80.0,
            b_lat=40.0, b_lon=-78.0,
        )
        self.assertAlmostEqual(t, 0.5, places=2)
        self.assertAlmostEqual(dist, 6.9, delta=0.5)

    def test_project_station_to_route(self):
        route_coords = [
            [-80.0, 40.0],
            [-78.0, 40.0],
            [-76.0, 40.0],
        ]
        cum_dist = compute_cumulative_distances(route_coords)

        # Station halfway along first segment
        along_dist, perp_dist = project_station_to_route(
            station_lat=40.05,
            station_lon=-79.0,
            route_coords=route_coords,
            cum_distances=cum_dist,
        )
        self.assertGreater(along_dist, 0.0)
        self.assertLess(along_dist, cum_dist[-1])
        self.assertLess(perp_dist, 5.0)
