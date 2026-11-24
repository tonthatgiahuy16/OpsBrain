"""Run a gold dataset through the minimal vector-evidence endpoint."""

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.database import AsyncSessionLocal
from src.main import app
from src.models.storage import Chunk


def _load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _fetch_chunk_sections(chunk_ids: set[str]) -> dict[str, tuple[str, ...]]:
    """Resolve chunk UUID strings to their stored heading_path tuples."""
    parsed_ids: list[UUID] = []
    for raw_id in chunk_ids:
        try:
            parsed_ids.append(UUID(raw_id))
        except ValueError:
            continue
    if not parsed_ids:
        return {}
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Chunk.id, Chunk.heading_path).where(Chunk.id.in_(parsed_ids))
        )
        return {str(chunk_id): tuple(heading_path or ()) for chunk_id, heading_path in rows}


def _resolve_section_ref(
    item: dict[str, Any],
    chunk_sections: dict[str, tuple[str, ...]] | None,
) -> str | None:
    """Build `<document_id>#<leaf_heading>` for an evidence item when heading_path is known."""
    document_id = item.get("document_id")
    if not isinstance(document_id, str) or not document_id:
        return None
    heading_path = item.get("heading_path")
    if not heading_path and chunk_sections and item.get("chunk_id"):
        heading_path = chunk_sections.get(str(item["chunk_id"]))
    if not heading_path:
        return None
    return f"{document_id}#{heading_path[-1]}"


def _detect_leakage_surfaces(
    evidence: list[dict[str, Any]],
    forbidden: set[str],
) -> tuple[list[str], list[str]]:
    """Return disclosed forbidden document IDs and the API response surfaces they appeared on."""
    if not forbidden:
        return [], []
    disclosed: set[str] = set()
    surfaces: set[str] = set()
    for item in evidence:
        doc_id = item.get("document_id")
        source_uri = item.get("source_uri") or ""
        if doc_id in forbidden:
            disclosed.add(doc_id)
            surfaces.add("evidence.document_id")
            if item.get("citation_id") is not None:
                surfaces.add("evidence.citation_id")
            if item.get("excerpt") is not None:
                surfaces.add("evidence.excerpt")
            if item.get("scores") is not None:
                surfaces.add("evidence.scores")
        for forbidden_id in forbidden:
            if forbidden_id and forbidden_id in source_uri:
                disclosed.add(forbidden_id)
                surfaces.add("evidence.source_uri")
    return sorted(disclosed), sorted(surfaces)


def _evaluate_case(
    case: dict[str, Any],
    response: dict[str, Any],
    *,
    chunk_sections: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    expected = set(case["expected_document_ids"])
    expected_sections = set(case.get("expected_sections", []))
    evidence = response.get("evidence", [])
    cited = [item["document_id"] for item in evidence]
    retrieved_sections = [
        ref for item in evidence
        if (ref := _resolve_section_ref(item, chunk_sections)) is not None
    ]
    forbidden = set(case.get("forbidden_document_ids", []))
    retrieved_expected = sorted(expected.intersection(cited))
    retrieved_expected_sections = sorted(expected_sections.intersection(retrieved_sections))
    disclosed, leakage_surfaces = _detect_leakage_surfaces(evidence, forbidden)
    answerable = case["answerable"]
    abstained = response["abstained"]
    return {
        "id": case["id"],
        "category": case.get("category"),
        "answerable": answerable,
        "expected_document_ids": sorted(expected),
        "retrieved_expected_document_ids": retrieved_expected,
        "recall_at_k": len(retrieved_expected) / len(expected) if expected else None,
        "expected_sections": sorted(expected_sections),
        "retrieved_sections": retrieved_sections,
        "retrieved_expected_sections": retrieved_expected_sections,
        "section_recall_at_k": (
            len(retrieved_expected_sections) / len(expected_sections)
            if expected_sections else None
        ),
        "citation_correct": [document_id in expected for document_id in cited],
        "forbidden_document_ids": sorted(forbidden),
        "forbidden_documents_disclosed": disclosed,
        "leakage_surfaces": leakage_surfaces,
        "abstention_correct": abstained is (not answerable),
        "response": response,
    }


def _group_metrics(cases: list[dict[str, Any]], *, top_k: int = 5) -> dict[str, Any]:
    answerable = [case for case in cases if case["answerable"]]
    expected_count = sum(len(case["expected_document_ids"]) for case in answerable)
    retrieved_count = sum(len(case["retrieved_expected_document_ids"]) for case in answerable)
    expected_section_count = sum(len(case["expected_sections"]) for case in answerable)
    retrieved_section_count = sum(len(case["retrieved_expected_sections"]) for case in answerable)
    citation_flags = [
        flag for case in answerable for flag in case["citation_correct"]
    ]
    return {
        "case_count": len(cases),
        f"recall_at_{top_k}_document_level": (
            retrieved_count / expected_count if expected_count else None
        ),
        f"section_recall_at_{top_k}": (
            retrieved_section_count / expected_section_count
            if expected_section_count else None
        ),
        "answerable_cases_with_any_expected_evidence": (
            sum(bool(case["retrieved_expected_document_ids"]) for case in answerable)
            / len(answerable)
            if answerable else None
        ),
        "citation_accuracy_document_level": (
            sum(citation_flags) / len(citation_flags) if citation_flags else None
        ),
        "abstention_accuracy": (
            sum(case["abstention_correct"] for case in cases) / len(cases)
            if cases else None
        ),
        "acl_leakage_cases": [
            case["id"] for case in cases if case["forbidden_documents_disclosed"]
        ],
    }


def _failed_case_ids(cases: list[dict[str, Any]]) -> list[str]:
    failed: list[str] = []
    for case in cases:
        doc_miss = case["answerable"] and (
            len(case["retrieved_expected_document_ids"]) < len(case["expected_document_ids"])
        )
        section_miss = case["answerable"] and (
            len(case["retrieved_expected_sections"]) < len(case["expected_sections"])
        )
        if not case["abstention_correct"] or case["forbidden_documents_disclosed"] or doc_miss or section_miss:
            failed.append(case["id"])
    return failed


def _metrics(cases: list[dict[str, Any]], *, top_k: int = 5) -> dict[str, Any]:
    summary = _group_metrics(cases, top_k=top_k)
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        if case.get("category"):
            by_category[str(case["category"])].append(case)
    if by_category:
        summary["metrics_by_category"] = {
            category: _group_metrics(group, top_k=top_k)
            for category, group in sorted(by_category.items())
        }
    summary["failed_cases"] = _failed_case_ids(cases)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-version", default="gold-questions-v1")
    parser.add_argument("--token", help="Default opaque token for cases without principal_role.")
    parser.add_argument(
        "--tokens-json", type=Path,
        help="JSON object mapping each case principal_role to an opaque token.",
    )
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    tokens_by_role = (
        json.loads(args.tokens_json.read_text(encoding="utf-8"))
        if args.tokens_json else {}
    )
    cases = _load_cases(args.dataset)
    raw_responses: list[dict[str, Any]] = []
    chunk_ids: set[str] = set()
    with TestClient(app) as client:
        for case in cases:
            token = tokens_by_role.get(case.get("principal_role"), args.token)
            if not isinstance(token, str) or not token:
                raise ValueError(f"no token configured for {case['id']}")
            http_response = client.post(
                "/api/v1/queries",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "question": case["question"],
                    "response_mode": "evidence_only",
                    "top_k": args.top_k,
                },
            )
            http_response.raise_for_status()
            payload = http_response.json()
            raw_responses.append(payload)
            for item in payload.get("evidence", []):
                if item.get("chunk_id") and not item.get("heading_path"):
                    chunk_ids.add(str(item["chunk_id"]))

    chunk_sections = asyncio.run(_fetch_chunk_sections(chunk_ids)) if chunk_ids else {}
    results = [
        _evaluate_case(case, payload, chunk_sections=chunk_sections)
        for case, payload in zip(cases, raw_responses, strict=True)
    ]

    output = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": args.dataset_version,
        "strategy": "vector",
        "response_mode": "evidence_only",
        "top_k": args.top_k,
        "cases": results,
        "metrics": _metrics(results, top_k=args.top_k),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
