---
schema_version: 1
document_id: payment-service-deployment-v1-4-1
document_type: deployment
title: Payment Service Deployment v1.4.1
document_status: active
review_state: in_review
owner_team: payments-platform
provenance_type: synthetic
access_scope: organization
service_refs:
  - payment-service
environment: production
release_version: 1.4.1
deployed_at: "2026-10-01T09:30:00+07:00"
tags:
  - payment
  - deployment
  - production
  - hotfix
---

# Payment Service Deployment v1.4.1

## Summary

Version 1.4.1 of Payment Service was deployed as a synthetic hotfix during the HTTP 503 incident.

## Configuration change

The SQLAlchemy pool configuration restored `pool_size` from 4 to 40 while keeping `max_overflow` at 0.

No unrelated application behavior was intentionally changed by this synthetic hotfix.

## Reason for change

Evidence collected during the incident showed:

- HTTP 503 responses increased after deployment version 1.4.0.
- Connection checkout wait increased.
- Version 1.4.0 reduced `pool_size` from 40 to 4.
- The previous reviewed configuration used `pool_size: 40` and `max_overflow: 0`.

## Observed result

HTTP 503 responses returned to the expected synthetic baseline by `2026-10-01T09:38:00+07:00`.

The temporal improvement supports the pool-exhaustion hypothesis but does not establish causation by itself.

## Governance note

This record documents a synthetic deployment event. It does not prove that an automated approval workflow or production deployment system exists.

The note remains `in_review` until an independent human reviewer accepts its content.