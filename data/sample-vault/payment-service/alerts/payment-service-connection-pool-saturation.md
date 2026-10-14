---
schema_version: 1
document_id: payment-service-connection-pool-saturation-alert
document_type: alert
title: Payment Service Connection Pool Saturation
document_status: active
review_state: in_review
owner_team: payments-platform
provenance_type: synthetic
source_refs:
  - https://docs.sqlalchemy.org/en/20/core/pooling.html
  - https://docs.sqlalchemy.org/en/20/core/events.html
access_scope: organization
service_refs:
  - payment-service
source_system: prometheus
alert_rule_ref: payment-service-connection-pool-saturation
severity: warning
tags:
  - payment
  - alert
  - database
  - connection-pool
---

# Payment Service Connection Pool Saturation

## Purpose

This proposed synthetic alert detects sustained database connection-pool saturation before it causes widespread HTTP 503 responses.

## Prerequisite

Payment Service must expose reviewed metrics for:

- Configured `pool_size`.
- Configured `max_overflow`.
- Currently checked-out connections.
- Connection checkout wait duration.

These metrics are planned for the synthetic scenario and have not been implemented or runtime-verified.

## Trigger condition

The warning alert fires when checked-out connections remain at or above 90 percent of the configured pool capacity for five consecutive minutes.

Pool capacity must be calculated from the reviewed `pool_size` and `max_overflow` configuration.

## Scenario status

This alert did not exist during the HTTP 503 incident on `2026-10-01`.

It was proposed as a postmortem action item and remains `in_review`. The sample vault does not claim that the alert rule has been deployed.

## Expected response

- Confirm whether connection checkout wait duration is increasing.
- Compare current pool configuration with the latest reviewed deployment.
- Review application logs for connection acquisition timeout.
- Follow `payment-service-503-diagnosis` when user impact or HTTP 503 errors are present.

## Safety rule

This alert must not automatically increase pool capacity, restart Payment Service or apply rollback. Any configuration change requires authorized human approval and post-change verification.