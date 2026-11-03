"""/admin API: token auth, embedding-model registry, blue/green reindex, health; request id, metrics."""

from collections.abc import Callable
from typing import Any

from tests.api.conftest import Api

H = {"X-Admin-Token": "s3cret"}


async def test_admin_disabled_without_token(make_api: Callable[..., Api]) -> None:
    api = make_api()
    r = await api.client.get("/admin/embedding-models", headers=H)
    assert r.status_code == 404 and r.json()["error"] == "not_found"


async def test_admin_rejects_bad_token(make_api: Callable[..., Api]) -> None:
    api = make_api(ADMIN_TOKEN="s3cret")
    assert (await api.client.get("/admin/health")).status_code == 401
    r = await api.client.get("/admin/health", headers={"X-Admin-Token": "nope"})
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"


async def test_admin_health(make_api: Callable[..., Api]) -> None:
    api = make_api(ADMIN_TOKEN="s3cret")
    r = await api.client.get("/admin/health", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["components"]["embedder"]["ok"] is True


async def test_admin_blue_green_flow(make_api: Callable[..., Api]) -> None:
    api = make_api(ADMIN_TOKEN="s3cret")
    c = api.client
    r = await c.post(
        "/kb/documents", json={"title": "Правила", "text": "Ежедневный стендап проходит в 11:00."}
    )
    assert r.status_code in (200, 201) and isinstance(r.json()["document_id"], str)

    models = (await c.get("/admin/embedding-models", headers=H)).json()
    assert [(m["name"], m["status"]) for m in models] == [("stub:hash-64", "active")]

    bad = await c.post(
        "/admin/embedding-models", headers=H, json={"provider": "stub", "name": "x", "dimension": 0}
    )
    assert bad.status_code == 422
    r = await c.post(
        "/admin/embedding-models",
        headers=H,
        json={"provider": "stub", "name": "stub:hash-16", "dimension": 16},
    )
    assert r.status_code == 201 and r.json()["status"] == "indexing" and r.json()["dimension"] == 16
    dup = await c.post(
        "/admin/embedding-models",
        headers=H,
        json={"provider": "stub", "name": "stub:hash-16", "dimension": 16},
    )
    assert dup.status_code == 409 and dup.json()["error"] == "conflict"

    r = await c.post("/admin/embedding-models/stub:hash-16/reindex", headers=H)
    assert r.status_code == 202 and r.json()["task_id"]
    st = (await c.get("/admin/embedding-models/stub:hash-16/status", headers=H)).json()
    assert st["status"] == "ready" and st["progress"] == 1.0 and st["progress_total"] >= 1

    r = await c.post("/admin/embedding-models/stub:hash-16/activate", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert (await c.delete("/admin/embedding-models/stub:hash-16", headers=H)).status_code == 409
    r = await c.delete("/admin/embedding-models/stub:hash-64", headers=H)
    assert r.status_code == 200 and r.json()["embeddings_deleted"] >= 1
    assert (await c.get("/admin/embedding-models/stub:hash-64/status", headers=H)).status_code == 404

    ask = await c.post("/kb/ask", json={"question": "Во сколько проходит стендап?"})
    assert ask.status_code == 200 and ask.json()["needs_review"] is False
    actions = [a["action"] for a in api.audit]
    assert {"admin.register_model", "admin.reindex", "admin.activate", "admin.delete_model"} <= set(actions)


async def test_request_id_header(api: Api) -> None:
    r = await api.client.get("/health/live")
    assert len(r.headers["x-request-id"]) >= 16
    r = await api.client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


async def test_audit_reuses_uuid_request_id(api: Api) -> None:
    rid = "0b8e1b4c-8a5c-4f3e-9d55-2f5b1d6e7a90"
    await api.client.post("/kb/documents", json={}, headers={"X-Request-ID": rid})
    rec: dict[str, Any] = api.audit[-1]
    assert str(rec["request_id"]) == rid


async def test_metrics_optional(make_api: Callable[..., Api]) -> None:
    off = make_api()
    assert (await off.client.get("/metrics")).status_code == 404
    on = make_api(METRICS_ENABLED=True)
    await on.client.get("/health/live")
    r = await on.client.get("/metrics")
    assert r.status_code == 200 and "kb_http_requests_total" in r.text


async def test_admin_reindex_failure_exposes_error(make_api: Callable[..., Api]) -> None:
    api = make_api(ADMIN_TOKEN="s3cret")
    c = api.client
    await c.post("/kb/documents", json={"title": "T", "text": "Стендап в 11:00."})
    r = await c.post(
        "/admin/embedding-models", json={"provider": "nope", "name": "bad", "dimension": 8}, headers=H
    )
    assert r.status_code == 201 and r.json()["error"] is None
    assert (await c.post("/admin/embedding-models/bad/reindex", headers=H)).status_code == 202
    st = (await c.get("/admin/embedding-models/bad/status", headers=H)).json()
    assert st["status"] == "failed" and "NotImplementedError" in st["error"]
    listed = {m["name"]: m for m in (await c.get("/admin/embedding-models", headers=H)).json()}
    assert listed["bad"]["status"] == "failed" and listed["bad"]["error"]
    assert (await c.post("/admin/embedding-models/bad/activate", headers=H)).status_code == 409
