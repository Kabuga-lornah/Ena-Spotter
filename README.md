# Fuel Route API (Spotter assessment)

A Django API that takes a start and finish location in the USA and returns:

- the driving route, as GeoJSON plus an interactive map page,
- the most cost-effective places to fuel up along it (500-mile range),
- the gallons bought at each stop and the total fuel cost (10 mpg).

It is built on **Django 6.1** (latest stable). Each new trip makes **one call to the free routing API** (OSRM). Locations given as "City, ST" or "lat,lon" need no geocoding call; only a free-form street address adds one geocoding call.

## Quick start

Requires Python 3.12+ (Django 6.1's minimum).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py load_fuel_stations  # one-off: loads + geocodes the CSV (~5 s)
python manage.py runserver
```

Then try it:

```bash
curl "http://127.0.0.1:8000/api/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA"
```

To see the map, open the `map_url` from the response in a browser, e.g.
http://127.0.0.1:8000/api/route/map/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA

For the Postman demo, import `postman/fuel-route-api.postman_collection.json`.

Run the tests with `python manage.py test`. They need no network access, because the routing API is mocked.

## API

### `GET /api/route/?start=...&finish=...` or `POST /api/route/` with `{"start": "...", "finish": "..."}`

| Parameter | Required | Meaning |
| --- | --- | --- |
| `start`, `finish` | yes | US locations (formats below) |
| `start_fuel_gallons` | no | Fuel in the tank at the start, 0–50. **Default 50 (full tank).** `0` = start empty and fill up near the start. |
| `stop_penalty_usd` | no | Default **0** = cheapest possible fuel. Set e.g. `2` to skip stops that save less than $2. |

Locations may be given as:

| Format | Example | External calls |
| --- | --- | --- |
| `City, ST` or `City, State` | `Chicago, IL`, `Salt Lake City, Utah` | **0** (offline gazetteer) |
| `lat,lon` | `41.8781,-87.6298` | **0** |
| Any address | `1600 Pennsylvania Ave NW, Washington, DC` | 1 geocoding call (Nominatim, cached) |

Response (abridged, illustrative values):

```json
{
  "start":  {"query": "New York, NY", "name": "New York, NY", "lat": 40.7571, "lon": -73.9778, "geocoded_by": "gazetteer"},
  "finish": {"query": "Los Angeles, CA", "name": "Los Angeles, CA", "lat": 34.0522, "lon": -118.2509, "geocoded_by": "gazetteer"},
  "route":  {"distance_miles": 2824.7, "duration_hours": 45.1},
  "vehicle": {"range_miles": 500, "miles_per_gallon": 10, "tank_gallons": 50.0},
  "fuel_stops": [
    {"stop_number": 3, "opis_id": 1234, "name": "QUIKTRIP #605", "address": "I-270, EXIT 33",
     "city": "Saint Louis", "state": "MO", "lat": 38.63, "lon": -90.24,
     "price_per_gallon": 2.899, "mile_marker": 1016.0, "distance_from_previous_stop_miles": 451.0,
     "distance_off_route_miles": 3.1, "fuel_on_arrival_gallons": 0.0,
     "gallons": 36.0, "cost": 104.36, "action": "to_next_stop",
     "reason": "Bought just enough to reach the cheaper SHORT STOP #13 at mile 1376 ($2.839/gal)."}
  ],
  "summary": {"number_of_stops": 13, "total_fuel_cost": 687.60, "fuel_purchased_gallons": 232.47,
              "average_price_per_gallon": 2.958, "starting_fuel_gallons": 50.0,
              "fuel_used_gallons": 282.47, "fuel_left_at_destination_gallons": 0.0, "stations_considered": 348},
  "assumptions": {"starting_fuel_gallons": 50.0,
                  "total_fuel_cost_covers": "fuel bought at the stops on this trip (not the fuel already in the tank)",
                  "station_corridor_miles": 10.0,
                  "station_locations": "town centre of each station's city; the price list has no coordinates",
                  "stop_penalty_usd": 0.0},
  "map": {"type": "FeatureCollection", "features": ["route LineString", "start", "finish", "fuel_stop points"]},
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
  "meta": {"cache_hit": false, "routing_api_calls": 1, "geocoding_api_calls": 0,
           "routing_provider": "OSRM (OpenStreetMap)", "compute_ms": 280.0}
}
```

Each stop explains itself: `action` is `fill`, `to_next_stop` (buy just enough to reach the next stop) or `to_destination`, and `reason` says why in plain words.

`map` is standard GeoJSON, so you can paste it into https://geojson.io. `map_url` renders the same data with Leaflet and OpenStreetMap tiles.

Errors are returned as `{"error": "..."}` with these status codes:

- `400`: bad or missing input, or a location outside the USA.
- `422`: no route, or no feasible fuel plan (somewhere on the route the next station is more than 500 miles away, or the first station is beyond the starting fuel). The API never returns a plan the vehicle couldn't drive.
- `502`: the routing or geocoding service is down.

Other endpoints:

- `GET /api/route/map/?start=...&finish=...` returns the HTML map.
- `GET /api/health/` returns the number of stations loaded.

## How it works

```
request ─► geocode start/finish ─► OSRM route (1 call) ─► stations near the route ─► optimiser ─► JSON + map
           (offline for City, ST)                          (in-memory grid index)     (exact DP)
```

Each stage only consumes the previous stage's output (coordinates → route polyline → stations with mile markers → stops), so the routing provider can be swapped (`services/routing.py`) without touching station matching or the optimiser.

1. **Stations are loaded ahead of time** (`load_fuel_stations`).
   - The CSV has no coordinates. Each station is placed at its town's centre (the median of its ZIP code centroids), using the US ZIP code database bundled with the [`zipcodes`](https://pypi.org/project/zipcodes/) package. Every US row is matched, and 620 Canadian rows are skipped.
   - `FuelStation` stores its own `lat`/`lon`, so a better source (e.g. geocoding each address once) can replace the town centres later without changing anything else.
   - Duplicate rows for the same station keep the cheapest price.
   - This means **no geocoding API calls are ever made for stations**.
2. **Routing**: [OSRM](https://project-osrm.org/) is free and needs no API key. One request returns the full route geometry, the distance and the duration.
3. **Stations along the route** (`services/stations.py`):
   - The route is resampled about every mile, and the samples are bucketed into a 0.2° lat/lon grid.
   - Each station is compared only with nearby samples. A station within `STATION_CORRIDOR_MILES` (default 10, configurable) of the route counts as on the way, and its **mile marker** is recorded. The brief doesn't say how far a driver will leave the route, so this is an explicit, adjustable assumption.
   - This takes tens of milliseconds for coast-to-coast routes, with no database queries: the stations are cached in memory.
4. **Choosing stops** (`services/optimizer.py`) is an exact dynamic program for the fixed-capacity refuelling problem (Khuller, Malekian & Mestre, *"To fill or not to fill"*).
   - Nodes run **start → stations → destination**. The start holds the starting fuel; the destination is the final node, so the last stop buys only enough to finish.
   - At every stop in an optimal plan, the driver either fills the tank or buys just enough to reach the next stop, so the search space stays small.
   - **Objective: total fuel cost.** Plans with (near-)equal cost are decided by fewer stops.
   - Pure cost can suggest top-ups that save only cents (on NY → LA: 13 stops, $687.60). `stop_penalty_usd=2` gives 7 stops for $688.71. It's off by default and opt-in per request.
   - It is tested against brute force on 1,000 random trips with empty, partial and full starting tanks.
5. **Caching**: whole results are cached per start, finish and options. Repeat requests, and opening the map page after the JSON call, take about 10 ms and make **0 external calls**. This uses Django's in-process memory cache, which is fine for the assessment but is cleared on restart and not shared between server processes; production would use Redis.

**Speed:** about 0.3 s for a coast-to-coast plan (NY → LA, about 350 stations considered), plus the OSRM round trip. Cached responses take about 10 ms.

## Assumptions

- The vehicle **starts with a full tank** (50 gallons = 500 miles), overridable with `start_fuel_gallons`. So `total_fuel_cost` is the money spent on fuel **during the trip**; trips under 500 miles need no stops and cost $0. The response also reports `fuel_used_gallons` for the whole trip.
- With `start_fuel_gallons=0`, the vehicle starts empty and fills up at the cheapest station within the first 25 miles of the route (`START_FILL_SEARCH_MILES`), or the first station if none is that close; the few miles to reach it are billed at its price. Then the total cost covers all the fuel the trip uses.
- The vehicle may arrive at the destination with an empty tank.
- Station locations are town centres, because the CSV has no coordinates. `distance_off_route_miles` is measured from that point.
- A station within 10 miles of the route (straight line) counts as "on the route".
- Only US stations and US locations are supported.

## Configuration (environment variables)

| Variable | Default |
| --- | --- |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` (public demo server; self-host for production) |
| `NOMINATIM_BASE_URL` | `https://nominatim.openstreetmap.org` |
| `VEHICLE_START_FUEL_GALLONS` | `50` (full tank) |
| `STATION_CORRIDOR_MILES` | `10` |
| `START_FILL_SEARCH_MILES` | `25` |
| `FUEL_STOP_PENALTY_USD` | `0` |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | dev defaults |

## Project layout

```
config/                      Django settings and URLs
routeplanner/
  models.py                  FuelStation, Place (offline gazetteer)
  views.py, urls.py          /api/route/, /api/route/map/, /api/health/
  services/
    geocoding.py             City, ST / lat,lon / Nominatim fallback
    routing.py               OSRM client (1 call)
    stations.py              stations-near-route grid index
    optimizer.py             cheapest fuel plan (exact DP)
    planner.py               orchestration + caching
  management/commands/load_fuel_stations.py
  templates/routeplanner/map.html   Leaflet map
  tests/                     optimiser (vs brute force), geocoding, API
data/fuel-prices-for-be-assessment.csv
postman/fuel-route-api.postman_collection.json
```

## What I'd improve with more time

- Geocode each station's actual address once at import (e.g. from the OPIS data or a batch geocoder) instead of using town centres.
- Measure station distance along real roads (the detour) rather than a straight line to the route.
- Redis cache and a self-hosted OSRM instance for production traffic.
