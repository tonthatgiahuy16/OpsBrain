---
schema_version: 1
document_id: fraud-service
document_type: service
title: Fraud Service
document_status: active
review_state: in_review
owner_team: risk-platform
provenance_type: synthetic
access_scope: organization
criticality: high
tags:
  - fraud
  - risk
  - backend
---

# Fraud Service

## Purpose

Fraud Service evaluates synthetic payment transactions and returns a risk decision to Payment Service.

## Responsibilities

- Evaluate transaction risk signals.
- Return an allow, review or deny decision.
- Record synthetic risk-decision metadata.
- Expose service health information to authorized operators.

## Operational context

Payment Service calls Fraud Service before authorizing transactions that require risk evaluation.

Fraud Service was not identified as a cause of the HTTP 503 incident in the current synthetic scenario.

## Dependencies

No direct service dependency is defined in the initial sample.