# OpsBrain Project Specification

**Status:** Active baseline

## 1. Purpose

OpsBrain is a prototype knowledge infrastructure for information technology operations teams. It converts Obsidian notes, operational documents, and monitoring evidence into searchable, versioned, and access-controlled knowledge.

The system focuses on retrieving valid evidence rather than acting as a general-purpose chatbot. Responses must use current and authorized sources, provide traceable citations, and abstain when sufficient evidence is unavailable.

## 2. Problem statement

Operational knowledge is often distributed across service records, incident reports, postmortems, runbooks, deployment histories, logs, and personal notes. During an incident, engineers may spend significant time finding the correct document, checking whether it is still valid, and connecting symptoms with related services and previous remediation steps.

OpsBrain addresses the following question:

How can an operations team retrieve relevant and authorized evidence for an incident, while avoiding outdated documents, unauthorized content, and answers unsupported by source material?

## 3. Goals

OpsBrain aims to:

- Ingest Markdown documents from an Obsidian vault through a validated data contract.
- Track document creation, unchanged replay, updates, deletion, and version history.
- Use PostgreSQL as the system of record for source data and processing state.
- Maintain rebuildable full-text, vector, and relationship-based derived data.
- Support vector retrieval, PostgreSQL full-text search, and hybrid retrieval using Reciprocal Rank Fusion.
- Enforce access control before ranking, context construction, and citation.
- Return evidence and source references that users can verify.
- Abstain when available evidence is insufficient, outdated, unapproved, or unauthorized.
- Use local Ollama models for embeddings and answer generation where applicable.
- Evaluate retrieval methods under reproducible configurations and clearly report limitations.

## 4. Users and use cases

The primary users of OpsBrain are:

- Site Reliability Engineering, DevOps, and backend engineering teams.
- Technical support engineers investigating system behavior.
- Technical leads reviewing operational history and current documentation.
- New team members learning how services, incidents, and runbooks are connected.

The main use cases are:

- Investigating an incident by finding similar incidents and applicable runbooks.
- Searching by symptoms, service identifiers, deployment identifiers, or error codes.
- Connecting services, deployments, incidents, alerts, postmortems, and remediation guidance.
- Asking questions within the context of the currently opened Obsidian note.
- Verifying an answer through citations to authorized source documents.
- Abstaining when the available evidence is insufficient, outdated, unapproved, or inaccessible.

## 5. Scope

### In scope

- Ingesting Markdown documents and validating their frontmatter.
- Processing document creation, unchanged replay, updates, deletion, and version history.
- Recording provenance, review state, lifecycle state, temporal validity, and access-control metadata.
- Storing source data and processing state in PostgreSQL.
- Maintaining rebuildable chunks, embeddings, full-text indexes, vector indexes, and bounded relationships.
- Supporting vector, full-text, and hybrid retrieval.
- Providing FastAPI endpoints for ingestion, retrieval, document state, and system health.
- Applying access control before ranking, context construction, and citation.
- Generating local embeddings and evidence-grounded answers through Ollama where applicable.
- Providing a minimal Obsidian integration through the FastAPI boundary.
- Testing ingestion lifecycle, retrieval behavior, access control, and reproducibility.

### Out of scope

- Autonomous incident remediation or execution of production commands.
- Fine-tuning language or embedding models.
- Kubernetes, Terraform, or cloud production deployment.
- A production-grade multi-tenant software-as-a-service platform.
- Real-time collection of organization-wide email, chat, or customer relationship management data.
- An unrestricted knowledge graph that expands without explicit depth, relationship, or document limits.
- Claims of reduced production incident resolution time without real operational trials.

## 6. Architecture boundaries

OpsBrain uses the following component boundaries:

- **Obsidian** is the authoring and reading interface for knowledge documents. The plugin communicates only with FastAPI and must not connect directly to PostgreSQL or Ollama.
- **FastAPI** is the service boundary for ingestion, retrieval, document state, identity context, citations, and system health.
- **PostgreSQL** is the system of record for source documents, versions, processing state, access-control data, and audit events.
- **pgvector and PostgreSQL full-text search** provide rebuildable retrieval indexes derived from approved source documents.
- **Ollama** provides local embedding and answer-generation capabilities. Its outputs do not replace source documents or authorization checks.
- **Derived data**, including chunks, embeddings, search indexes, and relationship edges, must be reproducible from source documents and recorded configuration.
- **Access control** is enforced by the backend before candidate ranking, context construction, answer generation, and citation.

No client component may bypass the API boundary to read unrestricted database records or invoke models with unauthorized context.

## 7. Data lifecycle

A document moves through the following lifecycle:

1. Receive a Markdown document and its frontmatter from an approved source.
2. Validate required fields, field types, provenance, lifecycle state, review state, temporal validity, relationships, and access-control metadata.
3. Resolve the stable document identifier and calculate a canonical content hash.
4. Treat an unchanged replay as idempotent and avoid creating duplicate active versions or derived records.
5. Create a new version when the body or version-relevant frontmatter changes semantically.
6. Create stable chunks and record their relationship to the source document version.
7. Generate embeddings and update full-text, vector, and bounded relationship indexes.
8. Record ingestion status, failure details, retry state, and audit events.
9. Invalidate obsolete derived data when a document is updated, deleted, revoked, or otherwise becomes ineligible.
10. Support retry and full index rebuild without changing the resulting logical state.

Source documents and version history remain authoritative. Chunks, embeddings, indexes, and relationship edges are derived data and must be safe to recreate.

## 8. Retrieval and access control

Only documents that are active, approved, temporally eligible, and accessible to the authenticated principal may participate in retrieval.

The retrieval path must follow these rules:

- Every document belongs to an explicit organization and uses a restricted access scope. The baseline does not provide a public or implicit default scope.
- User, team, and role permissions are resolved from authenticated backend identity rather than trusted directly from document frontmatter.
- Access control is applied before candidate ranking, graph expansion, context construction, answer generation, and citation.
- Vector retrieval uses similarity search over authorized and eligible chunks.
- Full-text retrieval supports identifiers, error codes, service names, and exact operational terms.
- Hybrid retrieval combines vector and full-text rankings through Reciprocal Rank Fusion.
- Relationship expansion is bounded by allowed relationship types, traversal depth, document count, and access control.
- Results expose only authorized evidence, metadata, scores, relationships, and citations.
- If no eligible evidence remains after filtering, the system must abstain instead of generating an unsupported answer.

Frontmatter provides access-control metadata as input. It cannot grant permissions, approve a document, or authorize its own lifecycle transition.

## 9. Evaluation and evidence

OpsBrain evaluates retrieval configurations under controlled and reproducible conditions.

The evaluation process must:

- Compare vector-only, hybrid, and hybrid-with-bounded-relationship retrieval on the same document corpus.
- Keep the dataset version, chunking rules, embedding model, model configuration, access context, and hardware configuration recorded.
- Use a reviewed gold dataset containing questions, expected evidence identifiers, answerability, and allowed access roles.
- Measure retrieval quality with appropriate metrics such as Recall at k, Mean Reciprocal Rank, and normalized Discounted Cumulative Gain.
- Measure answer behavior through evidence support, citation correctness, answerability, and abstention correctness.
- Require zero unauthorized document, chunk, relationship, context, or citation leakage in the current access-control test suite.
- Record latency measurements such as P50 and P95 together with the tested hardware and configuration.
- Preserve raw run results, configuration, model identifiers, and failure analysis needed to interpret or reproduce the experiment.

Targets and expected outcomes must be identified as goals until runtime or experiment evidence demonstrates that they were achieved.

## 10. Delivery milestones

Delivery is organized around observable acceptance gates:

1. Define project scope, shared contracts, source-of-truth boundaries, and Evidence policy.
2. Verify the FastAPI, PostgreSQL, pgvector, Docker Compose, configuration, health, and test foundation.
3. Create a linked sample vault and implement frontmatter validation.
4. Implement the PostgreSQL schema and complete ingestion lifecycle.
5. Deliver a vector retrieval vertical slice with source evidence and citations.
6. Enforce access control throughout the retrieval and answer path.
7. Add PostgreSQL full-text search and hybrid retrieval through Reciprocal Rank Fusion.
8. Add bounded relationship expansion without bypassing access control.
9. Expand the reviewed corpus and run reproducible retrieval evaluations.
10. Integrate the minimal Obsidian plugin through FastAPI.
11. Complete reliability, operations, documentation, and portfolio release requirements.

A milestone is complete only when its acceptance behavior is demonstrated by reproducible runtime output, test output, migration results, query results, or evaluation artifacts. Existing code or documentation alone is not completion evidence.

## 11. Known limitations

- OpsBrain is a prototype and is not a production incident-management platform.
- The planned corpus primarily uses synthetic scenarios and permitted public sources rather than confidential enterprise data.
- Evaluation results apply only to the recorded corpus, models, configuration, access context, and hardware.
- The project does not claim reduced production incident resolution time without real operational trials.
- Relationship data is a controlled derived layer and does not represent complete organizational knowledge.
- Local model quality and latency depend on available hardware and the selected Ollama models.
- The Obsidian integration is intentionally minimal and is not intended to replace a complete knowledge-management product.
- Planned capabilities must not be described as implemented or verified until matching Evidence exists.

## 12. Related documents

- [Repository overview and setup](../README.md)
- [Document frontmatter contract](contracts/document-frontmatter.md)
- [Runtime verification and audit trail](../AUDIT.md)

This Markdown file is the canonical public project specification and supersedes the legacy Word document previously stored at the repository root.
