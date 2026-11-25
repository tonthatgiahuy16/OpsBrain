# ACL-sensitive baseline — gold questions v0 (2026-11-26)

- Dataset: `docs/evaluation/acl_sensitive_gold_questions_v0.jsonl` (5 cases,
  3 with `answerable: false`).
- Retrieval: vector-only, `evidence_only`, `top_k=5`,
  `QUERY_MIN_VECTOR_SCORE=0.25`.
- Embeddings: `nomic-embed-text`, 768 dimensions, configuration
  `acl-eval-nomic-embed-text-v1`.
- Corpus: isolated organization `acl-eval-org` with three restricted fixtures
  only (`restricted-allowed`, `restricted-blocked`, `restricted-team`).
- Raw run: [acl_baseline_2026-11-26.json](acl_baseline_2026-11-26.json).

## Metrics

| Metric | Result |
| --- | ---: |
| Document Recall@5 | 1.000 |
| Answerable cases with expected evidence | 1.000 |
| Citation accuracy (document proxy) | 1.000 |
| Abstention accuracy | 0.800 |
| ACL leakage cases | 0 |

Section recall is not scored because this dataset does not declare
`expected_sections`.

## Cases

| Case | Expected | Retrieved expected | Forbidden disclosed | Abstained |
| --- | --- | --- | --- | --- |
| GQ-ACL-001 (role allow) | `restricted-allowed` | `restricted-allowed` | none | false |
| GQ-ACL-002 (role blocks other role) | none | none | none | false |
| GQ-ACL-003 (revoked membership) | none | none | none | true |
| GQ-ACL-004 (team allow) | `restricted-team` | `restricted-team` | none | false |
| GQ-ACL-005 (non-member team) | none | none | none | true |

## ACL gate

- No forbidden document appeared in `evidence.document_id`, `evidence.excerpt`,
  `evidence.scores` or `evidence.citation_id`.
- `restricted-allowed` is returned only to the active `incident-reader`
  principal, and `restricted-team` only to the active `incident-response`
  member.
- Revoked role membership and non-member team membership both abstain with an
  empty evidence list, so no identifier, score or citation for a blocked
  document reaches the response.

GQ-ACL-002 is the only failed case, and it fails on abstention, not access
control. The question targets `restricted-blocked`, which the principal cannot
read; the vector search still matches the permitted near-duplicate
`restricted-allowed` at a score of `0.529` (above the `0.25` evidence
threshold), so the endpoint returns that permitted evidence instead of
abstaining. The response contains no data from the blocked document. This is a
relevance/threshold behaviour on a synthetic corpus whose three fixtures share
one template, not a leaked ACL boundary.

## Gate decision (2026-11-26)

- The ACL gate is accepted: zero leakage cases, correct allowed evidence for
  the role and team cases, and abstention for revoked and non-member cases.
- `GQ-ACL-002` is recorded as a known abstention/relevance limitation of this
  dataset, not an ACL failure and not a reason to change the production
  threshold. The follow-up work is a separate relevance/answerability slice
  (for example a score-margin or answerability check), evaluated against its
  own dataset rather than by loosening this gate.

## Reproducing

Identity setup and reindex run on the local Compose database; tokens stay in a
temporary directory outside the repository.

```bash
python scripts/load_acl_eval_vault.py --dimensions 768
python scripts/run_reindex_workers.py
python scripts/evaluate_vector_baseline.py \
  --dataset docs/evaluation/acl_sensitive_gold_questions_v0.jsonl \
  --tokens-json /tmp/opsbrain-acl-eval/tokens.json \
  --token-map /tmp/opsbrain-acl-eval/token_map.json \
  --dataset-version acl-sensitive-v0 \
  --output docs/evaluation/runs/acl_baseline_2026-11-26.json
```

`load_acl_eval_vault.py` uses the owner/migration DSN for identity rows and the
worker DSN for document ingestion and review approval, mirroring
`load_sample_vault.py`.

## Provenance

- Base commit: `7eab847`.
- Local changes required for this run: `scripts/load_acl_eval_vault.py`,
  `scripts/evaluate_vector_baseline.py` (worker-session section enrichment and
  `--token-map`), `tests/test_acl_database_enforcement.py`, and migration
  `b3e7c9a1d5f2` (worker `SELECT` on `teams` and
  `principal_team_memberships`).
- Database revision after the run: `b3e7c9a1d5f2` on `opsbrain` and
  `opsbrain_test`.
- This is a reproducible synthetic baseline, not a generalization or
  production-quality claim. Full-text, RRF and graph expansion are outside this
  run.
