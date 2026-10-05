"""Root URL Configuration for Spotter Fuel Route Optimizer."""

from django.contrib import admin
from django.urls import include, path
from routing.views import DashboardView

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("admin/", admin.site.urls),
    path("api/", include("routing.urls")),
]
