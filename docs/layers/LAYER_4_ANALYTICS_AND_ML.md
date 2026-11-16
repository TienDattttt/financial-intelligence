# ĐẶC TẢ XÂY DỰNG TẦNG 4: FINANCIAL ANALYTICS, RED FLAGS & GATED MACHINE LEARNING

* **Mã tài liệu:** `LAYER-4`
* **Module đích:** `src/finintel/analytics/` (Giai đoạn 4A - MVP) và `src/finintel/ml/` (Giai đoạn 4B - Chỉ mở khóa sau MVP)
* **Phạm vi áp dụng:** Doanh nghiệp phi tài chính (`accounting_regime == 'CORPORATE'`).

---

## 1. BÀI TOÁN & NGUYÊN TẮC TRIỂN KHAI THEO CỔNG (GATED PHASES)

Tầng 4 biến dữ liệu kế toán chuẩn hóa thành các chỉ số đánh giá sức khỏe doanh nghiệp, xếp hạng tương quan ngành và tín hiệu cảnh báo rủi ro. Để tránh tạo ra các kết quả thống kê vô nghĩa:
* **Giai đoạn 4A (Bắt buộc hoàn thành trong MVP):** Xây dựng bộ công thức tỷ số tài chính tường minh theo chuẩn VAS, phân tích DuPont 5 bước, so sánh phân vị ngành (`Industry Peer Benchmarking`) và hệ thống Cảnh báo Sớm dựa trên Quy tắc Kế toán (`Accounting Red Flags`).
* **Giai đoạn 4B (Bị khóa trong MVP - Chỉ triển khai ở Phase 2):** Các mô hình điểm số học thuật (`Altman Z''`, `Piotroski F`, `Beneish M`), phân tích không giám sát (`PCA`, `Clustering`, `IsolationForest`) và mô hình dự báo có giám sát (`XGBoost + SHAP`) **chỉ được phép viết code sau khi Giai đoạn 4A đã nghiệm thu và thỏa mãn điều kiện dữ liệu đầu vào**.

---

## 2. NGUỒN HỌC ĐÃ KIỂM CHỨNG & CÁCH TÁI SỬ DỤNG

1. **`JerBouma/FinanceToolkit` (`ratios/` & `models/dupont.py`):**
   * *Cách áp dụng:* Học cách thiết kế hàm tính tỷ số vector hóa và mô hình phân rã DuPont 5 thành phần.
2. **Tài liệu Học thuật Gốc (Dành cho Giai đoạn 4B):**
   * **Altman (1995, 2000):** Mô hình **Altman $Z''$-Score** (4 biến, loại bỏ vòng quay tài sản để áp dụng cho thị trường mới nổi và doanh nghiệp phi sản xuất).
   * **Piotroski (2000):** Mô hình **Piotroski $F$-Score** (9 tiêu chí nhị phân đo lường khả năng sinh lời, đòn bẩy/thanh khoản và hiệu quả hoạt động).
   * **Beneish (1999):** Mô hình **Beneish $M$-Score** (8 chỉ số phát hiện khả năng thao túng lợi nhuận).
   * **Alali & Romero (2013) / Amiram et al. (2015):** Căn cứ giải thích lý do **không áp dụng Luật Benford** lên từng BCTC đơn lẻ (số lượng khoản mục trên 1 báo cáo quá ít dẫn đến phương sai lớn và tỷ lệ báo động giả cao).

---

## 3. ĐẶC TẢ CÔNG THỨC TƯỜNG MINH CHO GIAI ĐOẠN 4A (MVP)

Agent **CẤM tự suy đoán công thức**. Mọi chỉ số trong `src/finintel/analytics/ratios.py` phải được cài đặt chính xác theo các định nghĩa dưới đây.

### 3.1. Định nghĩa Các Đại lượng Trung gian theo Chuẩn Kế toán Việt Nam (VAS)
Với mọi chỉ tiêu `FLOW` (KQKD, LCTT) khi ghép với chỉ tiêu `STOCK` (CĐKT), bắt buộc dùng giá trị **trượt 4 quý (`TTM`)** cho chỉ tiêu `FLOW` và **trung bình cộng đầu kỳ – cuối kỳ cùng khoảng thời gian 12 tháng** ($\overline{\text{Stock}}_t = \frac{\text{Stock}_t + \text{Stock}_{t-4}}{2}$) cho chỉ tiêu `STOCK`:

1. **Lợi nhuận trước thuế và lãi vay (`EBIT`):**
   $$\text{EBIT} = \text{IS\_PROFIT\_BEFORE\_TAX (Mã 50)} + \left|\text{IS\_INTEREST\_EXPENSE (Mã 23)}\right|$$
2. **Lợi nhuận trước thuế, lãi vay và khấu hao (`EBITDA`):**
   * Nếu doanh nghiệp lập LCTT gián tiếp (có `CF_DEPRECIATION_AMORTIZATION` khác `NULL`):
     $$\text{EBITDA} = \text{EBIT} + \left|\text{CF\_DEPRECIATION\_AMORTIZATION (Mã 02 LCTT)}\right|$$
   * Nếu doanh nghiệp lập LCTT trực tiếp (`CF_DEPRECIATION_AMORTIZATION` là `NULL`):
     `EBITDA` phải trả về `NULL` kèm cờ `computation_note = 'MISSING_DEPRECIATION_DIRECT_CF'` (Cấm tự gán khấu hao bằng $0$ làm `EBITDA` bị đánh đồng với `EBIT`!).
3. **Thuế suất hiệu dụng (`Effective Tax Rate - ETR`) & `NOPAT`:**
   * Nếu $\text{IS\_PROFIT\_BEFORE\_TAX} > 0$:
     $$\text{ETR} = \text{clip}\left(\frac{\text{IS\_TOTAL\_INCOME\_TAX\_EXPENSE (Mã 51 + 52)}}{\text{IS\_PROFIT\_BEFORE\_TAX}}, 0.0, 1.0\right)$$
   * Nếu $\text{IS\_PROFIT\_BEFORE\_TAX} \le 0$: Đặt $\text{ETR} = 0.20$ (thuế suất TNDN phổ thông tại Việt Nam).
   * Lợi nhuận hoạt động ròng sau thuế:
     $$\text{NOPAT} = \text{EBIT} \times (1 - \text{ETR})$$
4. **Tổng Nợ vay Chịu lãi (`Total Debt`) & Vốn Đầu tư (`Invested Capital - IC`):**
   $$\text{Total Debt} = \text{BS\_SHORT\_TERM\_BORROWINGS (Mã 320)} + \text{BS\_LONG\_TERM\_BORROWINGS (Mã 338)}$$
   $$\text{Invested Capital (IC)} = \text{Total Debt} + \text{BS\_TOTAL\_EQUITY (Mã 400)} - \text{BS\_CASH\_AND\_EQUIVALENTS (Mã 110)}$$

### 3.2. Quy tắc Xử lý Biên cho Mẫu số và Cờ Trạng thái (`status_flag`)
Mỗi tỷ số tính ra phải đi kèm cột cờ trạng thái `<ratio_name>_flag` theo đúng 3 quy tắc bắt buộc sau:

1. **Quy tắc Kiểm tra Đổi dấu Vốn chủ sở hữu (Áp dụng cho `ROE`, `Debt-to-Equity`, `Equity Multiplier`):**
   * Kiểm tra đồng thời Vốn chủ sở hữu đầu kỳ ($E_{t-4}$) **VÀ** Vốn chủ sở hữu cuối kỳ ($E_t$).
   * Nếu $E_{t-4} \le 0$ **HOẶC** $E_t \le 0$: Giá trị tỷ số bắt buộc gán bằng `NULL` và cờ trạng thái gán bằng `'NEGATIVE_OR_ZERO_EQUITY'` (ngăn chặn trường hợp một kỳ âm lớn, một kỳ dương lớn làm trung bình cộng vẫn dương hoặc phép chia hai số âm ra số dương).
2. **Quy tắc Kiểm tra Vốn đầu tư hoặc Tổng tài sản $\le 0$ (Áp dụng cho `ROIC`, `ROA`):**
   * Nếu $\text{IC}_{t-4} \le 0$ hoặc $\text{IC}_t \le 0$ $\rightarrow$ `ROIC = NULL`, cờ `'NON_POSITIVE_INVESTED_CAPITAL'`.
3. **Quy tắc Tách biệt Không có Chi phí Lãi vay (`Interest Coverage Ratio`):**
   * $\text{Interest Coverage} = \frac{\text{EBIT}_{TTM}}{\left|\text{IS\_INTEREST\_EXPENSE}_{TTM}\right|}$.
   * Nếu doanh nghiệp không vay nợ và $\text{IS\_INTEREST\_EXPENSE}_{TTM} == 0$: Trả về `NULL` kèm cờ `'NO_INTEREST_EXPENSE'` (đây là tín hiệu tài chính lành mạnh, **cấm gộp chung** vào cờ lỗi `'ZERO_DENOMINATOR'`).

### 3.3. Phân rã DuPont 5 Thành phần (`dupont.py`)
Chỉ tính khi cả $E_{t-4} > 0$, $E_t > 0$, $\text{IS\_PROFIT\_BEFORE\_TAX}_{TTM} \neq 0$, $\text{EBIT}_{TTM} \neq 0$ và $\text{IS\_NET\_REVENUE}_{TTM} > 0$:
$$\text{ROE} = \underbrace{\frac{\text{Net Income}}{\text{EBT}}}_{\text{Tax Burden}} \times \underbrace{\frac{\text{EBT}}{\text{EBIT}}}_{\text{Interest Burden}} \times \underbrace{\frac{\text{EBIT}}{\text{Net Revenue}}}_{\text{Operating Margin}} \times \underbrace{\frac{\text{Net Revenue}}{\overline{\text{Total Assets}}}}_{\text{Asset Turnover}} \times \underbrace{\frac{\overline{\text{Total Assets}}}{\overline{\text{Total Equity}}}}_{\text{Equity Multiplier}}$$

### 3.4. So sánh Ngành (`comparisons.py`) & Cảnh báo Kế toán (`red_flags.py`)
1. **Industry Peer Benchmarking (`comparisons.py`):**
   * Gom nhóm theo `(period_end_date, icb_code_level2)` (chỉ tính khi nhóm ngành có tối thiểu $N \ge 5$ doanh nghiệp có dữ liệu hợp lệ trong kỳ).
   * Tính Trung vị ngành ($\text{Median}_g$), Khoảng tứ phân vị ($\text{IQR}_g = P_{75} - P_{25}$), Thứ hạng phân vị (`Percentile Rank` từ $0$ đến $100$) và Điểm chuẩn hóa bền vững:
     $$\text{Robust Z-Score} = \frac{X_{i, g, t} - \text{Median}_g(X_t)}{\max(\text{IQR}_g(X_t) \times 0.7413, \epsilon)}$$
2. **Rule-based Accounting Warnings (`red_flags.py`):**
   Xuất bảng `accounting_red_flags.parquet` với mã cảnh báo, mức độ và câu giải thích kèm số liệu cụ thể:
   * `RF_ACCRUAL_DIVERGENCE`: Lợi nhuận sau thuế (`Net Income TTM`) $> 0$ nhưng Dòng tiền hoạt động kinh doanh (`CFO TTM`) $< 0$ trong $\ge 2$ quý liên tiếp.
   * `RF_RECEIVABLES_OUTPACE_REVENUE`: Tốc độ tăng trưởng Khoản phải thu ngắn hạn (`YoY`) $> 1.5 \times$ tốc độ tăng trưởng Doanh thu thuần (`YoY`) và Doanh thu tăng dương.
   * `RF_INVENTORY_BLOAT`: Tốc độ tăng trưởng Hàng tồn kho (`YoY`) $> 1.5 \times$ tốc độ tăng trưởng Giá vốn hàng bán (`YoY`) kèm Vòng quay hàng tồn kho giảm.
   * `RF_DEBT_SERVICE_STRESS`: Hệ số thanh toán lãi vay (`Interest Coverage`) $< 1.0$ hoặc $\text{CFO}_{TTM} < 0$ đi kèm $\frac{\text{Total Debt}}{\text{Total Equity}} > 2.0$.
   * `RF_AUDIT_QUALIFICATION_OR_LATE`: Báo cáo có `audit_opinion` $\in \{\text{'EXCEPT\_FOR'}, \text{'ADVERSE'}, \text{'DISCLAIMER'}\}$ hoặc `is_filed_late == 1`.

---

## 4. CỔNG KIỂM SOÁT GIAI ĐOẠN 4B — RESEARCH & MACHINE LEARNING (PHASE 2)

Khi và chỉ khi người dùng yêu cầu mở khóa Phase 2, Agent phải tuân thủ các ràng buộc sau cho `src/finintel/ml/`:
1. **Altman $Z''$-Score (1995, 2000), Piotroski $F$-Score (2000), Beneish $M$-Score (1999):**
   * Điểm số tính ra chỉ được gắn nhãn là **`screening_indicator` (Chỉ báo sàng lọc tham khảo)**. Không được tuyên bố các ngưỡng cắt của thị trường Mỹ ($Z'' < 1.10$ hay $M > -1.78$) là xác suất phá sản hay kết luận gian lận tại Việt Nam nếu chưa có tập kiểm định nội địa.
   * Với `Beneish M-Score`: Nếu doanh nghiệp lập LCTT theo phương pháp trực tiếp làm khuyết chỉ tiêu Khấu hao (`CF_DEPRECIATION_AMORTIZATION IS NULL`), hàm tính `Beneish M-Score`8 biến phải trả về `NULL` kèm cờ `'MISSING_DEPRECIATION_DIRECT_CF'`.
2. **Sáu Ràng buộc Chống Rò rỉ Dữ liệu (6 Mandatory ML Guardrails) cho XGBoost:**
   * **G1 (Strict Point-in-Time):** Chỉ huấn luyện khi các bản ghi có `published_at_basis == 'EXACT_FILING'` và $\text{published\_at} \le \text{prediction\_date}$.
   * **G2 (Approved Target):** Biến mục tiêu $Y$ trong `target.py` phải có cửa sổ quan sát cố định (ví dụ 4 quý tới) và được con người phê duyệt trước khi chạy `train.py`.
   * **G3 (Purged Temporal Split):** Chia tập `Train / Validation / Test` theo trục thời gian công bố, có khoảng đệm (`purge_gap` $\ge$ độ dài cửa sổ nhãn) giữa tập Train và Test.
   * **G4 (Isolated Preprocessing):** Mọi bộ `Winsorizer (1%/99%)`, `MedianImputer`, `RobustScaler`, `PCA` chỉ được `.fit()` trên tập `Train`, cấm `.fit()` trên toàn bộ dữ liệu trước khi chia.
   * **G5 (Baseline Benchmark):** Phải báo cáo song song thước đo `PR-AUC`, `F2-Score` và `Recall@Top-10%` của `XGBoost` so với mô hình Baseline đơn giản (`LogisticRegression` + `Altman Z''`) theo từng năm.
   * **G6 (Probability Calibration):** Chỉ hiển thị điểm số dưới dạng xác suất (`%`) nếu đã bọc qua `CalibratedClassifierCV` và kiểm tra `Brier score`.

---

## 5. TIÊU CHÍ NGHIỆM THU TẦNG 4A (DEFINITION OF DONE)

Agent phải viết và chạy xanh `tests/unit/test_layer4_analytics.py`:
1. **Test Both-Losses Sign Trap on ROE:** Khi `Net Income = -50 tỷ` và `Ending Total Equity = -200 tỷ` $\rightarrow$ `ROE` phải trả về `NULL` và `roe_flag == 'NEGATIVE_OR_ZERO_EQUITY'` (tuyệt đối không trả về $+25\%$).
2. **Test Equity Sign Flip Across Period:** Khi `Beginning Equity = -10 tỷ` và `Ending Equity = +100 tỷ` (trung bình cộng bằng $+45\text{ tỷ} > 0$) $\rightarrow$ `ROE` vẫn phải trả về `NULL` kèm cờ `'NEGATIVE_OR_ZERO_EQUITY'` vì vốn chủ đầu kỳ âm.
3. **Test Zero Interest Expense:** Khi `EBIT = 100 tỷ` và `Interest Expense = 0` $\rightarrow$ `interest_coverage` trả về `NULL` và cờ `'NO_INTEREST_EXPENSE'`.
4. **Test DuPont Identity:** Với doanh nghiệp có đủ 5 thành phần dương hợp lệ, tích của 5 thành phần DuPont phải khớp với `ROE` trực tiếp trong phạm vi sai số dấu phẩy động $\le 10^{-6}$.