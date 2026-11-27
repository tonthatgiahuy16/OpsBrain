# Hybrid vs Vector retrieval — gold questions v1.2 (2026-11-27)

- Dataset: `gold_questions_v1_2.jsonl` (7 cases: 2 `exact identifier`, 2 `multi-hop`, 2 `semantic symptom`, 1 `unanswerable`).
- Corpus and config: 12 approved `sample-vault` documents under `sample-vault-nomic-embed-text-ft-v1` (`chunk-v1`, `max_characters=1200`, `nomic-embed-text`, `dimensions=768`, `full_text_configuration=english`), `evidence_only`, `top_k=5`.
- Runtime: disposable `opsbrain_test` at `127.0.0.1:5433` (migrated to `c6d4e2f1a8b3`) and local Ollama `nomic-embed-text` at `127.0.0.1:11434`.
- Raw runs:
  - Vector: [vector_m6_gold_v1_2_2026-11-27.json](vector_m6_gold_v1_2_2026-11-27.json)
  - Hybrid (vector + PostgreSQL `english` FTS + RRF `k=60`): [hybrid_m6_gold_v1_2_2026-11-27.json](hybrid_m6_gold_v1_2_2026-11-27.json)

## Overall comparison

| Metric | Vector | Hybrid (RRF) |
| --- | ---: | ---: |
| Document Recall@5 | 1.000 | 1.000 |
| Section Recall@5 | 0.500 | 0.500 |
| Answerable cases with expected evidence | 1.000 | 1.000 |
| Citation accuracy (document proxy) | 0.400 | 0.400 |
| Abstention accuracy | 0.857 | 0.857 |
| ACL leakage cases | 0 | 0 |
| Total latency avg / max (ms) | 723.0 / 1851 | 567.4 / 835 |

## Metrics by category

| Category | Cases | Vector Doc Recall@5 | Hybrid Doc Recall@5 | Vector Sec Recall@5 | Hybrid Sec Recall@5 | Vector Abstention | Hybrid Abstention |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `exact identifier` | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| `multi-hop` | 2 | 1.000 | 1.000 | 0.333 | 0.333 | 1.000 | 1.000 |
| `semantic symptom` | 2 | 1.000 | 1.000 | 0.500 | 0.500 | 1.000 | 1.000 |
| `unanswerable` | 1 | — | — | — | — | 0.000 | 0.000 |

## Full-text participation, failed cases, and limitations

- **Full-text participation:** In `GQ-019` (`exact identifier`: `"SQLAlchemy pool_size max_overflow deployment 1.4.0"`), PostgreSQL FTS returned non-null `scores.full_text` (`0.009009` and `0.005525`) that fused with vector hits via RRF (`top_k=5` returned `payment-service-http-503-postmortem-2026-10-01` and `payment-service-http-503-2026-10-01` with both `vector` and `full_text` scores, alongside `payment-service-deployment-v1-4-0`). On the other 6 cases, `websearch_to_tsquery` (conjunctive across all non-stopword query terms) returned no FTS rows, so `hybrid` scored from vector rank + RRF alone.
- **Failed cases:** Both strategies failed the same 4 cases: `GQ-013`, `GQ-016`, `GQ-017` (section-level misses on multi-hop/multi-section questions), and `GQ-018` (`unanswerable` question retrieved chunks containing `pool_size` / `runbook` instead of abstaining).
- **Limitations:** On this 7-case synthetic corpus, `hybrid` matches `vector` on every accuracy metric (1.000 Document Recall@5 on both) and does **not** prove higher accuracy than vector-only. Latency difference (`723.0 ms` vs `567.4 ms`) is dominated by an initial cold Ollama call (`1851 ms` on `GQ-019` in the vector run). Direct FTS-only recovery when vector misses and HTTP/DB ACL exclusion are covered by automated tests in `tests/test_full_text_search.py`, `tests/test_hybrid_retrieval.py`, `tests/test_query_api.py`, and `tests/test_query_security.py`.
