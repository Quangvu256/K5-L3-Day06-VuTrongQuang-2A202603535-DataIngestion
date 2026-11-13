"""Evaluate retrieved top-k hits against verified gold evidence and preserve full traces."""
from __future__ import annotations

import re
from typing import Any


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _matches_gold_location(hit: dict[str, Any], gold_options: list[tuple[str, int]]) -> bool:
    doc_id = hit.get("document_id") or hit.get("metadata", {}).get("document_id")
    page = hit.get("page") or hit.get("metadata", {}).get("page")
    return (doc_id, page) in gold_options


def _contains_evidence(hit_text: str, evidence_quote: str) -> bool:
    if evidence_quote in hit_text:
        return True
    return _collapse_ws(evidence_quote) in _collapse_ws(hit_text)


def _passes_filter(hit: dict[str, Any], filters: dict[str, Any] | None) -> bool | None:
    if not filters:
        return None
    meta = hit.get("metadata", {})
    for key, expected in filters.items():
        actual = hit.get(key) if key in hit else meta.get(key)
        if str(actual) != str(expected):
            return False
    return True


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Check source/page + gold evidence containment and record top-k retrieval trace."""
    gold_options: list[tuple[str, int]] = [(question["gold_source_id"], int(question["gold_page"]))]
    for alt in question.get("alternative_gold", []):
        gold_options.append((alt["gold_source_id"], int(alt["gold_page"])))

    evidence_quote = question["evidence_quote"]
    filters = question.get("filter")

    evaluated_hits: list[dict[str, Any]] = []
    for rank, hit in enumerate(hits, start=1):
        loc_ok = _matches_gold_location(hit, gold_options)
        ev_ok = _contains_evidence(hit["text"], evidence_quote)
        is_hit = loc_ok and ev_ok
        evaluated_hits.append(
            {
                "rank": rank,
                "chunk_id": hit["chunk_id"],
                "document_id": hit.get("document_id"),
                "source": hit.get("source"),
                "page": hit.get("page"),
                "score": hit.get("score"),
                "metadata": hit.get("metadata"),
                "location_match": loc_ok,
                "evidence_match": ev_ok,
                "is_hit": is_hit,
                "filter_compliant": _passes_filter(hit, filters),
                "text": hit["text"],
            }
        )

    hit_at_1 = bool(evaluated_hits and evaluated_hits[0]["is_hit"])
    hit_at_3 = any(h["is_hit"] for h in evaluated_hits[:3])
    loc_hit_at_3 = any(h["location_match"] for h in evaluated_hits[:3])

    return {
        "question_id": question["id"],
        "split": question["split"],
        "type": question.get("type", ""),
        "question": question["question"],
        "expected_answer": question["expected_answer"],
        "gold_source_id": question["gold_source_id"],
        "gold_page": question["gold_page"],
        "evidence_quote": evidence_quote,
        "filter": filters,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "location_only_hit_at_3": loc_hit_at_3,
        "evidence_split_warning": loc_hit_at_3 and not hit_at_3,
        "hits": evaluated_hits,
    }


def _agg_subset(items: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(items)
    if n == 0:
        return {"n": 0, "hit1": 0, "hit1_rate": 0.0, "hit3": 0, "hit3_rate": 0.0}
    h1 = sum(1 for t in items if t["hit_at_1"])
    h3 = sum(1 for t in items if t["hit_at_3"])
    loc3 = sum(1 for t in items if t["location_only_hit_at_3"])
    return {
        "n": n,
        "hit1": h1,
        "hit1_str": f"{h1}/{n}",
        "hit1_rate": round(h1 / n, 4),
        "hit3": h3,
        "hit3_str": f"{h3}/{n}",
        "hit3_rate": round(h3 / n, 4),
        "location_only_hit3": loc3,
    }


def summarize(traces: list[dict[str, Any]], strategy_key: str = "strategy") -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 by strategy and dev/test split."""
    strategies = sorted({t[strategy_key] for t in traces})
    summary: dict[str, Any] = {}
    for strat in strategies:
        strat_traces = [t for t in traces if t[strategy_key] == strat]
        summary[strat] = {
            "dev": _agg_subset([t for t in strat_traces if t["split"] == "dev"]),
            "test": _agg_subset([t for t in strat_traces if t["split"] == "test"]),
            "all": _agg_subset(strat_traces),
        }
    return summary
