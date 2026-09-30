"""Turn a user-supplied location into coordinates, preferring zero network calls.

"Geocoding" means turning a place name or address into latitude/longitude.

Accepted inputs, in order of preference:
  1. "lat,lon"               e.g. "41.8781,-87.6298"  (no API call)
  2. "City, ST" / "City, State" e.g. "Chicago, IL"      (offline gazetteer, no API call)
  3. anything else            e.g. "1600 Pennsylvania Ave, Washington DC" (Nominatim, 1 call, cached)

The "gazetteer" is the Place table: a list of every US town with its centre point,
built once by the load_fuel_stations command.
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

# Matches two numbers separated by a comma, e.g. "41.8781,-87.6298" or " 41.8 , -87.6 ".
_LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
# Raw coordinates must be this close to a known US town (rejects e.g. Toronto or Tijuana,
# which fall inside the rough US bounding box).
MAX_MILES_FROM_US_PLACE = 25
# Strips a trailing ", USA" / " US" / ", United States" so "Chicago, IL, USA" still works.
_COUNTRY_SUFFIX_RE = re.compile(r",?\s*(USA|US|United States( of America)?)\s*$", re.IGNORECASE)


@dataclass
class Location:
    """A geocoded place: what the user typed, a display name, and its coordinates."""

    query: str
    name: str
    lat: float
    lon: float
    source: str  # "coordinates" | "gazetteer" | "nominatim"
    api_calls: int = 0  # how many external API calls finding this location cost (0 or 1)

    def as_dict(self):
        """The shape this location takes in the API's JSON response."""
        return {
            "query": self.query,
            "name": self.name,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "geocoded_by": self.source,
        }


def geocode(query: str) -> Location:
    """Find the coordinates for `query`, trying the free/offline methods first."""
    query = (query or "").strip()
    if not query:
        raise InvalidLocation("Location is required.")

    # `a or b or c` tries each method in turn and stops at the first that returns a result
    # (each returns None when the input isn't in its format).
    loc = _from_coordinates(query) or _from_gazetteer(query) or _from_nominatim(query)
    if not is_in_usa(loc.lat, loc.lon):
        raise InvalidLocation(f"'{query}' is not within the USA.")
    return loc


def _from_coordinates(query):
    """Method 1: the user typed "lat,lon" directly. No lookup needed."""
    m = _LATLON_RE.match(query)
    if not m:
        return None  # not in "lat,lon" format: let the next method try
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise InvalidLocation(f"'{query}' is not a valid 'lat,lon' pair.")
    if not _near_us_place(lat, lon):
        raise InvalidLocation(f"'{query}' is not within the USA.")
    return Location(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")


def _near_us_place(lat, lon):
    """True if some US town is within MAX_MILES_FROM_US_PLACE of the point.

    Stricter than the bounding-box check: Toronto is inside the US bounding box,
    but there's no US town within 25 miles of it.
    """
    d = MAX_MILES_FROM_US_PLACE / 69.0  # the radius in degrees of latitude (69 miles per degree)
    # First a cheap square search in the database (longitude degrees are shorter, so allow 2x)...
    nearby = Place.objects.filter(lat__range=(lat - d, lat + d), lon__range=(lon - 2 * d, lon + 2 * d))
    # ...then the exact distance check on the few towns it returns.
    return any(haversine(lat, lon, p.lat, p.lon) <= MAX_MILES_FROM_US_PLACE for p in nearby.only("lat", "lon"))


def _from_gazetteer(query):
    """Method 2: "City, ST" or "City, State" looked up in the offline town list."""
    text = _COUNTRY_SUFFIX_RE.sub("", query).strip()
    if "," in text:
        # "Chicago, IL" -> city "Chicago", state " IL" (split at the LAST comma).
        city, _, state = text.rpartition(",")
    else:
        # "Dallas TX" / "Salt Lake City Utah" style: try the last one or two words as the state.
        words = text.split()
        for n in (2, 1):  # two words first, for states like "New York" / "North Dakota"
            if len(words) > n and normalize_state(" ".join(words[-n:])):
                city, state = " ".join(words[:-n]), " ".join(words[-n:])
                break
        else:  # (runs only if the loop didn't `break`) no state found at the end
            return None
    state_code = normalize_state(state)
    if not state_code or not city.strip():
        return None  # not a US state, or no city part: let Nominatim have a go
    place = Place.objects.filter(key=normalize_city(city), state=state_code).first()
    if place is None:
        return None  # unknown town: fall back to Nominatim
    return Location(query, f"{place.name}, {place.state}", place.lat, place.lon, "gazetteer")


def _from_nominatim(query):
    """Method 3: ask the free Nominatim (OpenStreetMap) geocoder. Costs one API call.

    Results are cached for a day, so asking for the same address again is free.
    """
    # The cache key is a hash of the lower-cased query, so "Main St" and "main st" share it.
    normalized = re.sub(r"\s+", " ", query.lower())
    cache_key = "geocode:" + hashlib.sha256(normalized.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return Location(query, cached["name"], cached["lat"], cached["lon"], "nominatim")

    try:
        resp = requests.get(
            f"{settings.NOMINATIM_BASE_URL}/search",
            # countrycodes=us restricts results to the USA; limit=1 returns only the best match.
            params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us"},
            # Nominatim's usage policy requires a descriptive User-Agent.
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()  # turn HTTP errors (e.g. 500) into exceptions
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        # Network problem or garbage response: report it as "the upstream service is down" (502).
        raise UpstreamError(f"Geocoding service unavailable: {exc}") from exc

    if not results:
        raise InvalidLocation(f"Could not find a location in the USA matching '{query}'.")
    hit = results[0]
    data = {"name": hit.get("display_name", query), "lat": float(hit["lat"]), "lon": float(hit["lon"])}
    cache.set(cache_key, data, timeout=60 * 60 * 24)  # remember it for 24 hours
    return Location(query, data["name"], data["lat"], data["lon"], "nominatim", api_calls=1)
