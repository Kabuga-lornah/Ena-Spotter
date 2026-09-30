from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.RoutePlanView.as_view(), name="route-plan"),
    path("route/map/", views.RouteMapView.as_view(), name="route-map"),
    path("health/", views.health, name="health"),
]
