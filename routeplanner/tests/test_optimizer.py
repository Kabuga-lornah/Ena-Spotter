import random
from functools import lru_cache

from django.test import SimpleTestCase

from routeplanner.services.errors import NoFuelPlan
from routeplanner.services.optimizer import plan_fuel_stops
from routeplanner.services.stations import RouteStation


def rs(mile, price, name=None):
    return RouteStation({"name": name or f"S{mile}", "price": price}, mile, 0.0)


def brute_force_cost(stations, total, capacity):
    """Exact minimum cost by dynamic programming over integer fuel levels (miles of range)."""
    nodes = sorted(stations, key=lambda s: s.mile) + [rs(total, 0.0, "DEST")]

    @lru_cache(maxsize=None)
    def best(i, fuel):
        if i == len(nodes) - 1:
            return 0.0
        gap = nodes[i + 1].mile - nodes[i].mile
        options = [
            (g - fuel) * nodes[i].price + best(i + 1, g - gap)
            for g in range(max(fuel, gap), capacity + 1)
        ]
        return min(options) if options else float("inf")

    return best(0, 0)


class OptimizerTests(SimpleTestCase):
    def plan(self, stations, total, capacity=500, mpg=1, start_search=0):
        return plan_fuel_stops(stations, total, capacity, mpg, start_search)

    def test_short_trip_single_fill_at_start(self):
        stops = self.plan([rs(0, 3.0), rs(100, 2.0)], total=80, capacity=500)
        # Station at mile 100 is past the destination's needs; buy only what is needed at the start.
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(stops[0].miles_of_fuel, 80)

    def test_buys_just_enough_to_reach_cheaper_station(self):
        stops = self.plan([rs(0, 4.0), rs(100, 3.0)], total=300, capacity=500)
        self.assertEqual([s.route_station.station["name"] for s in stops], ["S0", "S100"])
        self.assertAlmostEqual(stops[0].miles_of_fuel, 100)
        self.assertAlmostEqual(stops[1].miles_of_fuel, 200)

    def test_fills_up_when_start_is_cheapest(self):
        stops = self.plan([rs(0, 2.0), rs(400, 5.0), rs(700, 4.0)], total=900, capacity=500)
        self.assertAlmostEqual(stops[0].miles_of_fuel, 500)
        self.assertAlmostEqual(sum(s.miles_of_fuel for s in stops), 900)

    def test_mpg_converts_to_gallons_and_cost(self):
        stops = plan_fuel_stops([rs(0, 3.5)], 250, 500, 10, 0)
        self.assertAlmostEqual(stops[0].gallons, 25)
        self.assertAlmostEqual(stops[0].cost, 87.5)

    def test_start_fill_uses_cheapest_station_near_start(self):
        stops = self.plan([rs(3, 4.0, "pricey"), rs(20, 3.0, "cheap"), rs(40, 1.0, "far")],
                          total=100, capacity=500, start_search=25)
        self.assertEqual(stops[0].route_station.station["name"], "cheap")
        self.assertEqual(stops[1].route_station.station["name"], "far")
        self.assertAlmostEqual(stops[0].miles_of_fuel, 40)

    def test_same_price_top_ups_are_merged(self):
        # Equal cost either way: prefer the plan with fewer stops.
        stops = self.plan([rs(0, 3.0), rs(10, 3.0), rs(400, 3.0), rs(450, 3.0)], total=900, capacity=500)
        self.assertEqual(len(stops), 2)

    def test_stop_penalty_skips_tiny_savings(self):
        stations = [rs(0, 3.00), rs(100, 2.99)]
        self.assertEqual(len(plan_fuel_stops(stations, 300, 500, 1, 0, stop_penalty=0)), 2)
        # Saving $0.01 x 200 = $2.00 is not worth a $5 stop.
        stops = plan_fuel_stops(stations, 300, 500, 1, 0, stop_penalty=5)
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(stops[0].miles_of_fuel, 300)

    def test_gap_longer_than_range_raises(self):
        with self.assertRaises(NoFuelPlan):
            self.plan([rs(0, 3.0), rs(400, 3.0)], total=1200, capacity=500)

    def test_no_stations_raises(self):
        with self.assertRaises(NoFuelPlan):
            self.plan([], total=100)

    def test_matches_brute_force_on_random_trips(self):
        rng = random.Random(42)
        for _ in range(500):
            capacity = rng.randint(20, 60)
            total = rng.randint(30, 250)
            miles = sorted(rng.sample(range(1, total), k=min(total - 1, rng.randint(3, 25))))
            stations = [rs(0, round(rng.uniform(2.5, 5), 2))] + [rs(m, round(rng.uniform(2.5, 5), 2)) for m in miles]
            expected = brute_force_cost(stations, total, capacity)
            if expected == float("inf"):
                with self.assertRaises(NoFuelPlan):
                    self.plan(stations, total, capacity)
                continue
            stops = self.plan(stations, total, capacity)
            self.assertAlmostEqual(sum(s.cost for s in stops), expected, places=6)
            self.assertAlmostEqual(sum(s.miles_of_fuel for s in stops), total, places=6)
