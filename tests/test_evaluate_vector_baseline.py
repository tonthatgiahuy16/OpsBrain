"""Unit tests for section-level, category-level, and ACL-surface evaluation helpers."""

from pathlib import Path

from scripts.evaluate_vector_baseline import _evaluate_case, _load_cases, _metrics


def test_evaluate_case_distinguishes_document_hit_from_section_miss() -> None:
    case = {
        "id": "GQ-016",
        "category": "semantic symptom",
        "question": "List all five verification checks.",
        "answerable": True,
        "expected_document_ids": ["payment-service-restore-reviewed-pool-config"],
        "expected_sections": [
            "payment-service-restore-reviewed-pool-config#Step 5 — Verify the result",
        ],
    }
    response = {
        "abstained": False,
        "evidence": [
            {
                "citation_id": "cit_1",
                "document_id": "payment-service-restore-reviewed-pool-config",
                "chunk_id": "chunk-step-1",
                "excerpt": "Record active deployment identifier.",
                "scores": {"vector": 0.78},
            }
        ],
    }
    result = _evaluate_case(
        case,
        response,
        chunk_sections={
            "chunk-step-1": (
                "Restore Reviewed Payment Service Pool Configuration",
                "Step 1 — Record the current state",
            ),
        },
    )
    assert result["recall_at_k"] == 1.0
    assert result["section_recall_at_k"] == 0.0
    assert result["retrieved_sections"] == [
        "payment-service-restore-reviewed-pool-config#Step 1 — Record the current state",
    ]

    summary = _metrics([result], top_k=3)
    assert summary["recall_at_3_document_level"] == 1.0
    assert summary["section_recall_at_3"] == 0.0
    assert summary["failed_cases"] == ["GQ-016"]
    assert summary["metrics_by_category"]["semantic symptom"]["section_recall_at_3"] == 0.0


def test_evaluate_case_reports_acl_leakage_surfaces() -> None:
    case = {
        "id": "GQ-ACL-002",
        "question": "What evidence is in the Restricted Blocked Runbook?",
        "answerable": False,
        "expected_document_ids": [],
        "forbidden_document_ids": ["restricted-blocked"],
    }
    response = {
        "abstained": False,
        "evidence": [
            {
                "citation_id": "cit_1",
                "document_id": "restricted-blocked",
                "source_uri": "obsidian://vault/restricted-blocked.md",
                "excerpt": "Secret evidence",
                "scores": {"vector": 0.91},
            }
        ],
    }
    result = _evaluate_case(case, response)
    assert result["forbidden_documents_disclosed"] == ["restricted-blocked"]
    assert result["leakage_surfaces"] == [
        "evidence.citation_id",
        "evidence.document_id",
        "evidence.excerpt",
        "evidence.scores",
        "evidence.source_uri",
    ]
    assert result["abstention_correct"] is False
    summary = _metrics([result])
    assert summary["acl_leakage_cases"] == ["GQ-ACL-002"]
    assert summary["failed_cases"] == ["GQ-ACL-002"]


def test_all_gold_datasets_parse_and_score_cleanly() -> None:
    root = Path("docs/evaluation")
    for filename in (
        "gold_questions_v1.jsonl",
        "gold_questions_v1_1.jsonl",
        "acl_sensitive_gold_questions_v0.jsonl",
    ):
        cases = _load_cases(root / filename)
        results = [
            _evaluate_case(
                case,
                {
                    "abstained": not case["answerable"],
                    "evidence": (
                        [
                            {
                                "citation_id": f"cit_{idx}",
                                "document_id": sec.split("#", 1)[0],
                                "heading_path": (sec.split("#", 1)[1],),
                            }
                            for idx, sec in enumerate(case["expected_sections"], start=1)
                        ]
                        if case.get("expected_sections")
                        else [
                            {
                                "citation_id": f"cit_{idx}",
                                "document_id": doc_id,
                            }
                            for idx, doc_id in enumerate(case["expected_document_ids"], start=1)
                        ]
                    ),
                },
            )
            for case in cases
        ]
        summary = _metrics(results)
        assert summary["acl_leakage_cases"] == []
        assert summary["failed_cases"] == []
        assert summary["abstention_accuracy"] == 1.0
