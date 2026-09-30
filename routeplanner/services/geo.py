"""Small, dependency-free geometry helpers (all distances in miles)."""

import math
import re

EARTH_RADIUS_MILES = 3958.8
METERS_PER_MILE = 1609.344

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
STATE_NAME_TO_CODE = {name.upper(): code for code, name in US_STATES.items()}

# Rough bounding boxes (min_lat, max_lat, min_lon, max_lon) covering US states.
_US_BOXES = (
    (24.3, 49.5, -125.0, -66.8),   # contiguous US
    (51.0, 71.6, -180.0, -129.9),  # Alaska
    (18.8, 22.4, -160.3, -154.7),  # Hawaii
)

_CITY_PREFIXES = (("SAINT ", "ST "), ("FORT ", "FT "), ("MOUNT ", "MT "))


def normalize_city(name: str) -> str:
    """Normalise a city name so 'De Forest', 'DeForest' and 'Saint Louis'/'St. Louis' match."""
    s = name.upper().replace(".", " ").strip()
    s = re.sub(r"\s+", " ", s)
    for long, short in _CITY_PREFIXES:
        if s.startswith(long):
            s = short + s[len(long):]
    return re.sub(r"[^A-Z0-9]", "", s)


def normalize_state(value: str) -> str | None:
    s = value.strip().upper().replace(".", "")
    if s in US_STATES:
        return s
    return STATE_NAME_TO_CODE.get(s)


def is_in_usa(lat: float, lon: float) -> bool:
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in _US_BOXES)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def cumulative_miles(coords: list[tuple[float, float]]) -> list[float]:
    """Cumulative distance along a [(lon, lat), ...] polyline."""
    out = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        out.append(out[-1] + haversine(lat1, lon1, lat2, lon2))
    return out


def thin_polyline(coords, miles, min_spacing):
    """Drop vertices closer than `min_spacing` miles to the last kept one (keeps both ends)."""
    if len(coords) <= 2:
        return list(coords), list(miles)
    kept_c, kept_m = [coords[0]], [miles[0]]
    for c, m in zip(coords[1:-1], miles[1:-1]):
        if m - kept_m[-1] >= min_spacing:
            kept_c.append(c)
            kept_m.append(m)
    kept_c.append(coords[-1])
    kept_m.append(miles[-1])
    return kept_c, kept_m
