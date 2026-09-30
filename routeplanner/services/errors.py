class PlannerError(Exception):
    """An error with an HTTP status and a message that is safe to show API clients."""

    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidLocation(PlannerError):
    status = 400


class RouteNotFound(PlannerError):
    status = 422


class NoFuelPlan(PlannerError):
    status = 422


class UpstreamError(PlannerError):
    status = 502
