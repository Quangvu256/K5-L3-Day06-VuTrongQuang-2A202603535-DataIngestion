# Báo cáo cá nhân — Day 6

- **Họ và tên:** Vũ Trọng Quang | **MSSV:** `2A202603535` | **Lớp:** `L3A`

## Dữ liệu và pipeline
- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:**
  - Chủ đề: Pháp luật Việt Nam về Dữ liệu và Giao dịch điện tử (2023–2024), gồm 3 văn bản Công báo Chính phủ:
    1. *Luật Dữ liệu 60/2024/QH15* (`starter/data/luat_du_lieu_2024.pdf`, `23` trang).
    2. *Luật Giao dịch điện tử 20/2023/QH15* (`starter/data/luat_gd_dt_2023.pdf`, `30` trang).
    3. *Nghị định 13/2023/NĐ-CP về Bảo vệ dữ liệu cá nhân* (`starter/data/nd13_2023_bvdlcn.pdf`, `37` trang vật lý).
  - Quyền sử dụng: Văn bản quy phạm pháp luật công khai tại Việt Nam (không thuộc đối tượng bảo hộ quyền tác giả theo Điều 15 Luật Sở hữu trí tuệ). Chi tiết URL nguồn, ngày truy cập (`2026-11-13`) và số ký tự lưu tại [data/manifest.json](../../data/manifest.json).
  - Quy tắc tính trang: Tổng `90` trang vật lý; loại `8` trang cuối của Nghị định 13/2023 (`trang 30` là mục lục Phụ lục, `trang 31–37` là các biểu mẫu điền đơn) → còn **`82` trang nội dung điều khoản hữu ích** (`23 + 30 + 29`), mỗi trang đều có `> 1.000` ký tự sau chuẩn hóa.
- **Chuẩn hóa, lỗi và giới hạn:**
  - Chuẩn hóa Unicode về dạng `NFC`, chuẩn hóa khoảng trắng/xuống dòng, tách bỏ dòng running header Công báo (`CÔNG BÁO/Số .../Ngày ...`) và nối các dòng bị ngắt mềm theo cấu trúc `Chương / Mục / Điều / Khoản` trong [ingestion.py](../../starter/src/ingestion.py).
  - Lỗi trích xuất đã phát hiện và xử lý: bản `13nd.signed.pdf` ban đầu tải từ `vanban.chinhphu.vn` là file scan (`39` trang, `0` ký tự) nên đã thay bằng bản Công báo chính thức có lớp text; `0` trang lỗi/rỗng trên `82` trang đã chọn ([ingestion_errors.json](../../starter/results/ingestion_errors.json)).
  - Giới hạn PDF: `pypdf` đôi khi tách khoảng trắng nhỏ bên trong từ tiếng Việt do kerning font (ví dụ `côn g lập`, `d ữ li ệu`). Hàm chuẩn hóa giữ nguyên khoảng trắng này để tránh ghép nhầm hai từ ngắn tiếng Việt, và bộ chấm [evaluation.py](../../starter/src/evaluation.py) đối chiếu trên text đã chuẩn hóa.
- **Embedding model, vector store, metric, lệnh chạy và chi phí:**
  - Embedding model: OpenAI `text-embedding-3-small`, `1,536` chiều, dùng chung SDK `openai==3.50.0` cho cả document và query.
  - Vector store: `PostgreSQL 16.16 + pgvector 0.8.7` (Docker local trong `starter/docker-compose.yml`), **exact cosine distance** (`<=>`, không dùng approximate index).
  - Lệnh chạy tái lập: từ thư mục `starter/`, chạy `python -m src.run`.
  - Chi phí & tài nguyên chạy thật: `30` API calls, `165,485` tokens, tổng chi phí **`$0.003310`**.
  - Kiểm chứng nạp lại (re-ingest idempotency): nạp lại Chiến lược A giữ nguyên `383 -> 383` chunks (`passed: true`), đồng thời `upsert` xóa chunk cũ nếu văn bản nguồn thay đổi.
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:**
  - Bắt buộc: `document_id`, `source`, `page` (số trang PDF vật lý).
  - Bổ sung: `doc_type` (`"luat"` | `"nghi_dinh"`), `year` (`2023` | `2024`), `chapter` (`Chương I..V`), `article` (`Điều ...`), `document_number`. Các giá trị `doc_type`, `year`, `document_number` lấy trực tiếp từ văn bản trong [data/manifest.json](../../data/manifest.json); `chapter`/`article` trích tự động bằng regex từ tiêu đề.
  - Lý do chọn `doc_type` và `year`: phân biệt rõ 2 Luật với Nghị định và năm ban hành `2023` vs `2024`, phù hợp cho các truy vấn lọc phạm vi văn bản mà không lộ gold answer.

## Cấu hình và benchmark
- **Ba chiến lược so sánh có kiểm soát** (chi tiết trong [experiment_log.csv](../../artifacts/experiment_log.csv)):
  - **A:** `fixed_size(size=500, overlap=50)`, lưu metadata đầy đủ, **không** dùng filter khi truy xuất.
  - **B:** `by_paragraph(max_size=1000)` (gom theo `Chương`/`Điều`/đoạn văn, chỉ tách câu khi đoạn vượt `1000` ký tự), lưu metadata đầy đủ, **không** dùng filter khi truy xuất. *(A–B chỉ thay đổi thuật toán chunking).*
  - **C:** Dùng chung chunk và vector của **B**, **thêm** parameterized SQL `WHERE` filter (`doc_type`, `year`, `document_id`) khi câu hỏi có `filter` cung cấp trước. *(B–C chỉ thay đổi duy nhất bước lọc metadata lúc truy xuất).*
- **Bộ benchmark (`20` câu, [questions.json](../../benchmark/questions.json)):**
  - Tách trước khi chạy: **`15` câu `dev` (`q01–q15`) / `5` câu `test` (`q16–q20`)**, rải đều cả 3 văn bản, gồm `truc_tiep`, `dien_dat_lai`, `dieu_kien_ngoai_le` và **`5` câu `metadata_filter`** (`3` câu trong `dev`: `q05`, `q10`, `q15`; `2` câu trong `test`: `q17`, `q20`).
  - Mọi `evidence_quote` đều được đối chiếu khớp trên đúng `(gold_source_id, gold_page)` trong [normalized_pages.json](../../starter/results/normalized_pages.json) trước khi chạy.
- **Tiêu chí chọn chiến lược chốt trước khi xem test:** Ưu tiên `Evidence Hit@3` cao nhất trên `dev`; nếu hòa thì ưu tiên `Hit@1` trên `dev`, sau đó ưu tiên số chunk ít hơn (giảm chi phí/lưu trữ).

## Kết quả
| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian (embed + search) | Chi phí |
|---|---|---|---|---:|---:|---:|
| A | dev | 4/15 (26.7%) | 7/15 (46.7%) | 383 | 10.143s + 0.620s | ~$0.0017 |
| A | test | 2/5 (40.0%) | 3/5 (60.0%) | 383 | (cùng index A) | (đã tính) |
| B | dev | 11/15 (73.3%) | 12/15 (80.0%) | 243 | 3.916s + 0.599s | ~$0.0016 |
| B | test | 4/5 (80.0%) | 4/5 (80.0%) | 243 | (cùng index B) | (đã tính) |
| C | dev | 11/15 (73.3%) | 12/15 (80.0%) | 243 | dùng chung vector B + 0.585s | $0 thêm |
| C | test | 4/5 (80.0%) | 4/5 (80.0%) | 243 | (cùng index C) | $0 thêm |

*(Tổng chi phí thực đo toàn bộ pipeline gồm cả 20 query embeddings là `$0.003310` cho `165,485` tokens).*

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---:|---:|---:|---|
| A | 0.55 / 2 (n=10) | 10/15 | 1/2 (test) · 4/5 (all) | 1 / 3 / 1 · avg **1.00 / 2** |
| B | 1.10 / 2 (n=10) | 6/15 | 1/2 (test) · 3/5 (all) | 1 / 0 / 4 · avg **1.60 / 2** |
| C | 1.10 / 2 (n=10) | 9/15 | **2/2 (test) · 5/5 (all)** | 1 / 0 / 4 · avg **1.60 / 2** |

- **Đường dẫn trace, nhãn thủ công và cách chọn mẫu:**
  - Số liệu tổng hợp và trace top-3 đầy đủ: [summary.json](../../starter/results/summary.json), [traces.json](../../starter/results/traces.json).
  - Nhãn audit thủ công do học viên tự chấm: [manual_audit.json](../../starter/results/manual_audit.json).
  - Cách chọn mẫu Coherence: chọn trước khi chạy `10` vị trí nguồn cố định (`c01–c10`, rải đều 3 văn bản theo các `Điều`/`Khoản` chính trong [questions.json](../../benchmark/questions.json)). Chấm Metadata & Grounding trên toàn bộ `5` câu `test` (`q16–q20`).
  - **Minh bạch người chấm & thiên lệch:** Nhãn Coherence, Metadata và Grounding ban đầu do **chính tác giả (Vũ Trọng Quang)** tự chấm qua `manual_audit_cli.py`; do phiên bản CLI đầu tiên cắt cụt hiển thị ở `420` ký tự làm sót nửa sau chunk ở B/C (`q17, q18, q20`), sau khi sửa `manual_audit_cli.py` để hiển thị đủ text, tác giả đã dùng AI đối chiếu lại phần text đầy đủ của 5 câu test B/C và tự xem xét, xác nhận lại nhãn Grounding theo [GUIDE.md](../../GUIDE.md). Nguy cơ thiên lệch vẫn tồn tại do tác giả tự kiểm chứng; riêng phần Metadata vẫn giữ nguyên nhãn lần chấm đầu (một số hit đúng `source/page/year` nhưng bị đánh `false` vì nội dung không chứa trực tiếp câu trả lời) và được ghi nhận ở mục Hạn chế.

## Phân tích và kết luận
- **So sánh A–B (Ảnh hưởng của Chunking):**
  - Chuyển từ cắt cứng `500/50` (A) sang gom theo đoạn/`Điều` `max 1000` (B) giúp **giảm 36.6% số chunk** (`383 → 243`) và giảm thời gian embed từ `10.14s` xuống `3.92s`, đồng thời tăng **DEV Hit@1 từ `4/15` lên `11/15`** và **DEV Hit@3 từ `7/15` lên `12/15`**.
  - Nguyên nhân: trong `A`, có `5` câu `dev` (`q06, q09, q10, q12, q15`) và `2` câu `test` (`q16, q20`) có `location_only_hit3=True` nhưng `evidence_split_warning=True` — nghĩa là truy xuất tìm đúng trang nhưng đoạn cắt cứng `500` ký tự cắt ngang giữa câu nên không giữ trọn `evidence_quote`, làm điểm Coherence trung bình của A chỉ đạt `0.55/2` so với `1.10/2` của B.
- **So sánh B–C (Ảnh hưởng của Metadata Filter):**
  - Trên `Hit@1` và `Evidence Hit@3`, B và C bằng nhau (`11/15` và `12/15` trên `dev`; `4/5` trên `test`), vì với 5 câu có filter, thuật ngữ đặc thù đã giúp rank 1 của B đứng đúng văn bản.
  - Tuy nhiên, ở khía cạnh **tuân thủ filter và chất lượng top-3**, B chỉ đạt `1/2` (test) và `3/5` (toàn bộ 5 câu filter): cụ thể tại `q10` (dev) rank 3 của B lọt sang `luat_du_lieu_2024` trang 7 (`score=0.6269`), và tại `q17` (test) rank 3 của B lọt sang `nd13_2023_bvdlcn` trang 15 (`score=0.5034`). Trong khi đó, **C đạt `2/2` (test) và `5/5` (all) tuân thủ filter 100%** (loại sạch nhiễu ngoài văn bản mục tiêu) và cùng B đạt Grounding trung bình **`1.60/2`** (cao hơn `1.00/2` của A nhờ giữ trọn các khoản và điều kiện ngoại lệ trong 1 chunk).
  - **Hữu ích & hạn chế của metadata:** Trường `doc_type`, `year`, `document_id` giúp thu hẹp phạm vi tìm kiếm đúng văn bản hiệu lực (hữu ích khi 2 văn bản cùng nói về "dữ liệu cá nhân" nhưng khác năm/cơ quan ban hành). Hạn chế: ở corpus này metadata `chapter` chưa được gắn làm điều kiện filter chính và chưa có filter theo hiệu lực sửa đổi giữa các luật.
- **2 ví dụ lỗi thực tế từ trace:**
  1. **`q06` (`dev`, gold: `luat_du_lieu_2024` trang 22):** Hỏi *“Tổ chức cung cấp dịch vụ sàn dữ liệu phải thuộc loại hình đơn vị nào?”*. Cả A (`fixed_500_50:luat_du_lieu_2024:p21:c001`, score `0.6062`) và B (`para_1000:luat_du_lieu_2024:p08:c001`, score `0.5783`) đều đạt `Hit@3 = False` và có `evidence_split_warning = True` (khoản 2 Điều 42 bị tách khỏi cụm câu trả lời hoặc dính lỗi khoảng trắng `côn g lập` từ PDF).
  2. **`q16` (`test`, gold: `luat_du_lieu_2024` trang 19, không filter):** Hỏi về điều kiện khai thác dữ liệu cá nhân trong *Cơ sở dữ liệu tổng hợp quốc gia*. B/C (`para_1000:luat_du_lieu_2024:p08:c003`, score `0.6930`) kéo về quy định chung ở trang 8 thay vì trang 19 vì nội dung khoản 3 Điều 34 nằm sang trang 19 nên mất tiêu đề `Điều 34` trên đầu trang.
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:**
  - Theo tiêu chí đã chốt trước khi xem test, chọn **Chiến lược C** (`by_paragraph max 1000` + metadata filter khi có ngữ cảnh): đạt `DEV Hit@3 = 12/15` (`80.0%`), `TEST Hit@3 = 4/5` (`80.0%`), giảm `36.6%` số chunk so với A, Grounding đạt `1.60/2` và tuân thủ filter `100%`.
  - Đánh đổi: `by_paragraph` tạo các chunk độ dài không đều (`53–1000` ký tự) và không khắc phục được trường hợp một `Điều` luật bị ngắt qua hai trang PDF vật lý; filter chỉ phát huy tác dụng khi người dùng/hệ thống cung cấp ngữ cảnh đúng trước khi truy xuất.
- **Hạn chế của dữ liệu, nhãn, test nhỏ, đo thời gian/chi phí:**
  - Tập `test` nhỏ (`n=5`) nên mỗi câu tương ứng `20%`, sai số mẫu cao; kết quả chỉ phản ánh corpus 3 văn bản pháp luật này, không suy rộng ra mọi loại tài liệu.
  - Khi chấm thủ công Metadata trên `5` câu `test`, người chấm đôi khi đánh giá độ liên quan nội dung của hit thay vì chỉ kiểm tra tính đúng/đủ của trường `source/page/doc_type/year`, làm điểm Metadata của B (`6/15`) thấp hơn A (`10/15`) và C (`9/15`).
  - Lỗi kerning khoảng trắng nhẹ trong `pypdf` và việc chia chunk độc lập trong từng trang PDF (không nối liên trang khi một `Điều` bắt đầu ở cuối trang trước và tiếp sang trang sau).
- **Nếu làm lại hoặc mở rộng, cải tiến gì?**
  1. Chunk theo cấu trúc toàn văn bản (gộp các trang trước khi tách theo `Điều`, đồng thời lưu mảng `pages: [18, 19]` để không làm đứt tiêu đề `Điều` qua ranh giới trang).
  2. Bổ sung **Hybrid Retrieval** (dense cosine + BM25 từ khóa số hiệu `Điều`/`Khoản` chính xác như `"Điều 42"`, `"Nghị định 13"`).
  3. Dùng thêm `PyMuPDF` (`fitz`) hoặc `pdfplumber` để giảm lỗi khoảng trắng ký tự tiếng Việt khi trích xuất PDF Công báo.

## Minh bạch sử dụng AI (`RULES.md`)
- **Công cụ sử dụng:** Trợ lý AI trong IDE (hỗ trợ viết code các module Python trong `starter/src/`, script `manual_audit_cli.py`, tạo `data/manifest.json` và bản nháp 20 câu hỏi `benchmark/questions.json` từ nội dung `normalized_pages.json`).
- **Cách kiểm tra của học viên:** Tự chạy kiểm chứng toàn bộ `20/20` `evidence_quote` khớp đúng trên trang nguồn; tự chạy pipeline `python -m src.run` thu thập số liệu và trace thật; tự chấm thủ công các nhãn Coherence, Metadata và Grounding A qua `manual_audit_cli.py`, và tự xem xét/xác nhận phần AI hỗ trợ đối chiếu lại Grounding B/C trên text đầy đủ sau khi sửa lỗi cắt cụt `420` ký tự của CLI.
