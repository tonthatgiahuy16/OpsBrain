---
schema_version: 1
document_id: payment-service-503-diagnosis
document_type: runbook
title: Diagnose Payment Service HTTP 503 Errors
document_status: active
review_state: in_review
owner_team: payments-platform
provenance_type: synthetic
source_refs:
  - https://docs.sqlalchemy.org/en/20/core/pooling.html
  - https://www.postgresql.org/docs/16/monitoring-stats.html
  - https://docs.docker.com/reference/cli/docker/compose/logs/
access_scope: organization
service_refs:
  - payment-service
runbook_kind: diagnosis
tags:
  - payment
  - runbook
  - http-503
  - database
---

# Diagnose Payment Service HTTP 503 Errors

## Purpose

Use this runbook to investigate elevated HTTP 503 responses from Payment Service when database connection exhaustion is suspected.

This runbook belongs to a synthetic demonstration scenario. Its commands and thresholds have not been verified against a real production Payment Service.

## Safety rules

- Do not change connection limits, restart services or perform rollback without human approval.
- Do not copy credentials, connection strings or restricted log content into an incident note.
- Prefer read-only checks during diagnosis.
- Stop and escalate when the observed evidence does not match this scenario.

## Step 1 — Confirm the impact

Confirm that the high 5xx alert is active and determine:

- When the error rate began increasing.
- Whether HTTP 503 is the dominant response code.
- Which Payment Service instances are affected.
- Whether the service health endpoint is responding.

## Step 2 — Correlate with recent changes

Check whether a deployment or configuration change occurred shortly before the error increase.

For this synthetic scenario, compare the incident timeline with:

- `payment-service-deployment-v1-4-0`
- `payment-service-high-5xx-alert`

A temporal correlation is evidence for investigation, but does not prove causation.

## Step 3 — Inspect application logs

For a Docker Compose deployment, inspect recent service logs:

```text
docker compose logs --no-color --timestamps --tail 200 payment-service
```

Look for:

- Connection acquisition timeout.
- Pool limit or overflow errors.
- Database connection failures.
- Increased request duration.
- Repeated HTTP 503 responses.

Do not place credentials or unrestricted raw logs in the knowledge vault.

## Step 4 — Inspect PostgreSQL activity

Use an authorized read-only database session to summarize current connection states:

```sql
SELECT state, count(*) AS connection_count
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY state
ORDER BY state;
```

Access to session details may be restricted by PostgreSQL permissions. Do not elevate privileges solely to complete this check.

## Step 5 — Review pool configuration

Compare the currently deployed configuration with the last reviewed configuration:

- `pool_size`
- `max_overflow`
- `pool_timeout`
- Number of application instances
- PostgreSQL connection capacity

A small application pool may cause requests to wait when all connections are checked out. Increasing the pool without checking database capacity may move the failure to PostgreSQL rather than resolve it.

## Decision

Connection-pool exhaustion is supported when several signals align:

- HTTP 503 errors began after a pool-related change.
- Application logs show connection acquisition timeout or pool-limit errors.
- Connection wait time increased.
- Restoring the previous reviewed configuration reduces the error rate.

If these signals do not align, stop using this runbook and continue with a broader HTTP 503 investigation.

## Human approval gate

Before changing configuration or restarting Payment Service, record:

- The proposed change.
- Supporting evidence.
- Expected effect.
- Rollback condition.
- Approving principal.

Execution may begin only after an authorized human approves the proposed action.

## Verification after an approved change

Verify that:

- HTTP 503 rate returns to the expected baseline.
- Connection acquisition timeout no longer appears.
- Database session count remains within the reviewed capacity.
- No new error pattern appears.
- The incident record contains the result and evidence.

A human reviewer must accept or reject the result before the remediation is considered complete.