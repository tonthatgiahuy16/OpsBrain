"""Pre-ACL retrieval eligibility rules shared by activation and enqueueing."""

from datetime import datetime


def is_pre_acl_retrieval_eligible(organization, document, version, now: datetime) -> bool:
    """Return lifecycle and temporal eligibility; ACL is evaluated elsewhere."""
    return bool(
        organization is not None
        and organization.status == "active"
        and document is not None
        and document.record_state == "active"
        and version is not None
        and version.document_status == "active"
        and version.review_state == "approved"
        and (version.effective_from is None or version.effective_from <= now)
        and (version.effective_until is None or version.effective_until > now)
    )
