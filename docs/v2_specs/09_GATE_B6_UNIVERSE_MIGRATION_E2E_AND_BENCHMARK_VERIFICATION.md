# ĐẶC TẢ TRIỂN KHAI WAVE 5 — GATE B6: DI CƯ DỮ LIỆU THẬT ~383 DOANH NGHIỆP, KIỂM ĐỊNH TOÀN TRÌNH E2E, ABLATION & BÁO CÁO BENCHMARK NGHIỆM THU CUỐI

* **Mã đặc tả:** `SPEC-V2-09-GATE-B6-v1.1`
* **Wave:** `Wave 5` (Cổng Nghiệm thu Cuối cùng — **CHỈ KÍCH HOẠT** sau khi:
  1. Chủ sở hữu dự án xác nhận tiến trình thu thập ~383 doanh nghiệp phi tài chính trong hội thoại `ed48af80-cf41-47ae-8a0d-93cadaeaaad8` (`task-748` + Stage 3/4) đã **DỪNG HOÀN TOÀN 100%**.
  2. Toàn bộ các nhánh `01_GATE_B0` $\rightarrow$ `08_STREAM_B3` đã vượt qua Review Gate và merge sạch vào `v2/integration`, với tag mốc nền `v1.5-baseline-before-v2` đã tồn tại từ Pre-Wave 0.)
* **Nhánh Git / Worktree:** `v2/b6-verify` (`../finintel-wt-b6-verify`)
* **Mục tiêu:** Chứng minh toàn bộ hệ thống V2 đã tinh gọn (**Native Polars Incremental Transform + Dagster + DuckLake hoặc Sorted Parquet + `ReadWriteLock` + FastAPI + Streamlit 5-page**, **KHÔNG `SQLMesh`**, **KHÔNG `delta-rs`**) chạy thật end-to-end trên dữ liệu lịch sử thật của tập **~383 doanh nghiệp phi tài chính Việt Nam (`2016–2026`, `EXACT_FILING`)**, đo đạc mọi tiêu chí định lượng bằng số đo thật 100% (**CẤM ước lượng hay bịa số!**), thực hiện đầy đủ **Rollback Test, Circuit-Breaker E2E, Mode B Contract Test, và Ablation Study**, sau đó gửi hồ sơ cho `review-lead` đánh giá độc lập cuối cùng.

---

## 1. QUYỀN SỞ HỮU FILE & RÀNG BUỘC AN TOÀN

* **Owned Files (Chỉ được phép tạo các file sau — mọi đường dẫn đều repo-relative):**
  * `scripts/migrate_universe_to_v2.py` *(mới — công cụ di cư chỉ-đọc WAL-safe, xác minh SHA-256 kép, kiểm thử rollback & chạy bộ đo benchmark tái lập — đóng `HI-18`)*
  * `tests/integration/test_option_b_e2e_platform.py` *(mới — bộ integration test E2E ngoại tuyến 0 mạng, chạy được trên cả Ubuntu & Windows GitHub Actions CI)*
  * `docs/evidence/V2_PLATFORM_BENCHMARK_VERIFICATION.md` *(mới — báo cáo nghiệm thu định lượng đo thật nằm trên đường dẫn được Git track per Pre-Wave 0 `BLK-03`)*
* **Read-Only Contract Files:** `docs/evidence/option_b_spike_report.json`, toàn bộ mã nguồn `src/` và `dashboard/` đã merge từ Wave 0–4.
* **Bảo vệ Dữ liệu Gốc & Nguyên tắc Không Vá Chắp Vá:**
  * Kho `data/live_fhsc_universe/` **CHỈ ĐƯỢC ĐỌC (READ-ONLY)** khi di cư sang kho chính thức `data/` — giữ nguyên 100% bản gốc không sửa/xóa.
  * **KHÔNG sửa bất kỳ file logic nào trong `src/` hay `dashboard/`** tại Gate B6! Nếu phát hiện lỗi thật trong quá trình chạy trên ~383 mã $\rightarrow$ dừng lại lập **Defect Report** rõ file/dòng/tái hiện và quay lại đúng Stream sở hữu để sửa theo quy trình TDD + Review.

---

## 2. QUY TRÌNH DI CƯ, ABLATION & THỰC NGHIỆM 5 BƯỚC BẮT BUỘC

### Bước 1 — Di cư Chỉ-Đọc WAL-Safe, Xác minh SHA-256 Kép & Kiểm thử Rollback (`scripts/migrate_universe_to_v2.py` — `HI-18`)
1. **Sao lưu trước cắt chuyển (`Pre-Cutover Backup`):** Nếu kho đích `data/` đã có dữ liệu trước đó, sao lưu toàn bộ `data/metadata/state.sqlite` (bằng API chuẩn **`sqlite3.Connection.backup()`** an toàn với chế độ WAL — **CẤM copy tệp `.sqlite` thô bỏ quên `-wal`/`-shm`!**) và bản chụp hiện hành sang thư mục backup ngoài đường dẫn đích.
2. **Xác minh Toàn vẹn Dữ liệu Nguồn (`data/live_fhsc_universe/`):**
   * Đọc `ingestion_progress.json` và ghi nhận trung thực chính xác 3 con số **`done` / `skipped` / `failed`** trên tổng danh mục ~383 doanh nghiệp phi tài chính (không làm tròn hay trộn lẫn số 380/383/500!).
   * Xác minh độc lập toàn bộ tệp dưới `raw/sha256/` và `raw/provenance/` đối chiếu **CẢ HAI mã băm `payload_file_sha256` VÀ `canonical_content_sha256`** với `filings_manifest` (phát hiện 100% hỏng file hay lệch nội dung trước khi cắt chuyển!).
3. **Di cư & Kiểm thử Rollback:**
   * Sao chép chỉ-đọc `raw/`, `raw_disclosures/`, `issuers.parquet` và nạp `filings_manifest` (via `sqlite3.backup()` / import nguyên tử) sang kho chính thức `data/`.
   * Đồng bộ đúng danh sách mã `done` vào `CORPORATE_TICKERS` dynamic partitions của Dagster.
   * Sau khi xuất bản snapshot V2 thử nghiệm đầu tiên và thứ hai, thực thi kiểm thử **`rollback_snapshot()`** quay lại snapshot trước rồi khôi phục lại snapshot mới nhất, ghi nhận bằng chứng xác minh `ParquetRepository.snapshot_id` đổi nguyên tử không lỗi!

### Bước 2 — Chạy Toàn trình Hệ thống V2 trên Dữ liệu Thật ~383 Doanh nghiệp
Thực thi liên hoàn toàn bộ pipeline V2 đã hợp nhất trên kho chính thức `data/`:
1. **Layer 1 Disclosure Enrichment (`02`):** Chạy `merge_disclosure_records` (có Audit-Tier PIT Guard & bảo toàn `audit_status != None`) + lịch lễ VN `2016–2026` $\rightarrow$ ghi nhận tỷ lệ `EXACT_FILING` và số lượng/tỷ lệ `RF_07` nộp trễ trước vs sau bản vá.
2. **Layer 2 Vectorized Batch & Incremental Normalize (`03`):**
   * Đo thời gian & đỉnh RAM (`tracemalloc`/RSS) của **Full Normalize (~383 mã)**.
   * Đo thời gian của **Incremental Normalize** (0 dirty issuers & 1 dirty issuer).
3. **Layer 3 + 4 + 6 via Native Polars Incremental Transform & Dagster (`04`, `05`, `06`, `07`):**
   * Chạy `published_financial_marts` (chạy `QUALITY_GATE_1`, `financial_ratios` đủ 38 tỷ số + 5 cột stress, `industry_benchmarks` đủ 38 tỷ số, `accounting_red_flags` đủ `RF_01..RF_09`, `ml_scores_and_explanations` cho `QUARTER` trên `{"VAS_TT200_2014", "VAS_TT99_2025"}` + `MarginPlattCalibrator`, và `run_blocking_publish_audits()` `QUALITY_GATE_2`).
   * Xuất bản nguyên tử 1 lần toàn bộ 9 bảng vào backend đã chốt ở Gate B0 (`"ducklake"` hoặc `"parquet"` + `ReadWriteLock`), và chạy `lakehouse_maintenance_job` (`maintain()` + `collect_curated_garbage()`).

### Bước 3 — Kiểm định Định lượng Có Thể Bác bỏ (`Falsifiable Benchmark & Ablation Gates` — `HI-07`, `HI-18`)
Bắt buộc truy vấn trực tiếp bảng đã xuất bản và ghi nhận **đầy đủ, trung thực cả điểm mạnh lẫn hạn chế** vào `docs/evidence/V2_PLATFORM_BENCHMARK_VERIFICATION.md`:

| Hạng mục Kiểm định | Tiêu chí Nghiệm thu Có Thể Bác bỏ (Falsifiable Gate) | Dữ liệu Bằng chứng Bắt buộc Ghi Trong Báo cáo |
|---|---|---|
| **1. Tốc độ & RAM Layer 2 (`03`)** | Full Normalize ~383 mã **`< 35s`**; Incremental 0–1 mã **`< 1.5s`**; RAM đỉnh **`< 500 MB`** | Thời gian thực tế (s), đỉnh RSS/tracemalloc (MB), số `done`/`skipped`/`failed` issuers, số filings & facts |
| **2. Data Skipping & GC Layer 3 (`04`, `HI-07`)** | Row-Group Skipping **`> 85%`** cho truy vấn 1 mã trên **cả** `financial_facts` (`row_group_size=25_000`) **lẫn** `financial_ratios` (`row_group_size=2_000`); `data/curated/` sau 3 lần chạy chỉ giữ đúng `keep_runs=2` cho từng `(subdir, source)` | Trích đoạn thật `EXPLAIN ANALYZE` + tỷ lệ row groups bị bỏ qua theo từng bảng + dung lượng ổ đĩa sau 3 run |
| **3. Độ phủ Năm 2026 TT99 (`GAP-02`, `MISS-07`)** | Trên các dòng `QUARTER` năm `2026-Q1..Q3` có đủ BCTC đầu vào: độ phủ non-null của `ebit_ttm`, `ebitda_ttm`, `roic`, `interest_coverage`, `dupont_valid_flag`, `altman_z_double_prime`, `piotroski_f_score`, `calibrated_distress_prob` đạt **`>= 80%`** (chấm dứt hoàn toàn `0%` & lỗi `ttm_exact_filing`) | Bảng tỷ lệ non-null thực tế (tử số / mẫu số) của từng chỉ tiêu trên toàn bộ dòng `QUARTER` năm 2026 |
| **4. Giảm Cảnh báo Giả `RF_07` (`GAP-07`)** | Loại bỏ cảnh báo trễ giả `1–5` ngày quanh kỳ nghỉ lễ `30/4–1/5` & Tết Nguyên Đán (`2024-Q4` `2025-01-30`) và lỗi ngày nhập liệu Vietcap IQ không vi phạm hạng kiểm toán | So sánh số lượng & tỷ lệ `RF_07` trước vs sau bản vá kèm mẫu ca kiểm chứng cụ thể |
| **5. Chống "Clever Hans", Hiệu chuẩn ML & Ablation Bắt buộc (`GAP-03..06`, `HI-18`)** | 1. Công khai **ĐẦY ĐỦ cả `Validation` (gồm OOF) lẫn `Test`** cho `Altman Z''`, `Baseline_2_Logit`, `XGBoost` (`PR-AUC`, `ROC-AUC`, `Brier`, `F2`, `Recall@Top-10%`).<br>2. **Bảng Ablation Bắt buộc:** (a) `STATUTORY_LAG` vs `EXACT_FILING`; (b) `BASE_FEATURES` vs `SECTOR_ADJUSTED_FEATURES` (ghi rõ `peer_standardization_mode`: `PIT_REGIME_SYMMETRIC` vs `PIT_ICB`).<br>3. **Platt Slope Gate:** **`0.0 < platt_slope <= 10.0`** (dương chặt & không bùng nổ!).<br>4. **K-Means Gate:** `0` cụm trùng tên, cụm lớn nhất `<= 85%`.<br>5. In **Top 15 dự báo rủi ro cao nhất trên Tập `Test`** kiểm tra không có blue-chip khỏe mạnh (kiểu `GMD`) bị phán `99.99%`. | Trích xuất trực tiếp từ `model_evaluation_report.json`, `_ml_run_manifest.json` và truy vấn DuckDB trên `ml_scores_and_explanations` |
| **6. Hiệu năng Serving Thực tế & Live Refresh (`08`, `HI-18`)** | Chạy server **`uvicorn` thật** dưới **20 người dùng đồng thời** với hỗn hợp tải chuẩn (`40% /profile`, `20% /benchmarks`, `15% /lineage`, `15% /screener`, `10% POST /stress-test`): **p95 `< 100ms`**, tỷ lệ lỗi `0%`. Đồng thời chạy 1 tiến trình publish snapshot mới xác nhận **`READ_ONLY` snapshot refresh** (ETag đổi sạch sang snapshot mới), `0` read-skew, `0` `ResourceInUseException`. | Báo cáo p50, p95, p99 theo từng nhóm endpoint, tỷ lệ lỗi, và log chuyển đổi ETag khi live publish |
| **7. Stress Testing & Forensics (`05`)** | Chạy 3 kịch bản IMF (`+300 bps`, `-20% doanh thu & -3 pp biên gộp`, kịch bản kép thanh khoản) trên ~383 mã + thống kê phân bố `RF_08`, `RF_09` (`BS_CIP_LONG_TERM` `242 -> 252`) theo ngành ICB2 | Bảng `% Debt-at-Risk` trước/sau sốc theo ngành ICB2 & danh sách top mã mới rơi xuống `ICR' < 1.0` |
| **8. Toàn vẹn Kiểm toán, Rollback & Hồi quy** | `orphan_filing_ids == 0`; `rollback_snapshot()` pass; `test_golden_ratios.py` (FPT 2024–2025) `100%` pass; `ruff format --check`, `ruff check`, `mypy --strict`, `pytest -q` `100%` pass | Log chạy lệnh thực tế không cắt xén |

### Bước 4 — Bộ Integration Test Bền vững Ngoại tuyến (`tests/integration/test_option_b_e2e_platform.py`)
Tạo bộ integration test khép kín hoàn toàn trong `tmp_path` từ fixture offline 0 mạng (chạy được trên cả Ubuntu & Windows GitHub Actions CI):
1. **Happy Path Full Stack:** Dữ liệu thô (`TT200 + TT99`, gồm `BS_CIP_LONG_TERM` `242 -> 252`) $\rightarrow$ `Ingest` + `Disclosure Enrichment` $\rightarrow$ `Layer 2 Batch/Incremental Normalize` $\rightarrow$ `Native Polars Incremental Transform` + `Blocking QG1/QG2 Audits` $\rightarrow$ `Atomic Snapshot Publish` (`DuckLake` hoặc `parquet` theo Gate B0) $\rightarrow$ **`FastAPI TestClient` kiểm thử ĐỦ CẢ 10 HTTP ROUTES** (gồm `ETag`/`304`, `38/38` `/lineage`, `/screener`, `POST /stress-test`, `/quality/summary`, `/quality/quarantine`) $\rightarrow$ `Curated GC` + `rollback_snapshot()`.
2. **Failing `QUALITY_GATE_1` Circuit-Breaker E2E (`HI-18`):** Bơm lô lỗi `> 10%` critical issuers $\rightarrow$ xác nhận `CriticalDataQualityError` ngắt mạch trước xuất bản và snapshot cũ trước đó vẫn hoạt động nguyên vẹn trên FastAPI!
3. **Mode B `REGULATORY_DISTRESS` Offline Library Contract E2E (`HI-18`):** Xác nhận hợp đồng nhãn pháp lý chạy đúng trên fixture ngoại tuyến (chống rò rỉ right-censoring & giữ forced delisting `y = 1`).
4. **Concurrent Read + Live Publish Race Test:** 8 luồng đọc FastAPI song song trong lúc 1 tiến trình xuất bản snapshot mới $\rightarrow$ `0` ngoại lệ, `0` cross-table read-skew, xác nhận `ETag` chuyển sạch sang snapshot mới.

### Bước 5 — Cổng Phê duyệt Cuối cùng Bởi `review-lead` Độc lập (`MED-07`)
* Nộp toàn bộ báo cáo `docs/evidence/V2_PLATFORM_BENCHMARK_VERIFICATION.md` + `git diff --stat v1.5-baseline-before-v2...HEAD` (đối chiếu tag mốc nền bắt buộc từ Pre-Wave 0!) + log chạy CI cho subagent **`review-lead`** (phối hợp `architecture-auditor`, `security-auditor`, `performance-auditor`, `test-auditor` theo `.agents/skills/review-phase/SKILL.md`).
* Chỉ đề xuất gắn tag hoàn thành **`v2.0-platform-verified`** sau khi nhận kết luận **`PASS`** không còn bất kỳ `VERIFIED_DEFECT` hay `HIGH_CONFIDENCE_STATIC` blocker nào.

---

## 3. PROMPT GIAO VIỆC MẪU CHO VERIFICATION AGENT (COPY-PASTE READY)

```text
Đọc AGENT.md, docs/v2_specs/00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md và docs/v2_specs/09_GATE_B6_UNIVERSE_MIGRATION_E2E_AND_BENCHMARK_VERIFICATION.md.

KIỂM TRA TIỀN ĐIỀU KIỆN BẮT BUỘC: Chỉ chạy cắt chuyển dữ liệu thật sau khi tôi xác nhận phiên thu thập ed48af80 đã dừng 100%, tag v1.5-baseline-before-v2 đã tồn tại, và toàn bộ nhánh 01..08 đã merge vào v2/integration!

Thực hiện ĐÚNG phạm vi Wave 5 (Gate B6) trên worktree v2/b6-verify:
1. Chỉ tạo các file trong mục "Owned Files" của 09_GATE_B6 (scripts/migrate_universe_to_v2.py, tests/integration/test_option_b_e2e_platform.py, docs/evidence/V2_PLATFORM_BENCHMARK_VERIFICATION.md). Chỉ đọc data/live_fhsc_universe/ (dùng sqlite3.backup() WAL-safe & kiểm tra cả payload_file_sha256 + canonical_content_sha256; tuyệt đối không sửa/xóa bản gốc).
2. KHÔNG tự ý sửa code trong src/ hay dashboard/ tại bước này — nếu phát hiện lỗi thật, dừng lại lập Defect Report rõ file/dòng/tái hiện.
3. Chạy toàn trình V2 (Native Polars Incremental Transform + Dagster + DuckLake hoặc Sorted Parquet+ReadWriteLock — KHÔNG SQLMesh, KHÔNG delta-rs) trên tập ~383 doanh nghiệp thật, đo đạc thực tế đủ 8 cổng có thể bác bỏ (gồm Ablation STATUTORY_LAG vs EXACT_FILING và BASE vs SECTOR_ADJUSTED, 0 < platt_slope <= 10, Top 15 dự báo Test, benchmark uvicorn thật 20 users + live publish refresh, rollback_snapshot) và ghi vào docs/evidence/V2_PLATFORM_BENCHMARK_VERIFICATION.md.
4. Viết tests/integration/test_option_b_e2e_platform.py (đủ 10 HTTP routes + QG1 circuit breaker + Mode B offline contract + 8-thread live publish) và chạy xanh 100% ruff, mypy --strict, pytest -q, test_golden_ratios.py.
```
