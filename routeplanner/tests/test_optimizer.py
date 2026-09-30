"""Tests for the fuel optimiser (services/optimizer.py).

Small hand-made trips check specific behaviours (full tank, empty tank, destination
handling, impossible trips). The last test is the strongest: it builds 1,000 random
trips and checks that the optimiser's cost equals the true minimum found by a slow
brute-force search over every possible fuel level.

In these tests mpg=1, so "miles of fuel" and "gallons" are the same number.
"""

import random
from functools import lru_cache

from django.test import SimpleTestCase

from routeplanner.services.errors import NoFuelPlan
from routeplanner.services.optimizer import FILL, TO_DESTINATION, TO_NEXT, plan_fuel_stops
from routeplanner.services.stations import RouteStation


def rs(mile, price, name=None):
    return RouteStation({"name": name or f"S{mile}", "price": price}, mile, 0.0)


def brute_force_cost(stations, total, capacity, start_fuel):
    """Exact minimum cost by dynamic programming over integer fuel levels (miles of range).

    Node 0 is the start (no fuel for sale), holding `start_fuel`; the last node is the destination.
    """
    nodes = [(0, None)] + sorted((s.mile, s.price) for s in stations) + [(total, None)]

    @lru_cache(maxsize=None)
    def best(i, fuel):
        if i == len(nodes) - 1:
            return 0.0
        gap = nodes[i + 1][0] - nodes[i][0]
        price = nodes[i][1]
        if price is None:  # can't buy here
            return best(i + 1, fuel - gap) if fuel >= gap else float("inf")
        options = [
            (g - fuel) * price + best(i + 1, g - gap)
            for g in range(max(fuel, gap), capacity + 1)
        ]
        return min(options) if options else float("inf")

    return best(0, start_fuel)


class OptimizerTests(SimpleTestCase):
    def plan(self, stations, total, capacity=500, mpg=1, start_fuel=0, start_search=0, stop_penalty=0):
        return plan_fuel_stops(stations, total, capacity, mpg, start_fuel, start_search, stop_penalty)

    # --- full tank at the start (the default in the API) ---

    def test_full_tank_short_trip_needs_no_stops(self):
        self.assertEqual(self.plan([rs(100, 3.0)], total=450, start_fuel=500), [])

    def test_full_tank_buys_only_what_is_needed_to_finish(self):
        # 700-mile trip: 500 in the tank, so exactly 200 more miles of fuel, at the cheaper station.
        stops = self.plan([rs(300, 3.5), rs(450, 3.0)], total=700, start_fuel=500)
        self.assertEqual([s.route_station.station["name"] for s in stops], ["S450"])
        self.assertAlmostEqual(stops[0].miles_of_fuel, 200)
        self.assertEqual(stops[0].action, TO_DESTINATION)
        self.assertAlmostEqual(stops[0].fuel_on_arrival_miles, 50)

    def test_destination_is_the_final_node(self):
        # From the review: A mile 300 $3.50, B mile 450 $3.00, destination mile 600.
        # Don't fill up at B; buy just enough there to finish.
        stops = self.plan([rs(300, 3.5), rs(450, 3.0)], total=600, start_fuel=320)
        self.assertEqual([s.route_station.station["name"] for s in stops], ["S300", "S450"])
        self.assertAlmostEqual(stops[0].miles_of_fuel, 150 - 20)  # just enough to reach B
        self.assertEqual(stops[0].action, TO_NEXT)
        self.assertAlmostEqual(stops[1].miles_of_fuel, 150)  # just enough to finish
        self.assertEqual(stops[1].action, TO_DESTINATION)

    def test_first_station_out_of_reach_of_starting_fuel(self):
        with self.assertRaises(NoFuelPlan):
            self.plan([rs(300, 3.0)], total=700, start_fuel=200)

    # --- empty tank at the start ---

    def test_empty_start_buys_just_enough_to_reach_cheaper_station(self):
        stops = self.plan([rs(0, 4.0), rs(100, 3.0)], total=300)
        self.assertEqual([s.route_station.station["name"] for s in stops], ["S0", "S100"])
        self.assertAlmostEqual(stops[0].miles_of_fuel, 100)
        self.assertAlmostEqual(stops[1].miles_of_fuel, 200)

    def test_empty_start_fills_up_when_start_is_cheapest(self):
        stops = self.plan([rs(0, 2.0), rs(400, 5.0), rs(700, 4.0)], total=900)
        self.assertAlmostEqual(stops[0].miles_of_fuel, 500)
        self.assertEqual(stops[0].action, FILL)
        self.assertAlmostEqual(sum(s.miles_of_fuel for s in stops), 900)

    def test_empty_start_uses_cheapest_station_near_start(self):
        stops = self.plan([rs(3, 4.0, "pricey"), rs(20, 3.0, "cheap"), rs(40, 1.0, "far")],
                          total=100, start_search=25)
        self.assertEqual(stops[0].route_station.station["name"], "cheap")
        self.assertEqual(stops[1].route_station.station["name"], "far")
        self.assertAlmostEqual(stops[0].miles_of_fuel, 40)

    def test_mpg_converts_to_gallons_and_cost(self):
        stops = plan_fuel_stops([rs(0, 3.5)], 250, 500, 10, 0, 0)
        self.assertAlmostEqual(stops[0].gallons, 25)
        self.assertAlmostEqual(stops[0].cost, 87.5)

    # --- objective ---

    def test_equal_cost_plans_prefer_fewer_stops(self):
        stops = self.plan([rs(0, 3.0), rs(10, 3.0), rs(400, 3.0), rs(450, 3.0)], total=900)
        self.assertEqual(len(stops), 2)

    def test_default_is_pure_fuel_cost(self):
        # A 1-cent saving is still taken by default (no stop penalty)...
        stations = [rs(0, 3.00), rs(100, 2.99)]
        self.assertEqual(len(self.plan(stations, total=300)), 2)
        # ...but an optional penalty can skip it: saving $2.00 isn't worth a $5 stop.
        stops = self.plan(stations, total=300, stop_penalty=5)
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(stops[0].miles_of_fuel, 300)

    # --- infeasible routes ---

    def test_gap_longer_than_range_raises(self):
        with self.assertRaises(NoFuelPlan):
            self.plan([rs(0, 3.0), rs(400, 3.0)], total=1200)
        with self.assertRaises(NoFuelPlan):
            self.plan([rs(100, 3.0), rs(620, 3.0)], total=900, start_fuel=500)

    def test_no_stations_raises(self):
        with self.assertRaises(NoFuelPlan):
            self.plan([], total=100)
        with self.assertRaises(NoFuelPlan):
            self.plan([], total=600, start_fuel=500)

    # --- exactness ---

    def test_matches_brute_force_on_random_trips(self):
        rng = random.Random(42)
        for _ in range(1000):
            capacity = rng.randint(20, 60)
            total = rng.randint(30, 250)
            start_fuel = rng.choice([0, capacity, rng.randint(1, capacity)])
            miles = sorted(rng.sample(range(1, total), k=min(total - 1, rng.randint(3, 25))))
            stations = [rs(m, round(rng.uniform(2.5, 5), 2)) for m in miles]
            if start_fuel == 0:
                stations.insert(0, rs(0, round(rng.uniform(2.5, 5), 2)))
            expected = brute_force_cost(stations, total, capacity, start_fuel)
            if expected == float("inf"):
                with self.assertRaises(NoFuelPlan):
                    self.plan(stations, total, capacity, start_fuel=start_fuel)
                continue
            stops = self.plan(stations, total, capacity, start_fuel=start_fuel)
            self.assertAlmostEqual(sum(s.cost for s in stops), expected, places=6)
            # Fuel bought + starting fuel always covers the trip, and the tank never overflows.
            self.assertGreaterEqual(sum(s.miles_of_fuel for s in stops) + start_fuel, total - 1e-6)
            for s in stops:
                self.assertLessEqual(s.fuel_on_arrival_miles + s.miles_of_fuel, capacity + 1e-6)
