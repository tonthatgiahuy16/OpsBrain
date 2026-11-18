# Vector-only baseline — 2026-11-18

## Run identity

- Dataset: `gold-questions-v1`, 12 English questions; source corpus at `246451ecfbfd1a7f68993b6caedfc53b6175b19f`.
- Dataset SHA-256: `aa75e7eaf498907dd2c720eb2261cc4b78609e2185e1fd333e648a9706da7fcf`.
- Endpoint: `POST /api/v1/queries` through the FastAPI ASGI application.
- Retrieval: vector-only, `evidence_only`, `top_k=5`.
- Active profile: `sample-vault-nomic-embed-text-v1`, `nomic-embed-text`, revision `null`, 768 dimensions.
- Threshold: `QUERY_MIN_VECTOR_SCORE=0.25`.
- Runtime: Docker API container to host Ollama; all 12 `/api/embed` calls returned HTTP 200.
- Raw responses: [vector_baseline_2026-11-18.json](vector_baseline_2026-11-18.json), SHA-256 `c9a0f0fc1eb9e0b48476b61656188af865b4dcfb18f54bdea1512f8c8703f8f`.

## Measurement conventions

- **Recall@5** is document-level coverage: expected `document_id`s retrieved / expected `document_id`s, over the ten answerable cases. Heading-level scoring is unavailable because the current citation contract returns chunk and document IDs, not source headings.
- **Citation accuracy** is document-level citation precision: returned citations whose `document_id` is in the case's expected document set / all returned citations for answerable cases. It is a strict proxy; it treats relevant-but-not-gold support documents as incorrect.
- **Abstention accuracy** is whether the endpoint abstained exactly for the two cases marked unanswerable and returned evidence for the ten answerable cases.

## Results

| Metric | Result |
| --- | ---: |
| Recall@5, document-level | 84.62% (11 / 13 expected documents) |
| Answerable cases with at least one expected document | 100% (10 / 10) |
| Citation accuracy, document-level proxy | 34.00% (17 / 50 citations) |
| Abstention accuracy | 83.33% (10 / 12) |
| End-to-end latency | mean 265.67 ms; min 147 ms; max 902 ms |

## Per-case results

| ID | Expected document coverage | Endpoint status | Observation |
| --- | --- | --- | --- |
| GQ-001 | 1 / 1 | evidence returned | Expected deployment appears rank 4. |
| GQ-002 | 2 / 2 | evidence returned | Both alert and incident appear. |
| GQ-003 | 1 / 2 | evidence returned | Diagnosis appears; expected postmortem is absent. |
| GQ-004 | 1 / 2 | evidence returned | v1.4.1 appears; expected postmortem is absent. |
| GQ-005 | 1 / 1 | evidence returned | Diagnosis appears. |
| GQ-006 | 1 / 1 | evidence returned | Target alert is rank 1 at score 0.7914 despite `alert_rule_ref` being frontmatter-only. |
| GQ-007 | 1 / 1 | evidence returned | Expected v1.3.2 appears, but not as rank 1. |
| GQ-008 | 1 / 1 | evidence returned | Checkout Service appears. |
| GQ-009 | 1 / 1 | evidence returned | Target saturation alert is rank 1. |
| GQ-010 | 1 / 1 | evidence returned | Diagnosis runbook is rank 1. |
| GQ-011 | n/a | evidence returned | **False non-abstention**; top score 0.6790. |
| GQ-012 | n/a | evidence returned | **False non-abstention**; top score 0.6458. |

## Interpretation and next decision

- This establishes a reproducible baseline only; it does not establish generalization because the gold set and corpus are both synthetic and no holdout set exists.
- The fixed 0.25 threshold does not distinguish the two unanswerable questions from corpus-related questions. Raising it cannot be decided from this set alone because answerable scores overlap and must be evaluated on a separate calibration/holdout set.
- GQ-003 and GQ-004 are the only expected-document recall misses. They are candidates for a later hybrid/RRF comparison, not grounds to tune this vector baseline in place.
- Low strict citation precision shows that evidence-only top-5 returns supporting-but-not-gold chunks. Any answer mode should select or rerank citations before using the whole top-k as context.
