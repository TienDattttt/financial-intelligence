# 03 — Đối chiếu UI ↔ Backend & Đặc tả mở rộng API (Contract-First)

> Tài liệu này đối chiếu từng khối giao diện trong [`02-ia-flows-wireframes.md`](./02-ia-flows-wireframes.md) với **10 route hiện có** trong `src/finintel/serving/api.py` và **các bảng Layer 3 đã có sẵn trong snapshot** (`src/finintel/storage/schemas.py`, `config/metric_definitions.yaml`).
> Nguyên tắc: **mọi endpoint mới đều giữ nguyên tính chất chỉ đọc (read-only), dùng `snapshot_session`, hỗ trợ `ETag`/`If-None-Match` (`304`) và sanitize lỗi** như 10 route hiện tại.

---

## 1. Kiểm kê nhanh: Backend hiện tại ĐÃ CÓ gì mà chưa cần mở rộng?

| Nhu cầu trên UI | Endpoint hiện có | Chi tiết đã xác minh trong mã nguồn |
|---|---|---|
| Trạng thái hệ thống, `snapshot_id` | `GET /api/v1/health` | Trả `status`, `active_snapshot_id`, `storage_backend` + `ETag` |
| Số liệu tổng quan snapshot | `GET /api/v1/snapshot/overview` | `total_issuers`, `total_ratio_periods`, `pass_periods`, `warning_periods`, `quarantined_rows`, `published_at` |
| Danh mục DN cho `Ctrl+K` | `GET /api/v1/issuers?limit=200&offset=...` | Có `ticker`, `company_name_vn`, `exchange`, `accounting_regime`, ICB L2/L3 (với ~375 DN chỉ cần 2 request song song và cache theo `snapshot_id`) |
| **Hồ sơ DN + Lịch sử điểm số (F2)** | `GET /api/v1/companies/{ticker}/profile` | **ĐÃ CÓ SẴN** `ml_scores` tối đa 200 kỳ (Altman Z″/EM, Piotroski F, Beneish M8, K-Means archetype, anomaly, `calibrated_distress_prob`, `shap_top_drivers`), `growth_series`, `capital_structure_series`, `dupont_series`, `core_earnings_series`, `red_flags`, `ratios_table` → **FE tự tính delta so kỳ trước và dải min/median/max 10 kỳ từ `ml_scores`, không cần API F2 riêng!** |
| So sánh ngành 1 chỉ số + lịch sử dải P25–P75 | `GET /api/v1/companies/{ticker}/benchmarks` | Có `peer_p25`, `peer_median`, `peer_p75`, `percentile_rank`, `peer_group_level`, `peer_count`, `peer_ranking`, `history` |
| Truy nguồn `SourceDrawer` (38/38 chỉ số) | `GET /api/v1/companies/{ticker}/lineage` | Có `formula`, `numerator_components`, `denominator_components`, `filing_id`, `published_at`, `taxonomy_version`, `canonical_content_sha256` |
| Sàng lọc toàn thị trường | `GET /api/v1/screener` | Lọc `icb_code_level2`, `min_f_score`, `altman_zone`, `beneish_flag`, `max_distress_prob`, `max_red_flags`, phân trang `limit`/`offset` |
| Mô phỏng sốc 5 tham số | `POST /api/v1/stress-test` | Hỗ trợ `interest_rate_shock_bps`, `revenue_shock_pct`, `gross_margin_shock_pp`, `dso_shock_days`, `dio_shock_days` + semaphore 503 |
| Bảng chất lượng & quarantine | `GET /api/v1/quality/summary`, `/quality/quarantine` | Đã phân trang phía server |

---

## 2. Bảng Gap Analysis — Các chỗ UI cần thêm từ Backend

| Mã | Khối UI cần | Trạng thái dữ liệu ở Layer 3 | Thiếu gì ở tầng API? | Ưu tiên (MoSCoW) |
|---|---|---|---|---|
| **G1** | **Bảng Traffic Light nhiều chỉ số cùng lúc** trên trang Tổng quan DN (8–12 chỉ số cốt lõi kèm `peer_median` và `percentile_rank`) | ✅ Đã có đủ trong `industry_benchmarks.parquet` | `/benchmarks` hiện chỉ nhận **1 `metric_name`** mỗi lần gọi → nếu FE gọi 12 lần song song sẽ thừa request | **Must** |
| **G2 (F1)** | **Tab Báo cáo tài chính chuẩn hóa nhiều kỳ** (CĐKT, KQKD, LCTT + tỷ trọng + tăng trưởng YoY + ranh giới TT200/TT99) | ✅ Đã có `financial_facts.parquet` + `config/metric_definitions.yaml` (có sẵn `statement_type`, `tt200_code`, `tt99_code`, `comparable_across_taxonomy`) | Chưa có route đọc `financial_facts` theo `statement_type` | **Must** |
| **G3 (F3)** | **Câu diễn giải rủi ro bằng tiếng Việt** bên dưới biểu đồ SHAP (quy tắc cố định, không dùng LLM) | ✅ Đã có `shap_top_drivers` (`display_name_vn`, `value`, `peer_median`, `shap_delta`) | Có thể sinh đồng bộ ở **BE** (đảm bảo nhất quán cho cả API/PDF) hoặc helper thuần ở FE; khuyến nghị đặt hàm thuần ở BE + trả kèm hoặc dùng chung quy tắc | **Must** |
| **G4 (F13)** | **Từ điển chỉ số & 9 quy tắc cờ đỏ** (tooltip ⓘ + trang `/methodology`) | ✅ Đã có bảng định nghĩa 38 tỷ số + `metric_definitions.yaml` + metadata 9 red flags | Chưa có route trả metadata tĩnh (`display_name_vn`, công thức, nhóm, **chiều tốt/xấu `higher_is_better`**, ngưỡng) | **Must** |
| **G5 (F11)** | **Bản đồ rủi ro theo ngành (Heatmap trang chủ)** + danh sách "Cần chú ý" | ✅ Đã có trong `financial_ratios` + `ml_scores` + `accounting_red_flags` + `issuers` | Chưa có route tổng hợp theo `icb_code_level2` (hiện phải tải toàn bộ screener nhiều trang) | **Should** |
| **G6 (F9/F10)** | **Lịch sử kiểm toán, ý kiến kiểm toán & điều chỉnh hồi tố** trên tab Chất lượng LN | ✅ `filings_manifest.parquet` ĐÃ CÓ `audit_status`, `audit_opinion`, `revision_no`, `revision_reason`, `published_at` | Chưa có route lộ `filings_manifest` theo `ticker` (và chưa tính tự động chênh lệch LN trước/sau kiểm toán nếu có nhiều `revision_no`) | **Should** |
| **G7 (F7)** | **Trang So sánh 2–5 DN cạnh nhau** (`/compare`) | ✅ Đã có trong `financial_ratios` + `ml_scores` + `industry_benchmarks` | FE có thể ghép từ `/profile` × N (với N ≤ 5 chấp nhận được ở v1), nhưng 1 endpoint `/compare` sẽ gọn và nhất quán cùng kỳ | **Should** |
| **G8 (F6)** | **Biến động kỳ mới** cho Watchlist cục bộ (DN nào đổi vùng Altman, thêm red flag, xác suất tăng mạnh so với kỳ trước) | ✅ Đã có nhiều kỳ trong `ml_scores` và `accounting_red_flags` | Chưa có route tính diff giữa kỳ mới nhất `T` và kỳ liền trước `T-1` theo danh sách `tickers` hoặc toàn thị trường | **Could** |
| **G9** | **Sàng lọc mở rộng (sắp xếp + tìm theo từ khóa + lọc theo mã ngành L3)** | ✅ Có sẵn cột trong `screen_market` | `/screener` hiện chưa có `sort_by`, `sort_dir`, `search` (lọc tên/mã), `min_distress_prob` (để lọc nhóm rủi ro cao!) | **Should** |
| **G10** | **Chọn danh sách kỳ khả dụng** cho `PeriodPicker` | ✅ `DashboardQueryService.list_available_periods()` đã viết sẵn! | Chưa có route trả danh sách `period_end_date` khả dụng (FE hiện phải đoán hoặc lấy từ response screener) | **Must** |

> [!TIP]
> Phát hiện lớn từ đọc mã nguồn thật:
> 1. **G1 (batch benchmarks)** và **G10 (`available_periods`)** là 2 thiếu hụt UX quan trọng mà nếu chỉ nhìn danh sách tên endpoint sẽ không thấy, nhưng khi vẽ wireframe lập tức lộ ra!
> 2. **G9 (`min_distress_prob`, `sort_by`, `sort_dir`)** rất quan trọng: `/screener` hiện chỉ có `max_distress_prob`, tức là lọc được DN *an toàn*, nhưng chưa lọc trực tiếp được "DN có xác suất kiệt quệ ≥ 20%" hay sắp xếp từ rủi ro cao xuống thấp!
> 3. **F2 (lịch sử điểm)** đã có sẵn trong `/profile` — đỡ 1 endpoint.

---

## 3. Đặc tả Hợp đồng API mới (OpenAPI-ready, chỉ đọc)

Mọi endpoint mới đặt dưới `/api/v2` (để giữ nguyên đúng 10 route chuẩn của `/api/v1` và đảm bảo tương thích ngược 100%), trả `ETag` + `Cache-Control: private, max-age=0, must-revalidate`, hỗ trợ `If-None-Match` → `304`, không chứa `NaN`/`Inf`.

### 3.1. `GET /api/v2/meta/periods` (Giải quyết G10)
Trả danh sách kỳ dữ liệu và danh mục ngành ICB L2 có trong snapshot hiện hành (để dựng `PeriodPicker` và dropdown ngành không cần hardcode).
```json
{
  "snapshot_id": "20261115T000000Z",
  "published_at": "2026-11-15T08:00:00Z",
  "available_periods": {
    "CONSOLIDATED": {
      "QUARTER": ["2026-09-30", "2026-06-30", "2026-03-31"],
      "ANNUAL": ["2025-12-31", "2024-12-31"]
    },
    "SEPARATE": { "QUARTER": ["..."], "ANNUAL": ["..."] }
  },
  "sectors_level2": [
    { "icb_code_level2": "1700", "icb_name_level2": "Tài nguyên cơ bản", "issuer_count": 24 }
  ]
}
```

### 3.2. `GET /api/v2/meta/dictionary` (Giải quyết G4 / F13)
Trả từ điển 38 chỉ số tài chính + 9 quy tắc cờ đỏ + mô tả ngắn phương pháp. Quan trọng nhất cho UI: trường **`direction`** (`"HIGHER_IS_BETTER" | "LOWER_IS_BETTER" | "NEUTRAL"`) để thành phần `PercentileBar` biết khi nào phải đảo thang màu xanh/đỏ!
```json
{
  "ratios": [
    {
      "ratio_name": "debt_to_equity",
      "display_name_vn": "Nợ vay / Vốn chủ sở hữu",
      "category": "LEVERAGE",
      "unit": "x",
      "format_type": "MULTIPLE",
      "direction": "LOWER_IS_BETTER",
      "formula_display": "Tổng nợ vay tài chính / Tổng vốn chủ sở hữu",
      "description_vn": "Đo lường mức độ phụ thuộc vào nguồn vốn vay so với vốn tự có.",
      "tt200_tt99_note": "Tương thích liên tục qua chuyển đổi TT200 → TT99."
    }
  ],
  "red_flags": [
    {
      "flag_code": "RF_08_NON_CORE_EARNINGS_DEPENDENCE",
      "title_vn": "Lợi nhuận phụ thuộc hoạt động ngoài cốt lõi",
      "default_severity": "HIGH",
      "methodology_ref": "Rouen, So & Wang (2021); Schilit et al. (2018) Shenanigan #3",
      "description_vn": "..."
    }
  ]
}
```

### 3.3. `GET /api/v2/companies/{ticker}/benchmarks/summary` (Giải quyết G1)
Trả nhanh một mảng các chỉ số của `ticker` tại một kỳ kèm `company_value`, `peer_median`, `peer_p25`, `peer_p75`, `percentile_rank`, `peer_group_level`, `peer_count`, `direction`, và `sparkline_8p` (8 kỳ gần nhất).
- Query params: `statement_scope`, `period_type`, `period_end_date?`, `metrics?` (mặc định trả bộ 12 chỉ số cốt lõi cho tab Tổng quan, hoặc `all` cho cả 38 chỉ số).
- Tác dụng: dựng toàn bộ bảng Traffic Light kiểu Stockopedia chỉ trong **1 HTTP request**.

### 3.4. `GET /api/v2/companies/{ticker}/statements` (Giải quyết G2 / F1)
Trả báo cáo tài chính chuẩn hóa nhiều kỳ từ `financial_facts.parquet` + `config/metric_definitions.yaml`.
- Query params: `statement_type` (`BALANCE_SHEET | INCOME_STATEMENT | CASH_FLOW`), `statement_scope`, `period_type`, `periods_limit` (mặc định `8`, tối đa `20`).
- Response:
```json
{
  "snapshot_id": "...",
  "ticker": "HPG",
  "statement_type": "INCOME_STATEMENT",
  "unit_display": "VND",
  "periods": [
    { "period_end_date": "2026-09-30", "label": "Q3/2026", "taxonomy_version": "TT99", "audit_status": "UNAUDITED" }
  ],
  "rows": [
    {
      "metric_id": "IS_NET_REVENUE",
      "display_name_vn": "Doanh thu thuần",
      "tt200_code": "10",
      "tt99_code": "10",
      "level": 0,
      "is_total": true,
      "comparable_across_taxonomy": true,
      "values": { "2026-09-30": 35120000000000.0, "2026-06-30": 34050000000000.0 }
    }
  ]
}
```
- **Tỷ trọng (common-size) và Tăng trưởng YoY**: FE tính trực tiếp từ bảng `rows` trả về (chia cho dòng gốc `IS_NET_REVENUE` hoặc `BS_TOTAL_ASSETS`, hoặc so cùng kỳ năm trước), không cần gọi lại API khi người dùng bấm toggle!

### 3.5. `GET /api/v2/companies/{ticker}/filings` (Giải quyết G6 / F9–F10)
Đọc `filings_manifest.parquet` theo `ticker`: trả danh sách hồ sơ theo thời gian gồm `period_end_date`, `statement_type`, `audit_status`, `audit_opinion`, `published_at`, `revision_no`, `revision_reason`, kèm cờ `has_restatement: bool` (khi `revision_no > 1`).

### 3.6. `GET /api/v2/sectors/risk-summary` (Giải quyết G5 / F11)
Tổng hợp theo `icb_code_level2` tại kỳ chọn:
- `issuer_count`, `distress_zone_count`, `grey_zone_count`, `safe_zone_count`, `beneish_flagged_count`, `median_distress_prob`, `avg_red_flags`, `total_debt_sum`.
- Kèm `top_deteriorating_issuers` (top 10 DN chuyển vùng xấu đi hoặc tăng `calibrated_distress_prob` mạnh nhất giữa kỳ hiện tại và kỳ liền trước) để hiển thị khối "Cần chú ý" trên Trang chủ.

### 3.7. Mở rộng `GET /api/v1/screener` (Giải quyết G9 — tương thích ngược 100%)
Thêm các query params tùy chọn (không làm vỡ client cũ / Streamlit):
- `q: str | None` (tìm mã hoặc tên DN không phân biệt hoa/thường)
- `min_distress_prob: float | None` (`0.0..1.0`)
- `min_red_flags: int | None` (`>= 0`)
- `sort_by: Literal["ticker","calibrated_distress_prob","altman_z_double_prime","piotroski_f_score","beneish_m8_score","red_flag_count","roe_total","debt_to_equity"]`
- `sort_dir: Literal["asc","desc"]`

### 3.8. `GET /api/v2/market/changes` (Giải quyết G8 / F6 — giữ BE chỉ đọc)
- Query params: `tickers` (danh sách ngăn dấu phẩy, tối đa 50 mã từ Watchlist cục bộ; nếu bỏ trống thì trả top 30 toàn thị trường), `statement_scope`, `period_type`.
- Trả danh sách các thay đổi giữa kỳ mới nhất `T` và kỳ liền trước `T-1`: `altman_zone_prev → altman_zone_curr`, `distress_prob_delta`, `new_red_flag_codes[]`, `resolved_red_flag_codes[]`.

---

## 4. Quy tắc sinh diễn giải tiếng Việt cố định (G3 / F3 — Không dùng LLM)

Dùng hàm thuần (deterministic template) cho `shap_top_drivers`:
- **Yếu tố tăng rủi ro** (`shap_delta > 0`):
  - Nếu có `value` và `peer_median`: `"{display_name_vn} ({value_fmt}) bất lợi hơn trung vị ngành ({median_fmt}), làm tăng xác suất kiệt quệ (+{delta_pp} đ%)."`
  - Nếu thiếu `peer_median`: `"{display_name_vn} ({value_fmt}) đóng góp làm tăng xác suất kiệt quệ (+{delta_pp} đ%)."`
- **Yếu tố giảm rủi ro** (`shap_delta < 0`):
  - `"{display_name_vn} ({value_fmt}) tích cực hơn trung vị ngành ({median_fmt}), giúp giảm rủi ro ({delta_pp} đ%)."`
- **Ưu điểm:** 100% tái lập, 0% ảo giác, kiểm thử unit test được, giải thích rõ ràng khi bảo vệ đồ án.

---

## 5. Chiến lược Song song hóa FE ↔ BE (Contract-First)

1. Khởi tạo FE chạy được ngay trên **10 route hiện có** (dùng `openapi-typescript` sinh `src/types/api.generated.ts` từ `http://127.0.0.1:8000/openapi.json`).
2. Với 7 route/mở rộng mới (G1–G10), định nghĩa trước TypeScript interfaces + **MSW handlers** có dữ liệu mẫu thực tế (lấy từ 3 mã mẫu như HPG, VNM, NVL) để FE không bị chặn.
3. Khi từng endpoint BE mới hoàn thành và có pytest pass, chỉ cần tắt handler MSW của endpoint đó và chạy lại `npm run gen:api-types` để khớp 100% với Pydantic schema thật.
