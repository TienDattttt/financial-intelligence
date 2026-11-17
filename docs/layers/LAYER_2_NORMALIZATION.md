# ĐẶC TẢ XÂY DỰNG TẦNG 2: CANONICAL NORMALIZATION & ACCOUNTING PERIOD ENGINE

* **Mã tài liệu:** `LAYER-2`
* **Module đích:** `src/finintel/normalization/` và `config/taxonomy/`
* **Phụ thuộc đầu vào:** Payload từ `data/raw/` và metadata từ `filings_manifest` (Tầng 1).

---

## 1. BÀI TOÁN & PHẠM VI TRÁCH NHIỆM

Tầng 2 là trái tim nghiệp vụ kế toán của hệ thống, chịu trách nhiệm chuyển đổi các bảng số liệu từ nhiều nguồn và nhiều thời kỳ về một **Canonical Financial Fact Schema** thống nhất cho doanh nghiệp phi tài chính (`CORPORATE`), giải quyết triệt để 4 bài toán thực tế tại Việt Nam:
1. **Chuyển giao Chế độ Kế toán từ Thông tư 200/2014/TT-BTC sang Thông tư 99/2025/TT-BTC:** Xác định đúng chế độ kế toán theo ngày bắt đầu năm tài chính (`fiscal_year_start_date`), xử lý số liệu so sánh được trình bày lại (`RESTATED_REGIME_CHANGE`) và kiểm soát tính tương thích khi cộng trượt 4 quý (`TTM`) xuyên qua mốc 01/01/2026.
2. **Ánh xạ Chỉ tiêu Tất định (Deterministic Exact Mapping):** Tuyệt đối không dùng so khớp mờ (fuzzy matching); tận dụng mã chuẩn của nguồn nếu đã khớp và ghi nhận mọi chỉ tiêu lạ vào `unmapped_metrics`.
3. **Đồng bộ Kỳ Kế toán & Tách số liệu Quý rời rạc:** Xử lý sự khác biệt giữa chỉ tiêu thời điểm (`STOCK` - Bảng CĐKT) và chỉ tiêu thời kỳ (`FLOW` - Bảng KQKD, LCTT), tách số lũy kế đầu năm (`YTD`) thành số phát sinh quý rời và gắn cờ cảnh báo khi tách Quý 4 từ báo cáo năm đã kiểm toán trừ 9 tháng chưa kiểm toán.
4. **Chuẩn hóa Đơn vị & Dấu Kế toán:** Đưa toàn bộ giá trị tiền tệ về đơn vị chuẩn (**Đồng - VND**) và chuẩn hóa quy ước dấu của các khoản mục chi phí, khấu hao, dòng tiền chi ra.

---

## 2. NGUỒN HỌC ĐÃ KIỂM CHỨNG & CÁCH TÁI SỬ DỤNG

1. **Văn bản Pháp quy Bộ Tài chính Việt Nam:**
   * **Thông tư 200/2014/TT-BTC:** Hệ thống mã số chỉ tiêu BCTC áp dụng cho các năm tài chính bắt đầu trước ngày `01/01/2026` (Bảng CĐKT: Mã `100` đến `440`; Bảng KQKD: Mã `01` đến `71`; Bảng LCTT: Mã `01` đến `70`).
   * **Thông tư 99/2025/TT-BTC:** Có hiệu lực từ ngày `01/01/2026`, áp dụng cho các năm tài chính bắt đầu từ hoặc sau ngày `01/01/2026`.
2. **`JerBouma/FinanceToolkit` & `dgunning/edgartools`:**
   * *Cách áp dụng:* Học kiến trúc tách biệt hoàn toàn từ điển ánh xạ (`config/taxonomy/*.yaml`) khỏi bộ máy xử lý kỳ thời gian (`periods.py`). Học cách kiểm tra độ dài kỳ (`period_days`) và tính liên tục của ngày đầu kỳ – cuối kỳ trước khi thực hiện phép trừ tách quý hoặc phép cộng trượt 4 quý (`TTM`).

### Đối chiếu khi triển khai

* Mẫu B01 của TT200 dùng mã `270` cho tổng cộng tài sản. Mẫu B01 của TT99 dùng mã `280` cho tổng cộng tài sản và mã `270` cho nhóm tài sản dài hạn khác. Mẫu B02 của TT99 bổ sung lãi/lỗ bán bất động sản đầu tư ở mã `21`, chuyển doanh thu và chi phí tài chính sang mã `22` và `23`. Mã `02` của B03 cần biết phương pháp trực tiếp hoặc gián tiếp nếu đầu vào chỉ có mã số. [TT200, B01/B02](https://congbao.chinhphu.vn/tai-ve-van-ban-so-200-2014-tt-btc-6697-3551?format=pdf) · [TT200, B03](https://congbao.chinhphu.vn/tai-ve-van-ban-so-200-2014-tt-btc-6697-3553?format=pdf) · [TT99, Phụ lục IV](https://congbao.chinhphu.vn/tai-ve-van-ban-so-99-2025-tt-btc-46529-59652?format=pdf)
* Bộ Tài chính xác nhận doanh nghiệp có năm tài chính từ 01/07 đến 30/06 bắt đầu áp dụng TT99 từ 01/07/2026. [Giải đáp của Bộ Tài chính](https://ttcg.mof.gov.vn/hoidapcstc/home/cthoidap/159555)
* FinanceToolkit nạp bảng ánh xạ từ tệp riêng. EdgarTools đối chiếu khoảng ngày và độ dài khi chọn kỳ. Layer 2 áp dụng hai cách tổ chức này với YAML và Polars, tính số ngày bao gồm cả ngày đầu lẫn ngày cuối và kiểm tra các quý kề nhau theo ngày. [FinanceToolkit, normalization_model.py](https://github.com/JerBouma/FinanceToolkit/blob/main/financetoolkit/normalization_model.py) · [EdgarTools, period_selector.py](https://github.com/dgunning/edgartools/blob/main/edgar/xbrl/period_selector.py)
* Mã FHSC lấy từ payload thực nghiệm ở Chặng 0. Mẫu trích FPT 2025 khớp số năm đã kiểm toán cho doanh thu thuần, lợi nhuận sau thuế và lưu chuyển tiền thuần từ hoạt động kinh doanh. Chặng 0 cũng ghi nhận ngoại lệ về quy ước quý, nên ngày bắt đầu và kết thúc đã xác minh trong manifest quyết định một giá trị là quý rời hay lũy kế. [BCTC hợp nhất FPT 2025](https://fpt.com/-/media/project/fpt-corporation/fpt/ir/information-disclosures/year-report/2026/march/20260319---fpt---audited-consolidated-financial-statements-for-2025.pdf)

---

## 3. KIẾN TRÚC MODULE & CẤU HÌNH TAXONOMY

### 3.1. Cấu trúc File
```text
config/
├── metric_definitions.yaml            # Định nghĩa tập Canonical Metric IDs nội bộ
└── taxonomy/
    ├── corporate_tt200_2014.yaml      # Ánh xạ mã số TT200 & mã vnstock -> Canonical ID
    └── corporate_tt99_2025.yaml       # Ánh xạ mã số TT99 & mã vnstock -> Canonical ID
src/finintel/normalization/
├── __init__.py
├── regime_resolver.py                 # Xác định TT200 hay TT99 dựa trên fiscal_year_start_date
├── mapper.py                          # Exact mapping, chuẩn hóa dấu/đơn vị, xuất unmapped_metrics
├── periods.py                         # Tách YTD -> Quý rời, suy diễn Q4 kèm cờ derivation_method, tính TTM
└── restatement.py                     # Xử lý cột "Số đầu năm / Kỳ trước" trình bày lại khi đổi chế độ
```

### 3.2. Cấu trúc Chuẩn của `config/metric_definitions.yaml`
Mọi chỉ tiêu chuẩn trong hệ thống phải được định nghĩa tường minh với các thuộc tính sau:

```yaml
metrics:
  BS_TOTAL_ASSETS:
    canonical_id: "BS_TOTAL_ASSETS"
    statement_type: "BALANCE_SHEET"
    value_type: "STOCK"                        # Giá trị tại một thời điểm (không cộng dồn TTM)
    unit: "VND"
    sign_convention: "POSITIVE_NORMAL"
    tt200_code: "270"
    tt99_code: "280"
    comparable_across_taxonomy: true           # Được phép tính trung bình và YoY xuyên qua ranh giới TT200 -> TT99

  IS_NET_REVENUE:
    canonical_id: "IS_NET_REVENUE"
    statement_type: "INCOME_STATEMENT"
    value_type: "FLOW"                         # Giá trị phát sinh trong kỳ (cần tách quý rời và cộng TTM 4 quý)
    unit: "VND"
    sign_convention: "POSITIVE_REVENUE"
    tt200_code: "10"
    tt99_code: "10"
    comparable_across_taxonomy: true

  CF_DEPRECIATION_AMORTIZATION:
    canonical_id: "CF_DEPRECIATION_AMORTIZATION"
    statement_type: "CASH_FLOW"
    value_type: "FLOW"
    unit: "VND"
    sign_convention: "POSITIVE_MAGNITUDE"      # Luôn chuẩn hóa về độ lớn dương khi dùng trong EBITDA/Beneish
    tt200_code: "02"                           # Chỉ xuất hiện trên LCTT phương pháp gián tiếp
    tt99_code: "02"
    comparable_across_taxonomy: true
```

#### Chỉ tiêu lấy từ thuyết minh báo cáo tài chính

Đặt `filing_note: true` cho chỉ tiêu lấy từ thuyết minh. Chỉ tiêu này không có mã số trên biểu mẫu BCTC, vì vậy bỏ cả `tt200_code` và `tt99_code` khỏi định nghĩa.

Cả `config/taxonomy/corporate_tt200_2014.yaml` và `config/taxonomy/corporate_tt99_2025.yaml` phải khai báo mục rỗng `{}` dưới `metrics` cho chỉ tiêu đó. Ví dụ, mỗi tệp đều có `BS_TRADE_RECEIVABLES_ALLOWANCE_ST: {}`. Thiếu mục này hoặc khai báo mapping có nội dung đều khiến cấu hình bị từ chối.

`FileAdapter` tiếp nhận số liệu trích từ thuyết minh theo khóa khớp chính xác với `canonical_id`, ví dụ `BS_TRADE_RECEIVABLES_ALLOWANCE_ST`. Mapper chỉ ánh xạ khóa chuẩn này cho chỉ tiêu thuyết minh.

---

## 4. QUY TẮC NGHIỆP VỤ CHUẨN HÓA KẾ TOÁN (BẮT BUỘC TUÂN THỦ)

### 4.1. Quy tắc Xác định Chế độ Kế toán (`regime_resolver.py`)
* **Không dùng `calendar_year` của `period_end_date` để chọn Thông tư!**
* Phải kiểm tra `fiscal_year_start_date` của kỳ báo cáo đó:
  * Nếu $\text{fiscal\_year\_start\_date} < \text{'2026-01-01'}$ $\rightarrow$ Áp dụng `taxonomy_version = 'VAS_TT200_2014'`. (Ví dụ: Doanh nghiệp có niên độ tài chính từ `01/04/2025` đến `31/03/2026`, báo cáo Quý 4 kết thúc ngày `31/03/2026` vẫn lập theo **Thông tư 200**).
  * Nếu $\text{fiscal\_year\_start\_date} \ge \text{'2026-01-01'}$ $\rightarrow$ Áp dụng `taxonomy_version = 'VAS_TT99_2025'`.

### 4.2. Quy tắc Xử lý Số liệu So sánh Trình bày lại (`restatement.py`)
Khi một báo cáo năm tài chính 2026 (lập theo `VAS_TT99_2025`) chứa cột "Số đầu năm" hoặc "Kỳ trước tương ứng" (tức số liệu của năm 2025 được phân loại lại theo biểu mẫu TT99):
* Không được ghi đè lên bản ghi năm 2025 gốc (vốn mang `taxonomy_version = 'VAS_TT200_2014'`).
* Phải lưu thành một bản ghi mới cho kỳ 2025 với:
  * `taxonomy_version = 'VAS_TT99_2025'`
  * `revision_reason = 'RESTATED_REGIME_CHANGE'`
  * `filing_id` trỏ về chính tờ khai năm 2026 nơi con số trình bày lại đó được công bố (đảm bảo tính Point-in-Time: tại năm 2025 người dùng chỉ thấy số TT200; từ thời điểm công bố báo cáo 2026 trở đi, hệ thống có số năm 2025 đã quy đổi sang TT99 để tính tăng trưởng `YoY` đồng nhất cùng chế độ!).

### 4.3. Quy tắc Tách Quý rời rạc & Cờ Suy diễn Quý 4 (`periods.py`)
Đối với các chỉ tiêu `value_type == 'FLOW'` (Bảng KQKD và LCTT), mỗi bản ghi sau chuẩn hóa phải được gắn cột `derivation_method` nhận 1 trong 3 giá trị:
1. **`'REPORTED_DIRECTLY'`:** Nguồn dữ liệu cung cấp trực tiếp con số phát sinh của riêng quý đó ($\text{period\_days} \in [80, 100]$ ngày).
2. **`'DERIVED_YTD_DIFF'`:** Nguồn chỉ cung cấp số lũy kế từ đầu năm tài chính ($YTD_q$). Khi đó số phát sinh quý $q$ ($q \in \{2, 3, 4\}$) được tính bằng:
   $$\text{QuarterValue}_q = YTD_q - YTD_{q-1}$$
   *Điều kiện bắt buộc:* Cả hai kỳ $q$ và $q-1$ phải có cùng `issuer_id`, cùng `statement_scope`, cùng `fiscal_year_start_date` và cùng `audit_status`.
3. **`'DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX'`:** Khi doanh nghiệp không công bố báo cáo Quý 4 riêng lẻ mà chỉ công bố Báo cáo Kiểm toán Năm (`AUDITED`) trong khi 3 quý đầu năm (`Q1, Q2, Q3`) là số liệu tự lập/soát xét:
   $$\text{QuarterValue}_4 = \text{AnnualAudited} - (Q_1 + Q_2 + Q_3)$$
   *Cảnh báo nghiệp vụ:* Phép trừ này sẽ dồn toàn bộ các bút toán điều chỉnh kiểm toán của cả 3 quý đầu năm vào Quý 4. Vì vậy **bắt buộc** phải gắn cờ `derivation_method = 'DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX'` để Tầng 4 và Tầng 5 hiển thị chú thích cảnh báo rõ ràng cho người phân tích.

### 4.4. Quy tắc Tính Trượt 4 Quý (`TTM - Trailing Twelve Months`)
Hàm `compute_ttm(facts_df)` chỉ tính tổng 4 quý liên tiếp ($t-3, t-2, t-1, t$) cho một chỉ tiêu `FLOW` khi thỏa mãn **đồng thời 4 điều kiện**:
1. Có đủ đúng 4 quý liên tiếp không bị khuyết bất kỳ quý nào ở giữa (`count(non_null_quarters) == 4`).
2. Tổng số ngày bao phủ của 4 quý nằm trong khoảng $[350, 380]$ ngày và không chồng lấn nhau.
3. Cả 4 quý có cùng `statement_scope` (`CONSOLIDATED` hoặc `SEPARATE`).
4. **Kiểm tra tương thích chế độ kế toán:** Nếu 4 quý có cùng `taxonomy_version` $\rightarrow$ hợp lệ. Nếu 4 quý nằm vắt qua ranh giới `VAS_TT200_2014` và `VAS_TT99_2025` (ví dụ TTM tại Q2/2026 gồm Q3/2025, Q4/2025 theo TT200 và Q1/2026, Q2/2026 theo TT99):
   * Nếu chỉ tiêu có `comparable_across_taxonomy == true` hoặc đã có số liệu `RESTATED_REGIME_CHANGE` sang TT99 cho từng quý TT200 xuất hiện trong cửa sổ $\rightarrow$ Cho phép tính TTM. Giá trị so sánh cả năm có phạm vi cả năm và chỉ được dùng cho phép tính có cùng phạm vi.
   * Nếu `comparable_across_taxonomy == false` và không có số trình bày lại $\rightarrow$ Trả về `NULL` kèm cờ `computation_note = 'MIXED_TAXONOMY'`.

Mã và nhãn giống nhau chưa đủ để xác nhận tính tương đương. Ví dụ, doanh thu bán hàng mã `01` theo TT200 có thể gồm doanh thu bán bất động sản đầu tư. TT99 loại giao dịch này khỏi mã `01` và trình bày lãi/lỗ ở mã `21`. Cờ `false` giữ nguyên khi phạm vi thay đổi hoặc chưa có bằng chứng tương đương. [TT200, Điều 113](https://congbao.chinhphu.vn/tai-ve-van-ban-so-200-2014-tt-btc-6697-3551?format=pdf) · [TT99, Phụ lục IV](https://congbao.chinhphu.vn/tai-ve-van-ban-so-99-2025-tt-btc-46529-59655?format=pdf)

### 4.5. Quy ước dấu doanh thu và chi phí trên B02

`SIGNED_EXPENSE` áp dụng cho `IS_COST_OF_GOODS_SOLD`, `IS_FINANCIAL_EXPENSES`, `IS_SELLING_EXPENSES` và `IS_ADMINISTRATIVE_EXPENSES`. Chi phí thông thường có dấu dương và được trừ trong công thức B02. Layer 2 nhân số đã báo cáo với hệ số đơn vị VND và giữ nguyên dấu để bảo toàn các khoản hoàn nhập hoặc ghi giảm chi phí. `SIGNED_EXPENSE` giữ nguyên số 0 và `NULL`; quy ước doanh thu hiện hành `POSITIVE_REVENUE` không bị thay đổi trong lần chuẩn hóa này. [TT200, Điều 113](https://congbao.chinhphu.vn/tai-ve-van-ban-so-200-2014-tt-btc-6697-3551?format=pdf) · [TT99, Phụ lục IV](https://congbao.chinhphu.vn/tai-ve-van-ban-so-99-2025-tt-btc-46529-59655?format=pdf)

---

## 5. TIÊU CHÍ NGHIỆM THU TẦNG 2 (DEFINITION OF DONE)

Agent phải viết và chạy xanh `tests/unit/test_layer2_normalization.py`:
1. **Test Fiscal Regime Boundary:** Doanh nghiệp có niên độ `2025-04-01` đến `2026-03-31` phải được phân loại vào `VAS_TT200_2014`, trong khi niên độ `2026-01-01` đến `2026-12-31` được phân loại vào `VAS_TT99_2025`.
2. **Test Unmapped Metric Isolation:** Đưa vào một dòng chỉ tiêu lạ `"Phải thu nội bộ đặc thù XYZ"` không có trong file YAML $\rightarrow$ `mapper.py` không được tự đoán mà phải ghi vào bảng `unmapped_metrics` kèm `filing_id`.
3. **Test Missing Quarter in TTM:** Chuỗi dữ liệu chỉ có `Q1, Q2, Q4` (khuyết `Q3`) $\rightarrow$ Giá trị `TTM` tại `Q4` bắt buộc phải là `NULL` (cấm cộng 3 quý thành TTM).
4. **Test Cross-Taxonomy TTM:** Kiểm tra chỉ tiêu có `comparable_across_taxonomy = false` đi qua mốc 2025–2026 phải trả về `NULL` và `computation_note == 'MIXED_TAXONOMY'`.

## 6. CHẠY CHUẨN HÓA TỪ RAW ARCHIVE

```sh
PYTHONPATH=src uv run --locked python -m finintel.pipeline.cli run --stage normalize --source fhsc --ticker FPT --period 2026-Q2
```

```powershell
$env:PYTHONPATH = 'src'
uv run --locked python -m finintel.pipeline.cli run --stage normalize --source fhsc --ticker FPT --period 2026-Q2
```

Lệnh đọc các phiên bản trong `filings_manifest`, xác minh mã băm và dùng các kỳ lịch sử cùng mã doanh nghiệp để tính quý rời. Mỗi lần chạy thành công ghi một batch tại `data/curated/financial_facts/<run_id>/` với `financial_facts.parquet` và `unmapped_metrics.parquet`. Batch lưu số báo cáo gốc, vùng số so sánh đã cấu hình và quý suy diễn kèm `source_filing_ids`. Các batch chạy lại có thể chứa cùng phiên bản báo cáo.

FHSC dùng VND theo bằng chứng khảo sát. Với `--source file`, `unit_multiplier` là bắt buộc cho từng filing và phải là số nguyên dương đã xác minh; thiếu hoặc sai kiểu là vi phạm hợp đồng và phải dừng chuẩn hóa. Thêm `cash_flow_method: DIRECT` hoặc `INDIRECT` cho báo cáo LCTT dùng mã số cần phân biệt phương pháp. Khai báo `comparative` khi tệp TT99 có cột số so sánh đã xác minh. Ví dụ báo cáo kết quả kinh doanh năm 2026 công bố lại số năm 2025 trong cột `prior`.

```yaml
normalization:
  filings:
    "<filing_id>":
      unit_multiplier: 1000
      comparative:
        value_column: prior
        fiscal_year: 2025
        fiscal_year_start_date: 2025-01-01
        period_type: ANNUAL
        period_start_date: 2025-01-01
        period_end_date: 2025-12-31
```

`derive_quarters(facts, as_of=...)` và `compute_ttm(facts, as_of=...)` dùng ngày có dữ liệu muộn nhất của các đầu vào. Dùng `as_of` khi cần tái hiện thông tin đã công bố tại một ngày cụ thể. Mỗi chuỗi kỳ dùng cùng nguồn, doanh nghiệp và phạm vi báo cáo đã xác minh.
