"""Find the fuel stations that lie along a route.

All ~6.7k US stations are held in memory (loaded once per process), and route
vertices are bucketed into a lat/lon grid so each station is only compared
against the handful of route points near it. This keeps the whole match in
the tens of milliseconds even for coast-to-coast routes, with no DB queries.

Why a grid? Comparing every station with every route point would be about
6,600 stations x 2,800 points = 18 million distance calculations. With the grid,
most stations are rejected instantly (no route points in the squares around
them) and the rest are compared with only a few dozen nearby points.
"""

import math
from collections import defaultdict
from dataclasses import dataclass

from routeplanner.models import FuelStation

# One degree of latitude is about 69 miles everywhere on Earth.
MILES_PER_DEG_LAT = 69.0
# Size of each grid square, in degrees (0.2 deg is about 14 miles north-south).
GRID_DEG = 0.2
# We place a sample point on the route every mile.
SAMPLE_SPACING_MILES = 1.0

# In-memory copy of all stations. Filled on first use, then reused by every request,
# so planning a route never has to query the database for stations.
_station_cache: list[dict] | None = None


def get_all_stations() -> list[dict]:
    """Return every station as a plain dict, loading them from the database only once."""
    global _station_cache
    if _station_cache is None:
        _station_cache = [
            # Prices are stored as Decimal in the DB; float is faster for the maths here.
            {**s, "price": float(s["price"])}
            for s in FuelStation.objects.values(
                "opis_id", "name", "address", "city", "state", "price", "lat", "lon"
            )
        ]
    return _station_cache


def clear_station_cache():
    """Forget the in-memory copy (called after re-loading the CSV) so fresh data is used."""
    global _station_cache
    _station_cache = None


@dataclass
class RouteStation:
    """A fuel station that is near the route, plus where along the route it is."""

    station: dict
    mile: float  # distance along the route where the station is reached
    off_route_miles: float  # straight-line distance from the route

    @property
    def price(self) -> float:
        return self.station["price"]


def resample(coords, miles, step=SAMPLE_SPACING_MILES):
    """Return (lon, lat, mile) samples roughly every `step` miles along the polyline.

    OSRM's points are unevenly spaced: many close together in cities, few on long
    straight highways. Evenly spaced samples guarantee that no stretch of road is
    left without a point to compare stations against.
    """
    out = [(coords[0][0], coords[0][1], miles[0])]  # start of the route
    next_at = step  # mile at which the next sample should be placed
    # Walk each segment of the route (from point 1 to point 2)...
    for (lon1, lat1), (lon2, lat2), m1, m2 in zip(coords, coords[1:], miles, miles[1:]):
        seg = m2 - m1  # length of this segment in miles
        # ...and drop a sample at every whole `step` mile that falls inside it.
        while seg > 0 and next_at <= m2:
            t = (next_at - m1) / seg  # how far along the segment (0.0 to 1.0)
            # Linear interpolation between the two end points.
            out.append((lon1 + (lon2 - lon1) * t, lat1 + (lat2 - lat1) * t, next_at))
            next_at += step
    out.append((coords[-1][0], coords[-1][1], miles[-1]))  # end of the route
    return out


def _cell(lat, lon):
    """Which grid square a point falls in, as (row, column)."""
    return math.floor(lat / GRID_DEG), math.floor(lon / GRID_DEG)


def stations_along_route(coords, miles, corridor_miles, stations=None) -> list[RouteStation]:
    """Stations within `corridor_miles` of the route, with their mile marker, sorted by mile."""
    stations = get_all_stations() if stations is None else stations
    samples = resample(coords, miles)

    # Step 1: put every route sample into its grid square.
    grid = defaultdict(list)
    for s in samples:
        grid[_cell(s[1], s[0])].append(s)

    # Step 2: work out how many grid squares the corridor spans in each direction.
    lats = [s[1] for s in samples]
    lons = [s[0] for s in samples]
    # A degree of longitude gets shorter towards the poles, so use the most northern
    # (or southern) latitude on the route to stay on the safe side.
    max_abs_lat = max(abs(min(lats)), abs(max(lats)))
    miles_per_deg_lon = MILES_PER_DEG_LAT * max(math.cos(math.radians(max_abs_lat + 1)), 0.1)
    dlat = corridor_miles / MILES_PER_DEG_LAT  # corridor width in degrees of latitude
    dlon = corridor_miles / miles_per_deg_lon  # corridor width in degrees of longitude
    # A bounding box around the whole route (plus the corridor) to skip far-away stations fast.
    min_lat, max_lat = min(lats) - dlat, max(lats) + dlat
    min_lon, max_lon = min(lons) - dlon, max(lons) + dlon
    # How many neighbouring squares to check around a station's own square.
    r_lat = math.ceil(dlat / GRID_DEG)
    r_lon = math.ceil(dlon / GRID_DEG)

    # Step 3: for each station, find the nearest route sample (if any is close enough).
    found = []
    for st in stations:
        lat, lon = st["lat"], st["lon"]
        # Quick reject: nowhere near the route at all.
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        ci, cj = _cell(lat, lon)
        cos_lat = math.cos(math.radians(lat))
        best_d2, best_mile = None, None  # squared distance to, and mile of, the nearest sample
        # Look only at the samples in this station's square and the squares around it.
        for i in range(ci - r_lat, ci + r_lat + 1):
            for j in range(cj - r_lon, cj + r_lon + 1):
                for s_lon, s_lat, s_mile in grid.get((i, j), ()):
                    # Equirectangular approximation: accurate to well under 1% at these distances.
                    dy = (s_lat - lat) * MILES_PER_DEG_LAT
                    dx = (s_lon - lon) * MILES_PER_DEG_LAT * cos_lat
                    d2 = dx * dx + dy * dy  # squared distance (no square root needed to compare)
                    if best_d2 is None or d2 < best_d2:
                        best_d2, best_mile = d2, s_mile
        # Keep the station if its nearest route point is inside the corridor.
        if best_d2 is not None and best_d2 <= corridor_miles**2:
            found.append(RouteStation(st, best_mile, math.sqrt(best_d2)))

    # Step 4: order the stations as the driver meets them (cheapest first at the same mile).
    found.sort(key=lambda rs: (rs.mile, rs.price))
    return found
