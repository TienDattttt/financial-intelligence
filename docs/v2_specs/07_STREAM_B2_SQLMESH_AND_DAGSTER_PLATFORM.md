# ĐẶC TẢ TRIỂN KHAI WAVE 3 — STREAM B2: NATIVE POLARS INCREMENTAL TRANSFORM ENGINE, BLOCKING QUALITY AUDITS & `Dagster` PARTITIONED ORCHESTRATION *(ĐÃ LOẠI BỎ `SQLMesh`)*

* **Mã đặc tả:** `SPEC-V2-07-STREAM-B2-v1.1`
* **Wave:** `Wave 3` (Chạy **sau khi** toàn bộ Wave 1 (`02`, `03`, `04`) và Wave 2 (`05`, `06`) đã merge vào `v2/integration`)
* **Nhánh Git / Worktree:** `v2/b2-platform` (`../finintel-wt-b2-platform`)
* **Quyết định Kiến trúc Bắt buộc (Sau Kiểm toán Độc lập — đóng `BLK-19`, `SEAM-04`, `MISS-03`, `MISS-04`, `MISS-05`):**
  > [!IMPORTANT]
  > **`SQLMesh` đã bị LOẠI BỎ CHÍNH THỨC khỏi Phase V2** (tên tệp đặc tả giữ nguyên theo quy tắc phạm vi tệp). Nguyên nhân đã được kiểm chứng bằng mã nguồn và tài liệu chính thức:
  > 1. `SQLMesh` **không hỗ trợ `state_connection: sqlite` độc lập** (`MISS-03`).
  > 2. 6 lỗi tích hợp dây chuyền (`BLK-19`): `mart_financial_ratios` cắt mất cột trung gian khiến `benchmark_ratios()` & `detect_red_flags()` sập ngay; `INCREMENTAL_BY_UNIQUE_KEY` tạo dòng trùng trên `ANNUAL` vì `fiscal_quarter IS NULL`; upsert để lại ghost red flags; phá môi trường ảo `dev`/`prod`; và tách `SQLMesh` khỏi `state.sqlite`/`DuckLakeStore` gây split-brain 4 kho metadata (`SEAM-04`).
  > 3. Ở quy mô 1 nút (`383–1,600` doanh nghiệp $\approx 10–40\text{ MB}$ Parquet), **Native Polars Incremental Transform (`src/finintel/transform/`)** dùng chung trực tiếp các hàm đã kiểm định của Tầng 4/6 chạy nhanh hơn, gọn hơn, 100% `mypy --strict`, và loại bỏ hoàn toàn 1 tầng hạ tầng dư thừa (`MISS-05`).
* **Mục tiêu cốt lõi V2:**
  1. **Phần 1 — Native Polars Incremental Transform & Blocking Audit Engine (`src/finintel/transform/`):** Thực thi gia tăng theo nguyên lý DBSP (`dirty_issuers` cho `financial_ratios` & `accounting_red_flags`; `dirty_period_end_dates` cho `industry_benchmarks`; tự động full rebuild benchmark khi `issuers.parquet` đổi ICB/regime), chạy chặn trước xuất bản `QUALITY_GATE_1` & `QUALITY_GATE_2` (gồm DuPont 5 & 6 nhân tố $\le 10^{-9}$, 0 orphan `filing_id`), và xuất bản **1 lần duy nhất nguyên tử 9 bảng** dưới `pipeline.lock` + `state.sqlite` `RUNNING` run!
  2. **Phần 2 — `Dagster` Local Single-Node Partitioned Assets (`config/dagster.yaml` & `src/finintel/orchestration/`):** Điều phối theo `CORPORATE_TICKERS` dynamic partitions, **phân tách tường minh Concurrency Pool (`concurrency.pools` `granularity: "op"`) với Request-Rate Pacing (`0.6 req/s` trong `FhscOapiAdapter`)**, khai báo đúng `deps=[raw_fhsc_filings]` cho `raw_public_disclosures`, yield `AssetMaterialization` từng partition trước khi fail-fast `RateLimitOrCircuitOpenError`, lưu metadata Dagster dưới `data/metadata/dagster/`, và lên lịch `lakehouse_maintenance_job` đồng bộ qua `pipeline.lock`.

---

## 1. QUYỀN SỞ HỮU FILE TUYỆT ĐỐI (`STRICT FILE OWNERSHIP`)

* **Owned Files (Chỉ được phép tạo mới các file sau — KHÔNG sửa code lõi ở Layer 0–4/6!):**
  * `config/dagster.yaml` *(mới — cấu hình `storage.sqlite.base_dir` dưới `data/metadata/dagster/` & `concurrency.pools`, KHÔNG đặt tại thư mục gốc Git!)*
  * `src/finintel/transform/__init__.py` *(mới)*
  * `src/finintel/transform/incremental.py` *(mới — Native Polars Incremental Transform Engine)*
  * `src/finintel/transform/audits.py` *(mới — Blocking Pre-Publish `QUALITY_GATE_2` + DuPont 5/6-factor & PK Audits)*
  * `src/finintel/orchestration/__init__.py` *(mới)*
  * `src/finintel/orchestration/partitions.py` *(mới)*
  * `src/finintel/orchestration/resources.py` *(mới)*
  * `src/finintel/orchestration/assets.py` *(mới)*
  * `src/finintel/orchestration/checks.py` *(mới)*
  * `src/finintel/orchestration/definitions.py` *(mới)*
  * `tests/unit/test_option_b_incremental_transform.py` *(mới)*
  * `tests/unit/test_option_b_dagster.py` *(mới)*
* **Read-Only Contract Files:** Toàn bộ `src/finintel/{domain,ingestion,normalization,storage,quality,analytics,ml,pipeline}/` đã ổn định sau Wave 1–2, `docs/evidence/option_b_spike_report.json`.
* **Forbidden Files:** Toàn bộ code lõi `src/finintel/{domain,ingestion,normalization,storage,quality,analytics,ml,pipeline}/` (KHÔNG xóa `cli.py`/`runner.py`!), `dashboard/`, `pyproject.toml`, `data/live_fhsc_universe/`, `AGENT.md`. *(CẤM cài hoặc import `sqlmesh` hay `deltalake`!)*

---

## 2. NGUỒN KHOA HỌC & TÀI LIỆU CHÍNH THỨC BẮT BUỘC ĐỐI CHIẾU TRƯỚC KHI CODE

1. **Budiu, M., Chajed, T., McSherry, F., Ryzhyk, L., & Tannen, V. (2023).** *"DBSP: Automatic Incremental View Maintenance for Rich Query Languages."* **PVLDB Vol. 16, No. 7** (VLDB 2023 Best Research Paper Award) — phân tách đơn vị cập nhật gia tăng: theo `dirty_issuers` cho bảng cấp công ty, theo `dirty_period_end_dates` cho bảng mặt cắt ngang toàn ngành.
2. **Dagster Official Docs & GitHub (BẮT BUỘC kiểm tra chữ ký API thực tế trong `.venv` bằng `inspect.signature`!):**
   * `https://docs.dagster.io/guides/build/partitions-and-backfills/`
   * `https://docs.dagster.io/guides/operate/configuration/dagster-yaml`
   * `https://docs.dagster.io/guides/limit-concurrency` *(xác nhận `concurrency.pools` `granularity: "op"` + `pool=` trên `@dg.asset` — KHÁC với `run_coordinator.tag_concurrency_limits` vốn chỉ áp lên run tags!)*
   * `https://docs.dagster.io/guides/test/asset-checks` (`@dg.asset_check(..., blocking=True)`)
   * `https://github.com/dagster-io/dagster`

---

## 3. ĐẶC TẢ KỸ THUẬT CHI TIẾT

### 3.1. Phần 1 — Native Polars Incremental Transform & Blocking Audit Engine (`src/finintel/transform/`)

1. **Single Authoritative Transform (`src/finintel/transform/incremental.py` — `BLK-19`, `SEAM-04`):**
   * Hàm chính `run_incremental_analytics_transform(...) -> TransformBundle` (100% gọi trực tiếp các hàm lõi `src/finintel/{quality,analytics,ml}/` — **0% copy-paste công thức VAS/ML!**):
     1. **Cổng Chất lượng 1 (`QUALITY_GATE_1`):** Gọi `evaluate_quality_gate(facts, issuers=issuers, run_id=run_id)` (`src/finintel/quality/gate.py`). Nếu `critical_issuers / total_issuers > 10%` $\rightarrow$ ném `CriticalDataQualityError` chặn đứng trước khi tính analytics hoặc xuất bản!
     2. **Tính tập thay đổi (`TransformDiff`):** Đọc trạng thái trước đó (`_transform_state.json` gắn với snapshot hiện hành trong `data/metadata/`):
        - So sánh `{issuer_id: sha256_of_clean_facts_rows}` giữa `clean_facts` mới và bản trước $\rightarrow$ `dirty_issuers = added | modified | deleted`.
        - So sánh `issuers_metadata_sha256` (băm `issuer_id`, `accounting_regime`, `icb_code_level2/3`, `industry_snapshot_date`, `delisted_date`) và `transform_config_sha256` (gồm `as_of` & cấu hình ML). Nếu đổi hoặc chưa có snapshot trước $\rightarrow$ `full_rebuild = True`.
     3. **Tầng 4A Gia tăng Không Mất Cột Trung gian (`BLK-19` #1 & #4):**
        - Chỉ lọc `clean_facts` của `dirty_issuers` (hoặc toàn bộ nếu `full_rebuild`) $\rightarrow$ gọi `periods = prepare_periods(dirty_facts, issuers, as_of=as_of)` $\rightarrow$ `ratios_rich = add_dupont(calculate_ratios(periods))`.
        - Giữ nguyên `ratios_rich` đầy đủ cột trung gian trong RAM khi gọi `detect_red_flags(ratios_rich)` (`RF_01..RF_09` — chỉ chiếu xuống `FINANCIAL_RATIOS_SCHEMA` ở bước tạo bảng xuất bản `new_ratios_pub`!).
        - Hợp nhất vào bản chụp trước bằng **delete-by-`issuer_id` + concat** (`prev_df.filter(~pl.col("issuer_id").is_in(dirty_issuers))` + `new_df`) cho cả `financial_ratios` lẫn `accounting_red_flags` $\implies$ **0 ghost red flags** khi cờ đỏ biến mất, **0 dòng `ANNUAL` bị nhân đôi** vì `fiscal_quarter IS NULL`!
     4. **Benchmark Ngành Gia tăng Theo `dirty_period_end_dates` (Bất biến Toán học DBSP):**
        - Xác định tập `dirty_slices = (statement_scope, period_type, period_end_date)` bị chạm bởi `dirty_issuers` (gồm cả kỳ vừa bị xóa!).
        - Nếu `issuers_metadata_sha256` đổi hoặc `full_rebuild` $\rightarrow$ tái tính toàn bộ `benchmark_ratios()` trên toàn tập `financial_ratios` gắn cột ICB từ `issuers` (`industry_snapshot_date <= as_of`).
        - Nếu chỉ đổi một phần: tái tính `benchmark_ratios()` trên **TẤT CẢ doanh nghiệp** có mặt trong `dirty_slices` (dùng khung tỷ số đã ghép ICB hợp lệ), sau đó thay thế vào `prev_benchmarks` bằng **anti-join theo `(statement_scope, period_type, period_end_date)` + concat**!
     5. **Tầng 4B ML có Cổng Kiểm soát (`BLK-19` #3):**
        - Nếu `ml_config.enabled is False`: trả `ml_scores = pl.DataFrame(schema=ML_SCORES_SCHEMA)`, `evaluation_report = None`, `ml_run_manifest = None`.
        - Nếu `ml_config.enabled is True`: ép buộc `require_exact_filing=True` (Guardrail G1) và `approved_target=True` (Guardrail G2), truyền đầy đủ `as_of`, `validation_start`, `test_start`, `statement_scope`, `taxonomy_version`, `scored_taxonomies`, `feature_set`, `target_mode`, `regulatory_events`, `peer_benchmarks=benchmarks`, `facts_sha256`, `issuers_sha256` vào `build_ml_artifacts()`, và trả kèm `evaluation_report` + `ml_run_manifest` để lưu trong thư mục metadata của run.
2. **Blocking Pre-Publish Audits (`src/finintel/transform/audits.py` — `BLK-19` #2):**
   * Hàm `run_blocking_publish_audits(bundle: TransformBundle, *, run_id: str) -> pl.DataFrame`:
     1. Gọi trực tiếp **`verify_analytics_quality_gate(...)`** (`src/finintel/quality/gate.py:315-455`) để kiểm tra 0 orphan `filing_id` trên `financial_ratios` (`filing_id` + `source_filing_ids`), `accounting_red_flags` (`filing_id`), `ml_scores_and_explanations` (`filing_id`), VÀ bảo toàn đầy đủ cơ chế bổ sung `DERIVED_Q4_INPUT` / `NEGATIVE_ROE` vào `quality_summary`!
     2. Kiểm tra tính duy nhất khóa chính (`ANALYTICS_KEYS`) cho toàn bộ bảng xuất bản.
     3. Kiểm tra **đẳng thức DuPont 5 nhân tố VÀ 6 nhân tố** với sai số chặt **`<= 1e-9`** (khớp `dupont.py`, KHÔNG nới lỏng lên `1e-6`!) trên mọi dòng có `dupont_valid_flag == True`:
        - 5 nhân tố: `|tax_burden * interest_burden * ebit_margin * asset_turnover * equity_multiplier_total - roe_total| <= 1e-9`.
        - 6 nhân tố (khi `parent_profit_share` & `equity_multiplier_parent` khác `NULL`): `|tax_burden * interest_burden * ebit_margin * asset_turnover * parent_profit_share * equity_multiplier_parent - roe_parent| <= 1e-9`.
     4. Mọi vi phạm lập tức ném **`CriticalDataQualityError`** **TRƯỚC** khi chạm vào `publish_snapshot()`, bảo toàn 100% snapshot đang phục vụ!

### 3.2. Phần 2 — `Dagster` Partitioned Orchestration (`config/dagster.yaml` & `src/finintel/orchestration/`)

1. **`config/dagster.yaml` (`BLK-19` #5 & #6, `MED-06`, Phân tách Concurrency vs Rate Limit):**
   * Đặt `storage.sqlite.base_dir` tại `data/metadata/dagster` (tuyệt đối không để SQLite của Dagster rơi ở thư mục gốc Git!), khuyến nghị `DAGSTER_HOME` trỏ tới thư mục cấu hình và bind `dagster-webserver` mặc định vào **`127.0.0.1`**.
   * **Phân tách rõ Giới hạn Đồng thời (`Concurrency Limit`) và Tốc độ Gọi API (`Rate Limit`):**
     ```yaml
     concurrency:
       pools:
         granularity: "op"
         default_limit: 4
     ```
     * Cấu hình pool op-level (thực thi bởi `dagster-daemon`, **KHÔNG** dùng `run_coordinator.tag_concurrency_limits` vốn chỉ áp cho run tags!):
       - `fhsc_api_pool`: **giới hạn đồng thời `limit = 1`** (đảm bảo tối đa 1 worker gọi FHSC tại một thời điểm).
       - `public_disclosure_pool`: **giới hạn đồng thời `limit = 3`**.
     * Ghi rõ trong docstring & README vận hành: `fhsc_api_pool = 1` chỉ là **giới hạn đồng thời (concurrency)**; **giới hạn tốc độ gọi `0.6 req/s` (`~1.67s/req`)** được cưỡng chế trực tiếp bên trong `FhscOapiAdapter` (`02_STREAM_A1`).
2. **`partitions.py`:**
   * `CORPORATE_TICKERS = dg.DynamicPartitionsDefinition(name="corporate_tickers")` kèm hàm tiện ích `sync_corporate_ticker_partitions(instance, issuers_path)` nạp danh sách mã từ `issuers.parquet`.
3. **`resources.py` (Backend-Agnostic — Không `delta-rs`, Không `SQLMesh`):**
   * `FinIntelPathsResource`, `StateStoreResource`, `StorageResource` (chọn `DuckLakeStore` khi `backend == "ducklake"` hoặc `SnapshotPublisher` khi `backend == "parquet"` theo `config/settings.yaml`), và `DisclosureFetcherResource` (HTTP client `httpx` có thể inject mock khi test, bắt buộc kiểm tra HTTPS host allowlist `{"iq.vietcap.com.vn", "s.cafef.vn", "cafef.vn"}`, `follow_redirects=False`, timeout $\le 15\text{s}$ theo `02_STREAM_A1`).
4. **`assets.py` (Khép kín Toàn bộ 9 Bảng & Khử Split-Brain `SEAM-04`):**
   * **`raw_fhsc_filings`** (`partitions_def=CORPORATE_TICKERS`, `backfill_policy=dg.BackfillPolicy.multi_run(max_partitions_per_run=10)`, `pool="fhsc_api_pool"`):
     - Gọi `IngestHandler`/`FhscOapiAdapter` (tắt `early_stop_predicate` khi backfill; bật cho sync định kỳ).
     - **Bảo toàn tiến độ lô 10 mã (`BLK-19` #6):** Lặp từng mã trong batch; ngay khi 1 mã hoàn tất, **phát `yield dg.AssetMaterialization(asset_key=..., partition=ticker, metadata=...)`**; nếu gặp `RateLimitOrCircuitOpenError`, phát cảnh báo rõ ràng rồi raise fail-fast mà KHÔNG làm mất trạng thái các mã đã hoàn tất trước đó!
   * **`raw_public_disclosures`** (**`deps=[raw_fhsc_filings]`** — sửa race condition `BLK-19` #6!, `partitions_def=CORPORATE_TICKERS`, `pool="public_disclosure_pool"`):
     - Tải/đọc hồ sơ Vietcap IQ & CafeF qua `DisclosureFetcherResource` (đã allowlist + confinement), gọi `merge_disclosure_records()` và `enrich_fhsc_manifest_disclosures(..., tickers=batch_tickers)` (`02_STREAM_A1`) lưu biên lai SHA-256 vào `raw/provenance/`.
   * **`normalized_financial_facts`** (`deps=[raw_fhsc_filings, raw_public_disclosures]`):
     - Gọi `normalize_archive_incremental()` (`03_STREAM_A2`) tạo batch `curated/financial_facts/<run_id>/`.
   * **`published_financial_marts`** (`deps=[normalized_financial_facts]`):
     - **Giao thức Xuất bản Đơn-Điểm An toàn (`SEAM-04`, `MISS-04`):**
       1. Giữ khóa ghi duy nhất `pipeline.lock` (cùng khóa với CLI `runner.py`!).
       2. Đăng ký `run_id` trạng thái `RUNNING` trong `state.sqlite.pipeline_runs` (thỏa `PRAGMA foreign_keys = ON` tại `state_store.py:63,183-191`!).
       3. Gọi `run_incremental_analytics_transform()` $\rightarrow$ `run_blocking_publish_audits()` (fail-fast nếu vi phạm!).
       4. Dựng `latest_facts` & `filings_manifest` (từ `clean_facts` trong RAM, 1× đọc) $\rightarrow$ xuất bản **1 lần duy nhất toàn bộ 9 bảng nguyên tử** qua `StorageResource` (`DuckLakeStore.publish_snapshot` hoặc `SnapshotPublisher.publish`), sau đó chốt `state.finish_run(run_id, status="SUCCEEDED")` (hoặc `"FAILED"` trong `except`).
   * **`lakehouse_maintenance_job` + `lakehouse_maintenance_schedule` (`HI-17`, `MISS-04`):**
     - Lịch chạy định kỳ (`cron_schedule="0 3 * * 0"`, `execution_timezone="Asia/Ho_Chi_Minh"`), bắt buộc giữ **`pipeline.lock`** trước khi gọi `DuckLakeStore.maintain()` (nếu backend `"ducklake"`) và `collect_curated_garbage()` (`04_STREAM_B1`).
5. **`checks.py` & `definitions.py`:**
   * `@dg.asset_check(asset=published_financial_marts, blocking=True)` cho `QUALITY_GATE_1` (`critical_issuer_ratio <= 10%`) và `QUALITY_GATE_2` (`orphan_filing_ids == 0`, DuPont 5/6-factor $\le 10^{-9}$).
   * Export `defs = dg.Definitions(assets=..., asset_checks=..., jobs=..., schedules=..., resources=...)` trong `definitions.py`.

---

## 4. KIỂM THỬ TDD & TIÊU CHÍ NGHIỆM THU (`DEFINITION OF DONE`)

1. **`tests/unit/test_option_b_incremental_transform.py`:**
   * **Incremental Equivalence (`1e-12`):** Chạy full 10 mã $\rightarrow$ (a) thêm 1 mã mới + cập nhật 1 kỳ của 1 mã cũ + xóa 1 cờ đỏ của 1 mã khác $\rightarrow$ chạy incremental $\rightarrow$ xác nhận `financial_ratios`, `industry_benchmarks`, `accounting_red_flags` khớp **`1e-12`** với full rebuild từ đầu, **0 ghost red flags**, **0 duplicate `ANNUAL` rows**!
   * **ICB Change Auto-Full-Rebuild:** Đổi `icb_code_level2` của 1 mã trong `issuers.parquet` $\rightarrow$ xác nhận `industry_benchmarks` tự động rebuild toàn bộ và khớp full rebuild.
   * **Blocking Quality Audits:**
     - Bơm `> 10%` issuer lỗi `BS_IDENTITY_MISMATCH` $\rightarrow$ `CriticalDataQualityError` chặn xuất bản.
     - Bơm 1 `orphan_filing_id` giả (trên `financial_ratios`, `accounting_red_flags`, hoặc `ml_scores`) $\rightarrow$ `run_blocking_publish_audits` ném `CriticalDataQualityError`.
     - Bơm sai lệch DuPont `> 1e-9` (cả 5 nhân tố và 6 nhân tố) $\rightarrow$ ném `CriticalDataQualityError`.
     - Xác nhận `DERIVED_Q4_INPUT` & `NEGATIVE_ROE` vẫn được lan truyền đầy đủ vào `quality_summary`.
   * **ML Enabled vs Disabled:** Xác nhận `ml_enabled=False` trả DataFrame rỗng đúng `ML_SCORES_SCHEMA`; `ml_enabled=True` truyền đủ tham số PIT và lưu báo cáo đánh giá.
2. **`tests/unit/test_option_b_dagster.py`:**
   * `defs.validate_loadable()` sạch không lỗi; xác nhận `raw_public_disclosures` phụ thuộc `raw_fhsc_filings` và dùng đúng `pool="fhsc_api_pool"` / `pool="public_disclosure_pool"`.
   * `dg.materialize(...)` end-to-end với mock FHSC + mock `DisclosureFetcherResource` trên 3 dynamic partitions trong `tmp_path` $\rightarrow$ tất cả assets & `@dg.asset_check` xanh, `state.sqlite` ghi nhận `pipeline_runs` (`SUCCEEDED`) + `active_snapshot` hợp lệ, xuất bản đủ 9 bảng và `ParquetRepository` đọc sạch.
   * Test fail-fast `RateLimitOrCircuitOpenError` giữa batch 3 mã: xác nhận mã 1 đã phát `AssetMaterialization` và lưu dữ liệu bền vững trước khi mã 2 raise lỗi.
   * Test `lakehouse_maintenance_job` tôn trọng `pipeline.lock` và không đụng file đang dùng.
3. **Cổng chất lượng bắt buộc:** `ruff format --check`, `ruff check`, `mypy --strict`, `pytest -q` xanh 100% (100% CLI/Runner cũ không bị phá vỡ!).

---

## 5. PROMPT GIAO VIỆC MẪU CHO CODING AGENT (COPY-PASTE READY)

```text
Đọc AGENT.md, docs/v2_specs/00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md và docs/v2_specs/07_STREAM_B2_SQLMESH_AND_DAGSTER_PLATFORM.md.

Thực hiện ĐÚNG phạm vi Wave 3 (Stream B2) trên git worktree riêng v2/b2-platform (tách từ v2/integration sau khi Wave 1 & Wave 2 đã merge):
1. CHỈ tạo các file mới trong mục "Owned Files" của 07_STREAM_B2. LƯU Ý: SQLMesh và deltalake ĐÃ BỊ LOẠI BỎ khỏi V2 — CẤM cài/import sqlmesh hoặc deltalake; CẤM sửa code lõi trong src/finintel/{domain,ingestion,normalization,storage,quality,analytics,ml,pipeline}/ và CẤM copy-paste công thức tài chính VAS/ML.
2. Đối chiếu DBSP (Budiu et al., VLDB 2023) và Dagster Docs (partitions, concurrency.pools granularity="op", blocking asset checks); kiểm tra chữ ký API thật trong .venv bằng inspect.signature trước khi code và ghi Verified Sources trong docstring.
3. Đảm bảo: (a) incremental delete-by-issuer_id (ratios/red_flags) + delete-by-slice (benchmarks) + auto-full-rebuild khi issuers.parquet đổi ICB; (b) blocking QG1/QG2 giữ nguyên verify_analytics_quality_gate + DuPont 5/6-factor <= 1e-9; (c) Dagster dùng concurrency.pools (KHÔNG dùng run_coordinator.tag_concurrency_limits), deps=[raw_fhsc_filings], yield AssetMaterialization từng mã trước fail-fast, đặt SQLite Dagster dưới data/metadata/dagster/, và đồng bộ ghi/maintenance qua pipeline.lock + StateStore RUNNING run.
4. Viết đầy đủ test trong test_option_b_incremental_transform.py và test_option_b_dagster.py; chạy xanh 100% ruff, mypy --strict, pytest -q.
```
