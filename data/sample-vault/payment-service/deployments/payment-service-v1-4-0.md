---
schema_version: 1
document_id: payment-service-deployment-v1-4-0
document_type: deployment
title: Payment Service Deployment v1.4.0
document_status: active
review_state: in_review
owner_team: payments-platform
provenance_type: synthetic
access_scope: organization
service_refs:
  - payment-service
environment: production
release_version: 1.4.0
deployed_at: "2026-10-01T09:00:00+07:00"
tags:
  - payment
  - deployment
  - production
---

# Payment Service Deployment v1.4.0

## Summary

Version 1.4.0 of Payment Service was deployed to the synthetic production environment.

## Changes

- Updated the database connection-pool configuration.
- Added transaction timeout metrics.
- Updated the service runtime configuration.

## Observed behavior

Several minutes after deployment, the service began returning HTTP 503 responses during increased request volume.

## Suspected change

The SQLAlchemy `pool_size` setting was reduced from 40 to 4 while `max_overflow` remained 0.

With overflow disabled, the application pool allowed at most four simultaneous checked-out connections per application instance. This change may have caused connection exhaustion under load.

## Rollback information

The previous synthetic release was version 1.3.2. No rollback was performed automatically.