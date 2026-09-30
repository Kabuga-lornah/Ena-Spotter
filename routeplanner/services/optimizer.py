"""Choose where to buy fuel, and how much, so the trip costs as little as possible.

Exact dynamic programme for the fixed-capacity refuelling problem
(Khuller, Malekian & Mestre, "To fill or not to fill", 2007). With linear fuel
prices there is always an optimal plan in which, at every stop, the driver
either fills the tank or buys just enough to reach the next stop empty. So the
fuel on arrival at a station is either 0 or "full tank at an earlier station k,
minus the miles driven since k", which gives O(stations within range) states per
station instead of a continuous fuel level.

Objective: fuel cost + `stop_penalty` per stop. A small penalty (a few dollars)
stops the plan from making a detour for a 1-gallon top-up that saves cents; with
a penalty of 0 the plan is the cheapest possible, ties broken by fewer stops.

Assumption: the vehicle starts with an empty tank. It fills up at the cheapest
station near the start (within `start_search_miles` along the route, or the
first station on the route if none is that close), and the miles driven to
reach that station are billed at its price.
"""

from dataclasses import dataclass

from .errors import NoFuelPlan
from .stations import RouteStation

EPS = 1e-9
EMPTY = -1  # state key: arrived with an empty tank


@dataclass
class FuelStop:
    route_station: RouteStation
    miles_of_fuel: float
    gallons: float
    cost: float


def plan_fuel_stops(
    candidates: list[RouteStation],
    total_miles: float,
    range_miles: float,
    mpg: float,
    start_search_miles: float,
    stop_penalty: float = 0.0,
) -> list[FuelStop]:
    if total_miles <= 0:
        return []
    candidates = sorted((c for c in candidates if c.mile < total_miles), key=lambda c: (c.mile, c.price))
    if not candidates:
        raise NoFuelPlan("No fuel stations from the price list were found along this route.")

    near_start = [c for c in candidates if c.mile <= start_search_miles] or [candidates[0]]
    first = min(near_start, key=lambda c: (c.price, c.mile))
    if first.mile > range_miles:
        raise NoFuelPlan(f"The first fuel station on this route is {first.mile:.0f} miles from the start.")

    # The start fill-up is modelled as a station at mile 0 with the first station's price.
    # After it, only the cheapest station at each mile marker can ever be worth using.
    nodes: list[tuple[float, RouteStation]] = [(0.0, first)]
    for c in candidates:
        if c.mile > first.mile and c.mile > nodes[-1][0]:
            nodes.append((c.mile, c))
    _check_gaps(nodes, total_miles, range_miles)

    pos = [p for p, _ in nodes]
    price = [rs.price / mpg for _, rs in nodes]  # dollars per mile of fuel
    n = len(nodes)
    R = range_miles

    # best[i][key] = (objective, stops, back-pointer); key is EMPTY or the index k of the
    # station where the tank was last filled (arrival fuel = R - (pos[i] - pos[k])).
    best: list[dict] = [dict() for _ in range(n)]
    best[0][EMPTY] = (0.0, 0, None)
    finish = None

    def relax(j, key, obj, stops, back):
        cur = best[j].get(key)
        if cur is None or _better(obj, stops, cur[0], cur[1]):
            best[j][key] = (obj, stops, back)

    for i in range(n):
        for key, (obj, stops, _) in best[i].items():
            fuel = 0.0 if key == EMPTY else R - (pos[i] - pos[key])
            here = (i, key)

            to_end = total_miles - pos[i]
            if to_end <= R + EPS:
                buy = max(0.0, to_end - fuel)
                end_obj = obj + buy * price[i] + (stop_penalty if buy > EPS else 0)
                end_stops = stops + (buy > EPS)
                if finish is None or _better(end_obj, end_stops, finish[0], finish[1]):
                    finish = (end_obj, end_stops, here, buy)

            fill = R - fuel
            for j in range(i + 1, n):
                d = pos[j] - pos[i]
                if d > R + EPS:
                    break
                if d > fuel + EPS:  # buy just enough to arrive at j empty
                    buy = d - fuel
                    relax(j, EMPTY, obj + buy * price[i] + stop_penalty, stops + 1, (here, buy))
                if fill > EPS:  # fill up here and drive straight to j
                    relax(j, i, obj + fill * price[i] + stop_penalty, stops + 1, (here, fill))

    if finish is None:  # unreachable given _check_gaps, kept as a safeguard
        raise NoFuelPlan("The trip cannot be completed with this price list.")

    purchases: list[tuple[int, float]] = []
    _, _, (i, key), buy = finish
    if buy > EPS:
        purchases.append((i, buy))
    back = best[i][key][2]
    while back is not None:
        (i, key), buy = back
        purchases.append((i, buy))
        back = best[i][key][2]
    purchases.reverse()

    return [
        FuelStop(
            route_station=nodes[i][1],
            miles_of_fuel=miles,
            gallons=miles / mpg,
            cost=miles / mpg * nodes[i][1].price,
        )
        for i, miles in purchases
    ]


def _better(obj, stops, other_obj, other_stops):
    """Lower objective wins; (near-)equal objectives are decided by fewer stops."""
    if abs(obj - other_obj) <= EPS:
        return stops < other_stops
    return obj < other_obj


def _check_gaps(nodes, total_miles, range_miles):
    stops = [p for p, _ in nodes] + [total_miles]
    for a, b in zip(stops, stops[1:]):
        if b - a > range_miles + EPS:
            raise NoFuelPlan(
                f"No fuel station within {range_miles:.0f} miles after mile {a:.0f}; "
                "the trip cannot be completed with this price list."
            )
