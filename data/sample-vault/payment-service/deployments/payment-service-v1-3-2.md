---
schema_version: 1
document_id: payment-service-deployment-v1-3-2
document_type: deployment
title: Payment Service Deployment v1.3.2
document_status: active
review_state: in_review
owner_team: payments-platform
provenance_type: synthetic
access_scope: organization
service_refs:
  - payment-service
environment: production
release_version: 1.3.2
deployed_at: "2026-09-20T14:00:00+07:00"
tags:
  - payment
  - deployment
  - production
  - baseline
---

# Payment Service Deployment v1.3.2

## Summary

Version 1.3.2 of Payment Service was deployed to the synthetic production environment as the reviewed baseline before version 1.4.0.

## Configuration

The reviewed SQLAlchemy pool configuration used `pool_size: 40` and `max_overflow: 0`.

Connections were opened on demand. With overflow disabled, the application pool allowed at most 40 simultaneous checked-out connections per application instance.

## Observed behavior

Payment Service operated within the expected synthetic error-rate and connection-wait baselines after this deployment.

## Relationship to the incident

The configuration from version 1.3.2 was compared with version 1.4.0 during the HTTP 503 investigation.

Restoring the previous reviewed pool limit was followed by a reduction in HTTP 503 responses. This temporal result supports the connection-pool hypothesis but must be interpreted together with the incident evidence.

## Lifecycle note

This deployment record remains active as historical evidence even though a newer release was deployed later.