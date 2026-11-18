"""Narrow Ollama `/api/embed` client for derived embedding work."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

import httpx

from src.config import get_settings


class OllamaEmbeddingError(RuntimeError):
    """A sanitized Ollama failure suitable for worker classification."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class OllamaEmbeddingClient:
    """Generate fixed-dimension text embeddings through Ollama's batch API."""

    base_url: str
    model: str
    dimensions: int
    http_client: httpx.AsyncClient | None = None

    @classmethod
    def from_settings(
        cls,
        *,
        model: str | None = None,
        dimensions: int,
    ) -> "OllamaEmbeddingClient":
        settings = get_settings()
        return cls(
            base_url=settings.OLLAMA_BASE_URL,
            model=model or settings.OLLAMA_EMBED_MODEL,
            dimensions=dimensions,
        )

    async def embed_texts(self, inputs: Sequence[str]) -> list[list[float]]:
        if not self.model.strip() or self.dimensions < 1:
            raise ValueError("embedding model and dimensions must be configured")
        if not inputs or any(not isinstance(item, str) for item in inputs):
            raise ValueError("embedding input batch must contain text")

        payload = {
            "model": self.model,
            "input": list(inputs),
            "dimensions": self.dimensions,
            "truncate": False,
        }
        if self.http_client is None:
            async with httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"), timeout=30.0,
            ) as http_client:
                response = await self._post_embed(http_client, payload)
        else:
            response = await self._post_embed(self.http_client, payload)

        return self._validate_response(response, expected_count=len(inputs))

    async def _post_embed(
        self,
        http_client: httpx.AsyncClient,
        payload: dict[str, object],
    ) -> httpx.Response:
        try:
            response = await http_client.post("/api/embed", json=payload)
        except httpx.RequestError as error:
            raise OllamaEmbeddingError(
                "ollama_transport_failed", retryable=True,
            ) from error

        if response.status_code == 429 or response.status_code >= 500:
            raise OllamaEmbeddingError("ollama_unavailable", retryable=True)
        if response.status_code >= 400:
            raise OllamaEmbeddingError("ollama_request_rejected", retryable=False)
        return response

    def _validate_response(
        self,
        response: httpx.Response,
        *,
        expected_count: int,
    ) -> list[list[float]]:
        try:
            payload = response.json()
        except ValueError as error:
            raise OllamaEmbeddingError(
                "ollama_response_invalid", retryable=False,
            ) from error
        if (
            not isinstance(payload, dict)
            or not self._same_model(payload.get("model"), self.model)
        ):
            raise OllamaEmbeddingError("ollama_model_mismatch", retryable=False)

        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != expected_count:
            raise OllamaEmbeddingError(
                "ollama_embedding_count_mismatch", retryable=False,
            )

        normalized: list[list[float]] = []
        for embedding in embeddings:
            if not isinstance(embedding, list) or len(embedding) != self.dimensions:
                raise OllamaEmbeddingError(
                    "ollama_embedding_dimension_mismatch", retryable=False,
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                for value in embedding
            ):
                raise OllamaEmbeddingError(
                    "ollama_embedding_values_invalid", retryable=False,
                )
            normalized.append([float(value) for value in embedding])
        return normalized

    @staticmethod
    def _same_model(reported: object, requested: str) -> bool:
        """Treat an omitted tag as Ollama's conventional ``:latest`` tag."""
        if not isinstance(reported, str):
            return False

        def canonical(model: str) -> str:
            normalized = model.strip()
            return normalized if ":" in normalized else f"{normalized}:latest"

        return canonical(reported) == canonical(requested)
