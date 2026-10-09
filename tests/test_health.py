import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from src.main import app


@pytest.mark.asyncio
async def test_root_endpoint():
    """Verify that root endpoint returns application metadata."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "OpsBrain API"
        assert data["status"] == "running"


@pytest.mark.asyncio
async def test_liveness_endpoint():
    """Verify that liveness probe returns alive status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/liveness")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_health_endpoint_healthy():
    """Verify /health returns HTTP 200 when database and pgvector are ready."""
    mock_db_status = {
        "connected": True,
        "pgvector_ready": True,
        "error": None,
    }
    with patch("src.main.check_database_readiness", new=AsyncMock(return_value=mock_db_status)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert data["components"]["database"]["connected"] is True
            assert data["components"]["database"]["pgvector_ready"] is True
            assert data["components"]["database"]["error"] is None


@pytest.mark.asyncio
async def test_health_endpoint_db_disconnected():
    """Verify /health returns HTTP 503 when database connection is down."""
    mock_db_status = {
        "connected": False,
        "pgvector_ready": False,
        "error": "Connection refused to database host",
    }
    with patch("src.main.check_database_readiness", new=AsyncMock(return_value=mock_db_status)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["components"]["database"]["connected"] is False
            assert data["components"]["database"]["pgvector_ready"] is False
            assert "Connection refused" in data["components"]["database"]["error"]


@pytest.mark.asyncio
async def test_health_endpoint_missing_pgvector():
    """Verify /health returns HTTP 503 when connected to DB but pgvector is not installed."""
    mock_db_status = {
        "connected": True,
        "pgvector_ready": False,
        "error": "pgvector extension is not activated in database",
    }
    with patch("src.main.check_database_readiness", new=AsyncMock(return_value=mock_db_status)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["components"]["database"]["connected"] is True
            assert data["components"]["database"]["pgvector_ready"] is False
            assert "pgvector extension is not activated" in data["components"]["database"]["error"]
