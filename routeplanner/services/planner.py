"""Glue: geocode -> route (1 routing call) -> stations along route -> cheapest fuel plan.

Each stage only depends on the previous stage's output (coordinates, then a route
polyline, then stations with mile markers), so the routing provider can be swapped
without touching station matching or the optimiser.

Whole results are cached by (start, finish, starting fuel), so repeating a request,
or opening the map page for a route the JSON API already planned, makes no API calls.
"""

import hashlib
import time

from django.conf import settings
from django.core.cache import cache

from . import geocoding, routing
from .geo import cumulative_miles, thin_polyline
from .optimizer import FILL, TO_DESTINATION, TO_NEXT, plan_fuel_stops
from .stations import stations_along_route

CACHE_VERSION = 3
# Route geometry in the response is thinned to this vertex spacing to keep payloads small.
OUTPUT_GEOMETRY_SPACING_MILES = 0.25


def _cache_key(start: str, finish: str, start_fuel_gallons: float, stop_penalty_usd: float) -> str:
    raw = f"{start.strip().lower()}|{finish.strip().lower()}|{start_fuel_gallons:.3f}|{stop_penalty_usd:.3f}"
    return f"plan:v{CACHE_VERSION}:" + hashlib.sha256(raw.encode()).hexdigest()


def plan_trip(start_query: str, finish_query: str, start_fuel_gallons: float | None = None,
              stop_penalty_usd: float | None = None) -> dict:
    t0 = time.perf_counter()
    mpg = settings.VEHICLE_MPG
    tank_gallons = settings.VEHICLE_RANGE_MILES / mpg
    if start_fuel_gallons is None:
        start_fuel_gallons = settings.VEHICLE_START_FUEL_GALLONS
    start_fuel_gallons = min(max(start_fuel_gallons, 0.0), tank_gallons)
    if stop_penalty_usd is None:
        stop_penalty_usd = settings.FUEL_STOP_PENALTY_USD

    key = _cache_key(start_query, finish_query, start_fuel_gallons, stop_penalty_usd)
    cached = cache.get(key)
    if cached is not None:
        meta = {**cached["meta"], "cache_hit": True, "geocoding_api_calls": 0, "routing_api_calls": 0,
                "compute_ms": round((time.perf_counter() - t0) * 1000, 1)}
        return {**cached, "meta": meta}

    start = geocoding.geocode(start_query)
    finish = geocoding.geocode(finish_query)
    route = routing.get_route(start.lat, start.lon, finish.lat, finish.lon)

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
        mpg=mpg,
        start_fuel_miles=start_fuel_gallons * mpg,
        start_search_miles=settings.START_FILL_SEARCH_MILES,
        stop_penalty=stop_penalty_usd,
    )

    purchased_gallons = sum(s.gallons for s in stops)
    total_cost = sum(s.cost for s in stops)
    used_gallons = route.distance_miles / mpg
    stops_out = []
    prev_mile = 0.0
    for n, s in enumerate(stops, start=1):
        st = s.route_station.station
        stops_out.append({
            "stop_number": n,
            "opis_id": st["opis_id"],
            "name": st["name"],
            "address": st["address"],
            "city": st["city"],
            "state": st["state"],
            "lat": round(st["lat"], 6),
            "lon": round(st["lon"], 6),
            "price_per_gallon": round(s.route_station.price, 3),
            "mile_marker": round(s.route_station.mile, 1),
            "distance_from_previous_stop_miles": round(s.route_station.mile - prev_mile, 1),
            "distance_off_route_miles": round(s.route_station.off_route_miles, 1),
            "fuel_on_arrival_gallons": round(s.fuel_on_arrival_miles / mpg, 2),
            "gallons": round(s.gallons, 2),
            "cost": round(s.cost, 2),
            "action": s.action,
            "reason": _reason(s, stops[n] if n < len(stops) else None),
        })
        prev_mile = s.route_station.mile

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
            "miles_per_gallon": mpg,
            "tank_gallons": tank_gallons,
        },
        "fuel_stops": stops_out,
        "summary": {
            "number_of_stops": len(stops_out),
            "total_fuel_cost": round(total_cost, 2),
            "fuel_purchased_gallons": round(purchased_gallons, 2),
            "average_price_per_gallon": round(total_cost / purchased_gallons, 3) if purchased_gallons else None,
            "starting_fuel_gallons": round(start_fuel_gallons, 2),
            "fuel_used_gallons": round(used_gallons, 2),
            "fuel_left_at_destination_gallons": round(max(0.0, start_fuel_gallons + purchased_gallons - used_gallons), 2),
            "stations_considered": len(candidates),
        },
        "assumptions": {
            "starting_fuel_gallons": round(start_fuel_gallons, 2),
            "total_fuel_cost_covers": "fuel bought at the stops on this trip (not the fuel already in the tank)",
            "station_corridor_miles": settings.STATION_CORRIDOR_MILES,
            "station_locations": "town centre of each station's city; the price list has no coordinates",
            "stop_penalty_usd": stop_penalty_usd,
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
            "routing_api_calls": 1,
            "geocoding_api_calls": start.api_calls + finish.api_calls,
            "routing_provider": "OSRM (OpenStreetMap)",
        },
    }
    cache.set(key, result)
    result["meta"]["compute_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


def _reason(stop, next_stop) -> str:
    price = stop.route_station.price
    if stop.action == TO_DESTINATION or next_stop is None:
        return "Bought just enough fuel to reach the destination."
    nxt_price = next_stop.route_station.price
    nxt = f"{next_stop.route_station.station['name']} at mile {next_stop.route_station.mile:.0f} (${nxt_price:.3f}/gal)"
    if stop.action == TO_NEXT:
        if nxt_price < price:
            return f"Bought just enough to reach the cheaper {nxt}."
        return f"Bought just enough to reach {nxt}."
    if stop.action == FILL and price < nxt_price:
        return f"Filled the tank: ${price:.3f}/gal here is cheaper than the next stop, {nxt}."
    return f"Filled the tank to reach {nxt}; no cheaper station was within range."


def _point(lon, lat, properties):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": properties,
    }
