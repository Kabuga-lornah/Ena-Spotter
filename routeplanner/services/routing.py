"""Driving route from the free OSRM routing API: one HTTP call per route."""

from dataclasses import dataclass

import requests
from django.conf import settings

from .errors import RouteNotFound, UpstreamError
from .geo import METERS_PER_MILE


@dataclass
class Route:
    distance_miles: float
    duration_hours: float
    coordinates: list[tuple[float, float]]  # [(lon, lat), ...]


def get_route(start_lat, start_lon, end_lat, end_lon) -> Route:
    url = f"{settings.OSRM_BASE_URL}/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}"
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "alternatives": "false", "steps": "false"},
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise UpstreamError(f"Routing service unavailable: {exc}") from exc

    if data.get("code") != "Ok" or not data.get("routes"):
        if resp.status_code >= 500:
            raise UpstreamError(f"Routing service error: {data.get('message', resp.status_code)}")
        raise RouteNotFound(data.get("message") or "No driving route found between these locations.")

    route = data["routes"][0]
    return Route(
        distance_miles=route["distance"] / METERS_PER_MILE,
        duration_hours=route["duration"] / 3600,
        coordinates=[(c[0], c[1]) for c in route["geometry"]["coordinates"]],
    )
