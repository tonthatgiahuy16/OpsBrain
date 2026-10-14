---
schema_version: 1
document_id: payment-service-http-503-postmortem-2026-10-01
document_type: postmortem
title: Payment Service HTTP 503 Postmortem
document_status: active
review_state: in_review
owner_team: payments-platform
provenance_type: synthetic
access_scope: organization
incident_refs:
  - payment-service-http-503-2026-10-01
contributing_service_refs:
  - payment-service
tags:
  - payment
  - postmortem
  - http-503
  - database
---

# Payment Service HTTP 503 Postmortem

## Summary

Payment Service intermittently returned HTTP 503 responses for 31 minutes after deployment version 1.4.0 changed the database connection-pool configuration.

This postmortem describes a synthetic incident created for the OpsBrain sample vault. It contains no real customer or production data.

## Impact

Payment requests in the synthetic production environment intermittently failed between `2026-10-01T09:07:00+07:00` and `2026-10-01T09:38:00+07:00`.

## Root cause

Deployment version 1.4.0 reduced the SQLAlchemy `pool_size` setting from 40 to 4 while keeping `max_overflow` at 0.

During increased request volume, all four available connections were checked out. Additional requests waited for a connection and eventually timed out, after which Payment Service returned HTTP 503 in the synthetic scenario.

## Supporting evidence

- The 5xx rate increased seven minutes after deployment version 1.4.0.
- Database connection wait time increased during the incident.
- The deployed pool limit differed from the previous reviewed value.
- HTTP 503 responses returned to the expected synthetic baseline after the previous limit was restored.

## Contributing factors

- The pool configuration change did not receive a load-oriented validation.
- No automated check compared the new pool size with the reviewed baseline.
- The alert detected elevated 5xx responses but did not identify connection-pool saturation directly.
- The diagnosis procedure had not yet been documented as a reviewed runbook.

## What went well

- The high 5xx alert detected the failure shortly after deployment.
- The deployment timeline helped narrow the investigation.
- Engineers used read-only evidence before changing configuration.
- The previous configuration was available for restoration.

## What could be improved

- Configuration changes need explicit review and validation.
- Connection checkout wait time should be observable.
- Deployment evidence should be linked consistently to incident records.
- Mitigation actions and their approval should be recorded in the audit trail.

## Action items

1. Add validation for database connection-pool configuration.
2. Add an observable metric for connection checkout wait time.
3. Review and approve the Payment Service HTTP 503 diagnosis runbook.
4. Add a regression test covering constrained connection-pool capacity.
5. Require human approval before applying pool changes in the synthetic workflow.
6. Record post-change verification before closing an incident.

## Related documents

- Incident: [[incidents/payment-service-http-503]]
- Deployment: [[deployments/payment-service-v1-4-0]]
- Alert: [[alerts/payment-service-high-5xx]]
- Runbook: [[runbooks/payment-service-503-diagnosis]]