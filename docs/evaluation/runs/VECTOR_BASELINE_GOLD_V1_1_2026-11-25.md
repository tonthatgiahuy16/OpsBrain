# Vector baseline — gold questions v1.1 (2026-11-25)

- Dataset: `gold_questions_v1_1.jsonl` (5 cases).
- Retrieval: vector-only, `evidence_only`, `top_k=5`.
- Runtime: host evaluator with PostgreSQL at `127.0.0.1:5433` and local
  Ollama `nomic-embed-text` at `127.0.0.1:11434`.
- Raw run: [vector_baseline_gold_v1_1_2026-11-25.json](vector_baseline_gold_v1_1_2026-11-25.json).

| Metric | Result |
| --- | ---: |
| Document Recall@5 | 1.000 |
| Section Recall@5 | 0.375 |
| Answerable cases with expected evidence | 1.000 |
| Citation accuracy (document proxy) | 0.450 |
| Abstention accuracy | 0.800 |
| ACL leakage cases | 0 |

The failed cases are `GQ-013`, `GQ-016`, `GQ-017`, and `GQ-018`. The result
is a reproducible vector-only baseline on synthetic material, not a
generalization or production-quality claim. ACL-specific gold cases still need
persistent, token-mapped ACL fixture principals before they can be evaluated
end-to-end; the database-level negative RLS tests cover direct SQL isolation.
