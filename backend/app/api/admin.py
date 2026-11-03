"""Admin API (/admin): embedding-model registry, blue/green reindex, health. Header X-Admin-Token.

Disabled (404) when ADMIN_TOKEN is empty.
"""

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.domain.models import EMBEDDING_DIM_MAX, AuditRecord, EmbeddingModelInfo
from app.services.admin import AdminService
from app.services.health import HealthService


def require_admin(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    expected: str = request.app.state.container.settings.ADMIN_TOKEN
    if not expected:
        raise ApiError(404, "not_found", "admin API is disabled (ADMIN_TOKEN is not set)")
    if not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), expected.encode()):
        raise ApiError(401, "unauthorized", "invalid or missing X-Admin-Token")


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _svc(request: Request) -> AdminService:
    try:
        svc: AdminService = request.app.state.container.admin_service()
    except RuntimeError as e:
        raise ApiError(503, "unavailable", str(e)) from e
    return svc


async def _audit(request: Request, action: str, inp: dict[str, Any], out: dict[str, Any]) -> None:
    """Successful admin commands leave an audit row (errors are recorded by AuditMiddleware)."""
    await request.app.state.container.audit_service().record(
        AuditRecord(action=action, input=inp, output=out, status="ok", duration_ms=0)
    )
    request.state.audited = True


class RegisterModel(BaseModel):
    provider: str = Field(min_length=1, max_length=50, examples=["ollama"])
    name: str = Field(min_length=1, max_length=200, examples=["bge-m3"])
    dimension: int = Field(gt=0, le=EMBEDDING_DIM_MAX, examples=[1024])


class ModelOut(BaseModel):
    name: str
    provider: str
    dimension: int
    status: str
    progress_done: int
    progress_total: int
    progress: float
    created_at: str
    activated_at: str | None
    retired_at: str | None
    error: str | None

    @classmethod
    def of(cls, i: EmbeddingModelInfo) -> "ModelOut":
        return cls(
            name=i.name,
            provider=i.provider,
            dimension=i.dim,
            status=i.status,
            progress_done=i.progress_done,
            progress_total=i.progress_total,
            progress=round(i.progress_done / i.progress_total, 4) if i.progress_total else 1.0,
            created_at=i.created_at.isoformat(),
            activated_at=i.activated_at.isoformat() if i.activated_at else None,
            retired_at=i.retired_at.isoformat() if i.retired_at else None,
            error=i.error,
        )


@router.get("/health")
async def admin_health(request: Request) -> dict[str, Any]:
    svc: HealthService = request.app.state.admin_health_service
    return await svc.check()


@router.get("/embedding-models")
async def list_models(request: Request) -> list[ModelOut]:
    return [ModelOut.of(i) for i in await _svc(request).list_models()]


@router.get("/embedding-models/{name:path}/status")
async def model_status(name: str, request: Request) -> ModelOut:
    return ModelOut.of(await _svc(request).get(name))


@router.post("/embedding-models", status_code=201)
async def register_model(body: RegisterModel, request: Request) -> ModelOut:
    out = ModelOut.of(await _svc(request).register(body.name, body.provider, body.dimension))
    await _audit(request, "admin.register_model", body.model_dump(), out.model_dump())
    return out


@router.post("/embedding-models/{name:path}/reindex", status_code=202)
async def start_reindex(name: str, request: Request) -> dict[str, Any]:
    svc = _svc(request)
    task_id = await svc.start_reindex(name)
    out = {"task_id": task_id, "model": ModelOut.of(await svc.get(name)).model_dump()}
    await _audit(request, "admin.reindex", {"name": name}, {"task_id": task_id})
    return out


@router.post("/embedding-models/{name:path}/activate")
async def activate_model(name: str, request: Request) -> ModelOut:
    out = ModelOut.of(await _svc(request).activate(name))
    await _audit(request, "admin.activate", {"name": name}, out.model_dump())
    return out


@router.delete("/embedding-models/{name:path}")
async def delete_model(name: str, request: Request) -> dict[str, Any]:
    n = await _svc(request).delete(name)
    out = {"deleted": name, "embeddings_deleted": n}
    await _audit(request, "admin.delete_model", {"name": name}, out)
    return out
