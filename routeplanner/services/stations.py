"""Find the fuel stations that lie along a route.

All ~6.7k US stations are held in memory (loaded once per process), and route
vertices are bucketed into a lat/lon grid so each station is only compared
against the handful of route points near it. This keeps the whole match in
the tens of milliseconds even for coast-to-coast routes, with no DB queries.
"""

import math
from collections import defaultdict
from dataclasses import dataclass

from routeplanner.models import FuelStation

MILES_PER_DEG_LAT = 69.0
GRID_DEG = 0.2
SAMPLE_SPACING_MILES = 1.0

_station_cache: list[dict] | None = None


def get_all_stations() -> list[dict]:
    global _station_cache
    if _station_cache is None:
        _station_cache = [
            {**s, "price": float(s["price"])}
            for s in FuelStation.objects.values(
                "opis_id", "name", "address", "city", "state", "price", "lat", "lon"
            )
        ]
    return _station_cache


def clear_station_cache():
    global _station_cache
    _station_cache = None


@dataclass
class RouteStation:
    station: dict
    mile: float  # distance along the route where the station is reached
    off_route_miles: float  # straight-line distance from the route

    @property
    def price(self) -> float:
        return self.station["price"]


def resample(coords, miles, step=SAMPLE_SPACING_MILES):
    """Return (lon, lat, mile) samples roughly every `step` miles along the polyline."""
    out = [(coords[0][0], coords[0][1], miles[0])]
    next_at = step
    for (lon1, lat1), (lon2, lat2), m1, m2 in zip(coords, coords[1:], miles, miles[1:]):
        seg = m2 - m1
        while seg > 0 and next_at <= m2:
            t = (next_at - m1) / seg
            out.append((lon1 + (lon2 - lon1) * t, lat1 + (lat2 - lat1) * t, next_at))
            next_at += step
    out.append((coords[-1][0], coords[-1][1], miles[-1]))
    return out


def _cell(lat, lon):
    return math.floor(lat / GRID_DEG), math.floor(lon / GRID_DEG)


def stations_along_route(coords, miles, corridor_miles, stations=None) -> list[RouteStation]:
    """Stations within `corridor_miles` of the route, with their mile marker, sorted by mile."""
    stations = get_all_stations() if stations is None else stations
    samples = resample(coords, miles)

    grid = defaultdict(list)
    for s in samples:
        grid[_cell(s[1], s[0])].append(s)

    lats = [s[1] for s in samples]
    lons = [s[0] for s in samples]
    max_abs_lat = max(abs(min(lats)), abs(max(lats)))
    miles_per_deg_lon = MILES_PER_DEG_LAT * max(math.cos(math.radians(max_abs_lat + 1)), 0.1)
    dlat = corridor_miles / MILES_PER_DEG_LAT
    dlon = corridor_miles / miles_per_deg_lon
    min_lat, max_lat = min(lats) - dlat, max(lats) + dlat
    min_lon, max_lon = min(lons) - dlon, max(lons) + dlon
    r_lat = math.ceil(dlat / GRID_DEG)
    r_lon = math.ceil(dlon / GRID_DEG)

    found = []
    for st in stations:
        lat, lon = st["lat"], st["lon"]
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        ci, cj = _cell(lat, lon)
        cos_lat = math.cos(math.radians(lat))
        best_d2, best_mile = None, None
        for i in range(ci - r_lat, ci + r_lat + 1):
            for j in range(cj - r_lon, cj + r_lon + 1):
                for s_lon, s_lat, s_mile in grid.get((i, j), ()):
                    # Equirectangular approximation: accurate to well under 1% at these distances.
                    dy = (s_lat - lat) * MILES_PER_DEG_LAT
                    dx = (s_lon - lon) * MILES_PER_DEG_LAT * cos_lat
                    d2 = dx * dx + dy * dy
                    if best_d2 is None or d2 < best_d2:
                        best_d2, best_mile = d2, s_mile
        if best_d2 is not None and best_d2 <= corridor_miles**2:
            found.append(RouteStation(st, best_mile, math.sqrt(best_d2)))

    found.sort(key=lambda rs: (rs.mile, rs.price))
    return found
