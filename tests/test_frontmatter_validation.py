from pathlib import Path
from src.ingestion import frontmatter as frontmatter_module
from src.ingestion.frontmatter import validate_markdown_document
import pytest
from pydantic import ValidationError

SAMPLE_VAULT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "sample-vault"
    / "payment-service"
)

SAMPLE_DOCUMENTS = sorted(SAMPLE_VAULT.rglob("*.md"))

def test_valid_service_note_is_accepted():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"

    document = validate_markdown_document(
        source_path.read_text(encoding="utf-8"),
        source_uri=source_path.as_uri(),
    )

    assert document.frontmatter.schema_version == 1
    assert document.frontmatter.document_id == "fraud-service"
    assert document.frontmatter.document_type == "service"
    assert document.frontmatter.criticality == "high"
    assert document.body.startswith("# Fraud Service")
    assert document.source_uri == source_path.as_uri()

def test_service_without_required_criticality_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "criticality: high\n",
        "",
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("criticality",)
        and error["type"] == "missing"
        for error in exc_info.value.errors()
    )


def test_service_with_invalid_criticality_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "criticality: high",
        "criticality: urgent",
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("criticality",)
        and error["type"] == "literal_error"
        for error in exc_info.value.errors()
    )

def test_service_with_non_kebab_case_document_id_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "document_id: fraud-service",
        "document_id: Fraud_Service",
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("document_id",)
        and error["type"] == "value_error"
        for error in exc_info.value.errors()
    )

def test_valid_deployment_note_is_accepted():
    source_path = (
        SAMPLE_VAULT
        / "deployments"
        / "payment-service-v1-4-0.md"
    )

    document = validate_markdown_document(
        source_path.read_text(encoding="utf-8"),
        source_uri=source_path.as_uri(),
    )

    assert document.frontmatter.document_type == "deployment"
    assert document.frontmatter.environment == "production"
    assert document.frontmatter.release_version == "1.4.0"
    assert document.frontmatter.service_refs == ["payment-service"]
    assert document.frontmatter.deployed_at.utcoffset() is not None
    assert document.body.startswith(
        "# Payment Service Deployment v1.4.0"
    )

def test_sample_vault_contains_twelve_documents():
    assert len(SAMPLE_DOCUMENTS) == 12


@pytest.mark.parametrize(
    "source_path",
    SAMPLE_DOCUMENTS,
    ids=lambda path: str(path.relative_to(SAMPLE_VAULT)),
)
def test_sample_vault_document_is_accepted(source_path: Path):
    document = validate_markdown_document(
        source_path.read_text(encoding="utf-8"),
        source_uri=source_path.as_uri(),
    )

    assert document.frontmatter.document_id
    assert document.frontmatter.document_type
    assert document.body.startswith("# ")

def test_restricted_document_without_allowlist_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "access_scope: organization",
        "access_scope: restricted",
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ()
        and "restricted access_scope requires" in error["msg"]
        for error in exc_info.value.errors()
    )

def test_organization_document_with_allowlist_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "access_scope: organization\n",
        (
            "access_scope: organization\n"
            "allowed_teams:\n"
            "  - risk-platform\n"
        ),
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ()
        and "organization access_scope cannot contain" in error["msg"]
        for error in exc_info.value.errors()
    )


def test_restricted_document_with_allowlist_is_accepted():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    restricted_markdown = markdown_content.replace(
        "access_scope: organization\n",
        (
            "access_scope: restricted\n"
            "allowed_teams:\n"
            "  - risk-platform\n"
        ),
        1,
    )

    document = validate_markdown_document(
        restricted_markdown,
        source_uri=source_path.as_uri(),
    )

    assert document.frontmatter.access_scope == "restricted"
    assert document.frontmatter.allowed_teams == ["risk-platform"]

def test_resolved_incident_without_resolved_at_is_rejected():
    source_path = (
        SAMPLE_VAULT
        / "incidents"
        / "payment-service-http-503.md"
    )
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        'resolved_at: "2026-10-01T09:38:00+07:00"\n',
        "",
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ()
        and "resolved incident requires resolved_at" in error["msg"]
        for error in exc_info.value.errors()
    )

def test_deployment_with_empty_service_refs_is_rejected():
    source_path = (
        SAMPLE_VAULT
        / "deployments"
        / "payment-service-v1-4-0.md"
    )
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        (
            "service_refs:\n"
            "  - payment-service\n"
        ),
        "service_refs: []\n",
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("service_refs",)
        and error["type"] == "too_short"
        for error in exc_info.value.errors()
    )

def test_batch_with_broken_required_reference_is_rejected():
    documents = []

    for source_path in SAMPLE_DOCUMENTS:
        markdown_content = source_path.read_text(encoding="utf-8")

        if source_path.name == "payment-service-v1-4-0.md":
            markdown_content = markdown_content.replace(
                (
                    "service_refs:\n"
                    "  - payment-service\n"
                ),
                (
                    "service_refs:\n"
                    "  - missing-service\n"
                ),
                1,
            )

        documents.append(
            validate_markdown_document(
                markdown_content,
                source_uri=source_path.as_uri(),
            )
        )

    with pytest.raises(
        ValueError,
        match=(
            "broken required reference: "
            "payment-service-deployment-v1-4-0"
            r"\.service_refs -> missing-service"
        ),
    ):
        frontmatter_module.validate_document_batch(documents)

def test_batch_with_broken_optional_reference_returns_warning():
    documents = []

    for source_path in SAMPLE_DOCUMENTS:
        markdown_content = source_path.read_text(encoding="utf-8")

        if source_path.name == "payment-service.md":
            markdown_content = markdown_content.replace(
                (
                    "dependency_refs:\n"
                    "  - fraud-service\n"
                ),
                (
                    "dependency_refs:\n"
                    "  - missing-service\n"
                ),
                1,
            )

        documents.append(
            validate_markdown_document(
                markdown_content,
                source_uri=source_path.as_uri(),
            )
        )

    result = frontmatter_module.validate_document_batch(
        documents
    )

    assert len(result.documents) == 12
    assert len(result.warnings) == 1

    warning = result.warnings[0]

    assert warning.code == "broken_optional_reference"
    assert warning.document_id == "payment-service"
    assert warning.field_name == "dependency_refs"
    assert warning.reference_id == "missing-service"

def test_batch_with_duplicate_document_ids_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    duplicate_source_uri = (
        f"{source_path.as_uri()}?duplicate=1"
    )

    first_document = validate_markdown_document(
        markdown_content,
        source_uri=source_path.as_uri(),
    )
    duplicate_document = validate_markdown_document(
        markdown_content,
        source_uri=duplicate_source_uri,
    )

    with pytest.raises(ValueError) as exc_info:
        frontmatter_module.validate_document_batch(
            [first_document, duplicate_document]
        )

    assert str(exc_info.value) == (
        "duplicate document_id: fraud-service; "
        f"sources: {source_path.as_uri()}, "
        f"{duplicate_source_uri}"
    )

def test_external_document_without_source_refs_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "provenance_type: synthetic",
        "provenance_type: external",
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ()
        and "external provenance requires source_refs"
        in error["msg"]
        for error in exc_info.value.errors()
    )

def test_effective_from_without_timezone_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "access_scope: organization\n",
        (
            "access_scope: organization\n"
            'effective_from: "2026-10-01T09:00:00"\n'
        ),
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("effective_from",)
        and "datetime must include timezone" in error["msg"]
        for error in exc_info.value.errors()
    )

def test_effective_until_before_effective_from_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "access_scope: organization\n",
        (
            "access_scope: organization\n"
            'effective_from: "2026-10-02T09:00:00+07:00"\n'
            'effective_until: "2026-10-01T09:00:00+07:00"\n'
        ),
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ()
        and "effective_until must be after effective_from"
        in error["msg"]
        for error in exc_info.value.errors()
    )

def test_duplicate_relationship_references_are_rejected():
    source_path = (
        SAMPLE_VAULT
        / "services"
        / "payment-service.md"
    )
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        (
            "dependency_refs:\n"
            "  - fraud-service\n"
        ),
        (
            "dependency_refs:\n"
            "  - fraud-service\n"
            "  - fraud-service\n"
        ),
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("dependency_refs",)
        and "references must not contain duplicates"
        in error["msg"]
        for error in exc_info.value.errors()
    )

def test_self_reference_is_rejected():
    documents = []

    for source_path in SAMPLE_DOCUMENTS:
        markdown_content = source_path.read_text(encoding="utf-8")

        if source_path.name == "payment-service.md":
            markdown_content = markdown_content.replace(
                (
                    "dependency_refs:\n"
                    "  - fraud-service\n"
                ),
                (
                    "dependency_refs:\n"
                    "  - payment-service\n"
                ),
                1,
            )

        documents.append(
            validate_markdown_document(
                markdown_content,
                source_uri=source_path.as_uri(),
            )
        )

    with pytest.raises(
        ValueError,
        match=(
            "self-reference is not allowed: "
            "payment-service"
            r"\.dependency_refs -> payment-service"
        ),
    ):
        frontmatter_module.validate_document_batch(documents)

def test_optional_reference_with_wrong_document_type_is_rejected():
    documents = []

    for source_path in SAMPLE_DOCUMENTS:
        markdown_content = source_path.read_text(encoding="utf-8")

        if source_path.name == "payment-service.md":
            markdown_content = markdown_content.replace(
                (
                    "dependency_refs:\n"
                    "  - fraud-service\n"
                ),
                (
                    "dependency_refs:\n"
                    "  - payment-service-high-5xx-alert\n"
                ),
                1,
            )

        documents.append(
            validate_markdown_document(
                markdown_content,
                source_uri=source_path.as_uri(),
            )
        )

    with pytest.raises(ValueError) as exc_info:
        frontmatter_module.validate_document_batch(documents)

    assert str(exc_info.value) == (
        "invalid reference type: "
        "payment-service.dependency_refs "
        "-> payment-service-high-5xx-alert; "
        "expected service, got alert"
    )

def test_uppercase_tag_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "  - fraud\n",
        "  - Fraud\n",
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("tags",)
        and "tags must be lowercase" in error["msg"]
        for error in exc_info.value.errors()
    )

def test_duplicate_tags_are_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        (
            "tags:\n"
            "  - fraud\n"
        ),
        (
            "tags:\n"
            "  - fraud\n"
            "  - fraud\n"
        ),
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("tags",)
        and "tags must not contain duplicates" in error["msg"]
        for error in exc_info.value.errors()
    )

def test_sample_vault_batch_is_valid():
    documents = [
        validate_markdown_document(
            source_path.read_text(encoding="utf-8"),
            source_uri=source_path.as_uri(),
        )
        for source_path in SAMPLE_DOCUMENTS
    ]

    result = frontmatter_module.validate_document_batch(
        documents
    )

    assert len(result.documents) == 12
    assert result.warnings == []

def test_required_reference_with_wrong_document_type_is_rejected():
    documents = []

    for source_path in SAMPLE_DOCUMENTS:
        markdown_content = source_path.read_text(encoding="utf-8")

        if source_path.name == "payment-service-v1-4-0.md":
            markdown_content = markdown_content.replace(
                (
                    "service_refs:\n"
                    "  - payment-service\n"
                ),
                (
                    "service_refs:\n"
                    "  - payment-service-high-5xx-alert\n"
                ),
                1,
            )

        documents.append(
            validate_markdown_document(
                markdown_content,
                source_uri=source_path.as_uri(),
            )
        )

    with pytest.raises(ValueError) as exc_info:
        frontmatter_module.validate_document_batch(documents)

    assert str(exc_info.value) == (
        "invalid reference type: "
        "payment-service-deployment-v1-4-0.service_refs "
        "-> payment-service-high-5xx-alert; "
        "expected service, got alert"
    )

def test_empty_title_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "title: Fraud Service",
        'title: ""',
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("title",)
        and "title must not be empty" in error["msg"]
        for error in exc_info.value.errors()
    )

@pytest.mark.parametrize(
    (
        "relative_path",
        "valid_line",
        "empty_line",
        "field_name",
    ),
    [
        (
            Path("deployments")
            / "payment-service-v1-4-0.md",
            "release_version: 1.4.0",
            'release_version: ""',
            "release_version",
        ),
        (
            Path("alerts")
            / "payment-service-high-5xx.md",
            "source_system: prometheus",
            'source_system: ""',
            "source_system",
        ),
        (
            Path("alerts")
            / "payment-service-high-5xx.md",
            (
                "alert_rule_ref: "
                "payment-service-high-5xx-rate"
            ),
            'alert_rule_ref: ""',
            "alert_rule_ref",
        ),
    ],
)
def test_required_identifier_cannot_be_empty(
    relative_path: Path,
    valid_line: str,
    empty_line: str,
    field_name: str,
):
    source_path = SAMPLE_VAULT / relative_path
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        valid_line,
        empty_line,
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == (field_name,)
        and "must not be empty" in error["msg"]
        for error in exc_info.value.errors()
    )

def test_unknown_frontmatter_field_is_rejected():
    source_path = SAMPLE_VAULT / "services" / "fraud-service.md"
    markdown_content = source_path.read_text(encoding="utf-8")
    invalid_markdown = markdown_content.replace(
        "criticality: high\n",
        (
            "criticality: high\n"
            "unexpected_field: unexpected-value\n"
        ),
        1,
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_markdown_document(
            invalid_markdown,
            source_uri=source_path.as_uri(),
        )

    assert any(
        error["loc"] == ("unexpected_field",)
        and error["type"] == "extra_forbidden"
        for error in exc_info.value.errors()
    )
