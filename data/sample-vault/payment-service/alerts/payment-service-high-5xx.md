---
schema_version: 1
document_id: payment-service-high-5xx-alert
document_type: alert
title: Payment Service High 5xx Rate
document_status: active
review_state: in_review
owner_team: payments-platform
provenance_type: synthetic
access_scope: organization
service_refs:
  - payment-service
source_system: prometheus
alert_rule_ref: payment-service-high-5xx-rate
severity: critical
tags:
  - payment
  - alert
  - http-503
---

# Payment Service High 5xx Rate

## Summary

This synthetic alert detects an elevated rate of HTTP 5xx responses from Payment Service.

## Trigger condition

The alert fires when the proportion of HTTP 5xx responses exceeds 5 percent of Payment Service requests for five consecutive minutes.

## Observed event

The alert fired at `2026-10-01T09:07:00+07:00`, seven minutes after deployment version 1.4.0 began.

## Initial evidence

- Most failed requests returned HTTP 503.
- Request volume was higher than the normal baseline.
- Database connection wait time increased during the same interval.
- No automatic remediation was executed.

## Expected response

An operator should inspect Payment Service health, database connection-pool metrics and the most recent deployment before choosing a mitigation action.