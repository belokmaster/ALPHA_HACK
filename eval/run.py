import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from contracts.v1 import Evidence
from eval.cases.gold_v1 import GoldEvidenceGroup, GoldRetrievalQuery

logger = logging.getLogger(__name__)


def version_ids(logical_ids):
    from app.ingestion.pdf.pipeline import document_id, load_corpus_config

    config = load_corpus_config()
    ids = set(logical_ids)
    for entry in config["documents"].values():
        if entry["doc_id"] in ids:
            ids.add(document_id(entry, config))
    return ids


def _matches_group(ev: Evidence, group: GoldEvidenceGroup) -> bool:
    return ev.document_version_id in version_ids(group.acceptable_doc_ids) and (
        not group.acceptable_pages or ev.page in group.acceptable_pages
    )


def compute_query_recall_at_k(
    found_evidence: list[Evidence], query: GoldRetrievalQuery, k: int = 5
) -> dict:
    top_k = found_evidence[:k]
    found_doc_ids = {e.document_version_id for e in top_k}
    leaked_docs = sorted(found_doc_ids.intersection(version_ids(query.must_exclude_doc_ids)))

    if not query.answerable:
        # Раздел 8.3 плана: фоновые кандидаты допустимы, утечка запрещенных/будущих документов — нет
        return {
            "query_id": query.query_id,
            "event_id": query.event_id,
            "split": query.split,
            "answerable": False,
            "top_k_count": len(top_k),
            "no_leakage_clean": len(leaked_docs) == 0,
            "future_or_retro_leakage": leaked_docs,
        }

    total_groups = len(query.gold_groups)
    matched_groups = sum(
        1 for grp in query.gold_groups if any(_matches_group(ev, grp) for ev in top_k)
    )
    recall_at_k = matched_groups / total_groups if total_groups > 0 else 1.0
    precision_at_k = matched_groups / len(top_k) if top_k else 0.0

    return {
        "query_id": query.query_id,
        "event_id": query.event_id,
        "split": query.split,
        "is_paraphrase": query.is_paraphrase,
        "answerable": True,
        "recall_at_5": round(recall_at_k, 4),
        "precision_at_5": round(precision_at_k, 4),
        "matched_groups": matched_groups,
        "total_groups": total_groups,
        "found_doc_ids_at_5": [e.document_version_id for e in top_k],
        "future_or_retro_leakage": leaked_docs,
    }


def quality_gate(
    test_recalls,
    *,
    leakage=0,
    mode_exercised=False,
    gold_ready=False,
    no_answer_attribution_verified=False,
):
    return (
        len(test_recalls) == 10
        and mean(test_recalls) >= 0.85
        and leakage == 0
        and mode_exercised
        and gold_ready
        and no_answer_attribution_verified
    )


async def compare_diagnostics(engine, encoder, context):
    """All modes share one snapshot, data and fixed diagnostic queries. No test tuning."""
    import time

    from app.ingestion.pdf.pipeline import document_id, load_corpus_config
    from app.retrieval.service import HybridRetriever, RetrievalPolicy
    from contracts.v1 import AnalysisRequest

    config = load_corpus_config()
    retriever = HybridRetriever(engine, encoder, policy=RetrievalPolicy(allow_historical=True))
    # Document titles are technical coverage probes, not independent gold answers.
    queries = [
        {"id": e["doc_id"], "text": e["title"], "document_id": document_id(e, config)}
        for e in config["documents"].values()
    ]
    req = AnalysisRequest(
        event_id="corpus_diagnostic",
        context_version=context.version,
        temporal_mode="snapshot_demo",
        as_of=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )
    results = {}
    top = {}
    for mode in ("lexical", "exact", "hnsw"):
        rows = []
        top[mode] = {}
        for q in queries:
            start = time.perf_counter()
            res = await retriever.search_with_meta(
                context, req, q["text"], mode_preference=mode, explain=True
            )
            top[mode][q["id"]] = {e.id for e in res.evidence[:5]}
            rows.append(
                {
                    "query_id": q["id"],
                    "query": q["text"],
                    "mode": res.retrieval_mode,
                    "latency_seconds": time.perf_counter() - start,
                    "target_document_in_top5": any(
                        e.document_version_id == q["document_id"] for e in res.evidence[:5]
                    ),
                    "top5": [e.id for e in res.evidence[:5]],
                    "plan": res.query_plan,
                    "hnsw_index_used": "idx_evidence_hnsw" in json.dumps(res.query_plan),
                    "theses_count": len(res.theses),
                    "warnings": res.warnings,
                }
            )
        results[mode] = {
            "document_coverage_at_5": mean(r["target_document_in_top5"] for r in rows),
            "queries": rows,
        }
    agreements = [
        len(top["exact"][q["id"]] & top["hnsw"][q["id"]]) / len(top["exact"][q["id"]])
        if top["exact"][q["id"]]
        else 1.0
        for q in queries
    ]
    return {
        "status": "technical_diagnostic_not_gold",
        "modes": results,
        "hnsw_vs_exact_top5_agreement": mean(agreements),
        "query_count": len(queries),
        "temporal_mode": "snapshot_demo",
        "forced_ann": True,
        "test_split_used": False,
        "gold_recall_at_5": None,
        "independent_acceptance": "BLOCKED_pending_human_gold",
        "downstream_no_answer_attribution": "not_evaluated_M04_out_of_scope",
    }


async def evaluate():
    import os

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.auth.service import current_context
    from app.retrieval.service import LocalE5Encoder

    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise SystemExit("Explicit isolated /alfa_test TEST_DATABASE_URL required")
    encoder = LocalE5Encoder()
    if not encoder.is_available:
        raise SystemExit("Pinned E5 unavailable; cannot claim vector evaluation")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as c:
            context = await current_context(c, "alice")
        result = await compare_diagnostics(engine, encoder, context)
        out = Path("artifacts/eval/retrieval_comparison.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: v for k, v in result.items() if k != "modes"}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(evaluate())
    sys.exit(2)  # successful technical diagnostics do not certify independent gold
