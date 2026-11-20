---
name: FinIntel
version: 0.1.0
description: Giao diện phân tích rủi ro tài chính và kế toán điều tra cho DN niêm yết Việt Nam. Phong cách "báo cáo phân tích chuyên nghiệp" — nghiêm túc, dữ liệu là trung tâm, không trang trí.
locale: vi-VN
colors:
  # Nền và chữ (light mode mặc định)
  canvas: "#F7F6F3"          # nền trang, trắng ngà ấm (không trắng tinh)
  surface: "#FFFFFF"         # thẻ, bảng
  surface-muted: "#EFEDE8"   # hàng xen kẽ, vùng phụ
  border: "#DAD7D0"
  border-strong: "#B9B5AC"
  ink: "#1A1D21"             # chữ chính
  ink-muted: "#5B6068"       # chữ phụ, nhãn
  ink-subtle: "#868B93"      # chú thích, nguồn
  # Màu nhấn duy nhất — xanh mực, dùng cho liên kết, trạng thái chọn, focus
  accent: "#1F4E79"
  accent-hover: "#173D60"
  accent-soft: "#E3ECF5"
  focus-ring: "#1F4E79"
  # Hệ HƯỚNG (biến động số liệu) — theo quy ước thị trường VN
  up: "#0E8A4B"              # tăng
  down: "#C7362F"            # giảm
  unchanged: "#B58A00"       # không đổi / tham chiếu
  # Hệ ĐÁNH GIÁ (tốt/xấu) — tách khỏi hệ hướng
  risk-low: "#2E7D5B"        # An toàn
  risk-low-soft: "#E4F1EA"
  risk-mid: "#B7791F"        # Vùng xám / cần chú ý
  risk-mid-soft: "#F8EEDC"
  risk-high: "#A61B1B"       # Nguy cơ kiệt quệ / nghiêm trọng
  risk-high-soft: "#F6E1DF"
  neutral-flag: "#5B6068"    # Không đủ dữ liệu
  # Thang ngũ phân vị so ngành (Q1 tệ nhất → Q5 tốt nhất), dùng cho thanh percentile
  quintile-1: "#A61B1B"
  quintile-2: "#D9822B"
  quintile-3: "#A8A39A"
  quintile-4: "#5E9E7E"
  quintile-5: "#2E7D5B"
  # Biểu đồ: chuỗi tối đa 5 màu, ưu tiên DN đang xem = accent, ngành = xám
  chart-company: "#1F4E79"
  chart-peer-median: "#5B6068"
  chart-peer-band: "#DAD7D0"
  chart-series: ["#1F4E79", "#B7791F", "#2E7D5B", "#7A4E2D", "#5B6068"]
typography:
  font-sans: "'IBM Plex Sans', system-ui, sans-serif"     # hỗ trợ tiếng Việt, cảm giác báo cáo/kỹ thuật
  font-mono: "'IBM Plex Mono', ui-monospace, monospace"   # mã chỉ tiêu, mã ngành, ID
  font-serif: "'Source Serif 4', Georgia, serif"          # chỉ dùng cho tiêu đề báo cáo in/PDF
  numeric: "tabular-nums lining-nums"                     # BẮT BUỘC cho mọi con số
  scale:                                                   # px / line-height
    display: [28, 36]     # tên DN trên hồ sơ
    h1: [22, 30]
    h2: [18, 26]
    h3: [15, 22]
    body: [14, 21]        # mặc định ứng dụng dày dữ liệu
    small: [13, 19]
    caption: [12, 16]     # nguồn, chú thích
    kpi: [26, 32]         # số lớn trong thẻ kết luận
  weights: { regular: 400, medium: 500, semibold: 600 }   # không dùng 700+ ngoài tên DN
spacing:
  base: 4
  scale: [0, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64]
  page-gutter: 24
  max-content-width: 1440
radius:
  none: 0
  sm: 2
  md: 4                 # thẻ, input, nút — bo nhẹ, không bo tròn kiểu app tiêu dùng
  pill: 999             # chỉ cho badge
elevation:
  card: "0 0 0 1px #DAD7D0"                 # viền, không đổ bóng lớn
  popover: "0 4px 16px rgba(26,29,33,0.12)"
motion:
  duration-fast: 120ms
  duration-base: 180ms
  easing: "cubic-bezier(0.2, 0, 0, 1)"
  reduce-motion: respect
density:
  table-row-compact: 32
  table-row-comfortable: 40
breakpoints: { sm: 640, md: 768, lg: 1024, xl: 1280, 2xl: 1536 }
---

# DESIGN.md — FinIntel Web

> Nguồn sự thật về **giao diện**. Mọi agent/người viết UI trong `frontend/` phải đọc file này và [`PRODUCT.md`](./PRODUCT.md) trước.
> Token ở phần YAML phía trên là giá trị chuẩn; `src/app/globals.css` ánh xạ chúng thành biến CSS cho Tailwind/shadcn.

## 1. Tinh thần thiết kế

**"Báo cáo phân tích của một công ty chứng khoán uy tín, nhưng tương tác được."**
Tham chiếu tinh thần: trang StockReport của Stockopedia (một trang, so ngành bằng thanh), thẻ cảnh báo của GuruFocus (điểm + dải lịch sử + câu kết luận), khung ứng dụng của Koyfin (thanh bên, Ctrl+K, bảng dày), cách Việt hóa của Simplize (giải nghĩa tại chỗ).
Chúng ta **học mẫu**, không sao chép thương hiệu, nội dung hay mã nguồn của họ.

Ba từ khóa: **Nghiêm túc · Rõ ràng · Truy được.**

## 2. Luật bắt buộc

### 2.1. Số liệu tài chính
1. Định dạng `vi-VN`: dấu chấm ngăn nghìn, dấu phẩy thập phân — `1.234.567,8`. Dùng `Intl.NumberFormat('vi-VN')` qua helper `formatNumber`, **không tự nối chuỗi**.
2. Đơn vị ghi ở **tiêu đề cột/bảng/thẻ** (`Doanh thu thuần (tỷ đồng)`), không lặp trong từng ô.
3. Tiền tệ trong bảng BCTC: quy đổi về **tỷ đồng**, 1 chữ số thập phân; số âm trong **ngoặc đơn, màu chữ thường**: `(1.250,3)`.
4. Tỷ lệ, biến động: dấu `−` (U+2212) và màu hệ hướng: `−3,2%`.
5. Giá trị 0 → `–`. Không có dữ liệu → `N/A` kèm tooltip nêu lý do (thiếu chỉ tiêu, mẫu số âm, không áp dụng chuẩn mực…). Không bao giờ hiển thị `NaN`, `null`, `undefined`, `Infinity`.
6. Mọi con số dùng `font-variant-numeric: tabular-nums` và **căn phải**; nhãn căn trái.
7. Luôn ghi **kỳ** và **phạm vi**: `Q3/2026 · Hợp nhất`. Khi so sánh qua kỳ chuyển chuẩn mực, ghi `TT200 → TT99`.
8. Dòng tổng: viền trên; tổng cuối: gạch dưới kép.

### 2.2. Màu
1. **Một màu nhấn duy nhất** (`accent`, xanh mực). Không gradient. Không màu neon.
2. **Hệ hướng** (`up`/`down`/`unchanged`) chỉ cho biến động số liệu, theo quy ước thị trường VN: xanh tăng, đỏ giảm, vàng không đổi.
3. **Hệ đánh giá** (`risk-*`) cho tốt/xấu. "Tăng" không mặc định là tốt: nợ vay tăng, xác suất kiệt quệ tăng → dùng `risk-high`, không dùng `up`.
4. Không dùng tím và xanh lơ (người dùng VN liên tưởng giá trần/sàn).
5. **Không chỉ dùng màu để truyền đạt**: luôn kèm chữ và biểu tượng (✓ An toàn, ▲ Vùng xám, ■ Nguy cơ). Tương phản tối thiểu WCAG AA (4.5:1 cho chữ thường).

### 2.3. Chữ
1. IBM Plex Sans cho giao diện, IBM Plex Mono cho mã (mã chỉ tiêu `BS_TOTAL_ASSETS` chỉ xuất hiện ở trang Nguồn dữ liệu).
2. Không chữ in hoa giãn cách để trang trí. Nhãn viết hoa chữ cái đầu câu.
3. Không cắt cụt tên DN tiếng Việt giữa từ; khi buộc phải cắt, có tooltip tên đầy đủ.

### 2.4. Biểu đồ
1. Tiêu đề biểu đồ **nói kết luận**, không chỉ tên chỉ số: *"Biên lãi gộp thấp hơn trung vị ngành 5 quý liên tiếp"*. Tên chỉ số đặt ở phụ đề.
2. Mỗi biểu đồ có dòng nguồn: `Nguồn: BCTC hợp nhất đã công bố · FinIntel tính toán`.
3. Cấm: biểu đồ tròn/donut cho so sánh, 3D, hai trục Y khác đơn vị, quá 5 chuỗi màu, chú giải tách xa dữ liệu (ưu tiên nhãn trực tiếp).
4. DN đang xem = `chart-company`; trung vị ngành = `chart-peer-median` (nét đứt); dải P25–P75 = `chart-peer-band`.
5. Khi nhóm so sánh là `REGIME_FALLBACK` hoặc có dưới 5 DN: đoạn đó vẽ nền sọc + chú thích cảnh báo.

### 2.5. Bố cục
1. Khung: thanh bên trái cố định (thu gọn được) + thanh trên (tìm kiếm Ctrl+K, chọn kỳ, ngày dữ liệu). Nội dung tối đa 1440px.
2. Hồ sơ DN: **Kết luận → Lý do → Bằng chứng → Nguồn**. Khối kết luận luôn nằm trong màn hình đầu tiên ở 1366×768.
3. Thẻ: viền 1px, bo 4px, không đổ bóng lớn. Không "lưới 3 thẻ giống hệt có icon tròn".
4. Mật độ: mặc định *compact* cho bảng (hàng 32px), có công tắc *thoải mái* (40px).

### 2.6. Ngôn ngữ
1. Tiếng Việt nghiệp vụ, câu ngắn, chủ động. Không marketing ("Siêu việt", "Thông minh").
2. Tên phương pháp có trích dẫn: *Altman (1968, biến thể Z″ thị trường mới nổi)*, *Piotroski (2000)*, *Beneish (1999, 8 biến)*.
3. Mỗi thuật ngữ có biểu tượng ⓘ mở định nghĩa ngắn + liên kết "Phương pháp tính".
4. Chân trang mọi trang: *"Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư."*

## 3. Danh sách CẤM (chống "giao diện kiểu AI")

| Cấm | Lý do |
|---|---|
| Gradient (đặc biệt tím → xanh), nền phát sáng, orb mờ | Dấu hiệu template AI, giảm độ tin cậy tài chính |
| Glassmorphism (`backdrop-blur`, thẻ trong suốt viền sáng) | Như trên; giảm tương phản số liệu |
| Biểu tượng ✨, nhãn "AI", "Smart", "Powered by AI" | Sản phẩm trình bày **mô hình định lượng có phương pháp**, không quảng cáo AI |
| Hero section, khẩu hiệu, số liệu giả ("10.000+ nhà đầu tư tin dùng") | Đây là công cụ làm việc, không phải landing page |
| `hover:scale-*`, nảy, xoay, hiệu ứng vào trang dài | Gây xao nhãng khi đọc số |
| Emoji trong giao diện | Không chuyên nghiệp |
| Bo góc lớn (≥12px) cho thẻ dữ liệu | Cảm giác app tiêu dùng |
| Màu đỏ/xanh rực cho cả mảng nền lớn | Mỏi mắt, gây hoảng |
| Spinner toàn trang | Dùng skeleton đúng hình dạng nội dung |

## 4. Thành phần đặc thù (phải dùng lại, không tự chế biến thể)

| Thành phần | Mô tả ngắn | Trạng thái bắt buộc |
|---|---|---|
| `AppShell` | Thanh bên + thanh trên + vùng nội dung + chân trang miễn trừ | thu gọn/mở rộng, mobile drawer |
| `CommandSearch` | Ctrl+K, tìm theo mã, tên có/không dấu | rỗng, không tìm thấy, điều hướng bàn phím |
| `PeriodPicker` | Kỳ + Quý/Năm + Hợp nhất/Riêng lẻ | không có dữ liệu cho kỳ |
| `DataStamp` | "Dữ liệu đến {ngày} · Nguồn: BCTC công bố" | — |
| `VerdictCard` | Điểm chính + nhãn đánh giá + ngưỡng + delta so kỳ trước + ⓘ | thiếu dữ liệu, không áp dụng |
| `RiskBadge` | An toàn / Vùng xám / Nguy cơ / Không đủ dữ liệu — chữ + biểu tượng + màu | 4 trạng thái |
| `ScoreDots` | Piotroski 0–9 dạng 9 chấm | thiếu thành phần (`valid_components_count < 9`) |
| `PercentileBar` | Thanh 0–100, vạch tứ phân vị, độ dài = percentile, màu = ngũ phân vị, tooltip "hạng X/N trong {nhóm}" | chiều "cao là xấu" (đảo màu), nhóm nhỏ |
| `MetricRow` | Tên chỉ số + ⓘ + giá trị + trung vị ngành + PercentileBar + sparkline | N/A có lý do |
| `ExplainWaterfall` | Waterfall đóng góp yếu tố (SHAP) + câu diễn giải theo quy tắc | không có mô hình cho DN/kỳ |
| `RedFlagList` | Mã, tiêu đề tiếng Việt, mức độ, giải thích, bằng chứng, liên kết nguồn | không có cờ (trạng thái tích cực rõ ràng) |
| `PeerBandChart` | Dải P25–P75 + trung vị + DN | đoạn fallback sọc |
| `FinancialTable` | BCTC nhiều kỳ, cột kỳ ghim, dòng tổng, ngoặc âm | tỷ trọng/tăng trưởng |
| `SourceDrawer` | Ngăn kéo "Số này từ đâu?": công thức, tử/mẫu, chỉ tiêu, kỳ, chuẩn mực, ngày công bố, mã hồ sơ | thiếu nguồn |
| `ScenarioControl` | Thanh trượt + ô nhập số + mốc + giới hạn hợp lệ của API | ngoài khoảng, đang tính |
| `EmptyState` / `ErrorState` | Giải thích + hành động tiếp theo; lỗi đã làm sạch (không lộ đường dẫn, traceback) | — |

## 5. Trạng thái màn hình (mọi khối dữ liệu đều phải có đủ 4)

`loading` (skeleton đúng hình) · `ready` · `empty` (giải thích vì sao + gợi ý) · `error` (thông báo đã làm sạch + nút thử lại).

## 6. Khả năng tiếp cận

Điều hướng bàn phím đầy đủ (Tab, mũi tên trong bảng/menu, Esc đóng ngăn kéo), `focus-visible` dùng `focus-ring` 2px, nhãn `aria-label` cho nút chỉ có biểu tượng, bảng có `<th scope>`, biểu đồ có bảng dữ liệu thay thế ("Xem dạng bảng"), tôn trọng `prefers-reduced-motion`.

## 7. Cổng chất lượng trước khi coi một màn hình là xong

- [ ] Đúng luật số liệu 2.1 (kiểm tra bằng dữ liệu âm, 0, null, rất lớn).
- [ ] Đủ 4 trạng thái (mục 5).
- [ ] Không vi phạm danh sách CẤM (mục 3).
- [ ] Khối kết luận nằm trong màn hình đầu ở 1366×768; không tràn ngang ở 375px.
- [ ] axe: 0 lỗi nghiêm trọng; Tab đi hết các điều khiển.
- [ ] Mọi con số chính có đường tới `SourceDrawer` hoặc trang Phương pháp.
- [ ] Ảnh chụp màn hình desktop + mobile đính kèm khi review.
