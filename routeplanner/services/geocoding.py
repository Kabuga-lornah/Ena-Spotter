"""Turn a user-supplied location into coordinates, preferring zero network calls.

Accepted inputs, in order of preference:
  1. "lat,lon"               e.g. "41.8781,-87.6298"  (no API call)
  2. "City, ST" / "City, State" e.g. "Chicago, IL"      (offline gazetteer, no API call)
  3. anything else            e.g. "1600 Pennsylvania Ave, Washington DC" (Nominatim, 1 call, cached)
"""

import hashlib
import re
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from routeplanner.models import Place

from .errors import InvalidLocation, UpstreamError
from .geo import haversine, is_in_usa, normalize_city, normalize_state

_LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
# Raw coordinates must be this close to a known US town (rejects e.g. Toronto or Tijuana,
# which fall inside the rough US bounding box).
MAX_MILES_FROM_US_PLACE = 25
_COUNTRY_SUFFIX_RE = re.compile(r",?\s*(USA|US|United States( of America)?)\s*$", re.IGNORECASE)


@dataclass
class Location:
    query: str
    name: str
    lat: float
    lon: float
    source: str  # "coordinates" | "gazetteer" | "nominatim"
    api_calls: int = 0

    def as_dict(self):
        return {
            "query": self.query,
            "name": self.name,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "geocoded_by": self.source,
        }


def geocode(query: str) -> Location:
    query = (query or "").strip()
    if not query:
        raise InvalidLocation("Location is required.")

    loc = _from_coordinates(query) or _from_gazetteer(query) or _from_nominatim(query)
    if not is_in_usa(loc.lat, loc.lon):
        raise InvalidLocation(f"'{query}' is not within the USA.")
    return loc


def _from_coordinates(query):
    m = _LATLON_RE.match(query)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise InvalidLocation(f"'{query}' is not a valid 'lat,lon' pair.")
    if not _near_us_place(lat, lon):
        raise InvalidLocation(f"'{query}' is not within the USA.")
    return Location(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")


def _near_us_place(lat, lon):
    d = MAX_MILES_FROM_US_PLACE / 69.0
    nearby = Place.objects.filter(lat__range=(lat - d, lat + d), lon__range=(lon - 2 * d, lon + 2 * d))
    return any(haversine(lat, lon, p.lat, p.lon) <= MAX_MILES_FROM_US_PLACE for p in nearby.only("lat", "lon"))


def _from_gazetteer(query):
    text = _COUNTRY_SUFFIX_RE.sub("", query).strip()
    if "," in text:
        city, _, state = text.rpartition(",")
    else:
        # "Dallas TX" / "Salt Lake City Utah" style: try the last one or two words as the state.
        words = text.split()
        for n in (2, 1):
            if len(words) > n and normalize_state(" ".join(words[-n:])):
                city, state = " ".join(words[:-n]), " ".join(words[-n:])
                break
        else:
            return None
    state_code = normalize_state(state)
    if not state_code or not city.strip():
        return None
    place = Place.objects.filter(key=normalize_city(city), state=state_code).first()
    if place is None:
        return None
    return Location(query, f"{place.name}, {place.state}", place.lat, place.lon, "gazetteer")


def _from_nominatim(query):
    normalized = re.sub(r"\s+", " ", query.lower())
    cache_key = "geocode:" + hashlib.sha256(normalized.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return Location(query, cached["name"], cached["lat"], cached["lon"], "nominatim")

    try:
        resp = requests.get(
            f"{settings.NOMINATIM_BASE_URL}/search",
            params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise UpstreamError(f"Geocoding service unavailable: {exc}") from exc

    if not results:
        raise InvalidLocation(f"Could not find a location in the USA matching '{query}'.")
    hit = results[0]
    data = {"name": hit.get("display_name", query), "lat": float(hit["lat"]), "lon": float(hit["lon"])}
    cache.set(cache_key, data, timeout=60 * 60 * 24)
    return Location(query, data["name"], data["lat"], data["lon"], "nominatim", api_calls=1)
