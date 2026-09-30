"""The web layer: turns HTTP requests into calls to the planner, and results into responses.

The views are deliberately thin: they read and validate the inputs, call
`plan_trip()`, and turn the result (or a PlannerError) into JSON or HTML.
All the real work lives in routeplanner/services/.
"""

import json
from urllib.parse import urlencode

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .models import FuelStation
from .services.errors import PlannerError
from .services.planner import plan_trip


def _read_params(request):
    """Return (start, finish, options) from a query string or JSON body."""
    if request.method == "POST" and request.body:
        # POST: the inputs come as JSON in the request body.
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            raise PlannerError("Request body must be JSON: {\"start\": \"...\", \"finish\": \"...\"}")
        if not isinstance(body, dict):
            raise PlannerError("Request body must be a JSON object.")
        params = body
    else:
        # GET: the inputs come in the URL, e.g. /api/route/?start=...&finish=...
        params = request.GET
    start, finish = str(params.get("start", "")), str(params.get("finish", ""))
    _validate(start, finish)
    # Optional settings; anything not given is left out, so the defaults in settings.py apply.
    options = {
        "start_fuel_gallons": _number(params.get("start_fuel_gallons"), "start_fuel_gallons",
                                      settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG),
        "stop_penalty_usd": _number(params.get("stop_penalty_usd"), "stop_penalty_usd", 100),
    }
    return start, finish, {k: v for k, v in options.items() if v is not None}


def _validate(start, finish):
    """Both locations are required; say exactly which one is missing."""
    missing = [name for name, value in (("start", start), ("finish", finish)) if not value.strip()]
    if missing:
        raise PlannerError(f"Missing required parameter(s): {', '.join(missing)}. "
                           "Use a US 'City, ST', an address, or 'lat,lon'.")


def _number(value, name, maximum):
    """Optional numeric parameter in [0, maximum]; None when absent."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = -1  # not a number: force the range check below to fail
    if not 0 <= number <= maximum:
        raise PlannerError(f"{name} must be a number between 0 and {maximum:g}.")
    return number


def _query_string(start, finish, options):
    """Rebuild the URL query (e.g. for map_url) so the map shows exactly the same plan."""
    return urlencode({"start": start, "finish": finish, **{k: f"{v:g}" for k, v in options.items()}})


# csrf_exempt: Django normally demands a CSRF token on POST (a protection for browser
# forms). This is a JSON API called from tools like Postman, so we switch it off here.
@method_decorator(csrf_exempt, name="dispatch")
class RoutePlanView(View):
    """GET /api/route/?start=...&finish=...[&start_fuel_gallons=...][&stop_penalty_usd=...]
    or POST /api/route/ with the same fields as JSON."""

    def get(self, request):
        return self._plan(request)

    def post(self, request):
        return self._plan(request)

    def _plan(self, request):
        try:
            start, finish, options = _read_params(request)
            result = plan_trip(start, finish, **options)
        except PlannerError as exc:
            # Any planner error becomes {"error": "..."} with the right HTTP status (400/422/502).
            return JsonResponse({"error": exc.message}, status=exc.status)
        # Add a link to the HTML map of this same plan.
        map_path = reverse("route-map") + "?" + _query_string(start, finish, options)
        return JsonResponse({**result, "map_url": request.build_absolute_uri(map_path)})


class RouteMapView(View):
    """GET /api/route/map/?start=...&finish=...  -> interactive Leaflet map of the plan."""

    def get(self, request):
        try:
            start, finish, options = _read_params(request)
            # Usually a cache hit: the JSON request above already planned this trip.
            plan = plan_trip(start, finish, **options)
        except PlannerError as exc:
            return render(request, "routeplanner/map.html", {"error": exc.message}, status=exc.status)
        return render(request, "routeplanner/map.html", {"plan": plan})


def health(request):
    """GET /api/health/ -> quick check that the server is up and the stations are loaded."""
    return JsonResponse({"status": "ok", "fuel_stations_loaded": FuelStation.objects.count()})
