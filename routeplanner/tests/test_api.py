from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from routeplanner.services import stations as stations_module
from routeplanner.services.geo import METERS_PER_MILE, cumulative_miles
from routeplanner.services.geocoding import geocode
from routeplanner.services.errors import InvalidLocation
from routeplanner.services.stations import resample, stations_along_route

# A rough Chicago -> Dallas road path through real towns (lon, lat).
WAYPOINTS = [
    (-87.6298, 41.8781),  # Chicago, IL
    (-89.6501, 39.7817),  # Springfield, IL
    (-90.1994, 38.6270),  # St Louis, MO
    (-92.1735, 37.9514),  # Rolla area, MO
    (-93.2923, 37.2090),  # Springfield, MO
    (-94.5133, 37.0842),  # Joplin, MO
    (-95.9928, 36.1540),  # Tulsa, OK
    (-97.5164, 35.4676),  # Oklahoma City, OK
    (-97.1331, 33.2148),  # Denton, TX
    (-96.7970, 32.7767),  # Dallas, TX
]


def fake_osrm_response(waypoints=WAYPOINTS):
    coords = [(p[0], p[1]) for p in resample(waypoints, cumulative_miles(waypoints), step=0.5)]
    distance_m = cumulative_miles(coords)[-1] * 1.05 * METERS_PER_MILE
    payload = {
        "code": "Ok",
        "routes": [{"distance": distance_m, "duration": distance_m / 27,
                    "geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]}}],
    }
    resp = mock.Mock(status_code=200)
    resp.json.return_value = payload
    return resp


class DataTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_fuel_stations", stdout=mock.Mock())

    def setUp(self):
        cache.clear()
        stations_module.clear_station_cache()


class GeocodingTests(DataTestCase):
    def test_city_state_offline(self):
        for q in ["Chicago, IL", "chicago, illinois", "Chicago IL", "Chicago, IL, USA"]:
            loc = geocode(q)
            self.assertEqual(loc.source, "gazetteer", q)
            self.assertAlmostEqual(loc.lat, 41.88, delta=0.2)
            self.assertEqual(loc.api_calls, 0)

    def test_saint_abbreviations(self):
        self.assertEqual(geocode("Saint Louis, MO").name, geocode("St. Louis, MO").name)

    def test_coordinates(self):
        loc = geocode("36.1540, -95.9928")
        self.assertEqual((loc.source, loc.lat, loc.lon), ("coordinates", 36.154, -95.9928))

    def test_outside_usa_rejected(self):
        with self.assertRaises(InvalidLocation):
            geocode("48.8566,2.3522")  # Paris

    @mock.patch("routeplanner.services.geocoding.requests.get")
    def test_nominatim_fallback_is_cached(self, get):
        get.return_value.json.return_value = [{"lat": "38.8977", "lon": "-77.0365", "display_name": "White House"}]
        loc = geocode("1600 Pennsylvania Ave NW, Washington")
        self.assertEqual((loc.source, loc.api_calls), ("nominatim", 1))
        loc2 = geocode("1600 Pennsylvania Ave NW, Washington")
        self.assertEqual(loc2.api_calls, 0)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs["params"]["countrycodes"], "us")


class StationMatchingTests(DataTestCase):
    def test_finds_stations_along_route_in_order(self):
        coords = [tuple(c) for c in fake_osrm_response().json()["routes"][0]["geometry"]["coordinates"]]
        miles = cumulative_miles(coords)
        found = stations_along_route(coords, miles, corridor_miles=10)
        self.assertGreater(len(found), 50)
        self.assertEqual([f.mile for f in found], sorted(f.mile for f in found))
        self.assertTrue(all(f.off_route_miles <= 10 for f in found))
        states = {f.station["state"] for f in found}
        self.assertTrue({"IL", "MO", "OK", "TX"} <= states)


@mock.patch("routeplanner.services.routing.requests.get", return_value=fake_osrm_response())
class RouteApiTests(DataTestCase):
    def test_plan_route_get(self, get):
        resp = self.client.get("/api/route/", {"start": "Chicago, IL", "finish": "Dallas, TX"})
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()

        self.assertEqual(get.call_count, 1)
        self.assertEqual(data["meta"]["external_api_calls"], 1)
        self.assertGreater(data["route"]["distance_miles"], 800)
        stops = data["fuel_stops"]
        self.assertGreaterEqual(len(stops), 2)  # > 500 miles needs at least a second fill-up

        # Enough fuel is bought for the whole trip at 10 mpg, and the totals add up.
        self.assertAlmostEqual(data["summary"]["total_gallons"], data["route"]["distance_miles"] / 10, delta=0.1)
        self.assertAlmostEqual(data["summary"]["total_fuel_cost"], sum(s["cost"] for s in stops), delta=0.05)

        # Never more than 500 miles between consecutive fill-ups.
        markers = [0.0] + [s["mile_marker"] for s in stops[1:]] + [data["route"]["distance_miles"]]
        self.assertTrue(all(b - a <= 500 for a, b in zip(markers, markers[1:])))

        kinds = [f["properties"]["kind"] for f in data["map"]["features"]]
        self.assertEqual(kinds[:3], ["route", "start", "finish"])
        self.assertEqual(kinds.count("fuel_stop"), len(stops))
        self.assertIn("/api/route/map/?start=Chicago", data["map_url"])

    def test_post_json_and_cache(self, get):
        body = {"start": "Chicago, IL", "finish": "Dallas, TX"}
        first = self.client.post("/api/route/", body, content_type="application/json").json()
        second = self.client.post("/api/route/", body, content_type="application/json").json()
        self.assertEqual(get.call_count, 1)
        self.assertFalse(first["meta"]["cache_hit"])
        self.assertTrue(second["meta"]["cache_hit"])
        self.assertEqual(second["meta"]["external_api_calls"], 0)
        self.assertEqual(first["summary"], second["summary"])

    def test_map_page_reuses_cached_plan(self, get):
        self.client.get("/api/route/", {"start": "Chicago, IL", "finish": "Dallas, TX"})
        resp = self.client.get("/api/route/map/", {"start": "Chicago, IL", "finish": "Dallas, TX"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "leaflet")
        self.assertContains(resp, "Total fuel cost")
        self.assertEqual(get.call_count, 1)

    def test_missing_params(self, get):
        resp = self.client.get("/api/route/", {"start": "Chicago, IL"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("finish", resp.json()["error"])
        get.assert_not_called()

    def test_location_outside_usa(self, get):
        resp = self.client.get("/api/route/", {"start": "43.6532,-79.3832", "finish": "Dallas, TX"})
        self.assertEqual(resp.status_code, 400)

    def test_bad_json(self, get):
        resp = self.client.post("/api/route/", "not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_health(self, get):
        self.assertGreater(self.client.get("/api/health/").json()["fuel_stations_loaded"], 6000)


class RoutingErrorTests(DataTestCase):
    @mock.patch("routeplanner.services.routing.requests.get")
    def test_no_route(self, get):
        get.return_value = mock.Mock(status_code=400)
        get.return_value.json.return_value = {"code": "NoRoute", "message": "Impossible route"}
        resp = self.client.get("/api/route/", {"start": "Chicago, IL", "finish": "Honolulu, HI"})
        self.assertEqual(resp.status_code, 422)

    @mock.patch("routeplanner.services.routing.requests.get")
    def test_upstream_down(self, get):
        import requests

        get.side_effect = requests.ConnectionError("boom")
        resp = self.client.get("/api/route/", {"start": "Chicago, IL", "finish": "Dallas, TX"})
        self.assertEqual(resp.status_code, 502)
