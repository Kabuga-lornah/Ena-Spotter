"""Errors the planner can raise, each tied to an HTTP status code.

The view catches any PlannerError and returns {"error": message} with its `status`,
so every failure reaches the client as a clear message instead of a crash.
"""


class PlannerError(Exception):
    """An error with an HTTP status and a message that is safe to show API clients."""

    status = 400  # 400 Bad Request: something wrong with the input

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidLocation(PlannerError):
    """The start or finish couldn't be found, or isn't in the USA."""

    status = 400


class RouteNotFound(PlannerError):
    """OSRM found no driving route between the two points."""

    status = 422  # 422 Unprocessable: valid input, but the request can't be fulfilled


class NoFuelPlan(PlannerError):
    """No valid fuel plan exists (e.g. two stations more than 500 miles apart)."""

    status = 422


class UpstreamError(PlannerError):
    """An external service (OSRM or Nominatim) is down or returned garbage."""

    status = 502  # 502 Bad Gateway: a service we depend on failed
