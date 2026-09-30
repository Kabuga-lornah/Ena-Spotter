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
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            raise PlannerError("Request body must be JSON: {\"start\": \"...\", \"finish\": \"...\"}")
        if not isinstance(body, dict):
            raise PlannerError("Request body must be a JSON object.")
        params = body
    else:
        params = request.GET
    start, finish = str(params.get("start", "")), str(params.get("finish", ""))
    _validate(start, finish)
    options = {
        "start_fuel_gallons": _number(params.get("start_fuel_gallons"), "start_fuel_gallons",
                                      settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG),
        "stop_penalty_usd": _number(params.get("stop_penalty_usd"), "stop_penalty_usd", 100),
    }
    return start, finish, {k: v for k, v in options.items() if v is not None}


def _validate(start, finish):
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
        number = -1
    if not 0 <= number <= maximum:
        raise PlannerError(f"{name} must be a number between 0 and {maximum:g}.")
    return number


def _query_string(start, finish, options):
    return urlencode({"start": start, "finish": finish, **{k: f"{v:g}" for k, v in options.items()}})


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
            return JsonResponse({"error": exc.message}, status=exc.status)
        map_path = reverse("route-map") + "?" + _query_string(start, finish, options)
        return JsonResponse({**result, "map_url": request.build_absolute_uri(map_path)})


class RouteMapView(View):
    """GET /api/route/map/?start=...&finish=...  -> interactive Leaflet map of the plan."""

    def get(self, request):
        try:
            start, finish, options = _read_params(request)
            plan = plan_trip(start, finish, **options)
        except PlannerError as exc:
            return render(request, "routeplanner/map.html", {"error": exc.message}, status=exc.status)
        return render(request, "routeplanner/map.html", {"plan": plan})


def health(request):
    return JsonResponse({"status": "ok", "fuel_stations_loaded": FuelStation.objects.count()})
