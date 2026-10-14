import re
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

DOCUMENT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

def validate_unique_references(
    values: list[str],
) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(
            "references must not contain duplicates"
        )

    return values

def validate_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone")

    return value

def validate_non_empty_string(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be empty")

    return value

NonEmptyString = Annotated[
    str,
    AfterValidator(validate_non_empty_string),
]

TimezoneAwareDatetime = Annotated[
    datetime,
    AfterValidator(validate_timezone),
]

UniqueReferenceList = Annotated[
    list[str],
    AfterValidator(validate_unique_references),
]

NonEmptyReferenceList = Annotated[
    UniqueReferenceList,
    Field(min_length=1),
]


class CommonFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    document_id: str
    document_type: str
    title: str
    document_status: Literal["active", "deprecated", "revoked"]
    review_state: Literal["draft", "in_review", "approved", "rejected"]
    owner_team: str
    provenance_type: Literal["internal", "external", "synthetic"]
    source_refs: list[str] = Field(default_factory=list)
    access_scope: Literal["organization", "restricted"]
    allowed_users: list[str] = Field(default_factory=list)
    allowed_teams: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    effective_from: TimezoneAwareDatetime | None = None
    effective_until: TimezoneAwareDatetime | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not DOCUMENT_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "document_id must use lowercase kebab-case"
            )

        return value

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be empty")

        return value

    @field_validator("tags")
    @classmethod
    def validate_tags_lowercase(
        cls,
        values: list[str],
    ) -> list[str]:
        if any(tag != tag.lower() for tag in values):
            raise ValueError("tags must be lowercase")

        if len(values) != len(set(values)):
            raise ValueError(
                "tags must not contain duplicates"
            )

        return values

    @model_validator(mode="after")
    def validate_access_scope(self) -> Self:
        has_allowlist = any(
            (
                self.allowed_users,
                self.allowed_teams,
                self.allowed_roles,
            )
        )

        if self.access_scope == "restricted" and not has_allowlist:
            raise ValueError(
                "restricted access_scope requires at least one "
                "allowed_user, team, or role"
            )

        if self.access_scope == "organization" and has_allowlist:
            raise ValueError(
                "organization access_scope cannot contain "
                "allowed_users, teams, or roles"
            )

        return self

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if (
            self.provenance_type == "external"
            and not self.source_refs
        ):
            raise ValueError(
                "external provenance requires source_refs"
            )

        return self

    @model_validator(mode="after")
    def validate_temporal_window(self) -> Self:
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until <= self.effective_from
        ):
            raise ValueError(
                "effective_until must be after effective_from"
            )

        return self

class ServiceFrontmatter(CommonFrontmatter):
    document_type: Literal["service"]
    criticality: Literal["low", "medium", "high", "critical"]
    dependency_refs: UniqueReferenceList = Field(
        default_factory=list
    )


class DeploymentFrontmatter(CommonFrontmatter):
    document_type: Literal["deployment"]
    service_refs: NonEmptyReferenceList
    environment: Literal["development", "staging", "production"]
    release_version: NonEmptyString
    deployed_at: TimezoneAwareDatetime




class IncidentFrontmatter(CommonFrontmatter):
    document_type: Literal["incident"]
    severity: Literal["sev1", "sev2", "sev3", "sev4"]
    incident_status: Literal[
        "detected",
        "investigating",
        "mitigated",
        "resolved",
    ]
    affected_service_refs: NonEmptyReferenceList
    started_at: TimezoneAwareDatetime
    resolved_at: TimezoneAwareDatetime | None = None


    @model_validator(mode="after")
    def validate_resolution_state(self) -> Self:
        if (
            self.incident_status == "resolved"
            and self.resolved_at is None
        ):
            raise ValueError(
                "resolved incident requires resolved_at"
            )

        return self

class PostmortemFrontmatter(CommonFrontmatter):
    document_type: Literal["postmortem"]
    incident_refs: NonEmptyReferenceList
    contributing_service_refs: UniqueReferenceList = Field(
        default_factory=list
    )


class RunbookFrontmatter(CommonFrontmatter):
    document_type: Literal["runbook"]
    service_refs: NonEmptyReferenceList
    runbook_kind: Literal[
        "diagnosis",
        "mitigation",
        "recovery",
        "maintenance",
    ]


class AlertFrontmatter(CommonFrontmatter):
    document_type: Literal["alert"]
    service_refs: NonEmptyReferenceList
    source_system: NonEmptyString
    alert_rule_ref: NonEmptyString
    severity: Literal["info", "warning", "critical"]

Frontmatter = (
    ServiceFrontmatter
    | DeploymentFrontmatter
    | IncidentFrontmatter
    | PostmortemFrontmatter
    | RunbookFrontmatter
    | AlertFrontmatter
)

class ValidatedMarkdownDocument(BaseModel):
    source_uri: str
    frontmatter: Frontmatter
    body: str

class ValidationWarning(BaseModel):
    code: Literal["broken_optional_reference"]
    document_id: str
    field_name: str
    reference_id: str
    message: str


class BatchValidationResult(BaseModel):
    documents: list[ValidatedMarkdownDocument]
    warnings: list[ValidationWarning] = Field(
        default_factory=list
    )

FRONTMATTER_MODELS: dict[
    str,
    type[CommonFrontmatter],
] = {
    "service": ServiceFrontmatter,
    "deployment": DeploymentFrontmatter,
    "incident": IncidentFrontmatter,
    "postmortem": PostmortemFrontmatter,
    "runbook": RunbookFrontmatter,
    "alert": AlertFrontmatter,
}


def validate_markdown_document(
    markdown_content: str,
    *,
    source_uri: str,
) -> ValidatedMarkdownDocument:
    lines = markdown_content.splitlines()

    if not lines or lines[0].strip() != "---":
        raise ValueError(
            "Markdown document must start with YAML frontmatter"
        )

    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise ValueError(
            "YAML frontmatter is missing its closing delimiter"
        ) from exc

    raw_frontmatter = "\n".join(lines[1:closing_index])
    parsed_frontmatter = yaml.safe_load(raw_frontmatter)

    if not isinstance(parsed_frontmatter, dict):
        raise ValueError("YAML frontmatter must be a mapping")

    document_type = parsed_frontmatter.get("document_type")
    frontmatter_model = FRONTMATTER_MODELS.get(document_type)

    if frontmatter_model is None:
        raise ValueError(
            f"Unsupported document_type: {document_type!r}"
        )

    validated_frontmatter = frontmatter_model.model_validate(
        parsed_frontmatter
    )
    body = "\n".join(
        lines[closing_index + 1 :]
    ).lstrip("\r\n")

    return ValidatedMarkdownDocument(
        source_uri=source_uri,
        frontmatter=validated_frontmatter,
        body=body,
    )

OPTIONAL_REFERENCE_TYPES: dict[
    str,
    dict[str, str],
] = {
    "service": {
        "dependency_refs": "service",
    },
    "postmortem": {
        "contributing_service_refs": "service",
    },
}

REQUIRED_REFERENCE_TYPES: dict[
    str,
    dict[str, str],
] = {
    "deployment": {
        "service_refs": "service",
    },
    "incident": {
        "affected_service_refs": "service",
    },
    "postmortem": {
        "incident_refs": "incident",
    },
    "runbook": {
        "service_refs": "service",
    },
    "alert": {
        "service_refs": "service",
    },
}

def validate_document_batch(
    documents: Sequence[ValidatedMarkdownDocument],
) -> BatchValidationResult:
    documents_by_id: dict[str, ValidatedMarkdownDocument] = {}

    for document in documents:
        document_id = document.frontmatter.document_id
        previous_document = documents_by_id.get(document_id)

        if previous_document is not None:
            raise ValueError(
                f"duplicate document_id: {document_id}; "
                f"sources: {previous_document.source_uri}, "
                f"{document.source_uri}"
            )

        documents_by_id[document_id] = document

    for document in documents:
        document_id = document.frontmatter.document_id
        document_type = document.frontmatter.document_type

        required_fields = REQUIRED_REFERENCE_TYPES.get(
            document_type,
            {},
        )

        relationship_fields = {
            **required_fields,
            **OPTIONAL_REFERENCE_TYPES.get(document_type, {}),
        }

        for field_name in relationship_fields:
            reference_ids = getattr(
                document.frontmatter,
                field_name,
            )

            if document_id in reference_ids:
                raise ValueError(
                    "self-reference is not allowed: "
                    f"{document_id}.{field_name} "
                    f"-> {document_id}"
                )

        for field_name, expected_type in required_fields.items():
            reference_ids = getattr(
                document.frontmatter,
                field_name,
            )

            for reference_id in reference_ids:
                referenced_document = documents_by_id.get(
                    reference_id
                )

                if referenced_document is None:
                    raise ValueError(
                        "broken required reference: "
                        f"{document_id}.{field_name} "
                        f"-> {reference_id}"
                    )

                actual_type = (
                    referenced_document.frontmatter.document_type
                )

                if actual_type != expected_type:
                    raise ValueError(
                        "invalid reference type: "
                        f"{document_id}.{field_name} "
                        f"-> {reference_id}; "
                        f"expected {expected_type}, "
                        f"got {actual_type}"
                    )

    warnings: list[ValidationWarning] = []

    for document in documents:
        document_id = document.frontmatter.document_id
        document_type = document.frontmatter.document_type
        optional_fields = OPTIONAL_REFERENCE_TYPES.get(
            document_type,
            {},
        )

        for field_name, expected_type in optional_fields.items():
            reference_ids = getattr(
                document.frontmatter,
                field_name,
            )

            for reference_id in reference_ids:
                referenced_document = documents_by_id.get(
                    reference_id
                )

                if referenced_document is None:
                    warnings.append(
                        ValidationWarning(
                            code="broken_optional_reference",
                            document_id=document_id,
                            field_name=field_name,
                            reference_id=reference_id,
                            message=(
                                "broken optional reference: "
                                f"{document_id}.{field_name} "
                                f"-> {reference_id}"
                            ),
                        )
                    )
                    continue

                actual_type = (
                    referenced_document.frontmatter.document_type
                )

                if actual_type != expected_type:
                    raise ValueError(
                        "invalid reference type: "
                        f"{document_id}.{field_name} "
                        f"-> {reference_id}; "
                        f"expected {expected_type}, "
                        f"got {actual_type}"
                    )

    return BatchValidationResult(
        documents=list(documents),
        warnings=warnings,
    )
