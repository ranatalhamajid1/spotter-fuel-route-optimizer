"""
URL routing configuration for the routing app.
"""

from django.urls import path
from routing.views import RoutePlanView

app_name = "routing"

urlpatterns = [
    path("route/", RoutePlanView.as_view(), name="route-plan"),
]
