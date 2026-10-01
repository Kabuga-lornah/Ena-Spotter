"""Driving route from the free OSRM routing API: one HTTP call per route.

OSRM (Open Source Routing Machine) uses OpenStreetMap road data and needs no API key.
This is the only file that knows about OSRM: to switch to another provider (Google,
Mapbox, a self-hosted OSRM...), only this file has to change, as long as it still
returns a `Route`.
"""

from dataclasses import dataclass

import requests
from django.conf import settings

from .errors import RouteNotFound, UpstreamError
from .geo import METERS_PER_MILE


@dataclass
class Route:
    """What the rest of the app needs to know about a route."""

    distance_miles: float
    duration_hours: float
    coordinates: list[tuple[float, float]]  # [(lon, lat), ...]


def get_route(start_lat, start_lon, end_lat, end_lon) -> Route:
    """Ask OSRM for the driving route between two points."""
    # OSRM wants "lon,lat;lon,lat" (longitude first) in the URL path.
    url = f"{settings.OSRM_BASE_URL}/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}"
    try:
        resp = requests.get(
            url,
            params={
                # overview=full: the complete road shape (not a simplified one), so stations can
                # be matched accurately. geometries=geojson: coordinates as plain [lon, lat] lists.
                # alternatives=false / steps=false: one route, no turn-by-turn directions needed.
                "overview": "full", "geometries": "geojson", "alternatives": "false", "steps": "false",
            },
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=settings.HTTP_TIMEOUT_SECONDS,  # give up rather than hang forever
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        # Couldn't connect, timed out, or the reply wasn't JSON -> 502 "upstream error".
        raise UpstreamError(f"Routing service unavailable: {exc}") from exc

    # OSRM answers {"code": "Ok", "routes": [...]} on success.
    if data.get("code") != "Ok" or not data.get("routes"):
        if resp.status_code >= 500:  # OSRM itself is broken
            raise UpstreamError(f"Routing service error: {data.get('message', resp.status_code)}")
        # e.g. no road connects the two points (Hawaii to Texas) -> 422.
        raise RouteNotFound(data.get("message") or "No driving route found between these locations.")

    route = data["routes"][0]
    return Route(
        distance_miles=route["distance"] / METERS_PER_MILE,  # OSRM gives metres
        duration_hours=route["duration"] / 3600,  # OSRM gives seconds
        coordinates=[(c[0], c[1]) for c in route["geometry"]["coordinates"]],
    )
