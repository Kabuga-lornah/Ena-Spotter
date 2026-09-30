# Fuel Route API (Spotter assessment)

A Django API that takes a start and finish location in the USA and returns:

- the driving route, as GeoJSON plus an interactive map page,
- the most cost-effective places to fuel up along it (500-mile range),
- the gallons bought at each stop and the total fuel cost (10 mpg).

It is built on **Django 6.1** (latest stable), and each route makes **one call** to the free routing API.

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

Locations may be given as:

| Format | Example | External calls |
| --- | --- | --- |
| `City, ST` or `City, State` | `Chicago, IL`, `Salt Lake City, Utah` | **0** (offline gazetteer) |
| `lat,lon` | `41.8781,-87.6298` | **0** |
| Any address | `1600 Pennsylvania Ave NW, Washington, DC` | 1 (Nominatim, cached) |

Response (abridged, illustrative values):

```json
{
  "start":  {"query": "New York, NY", "name": "New York, NY", "lat": 40.7571, "lon": -73.9778, "geocoded_by": "gazetteer"},
  "finish": {"query": "Los Angeles, CA", "name": "Los Angeles, CA", "lat": 34.0522, "lon": -118.2509, "geocoded_by": "gazetteer"},
  "route":  {"distance_miles": 2824.7, "duration_hours": 45.1},
  "vehicle": {"range_miles": 500, "miles_per_gallon": 10, "tank_gallons": 50.0},
  "fuel_stops": [
    {"stop_number": 1, "opis_id": 40084, "name": "7-ELEVEN #40084", "address": "US-46/US-1/US-9",
     "city": "Palisades Park", "state": "NJ", "lat": 40.85, "lon": -73.99,
     "price_per_gallon": 3.099, "mile_marker": 0.0, "distance_off_route_miles": 4.2,
     "gallons": 50.0, "cost": 154.95}
  ],
  "summary": {"number_of_stops": 8, "total_gallons": 282.47, "total_fuel_cost": 843.66,
              "average_price_per_gallon": 2.987, "stations_considered": 348},
  "map": {"type": "FeatureCollection", "features": ["route LineString", "start", "finish", "fuel_stop points"]},
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
  "meta": {"cache_hit": false, "external_api_calls": 1, "routing_provider": "OSRM (OpenStreetMap)", "compute_ms": 240.0}
}
```

`map` is standard GeoJSON, so you can paste it into https://geojson.io. `map_url` renders the same data with Leaflet and OpenStreetMap tiles.

Errors are returned as `{"error": "..."}` with these status codes:

- `400`: bad or missing input, or a location outside the USA.
- `422`: no route, or no fuel stations close enough together to finish the trip.
- `502`: the routing or geocoding service is down.

Other endpoints:

- `GET /api/route/map/?start=...&finish=...` returns the HTML map.
- `GET /api/health/` returns the number of stations loaded.

## How it works

```
request ─► geocode start/finish ─► OSRM route (1 call) ─► stations near the route ─► optimiser ─► JSON + map
           (offline for City, ST)                          (in-memory grid index)     (exact DP)
```

1. **Stations are loaded ahead of time** (`load_fuel_stations`).
   - The CSV has no coordinates. Each station is placed at its town's centre (the median of its ZIP code centroids), using the US ZIP code database bundled with the [`zipcodes`](https://pypi.org/project/zipcodes/) package. Every US row is matched, and 620 Canadian rows are skipped.
   - Duplicate rows for the same station keep the cheapest price.
   - This means **no geocoding API calls are ever made for stations**.
2. **Routing**: [OSRM](https://project-osrm.org/) is free and needs no API key. One request returns the full route geometry, the distance and the duration.
3. **Stations along the route** (`services/stations.py`):
   - The route is resampled about every mile, and the samples are bucketed into a 0.2° lat/lon grid.
   - Each station is compared only with nearby samples. A station within `STATION_CORRIDOR_MILES` (10) of the route counts as on the way, and its **mile marker** is recorded.
   - This takes tens of milliseconds for coast-to-coast routes, with no database queries: the stations are cached in memory.
4. **Choosing stops** (`services/optimizer.py`) is an exact dynamic program for the fixed-capacity refuelling problem (Khuller, Malekian & Mestre, *"To fill or not to fill"*).
   - At every stop in an optimal plan, the driver either fills the tank or buys just enough to reach the next stop, so the search space stays small.
   - It is tested against brute force on 500 random trips.
   - The objective is fuel cost plus a small **$2 penalty per stop** (`FUEL_STOP_PENALTY_USD`). "Optimal mostly means cost effective", so the plan will not stop for a one-gallon top-up that saves 30 cents. On NY to LA this cuts 13 stops down to 8 for $0.83 more. Set the penalty to `0` for the absolute cheapest plan.
5. **Caching**: whole results are cached per start/finish pair. Repeat requests, and opening the map page after the JSON call, take about 10 ms and make **0 external calls**.

**Speed:** about 0.25 s for a coast-to-coast plan (NY to LA, about 350 stations considered), plus the OSRM round trip. Cached responses take about 10 ms.

## Assumptions

- The vehicle **starts with an empty tank**. It fills up at the cheapest station within the first 25 miles of the route (`START_FILL_SEARCH_MILES`), or at the first station if none is that close. The few miles driven to reach that station are billed at its price. As a result, the total cost covers fuel for the whole trip: distance ÷ 10 mpg.
- The tank holds 500 miles of fuel (50 gallons). The vehicle arrives at the destination with an empty tank.
- Station locations are town centres, because the CSV has no coordinates. `distance_off_route_miles` is measured from that point.
- Only US stations and US locations are supported.

## Configuration (environment variables)

| Variable | Default |
| --- | --- |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` (public demo server; self-host for production) |
| `NOMINATIM_BASE_URL` | `https://nominatim.openstreetmap.org` |
| `STATION_CORRIDOR_MILES` | `10` |
| `START_FILL_SEARCH_MILES` | `25` |
| `FUEL_STOP_PENALTY_USD` | `2.0` |
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
