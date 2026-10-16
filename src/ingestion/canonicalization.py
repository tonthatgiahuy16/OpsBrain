import hashlib
import json

from src.ingestion.frontmatter import ValidatedMarkdownDocument


CANONICALIZATION_VERSION = 1
HASH_ALGORITHM = "sha256"


def normalize_markdown_body(body: str) -> str:
    return body.replace("\r\n", "\n").replace("\r", "\n")


def canonical_content_bytes(
    document: ValidatedMarkdownDocument,
) -> bytes:
    payload = {
        "frontmatter": document.frontmatter.model_dump(
            mode="json",
        ),
        "markdown_body": normalize_markdown_body(document.body),
    }

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return serialized.encode("utf-8")


def canonical_content_hash(
    document: ValidatedMarkdownDocument,
) -> str:
    return hashlib.sha256(
        canonical_content_bytes(document)
    ).hexdigest()
