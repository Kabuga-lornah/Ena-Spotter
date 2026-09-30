import json
from urllib.parse import urlencode

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
    if request.method == "POST" and request.body:
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            raise PlannerError("Request body must be JSON: {\"start\": \"...\", \"finish\": \"...\"}")
        if not isinstance(body, dict):
            raise PlannerError("Request body must be a JSON object.")
        return str(body.get("start", "")), str(body.get("finish", ""))
    return request.GET.get("start", ""), request.GET.get("finish", "")


def _validate(start, finish):
    missing = [name for name, value in (("start", start), ("finish", finish)) if not value.strip()]
    if missing:
        raise PlannerError(f"Missing required parameter(s): {', '.join(missing)}. "
                           "Use a US 'City, ST', an address, or 'lat,lon'.")


@method_decorator(csrf_exempt, name="dispatch")
class RoutePlanView(View):
    """GET /api/route/?start=...&finish=...  or  POST /api/route/ {"start": ..., "finish": ...}"""

    def get(self, request):
        return self._plan(request)

    def post(self, request):
        return self._plan(request)

    def _plan(self, request):
        try:
            start, finish = _read_params(request)
            _validate(start, finish)
            result = plan_trip(start, finish)
        except PlannerError as exc:
            return JsonResponse({"error": exc.message}, status=exc.status)
        map_path = reverse("route-map") + "?" + urlencode({"start": start, "finish": finish})
        return JsonResponse({**result, "map_url": request.build_absolute_uri(map_path)})


class RouteMapView(View):
    """GET /api/route/map/?start=...&finish=...  -> interactive Leaflet map of the plan."""

    def get(self, request):
        start, finish = request.GET.get("start", ""), request.GET.get("finish", "")
        try:
            _validate(start, finish)
            plan = plan_trip(start, finish)
        except PlannerError as exc:
            return render(request, "routeplanner/map.html", {"error": exc.message}, status=exc.status)
        return render(request, "routeplanner/map.html", {"plan": plan})


def health(request):
    return JsonResponse({"status": "ok", "fuel_stations_loaded": FuelStation.objects.count()})
