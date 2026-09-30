"""Glue: geocode -> route (1 API call) -> stations along route -> cheapest fuel plan.

Whole results are cached by (start, finish), so repeating a request, or opening
the map page for a route the JSON API already planned, makes no API calls.
"""

import hashlib
import time

from django.conf import settings
from django.core.cache import cache

from . import geocoding, routing
from .geo import cumulative_miles, thin_polyline
from .optimizer import plan_fuel_stops
from .stations import stations_along_route

CACHE_VERSION = 2
# Route geometry in the response is thinned to this vertex spacing to keep payloads small.
OUTPUT_GEOMETRY_SPACING_MILES = 0.25


def _cache_key(start: str, finish: str) -> str:
    raw = f"{start.strip().lower()}|{finish.strip().lower()}"
    return f"plan:v{CACHE_VERSION}:" + hashlib.sha256(raw.encode()).hexdigest()


def plan_trip(start_query: str, finish_query: str) -> dict:
    t0 = time.perf_counter()
    key = _cache_key(start_query, finish_query)
    cached = cache.get(key)
    if cached is not None:
        return {**cached, "meta": {**cached["meta"], "cache_hit": True, "external_api_calls": 0,
                                   "compute_ms": round((time.perf_counter() - t0) * 1000, 1)}}

    start = geocoding.geocode(start_query)
    finish = geocoding.geocode(finish_query)
    route = routing.get_route(start.lat, start.lon, finish.lat, finish.lon)
    api_calls = start.api_calls + finish.api_calls + 1

    coords = route.coordinates
    miles = cumulative_miles(coords)
    # Scale the polyline's length to OSRM's road distance so mile markers add up exactly.
    scale = route.distance_miles / miles[-1] if miles[-1] > 0 else 1.0
    miles = [m * scale for m in miles]

    candidates = stations_along_route(coords, miles, settings.STATION_CORRIDOR_MILES)
    stops = plan_fuel_stops(
        candidates,
        total_miles=route.distance_miles,
        range_miles=settings.VEHICLE_RANGE_MILES,
        mpg=settings.VEHICLE_MPG,
        start_search_miles=settings.START_FILL_SEARCH_MILES,
        stop_penalty=settings.FUEL_STOP_PENALTY_USD,
    )

    total_gallons = sum(s.gallons for s in stops)
    total_cost = sum(s.cost for s in stops)
    stops_out = [
        {
            "stop_number": n,
            "opis_id": s.route_station.station["opis_id"],
            "name": s.route_station.station["name"],
            "address": s.route_station.station["address"],
            "city": s.route_station.station["city"],
            "state": s.route_station.station["state"],
            "lat": round(s.route_station.station["lat"], 6),
            "lon": round(s.route_station.station["lon"], 6),
            "price_per_gallon": round(s.route_station.price, 3),
            "mile_marker": round(s.route_station.mile, 1),
            "distance_off_route_miles": round(s.route_station.off_route_miles, 1),
            "gallons": round(s.gallons, 2),
            "cost": round(s.cost, 2),
        }
        for n, s in enumerate(stops, start=1)
    ]

    out_coords, _ = thin_polyline(coords, miles, OUTPUT_GEOMETRY_SPACING_MILES)
    geometry = {"type": "LineString", "coordinates": [[round(lon, 5), round(lat, 5)] for lon, lat in out_coords]}

    result = {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 2),
        },
        "vehicle": {
            "range_miles": settings.VEHICLE_RANGE_MILES,
            "miles_per_gallon": settings.VEHICLE_MPG,
            "tank_gallons": settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG,
        },
        "fuel_stops": stops_out,
        "summary": {
            "number_of_stops": len(stops_out),
            "total_gallons": round(total_gallons, 2),
            "total_fuel_cost": round(total_cost, 2),
            "average_price_per_gallon": round(total_cost / total_gallons, 3) if total_gallons else None,
            "stations_considered": len(candidates),
        },
        "map": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": geometry, "properties": {"kind": "route"}},
                _point(start.lon, start.lat, {"kind": "start", "name": start.name}),
                _point(finish.lon, finish.lat, {"kind": "finish", "name": finish.name}),
                *(
                    _point(s["lon"], s["lat"], {"kind": "fuel_stop", **{k: v for k, v in s.items() if k not in ("lat", "lon")}})
                    for s in stops_out
                ),
            ],
        },
        "meta": {
            "cache_hit": False,
            "external_api_calls": api_calls,
            "routing_provider": "OSRM (OpenStreetMap)",
        },
    }
    cache.set(key, result)
    result["meta"]["compute_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


def _point(lon, lat, properties):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": properties,
    }
