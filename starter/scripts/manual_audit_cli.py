"""Interactive CLI for human-only manual audit (Coherence, Metadata, Grounding).

Displays only the relevant chunks/hits cleanly in the terminal so the student
can score 0/1/2 and verify metadata without manually scrolling a 2,000-line JSON.
Saves progress incrementally to starter/results/manual_audit.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ANCHOR_KEYWORDS: dict[str, list[str]] = {
    "c01": ["Dữ liệu cốt lõi"],
    "c02": ["Bộ Công an là cơ quan đầu mối", "quản lý nhà nước về dữ liệu"],
    "c03": ["Cung cấp dữ liệu cho cơ quan nhà nước", "không cần chủ thể dữ liệu đồng ý"],
    "c04": ["Sàn dữ liệu", "Tổ chức cung cấp dịch vụ sàn dữ liệu"],
    "c05": ["Hình thức thể hiện của thông điệp dữ liệu"],
    "c06": ["Giá trị pháp lý của chứng thư điện tử", "hợp pháp hóa lãnh sự"],
    "c07": ["Danh mục cơ sở dữ liệu của Bộ, ngành, địa phương"],
    "c08": ["Dữ liệu cá nhân bao gồm dữ liệu cá nhân cơ bản"],
    "c09": ["Thông báo vi phạm quy định về bảo vệ dữ liệu cá nhân", "72 giờ"],
    "c10": ["miễn trừ quy định về chỉ định cá nhân", "02 năm đầu"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _find_anchor_chunks(pos_id: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kws = ANCHOR_KEYWORDS.get(pos_id, [])
    matched = [c for c in chunks if any(_norm(kw) in _norm(c["text"]) for kw in kws)]
    return matched if matched else chunks[:2]


def _prompt_score(label: str) -> int:
    while True:
        val = input(f"  {label} [0/1/2]: ").strip()
        if val in {"0", "1", "2"}:
            return int(val)
        print("  -> Vui long nhap 0, 1, hoac 2.")


def _prompt_reason(label: str) -> str:
    while True:
        val = input(f"  {label}: ").strip()
        if val:
            return val
        print("  -> Vui long nhap ly do ngan gon.")


def _prompt_yes_no(label: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        val = input(f"  {label} {hint}: ").strip().lower()
        if not val:
            return default
        if val in {"y", "yes", "1", "true"}:
            return True
        if val in {"n", "no", "0", "false"}:
            return False
        print("  -> Vui long nhap y hoac n.")


def _save(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    starter_dir = Path(__file__).resolve().parents[1]
    template_path = starter_dir / "results" / "manual_audit_template.json"
    out_path = starter_dir / "results" / "manual_audit.json"

    src_path = out_path if out_path.exists() else template_path
    data = json.loads(src_path.read_text(encoding="utf-8"))

    print("=== CONG CU CHAM THU CONG DAY 06 ===")
    print("Huong dan: Thang diem 0 = cat cut/thieu bang chung | 1 = thieu ngu canh/dieu kien | 2 = tron y/du bang chung.\n")

    # 1. Coherence: 10 positions x (A, B; C mirrors B since chunks are identical)
    print("--- PHAN 1: COHERENCE (10 vi tri x A va B; C dung chung chunk voi B) ---")
    for pos in data["coherence_10_positions"]:
        pos_id = pos["pos_id"]
        print(f"\n[{pos_id}] {pos['document_id']} p.{pos['page']} | Anchor: {pos['anchor']}")

        for strat in ("A", "B"):
            relevant = _find_anchor_chunks(pos_id, pos["chunks_by_strategy"][strat])
            already_done = all(c["manual_score_0_to_2"] is not None for c in relevant)
            if already_done:
                print(f"  Strategy {strat}: da cham ({[c['manual_score_0_to_2'] for c in relevant]}), bo qua.")
                continue

            print(f"\n  >> Strategy {strat} ({len(relevant)} chunk lien quan):")
            for c in relevant:
                print(f"     ID: {c['chunk_id']}")
                print(f"     Text: {c['text']}")
                score = _prompt_score(f"Diem Coherence ({c['chunk_id']})")
                reason = _prompt_reason("Ly do ngan")
                c["manual_score_0_to_2"] = score
                c["manual_reason"] = reason
            _save(out_path, data)

        # Mirror B -> C since B and C share identical chunks
        b_map = {c["chunk_id"]: c for c in pos["chunks_by_strategy"]["B"]}
        for c_chunk in pos["chunks_by_strategy"]["C"]:
            b_src = b_map.get(c_chunk["chunk_id"])
            if b_src and b_src["manual_score_0_to_2"] is not None:
                c_chunk["manual_score_0_to_2"] = b_src["manual_score_0_to_2"]
                c_chunk["manual_reason"] = f"(Dung chung chunk voi B) {b_src['manual_reason']}"
        _save(out_path, data)

    # 2. Metadata & Grounding on 5 test questions
    print("\n--- PHAN 2 & 3: METADATA & GROUNDING (5 cau test x A, B, C) ---")
    test_dict = data["test_5_questions_metadata_and_grounding"]
    b_by_qid = {t["question_id"]: t for t in test_dict["B"]}

    for strat in ("A", "B", "C"):
        print(f"\n================ STRATEGY {strat} ================")
        for item in test_dict[strat]:
            qid = item["question_id"]
            if item["manual_grounding_score_0_to_2"] is not None and all(
                h["manual_metadata_complete_and_correct"] is not None for h in item["hits"]
            ):
                print(f"  [{qid}] Da cham, bo qua.")
                continue

            if strat == "C":
                b_item = b_by_qid[qid]
                same_top3 = [h["chunk_id"] for h in item["hits"]] == [h["chunk_id"] for h in b_item["hits"]]
                if same_top3 and b_item["manual_grounding_score_0_to_2"] is not None:
                    print(f"\n  [{qid}] Top-3 cua C giong het B.")
                    if _prompt_yes_no("Dung lai nhan Grounding & Metadata cua B cho C?", default=True):
                        item["manual_grounding_score_0_to_2"] = b_item["manual_grounding_score_0_to_2"]
                        item["manual_grounding_reason"] = f"(Top-3 giong B) {b_item['manual_grounding_reason']}"
                        for hc, hb in zip(item["hits"], b_item["hits"]):
                            hc["manual_metadata_complete_and_correct"] = hb["manual_metadata_complete_and_correct"]
                            hc["manual_metadata_note"] = hb["manual_metadata_note"]
                        _save(out_path, data)
                        continue

            print(f"\n  [{qid}] Question: {item['question']}")
            print(f"  Expected: {item['expected_answer']}")
            print(f"  Gold: {item['gold_source_id']} p.{item['gold_page']} | Filter: {item['filter']} | Auto Hit@3: {item['auto_hit_at_3']}")
            for h in item["hits"]:
                meta = h["metadata"]
                print(
                    f"\n    Rank {h['rank']} | {h['chunk_id']} | score={h['score']}\n"
                    f"    Metadata: source={h['source']}, page={h['page']}, doc_type={meta.get('doc_type')}, year={meta.get('year')}, chapter={meta.get('chapter')}\n"
                    f"    Text: {h['text']}"
                )
                if h["manual_metadata_complete_and_correct"] is None:
                    ok = _prompt_yes_no(f"Metadata rank {h['rank']} du va dung?", default=True)
                    h["manual_metadata_complete_and_correct"] = ok
                    h["manual_metadata_note"] = "OK" if ok else _prompt_reason("Ghi loi metadata")

            if item["manual_grounding_score_0_to_2"] is None:
                g_score = _prompt_score(f"Diem Grounding top-3 cho {qid}")
                g_reason = _prompt_reason("Ly do Grounding")
                item["manual_grounding_score_0_to_2"] = g_score
                item["manual_grounding_reason"] = g_reason

            _save(out_path, data)

    # 3. Print final audit summary
    print("\n=== TONG HOP DIEM AUDIT THU CONG ===")
    for strat in ("A", "B", "C"):
        pos_scores = []
        for pos in data["coherence_10_positions"]:
            relevant = _find_anchor_chunks(pos["pos_id"], pos["chunks_by_strategy"][strat])
            vals = [c["manual_score_0_to_2"] for c in relevant if c["manual_score_0_to_2"] is not None]
            if vals:
                pos_scores.append(sum(vals) / len(vals))
        coh_avg = sum(pos_scores) / len(pos_scores) if pos_scores else 0.0

        items = test_dict[strat]
        all_hits = [h for it in items for h in it["hits"]]
        meta_ok = sum(1 for h in all_hits if h["manual_metadata_complete_and_correct"] is True)
        g_scores = [it["manual_grounding_score_0_to_2"] for it in items if it["manual_grounding_score_0_to_2"] is not None]
        g_counts = {k: g_scores.count(k) for k in (0, 1, 2)}
        g_avg = sum(g_scores) / len(g_scores) if g_scores else 0.0

        print(
            f"Strategy {strat}: Coherence={coh_avg:.2f}/2 (n={len(pos_scores)}) | "
            f"Metadata={meta_ok}/{len(all_hits)} | "
            f"Grounding 0/1/2={g_counts[0]}/{g_counts[1]}/{g_counts[2]} (avg={g_avg:.2f}/2)"
        )
    print(f"\nDa luu ket qua cham thu cong tai: {out_path}")


if __name__ == "__main__":
    main()
