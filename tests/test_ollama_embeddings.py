import json

import httpx
import pytest

from src.ingestion.ollama_embeddings import (
    OllamaEmbeddingClient,
    OllamaEmbeddingError,
)


@pytest.mark.asyncio
async def test_ollama_embedding_client_sends_complete_batch_and_validates_response():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "nomic-embed-text",
                "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://ollama.test",
    ) as http_client:
        client = OllamaEmbeddingClient(
            base_url="http://ollama.test/",
            model="nomic-embed-text",
            dimensions=3,
            http_client=http_client,
        )
        embeddings = await client.embed_texts(["first chunk", "second chunk"])

    assert embeddings == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert captured == {
        "url": "http://ollama.test/api/embed",
        "payload": {
            "model": "nomic-embed-text",
            "input": ["first chunk", "second chunk"],
            "dimensions": 3,
            "truncate": False,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        ({"model": "other-model", "embeddings": [[0.1, 0.2, 0.3]]}, "ollama_model_mismatch"),
        ({"model": "nomic-embed-text", "embeddings": [[0.1, 0.2]]}, "ollama_embedding_dimension_mismatch"),
        ({"model": "nomic-embed-text", "embeddings": []}, "ollama_embedding_count_mismatch"),
        ({"model": "nomic-embed-text", "embeddings": [[True, 0.2, 0.3]]}, "ollama_embedding_values_invalid"),
    ],
)
async def test_ollama_embedding_client_rejects_invalid_response(response, code):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://ollama.test",
    ) as http_client:
        client = OllamaEmbeddingClient(
            base_url="http://ollama.test",
            model="nomic-embed-text",
            dimensions=3,
            http_client=http_client,
        )
        with pytest.raises(OllamaEmbeddingError) as error:
            await client.embed_texts(["chunk"])

    assert (error.value.code, error.value.retryable) == (code, False)


@pytest.mark.asyncio
async def test_ollama_embedding_client_rejects_nan_response_value():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"model":"nomic-embed-text","embeddings":[[NaN,0.2,0.3]]}',
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama.test",
    ) as http_client:
        client = OllamaEmbeddingClient(
            base_url="http://ollama.test", model="nomic-embed-text", dimensions=3,
            http_client=http_client,
        )
        with pytest.raises(OllamaEmbeddingError) as error:
            await client.embed_texts(["chunk"])

    assert (error.value.code, error.value.retryable) == (
        "ollama_embedding_values_invalid", False,
    )


@pytest.mark.asyncio
async def test_ollama_embedding_client_accepts_latest_tag_alias():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"model": "nomic-embed-text:latest", "embeddings": [[0.1, 0.2, 0.3]]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama.test",
    ) as http_client:
        client = OllamaEmbeddingClient(
            base_url="http://ollama.test", model="nomic-embed-text", dimensions=3,
            http_client=http_client,
        )
        assert await client.embed_texts(["chunk"]) == [[0.1, 0.2, 0.3]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "retryable"), [(429, True), (503, True), (400, False)],
)
async def test_ollama_embedding_client_classifies_http_errors(status_code, retryable):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "test"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama.test",
    ) as http_client:
        client = OllamaEmbeddingClient(
            base_url="http://ollama.test", model="nomic-embed-text", dimensions=3,
            http_client=http_client,
        )
        with pytest.raises(OllamaEmbeddingError) as error:
            await client.embed_texts(["chunk"])

    assert error.value.retryable is retryable


@pytest.mark.asyncio
async def test_ollama_embedding_client_marks_transport_failure_retryable():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://ollama.test",
    ) as http_client:
        client = OllamaEmbeddingClient(
            base_url="http://ollama.test",
            model="nomic-embed-text",
            dimensions=3,
            http_client=http_client,
        )
        with pytest.raises(OllamaEmbeddingError) as error:
            await client.embed_texts(["chunk"])

    assert (error.value.code, error.value.retryable) == (
        "ollama_transport_failed", True,
    )
