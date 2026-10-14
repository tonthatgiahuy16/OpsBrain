---
schema_version: 1
document_id: payment-service-restore-reviewed-pool-config
document_type: runbook
title: Restore Reviewed Payment Service Pool Configuration
document_status: active
review_state: in_review
owner_team: payments-platform
provenance_type: synthetic
source_refs:
  - https://docs.sqlalchemy.org/en/20/core/pooling.html
access_scope: organization
service_refs:
  - payment-service
runbook_kind: recovery
tags:
  - payment
  - runbook
  - recovery
  - connection-pool
---

# Restore Reviewed Payment Service Pool Configuration

## Purpose

Use this synthetic recovery runbook when approved evidence shows that an unsafe SQLAlchemy connection-pool change is contributing to Payment Service failures.

This procedure has not been executed against a real production Payment Service.

## Preconditions

Before proposing recovery, confirm that:

- The active deployment changed `pool_size` or `max_overflow`.
- Application or database evidence supports connection-pool exhaustion.
- The last reviewed configuration is known.
- PostgreSQL capacity has been considered.
- A rollback condition has been defined.

If these conditions are not met, stop and continue diagnosis.

## Step 1 — Record the current state

Record:

- Active deployment identifier.
- Current `pool_size`.
- Current `max_overflow`.
- Connection checkout wait behavior.
- HTTP 5xx rate.
- Database session count.
- Time range of the evidence.

Do not record credentials, unrestricted raw logs or unauthorized session details.

## Step 2 — Prepare the proposed change

The proposal must include:

- Current configuration.
- Last reviewed configuration.
- Supporting evidence.
- Expected effect.
- Deployment method.
- Verification checks.
- Rollback condition.

For the synthetic HTTP 503 scenario, the proposed change is to restore `pool_size` from 4 to 40 while keeping `max_overflow` at 0.

## Step 3 — Obtain approval

An authorized human must review the proposal and approve or reject it before execution.

The approving principal, decision time and approved values must be recorded in the audit trail.

Editing this runbook or its frontmatter does not grant permission to execute the change.

## Step 4 — Apply through the normal deployment path

After approval:

- Apply only the approved configuration values.
- Use the normal reviewed deployment mechanism.
- Do not edit the database directly.
- Do not silently introduce unrelated changes.
- Keep the previous deployment available as the rollback reference.

This sample vault does not define or claim a production deployment command.

## Step 5 — Verify the result

After the approved change, verify:

- HTTP 503 rate returns to the expected baseline.
- Connection checkout wait decreases.
- No connection acquisition timeout appears.
- PostgreSQL session count remains within reviewed capacity.
- No new failure pattern appears.

Record both successful and unsuccessful observations.

## Step 6 — Obtain result acceptance

A human reviewer must compare the observed result with the approved proposal.

The reviewer must explicitly accept or reject the result before the recovery task is considered complete.

## Rollback condition

Stop or roll back through the approved deployment process when:

- HTTP 503 errors do not decrease.
- Database capacity is threatened.
- A new error pattern appears.
- The applied values differ from the approved proposal.
- Required verification evidence is unavailable.

## Related documents

- Diagnosis runbook: `payment-service-503-diagnosis`
- Incident: `payment-service-http-503-2026-10-01`
- Baseline deployment: `payment-service-deployment-v1-3-2`
- Problematic deployment: `payment-service-deployment-v1-4-0`