from __future__ import annotations

from fastapi import Request

from app.services.container import AppServices


def get_services(request: Request) -> AppServices:
    services = getattr(request.app.state, "services", None)
    if not isinstance(services, AppServices):
        raise TypeError("Application services have not been initialized.")
    return services
