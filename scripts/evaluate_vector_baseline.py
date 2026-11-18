"""Run the frozen EN gold set through the minimal vector-evidence endpoint."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from src.main import app


def _load_cases(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _evaluate_case(case: dict[str, object], response: dict[str, object]) -> dict[str, object]:
    expected = set(case["expected_document_ids"])
    evidence = response.get("evidence", [])
    cited = [item["document_id"] for item in evidence]
    forbidden = set(case.get("forbidden_document_ids", []))
    retrieved_expected = sorted(expected.intersection(cited))
    answerable = case["answerable"]
    abstained = response["abstained"]
    return {
        "id": case["id"],
        "answerable": answerable,
        "expected_document_ids": sorted(expected),
        "retrieved_expected_document_ids": retrieved_expected,
        "recall_at_k": len(retrieved_expected) / len(expected) if expected else None,
        "citation_correct": [document_id in expected for document_id in cited],
        "forbidden_document_ids": sorted(forbidden),
        "forbidden_documents_disclosed": sorted(forbidden.intersection(cited)),
        "abstention_correct": abstained is (not answerable),
        "response": response,
    }


def _metrics(cases: list[dict[str, object]]) -> dict[str, object]:
    answerable = [case for case in cases if case["answerable"]]
    expected_count = sum(len(case["expected_document_ids"]) for case in answerable)
    retrieved_count = sum(len(case["retrieved_expected_document_ids"]) for case in answerable)
    citation_flags = [
        flag for case in answerable for flag in case["citation_correct"]
    ]
    return {
        "recall_at_5_document_level": retrieved_count / expected_count,
        "answerable_cases_with_any_expected_evidence": sum(
            bool(case["retrieved_expected_document_ids"]) for case in answerable
        ) / len(answerable),
        "citation_accuracy_document_level": (
            sum(citation_flags) / len(citation_flags) if citation_flags else None
        ),
        "abstention_accuracy": sum(case["abstention_correct"] for case in cases) / len(cases),
        "acl_leakage_cases": [
            case["id"] for case in cases if case["forbidden_documents_disclosed"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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
    results = []
    with TestClient(app) as client:
        for case in _load_cases(args.dataset):
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
            results.append(_evaluate_case(case, http_response.json()))

    output = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": "gold-questions-v1",
        "strategy": "vector",
        "response_mode": "evidence_only",
        "top_k": args.top_k,
        "cases": results,
        "metrics": _metrics(results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
