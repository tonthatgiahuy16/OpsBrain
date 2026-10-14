---
schema_version: 1
document_id: checkout-service
document_type: service
title: Checkout Service
document_status: active
review_state: in_review
owner_team: commerce-platform
provenance_type: synthetic
access_scope: organization
criticality: high
dependency_refs:
  - payment-service
tags:
  - checkout
  - commerce
  - backend
---

# Checkout Service

## Purpose

Checkout Service coordinates the synthetic checkout workflow and requests payment authorization from Payment Service.

## Responsibilities

- Validate the synthetic checkout request.
- Calculate the final order amount.
- Request payment authorization.
- Return the checkout result to the caller.

## Operational context

During the HTTP 503 incident, some checkout requests failed because Payment Service was temporarily unavailable.

Checkout Service itself remained healthy and was not identified as the source of the failure.

## Dependencies

Checkout Service depends on Payment Service to authorize payment transactions.