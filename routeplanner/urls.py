# Which URL goes to which view. These are mounted under /api/ (see config/urls.py),
# so "route/" below becomes /api/route/.
from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.RoutePlanView.as_view(), name="route-plan"),  # JSON trip plan
    path("route/map/", views.RouteMapView.as_view(), name="route-map"),  # HTML map of the plan
    path("health/", views.health, name="health"),  # is the server up?
]
