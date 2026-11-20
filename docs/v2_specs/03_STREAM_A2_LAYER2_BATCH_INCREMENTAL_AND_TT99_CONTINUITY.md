# ĐẶC TẢ TRIỂN KHAI WAVE 1B — STREAM A2: PHẪU THUẬT TẦNG 2 (`NORMALIZE`) VECTORIZED BATCH + INCREMENTAL, LIÊN THÔNG `TT200 ↔ TT99` & `BS_CIP_LONG_TERM` (`242 → 252`)

* **Mã đặc tả:** `SPEC-V2-03-STREAM-A2-v1.1`
* **Wave:** `Wave 1B` (Chạy **song song** với Wave 1A `02_STREAM_A1` và Wave 1C `04_STREAM_B1` trên `git worktree` riêng — 0 file trùng lặp!)
* **Nhánh Git / Worktree:** `v2/a2-layer2` (`../finintel-wt-a2-layer2`)
* **Mục tiêu kép:**
  1. **Hiệu năng Quy mô (Option B Chặng B1):** Đưa Full Normalize 37 mã (~5,547 filings) từ **`64.687s` xuống `< 3.5s`** (500 mã `< 35s`, 1,600 mã `< 120s`), Incremental Normalize 0–10 mã đổi **`< 1.5s`**, RAM đỉnh `< 500 MB`, bảo toàn 100% tương thích ngược `normalize_archive()` (`MED-02`) và nối sạch qua `NormalizeHandler` (`BLK-05`).
  2. **Liên thông Kế toán `TT200 ↔ TT99` (`GAP-02`, `BLK-08`, `BLK-09`, `MISS-01`):**
     * Cập nhật tường minh **27** chỉ tiêu tương đương (24 chỉ tiêu không đổi phạm vi + 3 chỉ tiêu B02-DN kèm ghi chú TT99 Mã `21`).
     * Sửa triệt để cầu nối TTM `IS_INTEREST_EXPENSE` (TT200 Mã `23`) $\leftrightarrow$ `IS_BORROWING_COSTS` (TT99 Mã `24`): sắp xếp & anti-join hồi tố theo khóa cầu nối, phát ra **`ttm_exact_filing: pl.Boolean`** ngay từ `compute_ttm()` để chấm dứt lỗi xóa sổ 100% điểm ML năm 2026 (`BLK-09`).
     * Khai báo **`BS_CIP_LONG_TERM`** (đồng bộ tên chuẩn `docs/layers/LAYER_4_ANALYTICS_AND_ML.md:119`) với mã chính xác theo luật: **TT200 `242` $\rightarrow$ TT99 `252`** (**CẢNH BÁO PHÁP LÝ `MISS-01`:** Mã `242` theo Phụ lục IV TT99 là *Giá trị hao mòn lũy kế BĐS đầu tư* — tuyệt đối KHÔNG map `tt99_code: "242"` cho XDCB dở dang!).

---

## 1. QUYỀN SỞ HỮU FILE TUYỆT ĐỐI (`STRICT FILE OWNERSHIP`)

* **Owned Files (Chỉ được phép sửa/tạo các file sau — mọi đường dẫn đều repo-relative):**
  * `config/metric_definitions.yaml`
  * `config/taxonomy/corporate_tt200_2014.yaml`
  * `config/taxonomy/corporate_tt99_2025.yaml`
  * `src/finintel/normalization/mapper.py`
  * `src/finintel/normalization/archive.py`
  * `src/finintel/normalization/periods.py`
  * `src/finintel/pipeline/normalize.py` *(bắt buộc nối `normalize_archive_incremental` và giữ checkpoint `_normalize_meta.json` — `BLK-05`)*
  * `tests/unit/test_layer2_normalization.py` *(chỉ cập nhật chỉ tiêu test `MIXED_TAXONOMY` ở `L560-589` sang chỉ tiêu giữ `comparable_across_taxonomy: false` như `IS_GROSS_REVENUE` `"01"`)*
  * `tests/unit/test_layer2_batch_incremental.py` *(mới — bao gồm benchmark tổng hợp 37 mã chạy được hoàn toàn offline trong worktree)*
  * `tests/reconciliation/test_golden_ratios.py` *(CHỈ được chuyển Case 3 `EDGE2` `L311-361` sang tỷ số/chỉ tiêu giữ `comparable_across_taxonomy: false` nếu `BS_INVENTORIES` + `IS_COST_OF_GOODS_SOLD` chuyển sang `true`; **TUYỆT ĐỐI CẤM chạm 1 ký tự vào các kiểm định số liệu FPT 2024–2025!**)*
* **Read-Only Contract Files:** `docs/layers/LAYER_2_NORMALIZATION.md`, `docs/layers/LAYER_4_ANALYTICS_AND_ML.md` (`L119`), `src/finintel/ingestion/hasher.py`, `tests/fixtures/raw_samples/fhsc_cip_sample.json` *(commit sẵn ở Pre-Wave 0)*.
* **Forbidden Files:** Toàn bộ `src/finintel/{domain,ingestion,storage,analytics,ml,quality}/`, `dashboard/`, `data/live_fhsc_universe/`, `AGENT.md`.

---

## 2. NGUỒN KHOA HỌC, PHÁP LÝ & KỸ THUẬT BẮT BUỘC ĐỐI CHIẾU TRƯỚC KHI CODE

1. **Boncz, P. A., Zukowski, M., & Nes, N. (2005).** *"MonetDB/X100: Hyper-Pipelining Query Execution."* **CIDR 2005** — xử lý lô vector hóa liên tục thay vòng lặp từng filing.
2. **Mokhov, A., Mitchell, N., & Peyton Jones, S. (2018 / 2020).** *"Build Systems à la Carte."* **ICFP 2018** / *J. Functional Programming* (2020) — Content-Addressed Incremental Build & Early Cutoff.
3. **Polars Official Guide:**
   * `https://docs.pola.rs/user-guide/lazy/`
   * `https://docs.pola.rs/user-guide/concepts/streaming/`
4. **Thông tư 200/2014/TT-BTC (Phụ lục 2) & Thông tư 99/2025/TT-BTC (ban hành 27/10/2025, Phụ lục IV Mẫu B01-DN & B02-DN):**
   * `https://congbao.chinhphu.vn/van-ban/thong-tu-so-200-2014-tt-btc-6697/3529.htm`
   * `https://congbao.chinhphu.vn/tai-ve-van-ban-so-99-2025-tt-btc-46529-59652?format=pdf`
   * *Chứng cứ pháp lý bắt buộc (`MISS-01`, `BLK-08`):*
     - **B01-DN TT99:** `240` = Bất động sản đầu tư (`241` Nguyên giá, **`242` Giá trị hao mòn lũy kế BĐSĐT**); `250` = Tài sản dở dang dài hạn (`251` Chi phí SXKD dở dang dài hạn, **`252` Chi phí xây dựng cơ bản dở dang**); `260` = Đầu tư tài chính dài hạn.
     - **B02-DN TT99:** Bổ sung Mã `21` (Lãi/lỗ bán, thanh lý BĐSĐT — tách ra khỏi doanh thu `01`/giá vốn `11` của TT200 nhưng vẫn tổng hợp vào Lợi nhuận thuần từ HĐKD Mã `30`).

---

## 3. ĐẶC TẢ KỸ THUẬT CHI TIẾT

### 3.1. Phần 1 — Vectorized Batch Mapping & Content-Addressed Incremental Normalization

1. **`src/finintel/normalization/mapper.py` (`HI-05`):**
   * Giữ nguyên 100% chữ ký & hành vi của `TaxonomyMapper.map_metrics()` và `_map()`.
   * Lưu sẵn bảng phẳng `self._aliases_all: pl.DataFrame` (1 chunk duy nhất qua `.rechunk()`).
   * Khai báo tường minh `FilingMappingInput` (`@dataclass(frozen=True, slots=True)`) hỗ trợ đầy đủ cả bản ghi thường lẫn hồi tố so sánh (`map_restatement`, nơi `period != filing` và ép `taxonomy = TaxonomyVersion.TT99`):
     ```python
     @dataclass(frozen=True, slots=True)
     class FilingMappingInput:
         filing_id: str
         source_provider: str
         revision_no: int
         unit_multiplier: int | float
         cash_flow_method: str | None
         period: FilingMetadata
         filing: FilingMetadata
         taxonomy: TaxonomyVersion
         metrics: Mapping[str, object]
     ```
   * Trong `TaxonomyMapper.map_batch(entries: Sequence[FilingMappingInput]) -> tuple[pl.DataFrame, pl.DataFrame]`:
     * Giữ nguyên 100% kiểm tra hợp đồng đầu vào của `_map()` và `_context()` (`ContractViolationError` khi `unit_multiplier <= 0`, giá trị không hữu hạn `NaN`/`Inf`, sai `cash_flow_method`, trùng `(filing_idx, metric_id)`).
     * Chuẩn hóa `cash_flow_method = (entry.cash_flow_method or "")` khớp chính xác wildcard `""` trong `ALIAS_SCHEMA` (không bao giờ join trên `NULL`!).
     * Dùng List-Accumulation gom `ctx_df` (`filing_idx: pl.UInt32`) & `raw_df`, thực hiện **1 phép join duy nhất** với `maintain_order="left"` vào `self._aliases_all`, ưu tiên `fhsc_fallback` theo `.over(["filing_idx", "metric_id"])`, kiểm tra trùng khóa `(filing_idx, metric_id)`, áp quy ước dấu & khử `-0.0`, và trả `(normalized_df.rechunk(), unmapped_df.rechunk())`.

2. **`src/finintel/normalization/archive.py` & `src/finintel/pipeline/normalize.py` (`BLK-05`, `HI-04`, `MED-02`):**
   * **Cache JSON theo `(raw_root, relative_path, payload_file_sha256)` (`HI-04`):** Scope rõ theo `raw_root` (không dùng cache toàn cục chỉ theo đường dẫn tương đối!) với `maxsize=32`.
   * **Bảo toàn chữ ký & hành vi `normalize_archive()` (`MED-02`):** Tăng tốc `normalize_archive()` bằng sắp xếp manifest theo `(issuer_id, raw_archive_path, statement_type, period_end_date, revision_no, filing_id)` và gọi `mapper.map_batch(...)` theo lô `batch_issuers: int = 50` (nhóm trọn bộ từng `issuer_id`, không cắt đôi 1 `issuer_id` sang 2 lô!) — **vẫn trả về `(facts, unmapped)` thô để không phá vỡ bất kỳ caller/unit test hiện hữu nào!**
   * **Bổ sung `normalize_archive_incremental(...)` cho `NormalizeHandler` (`BLK-05`, `HI-04`):**
     * Hàm `compute_taxonomy_sha256(config_dir: Path, filing_settings: Mapping[str, object]) -> str` băm toàn bộ YAML cấu hình (`metric_definitions.yaml`, `corporate_tt200_2014.yaml`, `corporate_tt99_2025.yaml`) VÀ canonical JSON của `filing_settings`.
     * Cấu trúc `_normalize_meta.json` (do `NormalizeHandler` ghi duy nhất — chấm dứt ghi đè ở `normalize.py:100-108`!):
       ```json
       {
         "source": "fhsc",
         "full_history": true,
         "taxonomy_sha256": "<sha256>",
         "recomputed_issuers": 1,
         "issuer_filings": {
           "<issuer_id>": {
             "<filing_id>": "<signature_sha256_of_content_payload_dates_scope_audit_late_revision>"
           }
         }
       }
       ```
       *(Chữ ký `signature_sha256` bao gồm đầy đủ: `canonical_content_sha256`, `payload_file_sha256`, `published_at`, `published_at_basis`, `effective_available_at`, `is_filed_late`, `statement_scope`, `audit_status`, `audit_opinion`, `revision_reason`, `fiscal_year_start_date`, `period_start_date`, `period_end_date`.)*
     * So sánh theo `issuer_id`: `dirty_issuers = {iss for iss in (prev_issuers | cur_issuers) if prev.get(iss) != cur.get(iss)}` (phát hiện được cả thêm mới, sửa đổi lẫn xóa/hủy filing!).
     * **Nếu `dirty_issuers` rỗng (Early Cutoff):** `recomputed_issuers = 0`; **BẮT BUỘC** tạo liên kết cứng (`os.link`) hoặc sao chép tệp `financial_facts.parquet` và `unmapped_metrics.parquet` vào `curated/financial_facts/<run_id>/` cùng `_normalize_meta.json` mới để `pipeline/analytics.py:249-256` và `QualityGate1Handler` luôn đọc được `<run_id>/financial_facts.parquet`!
     * **Nếu `dirty_issuers` khác rỗng:** Chỉ xử lý lô 50 issuer cho `dirty_issuers` (gọi `map_batch` $\rightarrow$ `build_normalized_batch` trên từng lô 1-chunk để chặn đỉnh RAM `< 500 MB`), sau đó dùng Polars Lazy streaming ghép **cả** `financial_facts.parquet` **lẫn** `unmapped_metrics.parquet` bằng `pl.concat([pl.scan_parquet(prev).filter(~pl.col("issuer_id").is_in(dirty_issuers)), new_df.lazy()]).sink_parquet(...)`.
     * Nếu `taxonomy_sha256` đổi hoặc thiếu batch `full_history` trước đó (hoặc chạy standalone có filter `tickers`/`period`) $\rightarrow$ tự động chạy Full Batch Build.

---

### 3.2. Phần 2 — Liên thông `TT200 ↔ TT99` Năm 2026 (`BLK-08`, `BLK-09`) & `BS_CIP_LONG_TERM` (`MISS-01`)

1. **Rà soát minh bạch **27** chỉ tiêu `comparable_across_taxonomy: true` tại `config/metric_definitions.yaml` (`BLK-08`):**
   * **Nhóm A — 24 chỉ tiêu đồng nhất bản chất & phạm vi giữa TT200 và TT99:**
     - B01-DN (12): `BS_CURRENT_ASSETS` (`100`), `BS_SHORT_TERM_INVESTMENTS` (`120`), `BS_SHORT_TERM_RECEIVABLES` (`130`), `BS_INVENTORIES` (`140`), `BS_NONCURRENT_ASSETS` (`200`), `BS_FIXED_ASSETS` (`220`), `BS_TANGIBLE_FIXED_ASSETS` (`221`), `BS_INTANGIBLE_FIXED_ASSETS` (`227`), `BS_CURRENT_LIABILITIES` (`310`), `BS_SHORT_TERM_BORROWINGS` (`320 -> 321`), `BS_NONCURRENT_LIABILITIES` (`330`), `BS_LONG_TERM_BORROWINGS` (`338 -> 339`).
     - B02-DN (9): `IS_SELLING_EXPENSES` (`25`), `IS_ADMINISTRATIVE_EXPENSES` (`26`), `IS_CURRENT_INCOME_TAX` (`51`), `IS_DEFERRED_INCOME_TAX` (`52`), `IS_FINANCIAL_REVENUE` (`21 -> 22`), `IS_FINANCIAL_EXPENSES` (`22 -> 23`), `IS_OTHER_INCOME` (`31`), `IS_OTHER_EXPENSES` (`32`), `IS_OTHER_PROFIT` (`40`).
     - B03-DN (3): `CF_CAPITAL_EXPENDITURE` (`21`), `CF_INVESTING_CASH_FLOW` (`30`), `CF_FINANCING_CASH_FLOW` (`40`).
   * **Nhóm B — 3 chỉ tiêu B02-DN kèm ghi chú chính sách TT99 Mã `21` (nhất quán với `IS_NET_REVENUE = true` hiện có):**
     - `IS_OPERATING_PROFIT` (`30` — tổng hợp cả lãi/lỗ thanh lý BĐSĐT Mã `21` nên đồng nhất đại số với TT200), `IS_COST_OF_GOODS_SOLD` (`11`), `IS_GROSS_PROFIT` (`20`). Ghi chú tường minh bằng comment YAML: chỉ chịu ảnh hưởng nếu doanh nghiệp có phát sinh bán BĐSĐT trên Mã `21` TT99 (trường hợp đó `IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS` sẽ được đưa vào bằng chứng `RF_08` ở Wave 2A).
   * **Giữ NGUYÊN `comparable_across_taxonomy: false`:** `IS_GROSS_REVENUE` (`01`), `IS_REVENUE_DEDUCTIONS` (`02`), `IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS` (TT99 Mã `21`), `BS_OTHER_NONCURRENT_ASSETS` (`260 -> 270`), `IS_EPS_BASIC_REPORTED` (`70`), `BS_TREASURY_SHARES` (`415`), `IS_INTEREST_EXPENSE` (`23`), `IS_BORROWING_COSTS` (`24` — đi qua cầu nối có kiểm soát bên dưới!).
   * **Cập nhật 2 bài test `MIXED_TAXONOMY` được ủy quyền (`BLK-08`):**
     - `tests/unit/test_layer2_normalization.py:560-589`: đổi mã test từ `"30"` sang `"01"` (`IS_GROSS_REVENUE`, `comparable_across_taxonomy: false`) để tiếp tục kiểm chứng chặt cơ chế chặn `MIXED_TAXONOMY` & hồi tố TT99!
     - `tests/reconciliation/test_golden_ratios.py:311-361`: đổi Case 3 (`EDGE2`) sang kiểm chứng `MIXED_TAXONOMY` trên 1 tỷ số phụ thuộc chỉ tiêu `comparable_across_taxonomy: false` (hoặc tạo bản sao config test với `IS_COST_OF_GOODS_SOLD: false` riêng cho Case 3) — **KHÔNG chạm bất kỳ dòng nào của bài kiểm định FPT 2024–2025 (`L1-L299`)!**

2. **Cầu nối TTM Đúng Đắn cho `IS_INTEREST_EXPENSE` $\leftrightarrow$ `IS_BORROWING_COSTS` tại `src/finintel/normalization/periods.py` (`BLK-09`):**
   * Định nghĩa cầu nối:
     `CROSS_TAXONOMY_TTM_BRIDGES: dict[str, str] = {"IS_INTEREST_EXPENSE": "IS_BORROWING_COSTS_BRIDGE", "IS_BORROWING_COSTS": "IS_BORROWING_COSTS_BRIDGE"}`
     và khóa nhóm cầu nối `BRIDGED_SERIES = ["issuer_id", "statement_scope", "source_provider", "_ttm_series_key"]`, `BRIDGED_ORDER = [*BRIDGED_SERIES, "period_end_date"]`, `BRIDGED_QUARTER_KEY = [*BRIDGED_SERIES, "fiscal_year_start_date", "fiscal_quarter"]`.
   * **Sửa 4 lỗi chí mạng đã phát hiện trong `BLK-09`:**
     1. **Sắp xếp theo `BRIDGED_ORDER`** (dùng `_ttm_series_key` thay vì `metric_id` thô!) trong `_ttm_windows()` để `2025-Q2..Q4` (`IS_INTEREST_EXPENSE`) luôn đứng liền trước `2026-Q1..Q3` (`IS_BORROWING_COSTS`), không bị sắp ngược theo thứ tự chữ cái!
     2. **Anti-join hồi tố TT99 theo `BRIDGED_QUARTER_KEY`** tại `periods.py:311-331` (loại bỏ `L321` positional splice bằng `join` tường minh theo khóa!) để khi có bản ghi hồi tố `RESTATED_REGIME_CHANGE` `IS_BORROWING_COSTS` cho `2025-Q2..Q4`, nó thay thế sạch `IS_INTEREST_EXPENSE` TT200 mà không sinh dòng trùng!
     3. **Khóa chặt 1 chuỗi note và điều kiện `source_provider == "fhsc"`:** Khi cửa sổ 4 quý trượt qua biên TT200 $\rightarrow$ TT99 trên cầu nối `IS_BORROWING_COSTS_BRIDGE` (không có hồi tố TT99):
        - Nếu `source_provider == "fhsc"` (xác minh cùng 1 trường `trongdochiphilaivay`): tính `ttm_value` hợp lệ và gắn duy nhất **`computation_note = "BRIDGED_TT200_INTEREST_TO_TT99_BORROWING"`**.
        - Nếu `source_provider != "fhsc"` (nguồn BCTC chính thức TT200/TT99 chưa có cột hồi tố TT99): trả `ttm_value = None` và **`computation_note = "MIXED_TAXONOMY"`** (tuân thủ tuyệt đối `AGENT.md` §5.5!).
     4. **Phát ra cột `ttm_exact_filing: pl.Boolean` ngay trong `_ttm_windows()`:**
        ```python
        (allowed & pl.all_horizontal(*last_four(pl.col("published_at_basis") == "EXACT_FILING")))
        .fill_null(False)
        .alias("ttm_exact_filing")
        ```
        *(Tính trên đúng `BRIDGED_SERIES` 4 quý của cửa sổ TTM để Wave 2A `intermediates.py:276-291` tiêu thụ trực tiếp — triệt tiêu hoàn toàn lỗi rớt 100% điểm ML năm 2026!)*

3. **Khai báo `BS_CIP_LONG_TERM` theo Đúng Luật TT200/TT99 (`MISS-01`, `HI-06`):**
   * Đọc fixture đã commit `tests/fixtures/raw_samples/fhsc_cip_sample.json` để xác nhận khóa FHSC (dự kiến `chiphixaydungcobandodang` khớp `docs/layers/LAYER_4_ANALYTICS_AND_ML.md:119`).
   * Khai báo trong `config/metric_definitions.yaml`:
     ```yaml
     BS_CIP_LONG_TERM:
       canonical_id: BS_CIP_LONG_TERM
       statement_type: BALANCE_SHEET
       value_type: STOCK
       unit: VND
       sign_convention: POSITIVE_NORMAL
       tt200_code: "242"
       tt99_code: "252"   # Phụ lục IV TT99: 250 Tài sản dở dang dài hạn -> 252 Chi phí XDCB dở dang (242 TT99 là hao mòn BĐSĐT!)
       comparable_across_taxonomy: true
     ```
   * Cập nhật tương ứng vào `corporate_tt200_2014.yaml` (`code: "242"`) và `corporate_tt99_2025.yaml` (`code: "252"`, nhãn `"Chi phí xây dựng cơ bản dở dang"`).

---

## 4. KIỂM THỬ TDD & TIÊU CHÍ NGHIỆM THU (`DEFINITION OF DONE`)

1. **`tests/unit/test_layer2_batch_incremental.py`:**
   * **Bit-Exact Equivalence:** `map_batch` + batched `normalize_archive` khớp 100% từng ô & ngoại lệ `ContractViolationError` với đường dẫn `_map()` cũ (bao gồm cả `map_restatement` với `period != filing` và `cash_flow_method=None`).
   * **Batch Boundary Integrity:** 120 issuers với `batch_issuers=50` xác nhận 0 `issuer_id` bị cắt đôi giữa 2 lô.
   * **Incremental Early-Cutoff, Deletion & Invalidation:**
     - Lần 1 (10 mã): full build.
     - Lần 2 (không đổi): `recomputed_issuers == 0` VÀ `curated/financial_facts/<run_2>/financial_facts.parquet` + `unmapped_metrics.parquet` đều tồn tại & khớp 100%.
     - Lần 3 (thêm/sửa 1 mã và xóa 1 filing của 1 mã khác): chỉ tính lại đúng 2 mã bị ảnh hưởng và khớp 100% cả 2 bảng Parquet với full rebuild.
     - Lần 4 (đổi 1 byte YAML hoặc `filing_settings`): tự động Full Rebuild tất cả 10 mã.
   * **Cross-Taxonomy 2026 Debt-Cost Bridge (`BLK-09`):**
     - Nguồn `fhsc` (`2025-Q2..Q4` TT200 `IS_INTEREST_EXPENSE` + `2026-Q1..Q3` TT99 `IS_BORROWING_COSTS`, tất cả `EXACT_FILING`): `compute_ttm()` xuất `ttm_value` chính xác, `computation_note == "BRIDGED_TT200_INTEREST_TO_TT99_BORROWING"`, và **`ttm_exact_filing == True`** trên cả `2026-Q1..Q3`.
     - Nguồn `source_provider="issuer"` (non-FHSC, chưa có hồi tố): trả `ttm_value is None` và `computation_note == "MIXED_TAXONOMY"`.
     - Khi bổ sung hồi tố `RESTATED_REGIME_CHANGE` `IS_BORROWING_COSTS` cho `2025-Q2..Q4`: thay thế sạch dòng TT200, tính TTM đồng nhất TT99 với `computation_note is None` (không bị duplicate quarter!).
   * **TT99 `BS_CIP_LONG_TERM` Mapping (`MISS-01`):** Xác minh mã `"242"` trong TT200 map vào `BS_CIP_LONG_TERM`, mã `"252"` trong TT99 map vào `BS_CIP_LONG_TERM`, và mã `"242"` trong TT99 đẩy vào `unmapped_metrics` (KHÔNG bị map nhầm sang `BS_CIP_LONG_TERM`!).
   * **Offline Synthetic 37-Issuer Benchmark (`MED-09`):** Tạo bộ giả lập 37 mã × 150 filings (5,550 filings) trong `tmp_path` xác nhận Full Normalize `< 3.5s`, Incremental (0–1 dirty issuer) `< 1.5s`, đỉnh RAM `< 500 MB`.
2. **Cổng chất lượng bắt buộc:** `ruff format --check`, `ruff check`, `mypy --strict`, `pytest -q`, và `pytest tests/reconciliation/test_golden_ratios.py -v` xanh 100%.

---

## 5. PROMPT GIAO VIỆC MẪU CHO CODING AGENT (COPY-PASTE READY)

```text
Đọc AGENT.md, docs/v2_specs/00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md và docs/v2_specs/03_STREAM_A2_LAYER2_BATCH_INCREMENTAL_AND_TT99_CONTINUITY.md.

Thực hiện ĐÚNG phạm vi Wave 1B (Stream A2) trên git worktree riêng v2/a2-layer2:
1. Chỉ sửa/tạo các file trong mục "Owned Files" của 03_STREAM_A2 (được sửa normalize.py và CHỈ Case 3 EDGE2 trong test_golden_ratios.py; TUYỆT ĐỐI KHÔNG sửa số liệu FPT 2024–2025!).
2. Đọc đối chiếu Polars Lazy/Streaming Docs, Phụ lục IV Thông tư 99/2025/TT-BTC và bài báo Boncz (2005) / Mokhov (2018); ghi Verified Sources trong docstring.
3. Chú ý 3 điểm sống còn: (a) BS_CIP_LONG_TERM có tt200_code="242" nhưng tt99_code="252" (KHÔNG phải 242!); (b) trong periods.py phải sort & anti-join theo BRIDGED_SERIES (không sort theo metric_id thô) và xuất ttm_exact_filing; (c) giữ nguyên return contract của normalize_archive() và dùng normalize_archive_incremental() cho NormalizeHandler.
4. Viết đầy đủ TDD tests & synthetic 37-issuer benchmark trong test_layer2_batch_incremental.py (không đọc data/live_fhsc_exact_pit/ hay data/live_fhsc_universe/); chạy xanh 100% ruff, mypy --strict, pytest -q, test_golden_ratios.py.
```
