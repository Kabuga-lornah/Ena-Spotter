"""Django settings for the fuel route planner API."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "routeplanner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.gzip.GZipMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "routeplanner",
        "TIMEOUT": 60 * 60 * 6,
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Route planner -----------------------------------------------------------

FUEL_PRICES_CSV = BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv"

# Free routing API: OSRM (OpenStreetMap data, no API key).
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
# Free geocoder, only used when a location is not "City, ST" or "lat,lon".
NOMINATIM_BASE_URL = os.environ.get("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")
HTTP_USER_AGENT = os.environ.get("HTTP_USER_AGENT", "ena-spotter-fuel-route-api/1.0")
HTTP_TIMEOUT_SECONDS = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "15"))

VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10
# How far off the route (straight line) a station may be to count as "on the way".
STATION_CORRIDOR_MILES = float(os.environ.get("STATION_CORRIDOR_MILES", "10"))
# The vehicle starts empty and fills up at the cheapest station this close to the start.
START_FILL_SEARCH_MILES = float(os.environ.get("START_FILL_SEARCH_MILES", "25"))
# "Optimal mostly means cost effective": an extra stop must save at least this much (USD).
FUEL_STOP_PENALTY_USD = float(os.environ.get("FUEL_STOP_PENALTY_USD", "2.0"))
