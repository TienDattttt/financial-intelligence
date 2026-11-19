# ĐẶC TẢ TRIỂN KHAI WAVE 2B — STREAM A4: TẦNG 4B (`ML`) — KHẮC PHỤC `GAP-02..06`, PIT-SYMMETRIC PEER Z-SCORES, PLATT OOF & HỢP ĐỒNG MODE B NGOẠI TUYẾN

* **Mã đặc tả:** `SPEC-V2-06-STREAM-A4-v1.1`
* **Wave:** `Wave 2B` (Chạy **sau khi** Wave 2A `05_STREAM_A3` đã merge vào `v2/integration`)
* **Nhánh Git / Worktree:** `v2/a4-layer4b` (`../finintel-wt-a4-layer4b`)
* **Toàn bộ lỗ hổng & nâng cấp giải quyết triệt để:**
  1. **`GAP-02` (`MISS-07`, `MED-04`, `BLK-05`):** Mở khóa chấm điểm Unsupervised & Supervised ML cho toàn bộ kỳ **`QUARTER`** thuộc **`{"VAS_TT200_2014", "VAS_TT99_2025"}`** (phủ 100% năm `2026-Q1..Q3`!) **và giữ chấm điểm ML chặt chẽ trên `QUARTER`** (KHÔNG trộn dòng `ANNUAL` vào mô hình đã huấn luyện/hiệu chuẩn trên `QUARTER`; các điểm kinh điển Altman/Piotroski/Beneish tiếp tục chấm cả `QUARTER` & `ANNUAL`). Nối sạch cấu hình qua `src/finintel/pipeline/analytics.py`.
  2. **`GAP-03` (`BLK-18`):** Sửa `target.py` khắc phục bẫy `CFO_TTM > 0` trên LCTT gián tiếp, giữ `total_debt` là **cột tùy chọn** (bảo toàn 100% 5 unit test nền), viết đúng bảng chân trị 3-valued `*_known` bảo toàn doanh nghiệp `NO_INTEREST_EXPENSE`, mở rộng Event 3 trên 4 quý `{t+1..t+4}`, và sửa triệt để lỗi nhìn lui 7 quý về **đúng 4 quý `{t-3..t}`** (`already_distressed_at_t`).
  3. **`GAP-04`:** Sửa `_label_exact_filing_basis` trong `target.py` đọc `_period_exact_filing` của 4 quý tương lai `{t+1..t+4}`.
  4. **`GAP-05` (`BLK-16`, `HI-14`):**
     * Thay `CalibratedClassifierCV(FrozenEstimator)` bằng **`MarginPlattCalibrator`** trên `raw_margin` với Out-of-Fold Stratified Group CV (có bảo vệ `n_pos_groups < 2`, single-class fold, và fallback chặn trên MLE điều chỉnh theo `scale_pos_weight` khi $w \le 0$).
     * Cổng `min_unique_positive_issuers: int = 3` (fail-fast `InsufficientModelData`).
     * **Chuẩn hóa theo nhóm ngang hàng bảo toàn tuyệt đối Point-in-Time (`BLK-16` — CẤM coi ICB hiện tại 2025/2026 là chân lý lịch sử!):** Giữ nguyên lọc `industry_snapshot_date <= as_of`, tính `sector_z_*` nghiêm ngặt bên trong từng mốc `cutoff` của fold, áp dụng **Đối xứng Cohort PIT (`PIT_REGIME_SYMMETRIC` khi độ phủ ICB lịch sử `< 80%`, tự động nâng cấp lên `PIT_ICB` khi có lịch sử ICB $\ge 80\%$)**, giữ nguyên `BASE_FEATURES` (20 biến) và khai báo `SECTOR_ADJUSTED_FEATURES` đăng ký đủ quy tắc đơn điệu cho cả 4 biến `sector_z_*`.
  5. **`GAP-06` (`MED-04`):** Thêm `clip_range: tuple[float, float] | None = None` vào `CrossSectionalPreprocessor` (chỉ cắt `(-5.0, 5.0)` cho Unsupervised, giữ nguyên đuôi phân bố cho Supervised!); khử trùng tên cụm K-Means gồm cả nhánh cụm trung tính với `kind="mergesort"`.
  6. **Mode B `REGULATORY_DISTRESS` — Hợp đồng Thư viện Ngoại tuyến (`BLK-17`):** Giữ nguyên tên tham số `mode`, phạm vi chặt chẽ là **offline library contract** nhận bảng `regulatory_events` từ ngoài (KHÔNG xây trình cào HTML sàn trong V2), lọc `event_date <= as_of`, tiền tổng hợp chống nhân đôi dòng, cho phép hủy niêm yết bắt buộc sinh `distress_target = 1` không cần 4 quý sau delisting, và khóa chống rò rỉ right-censoring trên nhãn âm `y = 0` (`label_available_at = max(mode_a_avail, a_t + 365d) <= as_of`).

---

## 1. QUYỀN SỞ HỮU FILE TUYỆT ĐỐI (`STRICT FILE OWNERSHIP`)

* **Owned Files (Chỉ được phép sửa các file sau — mọi đường dẫn đều repo-relative):**
  * `src/finintel/ml/supervised/target.py`
  * `src/finintel/ml/supervised/temporal_split.py`
  * `src/finintel/ml/supervised/train.py`
  * `src/finintel/ml/supervised/explain.py`
  * `src/finintel/ml/unsupervised.py`
  * `src/finintel/ml/tables.py`
  * `src/finintel/pipeline/analytics.py` *(nối `scored_taxonomies`, `feature_set`, `target_mode`, `regulatory_events_path` tùy chọn — đóng `BLK-05`)*
  * `tests/unit/test_layer4_ml.py`
  * `tests/unit/test_layer4_ml_supervised.py`
* **Read-Only Contract Files:** `docs/layers/LAYER_4_ANALYTICS_AND_ML.md`, `src/finintel/storage/schemas.py`, `src/finintel/analytics/intermediates.py`, `src/finintel/analytics/comparisons.py`.
* **Forbidden Files:** Toàn bộ `src/finintel/{domain,ingestion,normalization,storage,analytics,quality}/`, `config/`, `dashboard/`, `data/live_fhsc_universe/`, `AGENT.md`.

---

## 2. NGUỒN KHOA HỌC, TÀI LIỆU CHÍNH THỨC & PHÁP LÝ BẮT BUỘC ĐỐI CHIẾU TRƯỚC KHI CODE

1. **Shumway, T. (2001).** *"Forecasting Bankruptcy More Accurately: A Simple Hazard Model."* ***The Journal of Business***, Vol. 74, No. 1, pp. 101–124.
2. **Falkenstein, E., Boral, A., & Carty, L. V. (2000).** *"RiskCalc™ For Private Companies: Moody's Default Model."* **Moody’s Investors Service Global Credit Research**.
3. **Platt, J. C. (1999)** & **Niculescu-Mizil, A., & Caruana, R. (2005)** (**ICML 2005**, pp. 625–632) — Platt Scaling fit trên `raw_margin` (log-odds), không fit Sigmoid chồng lên xác suất.
4. **Lundberg, S. M. et al. (2020).** ***Nature Machine Intelligence***, Vol. 2, pp. 56–67 · `https://shap.readthedocs.io/en/latest/generated/shap.TreeExplainer.html`
5. **XGBoost & scikit-learn Docs:**
   * `https://xgboost.readthedocs.io/en/stable/tutorials/monotonic.html`
   * `https://scikit-learn.org/stable/modules/calibration.html`
   * `https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.StratifiedGroupKFold.html`
6. **Khung Pháp lý cho Hợp đồng Mode B Ngoại tuyến:** Nghị định 155/2020/NĐ-CP (Điều 120) & Quyết định 22/QĐ-HĐTV ngày 18/04/2025 của VNX.

---

## 3. ĐẶC TẢ KỸ THUẬT CHI TIẾT

### 3.1. `src/finintel/ml/supervised/target.py` (`GAP-03`, `GAP-04`, `BLK-17`, `BLK-18`)
1. **Bảo toàn chữ ký & tương thích ngược:**
   ```python
   def build_distress_targets(
       periods: pl.DataFrame,
       *,
       mode: str = "FUNDAMENTAL_DISTRESS",
       as_of: date | None = None,
       regulatory_events: pl.DataFrame | None = None,
   ) -> pl.DataFrame: ...
   ```
   * **KHÔNG** đổi tên `mode` thành `target_mode` và **KHÔNG** đưa `total_debt` vào `_REQUIRED` (`BLK-18` — bảo vệ 100% 5 bài test mục tiêu hiện hữu!).
2. **Định nghĩa Sự kiện & Bảng chân trị 3-valued `*_known` Chính xác (`BLK-18`):**
   * `no_interest = (pl.col("interest_coverage_flag") == "NO_INTEREST_EXPENSE")` (nếu có cột, ngược lại `False`).
   * `has_total_debt = "total_debt" in base.columns`.
   * **Sự kiện 1 (`equity_bad`):** `((BS_TOTAL_EQUITY <= 0) | (parent_equity <= 0)).fill_null(False)`; `equity_known = BS_TOTAL_EQUITY.is_not_null() & parent_equity.is_not_null()`.
   * **Sự kiện 2 (`liquidity_bad` — phá bẫy `CFO > 0` LCTT gián tiếp):**
     - `branch_a = (interest_coverage < 1.0) & (CF_OPERATING_CASH_FLOW < 0) & (current_ratio < 1.0)`
     - `branch_b = (interest_coverage < 0.0) & (current_ratio < 0.80) & (total_debt > 0)` nếu `has_total_debt` ngược lại `pl.lit(False)`.
     - `liquidity_bad = (branch_a | branch_b).fill_null(False)`.
     - `branch_a_known = no_interest | (interest_coverage >= 1.0).fill_null(False) | (CF_OPERATING_CASH_FLOW >= 0).fill_null(False) | (current_ratio >= 1.0).fill_null(False) | (interest_coverage.is_not_null() & CF_OPERATING_CASH_FLOW.is_not_null() & current_ratio.is_not_null())`
     - `branch_b_known = pl.lit(True)` nếu `not has_total_debt` else `(no_interest | (interest_coverage >= 0.0).fill_null(False) | (current_ratio >= 0.80).fill_null(False) | (total_debt <= 0).fill_null(False) | (interest_coverage.is_not_null() & current_ratio.is_not_null() & total_debt.is_not_null()))`
     - `liquidity_known = branch_a_known & branch_b_known` *(bảo toàn 100% doanh nghiệp không nợ vay `no_interest == True` tại `test_layer4_ml_supervised.py:257`!)*.
   * **Sự kiện 3 (`severe_loss` trên cả 4 quý `{t+1..t+4}`):**
     - `severe_loss = ((roa < -0.10) & ((CF_OPERATING_CASH_FLOW < 0) | ((interest_coverage < 1.0) & (current_ratio < 1.0)))).fill_null(False)`.
     - `loss_known = roa.is_not_null() & ((roa >= -0.10) | (CF_OPERATING_CASH_FLOW.is_not_null() & (interest_coverage.is_not_null() | no_interest) & current_ratio.is_not_null()))`.
     - Trong cửa sổ tương lai: `event_3 = any(_loss_{1..4})` và `negative_verified` kiểm tra `all(_loss_known_{1..4})`!
3. **Sửa lỗi 7 Quý về Đúng Cửa sổ Duy trì 4 Quý (`already_distressed_at_t` — `BLK-18`):**
   * Join các độ trễ `lag in (1, 2, 3)` cho `_equity_bad`, `_severe_loss`, `_liquidity_bad` (độ trễ thiếu trước đầu lịch sử mặc định `False`).
   * `trailing_liquidity_4q = sum(_liquidity_bad ở {t, t-1, t-2, t-3})`.
   * `any_eq_or_loss_4q = any(_equity_bad | _severe_loss ở {t, t-1, t-2, t-3})`.
   * **`distressed_now = any_eq_or_loss_4q | (trailing_liquidity_4q >= 2)`** $\implies$ tuyệt đối không kéo ngược về `t-6..t-5`; doanh nghiệp phục hồi đủ 4 quý liên tiếp `{t-3..t}` sẽ trở về `already_distressed_at_t = False`!
4. **Sửa `_label_exact_filing_basis` (`GAP-04`):**
   * Đọc cột `_period_exact_filing` của 4 quý tương lai `{t+1..t+4}` (chỉ fallback sang `_exact_filing_basis` nếu frame test cũ không có `_period_exact_filing`).
5. **Mode B `REGULATORY_DISTRESS` — Hợp đồng Thư viện Ngoại tuyến An toàn PIT (`BLK-17`):**
   * **Phạm vi tường minh:** Mode B trong Phase V2 là **offline library contract** đã kiểm thử, nhận bảng `regulatory_events` từ ngoài (`{"issuer_id": pl.String, "event_date": pl.Date, "event_type": pl.String}`, tập hợp lệ `{"CONTROL", "TRADING_RESTRICTION", "SUSPENSION", "FORCED_DELISTING", "ADVERSE_OR_DISCLAIMER_AUDIT"}`); KHÔNG xây trình cào web HOSE/HNX trong V2.
   * Nếu `mode == "REGULATORY_DISTRESS"` và `regulatory_events is None` (hoặc thiếu cột / sai `event_type`): ném `ContractViolationError`!
   * **4 Bất biến chống rò rỉ & chống thiên lệch sống sót:**
     1. Lọc `regulatory_events` theo `event_date <= as_of` khi `as_of is not None`.
     2. **Tiền tổng hợp theo `(issuer_id, statement_scope, _quarter_index)`** (`earliest_future_event_date = min(event_date)` trong `(a_t, a_t + 365d]`; `had_prior_reg_event = any(event_date trong [a_t - 365d, a_t])`) $\implies$ join 1:1 không bao giờ nhân đôi dòng khi 1 công ty có nhiều án kỷ luật trong năm!
     3. **Chống thiên lệch sống sót khi hủy niêm yết bắt buộc:** Nếu có `earliest_future_event_date` trong `(a_t, a_t + 365d]`, lập tức gắn **`distress_target = 1`** với `label_available_at = min(mode_a_avail, earliest_future_event_date)` (hoặc `earliest_future_event_date` nếu Mode A thiếu quý hậu-delisting) — **KHÔNG yêu cầu `quarters_present` của 4 quý sau hủy niêm yết**!
     4. **Chống rò rỉ Right-Censoring trên nhãn âm (`distress_target = 0`):** Chỉ gắn `0` khi đồng thời thỏa Mode A `negative_verified`, KHÔNG có sự kiện pháp lý trong `(a_t, a_t + 365d]`, VÀ `(a_t + 365d <= as_of)` (nếu `as_of is not None`); đặt **`label_available_at = max(mode_a_label_available_at, a_t + 365d)`**!

### 3.2. `src/finintel/ml/unsupervised.py` (`GAP-06`, `MED-04`)
1. **`CrossSectionalPreprocessor` có `clip_range` cấu hình được (`MED-04`):**
   * Chữ ký: `def __init__(self, features: tuple[str, ...], *, clip_range: tuple[float, float] | None = None) -> None:`.
   * Giữ mặc định `clip_range=None` để không bẻ gãy đuôi phân bố của XGBoost có giám sát hay các unit test hiện hữu.
   * Trong `FinancialArchetypes`: khởi tạo **`CrossSectionalPreprocessor(UNSUPERVISED_FEATURE_SET, clip_range=(-5.0, 5.0))`** để cắt ngưỡng mọi giá trị sau `RobustScaler` vào `[-5.0, +5.0]`, ngăn 3 biến đuôi dày nuốt phương sai PCA/K-Means/Isolation Forest!
2. **Đặt tên Cụm K-Means Duy nhất 100% (`MED-04`):**
   * Tính `relative_centers = centers - centers.mean(axis=0, keepdims=True)`.
   * Sắp xếp chỉ số đặc trưng tất định bằng `np.argsort(-np.abs(rel), kind="mergesort")`.
   * Xử lý **chung 1 vòng khử trùng tên cho CẢ cụm trung tính** (`max(|rel|) < 0.35`, nhãn gốc `'Nhóm vận hành cân bằng / trung bình ngành'`) lẫn cụm đặc trưng: nếu tên đã tồn tại, bổ sung đặc trưng nổi bật kế tiếp hoặc hậu tố `f" (Nhóm {cluster_idx + 1})"`, bảo đảm `len(set(archetypes)) == len(archetypes)` trong mọi kịch bản!

### 3.3. `temporal_split.py`, `train.py` & `explain.py` — PIT-Symmetric Peer Z-Scores & `MarginPlattCalibrator` (`BLK-16`, `HI-14`)
1. **Đặc trưng Chuẩn hóa Ngành Bảo toàn Tuyệt đối Lịch sử PIT (`BLK-16` — CẤM coi ICB hiện tại là chân lý lịch sử!):**
   * **GIỮ NGUYÊN 100% `intermediates.py:568-583` (`industry_snapshot_date <= as_of`).** Tuyệt đối không chép ngược ICB 2025/2026 về năm 2018–2023.
   * **Tính toán nghiêm ngặt theo từng mốc `cutoff` của fold (`temporal_split.py`):** Trong `_materialize_at(cutoff)`, tính `peer_benchmarks` trực tiếp từ lát cắt `scoped` tại đúng `cutoff` (TUYỆT ĐỐI KHÔNG join `peer_benchmarks` của `as_of` cuối cùng vào `train`/`validation`!).
   * **Chính sách Đối xứng Cohort PIT (`peer_standardization_mode`):**
     - Kiểm tra tỷ lệ dòng có `icb_code_level2` hoặc `icb_code_level3` khả kiến tại mốc `cutoff` trên `train` và `validation`.
     - Nếu độ phủ ICB khả kiến PIT trên cả `train` và `validation` đạt **$\ge 80\%$** (khi có dữ liệu ICB lịch sử/SCD-2 thực sự): dùng chuẩn hóa phân cấp `ICB_LEVEL_3 -> ICB_LEVEL_2 -> REGIME_FALLBACK` trên tất cả fold & scoring (`peer_standardization_mode = "PIT_ICB"`).
     - Nếu độ phủ ICB khả kiến PIT trên `train` hoặc `validation` **$< 80\%$** (hiện trạng khi chỉ có snapshot ICB 2025/2026): tính 4 biến `sector_z_*` **đối xứng theo `REGIME_FALLBACK`** (trung vị & IQR mặt cắt ngang cùng kỳ tài chính của khối `CORPORATE`) trên **TẤT CẢ** `train`, `validation`, `test` và `scoring` (`peer_standardization_mode = "PIT_REGIME_SYMMETRIC"`), ghi nhận minh bạch vào `_ml_run_manifest.json` & `model_evaluation_report.json`!
     - *Lợi ích kép:* 0% rò rỉ ICB tương lai, 0% lệch phân bố train/serve, vẫn triệt tiêu hoàn toàn hiện tượng phạt nhầm `CCC < 0` (`GMD`), và tự động nâng cấp lên `PIT_ICB` khi bổ sung lịch sử ICB!
   * **Bảo toàn `BASE_FEATURES` & Khai báo đầy đủ 4 biến `SECTOR_ADJUSTED_FEATURES` (`BLK-16`):**
     - **GIỮ NGUYÊN** `BASE_FEATURES` (20 biến) để 100% test nền (`test_layer4_ml_supervised.py:150,191,384`) xanh nguyên vẹn!
     - Đăng ký đầy đủ ràng buộc đơn điệu trong `train.py`:
       * Thêm vào `POSITIVE` (`+1`): `"sector_z_cash_conversion_cycle"`, `"sector_z_debt_to_equity"`.
       * Thêm vào `NEGATIVE` (`-1`): `"sector_z_asset_turnover"`, `"sector_z_ebit_margin"`.
     - Định nghĩa tuple mới **`SECTOR_ADJUSTED_FEATURES`** thay thế đồng bộ 4 biến thô (`cash_conversion_cycle`, `asset_turnover`, `debt_to_equity`, `ebit_margin`) bằng 4 biến `sector_z_*` tương ứng.
2. **`MarginPlattCalibrator` & `min_unique_positive_issuers` (`src/finintel/ml/supervised/train.py`, `HI-14`):**
   * Bổ sung `min_unique_positive_issuers: int = 3` vào `train_distress_model()` (kiểm tra trên `train` khi có cột `issuer_id`; ném `InsufficientModelData` nếu `< min_unique_positive_issuers`).
   * **Xóa bỏ hoàn toàn `CalibratedClassifierCV(FrozenEstimator(classifier), ...)`.**
   * Xây dựng `MarginPlattCalibrator` fit trên `raw_margin = classifier.predict(X, output_margin=True)`:
     - Out-of-Fold CV trên `validation`: dùng `StratifiedGroupKFold(n_splits=k)` theo `issuer_id` chỉ khi `n_pos_groups >= 2` và `n_neg_groups >= 2` (`k = min(5, n_pos_groups, n_neg_groups)`); ngược lại fallback `StratifiedKFold(n_splits=min(5, min_valid_class))`. Bảo vệ fold đơn lớp (`np.unique(y_tr).size < 2` $\rightarrow$ dùng base rate của fold).
     - Trên mỗi fold và bản fit cuối trên toàn bộ `validation`: fit 1D `LogisticRegression(C=1.0, solver="lbfgs")` lên `raw_margin[:, None]`.
     - **Fallback đơn điệu hiệu chuẩn đúng `scale_pos_weight` (`HI-14`):** Nếu hệ số góc $w \le 0$ hoặc không hữu hạn, cố định hệ số góc $a = 1.0$ và giải chính xác bài toán MLE 1 tham số tìm intercept $b^*$ tối thiểu hóa binary cross-entropy của $\sigma(\text{raw\_margin} + b)$ trên `y_val` (khử sai lệch `scale_pos_weight` đồng thời bảo toàn tuyệt đối tính đơn điệu theo `raw_margin`!).
     - Lưu `platt_slope: float` và `platt_intercept: float` vào `TrainingResult` và `model_evaluation_report.json`.
     - Chọn `_optimal_f2_threshold(y_val[common], oof_xgb_prob[common])` trên xác suất **Out-of-Fold**, rồi fit bản `MarginPlattCalibrator` cuối trên toàn bộ `validation`.
3. **`src/finintel/ml/supervised/explain.py` (`HI-14`):**
   * **Giữ nguyên** ngưỡng cộng tính chặt **`rtol=1e-6, atol=1e-6`** (`L75`, không nới ra `1e-4`!).
   * **Giữ nguyên** khóa `"feature"` (không thay chuỗi tên cột để không vỡ test hiện hữu), bổ sung khóa additive `"display_name_vn"` (ánh xạ nhãn tiếng Việt gồm cả 4 biến `sector_z_*`), gán `peer_median = 0.0` cho các biến `sector_z_*`.
   * **Bổ sung `other_features_delta: float`** vào struct JSON của `shap_top_drivers_json` (= `prediction_score - base_value - sum(top_risk_increasing) - sum(top_risk_decreasing)`), bảo đảm biểu đồ Waterfall 10 nhân tố ở Wave 4 cộng khít `prediction_score` trong `1e-6`!

### 3.4. `src/finintel/ml/tables.py` & `src/finintel/pipeline/analytics.py` (`GAP-02`, `BLK-05`, `MED-04`, `MISS-07`)
* **`tables.py`:**
  * Bổ sung tham số tùy chọn backward-compatible:
    - `scored_taxonomies: tuple[str, ...] = ("VAS_TT200_2014", "VAS_TT99_2025")`
    - `feature_set: Literal["sector_adjusted", "base"] = "sector_adjusted"`
    - `target_mode: str = "FUNDAMENTAL_DISTRESS"`
    - `regulatory_events: pl.DataFrame | None = None`
    - `min_unique_positive_issuers: int = 3`
  * **Quy tắc chấm điểm an toàn (`MISS-07`, `MED-04`):**
    - `classical_as_of` (Altman $Z''$, Piotroski $F$, Beneish $M$) tiếp tục tính cho **cả `QUARTER` và `ANNUAL`** trên `CORPORATE`.
    - `eligible` cho **Unsupervised (PCA/K-Means/IForest) và Supervised (XGBoost + TreeSHAP)** lọc chặt:
      `(statement_scope == statement_scope) & (period_type == "QUARTER") & taxonomy_version.is_in(scored_taxonomies)` (kèm `_exact_filing_basis` khi `require_exact_filing=True`).
    - *Kết quả:* Phủ 100% các quý `2026-Q1..Q3` (`VAS_TT99_2025`) mà không trộn dòng `ANNUAL` vào mô hình hiệu chuẩn theo `QUARTER`!
  * Ghi đầy đủ vào `_ml_run_manifest.json` & `model_evaluation_report.json`: `scored_taxonomies`, `scored_period_types` (`["QUARTER"]`), `feature_set`, `peer_standardization_mode`, `target_mode`, `platt_slope`, `platt_intercept`, `f2_thresholds`, và đầy đủ số liệu `validation` (kèm OOF summary) + `test`.
* **`src/finintel/pipeline/analytics.py` (`BLK-05`):**
  * Giữ nguyên tương thích ngược với cấu hình `ml:` hiện hữu (`ml.taxonomy_version` cho split huấn luyện), bổ sung hỗ trợ đọc tùy chọn `ml.scored_taxonomies`, `ml.feature_set`, `ml.target_mode`, `ml.regulatory_events_path` (có kiểm tra path-confinement) và chuyển tiếp sạch vào `build_ml_artifacts()`.

---

## 4. KIỂM THỬ TDD & TIÊU CHÍ NGHIỆM THU (`DEFINITION OF DONE`)

1. **`tests/unit/test_layer4_ml.py`:**
   * Test `CrossSectionalPreprocessor(..., clip_range=(-5.0, 5.0))` cắt vào `[-5.0, +5.0]` trong khi mặc định `clip_range=None` bảo toàn đuôi phân bố.
   * Test `FinancialArchetypes`: `len(set(names)) == len(names)` kể cả khi $\ge 2$ cụm rơi vào vùng trung tính (`max(|rel|) < 0.35`), và chống ngoại lai `CCC = 5000` / `interest_coverage = 200`.
   * Test `build_ml_artifacts()`: chấm điểm thành công dòng `QUARTER` của cả `VAS_TT200_2014` và `VAS_TT99_2025` (`2026-Q1..Q3`), giữ `ANNUAL` chỉ có điểm kinh điển (không trộn ML `QUARTER`), ghi đầy đủ `peer_standardization_mode`, `platt_slope`, `scored_taxonomies` vào manifest.
2. **`tests/unit/test_layer4_ml_supervised.py`:**
   * Toàn bộ 5 bài test nền của `target.py` (không truyền `total_debt`, có doanh nghiệp `NO_INTEREST_EXPENSE` tại `L257`) và các bài test `BASE_FEATURES` (`L150, L191, L384`) **xanh 100%**.
   * **Test `GAP-03` & `BLK-18`:** Mô phỏng chuỗi `POM` (`CFO > 0`, `EBIT < 0`, `CR = 0.35`, `ROA = -13%`) xác nhận `already_distressed_at_t` duy trì `True` liên tục; đồng thời xác nhận công ty có 2 quý `liquidity_bad` ở `t-6, t-5` rồi khỏe mạnh 5 quý `t-4..t` có **`already_distressed_at_t == False`** (chứng minh hết lỗi 7 quý!); Event 3 kích hoạt ở `t+1..t+3`.
   * **Test `GAP-04`:** Năm `2017` là `STATUTORY_LAG`, `2018..2020` là `EXACT_FILING` $\rightarrow$ giữ `2018-Q4..2019-Q3` với `_label_exact_filing_basis == True`.
   * **Test `BLK-16` PIT-Symmetric Peer Z:** Khi `industry_snapshot_date = 2025-01-01 > test_start (2023-01-01)`, xác nhận `peer_standardization_mode == "PIT_REGIME_SYMMETRIC"` trên cả `train`/`val`/`test`/`scoring` (không có ICB leakage và không có covariate shift), và cả 4 biến `sector_z_*` qua `_constraints()` hợp lệ.
   * **Test `GAP-05` & `HI-14` (`MarginPlattCalibrator`):** Xác nhận fit trên `raw_margin`, `0 < platt_slope <= 10.0`, xử lý sạch `n_pos_groups == 1`, kích hoạt MLE intercept-only fallback đơn điệu khi dữ liệu suy biến tạo $w \le 0$, và fail-fast khi số issuer dương tính `< min_unique_positive_issuers`.
   * **Test `BLK-17` Mode B Offline Contract:** (a) `regulatory_events=None` ném `ContractViolationError`; (b) sự kiện `FORCED_DELISTING` tại `a_t + 180d` (không còn báo cáo `t+3`/`t+4`) vẫn sinh `distress_target == 1`; (c) nhiều sự kiện trong 1 năm không nhân đôi dòng; (d) nhãn âm `y=0` bị loại nếu `a_t + 365d > as_of` và có `label_available_at >= a_t + 365d`.
   * **Test `HI-14` TreeSHAP Waterfall Additivity:** Xác nhận `|base_value + sum(top_inc) + sum(top_dec) + other_features_delta - prediction_score| < 1e-6` và giữ nguyên khóa `"feature"` + thêm `"display_name_vn"`.
3. **Cổng chất lượng bắt buộc:** `ruff format --check`, `ruff check`, `mypy --strict`, `pytest -q` xanh 100%.

---

## 5. PROMPT GIAO VIỆC MẪU CHO CODING AGENT (COPY-PASTE READY)

```text
Đọc AGENT.md, docs/v2_specs/00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md và docs/v2_specs/06_STREAM_A4_LAYER4B_ML_REMEDIATION_SECTOR_ADJ_AND_REGULATORY_TARGET.md.

Thực hiện ĐÚNG phạm vi Wave 2B (Stream A4) trên git worktree riêng v2/a4-layer4b (tách từ v2/integration sau khi 05_STREAM_A3 đã merge):
1. Chỉ chỉnh sửa các tệp trong mục "Owned Files" của 06_STREAM_A4 (gồm src/finintel/pipeline/analytics.py; CẤM chạm vào src/finintel/analytics/, storage/, dashboard/, data/live_fhsc_universe/).
2. Đối chiếu Shumway (2001), Falkenstein et al. (2000), Platt (1999), Niculescu-Mizil & Caruana (2005), Lundberg et al. (2020), XGBoost/scikit-learn Docs; ghi Verified Sources trong docstring.
3. Bảo đảm 6 điểm sống còn: (a) KHÔNG coi ICB hiện tại 2025/2026 là chân lý lịch sử — giữ industry_snapshot_date <= as_of và áp PIT Cohort Symmetry (PIT_REGIME_SYMMETRIC khi độ phủ PIT ICB < 80%); (b) giữ nguyên BASE_FEATURES & tạo riêng SECTOR_ADJUSTED_FEATURES đăng ký đủ 4 biến sector_z_*; (c) giữ total_debt tùy chọn, tên tham số mode, cửa sổ persistence đúng 4 quý (không 7 quý!) và Mode B offline contract chống right-censoring & delisting survivorship; (d) MarginPlattCalibrator OOF trên raw_margin có guard n_pos_groups<2 & MLE intercept fallback; (e) explain_distress thêm other_features_delta giữ atol=1e-6; (f) giữ chấm điểm ML chặt trên QUARTER cho {"VAS_TT200_2014", "VAS_TT99_2025"}.
4. Chạy xanh 100%: ruff format --check, ruff check, mypy --strict, pytest -q.
```
