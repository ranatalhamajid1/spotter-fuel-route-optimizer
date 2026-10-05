"""
API views and custom exception handling for fuel-optimized routing.
"""

import logging
from typing import Any, Dict

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.views import exception_handler as drf_exception_handler

from routing.serializers import (
    RoutePlanResponseSerializer,
    RouteRequestSerializer,
)
from routing.services.fuel_optimizer import (
    FuelOptimizationError,
    FuelOptimizer,
    NoFeasibleFuelPlanError,
)
from routing.services.geocoding_service import (
    GeocodingError,
    GeocodingService,
    InvalidLocationError,
)
from routing.services.routing_service import (
    NoRouteFoundError,
    RoutingService,
    RoutingServiceError,
    RoutingTimeoutError,
)

logger = logging.getLogger(__name__)


def custom_exception_handler(exc: Exception, context: Dict[str, Any]) -> Response:
    """
    Global DRF exception handler ensuring no internal tracebacks leak to clients.
    """
    response = drf_exception_handler(exc, context)
    if response is not None:
        # Standardize validation error format
        if response.status_code == status.HTTP_400_BAD_REQUEST:
            return Response(
                {
                    "error": "Validation Error",
                    "details": response.data,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return response

    # Map custom service exceptions to appropriate HTTP status codes
    if isinstance(exc, InvalidLocationError):
        return Response(
            {"error": "Invalid Location", "detail": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(exc, NoRouteFoundError):
        return Response(
            {"error": "No Route Found", "detail": str(exc)},
            status=status.HTTP_404_NOT_FOUND,
        )
    if isinstance(exc, NoFeasibleFuelPlanError):
        return Response(
            {"error": "Unfeasible Fuel Plan", "detail": str(exc)},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, RoutingTimeoutError):
        return Response(
            {"error": "Routing Gateway Timeout", "detail": "The external routing service timed out."},
            status=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    if isinstance(exc, (GeocodingError, RoutingServiceError)):
        return Response(
            {"error": "Bad Gateway", "detail": f"External service failure: {str(exc)}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    if isinstance(exc, FuelOptimizationError):
        return Response(
            {"error": "Optimization Error", "detail": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    logger.exception("Unhandled server error: %s", exc)
    return Response(
        {"error": "Internal Server Error", "detail": "An unexpected error occurred processing your request."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


class RoutePlanView(APIView):
    """
    POST /api/route/

    Calculates a driving route between two US locations and returns the optimal,
    cost-effective fuel stops based on vehicle constraints and local fuel prices.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.geocoding_service = GeocodingService()
        self.routing_service = RoutingService()
        self.fuel_optimizer = FuelOptimizer()

    def post(self, request, *args, **kwargs) -> Response:
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        start_query = serializer.validated_data["start"]
        finish_query = serializer.validated_data["finish"]

        # 1. Geocode locations
        start_lat, start_lon, start_name = self.geocoding_service.geocode(start_query)
        finish_lat, finish_lon, finish_name = self.geocoding_service.geocode(finish_query)

        # 2. Compute driving route
        route_data = self.routing_service.get_route(
            start_lat=start_lat,
            start_lon=start_lon,
            finish_lat=finish_lat,
            finish_lon=finish_lon,
        )

        total_distance = route_data["distance_miles"]
        route_geometry = route_data["geometry"]

        # 3. Optimize fuel stops
        fuel_plan = self.fuel_optimizer.optimize(
            route_geometry=route_geometry,
            total_distance_miles=total_distance,
        )

        # 4. Construct response payload
        response_payload = {
            "request": {
                "start": start_query,
                "finish": finish_query,
                "start_resolved": {
                    "name": start_name,
                    "latitude": round(start_lat, 6),
                    "longitude": round(start_lon, 6),
                },
                "finish_resolved": {
                    "name": finish_name,
                    "latitude": round(finish_lat, 6),
                    "longitude": round(finish_lon, 6),
                },
            },
            "vehicle": {
                "max_range_miles": self.fuel_optimizer.max_range_miles,
                "mpg": self.fuel_optimizer.mpg,
                "tank_capacity_gallons": self.fuel_optimizer.tank_capacity,
            },
            "route": {
                "distance_miles": total_distance,
                "duration_minutes": route_data["duration_minutes"],
                "geometry": route_geometry,
            },
            "fuel_plan": fuel_plan,
        }

        response_serializer = RoutePlanResponseSerializer(data=response_payload)
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


class DashboardView(APIView):
    """Renders the interactive visual map dashboard for browser demonstration."""

    def get(self, request, *args, **kwargs):
        from django.shortcuts import render
        return render(request, "routing/dashboard.html")

