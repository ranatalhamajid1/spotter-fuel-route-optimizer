"""
Serializers for Route optimization request and response schemas.
"""

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Validates the route planning input request."""

    start = serializers.CharField(
        required=True,
        max_length=255,
        trim_whitespace=True,
        help_text="Start location in the USA (e.g. 'New York, NY')",
    )
    finish = serializers.CharField(
        required=True,
        max_length=255,
        trim_whitespace=True,
        help_text="Finish location in the USA (e.g. 'Los Angeles, CA')",
    )

    def validate_start(self, value: str) -> str:
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Start location cannot be blank.")
        if len(val) < 2:
            raise serializers.ValidationError("Start location query is too short.")
        return val

    def validate_finish(self, value: str) -> str:
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Finish location cannot be blank.")
        if len(val) < 2:
            raise serializers.ValidationError("Finish location query is too short.")
        return val

    def validate(self, attrs: dict) -> dict:
        start = attrs.get("start", "").strip()
        finish = attrs.get("finish", "").strip()

        if start.lower() == finish.lower():
            raise serializers.ValidationError(
                {"finish": "Finish location must be different from start location."}
            )
        return attrs


class FuelStopSerializer(serializers.Serializer):
    """Schema for individual recommended fuel stops."""

    stop_number = serializers.IntegerField()
    station_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField(allow_blank=True)
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    distance_from_start_miles = serializers.FloatField()
    distance_from_previous_stop_miles = serializers.FloatField()
    fuel_on_arrival_gallons = serializers.FloatField()
    fuel_purchased_gallons = serializers.FloatField()
    fuel_after_purchase_gallons = serializers.FloatField()
    cost = serializers.FloatField()


class FuelPlanSerializer(serializers.Serializer):
    """Schema for the complete fuel plan and cost breakdown."""

    stops = FuelStopSerializer(many=True)
    starting_fuel_gallons = serializers.FloatField()
    total_fuel_consumed_gallons = serializers.FloatField()
    total_fuel_purchased_gallons = serializers.FloatField()
    ending_fuel_gallons = serializers.FloatField()
    distance_from_last_stop_to_destination_miles = serializers.FloatField()
    total_fuel_cost = serializers.FloatField()
    assumptions_note = serializers.CharField()


class RouteInfoSerializer(serializers.Serializer):
    """Schema for route metrics and GeoJSON geometry."""

    distance_miles = serializers.FloatField()
    duration_minutes = serializers.FloatField()
    geometry = serializers.DictField()


class VehicleInfoSerializer(serializers.Serializer):
    """Vehicle assumptions specification."""

    max_range_miles = serializers.FloatField()
    mpg = serializers.FloatField()
    tank_capacity_gallons = serializers.FloatField()


class RoutePlanResponseSerializer(serializers.Serializer):
    """Top-level API response schema."""

    request = serializers.DictField()
    vehicle = VehicleInfoSerializer()
    route = RouteInfoSerializer()
    fuel_plan = FuelPlanSerializer()
