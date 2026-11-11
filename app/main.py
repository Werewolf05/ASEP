from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.config import load_settings
from app.services.container import AppServices

settings = load_settings()
app = FastAPI(
    title=settings.app_name.upper(),
    version="0.1.0",
    description="Autonomous Software Engineer Platform foundation for issue intake, repository inspection, and implementation planning.",
)
app.state.services = AppServices.create(settings)
app.include_router(router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "running"}
