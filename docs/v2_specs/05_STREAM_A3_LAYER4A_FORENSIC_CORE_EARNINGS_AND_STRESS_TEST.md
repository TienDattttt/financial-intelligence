# ĐẶC TẢ TRIỂN KHAI WAVE 2A — STREAM A3: TẦNG 4A (`ANALYTICS`) — KHẮC PHỤC TT99 `ttm_exact_filing`/`_exact_filing_basis`, CHẤT LƯỢNG LỢI NHUẬN LÕI (`SIGNED_EXPENSE`), `RF_08`/`RF_09` & STRESS TESTING

* **Mã đặc tả:** `SPEC-V2-05-STREAM-A3-v1.1`
* **Wave:** `Wave 2A` (Chạy **sau khi** Wave 1B `03_STREAM_A2` đã merge vào `v2/integration`; hoàn tất trước Wave 2B `06_STREAM_A4`)
* **Nhánh Git / Worktree:** `v2/a3-layer4a` (`../finintel-wt-a3-layer4a`)
* **Hạng mục giải quyết & Nâng cấp thực tiễn:**
  1. **`GAP-02` (Phần Layer 4A — đóng `BLK-09` & `MED-03`):** Tiêu thụ trực tiếp `ttm_exact_filing` từ `periods.py` (không tính lại `_ttm_exact` theo `metric_id` thô), nối liên tục `debt_cost` trên `VAS_TT99_2025` có khóa `MIXED_TAXONOMY`, và khôi phục `ebit_ttm`, `ebitda_ttm`, `nopat_ttm`, `fcf_ttm`, `ebit_growth_yoy` (nguồn FHSC đã bắc cầu), `DuPont` cùng 16/35 tỷ số trên `2026-Q1..Q3`.
  2. **`GAP-04` (Phần Layer 4A — đóng `MED-03`):** Sửa `_exact_filing_basis` trong `intermediates.py` so sánh `_period_available_at_prior <= _amounts_available_at` **TRƯỚC** khi ghi đè `_period_available_at` bằng `max_horizontal(...)`, xóa hiệu ứng domino 8 quý.
  3. **Nâng cấp Thương mại #1 — `Core Earnings & Non-Core Distortion Forensics` (Rouen, So & Wang, *JFE* 2021 — đóng `MISS-06`, `HI-11`, `HI-13`, `BLK-14`, `MISS-01`, `MISS-08`):**
     * Bổ sung `core_operating_profit_ttm` **bảo toàn đúng dấu `SIGNED_EXPENSE` (CẤM `.abs()`!)** với quy tắc khuyết chi phí bán hàng áp dụng **trước TTM theo từng tờ khai**.
     * Bổ sung `FORENSIC_RATIOS` (3 tỷ số) vào `schemas.py`, `ratios.py` và **`comparisons.py`** (đủ 38/38 tỷ số có benchmark ngành!), kèm 2 cờ đỏ pháp y **`RF_08_NON_CORE_EARNINGS_DEPENDENCE`** (tách điều kiện nhánh 1/2, ghi nhận TT99 Mã `21` & lưu ý liên doanh/tiền gửi) và **`RF_09_FROZEN_CIP_CAPITALIZATION`** dùng chuẩn **`BS_CIP_LONG_TERM` (TT200 `242` $\rightarrow$ TT99 `252`)** không chia cho `0`.
  4. **Nâng cấp Thương mại #2 — `Deterministic Corporate Financial Stress Testing` (IMF WP/15/216 — đóng `BLK-13`, `HI-12`):**
     * Bổ sung 5 cột scalar stress vào `RATIO_INTERMEDIATES` (`net_revenue_ttm`, `gross_profit_ttm`, `cogs_ttm`, `cfo_ttm`, `borrowing_cost_ttm`) để tầng Serving đọc trực tiếp không vi phạm `AGENT.md` §5.8.
     * Xây dựng module thuần túy vector hóa `src/finintel/analytics/stress_test.py` đo **Stressed EBIT, Stressed Interest Expense (`>= 0`), Stressed ICR, Stressed CFO (có chặn tax shield & sốc `DSO`/`DIO`), và IMF Debt-at-Risk (`ICR < 1.0`)** *(loại bỏ `Stressed Current Ratio` & `Stressed Altman Z''` chưa thiết kế khỏi phạm vi V2 để đồng bộ 100% giữa tiêu đề, mã nguồn và kiểm thử)*.

---

## 1. QUYỀN SỞ HỮU FILE TUYỆT ĐỐI (`STRICT FILE OWNERSHIP`)

* **Owned Files (Chỉ được phép sửa/tạo các file sau — mọi đường dẫn đều repo-relative):**
  * `src/finintel/storage/schemas.py` *(CHỈ mở rộng **additive backward-compatible**: giữ nguyên 100% `CORE_RATIOS` 28 tỷ số và `EXTRA_RATIOS` 7 tỷ số; thêm `FORENSIC_RATIOS` 3 tỷ số; bổ sung `core_operating_profit_ttm`, `net_revenue_ttm`, `gross_profit_ttm`, `cogs_ttm`, `cfo_ttm`, `borrowing_cost_ttm` vào `RATIO_INTERMEDIATES`, `core_operating_profit_flag` vào `RATIO_INTERMEDIATE_FLAGS`, và cặp value/flag `FORENSIC_RATIOS` vào `FINANCIAL_RATIOS_SCHEMA`)*
  * `src/finintel/analytics/intermediates.py`
  * `src/finintel/analytics/ratios.py`
  * `src/finintel/analytics/comparisons.py` *(mở rộng default `metrics = (*CORE_RATIOS, *EXTRA_RATIOS, *FORENSIC_RATIOS)` — đóng `BLK-05` & `BLK-14`)*
  * `src/finintel/analytics/red_flags.py`
  * `src/finintel/analytics/stress_test.py` *(mới)*
  * `src/finintel/analytics/tables.py`
  * `tests/unit/test_layer4_analytics.py`
  * `tests/unit/test_layer4_forensic_and_stress.py` *(mới)*
* **Read-Only Contract Files:** `docs/layers/LAYER_2_NORMALIZATION.md` (`L138` quy ước `SIGNED_EXPENSE`), `docs/layers/LAYER_4_ANALYTICS_AND_ML.md`, `config/metric_definitions.yaml`, `tests/reconciliation/test_golden_ratios.py`.
* **Forbidden Files:** Toàn bộ `src/finintel/{domain,ingestion,normalization,ml,quality,pipeline}/`, `src/finintel/storage/{repository,ducklake_store,snapshot_publisher,curated_gc,manifest_export}.py`, `dashboard/`, `data/live_fhsc_universe/`, `AGENT.md`.

---

## 2. NGUỒN KHOA HỌC & HỌC THUẬT ĐÃ KIỂM CHỨNG (BẮT BUỘC ĐỐI CHIẾU & GHI `Verified Sources`)

1. **Rouen, E., So, E. C., & Wang, C. C. Y. (2021).** *"Core Earnings: New Data and Evidence."* ***Journal of Financial Economics***, Vol. 142, Issue 3, pp. 1068–1091.
2. **Schilit, H. M., Perler, J., & Engelhart, Y. (2018).** *Financial Shenanigans: How to Detect Accounting Gimmicks and Fraud in Financial Reports* (4th Ed., McGraw-Hill) — *Shenanigan #3* (`RF_08`) & *Shenanigan #4* (`RF_09`).
3. **Dechow, P. M., Ge, W., Larson, C. R., & Sloan, R. G. (2011).** *"Predicting Material Accounting Misstatements."* ***Contemporary Accounting Research***, Vol. 28, Issue 1, pp. 17–82.
4. **Chow, J. T. S. (2015).** *"Stress Testing Corporate Balance Sheets in Emerging Markets."* **IMF Working Paper WP/15/216** (`https://www.elibrary.imf.org`).
5. **Thông tư 200/2014/TT-BTC & Thông tư 99/2025/TT-BTC (Phụ lục IV — B01-DN & B02-DN):** Quy ước `SIGNED_EXPENSE` (`docs/layers/LAYER_2_NORMALIZATION.md:138`), `BS_CIP_LONG_TERM` (`242 -> 252`), và TT99 B02-DN Mã `21` (`IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS`).

---

## 3. ĐẶC TẢ KỸ THUẬT CHI TIẾT

### 3.1. `src/finintel/analytics/intermediates.py` (`BLK-09`, `HI-11`, `HI-13`, `MED-03`, `MISS-06`, `BLK-13`)
1. **Tiêu thụ `ttm_exact_filing` từ `periods.py` (`BLK-09`):**
   * Tại `_period_amounts()` (`L276-L291`): bỏ việc tự tính lại `_ttm_exact` bằng `.shift(0..3).over(["issuer_id", "statement_scope", "source_provider", "metric_id"])` (vốn làm 100% `2026-Q1..Q3` rớt `_exact_filing_basis = False` khi `IS_BORROWING_COSTS` bắc cầu từ `IS_INTEREST_EXPENSE`).
   * Sử dụng trực tiếp cột `ttm_exact_filing` mà `compute_ttm(flow)` trả ra trên `BRIDGED_SERIES` (fallback về công thức 4-lag theo `metric_id` chỉ khi frame đầu vào từ mock test cũ không có cột `ttm_exact_filing`).
2. **Liên thông `debt_cost` trên TT99 có khóa `MIXED_TAXONOMY` (`MED-03`):**
   * `input_has("mixed", ...)` chỉ đánh dấu `True` khi `computation_note == "MIXED_TAXONOMY"` (KHÔNG chặn `computation_note == "BRIDGED_TT200_INTEREST_TO_TT99_BORROWING"`).
   * Trong `_aggregates()`:
     ```python
     debt_cost = (
         pl.when(c("taxonomy_version") == "VAS_TT99_2025")
         .then(
             pl.when(input_has("mixed", BORROWING_COSTS))
             .then(None)
             .otherwise(
                 pl.coalesce(
                     c(BORROWING_COSTS),
                     pl.when(~input_has("mixed", INTEREST)).then(c(INTEREST)),
                 )
             )
         )
         .otherwise(c(INTEREST))
         .abs()
     )
     ```
   * Xuất thêm 5 cột scalar chuẩn phục vụ `stress_test.py` (`BLK-13`): `net_revenue_ttm` (`c(REVENUE)`), `gross_profit_ttm` (`c(GROSS_PROFIT)`), `cogs_ttm` (`c(COGS).abs()`), `cfo_ttm` (`c(CFO)`), và `borrowing_cost_ttm` (`debt_cost`, song song giữ `_borrowing_cost_ttm` nếu có code nội bộ tham chiếu).
   * Lưu cờ `_bridged_debt_cost` (`computation_note == "BRIDGED_TT200_INTEREST_TO_TT99_BORROWING"` cho `BORROWING_COSTS`) để `ebit_growth_yoy` trong `ratios.py` cho phép so sánh YoY `2025 -> 2026` khi nguồn FHSC đã bắc cầu hợp lệ (trong khi vẫn giữ `MIXED_TAXONOMY` cho nguồn ngoài FHSC chưa hồi tố — `MED-03`).
3. **Sửa `_exact_filing_basis` so sánh TRƯỚC `max_horizontal` (`GAP-04`, `MED-03`):**
   * Tại `prepare_periods()` (`L635-L647`), `_period_available_at` bị ghi đè bằng `max_horizontal("_period_available_at", "_period_available_at_prior")`. Vì vậy **BẮT BUỘC** so sánh với cột gốc chưa max `_amounts_available_at`:
     ```python
     (
         pl.col("_period_exact_filing").fill_null(False)
         & pl.when(pl.col("_prior_end").is_not_null())
         .then(
             (pl.col("_period_available_at_prior") <= pl.col("_amounts_available_at"))
             .fill_null(False)
         )
         .otherwise(pl.lit(True))
     ).alias("_exact_filing_basis")
     ```
4. **Di chuyển sạch đầu vào khỏi `ML_STOCKS`/`ML_FLOWS` (Chống `DuplicateError` — `HI-11`):**
   * **Chuyển** `TANGIBLE_ASSETS` (`BS_TANGIBLE_FIXED_ASSETS`) từ `ML_STOCKS` sang `STOCKS` (`ML_STOCKS` chỉ còn `(RETAINED_EARNINGS, LT_INVESTMENTS)`).
   * **Chuyển** `SELLING_EXPENSES` (`IS_SELLING_EXPENSES`) và `ADMIN_EXPENSES` (`IS_ADMINISTRATIVE_EXPENSES`) từ `ML_FLOWS` sang `FLOWS` (`ML_FLOWS` chỉ còn `(EQUITY_PROCEEDS, NONCASH_ISSUANCE, CONTINUING_EARNINGS)`).
   * **Bổ sung** vào `STOCKS`: `CIP_LONG_TERM = "BS_CIP_LONG_TERM"` (`MISS-01`).
   * **Bổ sung** vào `FLOWS`: `FINANCIAL_REVENUE = "IS_FINANCIAL_REVENUE"`, `OTHER_INCOME = "IS_OTHER_INCOME"`, `OTHER_EXPENSES = "IS_OTHER_EXPENSES"`, `OTHER_PROFIT = "IS_OTHER_PROFIT"`, `IP_DISPOSAL = "IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS"`.
   * **Mở rộng theo dõi `_filing_*` (`L395-L398`):** Bổ sung `CIP_LONG_TERM` và `TANGIBLE_ASSETS` cùng `RF03_INPUTS` để `RF_09` đối chiếu cùng `filing_id` với `BS_TOTAL_ASSETS`.
   * *Bất biến:* `set(STOCKS).isdisjoint(ML_STOCKS)` và `set(FLOWS).isdisjoint(ML_FLOWS)` luôn `True` $\implies$ không bao giờ phát sinh `DuplicateError` khi Layer 4B gọi `prepare_periods(..., include_ml_inputs=True)`!
5. **`core_operating_profit_ttm` — BẢO TOÀN DẤU `SIGNED_EXPENSE` & QUY TẮC TRƯỚC-TTM (`MISS-06`, `HI-13`):**
   * **Công thức VAS chuẩn (CẤM dùng `.abs()`! — `MISS-06`):** Theo `docs/layers/LAYER_2_NORMALIZATION.md:138`, `IS_SELLING_EXPENSES` và `IS_ADMINISTRATIVE_EXPENSES` mang quy ước **`SIGNED_EXPENSE`** (chi phí thông thường mang dấu dương và bị trừ; khoản hoàn nhập chi phí mang dấu âm và cộng ngược lại lợi nhuận):
     $$\text{core\_operating\_profit\_ttm} = \text{IS\_GROSS\_PROFIT} - \text{IS\_SELLING\_EXPENSES} - \text{IS\_ADMINISTRATIVE\_EXPENSES}$$
   * **Quy tắc xử lý khuyết `IS_SELLING_EXPENSES` ÁP DỤNG TRƯỚC `compute_ttm()` (`HI-13`):**
     * **CẤM** thay `IS_SELLING_EXPENSES = NULL` thành `0.0` sau khi tính TTM (sẽ âm thầm xóa sổ 3 quý chi phí bán hàng thật nếu Q4 bị khuyết hoặc `MIXED_TAXONOMY`!).
     * Trong `_period_amounts()`, **TRƯỚC khi gọi `compute_ttm(flow)`**, với từng tờ khai đơn lẻ `(issuer_id, statement_scope, period_type, fiscal_year_start_date, period_end_date, filing_id, source_provider)`: chỉ chèn dòng tổng hợp `IS_SELLING_EXPENSES = 0.0` (kế thừa đúng `published_at_basis`, `effective_available_at`, `taxonomy_version`, `comparable_across_taxonomy` của `IS_OPERATING_PROFIT` trên đúng tờ khai đó) khi tờ khai đó có mặt **đồng thời** `IS_GROSS_PROFIT`, `IS_ADMINISTRATIVE_EXPENSES`, và `IS_OPERATING_PROFIT` (Mã `30`) không `NULL` nhưng hoàn toàn không có dòng `IS_SELLING_EXPENSES`. Mọi trường hợp thiếu dữ liệu khác giữ nguyên `NULL` (`MISSING_INPUT`).

### 3.2. `schemas.py`, `ratios.py` & `comparisons.py` (`BLK-13`, `BLK-14`)
* **`src/finintel/storage/schemas.py`:**
  * Giữ nguyên 100% `CORE_RATIOS` (28) và `EXTRA_RATIOS` (7).
  * Thêm `FORENSIC_RATIOS = ("core_operating_margin", "non_core_distortion_to_assets", "core_to_ebt_ratio")`.
  * Bổ sung vào `RATIO_INTERMEDIATES`: `"core_operating_profit_ttm"`, `"net_revenue_ttm"`, `"gross_profit_ttm"`, `"cogs_ttm"`, `"cfo_ttm"`, `"borrowing_cost_ttm"`; vào `RATIO_INTERMEDIATE_FLAGS`: `"core_operating_profit_flag"`.
  * Mở rộng `FINANCIAL_RATIOS_SCHEMA` gồm cả `FORENSIC_RATIOS` (value `pl.Float64` + flag `pl.String`) và các cột `RATIO_INTERMEDIATES` mới (thứ tự additive; `tables.py` xuất đầy đủ).
* **`src/finintel/analytics/ratios.py` (chỉ áp dụng cho `CORPORATE`; ngoài `CORPORATE` nhận `NOT_APPLICABLE_FOR_REGIME`):**
  1. `core_operating_margin = core_operating_profit_ttm / IS_NET_REVENUE` (dùng `_ratio(...)`, mẫu số $\le 0 \rightarrow$ `ZERO_OR_NEGATIVE_DENOMINATOR`).
  2. `non_core_distortion_to_assets = (IS_PROFIT_BEFORE_TAX - (core_operating_profit_ttm - borrowing_cost_ttm)) / average(BS_TOTAL_ASSETS)` (mẫu số $\le 0 \rightarrow$ `ZERO_OR_NEGATIVE_DENOMINATOR`; dương lớn nghĩa là EBT được nâng đỡ bởi thu nhập tài chính/bất thường theo Rouen, So & Wang 2021).
  3. `core_to_ebt_ratio = (core_operating_profit_ttm - borrowing_cost_ttm) / IS_PROFIT_BEFORE_TAX` (chỉ tính khi `IS_PROFIT_BEFORE_TAX > 0`, ngược lại `NULL` kèm `ZERO_OR_NEGATIVE_DENOMINATOR`).
  4. **`ebit_growth_yoy` (`MED-03`):** Kiểm tra `cross_taxonomy(EBT)` kèm cả `ebit_ttm` & `ebit_ttm_prior` đều hợp lệ và không `MIXED_TAXONOMY`, cho phép nguồn FHSC đã bắc cầu hợp lệ tính YoY `2025 -> 2026` trong khi giữ `MIXED_TAXONOMY` cho nguồn ngoài FHSC chưa hồi tố.
* **`src/finintel/analytics/comparisons.py` (`BLK-05`, `BLK-14`):**
  * Cập nhật mặc định `metrics: tuple[str, ...] = (*CORE_RATIOS, *EXTRA_RATIOS, *FORENSIC_RATIOS)` để `industry_benchmarks.parquet` chứa đầy đủ benchmark cho cả 38/38 tỷ số.

### 3.3. Bổ sung 2 Cờ đỏ Pháp y (`RF_08` & `RF_09`) tại `src/finintel/analytics/red_flags.py` (`HI-13`, `MISS-01`, `MISS-08`)
1. **`RF_08_NON_CORE_EARNINGS_DEPENDENCE`** (`severity = "HIGH"`, chỉ cho `CORPORATE`):
   * *Tiền đề chung:* `c(EBT) > 0`, `c("core_operating_profit_ttm").is_not_null()`, `c("borrowing_cost_ttm").is_not_null()`, và `~input_has("mixed", EBT, GROSS_PROFIT, SELLING_EXPENSES, ADMIN_EXPENSES, INTEREST, BORROWING_COSTS)`.
   * *Tách điều kiện nhánh không chặn oan (`HI-13`):*
     - **Nhánh 1 (lỗ lõi được che bởi lãi phi lõi):** `(c("core_operating_profit_ttm") - c("borrowing_cost_ttm") <= 0)` *(KHÔNG đòi hỏi tài sản đầu kỳ hay `other_profit_ttm` phải khác `NULL`!)*.
     - **Nhánh 2 (lệch phi lõi lớn):** `c("core_to_ebt_ratio").is_not_null() & c("non_core_distortion_to_assets").is_not_null() & (c("core_to_ebt_ratio") < 0.50) & (c("non_core_distortion_to_assets") > 0.03)`.
   * *Bằng chứng JSON (`MISS-08`):* `ebt_ttm`, `core_operating_profit_ttm`, `debt_cost_ttm`, `core_to_ebt_ratio`, `non_core_distortion_to_assets`, `financial_revenue_ttm`, `other_profit_ttm` (`pl.coalesce(c(OTHER_PROFIT), c(OTHER_INCOME) - c(OTHER_EXPENSES))`), `ip_disposal_gain_loss_ttm` (TT99 Mã `21`, `NULL` trên TT200), và `source_filing_ids`.
   * *Minh bạch giới hạn kế toán (`MISS-08`):* Ghi rõ trong `explanation_text_vn` và docstring rằng `RF_08` là cờ tầm soát sơ bộ — với các tập đoàn holding có lãi công ty liên doanh liên kết (Mã `24` B02-DN/HN chưa tách riêng) hoặc doanh nghiệp thừa tiền mặt hưởng lãi tiền gửi ngân hàng ổn định (`IS_FINANCIAL_REVENUE`), nhà phân tích cần đọc thuyết minh kiểm chứng nguồn thu tài chính.
2. **`RF_09_FROZEN_CIP_CAPITALIZATION`** (`severity = "MEDIUM"`, chỉ cho `CORPORATE`):
   * *Dùng đúng mã chuẩn `BS_CIP_LONG_TERM` (`MISS-01`: TT200 `242` $\rightarrow$ TT99 `252`):* Đọc `BS_CIP_LONG_TERM` và `BS_TANGIBLE_FIXED_ASSETS` theo đúng `filing_id` của `BS_TOTAL_ASSETS` (`_filing_BS_CIP_LONG_TERM == _filing_BS_TOTAL_ASSETS` và `_filing_BS_TANGIBLE_FIXED_ASSETS == _filing_BS_TOTAL_ASSETS`). Nếu `BS_CIP_LONG_TERM` là `NULL` $\rightarrow$ im lặng không kích hoạt (no-op an toàn).
   * *Điều kiện kích hoạt (chống chia cho `0` khi `TFA_{t-4} == 0` — `HI-13`):*
     - `(c(ASSETS) > 0) & (c(f"{ASSETS}_prior") > 0) & (c(CIP_LONG_TERM) > 0) & (c(f"{CIP_LONG_TERM}_prior") > 0)`
     - `~cross_taxonomy(ASSETS, CIP_LONG_TERM, TANGIBLE_ASSETS, REVENUE)` và `~input_has("mixed", ASSETS, CIP_LONG_TERM, TANGIBLE_ASSETS, REVENUE, f"{ASSETS}_prior", f"{CIP_LONG_TERM}_prior", f"{TANGIBLE_ASSETS}_prior", f"{REVENUE}_prior")`
     - `(c(CIP_LONG_TERM) / c(ASSETS) > 0.20)` và `(c(f"{CIP_LONG_TERM}_prior") / c(f"{ASSETS}_prior") > 0.20)`
     - `c(CIP_LONG_TERM) >= 0.95 * c(f"{CIP_LONG_TERM}_prior")`
     - `c("revenue_growth_yoy").is_not_null() & (c("revenue_growth_yoy") <= 0.05)`
     - `c(TANGIBLE_ASSETS).is_not_null() & c(f"{TANGIBLE_ASSETS}_prior").is_not_null() & (c(TANGIBLE_ASSETS) <= 1.05 * c(f"{TANGIBLE_ASSETS}_prior"))` *(không bao giờ chia cho `TFA_{t-4} == 0`!)*.
   * *Bằng chứng JSON:* `cip_to_assets_t`, `cip_to_assets_prior`, `cip_growth_yoy`, `tangible_fixed_assets_growth_yoy` (`None` nếu `TFA_{t-4} <= 0`), `revenue_growth_yoy`, `source_filing_ids`.

### 3.4. Module Kiểm định Sức chịu đựng Tài chính (`src/finintel/analytics/stress_test.py` — `BLK-13`, `HI-12`)
Theo phương pháp **IMF WP/15/216 (Chow, 2015)**, thuần túy vector hóa 100% bằng `Polars`, không đụng ổ đĩa:

```python
@dataclass(frozen=True, slots=True)
class StressScenario:
    name: str
    interest_rate_shock_bps: float = 0.0   # bps, biên [-500.0, +1500.0] (vd +300 = +3.0%/năm)
    revenue_shock_pct: float = 0.0         # THẬP PHÂN, biên [-0.80, +0.50] (vd -0.15 = -15%)
    gross_margin_shock_pp: float = 0.0     # THẬP PHÂN, biên [-0.50, +0.20] (vd -0.03 = -3 điểm %)
    dso_shock_days: float = 0.0            # ngày, biên [-90.0, +180.0]
    dio_shock_days: float = 0.0            # ngày, biên [-90.0, +180.0]
```

* **Validation chặt (`StressScenario.__post_init__`):** Kiểm tra `math.isfinite(...)` và đúng biên cho cả 5 tham số — ném `ContractViolationError` nếu vượt biên hoặc `NaN`/`Inf`.
* **`simulate_financial_stress(ratios_with_inputs: pl.DataFrame, scenario: StressScenario) -> pl.DataFrame`:**
  * Yêu cầu các cột đã xuất bản sẵn trong `financial_ratios`: `accounting_regime`, `statement_scope`, `period_type`, `period_end_date`, `net_revenue_ttm`, `gross_profit_ttm`, `cogs_ttm`, `cfo_ttm`, `borrowing_cost_ttm`, `total_debt`, `ebit_ttm`, `gross_margin`, `effective_tax_rate`, `interest_coverage`, `interest_coverage_flag`.
  * Tính toán cho các dòng `CORPORATE` có `net_revenue_ttm > 0` (nếu `net_revenue_ttm <= 0` hoặc ngoài `CORPORATE` $\rightarrow$ `stressed_*` là `NULL` để không đảo ngược dấu cú sốc!):
    1. **Doanh thu & Lợi nhuận gộp sau sốc:**
       - $\text{Rev}' = \text{net\_revenue\_ttm} \times (1 + \Delta \text{Rev})$
       - $\text{GM}' = \text{gross\_margin} + \Delta \text{GM}$; $\text{GP}' = \text{Rev}' \times \text{GM}'$; $\Delta \text{GP} = \text{GP}' - \text{gross\_profit\_ttm}$
       - $\text{COGS}' = \max(0.0, \text{Rev}' - \text{GP}')$ *(luôn không âm — `HI-12`)*
    2. **EBIT & Chi phí lãi vay sau sốc:**
       - $\text{EBIT}' = \text{ebit\_ttm} + \Delta \text{GP}$
       - $\text{Int}' = \max\!\left(0.0,\ \text{borrowing\_cost\_ttm} + \text{total\_debt} \times \frac{\Delta r_{\text{bps}}}{10000}\right)$ *(chặn dưới `0.0`, cấm sinh lãi vay âm — `HI-12`)*; $\Delta \text{Int} = \text{Int}' - \text{borrowing\_cost\_ttm}$.
    3. **ICR sau sốc:**
       - `stressed_interest_coverage`: `EBIT' / Int'` khi `Int' > 0`, ngược lại `None`.
       - `stressed_interest_coverage_flag`: `"NO_INTEREST_EXPENSE"` khi `Int' == 0`, `"MISSING_INPUT"` khi đầu vào khuyết, `"VALID"` khi hữu hạn.
    4. **CFO sau sốc (có chặn lá chắn thuế TNDN khi EBT âm — `HI-12`):**
       - $\text{EBT}_0 = \text{ebit\_ttm} - \text{borrowing\_cost\_ttm}$; $\text{EBT}' = \text{EBIT}' - \text{Int}'$.
       - $\Delta \text{Tax} = (\max(0.0, \text{EBT}') - \max(0.0, \text{EBT}_0)) \times \text{effective\_tax\_rate}$ *(không tạo hoàn thuế 20% ảo trên khoản lỗ!)*.
       - $\Delta \text{WC\_cash} = -\frac{\text{Rev}'}{365} \times \Delta \text{DSO} - \frac{\text{COGS}'}{365} \times \Delta \text{DIO}$.
       - $\text{CFO}' = \text{cfo\_ttm} + (\Delta \text{GP} - \Delta \text{Int}) - \Delta \text{Tax} + \Delta \text{WC\_cash}$.
    5. **Phân loại IMF Debt-at-Risk an toàn 3-valued `NULL` (`HI-12`):**
       - `baseline_debt_at_risk`: `False` nếu `interest_coverage_flag == "NO_INTEREST_EXPENSE"` hoặc `total_debt <= 0`; `True` nếu `(interest_coverage < 1.0) & (total_debt > 0)`; `False` nếu `interest_coverage >= 1.0`; `None` nếu thực sự khuyết đầu vào.
       - `stressed_debt_at_risk`: tương tự trên `stressed_interest_coverage` / `stressed_interest_coverage_flag`.
       - `newly_distressed_under_shock = (~baseline_debt_at_risk) & stressed_debt_at_risk` *(doanh nghiệp `NO_INTEREST_EXPENSE` trước sốc rơi xuống `ICR' < 1.0` sau sốc sẽ được gắn đúng `True`!)*.
* **`summarize_stress_by_sector(stressed_df: pl.DataFrame) -> pl.DataFrame` (`HI-12`):**
  * **Bắt buộc kiểm tra đơn-lát-cắt:** Nếu `stressed_df` chứa $> 1$ tổ hợp `(statement_scope, period_type, period_end_date)` khác nhau $\rightarrow$ ném `ContractViolationError` (cấm cộng dồn nợ vay nhiều kỳ!).
  * Tổng hợp theo `icb_code_level2` / `icb_name_level2` (fallback `"UNCLASSIFIED"` nếu `NULL`): `issuer_count`, `total_debt_sum`, `baseline_debt_at_risk_pct`, `stressed_debt_at_risk_pct`, `newly_distressed_count`.

---

## 4. KIỂM THỬ TDD & TIÊU CHÍ NGHIỆM THU (`DEFINITION OF DONE`)

1. **`tests/unit/test_layer4_forensic_and_stress.py`:**
   * **Test `GAP-02` & `BLK-09` (2026 TT99 Continuity + `ttm_exact_filing`):** 6 quý (`2025-Q1..Q4` TT200 + `2026-Q1..Q2` TT99, nguồn `fhsc`, `EXACT_FILING`) qua `prepare_periods` $\rightarrow$ `calculate_ratios` $\rightarrow$ `add_dupont` $\rightarrow$ `ebit_ttm`, `ebitda_ttm`, `roic`, `interest_coverage`, `dupont_valid_flag` đều `VALID` VÀ **`_exact_filing_basis == True`** trên cả `2026-Q1..Q2`; kiểm chứng nguồn non-FHSC vẫn ra `MIXED_TAXONOMY`.
   * **Test `GAP-04` & `MED-03`:** `2017` là `STATUTORY_LAG`, `2018-Q1..2019-Q3` là `EXACT_FILING` $\rightarrow$ `2018-Q4..2019-Q3` có `_exact_filing_basis == True`, và kỳ có `_prior_available_at > _amounts_available_at` bị loại (`False`).
   * **Test `HI-11` No DuplicateError:** Gọi `prepare_periods(..., include_ml_inputs=True)` và `include_ml_inputs=False` đều sạch không `DuplicateError`; `benchmark_ratios()` xuất đủ **38** tỷ số (gồm `FORENSIC_RATIOS`).
   * **Test `Core Earnings` (`MISS-06`, `HI-13`):**
     - Hoàn nhập chi phí bán hàng (`IS_SELLING_EXPENSES = -5`, `IS_GROSS_PROFIT = 100`, `IS_ADMINISTRATIVE_EXPENSES = 20`) $\rightarrow$ **`core_operating_profit_ttm == 85`** (KHÔNG phải `75`!).
     - Khuyết `IS_SELLING_EXPENSES` chỉ ở 1 quý trên tờ khai đủ `Mã 30` $\rightarrow$ quý đó là `0.0` trước TTM, còn 3 quý kia giữ nguyên chi phí thật (KHÔNG xóa sạch TTM!).
   * **Test `RF_08` & `RF_09` (`HI-13`, `MISS-01`, `MISS-08`):**
     - `RF_08` Nhánh 1 kích hoạt ngay cả khi thiếu tài sản đầu kỳ; `evidence_json` có `ip_disposal_gain_loss_ttm` & fallback `IS_OTHER_INCOME - IS_OTHER_EXPENSES`.
     - `RF_09` dùng `BS_CIP_LONG_TERM` (`242 -> 252`), xử lý sạch trường hợp `TFA_{t-4} == 0` không chia cho `0`, và không kích hoạt khi `BS_CIP_LONG_TERM` là `NULL` hoặc `TFA` tăng `+25%`.
   * **Test `stress_test.py` (`HI-12`):**
     - Zero-Shock Identity (khớp baseline trong `1e-12` cho mọi dòng `Rev > 0`).
     - Monotonicity Test: trên nhóm `EBIT > 0, GM > 0, Rev > 0, total_debt > 0`, tăng `bps` / giảm `rev` làm `ICR'` không tăng; trên toàn tập hợp lệ, `stressed_debt_at_risk` đơn điệu không giảm.
     - Edge cases: `-500 bps` không sinh `Int' < 0`; lỗ không sinh hoàn thuế `20%` ảo; doanh nghiệp `NO_INTEREST_EXPENSE` chuyển sang `newly_distressed_under_shock == True` (không `NULL`); đa kỳ vào `summarize_stress_by_sector()` ném `ContractViolationError`; tham số vượt biên ném `ContractViolationError`.
2. **Kiểm tra Hồi quy Tuyệt đối:** `pytest tests/reconciliation/test_golden_ratios.py -v`, `pytest tests/integration/test_end_to_end_slice.py -v`, `ruff format --check`, `ruff check`, `mypy --strict`, `pytest -q` xanh 100%.

---

## 5. PROMPT GIAO VIỆC MẪU CHO CODING AGENT (COPY-PASTE READY)

```text
Đọc AGENT.md, docs/v2_specs/00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md và docs/v2_specs/05_STREAM_A3_LAYER4A_FORENSIC_CORE_EARNINGS_AND_STRESS_TEST.md.

Thực hiện ĐÚNG phạm vi Wave 2A (Stream A3) trên git worktree riêng v2/a3-layer4a (tách từ v2/integration sau khi 03_STREAM_A2 đã merge):
1. Chỉ sửa/tạo các file trong mục "Owned Files" của 05_STREAM_A3 (gồm comparisons.py và schemas.py additive; giữ nguyên CORE_RATIOS 28 & EXTRA_RATIOS 7; bổ sung 5 cột scalar stress vào RATIO_INTERMEDIATES).
2. Đối chiếu Rouen, So & Wang (JFE 2021), Schilit et al. (2018), Chow (IMF WP/15/216) và docs/layers/LAYER_2_NORMALIZATION.md (L138 SIGNED_EXPENSE); ghi Verified Sources trong docstring.
3. Lưu ý 5 điểm sống còn: (a) TUYỆT ĐỐI KHÔNG dùng .abs() trên IS_SELLING_EXPENSES / IS_ADMINISTRATIVE_EXPENSES; (b) áp quy tắc khuyết bán hàng = 0 TRƯỚC compute_ttm theo từng tờ khai; (c) di chuyển SELLING/ADMIN/TANGIBLE khỏi ML_* sang FLOWS/STOCKS để tránh DuplicateError; (d) dùng BS_CIP_LONG_TERM (242->252) & TFA_t <= 1.05 * TFA_{t-4} (không chia 0); (e) kẹp Int'>=0, COGS'>=0, chặn tax shield trên EBT dương và kiểm tra đơn-lát-cắt trong summarize_stress_by_sector().
4. Viết đầy đủ test trong test_layer4_forensic_and_stress.py và chạy xanh 100%: ruff, mypy --strict, pytest -q, test_golden_ratios.py, test_end_to_end_slice.py.
```
