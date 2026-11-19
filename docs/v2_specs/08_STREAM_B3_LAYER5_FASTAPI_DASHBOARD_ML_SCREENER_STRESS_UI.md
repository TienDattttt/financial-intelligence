# ĐẶC TẢ TRIỂN KHAI WAVE 4 — STREAM B3: TẦNG 5 (`SERVING`) — FASTAPI ĐA LUỒNG, STREAMLIT CACHE, TREESHAP WATERFALL, PEER BAND TIME-SERIES, HONEST SCREENER & STRESS TEST UI

* **Mã đặc tả:** `SPEC-V2-08-STREAM-B3-v1.1`
* **Wave:** `Wave 4` (Chạy **sau khi** Wave 1C (`04`), Wave 2A (`05`), Wave 2B (`06`) và Wave 3 (`07`) đã merge vào `v2/integration`)
* **Nhánh Git / Worktree:** `v2/b3-serving` (`../finintel-wt-b3-serving`)
* **Mục tiêu toàn diện:**
  1. **Option B Chặng B5 (Kiến trúc Serving Chuẩn & Bảo mật — đóng `BLK-04`, `BLK-14`, `BLK-15`, `HI-16`, `MED-06`, `MED-08`):**
     * Chuyển `DashboardQueryService` thuần headless về **`src/finintel/serving/queries.py`** (giữ `dashboard/queries.py` là thin re-export shim tương thích ngược 100% — chấm dứt lỗi đảo ngược tầng `serving -> dashboard`!).
     * Xây dựng **FastAPI Read-Only Analytical API** (10 HTTP routes trên 9 nhóm endpoint): `:memory:` + `ATTACH ... (READ_ONLY)` (khi dùng DuckLake), đóng kết nối sạch khi shutdown, sync `def get_db_cursor` + sync `def` routes, truyền `connection=cursor` vào `repository.snapshot_session(connection=cursor)` (`0` read-skew & `0` ETag TOCTOU), RFC 9110 quoted `ETag: '"<snapshot_id>"'` + `If-None-Match -> 304` + `Cache-Control: "private, max-age=0, must-revalidate"`, mặc định bind `127.0.0.1`, deny-by-default CORS, phân trang `limit <= 200`, semaphore chống DoS trên `/stress-test`, và lọc lỗi an toàn không lộ đường dẫn hệ thống.
  2. **`GAP-08` (UI) + Năng lực V2 Chuẩn Thương mại (100% Zero-Math UI, Trung thực Nghiệp vụ theo `AGENT.md` §5.7 & §5.8 — đóng `HI-14`, `HI-15`, `MED-05`, `MISS-07`):**
     * **Trang 1 (`1_company_profile.py`):** Khối **Chất lượng Lợi nhuận Lõi (`Core Earnings` + `RF_08`/`RF_09`)** & Khối **Điểm Định lượng Kinh điển (`SAFE_ZONE`/`GREY_ZONE`/`DISTRESS_ZONE`, Piotroski $F$, Beneish $M$) + Hình mẫu K-Means + Xác suất Suy thoái hiệu chuẩn (`QUARTER`) + `Plotly Waterfall` Top 5+/5- TreeSHAP có cột `other_features_delta` cộng khít `raw_margin`**.
     * **Trang 2 (`2_industry_comparison.py`):** Thêm biểu đồ **Quỹ đạo Phân vị Ngành qua Thời gian (`[P25–P75]` + `Peer Median`)** có hiển thị `peer_group_level`, `icb_code_used`, `peer_count` (không trộn ngầm `ICB_LEVEL_3` với `REGIME_FALLBACK`!).
     * **Trang 3 (`3_audit_and_quality.py`):** Phân trang `quality_summary` / `audit_quarantine` và mở rộng `RATIO_LINEAGE_CATALOG` & `ALL_RATIOS` lên **38/38 tỷ số** (`CORE + EXTRA + FORENSIC`).
     * **Trang 4 MỚI (`4_forensic_screener.py`):** Đổi nhãn trung thực thành **"Tầm soát Mặt cắt Ngang Lịch sử — Latest Snapshot View (`Historical Cross-Sectional Screener`)"** với banner miễn trừ bắt buộc (`AGENT.md` §5.7: số liệu phản ánh bản chụp mới nhất gồm điều chỉnh kiểm toán sau kỳ, ICB hiện hành và điểm ML tập huấn luyện ở các quý quá khứ — KHÔNG phải mô phỏng backtest PIT!), dùng chung duy nhất `screen_market()`, xuất CSV chống formula injection.
     * **Trang 5 MỚI (`5_stress_testing.py`):** Giao diện giả lập cú sốc IMF WP/15/216 quy đổi rõ ràng thanh trượt `%`/`pp` sang **đơn vị thập phân** trước khi gọi `simulate_financial_stress()` / `POST /api/v1/stress-test` — chỉ cache lát cắt dữ liệu gốc theo `(snapshot_id, scope, period_type, period_end_date)`, KHÔNG cache theo giá trị slider liên tục!

---

## 1. QUYỀN SỞ HỮU FILE TUYỆT ĐỐI (`STRICT FILE OWNERSHIP`)

* **Owned Files (Chỉ được phép sửa/tạo các file sau — mọi đường dẫn đều repo-relative):**
  * `src/finintel/serving/__init__.py` *(mới)*
  * `src/finintel/serving/queries.py` *(mới — nơi định nghĩa chính thức `DashboardQueryService`, `RATIO_LINEAGE_CATALOG`, `ALL_RATIOS`)*
  * `src/finintel/serving/schemas.py` *(mới — Pydantic v2 DTOs cho lát cắt phân trang `limit <= 200`)*
  * `src/finintel/serving/dependencies.py` *(mới — DuckDB `:memory:` + `ATTACH READ_ONLY` lifespan, shutdown cleanup & sync `def get_db_cursor`)*
  * `src/finintel/serving/api.py` *(mới — 10 FastAPI v1 read-only routes + ETag/304 + hardening)*
  * `dashboard/queries.py` *(thin re-export shim từ `finintel.serving.queries` bảo toàn 100% import hiện hữu)*
  * `dashboard/client.py` *(mới — lớp giao tiếp thống nhất chọn `DashboardQueryService` cục bộ hoặc `httpx.Client` tới `FININTEL_API_URL` có kiểm tra URL/redirect/timeout)*
  * `dashboard/app.py`
  * `dashboard/components/charts.py`
  * `dashboard/components/widgets.py`
  * `dashboard/pages/1_company_profile.py`
  * `dashboard/pages/2_industry_comparison.py`
  * `dashboard/pages/3_audit_and_quality.py`
  * `dashboard/pages/4_forensic_screener.py` *(mới)*
  * `dashboard/pages/5_stress_testing.py` *(mới)*
  * `tests/unit/test_layer5_fastapi_serving.py` *(mới)*
  * `tests/integration/test_end_to_end_slice.py` *(được ủy quyền tường minh cập nhật duy nhất `L345`: `assert len(ALL_RATIOS) == 38` — đóng `BLK-14`!)*
* **Read-Only Contract Files:** `src/finintel/storage/repository.py`, `src/finintel/storage/schemas.py`, `src/finintel/analytics/stress_test.py`, `src/finintel/ml/classical_scores.py`.
* **Forbidden Files:** Toàn bộ `src/finintel/{domain,ingestion,normalization,storage,analytics,ml,quality,pipeline,transform,orchestration}/`, `config/`, `data/live_fhsc_universe/`, `AGENT.md`.

---

## 2. NGUỒN TÀI LIỆU CHÍNH THỨC BẮT BUỘC ĐỐI CHIẾU TRƯỚC KHI CODE

1. **FastAPI Official Docs (Lifespan & Sync Threadpool Concurrency):**
   * `https://fastapi.tiangolo.com/advanced/events/` · `https://fastapi.tiangolo.com/async/`
2. **DuckDB Python API & Multithreading Docs (`duckdb==1.4.2`):**
   * `https://duckdb.org/docs/stable/guides/python/multiple_threads`
   * `https://duckdb.org/docs/stable/clients/python/overview` *(xác nhận `:memory:` không dùng `read_only=True`; read-only đặt trên `ATTACH ... (READ_ONLY)`)*
3. **RFC 9110 (HTTP Conditional Requests — `ETag` & `If-None-Match` -> `304`):** `https://httpwg.org/specs/rfc9110.html#field.etag`
4. **Streamlit Caching Docs:** `https://docs.streamlit.io/develop/concepts/architecture/caching`
5. **Plotly Official Docs:** `https://plotly.com/python/waterfall-charts/` · `https://plotly.com/python/continuous-error-bars/`
6. **OWASP CSV Injection & Input Validation Cheat Sheets:** `https://owasp.org/www-community/attacks/CSV_Injection`

---

## 3. ĐẶC TẢ KỸ THUẬT CHI TIẾT

### 3.1. FastAPI Read-Only Analytical API (`src/finintel/serving/`)
* **4 Bất biến Kiến trúc & Đồng thời Bắt buộc (`BLK-15`, `HI-16`, `MED-08`):**
  1. **Zero Financial Math (`AGENT.md` §5.8) & Phân trang Pydantic (`MED-08`):** `serving/` chỉ đọc bảng đã xuất bản qua `DashboardQueryService` và gọi `simulate_financial_stress()` / `summarize_stress_by_sector()` từ `src/finintel/analytics/stress_test.py`. Mọi endpoint danh sách bắt buộc áp `limit <= 200`, chuyển dữ liệu bằng `df.to_dicts()` (0 vòng lặp `iter_rows()`).
  2. **Lifespan & Shutdown Clean (`BLK-15`):**
     - Trong `@asynccontextmanager async def lifespan(app: FastAPI)`: mở `duckdb_conn = duckdb.connect(":memory:")`, thiết lập mặc định tường minh **`SET threads = <min(4, os.cpu_count() or 2)>`**, **`SET memory_limit = '2GB'`** (cho phép ghi đè qua biến môi trường/cấu hình), và nếu `repository.backend == "ducklake"` thì thực thi `ATTACH 'ducklake:...' AS finintel_lake (READ_ONLY)`.
     - Trong nhánh shutdown (`after yield`): **BẮT BUỘC `duckdb_conn.close()`** trong khối `try ... finally`.
  3. **Sync `def` Cursor & Snapshot Pinning (`BLK-15`):**
     - `def get_db_cursor(request: Request) -> Iterator[duckdb.DuckDBPyConnection]` **PHẢI là hàm đồng bộ `def`** (không dùng `async def`!), mở `cursor = request.app.state.duckdb_conn.cursor()`, `yield cursor`, `finally: cursor.close()`.
     - Mọi route đọc DuckDB **PHẢI khai báo bằng `def` thường** và truyền `connection=cursor` vào **`repository.snapshot_session(connection=cursor)`** xuyên suốt toàn bộ truy vấn của request $\implies$ 0 read-skew giữa các bảng, 0 `ETag` TOCTOU!
  4. **RFC 9110 `ETag`, `304 Not Modified` & Cache-Control (`BLK-15`):**
     - Gắn header **`ETag: '"<snapshot_id>"'`** (có dấu ngoặc kép đúng chuẩn RFC 9110, lấy từ cùng `pinned` session của request!) và **`Cache-Control: "private, max-age=0, must-revalidate"`** cho tất cả GET analytical routes.
     - Kiểm tra `If-None-Match` (chấp nhận cả strong `"<id>"` lẫn weak `W/"<id>"` sau khi tách danh sách dấu phẩy): nếu khớp `active_snapshot_id` hiện hành $\rightarrow$ trả **`304 Not Modified`** kèm `ETag` và thân rỗng.
* **Bảo mật & Chống DoS Bắt buộc (`HI-16`, `MED-06`):**
  * Mặc định khởi chạy `uvicorn` bind **`127.0.0.1`**; **deny-by-default CORS** (chỉ bật middleware CORS cho các origin cụ thể khi có biến `FININTEL_CORS_ORIGINS`).
  * Kiểm tra chặt bằng Pydantic/FastAPI regex & `Literal`: `ticker` (`^[A-Z0-9]{1,10}$`), `statement_scope` (`CONSOLIDATED|SEPARATE`), `period_type` (`QUARTER|ANNUAL`), `period_view` (`TTM|Quarterly|Annual`), `altman_zone` (`SAFE_ZONE|GREY_ZONE|DISTRESS_ZONE`), `beneish_flag` (enum chính xác từ `classical_scores.py`), `limit` (`1..200`), `offset` (`>= 0`).
  * Bọc `POST /api/v1/stress-test` bằng **`threading.Semaphore(4)`** (giới hạn số phép mô phỏng chạy song song, trả `429`/`503` hoặc chờ ngắn có timeout nếu quá tải).
  * Đăng ký global exception handler chuẩn hóa: `ValueError`/`ContractViolationError` $\rightarrow$ `404`/`422` với thông điệp nghiệp vụ ngắn gọn; ngoại lệ không lường trước $\rightarrow$ `500 {"detail": "Internal serving error"}` (TUYỆT ĐỐI KHÔNG rò rỉ stack trace hay đường dẫn hệ thống!).
* **Danh mục Chính xác 10 HTTP Routes trên 9 Nhóm Endpoint (`HI-18`):**
  1. `GET /api/v1/health` $\rightarrow$ `status`, `active_snapshot_id`, `storage_backend`.
  2. `GET /api/v1/snapshot/overview`
  3. `GET /api/v1/issuers?limit=200&offset=0`
  4. `GET /api/v1/companies/{ticker}/profile?statement_scope=CONSOLIDATED&period_view=TTM` (trả đầy đủ growth, capital structure, DuPont, red flags `RF_01..RF_09`, core earnings metrics, và `ml_scores` + `shap_top_drivers` cho kỳ `QUARTER` khi tồn tại).
  5. `GET /api/v1/companies/{ticker}/benchmarks?metric_name=roe_total&statement_scope=CONSOLIDATED&period_type=QUARTER&period_end_date=...&include_history=true` (trả mặt cắt ngang kỳ chọn + chuỗi thời gian `[P25, Median, P75]` kèm `peer_group_level`, `icb_code_used`, `peer_count`).
  6. `GET /api/v1/companies/{ticker}/lineage?ratio_name=roe_total&statement_scope=CONSOLIDATED&period_type=QUARTER&period_end_date=...` (hỗ trợ đủ **38/38 tỷ số**).
  7. `GET /api/v1/screener?period_type=QUARTER&period_end_date=...&statement_scope=CONSOLIDATED&icb_code_level2=...&min_f_score=...&altman_zone=...&beneish_flag=...&max_distress_prob=...&max_red_flags=...&limit=50&offset=0` *(gọi trực tiếp `DashboardQueryService.screen_market(...)` — `MED-05`!)*.
  8. `POST /api/v1/stress-test` *(nhận `StressScenarioRequest` với **đơn vị thập phân chuẩn**: `revenue_shock_pct` trong `[-0.80, +0.50]`, `gross_margin_shock_pp` trong `[-0.50, +0.20]`, `interest_rate_shock_bps` trong `[-500, +1500]`, `dso_shock_days` & `dio_shock_days` trong `[-90, +180]`, `top_n: int = Field(50, ge=1, le=200)` $\rightarrow$ nạp lát cắt kỳ chọn bằng `get_stress_test_snapshot_inputs()` $\rightarrow$ gọi `simulate_financial_stress` & `summarize_stress_by_sector`)*.
  9. `GET /api/v1/quality/summary?limit=50&offset=0` *(Route 9)*
  10. `GET /api/v1/quality/quarantine?limit=50&offset=0` *(Route 10)*

### 3.2. `src/finintel/serving/queries.py` & `dashboard/queries.py` (`BLK-04`, `BLK-10`, `BLK-14`, `HI-15`, `HI-16`, `MED-05`)
* Mọi phương thức trong `DashboardQueryService` chấp nhận `connection: duckdb.DuckDBPyConnection | None = None` và truyền vào `self.repository.snapshot_session(connection=connection)` (khi gọi cục bộ từ Streamlit đa luồng không có `connection`, `ParquetRepository` mở cursor/session cô lập an toàn theo từng lời gọi!).
* **100% Predicate Pushdown & Tối ưu Bắt buộc (`HI-16`):**
  * `get_snapshot_overview()`: dùng `pinned.get_snapshot_metadata()` (xóa hoàn toàn việc đọc trực tiếp `_snapshot_meta.json` từ đĩa — `BLK-10`!) và dùng DuckDB SQL `COUNT(*)` / `COUNT(*) FILTER (...)` thay vì tải cả 4 bảng Parquet vào RAM!
  * `list_ratio_periods()` và `get_ratio_lineage()`: ánh xạ `ticker -> issuer_id` trước rồi đẩy lọc `filters={"issuer_id": issuer_id, ...}` và `columns=[...]` xuống `read_table(...)`.
  * `get_quality_tables(limit: int = 200, offset: int = 0)`: hỗ trợ phân trang `LIMIT/OFFSET` + trả tổng số dòng (`total_summary`, `total_quarantine`) cho cả Page 3 lẫn Routes 9–10.
* **Mở rộng 38/38 tỷ số:** Thêm `FORENSIC_RATIOS` vào `RATIO_LINEAGE_CATALOG` và đặt `ALL_RATIOS = (*CORE_RATIOS, *EXTRA_RATIOS, *FORENSIC_RATIOS)` (đồng bộ cập nhật `tests/integration/test_end_to_end_slice.py:345` `assert len(ALL_RATIOS) == 38`).
* **Các phương thức đọc mới (0% toán tài chính!):**
  * `get_company_ml_scores(ticker, *, statement_scope="CONSOLIDATED", period_type="QUARTER", connection=None) -> pl.DataFrame`: ánh xạ `ticker -> issuer_id`, lọc `period_type`, trả DataFrame rỗng đúng `ML_SCORES_SCHEMA` nếu snapshot chưa bật ML (`HI-15`, `MISS-07`).
  * `get_peer_band_history(ticker, statement_scope, period_type, metric_name, *, connection=None) -> pl.DataFrame`: trả `(period_end_date, value, peer_p25, peer_median, peer_p75, percentile_rank, peer_group_level, icb_code_used, peer_count)` (`MED-05`).
  * `screen_market(...) -> tuple[pl.DataFrame, int]`: hàm duy nhất dùng chung cho cả FastAPI `/screener` và Page 4 — ghép `issuers` + `financial_ratios` (tại lát cắt chọn) + đếm `accounting_red_flags` + **`LEFT JOIN` `ml_scores_and_explanations`** (hoạt động mượt ngay cả khi snapshot không có bảng ML!), hỗ trợ lọc `icb_code_level2`, `min_f_score`, `altman_zone` (`SAFE_ZONE`/`GREY_ZONE`/`DISTRESS_ZONE`), `beneish_flag`, `max_distress_prob`, `max_red_flags`, kèm `limit`/`offset` và `total_count`.
  * `get_stress_test_snapshot_inputs(statement_scope, period_type, period_end_date, *, connection=None) -> pl.DataFrame`: chỉ đọc đúng các cột đầu vào cần cho `simulate_financial_stress()` + ghép `icb_code_level2`, `icb_name_level2` từ `issuers`.

### 3.3. Nâng cấp Streamlit Dashboard 5 Trang & Bảo mật UI (`dashboard/`)
1. **Client & Caching Chuẩn (`dashboard/client.py`, `HI-16`, `MED-06`):**
   * Khi có biến môi trường `FININTEL_API_URL`: kiểm tra scheme thuộc `{"http", "https"}` + host không rỗng, cấu hình `httpx.Client(follow_redirects=False, timeout=15.0)`, bọc trong `@st.cache_resource`. Nếu không có biến môi trường: dùng `DashboardQueryService` cục bộ.
   * Mọi hàm `@st.cache_data` lấy **`snapshot_id: str`** làm tham số đầu tiên (`ttl=None`, tự vô hiệu hóa 100% khi có snapshot mới).
   * **CẤM cache giá trị slider liên tục ở Page 5:** `@st.cache_data(max_entries=32)` chỉ cache `get_stress_test_snapshot_inputs(snapshot_id, statement_scope, period_type, period_end_date)` — còn phép tính `simulate_financial_stress()` chạy trực tiếp (`< 10ms` trên ~383 dòng bằng Polars)!
   * **Bảo mật UI (`MED-06`, `HI-16`):**
     - **TUYỆT ĐỐI CẤM `unsafe_allow_html=True`** trên toàn bộ thư mục `dashboard/`.
     - Thay thế toàn bộ `st.error(exc)` thô bằng thông báo lỗi đã làm sạch (không hiển thị đường dẫn đĩa hay traceback).
     - Trên nút xuất CSV của Page 4: trung hòa công thức Excel (`CSV Injection Guard` — chèn tiền tố `'` vào mọi ô chuỗi bắt đầu bằng `=`, `+`, `-`, `@`, `\t`, `\r`).
2. **4 Biểu đồ Plotly Thuần túy trong `dashboard/components/charts.py` (`HI-14`, `MED-05`):**
   * `build_shap_waterfall_chart(shap_drivers_json: str) -> go.Figure`: dựng `go.Waterfall` với `base_value`, Top 5 nhân tố tăng rủi ro, Top 5 nhân tố giảm rủi ro, **cột `other_features_delta` ("Các nhân tố khác")** (khớp khít `prediction_score`!), hiển thị `display_name_vn` cùng `value` vs `peer_median` trên tooltip.
   * `build_peer_band_history_chart(history_df: pl.DataFrame, metric_label: str) -> go.Figure`: dải băng `[P25–P75]` + `Peer Median` + đường doanh nghiệp, hiển thị rõ `peer_group_level` (`ICB_LEVEL_3`/`ICB_LEVEL_2`/`REGIME_FALLBACK`) & `peer_count` trên hover và cảnh báo trực quan khi có kỳ rơi về `REGIME_FALLBACK`.
   * `build_screener_risk_scatter(df: pl.DataFrame) -> go.Figure`: biểu đồ phân tán rủi ro theo `Altman Zone` (`SAFE_ZONE`/`GREY_ZONE`/`DISTRESS_ZONE`).
   * `build_stress_sector_bar_chart(summary_df: pl.DataFrame) -> go.Figure`: cột ghép `% Debt-at-Risk Trước Sốc` vs `Sau Sốc` theo `icb_name_level2`.
3. **Cập nhật 5 Trang (`dashboard/pages/`):**
   * **Trang 1 (`1_company_profile.py`):** Khối `Core Earnings` + `RF_08`/`RF_09` và khối `ML_SCORES` (badge đúng enum `SAFE_ZONE`/`GREY_ZONE`/`DISTRESS_ZONE`, `Piotroski F (/9)`, `Beneish M`, `K-Means Archetype`, `Calibrated Distress Prob (QUARTER)`, nhãn `model_role` & `model_version`, `TreeSHAP Waterfall`).
   * **Trang 2 (`2_industry_comparison.py`):** Giữ nguyên `Box Plot` + thêm khối Quỹ đạo Phân vị Ngành qua Thời gian (`build_peer_band_history_chart`).
   * **Trang 3 (`3_audit_and_quality.py`):** Bổ sung 3 `FORENSIC_RATIOS` (38/38 Lineage Cards) + điều khiển phân trang cho bảng `quality_summary` và `audit_quarantine`.
   * **Trang 4 (`4_forensic_screener.py` — Trung thực Nghiệp vụ `HI-15`):**
     - Tiêu đề: **"Tầm soát Mặt cắt Ngang Lịch sử (Latest Snapshot View)"**.
     - **Banner cảnh báo cố định (`st.info` / `st.warning`):** Ghi rõ dữ liệu truy vấn trên **bản chụp xuất bản mới nhất** (có thể bao gồm báo cáo kiểm toán điều chỉnh sau kỳ, phân loại ngành ICB hiện hành và điểm ML thuộc giai đoạn huấn luyện ở các quý lịch sử — không sử dụng thay thế cho backtest Point-in-Time!).
     - Bộ lọc `period_end_date`, `icb_code_level2`, `Piotroski F`, `altman_zone`, `beneish_flag`, `max_red_flags`, `max_distress_prob` + `Market Risk Scatter` + bảng kết quả phân trang + xuất CSV đã chống formula injection.
   * **Trang 5 (`5_stress_testing.py`):** 5 thanh trượt tham số (hiển thị `%`/`pp` thân thiện và chia `/ 100.0` sang thập phân chuẩn cho `StressScenario`) + 3 nút kịch bản mẫu dựng sẵn + KPI `Debt-at-Risk` toàn sàn + biểu đồ ngành + danh sách mã mới rơi xuống `Stressed ICR < 1.0`.

---

## 4. KIỂM THỬ TDD & TIÊU CHÍ NGHIỆM THU (`DEFINITION OF DONE`)

1. **`tests/unit/test_layer5_fastapi_serving.py`:**
   * **Đủ 10 HTTP Routes:** Test tất cả 10 routes (`200 OK`, `404` ticker lạ, `422` sai enum/vượt `limit > 200`/vượt biên `StressScenario`, `ETag: '"<snapshot_id>"'`, **`If-None-Match` $\rightarrow$ `304 Not Modified`**, `Cache-Control: "private, max-age=0, must-revalidate"`).
   * **Đa luồng Thực tế (`20 Threads × 200 Concurrent Requests`):** Bắn song song vào `/profile`, `/benchmarks`, `/lineage`, `/screener`, `/stress-test` trên cùng 1 app instance $\rightarrow$ `200/200` thành công, `0` `ResourceInUseException`, `0` read-skew, kiểm chứng `duckdb_conn.close()` được gọi khi shutdown `TestClient`.
   * **Pushdown & Overview Efficiency Spy:** Xác nhận `get_company_profile`, `get_industry_comparison`, `get_ratio_lineage`, `list_ratio_periods` luôn truyền `columns` và `filters` có `issuer_id`; `get_snapshot_overview` dùng `get_snapshot_metadata()` + `COUNT(*)`.
   * **Security & Resilience Checks:** Kiểm chứng deny-by-default CORS, error sanitizer không lộ đường dẫn tệp, CSV formula neutralization (`=CMD|...` $\rightarrow$ `'=CMD|...`), `FININTEL_API_URL` chặn scheme `file://`, snapshot không bật ML vẫn trả `200 OK` trên `/profile` & `/screener` (`LEFT JOIN`), và không có chuỗi `unsafe_allow_html=True` trong `dashboard/`.
   * **Pure Plotly Figures & 38/38 Lineage Cards:** Xác nhận cả 4 chart builders chạy sạch trên dữ liệu đầy đủ lẫn rỗng (Waterfall có `other_features_delta`), và đủ **38/38** `ALL_RATIOS` trả về `RatioLineageCard` với `orphan_filing_ids == ()`.
2. **Cổng chất lượng bắt buộc:** `ruff format --check src scripts tests dashboard`, `ruff check src scripts tests dashboard`, `mypy --strict`, `pytest tests/integration/test_end_to_end_slice.py -v` (đã cập nhật `L345 == 38`), `pytest -q` xanh 100%.

---

## 5. PROMPT GIAO VIỆC MẪU CHO CODING AGENT (COPY-PASTE READY)

```text
Đọc AGENT.md, docs/v2_specs/00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md và docs/v2_specs/08_STREAM_B3_LAYER5_FASTAPI_DASHBOARD_ML_SCREENER_STRESS_UI.md.

Thực hiện ĐÚNG phạm vi Wave 4 (Stream B3) trên git worktree riêng v2/b3-serving (tách từ v2/integration sau khi Wave 1–3 đã merge):
1. Chỉ sửa/tạo các file trong mục "Owned Files" của 08_STREAM_B3 (chuyển DashboardQueryService về src/finintel/serving/queries.py, giữ dashboard/queries.py là thin re-export, tạo dashboard/client.py, và chỉ cập nhật L345 == 38 trong test_end_to_end_slice.py). TUYỆT ĐỐI KHÔNG viết công thức tài chính hay công thức stress test trong FastAPI/Streamlit.
2. Đối chiếu FastAPI Lifespan & Sync Concurrency, DuckDB Multithreading & :memory: + ATTACH READ_ONLY, RFC 9110 ETag/304, Streamlit Caching, Plotly Waterfall và OWASP CSV Injection; ghi Verified Sources trong docstring.
3. Bảo đảm: (a) sync def get_db_cursor + sync def routes + snapshot_session(connection=cursor) + đóng duckdb_conn khi shutdown; (b) RFC 9110 quoted ETag + 304 + private must-revalidate; (c) bảo mật 127.0.0.1, deny-by-default CORS, limit<=200, semaphore /stress-test, sanitized errors, CẤM unsafe_allow_html=True, CSV formula guard; (d) đổi nhãn Page 4 trung thực "Historical Cross-Sectional Screener (Latest Snapshot View)" kèm banner miễn trừ, dùng đúng enum SAFE_ZONE/GREY_ZONE/DISTRESS_ZONE & icb_code_level2, chỉ cache lát cắt gốc ở Page 5.
4. Viết đầy đủ test trong test_layer5_fastapi_serving.py (đủ 10 routes + 20 luồng × 200 requests + pushdown spy + security + 38/38 Lineage Cards) và chạy xanh 100% ruff, mypy --strict, test_end_to_end_slice.py, pytest -q.
```
