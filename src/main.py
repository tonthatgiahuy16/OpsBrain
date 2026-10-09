import logging
import sys
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Response, status
from fastapi.responses import JSONResponse

from src import __version__
from src.config import get_settings
from src.database import check_database_readiness

settings = get_settings()

# Configure Structured Logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("opsbrain.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info(
        f"Starting OpsBrain API v{__version__} [Environment: {settings.ENVIRONMENT}]"
    )
    yield
    logger.info("Shutting down OpsBrain API")


app = FastAPI(
    title="OpsBrain API",
    description="Enterprise Second Brain for IT Operations Context & Incident Investigation",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/", tags=["General"])
async def root() -> Dict[str, str]:
    """Root endpoint returning basic service info."""
    return {
        "name": "OpsBrain API",
        "version": __version__,
        "status": "running",
        "docs_url": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health_check(response: Response) -> Dict[str, Any]:
    """Comprehensive health and readiness check for OpsBrain API, PostgreSQL and pgvector."""
    db_readiness = await check_database_readiness()

    is_healthy = db_readiness["connected"] and db_readiness["pgvector_ready"]

    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "app": "OpsBrain API",
        "version": __version__,
        "environment": settings.ENVIRONMENT,
        "components": {
          "database": {
            "connected": db_readiness["connected"],
            "pgvector_ready": db_readiness["pgvector_ready"],
            "error": db_readiness["error"],
          }
        },
    }


@app.get("/health/liveness", tags=["Health"])
async def liveness() -> Dict[str, str]:
    """Liveness probe: verifies that the application process is running."""
    return {"status": "alive"}
