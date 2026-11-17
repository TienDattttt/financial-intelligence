# ĐẶC TẢ XÂY DỰNG TẦNG 4: QUANTITATIVE FINANCIAL ANALYTICS, FORENSIC RED FLAGS & GATED MACHINE LEARNING

* **Mã tài liệu:** `LAYER-4` (Phiên bản v2.1 — Deep Quant, Verification Protocol & AI Engineering Spec)
* **Module đích:** `src/finintel/analytics/` (Giai đoạn 4A) và `src/finintel/ml/` (Giai đoạn 4B)
* **Phụ thuộc đầu vào:** Bảng chuẩn hóa `curated/financial_facts.parquet`, `curated/issuers.parquet` (Tầng 2 & Tầng 3) và các trường đã xác minh từ `data/metadata/spike_report.json`.

---

## 1. TRIẾT LÝ THIẾT KẾ, PHÂN LUỒNG ĐA NGÀNH & LỘ TRÌNH THI CÔNG TỪNG BƯỚC

### 1.1. Lộ trình Thi công Từng bước (8 Sub-Milestones Tuần tự)

Tầng 4 là nơi kết tinh giá trị phân tích định lượng và AI của toàn dự án. Để Coding Agent không bị ngộp và không bao giờ sinh code rác, Tầng 4 được chia thành **8 bước thi công tuần tự (Bước 4.1 $\rightarrow$ Bước 4.8)** thuộc 2 giai đoạn có cổng kiểm định chặt chẽ:

```text
=====================================================================================
GIAI ĐOẠN 4A: CORE QUANTITATIVE ANALYTICS & FORENSIC RED FLAGS (MVP BẮT BUỘC)
=====================================================================================
[Bước 4.1] Intermediate Accounting Aggregates (EBIT, EBITDA, NOPAT, IC, Working Capital)
    |
    v
[Bước 4.2] 28 Core Financial Ratios (5 nhóm) + Hệ thống Cờ Biên Toán học (Boundary Flags)
    |
    v
[Bước 4.3] Phân rã DuPont 5 Thành phần & 6 Thành phần (Tách lợi ích cổ đông thiểu số)
    |
    v
[Bước 4.4] Industry Peer Benchmarking (Cross-sectional Median, IQR, Percentile, Robust Z)
    |
    v
[Bước 4.5] Rule-Based Forensic & Solvency Red Flags (7 quy tắc cảnh báo sớm có giải thích)
    |
    ====== [CỔNG NGHIỆM THU 4A: 100% Pytest & Reconciliation Tests Xanh] ======
    |
=====================================================================================
GIAI ĐOẠN 4B: CLASSICAL SCORING, UNSUPERVISED & SUPERVISED ML (PHASE 2)
=====================================================================================
    v
[Bước 4.6] Classical Academic Models: Altman Z'' (1995/2000), Piotroski F (2000), Beneish M (1999)
    |
    v
[Bước 4.7] Cross-Sectional Feature Engineering + PCA + K-Means Archetypes + Isolation Forest
    |
    v
[Bước 4.8] Purged Walk-Forward XGBoost (Monotone Constraints) + Calibration + TreeSHAP
```

**Quy tắc Thi công Từng bước cho Agent:**
* Agent **bắt buộc** triển khai và chạy `pytest` xanh cho từng bước một (`4.1` $\rightarrow$ `4.2` $\rightarrow$ `4.3` $\rightarrow$ `4.4` $\rightarrow$ `4.5`) trước khi chuyển sang bước tiếp theo.
* Tuyệt đối cấm viết nháp các hàm trống (`pass` hoặc `TODO`) cho Bước 4.6–4.8 khi đang làm Giai đoạn 4A.

### 1.2. Quy tắc Phân luồng theo Chế độ Kế toán (`Regime-Based Routing`)

Trên thị trường chứng khoán Việt Nam, doanh nghiệp niêm yết được chia thành 4 chế độ kế toán (`accounting_regime` trong bảng `issuers.parquet`):
1. **`CORPORATE` (Doanh nghiệp phi tài chính — Thông tư 200/2014 & Thông tư 99/2025, chiếm $\sim 88\%$ số mã trên sàn):** Có đầy đủ Tài sản ngắn hạn/dài hạn, Doanh thu thuần, Giá vốn hàng bán, Hàng tồn kho, Khoản phải thu và Nợ vay. **Chạy trọn vẹn 100% các bước từ Bước 4.1 đến Bước 4.8.**
2. **`BANK` (Ngân hàng — TT 49/2014 & TT 27/2021), `SECURITIES` (Chứng khoán — TT 210/2014), `INSURANCE` (Bảo hiểm — TT 232/2012):** Không có phân chia Tài sản ngắn hạn/dài hạn, không có Giá vốn hàng bán hay `EBITDA`, và "Nợ phải trả" của Ngân hàng là tiền gửi huy động từ khách hàng.
   * **Quy tắc xử lý trong Tầng 4:**
     * Chỉ tính **Nhóm Chỉ số Chung Toàn Sàn (`Universal Ratios`)**: `roa`, `roe_total`, `roe_parent`, `equity_to_assets`, `liabilities_to_assets`, `eps_ttm_estimated`, `net_income_parent_growth_yoy` và so sánh phân vị trong nội bộ ngành Ngân hàng / Chứng khoán / Bảo hiểm.
     * **Tuyệt đối lọc bỏ** các bản ghi có `accounting_regime != 'CORPORATE'` trước khi đưa vào tính toán các tỷ số thanh khoản ngắn hạn (`current_ratio`, `quick_ratio`, `inventory_turnover`), các điểm số `Altman Z''`, `Piotroski F`, `Beneish M` và các mô hình `PCA`, `K-Means`, `IsolationForest`, `XGBoost` để không làm nhiễm bẩn phân phối thống kê của mô hình.

---

## 2. NGUỒN HỌC ĐÃ KIỂM CHỨNG, GIAO THỨC AGENT TRA CỨU XÁC MINH & CÁCH TÁI SỬ DỤNG

### 2.1. Giao thức Bắt buộc Tra cứu & Xác minh Nguồn trước khi Viết Code (`Pre-Coding Verification Protocol`)

Trước khi bắt đầu viết code cho bất kỳ bước nào trong Tầng 4 (`Bước 4.1` $\rightarrow$ `Bước 4.8`), AI Coding Agent **BẮT BUỘC** thực hiện quy trình 3 bước xác minh độc lập sau đây:

1. **Bước 1 — Truy cập & Khảo sát Nguồn Gốc:**
   * Sử dụng công cụ tìm kiếm web / duyệt tài liệu (hoặc `python -c "import inspect; ..."` đối với các package đã cài trong `.venv`) để truy cập vào đúng URL repository, bài báo khoa học hoặc tài liệu chính thức được liệt kê trong Mục 2.2 tương ứng với module sắp viết.
   * Đối chiếu công thức toán học, cách xử lý trường hợp biên (như mẫu số bằng $0$, vốn chủ sở hữu âm, thiếu khấu hao) và chữ ký hàm (API signature) của nguồn học với đặc tả trong file này.
2. **Bước 2 — Kiểm tra Tương thích Phiên bản Thực tế trên Máy (`Runtime Signature Check`):**
   * Chạy lệnh kiểm tra chữ ký hàm thực tế của thư viện đang cài trong môi trường (ví dụ kiểm tra tham số của `polars`, `scipy.stats`, `sklearn.calibration.CalibratedClassifierCV`, `xgboost.XGBClassifier`, `shap.TreeExplainer`).
   * Nếu phát hiện bất kỳ sự khác biệt nào giữa nguồn học bên ngoài và quy định nghiệp vụ kế toán Việt Nam (VAS) trong tài liệu này: **Quy tắc nghiệp vụ kế toán Việt Nam và cấu trúc trường thực tế từ `spike_report.json` trong tài liệu này là chân lý cuối cùng để cài đặt**, nhưng Agent phải ghi chú rõ kết quả đối chiếu vào đầu file code (module docstring).
3. **Bước 3 — Ghi Bằng chứng Xác minh (`Verification Header`):**
   * Ở phần mở đầu (docstring) của mỗi file Python trong `src/finintel/analytics/` và `src/finintel/ml/`, Agent phải ghi rõ mục `Verified Sources:` liệt kê các nguồn đã tra cứu và xác nhận khớp với đặc tả.

### 2.2. Danh mục Nguồn học Đã Kiểm chứng & Hướng dẫn Tái sử dụng theo từng Module

| Bước / Module | Nguồn học chính thức cần Agent truy cập & xác minh | Nội dung cần kiểm chứng & học hỏi | Cách tái sử dụng & chuyển đổi vào dự án `finintel` |
|---|---|---|---|
| **Bước 4.1 & 4.2**<br>`intermediates.py`<br>`ratios.py` | **1. `JerBouma/FinanceToolkit`**<br>- URL: `https://github.com/JerBouma/FinanceToolkit`<br>- Các file cần đọc:<br>  + `financetoolkit/ratios/profitability_model.py`<br>  + `financetoolkit/ratios/liquidity_model.py`<br>  + `financetoolkit/ratios/solvency_model.py`<br>  + `financetoolkit/ratios/efficiency_model.py`<br>**2. Thông tư 200/2014/TT-BTC & Thông tư 99/2025/TT-BTC** | - Cách tách hàm tính từng tỷ số độc lập.<br>- Quy tắc dùng giá trị bình quân đầu kỳ và cuối kỳ $\frac{S_t + S_{t-4}}{2}$ cho chỉ tiêu Bảng CĐKT khi chia với chỉ tiêu KQKD/LCTT.<br>- Công thức `ROIC`, `NOPAT`, `Free Cash Flow`, `Cash Conversion Cycle`. | - **Tái sử dụng:** Kiến trúc tách lớp công thức toán học thuần túy của `FinanceToolkit`.<br>- **Chuyển đổi & Nâng cấp:** Viết lại bằng biểu thức vector hóa của `Polars` (không dùng vòng lặp Pandas). Bổ sung hệ thống **Cờ Biên Toán học (`RatioStatusFlag`)** để chặn trường hợp Vốn chủ sở hữu âm ở đầu kỳ hoặc cuối kỳ (`NEGATIVE_OR_ZERO_EQUITY`) mà `FinanceToolkit` gốc chưa chặn. |
| **Bước 4.3**<br>`dupont.py` | **`JerBouma/FinanceToolkit`**<br>- File cần đọc: `financetoolkit/models/dupont_model.py`<br>- Docs: `https://www.jeroenbouma.com/projects/financetoolkit/docs/models#extended-dupont-analysis` | - Cấu trúc phân rã Extended DuPont 5 thành phần (`Tax Burden`, `Interest Burden`, `Operating/EBIT Margin`, `Asset Turnover`, `Equity Multiplier`).<br>- Kiểm tra đẳng thức nhân chéo 5 thành phần bằng đúng `ROE`. | - **Tái sử dụng:** Đẳng thức 5 bước chuẩn cho `roe_total`.<br>- **Mở rộng cho VAS:** Trên BCTC hợp nhất Việt Nam, mở rộng thêm thành phần thứ 6 (`parent_profit_share` và `equity_multiplier_parent`) để giải thích chính xác cả `roe_parent` (Lợi nhuận sau thuế của cổ đông công ty mẹ). |
| **Bước 4.4**<br>`comparisons.py` | **`microsoft/qlib`**<br>- URL: `https://github.com/microsoft/qlib`<br>- File cần đọc: `qlib/data/dataset/processor.py` (lớp `CSZScoreNorm`, `CSRankNorm`, `RobustZScoreNorm`) | - Phương pháp chuẩn hóa theo mặt cắt ngang (`Cross-Sectional Normalization`) trong từng kỳ báo cáo.<br>- Dùng Median và MAD/IQR thay cho Mean và Std để không bị méo bởi một vài công ty ngoại lai. | - **Tái sử dụng:** Công thức `Robust Z-Score` theo trung vị ngành và thứ hạng phân vị `Percentile Rank` trên từng mặt cắt `(period_end_date, icb_code)`.<br>- **Chuyển đổi:** Bổ sung cơ chế Fallback 3 cấp ngành (`ICB Level 3` $\rightarrow$ `ICB Level 2` $\rightarrow$ `Market Corporate`) khi số lượng công ty trong ngành nhỏ hơn ngưỡng tối thiểu. |
| **Bước 4.5**<br>`red_flags.py` | **1. Sloan (1996)** - *Do Stock Prices Fully Reflect Information in Accruals and Cash Flows About Future Earnings?* (*The Accounting Review*).<br>**2. Schilit & Perler (2018)** - *Financial Shenanigans* (4th Ed.).<br>**3. Quy chế Niêm yết & CBTT HOSE/HNX (Thông tư 96/2020/TT-BTC)**. | - Định nghĩa biến Accrual Component $\frac{\text{Net Income} - \text{CFO}}{\text{Avg Total Assets}}$ của Sloan (1996).<br>- Dấu hiệu doanh thu ảo qua sự phình to của Khoản phải thu và Hàng tồn kho.<br>- Dấu hiệu rút ruột qua Khoản phải thu khác tại Việt Nam. | - **Tái sử dụng:** Biến hóa thành 7 quy tắc kiểm toán định lượng tất định (`RF_01` $\rightarrow$ `RF_07`), mỗi quy tắc xuất kèm chuỗi JSON chứng cứ số liệu cụ thể (`evidence_json`) để giải thích minh bạch trên giao diện. |
| **Bước 4.6**<br>`classical_scores.py` | **1. `JerBouma/FinanceToolkit`** (`financetoolkit/models/altman_model.py`, `financetoolkit/models/piotroski_model.py`).<br>**2. Altman (1995, 2000)** - *Emerging Market Corporate Bonds / Predicting Financial Distress of Companies*.<br>**3. Piotroski (2000)** - *Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers* (*JAR*).<br>**4. Beneish (1999)** - *The Detection of Earnings Manipulation* (*Financial Analysts Journal*).<br>**5. Alali & Romero (2013) / Amiram et al. (2015)** (*JETA* / *JAR*). | - Trọng số chính xác của mô hình **Altman $Z''$-Score** (4 biến cho thị trường mới nổi: $6.56, 3.26, 6.72, 1.05$ và hằng số $+3.25$).<br>- 9 tín hiệu nhị phân của **Piotroski $F$-Score**.<br>- 8 chỉ số thành phần ($\text{DSRI}, \text{GMI}, \text{AQI}, \text{SGI}, \text{DEPI}, \text{SGAI}, \text{LVGI}, \text{TATA}$) và trọng số của **Beneish $M$-Score**.<br>- Lý do thống kê loại bỏ Luật Benford trên BCTC đơn lẻ. | - **Tái sử dụng:** Công thức toán học chuẩn của Altman $Z''$, Piotroski $F$, Beneish $M$ (viết trực tiếp bằng Polars/NumPy, **tuyệt đối không cài thư viện alpha `ledgerlens`**).<br>- **Chuyển đổi cho Việt Nam:** Ở tiêu chí $F_7$ của Piotroski, dùng dòng tiền thu từ phát hành cổ phiếu (`CF_EQUITY_ISSUANCE_PROCEEDS`) thay vì chỉ nhìn vốn điều lệ tăng (tránh phạt nhầm công ty trả cổ tức bằng cổ phiếu). Ở Beneish, kiểm tra sự tồn tại của Khấu hao (`CF_DEPRECIATION_AMORTIZATION`) trước khi tính. |
| **Bước 4.7**<br>`unsupervised.py` | **1. `scikit-learn` Official Docs:**<br>- `https://scikit-learn.org/stable/modules/preprocessing.html#scaling-data-with-outliers`<br>- `https://scikit-learn.org/stable/modules/decomposition.html#pca`<br>- `https://scikit-learn.org/stable/modules/outlier_detection.html#isolation-forest`<br>**2. `yzhao062/pyod`** (`https://github.com/yzhao062/pyod`) | - Cách kết hợp `RobustScaler` trước khi chạy `PCA` và `KMeans` trên dữ liệu có đuôi dày.<br>- Sự khác biệt giữa `Outlier Detection` và kết luận gian lận.<br>- Quy tắc chuẩn hóa hướng điểm bất thường (điểm càng lớn càng bất thường). | - **Tái sử dụng:** Pipeline `Winsorizer` $\rightarrow$ `Contextual Imputer` (điền $P_{95}$ cho doanh nghiệp không vay nợ có `NO_INTEREST_EXPENSE`) $\rightarrow$ `RobustScaler` $\rightarrow$ `PCA` $\rightarrow$ `KMeans` & `IsolationForest`. |
| **Bước 4.8**<br>`supervised/`<br>(`target.py`, `temporal_split.py`, `train.py`, `explain.py`) | **1. `lzanam1/bankruptcy-prediction`** (`https://github.com/lzanam1/bankruptcy-prediction`).<br>**2. `XGBoost` Official Docs (Monotonic Constraints):**<br>- `https://xgboost.readthedocs.io/en/stable/tutorials/monotonic.html`<br>**3. `SHAP` Official Docs (`TreeExplainer`):**<br>- `https://shap.readthedocs.io/en/latest/`<br>**4. `scikit-learn` Probability Calibration:**<br>- `https://scikit-learn.org/stable/modules/calibration.html`<br>**5. Bao et al. (2020)** - *Detecting Accounting Fraud in Publicly Traded U.S. Firms Using a Machine Learning Approach* (*JAR*). | - Quy trình chống Data Leakage: Chia tập dữ liệu theo thời gian có khoảng đệm (`Purge Gap`) trước khi fit bất kỳ bộ biến đổi nào.<br>- Cách truyền `monotone_constraints` vào `XGBClassifier`.<br>- Tính chất cộng tính (`additivity`) của `shap.TreeExplainer`. | - **Tái sử dụng:** Kiến trúc huấn luyện chống rò rỉ từ `lzanam1/bankruptcy-prediction` và `microsoft/qlib`, kết hợp `monotone_constraints` của `XGBoost` để đảm bảo mô hình luôn tuân thủ chiều kinh tế hợp lý và xuất giải thích cục bộ Top 5 tăng/giảm rủi ro bằng `TreeSHAP`. |

---

## 3. ÁNH XẠ CHÍNH XÁC TỪ TRƯỜNG NGUYÊN BẢN `FHSC` SANG `CANONICAL_METRIC_ID`

Dựa trên kết quả thực nghiệm đã xác minh trong `data/metadata/spike_report.json`, dưới đây là bảng ánh xạ khóa cứng giữa trường JSON nguyên bản của nguồn `fhsc` (`vnstock-oapi`), mã số Thông tư 200/Thông tư 99 và `metric_id` chuẩn dùng trong mọi công thức của Tầng 4:

### 3.1. Bảng Cân Đối Kế Toán (`BALANCE_SHEET` — Loại giá trị: `STOCK`)

| `canonical_metric_id` | Trường nguyên bản `fhsc` (`vnstock-oapi`) | Mã TT200 | Quy ước dấu chuẩn ở Tầng 2/4 |
|---|---|---|---|
| `BS_TOTAL_ASSETS` | `tongcongtaisan` | 270 | Dương ($>0$) |
| `BS_CURRENT_ASSETS` | `taisannganhan` | 100 | Dương |
| `BS_CASH_AND_EQUIVALENTS` | `tienvacackhoantuongduongtien` | 110 | Dương ($\ge 0$) |
| `BS_ST_FINANCIAL_INVESTMENTS` | `cackhoandaututaichinhnganhan` | 120 | Dương ($\ge 0$) |
| `BS_ST_RECEIVABLES_TOTAL` | `cackhoanphaithunganhan` | 130 | Dương |
| `BS_TRADE_RECEIVABLES_ST` | `phaithukhachhang` | 131 | Dương ($\ge 0$) |
| `BS_BAD_DEBT_PROVISION_ST` | `duphongphaithunganhankhodoi` | 137 | Âm trên BCTC (Dùng `abs()` khi tính tỷ lệ dự phòng) |
| `BS_INVENTORIES_NET` | `hangtonkho_tong` | 140 | Dương ($\ge 0$) |
| `BS_INVENTORIES_GROSS` | `hangtonkho` | 141 | Dương ($\ge 0$) |
| `BS_INVENTORY_PROVISION` | `duphonggiamgiahangtonkho` | 149 | Âm trên BCTC |
| `BS_NON_CURRENT_ASSETS` | `taisandaihan` | 200 | Dương |
| `BS_LT_RECEIVABLES` | `cackhoanphaithudaihan` | 210 | Tổng phải thu dài hạn theo B01-DN (TT200 và TT99) |
| `BS_FIXED_ASSETS_NET` | `taisancodinh` | 220 | Dương ($\ge 0$) |
| `BS_TANGIBLE_FIXED_ASSETS_NET` | `taisancodinhhuuhinh` | 221 | Dương ($\ge 0$) |
| `BS_TANGIBLE_FIXED_ASSETS_COST` | `nguyengia_tscdhh` | 222 | Dương ($\ge 0$) |
| `BS_TANGIBLE_ACCUM_DEPRECIATION` | `giatrihaomonluyke_tscdhh` | 223 | Âm trên BCTC |
| `BS_CIP_LONG_TERM` | `chiphixaydungcobandodang` | 242 | Dương ($\ge 0$) |
| `BS_LT_FINANCIAL_INVESTMENTS` | `daututaichinhdaihan` | 250 | Dương ($\ge 0$) |
| `BS_GOODWILL` | `loithethuongmai` | 269 | Dương ($\ge 0$) |
| `BS_TOTAL_LIABILITIES` | `nophaitra` | 300 | Dương ($\ge 0$) |
| `BS_CURRENT_LIABILITIES` | `nonganhan` | 310 | Dương ($\ge 0$) |
| `BS_TRADE_PAYABLES_ST` | `phaitranguoibannganhan` | 311 | Dương ($\ge 0$) |
| `BS_CUSTOMER_ADVANCES_ST` | `nguoimuatratientruocnganhan` | 312 | Dương ($\ge 0$) |
| `BS_ST_BORROWINGS` | `vayvanothuetaichinhnganhan` | 320 | Dương ($\ge 0$) |
| `BS_NON_CURRENT_LIABILITIES` | `nodaihan` | 330 | Dương ($\ge 0$) |
| `BS_LT_BORROWINGS` | `vayvanothuetaichinhdaihan` | 338 | Dương ($\ge 0$) |
| `BS_CONVERTIBLE_BONDS` | `traiphieuchuyendoi` | 339 | Dương ($\ge 0$) |
| `BS_TOTAL_EQUITY` | `vonchusohuu_tong` (fallback `vonchusohuu`) | 400 | Có thể Dương hoặc Âm |
| `BS_CONTRIBUTED_CAPITAL` | `vongopcuachusohuu` | 411 | Dương ($>0$) |
| `BS_ORDINARY_SHARES_CAPITAL` | `cophieuphothongcoquyenbieuquyet` | 411a | Dương ($>0$, tính bằng VND theo mệnh giá 10.000đ) |
| `BS_TREASURY_SHARES` | `cophieuquy` | 415 | Âm hoặc $0$ |
| `BS_RETAINED_EARNINGS` | `loinhuansauthuechuaphanphoi` | 421 | Có thể Dương hoặc Âm |
| `BS_MINORITY_INTEREST` | `loiichcodongkhongkiemsoat` | 429 | Có thể Dương hoặc Âm |
| `BS_TOTAL_FUNDING` | `tongcongnguonvon` | 440 | Dương ($>0$) |

### 3.2. Bảng Kết Quả Kinh Doanh (`INCOME_STATEMENT` — Loại giá trị: `FLOW`)

| `canonical_metric_id` | Trường nguyên bản `fhsc` (`vnstock-oapi`) | Mã TT200 | Quy ước xử lý dấu trong công thức |
|---|---|---|---|
| `IS_GROSS_REVENUE` | `doanhthubanhangvacungcapdichvu` | 01 | Dương |
| `IS_NET_REVENUE` | `doanhthuthuanvebanhangvacungcapdichvu` | 10 | Dương |
| `IS_COGS` | `giavonhangban` | 11 | Lấy độ lớn dương $\left|\text{IS\_COGS}\right|$ khi tính vòng quay |
| `IS_GROSS_PROFIT` | `loinhuangopvebanhangvacungcapdichvu` | 20 | Có thể Dương hoặc Âm |
| `IS_FINANCIAL_INCOME` | `doanhthuhoatdongtaichinh` | 21 | Dương |
| `IS_FINANCIAL_EXPENSE` | `chiphitaichinh` | 22 | Lấy $\left|\text{IS\_FINANCIAL\_EXPENSE}\right|$ |
| `IS_INTEREST_EXPENSE` | `trongdochiphilaivay` | 23 | TT200. Lấy độ lớn khi tính EBIT và coverage |
| `IS_BORROWING_COSTS` | `trongdochiphilaivay` | — | TT99 mã 24. FHSC giữ khóa tên cũ. Hai chế độ giữ mã chuẩn riêng |
| `IS_ASSOCIATE_PROFIT_LOSS` | `phanlailohoaclotrongcongtyliendoanhlienket` | 24 | Có thể Dương hoặc Âm |
| `IS_SELLING_EXPENSE` | `chiphibanhang` | 25 | Luôn lấy $\left|\text{IS\_SELLING\_EXPENSE}\right| \ge 0$ |
| `IS_G_AND_A_EXPENSE` | `chiphiquanlydoanhnghiep` | 26 | Luôn lấy $\left|\text{IS\_G\_AND\_A\_EXPENSE}\right| \ge 0$ |
| `IS_OPERATING_PROFIT` | `loinhuanthuantuhoatdongkinhdoanh` | 30 | Có thể Dương hoặc Âm |
| `IS_PROFIT_BEFORE_TAX` | `tongloinhuanketoantruocthue` | 50 | Có thể Dương hoặc Âm (`EBT`) |
| `IS_CURRENT_TAX_EXPENSE` | `chiphithuetndnhienhanh` | 51 | Dương là chi phí. Âm là khoản lợi ích thuế |
| `IS_DEFERRED_TAX_EXPENSE` | `chiphithuetndnhoanlai` | 52 | Giữ dấu B02-DN. Khoản âm làm giảm chi phí thuế |
| `IS_NET_INCOME_TOTAL` | `loinhuansauthuethunhapdoanhnghiep` | 60 | Lợi nhuận sau thuế toàn công ty (Gồm cả CĐTS) |
| `IS_NET_INCOME_PARENT` | `loinhuansauthuecuacongtyme` | 61 | Lợi nhuận sau thuế của cổ đông công ty mẹ |
| `IS_MINORITY_NET_INCOME` | `loiichcuacodongthieuso_bctn` | 62 | Lợi ích của cổ đông thiểu số trên KQKD |
| `IS_EPS_BASIC_REPORTED` | `laicobantrencophieu` | 70 | `VND_PER_SHARE`, `NON_ADDITIVE`. Chỉ dùng EPS công bố của báo cáo năm, không cộng theo quý hoặc trượt TTM |

### 3.3. Bảng Lưu Chuyển Tiền Tệ (`CASH_FLOW` — Loại giá trị: `FLOW`)

| `canonical_metric_id` | Trường nguyên bản `fhsc` (`vnstock-oapi`) | Mã TT200 | Quy ước xử lý dấu trong công thức |
|---|---|---|---|
| `CF_DEPRECIATION_AMORTIZATION` | `khauhaotaisancodinh` | 02 | Luôn lấy $\left|\text{CF\_DEPRECIATION\_AMORTIZATION}\right| \ge 0$ |
| `CF_OPERATING_CASH_FLOW` | `luuchuyentienthuantuhoatdongkinhdoanh` | 20 | Giữ nguyên dấu (`>0` là thu ròng, `<0` là chi ròng) |
| `CF_CAPEX_PURCHASE_FIXED_ASSETS` | `tienchidemuasamxaydungtaisancodinh` | 21 | Trên LCTT là số âm; **BẮT BUỘC lấy $\left|\text{CAPEX}\right|$** khi trừ khỏi `CFO` |
| `CF_DISPOSAL_FIXED_ASSETS` | `tienthudothanhlynhuongbantscdvacactaisandaihankhac` | 22 | Dương ($\ge 0$) |
| `CF_INVESTING_CASH_FLOW` | `luuchuyentienthuantuhoatdongdautu` | 30 | Giữ nguyên dấu |
| `CF_EQUITY_ISSUANCE_PROCEEDS` | `tienthutuphathanhcophieunhangopvoncuachusohuu` | 31 | Dương ($\ge 0$) |
| `CF_SHARE_BUYBACK_PAID` | `tienchitravongopchocshmualaicp` | 32 | Lấy $\left|\cdot\right|$ khi đo lượng tiền mua cổ phiếu quỹ |
| `CF_BORROWINGS_PROCEEDS` | `tienvaynganhandaihannhanduoc` | 33 | Dương ($\ge 0$) |
| `CF_BORROWINGS_REPAID` | `tienchitranogocvay` | 34 | Lấy $\left|\cdot\right|$ |
| `CF_DIVIDENDS_PAID` | `cotucloinhuandatrachochusohuu` | 36 | Lấy $\left|\cdot\right|$ |
| `CF_FINANCING_CASH_FLOW` | `luuchuyentienthuantusudungvaohoatdongtaichinh` | 40 | Giữ nguyên dấu |
| `CF_NET_CASH_CHANGE` | `luuchuyentienthuantrongnam` | 50 | Giữ nguyên dấu |
| `CF_ENDING_CASH` | `tienvatuongduongtiencuoinam` | 70 | Dương ($\ge 0$, phải khớp với `BS_CASH_AND_EQUIVALENTS`) |

---

## 4. ĐẶC TẢ CHI TIẾT GIAI ĐOẠN 4A (BƯỚC 4.1 ĐẾN BƯỚC 4.5)

### 4.1. Bước 4.1 — Các Đại Lượng Kế Toán Trung Gian (`src/finintel/analytics/intermediates.py`)

Trước khi tính tỷ số, hệ thống phải tính toán các đại lượng trung gian tại mỗi kỳ $t$ (với dữ liệu quý, mọi chỉ tiêu `FLOW` đều sử dụng giá trị trượt 4 quý $\text{TTM}_t = \sum_{k=0}^{3} \text{QuarterValue}_{t-k}$; với dữ liệu năm, sử dụng trực tiếp giá trị năm đó):

1. **Tổng Nợ Vay Chịu Lãi (`total_debt`):**
   $$\text{TotalDebt}_t = \text{coalesce}(\text{BS\_ST\_BORROWINGS}_t, 0) + \text{coalesce}(\text{BS\_LT\_BORROWINGS}_t, 0) + \text{coalesce}(\text{BS\_CONVERTIBLE\_BONDS}_t, 0)$$
   Chỉ tính khi có ít nhất một khoản đã công bố trong ba khoản vay ngắn hạn, vay dài hạn và trái phiếu chuyển đổi. Nếu cả ba khoản đều thiếu, kết quả là `NULL`.

2. **Tiền và Đầu Tư Tài Chính Ngắn Hạn Thanh Khoản Cao (`total_cash_and_st_investments`):**
   $$\text{CashAndSTInv}_t = \text{BS\_CASH\_AND\_EQUIVALENTS}_t + \text{coalesce}(\text{BS\_ST\_FINANCIAL\_INVESTMENTS}_t, 0)$$

3. **Nợ Vay Ròng (`net_debt`):**
   $$\text{NetDebt}_t = \text{TotalDebt}_t - \text{CashAndSTInv}_t$$

4. **Vốn Chủ Sở Hữu Của Cổ Đông Công Ty Mẹ (`parent_equity`):**
   $$\text{ParentEquity}_t = \text{BS\_TOTAL\_EQUITY}_t - \text{coalesce}(\text{BS\_MINORITY\_INTEREST}_t, 0)$$

5. **Vốn Lưu Động Ròng (`working_capital`) & Vốn Đầu Tư (`invested_capital`):**
   $$\text{WorkingCapital}_t = \text{BS\_CURRENT\_ASSETS}_t - \text{BS\_CURRENT\_LIABILITIES}_t$$
   $$\text{InvestedCapital}_t = \text{TotalDebt}_t + \text{BS\_TOTAL\_EQUITY}_t - \text{BS\_CASH\_AND\_EQUIVALENTS}_t$$

6. **Lợi Nhuận Trước Thuế và Lãi Vay (`ebit_ttm`) & `ebitda_ttm`:**
   $$\text{EBIT}_{TTM, t} = \text{IS\_PROFIT\_BEFORE\_TAX}_{TTM, t} + |B_{TTM,t}|$$
   Dùng `IS_INTEREST_EXPENSE` (mã 23) cho TT200 và `IS_BORROWING_COSTS` (mã 24) cho TT99. $B$ phải là số đã công bố cho đúng chế độ, kể cả khi bằng 0. Thiếu EBT hoặc $B$ thì EBIT bằng `NULL` kèm cờ. So sánh TTM/YoY qua chuyển đổi chế độ tuân thủ cấu hình tương đương của Layer 2.
   * **Quy tắc tính `EBITDA`:**
     * Nếu $\text{CF\_DEPRECIATION\_AMORTIZATION}_{TTM, t}$ khác `NULL`:
       $$\text{EBITDA}_{TTM, t} = \text{EBIT}_{TTM, t} + \left|\text{CF\_DEPRECIATION\_AMORTIZATION}_{TTM, t}\right|$$
     * Nếu $\text{CF\_DEPRECIATION\_AMORTIZATION}_{TTM, t}$ là `NULL` (doanh nghiệp lập LCTT theo phương pháp trực tiếp):
       $$\text{EBITDA}_{TTM, t} = \text{NULL}, \quad \text{ebitda\_flag} = \text{'MISSING\_DEPRECIATION\_DIRECT\_CF'}$$

7. **Thuế Suất Hiệu Dụng (`effective_tax_rate`) & Lợi Nhuận Hoạt Động Ròng Sau Thuế (`nopat_ttm`):**
   $$\text{TotalTax}_{TTM, t} = \text{IS\_CURRENT\_TAX\_EXPENSE}_{TTM, t} + \text{IS\_DEFERRED\_TAX\_EXPENSE}_{TTM, t}$$
   $$\text{ETR}_t = \begin{cases} \text{clip}\left(\frac{\text{TotalTax}_{TTM, t}}{\text{IS\_PROFIT\_BEFORE\_TAX}_{TTM, t}}, 0.0, 1.0\right) & \text{nếu } \text{IS\_PROFIT\_BEFORE\_TAX}_{TTM, t} > 0 \\ 0.20 & \text{nếu } \text{IS\_PROFIT\_BEFORE\_TAX}_{TTM, t} \le 0 \end{cases}$$
   $$\text{NOPAT}_{TTM, t} = \text{EBIT}_{TTM, t} \times (1 - \text{ETR}_t)$$
   Khi EBT dương, ETR cần cả hai dòng thuế. Khoản hoãn lại âm giữ nguyên dấu theo đẳng thức B02-DN `LNST = EBT − thuế hiện hành − thuế hoãn lại`. Thiếu EBT hoặc thiếu dòng thuế bắt buộc thì ETR và NOPAT bằng `NULL` kèm cờ.

8. **Dòng Tiền Tự Do (`fcf_ttm`):**
   $$\text{FCF}_{TTM, t} = \text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t} - |\text{CF\_CAPEX\_PURCHASE\_FIXED\_ASSETS}_{TTM, t}|$$
   Cần cả CFO và CAPEX đã công bố. Thiếu một trong hai thì FCF bằng `NULL` kèm cờ. Trừ độ lớn của CAPEX để xử lý khoản chi âm.

9. **Số Cổ Phiếu Phổ Thông Đang Lưu Hành Ước Tính (`estimated_shares_outstanding`):**
   * Khi vốn cổ phần phổ thông dương và `BS_TREASURY_SHARES` công bố bằng 0, có thể ước tính số cổ phiếu lưu hành cuối kỳ từ mệnh giá $10.000\text{ VNĐ/cổ phiếu}$:
     $$\text{CharterShares}_t = \frac{\text{BS\_ORDINARY\_SHARES\_CAPITAL}_t}{10000.0}$$
   * Với báo cáo năm, ưu tiên số cổ phiếu bình quân gia quyền suy ra từ lợi nhuận công ty mẹ và EPS công bố khi thương số hữu hạn và dương. Áp dụng cả khi lợi nhuận và EPS đều âm:
     $$\text{ImpliedWeightedShares}_t = \frac{\text{IS\_NET\_INCOME\_PARENT}_t}{\text{IS\_EPS\_BASIC\_REPORTED}_t}$$
   * Khi thiếu cả hai căn cứ, EPS ước tính bằng `NULL`. `eps_estimate_basis` ghi `IMPLIED_ANNUAL_WEIGHTED` hoặc `NO_TREASURY_AT_PERIOD_END`. Cách thứ hai dùng số cuối kỳ làm mẫu số xấp xỉ.

10. **Quy tắc Bình Quân CĐKT Đầu Kỳ – Cuối Kỳ (`Average Balance Sheet Stock`):**
    * Với báo cáo quý tại kỳ $t$, kỳ đầu của cửa sổ TTM 12 tháng là quý $t-4$ (cùng quý năm trước, cách đúng $1$ năm tài chính):
      $$\overline{S}_t = \frac{S_t + S_{t-4}}{2}$$
    * Nếu kỳ $t-4$ không tồn tại trong lịch sử (4 quý đầu tiên của chuỗi dữ liệu), $\overline{S}_t$ trả về `NULL` kèm cờ `'MISSING_PRIOR_YEAR_BASE'` (không tự ý lấy số cuối kỳ $S_t$ thay cho số bình quân để tránh làm lệch chuẩn chuỗi thời gian).
    * Khi kỳ Q4 có dữ liệu dòng tiền hoặc KQKD nhưng thiếu khoản mục bảng cân đối, dùng khoản mục đã công bố của báo cáo năm có cùng doanh nghiệp, phạm vi, năm tài chính, ngày chốt và chế độ kế toán. Chỉ dùng dữ liệu khả dụng tại mốc PIT.

---

### 4.2. Bước 4.2 — Danh Mục 28 Tỷ Số Tài Chính Chuẩn & Hệ Thống Cờ Biên (`src/finintel/analytics/ratios.py`)

Mỗi tỷ số `<ratio_name>` phải sinh ra **2 cột** trong bảng đầu ra: cột giá trị số thực `<ratio_name>` (`pl.Float64`) và cột trạng thái `<ratio_name>_flag` (`pl.Utf8`).

#### A. Bảng Quy Tắc Cờ Biên Toán Học (`RatioStatusFlag` Enum)
Khi tính tỷ số $R = \frac{N}{D}$ (hoặc $R = \frac{N}{\overline{D}}$ với $\overline{D} = \frac{D_t + D_{t-4}}{2}$), hàm tính toán phải kiểm tra theo đúng thứ tự ưu tiên sau:

1. **`'NOT_APPLICABLE_FOR_REGIME'`** khi tỷ số nằm ngoài danh sách áp dụng cho chế độ kế toán của doanh nghiệp.
2. **`'MIXED_TAXONOMY'`:** Tử số hoặc mẫu số có `computation_note == 'MIXED_TAXONOMY'` khi vắt qua ranh giới TT200 và TT99 $\rightarrow$ $R = \text{NULL}$.
3. **`'NEGATIVE_OR_ZERO_EQUITY'`** khi tử số đã biết và vốn chủ sở hữu đầu kỳ hoặc cuối kỳ đã công bố bằng 0 hay âm. Áp dụng cho `roe_total`, `roe_parent`, `debt_to_equity` và `net_debt_to_equity`.
4. **`'MISSING_DEPRECIATION_DIRECT_CF'`** khi tỷ số yêu cầu EBITDA nhưng thiếu khấu hao.
5. **`'MISSING_PRIOR_YEAR_BASE'`** khi có tử số và phép tính yêu cầu kỳ cơ sở năm trước nhưng kỳ đó chưa có.
6. **`'MISSING_INPUT'`** khi thiếu tử số hoặc mẫu số bắt buộc.
7. Cờ biên riêng khi đủ đầu vào. `'NON_POSITIVE_INVESTED_CAPITAL'` khi vốn đầu tư tại một trong hai đầu kỳ $\le 0$, `'NON_POSITIVE_EBITDA'` khi EBITDA $\le 0$, `'NO_INTEREST_EXPENSE'` khi chi phí lãi vay hoặc đi vay đã công bố bằng 0, và `'NON_POSITIVE_BASE_PERIOD'` khi cơ sở tăng trưởng $\le 0$.
8. **`'ZERO_OR_NEGATIVE_DENOMINATOR'`** cho các mẫu số còn lại $\le 0$. **`'NUMERIC_OVERFLOW'`** khi phép tính vượt miền số hữu hạn.
9. **`'DERIVED_Q4_INPUT'`** khi kết quả hợp lệ và một đầu vào TTM dùng quý có `derivation_method == 'DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX'`.
10. **`'VALID'`** cho các kết quả hợp lệ còn lại. Chỉ hai cờ cuối giữ giá trị số.

#### B. Bảng Công Thức Tường Minh Cho 28 Tỷ Số Cốt Lõi

| STT | Tên cột (`ratio_name`) | Nhóm | Công thức Toán học Chính xác | Quy tắc Biên Đặc thù |
|---|---|---|---|---|
| 1 | `gross_margin` | Sinh lời | $\frac{\text{IS\_GROSS\_PROFIT}_{TTM, t}}{\text{IS\_NET\_REVENUE}_{TTM, t}}$ | Mẫu số $\le 0 \rightarrow \text{NULL}$ |
| 2 | `ebit_margin` | Sinh lời | $\frac{\text{EBIT}_{TTM, t}}{\text{IS\_NET\_REVENUE}_{TTM, t}}$ | Mẫu số $\le 0 \rightarrow \text{NULL}$ |
| 3 | `ebitda_margin` | Sinh lời | $\frac{\text{EBITDA}_{TTM, t}}{\text{IS\_NET\_REVENUE}_{TTM, t}}$ | Trả `NULL` nếu khuyết khấu hao |
| 4 | `operating_margin` | Sinh lời | $\frac{\text{IS\_OPERATING\_PROFIT}_{TTM, t}}{\text{IS\_NET\_REVENUE}_{TTM, t}}$ | Mẫu số $\le 0 \rightarrow \text{NULL}$ |
| 5 | `net_profit_margin` | Sinh lời | $\frac{\text{IS\_NET\_INCOME\_TOTAL}_{TTM, t}}{\text{IS\_NET\_REVENUE}_{TTM, t}}$ | Mẫu số $\le 0 \rightarrow \text{NULL}$ |
| 6 | `roa` | Sinh lời | $\frac{\text{IS\_NET\_INCOME\_TOTAL}_{TTM, t}}{\overline{\text{BS\_TOTAL\_ASSETS}}_t}$ | Áp dụng toàn sàn (`Universal`) |
| 7 | `roe_total` | Sinh lời | $\frac{\text{IS\_NET\_INCOME\_TOTAL}_{TTM, t}}{\overline{\text{BS\_TOTAL\_EQUITY}}_t}$ | `NEGATIVE_OR_ZERO_EQUITY` nếu $E_t \le 0 \lor E_{t-4} \le 0$ (`Universal`) |
| 8 | `roe_parent` | Sinh lời | $\frac{\text{IS\_NET\_INCOME\_PARENT}_{TTM, t}}{\overline{\text{ParentEquity}}_t}$ | `NEGATIVE_OR_ZERO_EQUITY` nếu $PE_t \le 0 \lor PE_{t-4} \le 0$ (`Universal`) |
| 9 | `roic` | Sinh lời | $\frac{\text{NOPAT}_{TTM, t}}{\overline{\text{InvestedCapital}}_t}$ | `NON_POSITIVE_INVESTED_CAPITAL` nếu $IC_t \le 0 \lor IC_{t-4} \le 0$ |
| 10 | `current_ratio` | Thanh khoản | $\frac{\text{BS\_CURRENT\_ASSETS}_t}{\text{BS\_CURRENT\_LIABILITIES}_t}$ | Chỉ tính cho `CORPORATE`; mẫu số $\le 0 \rightarrow \text{NULL}$ |
| 11 | `quick_ratio` | Thanh khoản | $\frac{\text{BS\_CASH\_AND\_EQUIVALENTS}_t + \text{coalesce}(\text{BS\_ST\_FINANCIAL\_INVESTMENTS}_t, 0) + \text{BS\_TRADE\_RECEIVABLES\_ST}_t}{\text{BS\_CURRENT\_LIABILITIES}_t}$ | Loại bỏ hàng tồn kho & phải thu khác kém thanh khoản |
| 12 | `cash_ratio` | Thanh khoản | $\frac{\text{CashAndSTInv}_t}{\text{BS\_CURRENT\_LIABILITIES}_t}$ | Mẫu số $\le 0 \rightarrow \text{NULL}$ |
| 13 | `ocf_to_current_liabilities` | Thanh khoản | $\frac{\text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t}}{\overline{\text{BS\_CURRENT\_LIABILITIES}}_t}$ | Đo khả năng trả nợ ngắn hạn từ dòng tiền HĐKD |
| 14 | `liabilities_to_assets` | Đòn bẩy | $\frac{\text{BS\_TOTAL\_LIABILITIES}_t}{\text{BS\_TOTAL\_ASSETS}_t}$ | Tỷ lệ Nợ phải trả / Tổng tài sản (`Universal`) |
| 15 | `financial_debt_to_assets` | Đòn bẩy | $\frac{\text{TotalDebt}_t}{\text{BS\_TOTAL\_ASSETS}_t}$ | Tỷ lệ Nợ vay chịu lãi / Tổng tài sản |
| 16 | `debt_to_equity` | Đòn bẩy | $\frac{\text{TotalDebt}_t}{\text{BS\_TOTAL\_EQUITY}_t}$ | `NEGATIVE_OR_ZERO_EQUITY` nếu $E_t \le 0$ hoặc đã biết $E_{t-4} \le 0$ |
| 17 | `net_debt_to_equity` | Đòn bẩy | $\frac{\text{NetDebt}_t}{\text{BS\_TOTAL\_EQUITY}_t}$ | Có thể âm khi tiền mặt vượt nợ vay. Cùng điều kiện vốn chủ với `debt_to_equity` |
| 18 | `net_debt_to_ebitda` | Đòn bẩy | $\frac{\text{NetDebt}_t}{\text{EBITDA}_{TTM, t}}$ | `NON_POSITIVE_EBITDA` nếu $\text{EBITDA}_{TTM, t} \le 0$ |
| 19 | `interest_coverage` | Đòn bẩy | $\frac{\text{EBIT}_{TTM, t}}{\left|B_{TTM,t}\right|}$ | $B$ theo chế độ tại `4.1. Cờ `NO_INTEREST_EXPENSE` nếu mẫu số đã công bố bằng 0 |
| 20 | `asset_turnover` | Hiệu quả | $\frac{\text{IS\_NET\_REVENUE}_{TTM, t}}{\overline{\text{BS\_TOTAL\_ASSETS}}_t}$ | Vòng quay tổng tài sản |
| 21 | `inventory_turnover` | Hiệu quả | $\frac{\left|\text{IS\_COGS}_{TTM, t}\right|}{\overline{\text{BS\_INVENTORIES\_NET}}_t}$ | Kèm `dio` $= \frac{365.0}{\text{inventory\_turnover}}$ |
| 22 | `receivables_turnover` | Hiệu quả | $\frac{\text{IS\_NET\_REVENUE}_{TTM, t}}{\overline{\text{BS\_TRADE\_RECEIVABLES\_ST}}_t}$ | Kèm `dso` $= \frac{365.0}{\text{receivables\_turnover}}$ |
| 23 | `payables_turnover` | Hiệu quả | $\frac{\left|\text{IS\_COGS}_{TTM, t}\right|}{\overline{\text{BS\_TRADE\_PAYABLES\_ST}}_t}$ | Kèm `dpo` $= \frac{365.0}{\text{payables\_turnover}}$ |
| 24 | `cash_conversion_cycle` | Hiệu quả | $\text{DIO}_t + \text{DSO}_t - \text{DPO}_t$ | Chỉ tính khi cả 3 đại lượng `DIO, DSO, DPO` khác `NULL` |
| 25 | `ocf_to_net_income` | Dòng tiền | $\frac{\text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t}}{\text{IS\_NET\_INCOME\_TOTAL}_{TTM, t}}$ | Chỉ tính khi $\text{IS\_NET\_INCOME\_TOTAL}_{TTM, t} > 0$ |
| 26 | `fcf_to_revenue` | Dòng tiền | $\frac{\text{FCF}_{TTM, t}}{\text{IS\_NET\_REVENUE}_{TTM, t}}$ | Biên dòng tiền tự do |
| 27 | `accruals_to_assets` | Chất lượng LN | $\frac{\text{IS\_NET\_INCOME\_TOTAL}_{TTM, t} - \text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t}}{\overline{\text{BS\_TOTAL\_ASSETS}}_t}$ | Biến Accrual Ratio kinh điển của Sloan (1996) |
| 28 | `eps_ttm_estimated` | Trên mỗi CP | Lợi nhuận công ty mẹ chia số cổ phiếu theo `4.1(9) | Thiếu căn cứ về số cổ phiếu thì trả `NULL`. Lưu `eps_basic_reported` gốc cho năm (`Universal`) |

*(Ngoài ra tính kèm 3 chỉ số tốc độ tăng trưởng so với cùng kỳ năm trước: `revenue_growth_yoy`, `ebit_growth_yoy`, `net_income_parent_growth_yoy` theo công thức $\frac{X_{TTM, t} - X_{TTM, t-4}}{X_{TTM, t-4}}$, chỉ tính khi kỳ cơ sở $X_{TTM, t-4} > 0$; nếu $X_{TTM, t-4} \le 0$ trả về `NULL` kèm cờ `'NON_POSITIVE_BASE_PERIOD'` để tránh tốc độ tăng trưởng bị đảo dấu vô nghĩa).*

---

### 4.3. Bước 4.3 — Mô Hình Phân Rã DuPont 5 Bước & 6 Bước (`src/finintel/analytics/dupont.py`)

Trên BCTC Hợp nhất (`CONSOLIDATED`), để đẳng thức đại số của DuPont khớp tuyệt đối $100\%$ đến từng chữ số thập phân (sai số $\le 10^{-9}$), module `dupont.py` phải xuất đồng thời hệ thống 5 thành phần (cho `roe_total`) và thành phần thứ 6 (để nối sang `roe_parent`):

1. **5 Thành phần DuPont Chuẩn (Giải thích `roe_total`):**
   * $\text{tax\_burden}_t = \frac{\text{IS\_NET\_INCOME\_TOTAL}_{TTM, t}}{\text{IS\_PROFIT\_BEFORE\_TAX}_{TTM, t}}$
   * $\text{interest\_burden}_t = \frac{\text{IS\_PROFIT\_BEFORE\_TAX}_{TTM, t}}{\text{EBIT}_{TTM, t}}$
   * $\text{ebit\_margin}_t = \frac{\text{EBIT}_{TTM, t}}{\text{IS\_NET\_REVENUE}_{TTM, t}}$
   * $\text{asset\_turnover}_t = \frac{\text{IS\_NET\_REVENUE}_{TTM, t}}{\overline{\text{BS\_TOTAL\_ASSETS}}_t}$
   * $\text{equity\_multiplier\_total}_t = \frac{\overline{\text{BS\_TOTAL\_ASSETS}}_t}{\overline{\text{BS\_TOTAL\_EQUITY}}_t}$
   * **Đẳng thức kiểm chứng bắt buộc:**
     $$\text{roe\_total}_t \equiv \text{tax\_burden}_t \times \text{interest\_burden}_t \times \text{ebit\_margin}_t \times \text{asset\_turnover}_t \times \text{equity\_multiplier\_total}_t$$

2. **Mở rộng Thành phần Thứ 6 cho Cổ đông Công ty mẹ (Giải thích `roe_parent`):**
   * $\text{parent\_profit\_share}_t = \frac{\text{IS\_NET\_INCOME\_PARENT}_{TTM, t}}{\text{IS\_NET\_INCOME\_TOTAL}_{TTM, t}}$
   * $\text{equity\_multiplier\_parent}_t = \frac{\overline{\text{BS\_TOTAL\_ASSETS}}_t}{\overline{\text{ParentEquity}}_t}$
   * **Đẳng thức kiểm chứng bắt buộc:**
     $$\text{roe\_parent}_t \equiv \text{parent\_profit\_share}_t \times \text{tax\_burden}_t \times \text{interest\_burden}_t \times \text{ebit\_margin}_t \times \text{asset\_turnover}_t \times \text{equity\_multiplier\_parent}_t$$

*Điều kiện biên của DuPont:* Chuỗi 5 bước cần EBT, EBIT, doanh thu thuần, tài sản bình quân và vốn chủ ở hai đầu kỳ đều dương. Chuỗi 6 bước cần thêm vốn của cổ đông công ty mẹ dương ở cả hai đầu kỳ và tổng LNST khác 0. `dupont_valid_flag` chỉ đúng khi cả hai chuỗi hợp lệ.

---

### 4.4. Bước 4.4 — So Sánh Tương Quan Ngành (`src/finintel/analytics/comparisons.py`)

Để khử tính chu kỳ vĩ mô và đặc thù cấu trúc vốn giữa các ngành (ví dụ ngành Thép, Bất động sản và Công nghệ có vòng quay tài sản và biên lợi nhuận khác nhau), mỗi tỷ số $X_{i, g, t}$ của doanh nghiệp $i$ thuộc ngành $g$ tại kỳ $t$ phải được đặt trong phân phối mặt cắt ngang (`Cross-Sectional Distribution`) của ngành đó:

1. **Quy tắc Chọn Nhóm Tham Chiếu (`Peer Group Resolution`):**
   * Mọi cấp dùng cùng `(accounting_regime, statement_scope, period_type, period_end_date, fiscal_year, fiscal_quarter, metric_or_ratio_name)`.
   * Nhóm ưu tiên 1 dùng cùng `icb_code_level3` khi có ít nhất 8 doanh nghiệp có giá trị hợp lệ.
   * Nhóm ưu tiên 2 dùng cùng `icb_code_level2` khi có ít nhất 5 doanh nghiệp có giá trị hợp lệ.
   * Nhóm ưu tiên 3 dùng toàn bộ doanh nghiệp trong cùng lát cắt cơ sở và gắn `peer_group_level = 'REGIME_FALLBACK'`.

2. **Các Thống Kê Bền Vững Xuất Ra (`industry_benchmarks.parquet`):**
   * `peer_count`: Số lượng doanh nghiệp hợp lệ $N_{\text{valid}}$ trong nhóm.
   * `peer_median`: Trung vị $\text{Median}_g(X_t)$.
   * `peer_p25`, `peer_p75` và khoảng tứ phân vị $\text{IQR}_g(X_t) = P_{75} - P_{25}$.
   * `percentile_rank`: Thứ hạng phần trăm của doanh nghiệp trong nhóm ($0.0 \rightarrow 100.0$).
   * `robust_z_score` dùng hệ số chuẩn hóa IQR $1 / (2\Phi^{-1}(0.75)) \approx 0.7413$ và cắt ngưỡng trong khoảng $[-5.0, +5.0]$.
     $$Z^{\text{robust}}_{i, g, t} = \text{clip}\left(\frac{X_{i, g, t} - \text{Median}_g(X_t)}{\max\left(\text{IQR}_g(X_t) \times 0.7413011,\; 10^{-6}\right)}, -5.0, +5.0\right)$$

---

### 4.5. Bước 4.5 — Hệ Thống Cảnh Báo Kế Toán & Thanh Khoản Sớm (`src/finintel/analytics/red_flags.py`)

Module `red_flags.py` quét toàn bộ lịch sử chuẩn hóa và xuất ra bảng `accounting_red_flags.parquet` gồm 7 quy tắc kiểm toán định lượng (mỗi cảnh báo phải lưu kèm `evidence_json` chứa con số thực tế để hiển thị lên Dashboard): Riêng RF_03 cần khoản phải thu dài hạn đã công bố, kể cả khi bằng 0.

| Mã Cảnh Báo (`flag_code`) | Mức độ | Điều kiện Toán học Kích hoạt Chính xác | Ý nghĩa Kinh tế / Kiểm toán |
|---|---|---|---|
| `RF_01_ACCRUAL_DIVERGENCE` | `HIGH` | $\text{IS\_NET\_INCOME\_TOTAL}_{TTM, t} > 0$ **VÀ** $\text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t} < 0$ trong $\ge 2$ kỳ liên tiếp ($t$ và $t-1$), đồng thời $\text{accruals\_to\_assets}_t > 0.10$. | Lợi nhuận trên giấy nhưng không thu được tiền mặt thực tế. |
| `RF_02_RECEIVABLES_SURGE` | `MEDIUM` | $\text{revenue\_growth\_yoy}_t > 0$ **VÀ** $\text{GrowthYoY}(\text{BS\_TRADE\_RECEIVABLES\_ST}_t) > \max(1.5 \times \text{revenue\_growth\_yoy}_t,\; 0.25)$ **VÀ** $\text{DSO}_t - \text{DSO}_{t-4} > 15\text{ ngày}$. | Nghi vấn nới lỏng tín dụng thương mại hoặc ghi nhận doanh thu đẩy kênh (channel stuffing). |
| `RF_03_OTHER_RECEIVABLES_ANOMALY` | `HIGH` | $\frac{\text{BS\_ST\_RECEIVABLES\_TOTAL}_t - \text{BS\_TRADE\_RECEIVABLES\_ST}_t + \text{BS\_LT\_RECEIVABLES}_t}{\text{BS\_TOTAL\_ASSETS}_t} > 0.25$. | Rủi ro rút ruột vốn qua "Phải thu khác / Hợp tác đầu tư / Tạm ứng" vượt $25\%$ tổng tài sản (đặc thù rủi ro rất phổ biến trên sàn chứng khoán Việt Nam!). |
| `RF_04_INVENTORY_BLOAT` | `MEDIUM` | $\frac{\text{BS\_INVENTORIES\_NET}_t}{\text{BS\_TOTAL\_ASSETS}_t} > 0.15$ **VÀ** $\text{DIO}_t - \text{DIO}_{t-4} > 30\text{ ngày}$ **VÀ** $\text{gross\_margin}_t < \text{gross\_margin}_{t-4}$. | Hàng tồn kho ứ đọng chậm luân chuyển kèm suy giảm biên lợi nhuận gộp. |
| `RF_05_DEBT_SERVICE_DISTRESS` | `CRITICAL` | $\left(\text{interest\_coverage}_t < 1.0 \text{ VÀ } \text{TotalDebt}_t > 0\right)$ **HOẶC** $\left(\text{current\_ratio}_t < 1.0 \text{ VÀ } \text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t} < 0 \text{ VÀ } \text{debt\_to\_equity}_t > 2.0\right)$. | Lợi nhuận hoạt động không đủ trả lãi vay hoặc căng thẳng thanh khoản nợ ngắn hạn nghiêm trọng. |
| `RF_06_NEGATIVE_EQUITY` | `CRITICAL` | $\text{BS\_TOTAL\_EQUITY}_t \le 0$ **HOẶC** $\text{ParentEquity}_t \le 0$. | Cảnh báo tài chính nội bộ về vốn chủ sở hữu không dương. |
| `RF_07_AUDIT_OR_FILING_WARNING` | `CRITICAL` | `audit_opinion` $\in \{\text{'EXCEPT\_FOR'}, \text{'ADVERSE'}, \text{'DISCLAIMER'}\}$ **HOẶC** `is_filed_late == 1`. | Kiểm toán ngoại trừ/từ chối/trái ngược hoặc nộp trễ hạn báo cáo. |

Layer 2 chuyển `is_filed_late` từ dữ liệu công bố của hồ sơ gốc. Khi thiếu ngày công bố đã xác minh, trường này là `NULL`. Các kỳ dẫn xuất không tạo sự kiện trễ hạn riêng.

---

## 5. ĐẶC TẢ CHI TIẾT GIAI ĐOẠN 4B (BƯỚC 4.6 ĐẾN BƯỚC 4.8 — QUANT MODELS & MACHINE LEARNING)

### 5.1. Bước 4.6 — Ba Mô Hình Chấm Điểm Định Lượng Học Thuật (`src/finintel/ml/classical_scores.py`)

Mọi điểm số trong module này chỉ áp dụng cho `accounting_regime == 'CORPORATE'` và phải đính kèm cột `model_role = 'SCREENING_INDICATOR'` (cấm tuyên bố ngưỡng cắt nước ngoài là xác suất vỡ nợ tuyệt đối tại Việt Nam).

#### 1. Mô hình Altman $Z''$-Score cho Thị trường Mới nổi (Altman 1995, 2000)
* **Bốn biến thành phần tại kỳ $t$:**
  $$X_1 = \frac{\text{BS\_CURRENT\_ASSETS}_t - \text{BS\_CURRENT\_LIABILITIES}_t}{\text{BS\_TOTAL\_ASSETS}_t}, \quad X_2 = \frac{\text{BS\_RETAINED\_EARNINGS}_t}{\text{BS\_TOTAL\_ASSETS}_t}$$
  $$X_3 = \frac{\text{EBIT}_{TTM, t}}{\text{BS\_TOTAL\_ASSETS}_t}, \quad X_4 = \frac{\text{BS\_TOTAL\_EQUITY}_t}{\text{BS\_TOTAL\_LIABILITIES}_t}$$
  *(Nếu $\text{BS\_TOTAL\_LIABILITIES}_t == 0$ và $\text{BS\_TOTAL\_EQUITY}_t > 0$, gán $X_4 = 10.0$ để tránh lỗi chia cho $0$).*
* **Công thức tính điểm:**
  $$\text{altman\_z\_double\_prime}_t = 6.56 X_1 + 3.26 X_2 + 6.72 X_3 + 1.05 X_4$$
  $$\text{altman\_em\_score}_t = 3.25 + \text{altman\_z\_double\_prime}_t$$
* **Phân vùng sàng lọc tham khảo (`altman_zone` dựa trên $\text{altman\_z\_double\_prime}_t$):**
  * `'SAFE_ZONE'` nếu $Z'' > 2.60$
  * `'GREY_ZONE'` nếu $1.10 \le Z'' \le 2.60$
  * `'DISTRESS_ZONE'` nếu $Z'' < 1.10$

#### 2. Mô hình Piotroski $F$-Score 9 Tiêu chí Nhị phân (Piotroski 2000)
Tính trên cửa sổ TTM tại kỳ $t$ so với kỳ $t-4$ (cùng kỳ năm trước):
1. $F_1 (\text{ROA} > 0) = \mathbb{I}(\text{roa}_t > 0)$
2. $F_2 (\text{CFO} > 0) = \mathbb{I}(\text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t} > 0)$
3. $F_3 (\Delta\text{ROA} > 0) = \mathbb{I}(\text{roa}_t - \text{roa}_{t-4} > 0)$
4. $F_4 (\text{Accrual}) = \mathbb{I}(\text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t} > \text{IS\_NET\_INCOME\_TOTAL}_{TTM, t})$
5. $F_5 (\Delta\text{Leverage} \le 0) = \mathbb{I}\left(\frac{\text{BS\_LT\_BORROWINGS}_t}{\overline{\text{BS\_TOTAL\_ASSETS}}_t} - \frac{\text{BS\_LT\_BORROWINGS}_{t-4}}{\overline{\text{BS\_TOTAL\_ASSETS}}_{t-4}} \le 0\right)$
6. $F_6 (\Delta\text{Liquidity} > 0) = \mathbb{I}(\text{current\_ratio}_t - \text{current\_ratio}_{t-4} > 0)$
7. $F_7 (\text{No Equity Dilution}) = \mathbb{I}\left(\text{CF\_EQUITY\_ISSUANCE\_PROCEEDS}_{TTM, t} = 0\right)$ *(Sử dụng dòng tiền thu từ phát hành vốn trên LCTT Mã 31 thay vì chỉ so sánh vốn điều lệ để không phạt nhầm các doanh nghiệp chia cổ tức bằng cổ phiếu/thưởng cổ phiếu từ nguồn vốn chủ sở hữu tại Việt Nam. Nếu thiếu số tiền đã công bố hoặc số tiền âm vi phạm quy ước dấu, để F7 là `NULL`.)*
8. $F_8 (\Delta\text{GrossMargin} > 0) = \mathbb{I}(\text{gross\_margin}_t - \text{gross\_margin}_{t-4} > 0)$
9. $F_9 (\Delta\text{AssetTurnover} > 0) = \mathbb{I}(\text{asset\_turnover}_t - \text{asset\_turnover}_{t-4} > 0)$
* **Tổng điểm:** $\text{piotroski\_f\_score}_t = \sum_{k=1}^{9} F_k \in \{0, 1, \dots, 9\}$ (chỉ xuất tổng điểm khi cả 9 tiêu chí đều xác định; lưu kèm cột `piotroski_valid_components_count`).

#### 3. Mô hình Beneish $M$-Score 8 Biến & 5 Biến (Beneish 1999)
Đặt $t$ là kỳ hiện tại (TTM/Năm) và $t-4$ là kỳ năm trước liền kề. Định nghĩa $\text{HardAssets}_k = \text{BS\_CURRENT\_ASSETS}_k + \text{BS\_TANGIBLE\_FIXED\_ASSETS\_NET}_k + \text{coalesce}(\text{BS\_LT\_FINANCIAL\_INVESTMENTS}_k, 0)$ và $D_k = \left|\text{CF\_DEPRECIATION\_AMORTIZATION}_{TTM, k}\right|$:

1. $\text{DSRI}_t = \frac{\text{BS\_TRADE\_RECEIVABLES\_ST}_t / \text{IS\_NET\_REVENUE}_{TTM, t}}{\text{BS\_TRADE\_RECEIVABLES\_ST}_{t-4} / \text{IS\_NET\_REVENUE}_{TTM, t-4}}$
2. $\text{GMI}_t = \frac{\text{gross\_margin}_{t-4}}{\text{gross\_margin}_t}$
3. $\text{AQI}_t = \frac{1.0 - \text{HardAssets}_t / \text{BS\_TOTAL\_ASSETS}_t}{1.0 - \text{HardAssets}_{t-4} / \text{BS\_TOTAL\_ASSETS}_{t-4}}$
4. $\text{SGI}_t = \frac{\text{IS\_NET\_REVENUE}_{TTM, t}}{\text{IS\_NET\_REVENUE}_{TTM, t-4}}$
5. $\text{DEPI}_t = \frac{D_{t-4} / (D_{t-4} + \text{BS\_TANGIBLE\_FIXED\_ASSETS\_NET}_{t-4})}{D_t / (D_t + \text{BS\_TANGIBLE\_FIXED\_ASSETS\_NET}_t)}$
6. $\text{SGAI}_t = \frac{(\left|\text{IS\_SELLING\_EXPENSE}_{TTM, t}\right| + \left|\text{IS\_G\_AND\_A\_EXPENSE}_{TTM, t}\right|) / \text{IS\_NET\_REVENUE}_{TTM, t}}{(\left|\text{IS\_SELLING\_EXPENSE}_{TTM, t-4}\right| + \left|\text{IS\_G\_AND\_A\_EXPENSE}_{TTM, t-4}\right|) / \text{IS\_NET\_REVENUE}_{TTM, t-4}}$
7. $\text{LVGI}_t = \frac{\text{BS\_TOTAL\_LIABILITIES}_t / \text{BS\_TOTAL\_ASSETS}_t}{\text{BS\_TOTAL\_LIABILITIES}_{t-4} / \text{BS\_TOTAL\_ASSETS}_{t-4}}$
8. $\text{TATA}_t = \frac{\text{IS\_OPERATING\_PROFIT}_{TTM, t} - \text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t}}{\text{BS\_TOTAL\_ASSETS}_t}$

* **Quy tắc Cắt Ngoại Lai Cho Chỉ Số Thành Phần Beneish:** Khi mẫu số của một chỉ số thành phần (như $\text{DSRI}, \text{AQI}, \text{DEPI}$) gần $0$ làm tỷ số vọt lên hàng trăm lần, phải cắt ngưỡng (clip) các biến chỉ số $\text{DSRI}, \text{GMI}, \text{AQI}, \text{SGI}, \text{DEPI}, \text{SGAI}, \text{LVGI}$ trong đoạn $[0.1, 10.0]$ và $\text{TATA} \in [-1.0, 1.0]$ trước khi nhân trọng số.
* **Công thức 8 biến (`beneish_m8_score`):**
  $$M_8 = -4.84 + 0.920\cdot\text{DSRI} + 0.528\cdot\text{GMI} + 0.404\cdot\text{AQI} + 0.892\cdot\text{SGI} + 0.115\cdot\text{DEPI} - 0.172\cdot\text{SGAI} + 4.679\cdot\text{TATA} - 0.327\cdot\text{LVGI}$$
  *(Ngưỡng sàng lọc tham khảo: $M_8 > -1.78 \rightarrow \text{'HIGH\_MANIPULATION\_RISK\_SCREEN'}$; nếu khuyết khấu hao $D_t$ thì trả về `NULL` kèm cờ `'MISSING_DEPRECIATION_DIRECT_CF'`).*

---

### 5.2. Bước 4.7 — Tiền Xử Lý Chéo Ngành, PCA, Phân Cụm Chân Dung Tài Chính & Isolation Forest (`src/finintel/ml/unsupervised.py`)

#### 1. Bộ 12 Đặc Trưng Cốt Lõi Độc Lập Dùng Cho PCA & Clustering (`UNSUPERVISED_FEATURE_SET`)
Để tránh hiện tượng đa cộng tuyến nặng làm méo trục PCA (ví dụ đưa cả 5 loại biên lợi nhuận vào cùng lúc), chỉ chọn đúng **12 đặc trưng đại diện cho 5 chiều sức khỏe tài chính**:
* **Sinh lời (3):** `roa`, `ebit_margin`, `roic`
* **Thanh khoản (2):** `current_ratio`, `cash_ratio`
* **Đòn bẩy & Khả năng trả nợ (3):** `liabilities_to_assets`, `financial_debt_to_assets`, `interest_coverage`
* **Hiệu quả Vốn lưu động (2):** `asset_turnover`, `cash_conversion_cycle`
* **Chất lượng Dòng tiền & Tăng trưởng (2):** `fcf_to_revenue`, `accruals_to_assets`

#### 2. Quy Trình Tiền Xử Lý Chống Nhiễu & Điền Khuyết Có Ngữ Cảnh (`CrossSectionalPreprocessor`)
Mọi bộ biến đổi phải tuân thủ giao diện `fit(train_df)` và `transform(eval_df)`:
1. **Xử lý thông minh cho `NO_INTEREST_EXPENSE`:** Những doanh nghiệp có `interest_coverage_flag == 'NO_INTEREST_EXPENSE'` và $\text{EBIT}_{TTM} > 0$ không phải là bị khuyết dữ liệu ngẫu nhiên, mà là những doanh nghiệp **không vay nợ (khả năng chi trả lãi vay mạnh nhất)**. Do đó, trước khi chạy Imputer chung, gán giá trị `interest_coverage` của các doanh nghiệp này bằng phân vị thứ $95$ ($P_{95}$) của `interest_coverage` trong tập `Train`!
2. **Winsorization (`[1%, 99%]`):** Cắt ngưỡng ngoại lai ở phân vị $P_{01}$ và $P_{99}$ học từ tập `Train`.
3. **Imputation (`MedianImputer`):** Điền các giá trị `NULL` còn lại bằng trung vị ngành (hoặc trung vị tập `Train`) và ghép thêm cột nhị phân đánh dấu khuyết.
4. **Scaling (`RobustScaler`):** Chuẩn hóa theo Median và IQR học từ tập `Train`.

#### 3. Giảm Chiều PCA & Phân Cụm Chân Dung Tài Chính (`PCA + K-Means`)
* Huấn luyện `sklearn.decomposition.PCA(n_components=0.80, svd_solver="full", random_state=42)` (giữ số thành phần chính giải thích tối thiểu $80\%$ phương sai, giới hạn trong khoảng $[3, 5]$ trục).
* Tự động đặt tên có ý nghĩa kinh tế cho mỗi trục `PC1, PC2, PC3` dựa trên 2 biến gốc có hệ số tải tuyệt đối ($\left|\text{loading}\right|$) lớn nhất.
* Huấn luyện `KMeans` trên không gian PCA với $K \in \{3, 4, 5, 6\}$ (`n_init=20, random_state=42`), chọn $K^*$ tối ưu hóa chỉ số **Silhouette Score** trên tập `Train`, và gán nhãn cụm định danh dựa trên vector tâm cụm (ví dụ: *"Cụm sinh lời cao - Đòn bẩy thấp"*, *"Cụm thâm dụng vốn - Vay nợ cao"*, *"Cụm suy giảm dòng tiền"*).

#### 4. Phát Hiện Bất Thường Đa Biến (`IsolationForest`)
* Huấn luyện `IsolationForest(n_estimators=200, max_samples="auto", contamination="auto", random_state=42)` trên ma trận đặc trưng đã chuẩn hóa.
* Xuất cột `anomaly_score_raw = -model.score_samples(X)` (điểm càng cao càng dị biệt) và `anomaly_percentile_rank` ($0 \rightarrow 100$).
* Gắn cờ `statistical_outlier_flag = True` cho các quan sát nằm ở phân vị $\ge 95\%$ kèm nhãn giải thích cứng: `'STATISTICAL_MULTIVARIATE_OUTLIER'` (tuyệt đối cấm dùng từ `'FRAUD'`).

---

### 5.3. Bước 4.8 — Mô Hình Dự Báo Suy Thoái Tài Chính Có Giám Sát (`XGBoost + Monotone Constraints + TreeSHAP`)

Module `src/finintel/ml/supervised/` dự báo xác suất một doanh nghiệp rơi vào trạng thái **Suy thoái Tài chính (`Financial Distress`)** trong cửa sổ tương lai $h = 4$ quý tới ($(t, t+4]$).

#### 1. Định Nghĩa Biến Mục Tiêu Nhị Phân Tường Minh (`src/finintel/ml/supervised/target.py`)
Biến mục tiêu $Y_{i, t \to t+4} \in \{0, 1\}$ hỗ trợ 2 chế độ rõ ràng:
* **Chế độ A (`FUNDAMENTAL_DISTRESS` — Mặc định, tự kiểm chứng 100% từ dữ liệu BCTC):**
  Một doanh nghiệp đang ở trạng thái bình thường tại quý $t$ sẽ nhận nhãn $Y_{i, t \to t+4} = 1$ nếu trong cửa sổ 4 quý tiếp theo $\{t+1, t+2, t+3, t+4\}$ doanh nghiệp đó xuất hiện **ít nhất một** trong 3 sự kiện đổ vỡ tài chính sau:
  1. **Vỡ vốn chủ sở hữu:** $\min_{k \in \{1..4\}} \text{BS\_TOTAL\_EQUITY}_{t+k} \le 0$ hoặc $\min_{k \in \{1..4\}} \text{ParentEquity}_{t+k} \le 0$.
  2. **Mất khả năng thanh toán lãi vay & dòng tiền kiệt quệ kéo dài:** Trong ít nhất 2 quý thuộc $\{t+1..t+4\}$, doanh nghiệp đồng thời bị $\text{interest\_coverage}_{t+k} < 1.0$ **VÀ** $\text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t+k} < 0$ **VÀ** $\text{current\_ratio}_{t+k} < 1.0$.
  3. **Suy thoái lợi nhuận & dòng tiền nghiêm trọng:** $\text{roa}_{t+4} < -0.10$ (lỗ ròng trên $10\%$ tổng tài sản) đi kèm $\text{CF\_OPERATING\_CASH\_FLOW}_{TTM, t+4} < 0$.
* **Chế độ B (`REGULATORY_DISTRESS` — Khi nạp thêm bảng sự kiện CBTT của HOSE/HNX):**
  Kết hợp điều kiện Chế độ A với sự kiện cổ phiếu bị đưa vào diện Cảnh báo / Kiểm soát / Hạn chế giao dịch / Hủy niêm yết bắt buộc hoặc nhận Ý kiến kiểm toán Trái ngược / Từ chối trong cửa sổ $(t, t+4]$.
* **Quy tắc Chống Rò Rỉ Nhãn Hiện Tại (Onset Filtering):** Nếu tại thời điểm dự báo $t$, doanh nghiệp **đã** nằm sẵn trong trạng thái suy thoái ở trên, bản ghi tại kỳ $t$ được gắn cờ `already_distressed_at_t = True` để tách riêng hoặc đưa vào biến trạng thái chuyển tiếp, tránh việc mô hình chỉ học cách nhận diện lại các công ty đã chết lâm sàng.

#### 2. Quy Tắc Chia Tập Dữ Liệu Theo Thời Gian Có Khoảng Đệm (`Purged Walk-Forward Split` trong `temporal_split.py`)
Thời điểm xác nhận nhãn $Y_{i, t \to t+4}$ là ngày khả dụng muộn nhất của mọi hồ sơ, phiên bản và sự kiện dùng để xác định nhãn trong cửa sổ bốn quý, kể cả hồ sơ của các quý đầu công bố muộn.
* Khi bắt đầu tập `Validation` hoặc `Test` tại mốc $T_{\text{start}}$, mỗi mẫu dùng để huấn luyện hoặc hiệu chuẩn trước mốc đó phải thỏa mãn:
  $$\text{label\_available\_at}_{i,t} = \max_{f \in F_{i,t}} \operatorname{available\_at}(f) \le T_{\text{start}}$$
  Trong đó $F_{i,t}$ là toàn bộ đầu vào của nhãn. Áp dụng quy tắc ngày công bố chính xác hoặc ngày khả dụng hiệu dụng cho từng hồ sơ. Cửa sổ nhãn dài bốn quý và khoảng đệm có thể dài hơn khi dữ liệu đến muộn.

#### 3. Ràng Buộc Đơn Điệu Nghiệp Vụ (`Monotone Constraints`) & Huấn Luyện `XGBoost` (`train.py`)
Với dữ liệu bảng tài chính có cỡ mẫu vừa phải, `XGBClassifier` bắt buộc phải được cấu hình **Ràng buộc Đơn điệu (`monotone_constraints`)** theo lý thuyết tài chính để ngăn chặn tuyệt đối hiện tượng overfitting ngược chiều kinh tế:
* **Nhóm đặc trưng có ràng buộc `-1` (Giá trị càng cao $\rightarrow$ Rủi ro suy thoái càng giảm):**
  * `roa`, `roe_parent`, `roic`, `ebit_margin`, `current_ratio`, `quick_ratio`, `cash_ratio`, `interest_coverage`, `ocf_to_current_liabilities`, `fcf_to_revenue`, `altman_z_double_prime`, `piotroski_f_score`.
* **Nhóm đặc trưng có ràng buộc `+1` (Giá trị càng cao $\rightarrow$ Rủi ro suy thoái càng tăng):**
  * `liabilities_to_assets`, `financial_debt_to_assets`, `debt_to_equity`, `net_debt_to_ebitda`, `accruals_to_assets`.
* **Nhóm đặc trưng có ràng buộc `0` (Không áp đặt chiều đơn điệu):**
  * `asset_turnover`, `cash_conversion_cycle`, `revenue_growth_yoy` (vì tăng trưởng quá nóng bằng nợ vay cũng có thể dẫn tới vỡ nợ).

*Siêu tham số chuẩn chống Overfitting cho Tabular Financial Data:*
`max_depth=4`, `min_child_weight=10`, `subsample=0.8`, `colsample_bytree=0.8`, `learning_rate=0.05`, `reg_lambda=5.0`, `reg_alpha=1.0`, `eval_metric="aucpr"`, kết hợp `CalibratedClassifierCV` trên tập `Validation` để hiệu chuẩn điểm dự báo thành xác suất thực.

#### 4. Đối Chứng Baseline & Giải Thích Mô Hình Bằng `TreeSHAP` (`explain.py`)
* **Bắt buộc đánh giá song song với 2 Baseline:**
  1. `Baseline_1_AltmanZ`: Phân loại trực tiếp bằng $-\text{altman\_z\_double\_prime}_t$.
  2. `Baseline_2_Logit`: `LogisticRegression(penalty="l2", C=0.1, class_weight="balanced")` trên bộ đặc trưng đã qua `CrossSectionalPreprocessor`.
* **Báo cáo Đánh giá (`model_evaluation_report.json`):** Xuất đầy đủ `PR-AUC (Average Precision)`, `ROC-AUC`, `Brier Score`, `F2-Score` và `Recall@Top-10%` cho cả 2 Baseline và `XGBoost`.
* **Giải thích Cục bộ & Toàn cục bằng `shap.TreeExplainer`:**
  * Với mỗi doanh nghiệp tại kỳ $t$, trích xuất giá trị SHAP của toàn bộ đặc trưng và đóng gói **Top 5 yếu tố làm tăng rủi ro ($\text{SHAP} > 0$)** cùng **Top 5 yếu tố làm giảm rủi ro ($\text{SHAP} < 0$)** vào cột JSON `shap_top_drivers_json` theo định dạng chuẩn để Tầng 5 vẽ biểu đồ Waterfall tức thì:
    ```json
    {
      "base_value": -2.15,
      "prediction_score": 0.78,
      "top_risk_increasing": [
        {"feature": "debt_to_equity", "value": 3.42, "peer_median": 0.85, "shap_delta": 0.94}
      ],
      "top_risk_decreasing": [
        {"feature": "ebit_margin", "value": 0.18, "peer_median": 0.11, "shap_delta": -0.31}
      ]
    }
    ```

---

## 6. HỢP ĐỒNG SCHEMA PARQUET ĐẦU RA CỦA TẦNG 4

Toàn bộ kết quả của Tầng 4 được xuất ra các bảng Parquet chuẩn hóa để Tầng 3 đóng gói vào Snapshot:

1. **`financial_ratios.parquet` (Từ Bước 4.1, 4.2, 4.3):**
   * Khóa chính: `(issuer_id, ticker, accounting_regime, statement_scope, period_type, fiscal_year, fiscal_quarter, period_end_date, filing_id)`.
   * Cột nguồn gồm `taxonomy_version` và `source_filing_ids` (`List(String)`). `filing_id` là hồ sơ đại diện kỳ; danh sách nguồn gồm các hồ sơ đầu vào đã chọn cho kỳ hiện tại và kỳ so sánh cùng kỳ.
   * Đại lượng trung gian (`Float64`) gồm `total_debt`, `total_cash_and_st_investments`, `net_debt`, `parent_equity`, `working_capital`, `invested_capital`, `ebit_ttm`, `ebitda_ttm`, `effective_tax_rate`, `nopat_ttm`, `fcf_ttm`, `estimated_shares_outstanding`, `implied_weighted_shares` và `eps_basic_reported`.
   * Các cột trạng thái (`String`) gồm `ebit_flag`, `ebitda_flag`, `effective_tax_rate_flag`, `nopat_flag`, `fcf_flag` và `eps_estimate_basis`.
   * 28 cặp cột tỷ số `<ratio_name>` (`pl.Float64`) + `<ratio_name>_flag` (`pl.Utf8`).
   * Bảy cặp bổ sung theo cùng kiểu gồm `equity_to_assets`, `dio`, `dso`, `dpo`, `revenue_growth_yoy`, `ebit_growth_yoy` và `net_income_parent_growth_yoy`.
   * Các cột DuPont: `tax_burden`, `interest_burden`, `ebit_margin`, `asset_turnover`, `equity_multiplier_total`, `parent_profit_share`, `equity_multiplier_parent`, `dupont_valid_flag`.
2. **`industry_benchmarks.parquet` (Từ Bước 4.4):**
   * Khóa chính: `(issuer_id, accounting_regime, statement_scope, period_type, period_end_date, metric_or_ratio_name)`.
   * Các cột: `icb_code_used`, `peer_group_level`, `peer_count`, `value`, `peer_median`, `peer_p25`, `peer_p75`, `peer_iqr`, `percentile_rank`, `robust_z_score`.
3. **`accounting_red_flags.parquet` (Từ Bước 4.5):**
   * Khóa chính: `(issuer_id, statement_scope, period_type, period_end_date, flag_code)`.
   * Các cột: `severity` (`'CRITICAL' | 'HIGH' | 'MEDIUM'`), `flag_title_vn`, `explanation_text_vn`, `evidence_json`, `filing_id`.
   * `evidence_json` gồm số liệu kích hoạt và `source_filing_ids`. Với RF_07, `filing_id` và bằng chứng trỏ tới cùng hồ sơ công bố.
4. **`ml_scores_and_explanations.parquet` (Từ Bước 4.6, 4.7, 4.8 — Phase 2):**
   * Khóa chính: `(issuer_id, statement_scope, period_type, period_end_date, filing_id)`.
   * Điểm số cổ điển: `altman_z_double_prime`, `altman_em_score`, `altman_zone`, `piotroski_f_score`, `beneish_m8_score`, `beneish_flag`.
   * Không giám sát: `pca_pc1`, `pca_pc2`, `pca_pc3`, `cluster_id`, `cluster_archetype_name`, `anomaly_score_raw`, `anomaly_percentile_rank`, `statistical_outlier_flag`.
   * Có giám sát: `distress_risk_score`, `calibrated_distress_prob`, `model_version`, `shap_top_drivers_json`.

---

## 7. BỘ TIÊU CHÍ NGHIỆM THU BẮT BUỘC (`pytest` DEFINITION OF DONE CHO TẦNG 4)

Agent phải viết đầy đủ các bài test trong `tests/unit/test_layer4_analytics.py` (cho 4A) và `tests/unit/test_layer4_ml.py` (cho 4B) và vượt qua $100\%$ các kịch bản kiểm định sau:

### 7.1. Kiểm định Giai đoạn 4A (8 Bài Test Bắt Buộc)
1. **Test Source Verification Header & Regime Routing:** Kiểm tra các module của Tầng 4 đều có docstring ghi rõ `Verified Sources:` và kiểm tra khi đưa vào một bản ghi có `accounting_regime == 'BANK'`, hệ thống chỉ tính các chỉ số `Universal` (`roa`, `roe_total`, `roe_parent`, `liabilities_to_assets`, `eps_ttm_estimated`) và tự động gán `NULL` kèm cờ `'NOT_APPLICABLE_FOR_REGIME'` cho các chỉ số thanh khoản ngắn hạn/hàng tồn kho.
2. **Test FPT Golden Reconciliation (2022–2025):** Chạy pipeline tính chỉ số trên dữ liệu thực của FPT đã thu thập ở Gate 0; kiểm tra `Total Assets == 88,141,991,634,625 VND`, `Net Revenue 2025 == 70,112,825,100,710 VND`, `Net Income Total 2025 == 11,232,339,450,734 VND`, `CFO 2025 == 10,136,043,915,911 VND`, và kiểm tra `FCF = CFO - |CAPEX|` không bị cộng nhầm dấu âm của CAPEX.
3. **Test Both-Losses Sign Trap on ROE:** Khi `Net Income = -50 tỷ` và `Ending Total Equity = -200 tỷ` $\rightarrow$ `roe_total` và `roe_parent` bắt buộc là `NULL` kèm cờ `'NEGATIVE_OR_ZERO_EQUITY'` (tuyệt đối không trả về $+0.25$).
4. **Test Equity Sign Flip Across Period:** Khi `Beginning Equity = -10 tỷ` và `Ending Equity = +100 tỷ` (trung bình cộng bằng $+45\text{ tỷ} > 0$) $\rightarrow$ `roe_total` vẫn phải trả về `NULL` kèm cờ `'NEGATIVE_OR_ZERO_EQUITY'`.
5. **Test Zero Interest Expense vs. Zero Denominator:** Khi `EBIT = 120 tỷ` và `IS_INTEREST_EXPENSE = 0` $\rightarrow$ `interest_coverage` trả về `NULL` kèm cờ `'NO_INTEREST_EXPENSE'`.
6. **Test Exact 5-Step & 6-Step DuPont Algebraic Identity:** Trên toàn bộ các kỳ hợp lệ của FPT (2018–2025), kiểm tra:
   $$\left|\text{roe\_total} - (\text{tax\_burden} \times \text{interest\_burden} \times \text{ebit\_margin} \times \text{asset\_turnover} \times \text{equity\_multiplier\_total})\right| \le 10^{-9}$$
   và kiểm tra đẳng thức 6 bước đối với `roe_parent` đạt sai số $\le 10^{-9}$.
7. **Test Robust Z-Score Outlier Resistance:** Tạo một nhóm ngành gồm 10 công ty, trong đó 9 công ty có `ROE` từ $10\% \rightarrow 20\%$ và 1 công ty đột biến có `ROE = 5000%` $\rightarrow$ Kiểm tra `peer_median` và `peer_iqr` không bị kéo lệch bởi công ty ngoại lai, và `robust_z_score` của công ty ngoại lai được cắt đúng ở $+5.0$.
8. **Test Forensic Red Flags Triggering:** Giả lập doanh nghiệp có `Net Income TTM > 0` nhưng `CFO TTM < 0` 2 quý liên tiếp và khoản phải thu khác chiếm $30\%$ tổng tài sản $\rightarrow$ Phải kích hoạt chính xác `RF_01_ACCRUAL_DIVERGENCE` và `RF_03_OTHER_RECEIVABLES_ANOMALY` kèm `evidence_json` hợp lệ.

### 7.2. Kiểm định Giai đoạn 4B (5 Bài Test Bắt Buộc Khi Mở Khóa Phase 2)
1. **Test Classical Scores Exactness:** Kiểm tra công thức `Altman Z''`, `Piotroski F-Score` (9 tiêu chí) và `Beneish M-Score` (8 biến) trên bộ vector đầu vào mẫu đã tính tay; kiểm tra khi `CF_DEPRECIATION_AMORTIZATION` là `NULL` thì `beneish_m8_score` trả về `NULL` kèm cờ `'MISSING_DEPRECIATION_DIRECT_CF'`.
2. **Test Zero Look-Ahead Leakage in Purged Walk-Forward Split:** Kiểm tra `label_available_at` của mọi mẫu dùng trước ngày bắt đầu `Validation` hoặc `Test`. Bao gồm trường hợp hồ sơ ở $t+1$ công bố sau $t+4$ và sau mốc bắt đầu.
3. **Test Preprocessor Isolation (No Test-Set Leakage):** Thay đổi một giá trị cực đoan trong tập `Test` $\rightarrow$ Ma trận đặc trưng sau khi biến đổi của tập `Train` phải không thay đổi dù chỉ $10^{-12}$, và các doanh nghiệp có cờ `'NO_INTEREST_EXPENSE'` phải được gán phân vị $P_{95}$ của `interest_coverage` tập `Train`.
4. **Test XGBoost Monotone Constraints:** Giữ nguyên các biến khác của một mẫu bất kỳ, tăng dần `interest_coverage` hoặc `roa` $\rightarrow$ Điểm rủi ro `distress_risk_score` của `XGBoost` bắt buộc phải giảm hoặc giữ nguyên (không bao giờ được tăng!).
5. **Test TreeSHAP Additivity & JSON Schema:** Kiểm tra tổng giá trị SHAP của tất cả các biến cộng với `base_value` khớp với `raw_margin` dự báo của `XGBoost`, và `shap_top_drivers_json` parse được thành JSON hợp lệ chứa đúng tên đặc trưng gốc.
