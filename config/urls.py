# The project's top-level URLs: everything under /api/ is handled by the routeplanner app
# (its own list of URLs is in routeplanner/urls.py).
from django.urls import include, path

urlpatterns = [
    path("api/", include("routeplanner.urls")),
]
