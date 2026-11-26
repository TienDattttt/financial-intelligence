# Kế Hoạch Thực Thi Nguyên Tử FinIntel V2.0 (`tasks/plan.md`)

> **Nhánh thực thi:** `feat/v2-autonomous-finintel`  
> **Nguyên tắc bất biến:** Tách riêng tuyệt đối commit `Behavior-Preserving` (`TASK-01`) khỏi các commit `Behavior-Changing` (`TASK-02..12`); không ghi/sửa/xóa `data/live_fhsc_universe/` và `data/universe_600_800/`; mọi kiểm thử chạy offline trên `tmp_path`.

---

## Tổng Quan Chuỗi 13 Task Nguyên Tử & Các Cổng Kiểm Chứng

| Mã Task | Phân loại Commit (`AGENTS.md` §2.2.1) | Năng lực (`Capability`) | Tệp sản phẩm tối đa | Trạng thái |
|---|---|---|---|---|
| `TASK-00` | Governance & Spec Baseline | Khởi tạo `CONSTRAINTS.md`, `spec/`, `tasks/`, `scripts/check_constraints.py`, `docs/runbooks/` | `6` tệp mới | Hoàn tất (Đợt 1) |
| `TASK-01` | **Behavior-Preserving** *(100% giữ nguyên hành vi)* | `cap-04`: Phân rã `serving/queries.py` -> gói `serving/queries/` (`< 1.000` dòng/file, `0` sửa `tests/`) | `7` tệp gói con (xóa file đơn) | Hoàn tất (Đợt 1) |
| `TASK-02` | Behavior-Changing | `cap-01`: Hợp nhất `FININTEL_DATA_ROOT` + chặn `DisclosureFetcherResource` báo pass giả offline | `6` tệp | Hoàn tất (Đợt 1) |
| `TASK-03` | Behavior-Changing | `cap-01` + `cap-04`: Pacing SQLite `< 1ms` liên tiến trình + `ssrf_guard.py` + Telemetry `entry_point` | `6` tệp | Hoàn tất (Đợt 1) |
| *Cổng 1* | **Verification Checkpoint #1** | Chạy toàn bộ `CONSTRAINTS.md` sau `TASK-00..03` | — | Hoàn tất (Cuối Đợt 1) |
| `TASK-04` | Behavior-Changing | `cap-02`: Sửa Dagster retrain path, `pipeline_write_pool=1`, Sensor BCTC `15m` & Quét TT96 Watermark `30m` | `6` tệp | Hoàn tất (Đợt 2) |
| `TASK-05` | Behavior-Changing | `cap-02`: Port 3 EOD an toàn chia tách + Port 1 replay pháp lý + Đối soát chéo `FHSC ↔ Vietcap IQ` | `6` tệp | Hoàn tất (Đợt 2) |
| `TASK-06` | Behavior-Changing | `cap-02`: Port 2 PDF CAS (`<= 400` trang) + Dagster Port 1/2/3 schedules + Docker `dagster-daemon` (`--read-only` UI) | `6` tệp | Hoàn tất (Đợt 2) |
| *Cổng 2* | **Verification Checkpoint #2** | Chạy toàn bộ `CONSTRAINTS.md` + kiểm chứng Dagster definitions sau `TASK-04..06` | — | Hoàn tất (Cuối Đợt 2) |
| `TASK-07` | Behavior-Changing | `cap-03`: Cờ đỏ Hậu kiểm toán `RF_10_POST_AUDIT_PROFIT_DOWNGRADE` (`severity="HIGH"`, `restatements.py` + DBSP bump) | `5` tệp | Hoàn tất (Đợt 3) |
| `TASK-08` | Behavior-Changing | `cap-03`: Chỉ báo cấu trúc thị trường `Merton DTD` (`merton_dtd.py` cho Overlay + Walk-Forward ML Ablation độc lập) | `5` tệp | Hoàn tất (Đợt 3) |
| *Cổng 3* | **Verification Checkpoint #3** | Kiểm chứng định lượng Layer 4A/4B (`0%` look-ahead, `0` QG2 orphan, DuPont `<= 1e-9`) | — | Hoàn tất (Cuối Đợt 3) |
| `TASK-09` | Behavior-Changing | `cap-04`: Con trỏ tiến SQLite/Postgres + Composite `ETag` + API Freshness/DTD + OTel spans + Sanitizer | `6` tệp | Hoàn tất (Đợt 4) |
| `TASK-10` | Behavior-Changing | `cap-05`: Frontend đồng bộ `X-Snapshot-ID`, tích hợp thẻ `Merton DTD` + bằng chứng `RF_10` & mã nhãn VN | `6` tệp | Hoàn tất (Đợt 4) |
| `TASK-11` | Behavior-Changing | `cap-05`: Nâng cấp WAI-ARIA `frontend/src/components/ui/segmented-control.tsx`, thanh Freshness 3 mốc `200%` zoom & chuẩn hóa `SourceDrawer` | `6` tệp | Hoàn tất (Đợt 4) |
| `TASK-12` | Behavior-Changing | `cap-05`: Tab/vùng cuộn `/methodology` A11y + Vitest JSDOM `.tsx` + Cổng `wcag22aa` & CWV + Sidecar `/automation` | `5` tệp | Đợt 4 |

---

## Chi Tiết Đợt 1 (`TASK-00` → `Verification Checkpoint #1`)

### `TASK-00` — Khởi Tạo Hồ Sơ Quản Trị Cơ Học & Baseline V2.0
- **Loại commit:** `chore(governance)`
- **Tệp tạo mới (`6` tệp):** `CONSTRAINTS.md`, `spec/capability-map.md`, `spec/SPEC-v2-autonomous-institutional-finintel.md`, `tasks/plan.md`, `scripts/check_constraints.py`, `docs/runbooks/autonomous_pipeline.md`.
- **Nghiệm thu:** Chạy đầy đủ các lệnh kiểm chứng trong `CONSTRAINTS.md`.

### `TASK-01` — Phân Rã `serving/queries.py` Thành Gói Domain (`100% Behavior-Preserving`)
- **Loại commit:** `refactor(serving)` (**Behavior-Preserving tuyệt đối:** `0` thay đổi logic, `0` sửa đổi bất kỳ file nào trong `tests/`).
- **Phạm vi:** Tách `src/finintel/serving/queries.py` (`5.019` dòng) thành gói `src/finintel/serving/queries/` với mọi file con `< 1.000` dòng, giữ đúng `parents[4]`, `_DEFAULT_READ_TABLE = ParquetRepository.read_table`, `__all__`, docstring `Verified Sources:`, `0` chuỗi `"data/raw"` / `"data/curated"` và `0` top-level import nhóm `platform`.

### `TASK-02` — Hợp Nhất `FININTEL_DATA_ROOT` & Khóa Chặn Chế Độ Offline Giả Trong Dagster (`cap-01`)
- **Loại commit:** `fix(config,orchestration)` (`Behavior-Changing`).
- **Phạm vi (`6` tệp sản phẩm + test đơn vị):** `config/settings.yaml`, `src/finintel/config.py`, `src/finintel/pipeline/cli.py`, `src/finintel/orchestration/resources.py`, `src/finintel/orchestration/assets.py`, `scripts/start_demo_685.ps1`.
- **Chi tiết:** Ghi đè đồng bộ mọi đường dẫn lưu trữ trong `Settings` khi đặt `FININTEL_DATA_ROOT`; đổi mặc định `DisclosureFetcherResource.fetch_online` theo `FININTEL_DISCLOSURE_FETCH_ONLINE` (khi `fetch_online=False`, `disclosure_dates_enrichment` xuất `status="SKIPPED_OFFLINE"`, `enriched_filings=0`); đồng bộ `scripts/start_demo_685.ps1` đọc từ `FININTEL_DATA_ROOT`.

### `TASK-03` — Pacing Liên Tiến Trình SQLite `< 1ms`, Chốt Chặn SSRF & Telemetry Đầu Vào (`cap-01`, `cap-04`)
- **Loại commit:** `feat(ingestion,observability)` (`Behavior-Changing`).
- **Phạm vi (`6` tệp sản phẩm + test đơn vị):** `src/finintel/ingestion/ssrf_guard.py` *(mới)*, `src/finintel/pipeline/state_store.py`, `src/finintel/pipeline/health.py`, `src/finintel/ingestion/fhsc_oapi_adapter.py`, `src/finintel/ingestion/disclosure_enricher.py`, `src/finintel/serving/observability.py` (+ `tests/unit/test_fhsc_pacing_lease.py`).
- **Chi tiết:** `acquire_host_pacing_slot()` (`BEGIN IMMEDIATE` `< 1ms`, `time.sleep` ngoài khóa) áp dụng cho cả `FhscOapiAdapter` (`min_interval=1.75s`) và `health_check(online=True)`; `ssrf_guard.py` chặn IP nội bộ/loopback/metadata + HTTP redirect + giới hạn kích thước đọc với `Accept-Encoding: identity`; bổ sung ngữ cảnh `entry_point` và metrics `finintel_ingestion_requests_total`, `finintel_ingestion_duration_seconds`.

---

## Chi Tiết Đợt 2 (`TASK-04` → `Verification Checkpoint #2`)

### `TASK-04` — Chuẩn Hóa Huấn Luyện Lại ML, Pool Ghi Đơn, Sensor BCTC `15m` & Quét TT96 Watermark `30m` (`cap-02`)
- **Loại commit:** `feat(orchestration)` (`Behavior-Changing`).
- **Phạm vi (`6` tệp sản phẩm + test đơn vị):** `config/dagster.yaml`, `config/dagster.postgres.yaml`, `src/finintel/pipeline/state_store.py`, `src/finintel/orchestration/partitions.py`, `src/finintel/orchestration/assets.py`, `src/finintel/orchestration/definitions.py`.
- **Chi tiết:** Chuẩn hóa đường dẫn tương đối `regulatory_ledger/regulatory_events.parquet`, gắn `pool="pipeline_write_pool"` (`limit=1`) và khởi tạo sẵn trong `DEFAULT_CONCURRENCY_POOL_LIMITS`, ghi nhận cooldown FHSC 429 & thất bại quét watermark, giới hạn tối đa `3` lần thử lại ở `downstream_publish_sensor`, bổ sung bảng `ticker_sweep_watermarks` (`*/30 * * * *`, `< 2.86` ngày/685 mã) và `bctc_disclosure_feed_sensor` (`900s`, tối đa `5` mã/nhịp, đối chiếu đúng ngày công bố thực `EXACT_FILING`).

### `TASK-05` — Port 3 An Toàn Chia Tách Cổ Tức, Port 1 Replay Pháp Lý & Đối Soát Chéo `FHSC ↔ Vietcap IQ` (`cap-02`)
- **Loại commit:** `feat(ingestion,normalization)` (`Behavior-Changing`).
- **Phạm vi (`6` tệp sản phẩm + test đơn vị):** `src/finintel/normalization/reconciliation.py` *(mới)*, `src/finintel/ingestion/market_prices_collector.py`, `src/finintel/ingestion/regulatory_events_collector.py`, `scripts/collect_regulatory_events.py`, `src/finintel/quality/gate.py`, `src/finintel/serving/observability.py`.
- **Chi tiết:** Đồng bộ giá EOD cửa sổ chồng lấn `5` phiên phát hiện chia tách/cổ tức (`|Δclose_adj| > 1e-3`) cùng nhà cung cấp tự tải lại `2018-01-01..today` và bảo toàn `shares_outstanding_market`; gỡ bỏ mốc chặn cứng cuối năm 2026, khóa ngoại tuyến `fetch_online=False` và nạp lại toàn bộ biên lai thô kèm trang đầu mới ở Port 1; cài đặt `reconcile_cross_provider_facts()` khớp cùng cấp kiểm toán phát `CROSS_PROVIDER_DISCREPANCY_WARN` vào `info_codes` (giữ nguyên toàn bộ phiên bản lịch sử trong `clean_facts`) và phơi bày `finintel_provider_discrepancy_total` qua `/metrics`.

### `TASK-06` — Gia Cố Port 2 PDF Từ Kho CAS, Lịch Chạy Dagster Port 1/2/3 & Docker Daemon Tự Trị (`cap-02`)
- **Loại commit:** `feat(ingestion,orchestration,docker)` (`Behavior-Changing`).
- **Phạm vi (`6` tệp sản phẩm + test đơn vị):** `src/finintel/ingestion/curated_pdf_service.py` *(mới)*, `src/finintel/rag/parser.py`, `src/finintel/orchestration/assets.py`, `src/finintel/orchestration/definitions.py`, `docker-compose.yml`, `Dockerfile`.
- **Chi tiết:** Trích xuất liên kết `.pdf`/`.zip` trực tiếp từ kho CAS `RawArchive` (lazy-import `pymupdf`/`pgvector`, pacing `0.6s`/host và chỉ khóa `pipeline.lock` lúc ghi manifest), chặn cứng `<= 400` trang và `<= 4096px`; đăng ký 3 asset & lịch chạy UTC `RUNNING` (`market_prices_eod_schedule`, `regulatory_events_ledger_schedule`, `curated_pdf_rag_schedule`); đồng bộ `uv:0.9.30` + `DAGSTER_HOME=/app/dagster_home` (`dagster.postgres.yaml`), bootstrap tự động pools/partitions trên `dagster-daemon` và tách `dagster-webserver --read-only` trên `127.0.0.1:3002`.

---

## Chi Tiết Đợt 3 (`TASK-07` → `Verification Checkpoint #3`)

### `TASK-07` — Tín Hiệu Điều Tra Hậu Kiểm Toán `RF_10_POST_AUDIT_PROFIT_DOWNGRADE` (`cap-03`)
- **Loại commit:** `feat(analytics)` (`Behavior-Changing`).
- **Phạm vi (`5` tệp sản phẩm + test đơn vị):** `src/finintel/analytics/restatements.py` *(mới)*, `src/finintel/analytics/intermediates.py`, `src/finintel/analytics/red_flags.py`, `src/finintel/transform/incremental.py`, `src/finintel/serving/dictionary.py`.
- **Chi tiết:** So sánh tổng 4 quý tự lập `UNAUDITED` rời rạc trước kiểm toán (loại trừ `DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX` và các bản sửa đổi sau kiểm toán, hỗ trợ niên độ lệch lịch `fiscal_year_start_date`, tổng ngày `[350, 380]`) với BCTC `ANNUAL` `AUDITED`; kích hoạt `RF_10_POST_AUDIT_PROFIT_DOWNGRADE` (`severity="HIGH"`) khi đảo chiều lãi-thành-lỗ (`<= -1 tỷ VND`) hoặc bốc hơi lợi nhuận trọng yếu (`<= -15%` và `<= -5 tỷ VND`), khóa PIT cấp dòng (`_period_available_at >= annual_audited_available_at`, mang theo trên `QUARTER` trong `(0, 275]` ngày), tăng `TRANSFORM_STATE_SCHEMA_VERSION = 2` và đạt `0` orphan `filing_id` ở `QG2`.

### `TASK-08` — Khoảng Cách Vỡ Nợ Cấu Trúc Thị Trường `Merton DTD` & Kiểm Chứng Walk-Forward (`cap-03`)
- **Loại commit:** `feat(analytics,ml)` (`Behavior-Changing`).
- **Phạm vi (`5` tệp sản phẩm + test đơn vị):** `src/finintel/analytics/merton_dtd.py` *(mới, `<= 450` LOC)*, `src/finintel/analytics/market_valuation.py`, `src/finintel/serving/queries/stress_and_overlay.py`, `src/finintel/ml/supervised/train.py`, `src/finintel/ml/supervised/explain.py` (+ `tests/unit/test_ui09b_market_valuation.py`).
- **Chi tiết:** Cài đặt `compute_merton_dtd_series()` và `attach_merton_dtd_to_periods()` theo phương trình Bharath & Shumway (2008): hợp nhất đủ 3 khoản mục nợ vay VAS (`BS_SHORT_TERM_BORROWINGS`, `BS_LONG_TERM_BORROWINGS`, `BS_CONVERTIBLE_BONDS` mã `338/339/340`) ở đơn vị `VND` gốc, phân biệt `VALID` / `MISSING_BCTC_DEBT` / `ZERO_FINANCIAL_DEBT` / `STALE_BCTC_DEBT` (`> 180d`) / `INSUFFICIENT_TRADING_SESSIONS` (`< 60` cặp phiên cách nhau `<= 14d` hoặc dừng giao dịch `volume == 0` quá `14d`) / `MISSING_SHARES`; căn chỉnh PIT 2 bước (`strategy="forward"` từ ngày công bố sang phiên giao dịch, sau đó `strategy="backward", tolerance="180d"`, không lùi kỳ `period_end_date`) đạt `0%` look-ahead cuối tuần và tiến `_period_available_at` sang `max(a_t, trade_date)` trước `build_distress_targets`; đăng ký ràng buộc đơn điệu XGBoost (`merton_dtd: -1`, `equity_volatility_1y: +1`, `naive_edf: +1`) cùng `MERTON_ABLATION_FEATURES` trong `train.py` và nhãn tiếng Việt trong `explain.py`.

---

## Chi Tiết Đợt 4 (`TASK-09` → Nghiệm Thu Cuối Hệ Thống)

### `TASK-09` — Con Trỏ Tiến SQLite/Postgres, Composite `ETag`, API Freshness/DTD, OTel Spans & Sanitizer (`cap-04`)
- **Loại commit:** `feat(serving,storage,observability)` (`Behavior-Changing`).
- **Phạm vi (`6` tệp sản phẩm + test):** `src/finintel/serving/freshness.py` *(mới)*, `src/finintel/serving/observability.py`, `src/finintel/serving/schemas.py`, `src/finintel/serving/api.py`, `src/finintel/storage/ducklake_store.py`, `src/finintel/orchestration/assets.py` (+ `tests/unit/test_serving_latency_budget.py`, cập nhật khẳng định composite `ETag` ở `test_layer5_fastapi_serving.py` và `test_option_b_e2e_platform.py`).
- **Chi tiết:** Bổ sung view `public.finintel_serving_pointer` và chốt chặn đơn điệu tiến `ducklake_version` trong `publish_snapshot()` / `reconcile_unpromoted_snapshots()`; xuất Composite `ETag` `"{snapshot_id}:{extension_ports_digest}"` và khóa Valkey L2 xoay tự động khi Port 1/2/3 cập nhật giữa hai kỳ BCTC; chặn tham số công khai `?snapshot_id=` (`HTTP 422`); mở rộng `PriceOverlayPointItem` với `merton_dtd` / `equity_volatility_1y` / `naive_edf` / `dtd_flag` và `MetaPeriodsResponse.freshness` (`DataFreshnessItem` 3 tầng); gắn đủ 4 span OTel chuẩn (`finintel.ingest.fetch`, `finintel.transform.incremental`, `finintel.storage.publish_snapshot`, `finintel.serving.query`), metrics `finintel_snapshot_freshness_age_seconds{port}` + `finintel_wap_publish_total{outcome}`, và bộ lọc `sanitize_error_detail` chống lộ đường dẫn/DSN/SQL/token nội bộ; kiểm chứng đạt ngân sách độ trễ `p95` (`Warm <= 50ms`, `DuckLake Cold <= 200ms`, `Stress-Test <= 300ms`).

### `TASK-10` — Đồng Bộ `X-Snapshot-ID`, Thẻ `Merton DTD` & Nhãn Bằng Chứng `RF_10` (`cap-05`)
- **Loại commit:** `feat(frontend)` (`Behavior-Changing`).
- **Phạm vi (`6` tệp sản phẩm + test đơn vị):** `frontend/src/lib/market-overlay-types.ts`, `frontend/src/lib/api-client.ts`, `frontend/src/components/providers/app-providers.tsx`, `frontend/src/lib/domain-labels.ts`, `frontend/src/components/charts/PriceRiskOverlayChart.tsx`, `frontend/src/components/rag/DocumentInvestigationPanel.tsx` (+ `frontend/tests/unit/task10_snapshot_dtd_rf10.test.ts`, cập nhật `frontend/tests/unit/ui08b.test.ts`).
- **Chi tiết:** Giám sát tiêu đề phản hồi `X-Snapshot-ID` trên mọi lời gọi `fetch` với debounce `150ms` để xoay đồng bộ toàn bộ khóa TanStack Query (`[*, snapshotId]`) mà không bị lệch phiên giữa các panel, đồng thời phơi bày `freshness` trên `useAppState()`; mở rộng `PitValuationStrip` lên 4 thẻ bổ sung **Merton DTD (Thị trường)** kèm `EDF` ngụ ý, biến động giá `1Y`, tooltip/bảng dữ liệu và giải thích tiếng Việt sạch cho `ZERO_FINANCIAL_DEBT` / `INSUFFICIENT_TRADING_SESSIONS` / `STALE_BCTC_DEBT`; bổ sung ánh xạ bằng chứng kế toán `RF_10` (`ni_unaudited_4q_sum`, `ni_audited_annual`, `audit_earnings_diff_vnd`, `audit_adjustment_pct`), câu hỏi điều tra PDF định sẵn `RF_10`, nhãn ML `merton_dtd` / `equity_volatility_1y` / `naive_edf` và nhãn đối soát chéo `CROSS_PROVIDER_DISCREPANCY_WARN`.

### `TASK-11` — Nâng Cấp WAI-ARIA `segmented-control.tsx`, Thanh Freshness 3 Mốc & Chuẩn Hóa `SourceDrawer` (`cap-05`)
- **Loại commit:** `feat(frontend)` (`Behavior-Changing`).
- **Phạm vi (`6` tệp sản phẩm + test đơn vị):** `frontend/src/components/ui/segmented-control.tsx`, `frontend/src/components/ui/info-tooltip.tsx`, `frontend/src/components/layout/AppShell.tsx`, `frontend/src/components/layout/SourceDrawer.tsx`, `frontend/src/components/company/OverviewTab.tsx`, `frontend/src/components/financial/RedFlagChecklist.tsx` (+ `frontend/tests/unit/task11_a11y_freshness_drawer.test.ts`).
- **Chi tiết:** Nâng cấp `segmented-control.tsx` đạt chuẩn WAI-ARIA Tabs / Radiogroup với roving `tabIndex`, điều hướng bàn phím (`ArrowRight`/`ArrowDown`, `ArrowLeft`/`ArrowUp`, `Home`, `End`), liên kết `aria-controls` và kích thước vùng bấm tối thiểu `>= 24x24px` (`WCAG 2.2 AA SC 2.5.8`, đồng bộ trên cả `InfoTooltip` và nút hành động nội dòng); bổ sung thanh độ tươi dữ liệu 3 tầng (`BCTC`, `Giá EOD`, `Pháp lý`) co giãn không tràn ngang ở `1440x900`, `720x450` (`200%` zoom) và `320x800`; chuẩn hóa `SourceDrawer` (`formatFilingReference` khử đuôi tệp và mã nội bộ, nhãn dự phòng an toàn cho `taxonomy_version`) và cập nhật bộ đếm `10` quy tắc cờ đỏ trên `OverviewTab` / `RedFlagChecklist`.

---

## Ghi Chú Cho Đợt 3 & Đợt 4 (Đã Khóa Theo Quyết Định Nghiệp Vụ)
- **`TASK-07` (`RF_10`):** Giữ `severity = "HIGH"` cho `RF_10_POST_AUDIT_PROFIT_DOWNGRADE`; loại trừ `DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX` và tăng `TRANSFORM_STATE_SCHEMA_VERSION`.
- **`TASK-08` (`Merton DTD`):** Cài đặt `merton_dtd.py` phục vụ đồng thời **Market Price & Risk Overlay** và đánh giá **Walk-Forward Ablation độc lập** (cộng đủ `BS_CONVERTIBLE_BONDS`, tách `ZERO_FINANCIAL_DEBT` khỏi `MISSING_BCTC_DEBT`, căn chỉnh 2 bước chống look-ahead cuối tuần).
- **`TASK-11` & `TASK-12` (UI/A11y):** Sửa đúng tệp thực tế trên đĩa `frontend/src/components/ui/segmented-control.tsx` (kebab-case) để đạt chuẩn WAI-ARIA Tabs/Radiogroup và vượt cổng `wcag22aa` ở `100%` & `200%` zoom.
