import pytest

from hashlib import sha256

from src.ingestion.canonicalization import normalize_markdown_body
from src.ingestion.chunking import chunk_markdown_body


def test_heading_aware_chunking_is_deterministic():
    body = (
        "# Payment Service\n"
        "\n"
        "Overview paragraph.\n"
        "\n"
        "## Impact\n"
        "\n"
        "Payment requests were delayed.\n"
        "\n"
        "### Customer impact\n"
        "\n"
        "Checkout latency increased.\n"
    )

    first = chunk_markdown_body(
        body,
        chunking_version="chunk-v1",
        max_characters=1200,
    )
    second = chunk_markdown_body(
        body,
        chunking_version="chunk-v1",
        max_characters=1200,
    )

    assert first == second
    assert [
        (
            chunk.chunk_key,
            chunk.chunk_index,
            chunk.chunking_version,
            chunk.heading_path,
            chunk.content,
        )
        for chunk in first
    ] == [
        (
            "chk_0000",
            0,
            "chunk-v1",
            ("Payment Service",),
            "Overview paragraph.",
        ),
        (
            "chk_0001",
            1,
            "chunk-v1",
            ("Payment Service", "Impact"),
            "Payment requests were delayed.",
        ),
        (
            "chk_0002",
            2,
            "chunk-v1",
            ("Payment Service", "Impact", "Customer impact"),
            "Checkout latency increased.",
        ),
    ]

    normalized_body = normalize_markdown_body(body)

    for chunk in first:
        assert chunk.content == normalized_body[
            chunk.start_offset:chunk.end_offset
        ]
        assert chunk.content_hash == sha256(
            chunk.content.encode("utf-8")
        ).hexdigest()
        assert chunk.end_offset > chunk.start_offset
        assert chunk.token_count is None


def test_chunking_accumulates_paragraphs_without_exceeding_limit():
    body = (
        "# Runbook\n"
        "\n"
        "Prepare deployment.\n"
        "\n"
        "Validate health checks.\n"
        "\n"
        "Notify stakeholders.\n"
    )

    chunks = chunk_markdown_body(
        body,
        chunking_version="chunk-v1",
        max_characters=44,
    )

    assert [
        (
            chunk.chunk_key,
            chunk.chunk_index,
            chunk.heading_path,
            chunk.content,
        )
        for chunk in chunks
    ] == [
        (
            "chk_0000",
            0,
            ("Runbook",),
            "Prepare deployment.\n\nValidate health checks.",
        ),
        (
            "chk_0001",
            1,
            ("Runbook",),
            "Notify stakeholders.",
        ),
    ]


def test_chunking_keeps_fenced_code_block_atomic():
    body = (
        "# Runbook\n"
        "\n"
        "Before command.\n"
        "\n"
        "```bash\n"
        "# restart safely\n"
        "\n"
        "systemctl restart payment\n"
        "```\n"
        "\n"
        "After command.\n"
    )

    chunks = chunk_markdown_body(
        body,
        chunking_version="chunk-v1",
        max_characters=32,
    )

    assert [chunk.heading_path for chunk in chunks] == [
        ("Runbook",),
        ("Runbook",),
        ("Runbook",),
    ]
    assert [chunk.content for chunk in chunks] == [
        "Before command.",
        "```bash\n# restart safely\n\nsystemctl restart payment\n```",
        "After command.",
    ]
    assert len(chunks[1].content) > 32


def test_chunking_keeps_bullet_list_atomic():
    body = (
        "# Response Steps\n"
        "\n"
        "- Stop traffic.\n"
        "\n"
        "- Verify error rate.\n"
        "\n"
        "- Notify on-call.\n"
    )

    chunks = chunk_markdown_body(
        body,
        chunking_version="chunk-v1",
        max_characters=32,
    )

    assert [
        (chunk.heading_path, chunk.content)
        for chunk in chunks
    ] == [
        (
            ("Response Steps",),
            "- Stop traffic.\n\n- Verify error rate.\n\n- Notify on-call.",
        )
    ]
    assert len(chunks[0].content) > 32


def test_chunking_normalizes_crlf_without_changing_output():
    lf_body = (
        "# Runbook\n"
        "\n"
        "Restart the service.\n"
        "\n"
        "Verify the health endpoint.\n"
    )
    crlf_body = lf_body.replace("\n", "\r\n")

    assert chunk_markdown_body(
        lf_body,
        chunking_version="chunk-v1",
        max_characters=1200,
    ) == chunk_markdown_body(
        crlf_body,
        chunking_version="chunk-v1",
        max_characters=1200,
    )


def test_chunking_keeps_oversized_paragraph_atomic():
    paragraph = "Restart the payment service safely. " * 4
    body = f"# Runbook\n\n{paragraph}"

    chunks = chunk_markdown_body(
        body,
        chunking_version="chunk-v1",
        max_characters=40,
    )

    assert len(chunks) == 1
    assert chunks[0].heading_path == ("Runbook",)
    assert chunks[0].content == paragraph.strip()
    assert len(chunks[0].content) > 40


def test_chunking_rejects_blank_chunking_version():
    with pytest.raises(
        ValueError,
        match="chunking_version must not be blank",
    ):
        chunk_markdown_body(
            "A paragraph.",
            chunking_version=" ",
            max_characters=1200,
        )
