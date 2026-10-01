"""Choose where to buy fuel, and how much, so the trip costs as little as possible.

THE PROBLEM
    Stations lie along the route at known mile markers, each with a price. The tank
    holds 500 miles of fuel. Where should the driver stop, and how much should they
    buy at each stop, so the total spent is as small as possible?

THE IDEA
    Exact dynamic programme for the fixed-capacity refuelling problem
    (Khuller, Malekian & Mestre, "To fill or not to fill", 2007). With linear fuel
    prices there is always an optimal plan in which, at every stop, the driver
    either fills the tank or buys just enough to reach the next stop empty. So the
    fuel on arrival at a station is one of:

      * 0 (the previous stop bought just enough),
      * "full tank at an earlier station k, minus the miles driven since k", or
      * "the starting fuel, minus the miles driven since the start".

    That gives O(stations within range) states per station instead of a continuous
    fuel level. The destination is the final node: the last stop only buys enough
    to reach it.

    "Dynamic programme" here means: walk the stations from first to last, and for
    each (station, fuel-on-arrival) pair remember the cheapest way found so far of
    getting there. The cheapest way to reach the destination is then read off at
    the end by following "came from" pointers backwards.

UNITS
    Fuel is measured in "miles of range" inside this module (a full tank = 500).
    Gallons = miles / mpg, and cost = gallons x price per gallon.

Objective: fuel cost, with (near-)equal costs decided by fewer stops. An
optional `stop_penalty` (dollars per stop, default 0) can be used to skip stops
that only save cents.

Starting fuel: by default the vehicle starts with a full tank. With
`start_fuel_miles=0` it starts empty and fills up at the cheapest station near
the start (within `start_search_miles` along the route, or the first station on
the route if none is that close); the miles driven to reach that station are
billed at its price.
"""

from dataclasses import dataclass

from .errors import NoFuelPlan
from .stations import RouteStation

# Tiny tolerance for floating-point comparisons (0.1 + 0.2 isn't exactly 0.3 in Python).
EPS = 1e-9
# "State keys" describe how much fuel the vehicle has when it arrives at a station:
EMPTY = -1  # state key: arrived with an empty tank
FROM_START = -2  # state key: still running on the fuel the vehicle started with
# (any key >= 0 is the index of the station where the tank was last filled up)

# What the driver does at a stop (reported in the API as `action`):
FILL = "fill"  # fill the tank completely
TO_NEXT = "to_next_stop"  # buy just enough to reach the next stop
TO_DESTINATION = "to_destination"  # buy just enough to finish the trip


@dataclass
class FuelStop:
    """One stop in the final plan."""

    route_station: RouteStation
    miles_of_fuel: float  # how much fuel is bought, in miles of range
    gallons: float
    cost: float
    action: str  # FILL, TO_NEXT or TO_DESTINATION
    fuel_on_arrival_miles: float  # fuel left in the tank when arriving here


def plan_fuel_stops(
    candidates: list[RouteStation],
    total_miles: float,
    range_miles: float,
    mpg: float,
    start_fuel_miles: float,
    start_search_miles: float,
    stop_penalty: float = 0.0,
) -> list[FuelStop]:
    """Return the cheapest list of fuel stops for the trip (empty if no stop is needed).

    Raises NoFuelPlan if the trip is impossible (a gap between stations > range).
    """
    # --- Easy cases 
    if total_miles <= 0:
        return []
    start_fuel_miles = min(max(start_fuel_miles, 0.0), range_miles)  # clamp to 0..tank size
    if total_miles <= start_fuel_miles + EPS:
        return []  # the starting fuel covers the whole trip

    # --- Build the list of "nodes" (points along the route) 
    # Stations past the destination are useless; sort the rest by mile, cheapest first.
    candidates = sorted((c for c in candidates if c.mile < total_miles), key=lambda c: (c.mile, c.price))
    # Only the cheapest station at each mile marker can ever be worth using.
    stations: list[tuple[float, RouteStation | None]] = []
    for c in candidates:
        if not stations or c.mile > stations[-1][0]:
            stations.append((c.mile, c))

    if start_fuel_miles > EPS:
        # A non-buying start node at mile 0 holding the starting fuel.
        nodes = [(0.0, None)] + stations
        _check_gaps(nodes, total_miles, range_miles, start_fuel_miles)
        initial_key = FROM_START
    else:
        # Starting empty: the vehicle must fill up somewhere near the start.
        if not stations:
            raise NoFuelPlan("No fuel stations from the price list were found along this route.")
        near_start = [rs for m, rs in stations if m <= start_search_miles] or [stations[0][1]]
        first = min(near_start, key=lambda c: (c.price, c.mile))  # cheapest one near the start
        if first.mile > range_miles:
            raise NoFuelPlan(f"The first fuel station on this route is {first.mile:.0f} miles from the start.")
        # The start fill-up is modelled as a station at mile 0 with the first station's price.
        nodes = [(0.0, first)] + [(m, rs) for m, rs in stations if m > first.mile]
        _check_gaps(nodes, total_miles, range_miles, range_miles)
        initial_key = EMPTY

    pos = [p for p, _ in nodes]  # mile marker of each node
    price = [None if rs is None else rs.price / mpg for _, rs in nodes]  # dollars per mile of fuel
    n = len(nodes)
    R = range_miles  # tank size, in miles

    def fuel_at(i, key):
        """Fuel in the tank (miles) on arriving at node i, for a given state key."""
        if key == EMPTY:
            return 0.0
        if key == FROM_START:
            return start_fuel_miles - pos[i]  # starting fuel minus the miles driven so far
        return R - (pos[i] - pos[key])  # full tank at node `key` minus the miles since then

    # --- The dynamic programme 
    # best[i][key] = (objective, stops, back-pointer); back = (prev_node, prev_key, bought, action).
    #   objective = dollars spent so far (+ stop penalties), stops = number of stops so far,
    #   back-pointer = how we got here (used at the end to rebuild the plan).
    best: list[dict] = [dict() for _ in range(n)]
    best[0][initial_key] = (0.0, 0, None)  # at the start: $0 spent, 0 stops
    finish = None  # (objective, stops, node, key, bought)  best way found to reach the destination

    def relax(j, key, obj, stops, back):
        """Record a way of reaching node j with state `key` if it beats what we already have."""
        cur = best[j].get(key)
        if cur is None or _better(obj, stops, cur[0], cur[1]):
            best[j][key] = (obj, stops, back)

    # Nodes are in route order, so when we reach node i every way of getting to it is known.
    for i in range(n):
        for key, (obj, stops, _) in best[i].items():
            fuel = fuel_at(i, key)
            to_end = total_miles - pos[i]  # miles left to the destination

            if price[i] is None:  # the start node: can't buy, just drive on the starting fuel
                if to_end <= fuel + EPS and (finish is None or _better(obj, stops, finish[0], finish[1])):
                    finish = (obj, stops, i, key, 0.0)
                # Every station reachable on the starting fuel is a possible first stop.
                for j in range(i + 1, n):
                    if pos[j] - pos[i] > fuel + EPS:
                        break
                    relax(j, FROM_START, obj, stops, (i, key, 0.0, None))
                continue

            # Option A: the destination is within one tank from here -> buy just enough to finish.
            if to_end <= R + EPS:
                buy = max(0.0, to_end - fuel)
                end_obj = obj + buy * price[i] + (stop_penalty if buy > EPS else 0)
                end_stops = stops + (buy > EPS)  # True counts as 1: only a real purchase is a stop
                if finish is None or _better(end_obj, end_stops, finish[0], finish[1]):
                    finish = (end_obj, end_stops, i, key, buy)

            # Options B and C: drive on to some later station j within one tank's range.
            fill = R - fuel  # how much it costs (in miles of fuel) to fill the tank here
            for j in range(i + 1, n):
                d = pos[j] - pos[i]  # miles from here to station j
                if d > R + EPS:
                    break  # j (and every later station) is out of range
                # Option B: buy just enough to arrive at j empty.
                if d > fuel + EPS:  # buy just enough to arrive at j empty
                    buy = d - fuel
                    relax(j, EMPTY, obj + buy * price[i] + stop_penalty, stops + 1, (i, key, buy, TO_NEXT))
                # Option C: fill the tank here and drive straight to j.
                if fill > EPS:  # fill up here and drive straight to j
                    relax(j, i, obj + fill * price[i] + stop_penalty, stops + 1, (i, key, fill, FILL))

    if finish is None:  # unreachable given _check_gaps, kept as a safeguard
        raise NoFuelPlan("The trip cannot be completed with this price list.")

    # --- Rebuild the plan by following the back-pointers from the destination -------
    purchases: list[tuple[int, int, float, str]] = []  # (node, key on arrival, bought, action)
    _, _, i, key, buy = finish
    if buy > EPS:
        purchases.append((i, key, buy, TO_DESTINATION))
    back = best[i][key][2]
    while back is not None:  # walk backwards until we reach the start (which has no back-pointer)
        i, key, buy, action = back
        if buy > EPS:  # passing through the start node buys nothing, so it isn't a stop
            purchases.append((i, key, buy, action))
        back = best[i][key][2]
    purchases.reverse()  # we collected them destination -> start; flip to start -> destination

    return [
        FuelStop(
            route_station=nodes[i][1],
            miles_of_fuel=miles,
            gallons=miles / mpg,
            cost=miles / mpg * nodes[i][1].price,
            action=action,
            fuel_on_arrival_miles=max(0.0, fuel_at(i, key)),
        )
        for i, key, miles, action in purchases
    ]


def _better(obj, stops, other_obj, other_stops):
    """Lower objective wins; (near-)equal objectives are decided by fewer stops."""
    if abs(obj - other_obj) <= 1e-6:
        return stops < other_stops
    return obj < other_obj


def _check_gaps(nodes, total_miles, range_miles, first_leg_miles):
    """Fail fast, with a useful message, if some stretch of the route is longer than the range.

    E.g. two stations 520 miles apart can never be bridged by a 500-mile tank, so no
    valid plan exists. The API turns this error into a 422 response.
    """
    points = [p for p, _ in nodes] + [total_miles]  # every node, then the destination
    for idx, (a, b) in enumerate(zip(points, points[1:])):
        # The first stretch is limited by the starting fuel; every later one by a full tank.
        limit = first_leg_miles if idx == 0 else range_miles
        if b - a > limit + EPS:
            where = "the start" if a == 0 else f"mile {a:.0f}"
            raise NoFuelPlan(
                f"No fuel station within {limit:.0f} miles after {where}; "
                "no feasible fuel plan exists for this route with this price list."
            )
