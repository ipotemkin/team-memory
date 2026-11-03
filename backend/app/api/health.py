"""Health endpoints."""

from typing import Any

from fastapi import APIRouter, Request

from app.services.health import HealthService

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    svc: HealthService = request.app.state.health_service
    return await svc.check()
