"""Contract tests for shared error envelope and request_id propagation (§14)."""

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field, field_validator

from src.api.errors import register_error_handlers


class _NestedItem(BaseModel):
    source_uri: str = Field(min_length=1)


class _EnvelopeRequest(BaseModel):
    documents: list[_NestedItem] = Field(min_length=1)
    secret_check: str | None = None

    @field_validator("secret_check")
    @classmethod
    def _reject_secret(cls, value: str | None) -> str | None:
        if value is not None:
            raise ValueError(f"leaked:{value}")
        return value


def _build_test_app() -> FastAPI:
    test_app = FastAPI()
    register_error_handlers(test_app)

    @test_app.get("/ok")
    async def ok():
        return {"status": "ok"}

    @test_app.get("/forbidden")
    async def forbidden():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "query_forbidden")

    @test_app.get("/unavailable")
    async def unavailable():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "embedding_unavailable")

    @test_app.get("/leaky-detail")
    async def leaky_detail():
        raise HTTPException(status.HTTP_409_CONFLICT, "safe_code\nsecret_leak")

    @test_app.post("/validate")
    async def validate_body(payload: _EnvelopeRequest):
        return {"count": len(payload.documents)}

    @test_app.get("/boom")
    async def boom():
        raise RuntimeError("postgresql://secret_user:secret_pass@db:5432/opsbrain")

    return test_app


@pytest.mark.asyncio
async def test_request_id_header_is_returned_on_success():
    test_app = _build_test_app()
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/ok", headers={"X-Request-Id": "req-custom-123"})
        bad = await client.get("/ok", headers={"X-Request-Id": "req-bad\n"})
    assert resp.status_code == 200
    assert resp.headers["X-Request-Id"] == "req-custom-123"
    assert bad.status_code == 200
    assert bad.headers["X-Request-Id"] != "req-bad\n"
    assert "\n" not in bad.headers["X-Request-Id"]


@pytest.mark.asyncio
async def test_http_exceptions_use_contract_envelope_and_retryable_flag():
    test_app = _build_test_app()
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r403 = await client.get("/forbidden")
        r503 = await client.get("/unavailable")
        r409 = await client.get("/leaky-detail")

    assert r403.status_code == 403
    body403 = r403.json()
    assert body403["request_id"]
    assert r403.headers["X-Request-Id"] == body403["request_id"]
    assert body403["error"] == {
        "code": "query_forbidden",
        "message": "Principal lacks query:execute capability.",
        "retryable": False,
        "details": [],
    }

    assert r503.status_code == 503
    body503 = r503.json()
    assert body503["request_id"]
    assert body503["error"] == {
        "code": "embedding_unavailable",
        "message": "Embedding service is temporarily unavailable.",
        "retryable": True,
        "details": [],
    }

    assert r409.status_code == 409
    assert "secret_leak" not in r409.text
    assert r409.json()["error"]["code"] == "conflict"


@pytest.mark.asyncio
async def test_validation_error_formats_field_and_hides_input_values():
    test_app = _build_test_app()
    transport = ASGITransport(app=test_app)
    sentinel = "SECRET-INPUT-VALUE-999"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/validate",
            json={
                "documents": [{"source_uri": 12345, "extra": sentinel}],
                "secret_check": sentinel,
            },
        )

    assert resp.status_code == 422
    assert sentinel not in resp.text
    body = resp.json()
    assert body["request_id"]
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["retryable"] is False
    assert body["error"]["details"][0]["field"] == "documents[0].source_uri"
    assert body["error"]["details"][1]["field"] == "secret_check"
    assert body["error"]["details"][1]["message"] == "Value failed validation."


@pytest.mark.asyncio
async def test_unhandled_exception_returns_generic_500_without_leaking_secrets():
    test_app = _build_test_app()
    transport = ASGITransport(app=test_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/boom")

    assert resp.status_code == 500
    assert "secret_pass" not in resp.text
    assert "RuntimeError" not in resp.text
    body = resp.json()
    assert body["request_id"]
    assert body["error"] == {
        "code": "internal_error",
        "message": "An internal error occurred.",
        "retryable": False,
        "details": [],
    }
