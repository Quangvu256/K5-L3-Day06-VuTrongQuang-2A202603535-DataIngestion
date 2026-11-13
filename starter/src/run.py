"""End-to-end Day 06 pipeline: ingest, chunk, embed, store in pgvector, and benchmark A/B/C."""
from __future__ import annotations

import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import load_pdf
from .models import Chunk, DocumentPage
from .store import PgVectorStore


def _chunk_stats(chunks: list[Chunk]) -> dict[str, Any]:
    lens = [len(c.text) for c in chunks]
    return {
        "chunk_count": len(chunks),
        "total_chars": sum(lens),
        "min_chars": min(lens) if lens else 0,
        "median_chars": int(statistics.median(lens)) if lens else 0,
        "max_chars": max(lens) if lens else 0,
    }


def _build_manual_audit_template(
    store: PgVectorStore,
    coherence_positions: list[dict[str, Any]],
    traces_by_strategy: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Export pre-filled samples for human scoring (coherence, metadata, grounding)."""
    coherence_samples: list[dict[str, Any]] = []
    for pos in coherence_positions:
        per_strategy: dict[str, list[dict[str, Any]]] = {}
        for strat in ("A", "B", "C"):
            chunks = store.get_chunks_for_page(strat, pos["document_id"], int(pos["page"]))
            per_strategy[strat] = [
                {
                    "chunk_id": c["chunk_id"],
                    "text": c["text"],
                    "manual_score_0_to_2": None,
                    "manual_reason": "",
                }
                for c in chunks
            ]
        coherence_samples.append({**pos, "chunks_by_strategy": per_strategy})

    test_audit: dict[str, list[dict[str, Any]]] = {}
    for strat, traces in traces_by_strategy.items():
        test_audit[strat] = [
            {
                "question_id": t["question_id"],
                "question": t["question"],
                "filter": t["filter"],
                "gold_source_id": t["gold_source_id"],
                "gold_page": t["gold_page"],
                "expected_answer": t["expected_answer"],
                "evidence_quote": t["evidence_quote"],
                "auto_hit_at_3": t["hit_at_3"],
                "manual_grounding_score_0_to_2": None,
                "manual_grounding_reason": "",
                "hits": [
                    {
                        "rank": h["rank"],
                        "chunk_id": h["chunk_id"],
                        "source": h["source"],
                        "page": h["page"],
                        "score": h["score"],
                        "metadata": h["metadata"],
                        "text": h["text"],
                        "manual_metadata_complete_and_correct": None,
                        "manual_metadata_note": "",
                    }
                    for h in t["hits"]
                ],
            }
            for t in traces
            if t["split"] == "test"
        ]

    return {
        "note": "Dien nhan thu cong boi hoc vien (khong dung LLM cham thay).",
        "coherence_10_positions": coherence_samples,
        "test_5_questions_metadata_and_grounding": test_audit,
    }


def main() -> None:
    starter_dir = Path(__file__).resolve().parents[1]
    repo_root = starter_dir.parent
    results_dir = starter_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    load_dotenv(starter_dir / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL missing in starter/.env")

    manifest = json.loads((repo_root / "data" / "manifest.json").read_text(encoding="utf-8"))
    bench = json.loads((repo_root / "benchmark" / "questions.json").read_text(encoding="utf-8"))
    questions: list[dict[str, Any]] = bench["questions"]

    dev_q = [q for q in questions if q["split"] == "dev"]
    test_q = [q for q in questions if q["split"] == "test"]
    if len(questions) < 20 or len(dev_q) < 15 or len(test_q) < 5:
        raise ValueError("Benchmark must have >=20 questions (>=15 dev / >=5 test)")

    # 1. Load & normalize corpus from manifest
    t0 = time.perf_counter()
    pages: list[DocumentPage] = []
    error_log: list[dict[str, Any]] = []
    for doc in manifest["documents"]:
        pdf_path = repo_root / doc["source_path"]
        pages.extend(
            load_pdf(
                path=pdf_path,
                document_id=doc["source_id"],
                source=doc["source_path"],
                allowed_pages=set(doc["included_pages"]),
                base_metadata={
                    "doc_type": doc["doc_type"],
                    "year": doc["year"],
                    "document_number": doc["document_number"],
                },
                error_log=error_log,
            )
        )
    ingest_sec = round(time.perf_counter() - t0, 3)

    if len(pages) < 30:
        raise ValueError(f"Corpus has only {len(pages)} valid pages (<30 required)")

    (results_dir / "ingestion_errors.json").write_text(
        json.dumps(error_log, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2. Build chunks for A (fixed 500/50) and B/C (paragraph max 1000)
    chunks_a = [c for p in pages for c in fixed_size(p, size=500, overlap=50)]
    chunks_b = [c for p in pages for c in by_paragraph(p, max_size=1000)]

    # 3. Initialize real Gemini embedder and pgvector store
    embedder = MyEmbedder()
    store = PgVectorStore(database_url=database_url, dimensions=embedder.dimensions)
    store.create_schema()

    t_emb_a = time.perf_counter()
    vecs_a = embedder.embed_documents([c.text for c in chunks_a])
    emb_a_sec = round(time.perf_counter() - t_emb_a, 3)
    store.upsert("A", chunks_a, vecs_a)

    # Idempotency check: re-upsert Strategy A and verify row count stays identical.
    count_before = store.count_chunks("A")
    store.upsert("A", chunks_a, vecs_a)
    count_after = store.count_chunks("A")
    if count_before != count_after:
        raise RuntimeError(f"Idempotency failed for A: {count_before} -> {count_after}")

    t_emb_b = time.perf_counter()
    vecs_b = embedder.embed_documents([c.text for c in chunks_b])
    emb_b_sec = round(time.perf_counter() - t_emb_b, 3)
    store.upsert("B", chunks_b, vecs_b)
    # Strategy C uses the exact same chunks and embeddings as B; only query-time filtering differs.
    store.upsert("C", chunks_b, vecs_b)

    # 4. Embed all benchmark queries once with the same model
    t_q = time.perf_counter()
    query_vectors = {q["id"]: embedder.embed_query(q["question"]) for q in questions}
    query_sec = round(time.perf_counter() - t_q, 3)

    # 5. Run top-3 search and evaluate A, B, C
    all_traces: list[dict[str, Any]] = []
    traces_by_strategy: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": []}
    search_times: dict[str, float] = {}

    for strat, use_filter in [("A", False), ("B", False), ("C", True)]:
        ts = time.perf_counter()
        for q in questions:
            active_filter = q.get("filter") if use_filter else None
            hits = store.search(
                strategy=strat,
                query_vector=query_vectors[q["id"]],
                top_k=3,
                filters=active_filter,
            )
            trace = evaluate_query(q, hits)
            trace["strategy"] = strat
            trace["applied_filter"] = active_filter
            all_traces.append(trace)
            traces_by_strategy[strat].append(trace)
        search_times[strat] = round(time.perf_counter() - ts, 3)

    metrics = summarize(all_traces)

    summary_payload = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "embedding_provider": "OpenAI",
        "embedding_model": embedder.model_name,
        "dimensions": embedder.dimensions,
        "vector_store": "PostgreSQL 16 + pgvector 0.8.7 (exact cosine distance <=>)",
        "api_calls_total": embedder.api_calls,
        "api_chars_total": embedder.total_chars,
        "api_tokens_total": embedder.total_tokens,
        "estimated_cost_usd": embedder.estimated_cost_usd,
        "corpus_pages_loaded": len(pages),
        "ingestion_errors_count": len(error_log),
        "idempotency_check_strategy_A": {
            "count_before_reingest": count_before,
            "count_after_reingest": count_after,
            "passed": count_before == count_after == len(chunks_a),
        },
        "timings_seconds": {
            "ingest_pdf": ingest_sec,
            "embed_A": emb_a_sec,
            "embed_B_and_C_shared": emb_b_sec,
            "embed_queries_20": query_sec,
            "search_top3": search_times,
        },
        "chunk_stats": {
            "A": _chunk_stats(chunks_a),
            "B": _chunk_stats(chunks_b),
            "C": _chunk_stats(chunks_b),
        },
        "metrics": metrics,
    }

    (results_dir / "traces.json").write_text(
        json.dumps(all_traces, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (results_dir / "summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    audit_template = _build_manual_audit_template(
        store=store,
        coherence_positions=bench["coherence_sample_positions"],
        traces_by_strategy=traces_by_strategy,
    )
    (results_dir / "manual_audit_template.json").write_text(
        json.dumps(audit_template, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("=== Day 06 Benchmark Summary ===")
    print(
        f"Model: {embedder.model_name} (dim={embedder.dimensions}) | "
        f"API calls: {embedder.api_calls} | Tokens: {embedder.total_tokens} | "
        f"Cost: ${embedder.estimated_cost_usd:.6f}"
    )
    print(f"Idempotency A: {count_before} -> {count_after} (OK)")
    for strat in ("A", "B", "C"):
        m = metrics[strat]
        print(
            f"Strategy {strat} | "
            f"DEV Hit@1={m['dev']['hit1_str']} ({m['dev']['hit1_rate']:.1%}), "
            f"Hit@3={m['dev']['hit3_str']} ({m['dev']['hit3_rate']:.1%}) | "
            f"TEST Hit@1={m['test']['hit1_str']} ({m['test']['hit1_rate']:.1%}), "
            f"Hit@3={m['test']['hit3_str']} ({m['test']['hit3_rate']:.1%})"
        )


if __name__ == "__main__":
    main()
