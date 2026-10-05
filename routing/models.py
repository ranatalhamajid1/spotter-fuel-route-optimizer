from django.db import models


class FuelStation(models.Model):
    """
    Represents a fuel station / truck stop with pricing and geographic location.

    Coordinates represent city/state centroid approximation unless exact
    station coordinates are provided in the source dataset.
    """

    opis_id = models.IntegerField(
        unique=True,
        db_index=True,
        help_text="Unique OPIS Truckstop ID",
    )
    name = models.CharField(
        max_length=255,
        help_text="Truckstop Name",
    )
    address = models.CharField(
        max_length=255,
        blank=True,
        help_text="Physical address / highway exit",
    )
    city = models.CharField(
        max_length=100,
        db_index=True,
        help_text="City name",
    )
    state = models.CharField(
        max_length=10,
        db_index=True,
        help_text="Two-letter state / province code",
    )
    rack_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Rack identifier from source data",
    )
    retail_price = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        db_index=True,
        help_text="Retail price per gallon (USD)",
    )
    latitude = models.FloatField(
        db_index=True,
        help_text="Latitude coordinate (WGS84)",
    )
    longitude = models.FloatField(
        db_index=True,
        help_text="Longitude coordinate (WGS84)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fuel_stations"
        ordering = ["retail_price"]
        indexes = [
            models.Index(fields=["latitude", "longitude"], name="idx_station_coords"),
            models.Index(fields=["state", "city"], name="idx_station_location"),
            models.Index(fields=["retail_price"], name="idx_station_price"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) - ${self.retail_price:.3f}/gal"
