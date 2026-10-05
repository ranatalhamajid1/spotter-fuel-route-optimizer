from django.contrib import admin
from routing.models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = (
        "opis_id",
        "name",
        "city",
        "state",
        "retail_price",
        "latitude",
        "longitude",
    )
    search_fields = ("name", "city", "state", "opis_id")
    list_filter = ("state",)
    ordering = ("retail_price",)
