---
name: FinIntel
version: 2.0.0
description: >-
  Giao diện phân tích rủi ro tài chính và kế toán điều tra cho DN phi tài chính niêm yết Việt Nam.
  Phong cách "Modern Institutional FinTech 2026" (Cool Slate Light Mode + High-Contrast Dark Mode) —
  nghiêm túc, mật độ thông tin cao, truy được nguồn gốc, tuyệt đối không "lậm AI".
locale: vi-VN
themes:
  default: light
  supported: [light, dark, system]
  storage_key: "finintel:theme:v1"
colors:
  light:
    canvas: "#F8FAFC"          # Cool Slate nền trang
    surface: "#FFFFFF"         # Thẻ, bảng, thanh công cụ
    surface-muted: "#F1F5F9"   # Hàng xen kẽ, vùng phụ
    border: "#E2E8F0"
    border-strong: "#CBD5E1"
    ink: "#0F172A"             # Chữ chính (17.85:1 trên surface)
    ink-muted: "#475569"       # Chữ phụ, nhãn (7.58:1 trên surface)
    ink-subtle: "#5D6C82"      # Chú thích, nguồn (5.34:1 trên surface, 4.87:1 trên surface-muted)
    # Một màu nhấn duy nhất — xanh mực sâu
    accent: "#1E3A5F"          # 11.50:1 trên surface
    accent-hover: "#162D4A"
    accent-soft: "#E8EFF7"
    primary: "#1E3A5F"
    primary-foreground: "#FFFFFF"
    focus-ring: "#1E3A5F"
    # Hệ HƯỚNG (biến động số liệu) — quy ước thị trường VN (đạt >= 4.5:1)
    up: "#0D7A42"              # 5.41:1 trên surface
    down: "#C7362F"            # 5.25:1 trên surface
    unchanged: "#9A6E00"       # 4.56:1 trên surface
    # Hệ ĐÁNH GIÁ (tốt/xấu) — tách khỏi hệ hướng
    risk-low: "#1F6E4E"        # 5.45:1 trên risk-low-soft
    risk-low-soft: "#E6F4ED"
    risk-mid: "#9A5B13"        # 4.79:1 trên risk-mid-soft
    risk-mid-soft: "#FBF0D9"
    risk-high: "#A61B1B"       # 6.46:1 trên risk-high-soft
    risk-high-soft: "#FBEAE9"
    neutral-flag: "#475569"
    # Thang ngũ phân vị so ngành (Q1 tệ nhất -> Q5 tốt nhất)
    quintile-1: "#A61B1B"
    quintile-2: "#C96B18"
    quintile-3: "#64748B"
    quintile-4: "#3B8765"
    quintile-5: "#1F6E4E"
    # Biểu đồ: chuỗi tối đa 5 màu, không tím/xanh lơ trần-sàn
    chart-company: "#1E3A5F"
    chart-peer-median: "#475569"
    chart-peer-band: "rgba(71, 85, 105, 0.14)"
    chart-series: ["#1E3A5F", "#1F6E4E", "#9A5B13", "#A61B1B", "#475569"]
  dark:
    canvas: "#0B0F14"          # Nền terminal tối tương phản cao
    surface: "#111827"         # Thẻ, bảng, thanh công cụ
    surface-muted: "#1A2332"   # Hàng xen kẽ, vùng phụ
    border: "#232D3F"
    border-strong: "#334155"
    ink: "#F1F5F9"             # Chữ chính (16.19:1 trên surface)
    ink-muted: "#94A3B8"       # Chữ phụ, nhãn (6.92:1 trên surface)
    ink-subtle: "#8B9BB2"      # Chú thích, nguồn (6.28:1 trên surface, 5.58:1 trên surface-muted)
    # Màu nhấn xanh mực sáng cho nền tối & nút đặc riêng biệt đạt >= 4.5:1
    accent: "#669DF6"          # 6.52:1 trên surface, 5.66:1 trên accent-soft
    accent-hover: "#93C5FD"
    accent-soft: "#17253B"
    primary: "#1D4ED8"         # 6.70:1 với #FFFFFF
    primary-foreground: "#FFFFFF"
    focus-ring: "#669DF6"
    # Hệ HƯỚNG (biến động số liệu) trên nền tối
    up: "#34D399"              # 9.23:1 trên surface
    down: "#F87171"            # 6.41:1 trên surface
    unchanged: "#FBBF24"       # 10.63:1 trên surface
    # Hệ ĐÁNH GIÁ (tốt/xấu) trên nền tối
    risk-low: "#34D399"        # 7.60:1 trên risk-low-soft
    risk-low-soft: "#0F2E24"
    risk-mid: "#FBBF24"        # 9.00:1 trên risk-mid-soft
    risk-mid-soft: "#33240B"
    risk-high: "#F87171"       # 5.81:1 trên risk-high-soft
    risk-high-soft: "#3B1518"
    neutral-flag: "#94A3B8"
    quintile-1: "#F87171"
    quintile-2: "#FB923C"
    quintile-3: "#94A3B8"
    quintile-4: "#6EE7B7"
    quintile-5: "#34D399"
    chart-company: "#669DF6"
    chart-peer-median: "#94A3B8"
    chart-peer-band: "rgba(148, 163, 184, 0.16)"
    chart-series: ["#669DF6", "#34D399", "#FBBF24", "#F87171", "#94A3B8"]
typography:
  self_hosted: ["@fontsource/ibm-plex-sans", "@fontsource/ibm-plex-mono"]
  font-sans: "'IBM Plex Sans', system-ui, -apple-system, sans-serif"
  font-mono: "'IBM Plex Mono', ui-monospace, SFMono-Regular, monospace"
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
  sm: 4
  md: 8                   # thẻ, input, nút — chuẩn Modern Institutional 2026
  lg: 8
  pill: 999               # chỉ cho badge
elevation:
  xs-light: "0 1px 2px 0 rgba(15, 23, 42, 0.05)"
  xs-dark: "0 1px 2px 0 rgba(0, 0, 0, 0.35)"
  popover-light: "0 4px 16px rgba(15, 23, 42, 0.12)"
  popover-dark: "0 4px 16px rgba(0, 0, 0, 0.48)"
motion:
  ui-duration: 150ms
  ui-easing: "cubic-bezier(0.2, 0, 0, 1)"
  chart-duration: 250ms
  chart-easing: "cubicOut"
  reduce-motion: respect
density:
  table-row-compact: 32
  table-row-comfortable: 40
breakpoints: { sm: 640, md: 768, lg: 1024, xl: 1280, 2xl: 1536 }
---

# DESIGN.md — FinIntel Web (v2.0)

> Nguồn sự thật về **giao diện** theo `ADR-UI-01`. Mọi agent/người viết UI trong `frontend/` phải đọc file này và [`PRODUCT.md`](./PRODUCT.md) trước khi sửa code.
> Token ở phần YAML phía trên là giá trị chuẩn; `src/app/globals.css` ánh xạ thành biến CSS Light/Dark cho Tailwind/shadcn và `src/lib/echarts-theme.ts` đồng bộ cho toàn bộ biểu đồ ECharts.

## 1. Tinh thần thiết kế ("Modern Institutional FinTech 2026")

**"Bàn làm việc phân tích định lượng và kế toán điều tra chuẩn định chế tài chính — sắc nét, điềm tĩnh, truy được từng con số."**
Tham chiếu tinh thần: mật độ thông tin rõ ràng kiểu Koyfin / Linear / Stripe Dashboard, kết hợp khả năng so ngành kiểu Stockopedia và Việt hóa trực quan kiểu Simplize.
Chúng ta **học mẫu**, không sao chép thương hiệu, nội dung hay mã nguồn của họ.

Ba từ khóa: **Nghiêm túc · Rõ ràng · Truy được.**

## 2. Luật bắt buộc

### 2.1. Số liệu tài chính
1. Định dạng `vi-VN`: dấu chấm ngăn nghìn, dấu phẩy thập phân — `1.234.567,8`. Dùng `Intl.NumberFormat('vi-VN')` qua helper `src/lib/format.ts`, **không tự nối chuỗi**.
2. Đơn vị ghi ở **tiêu đề cột/bảng/thẻ** (`Doanh thu thuần (tỷ đồng)`), không lặp trong từng ô.
3. Tiền tệ trong bảng BCTC: quy đổi về **tỷ đồng**, 1 chữ số thập phân; số âm trong **ngoặc đơn, màu chữ thường**: `(1.250,3)`.
4. Tỷ lệ, biến động: dấu `−` (U+2212) và màu hệ hướng: `−3,2%`.
5. Giá trị 0 → `–`. Không có dữ liệu → `N/A` kèm tooltip nêu lý do. Không bao giờ hiển thị `NaN`, `null`, `undefined`, `Infinity`.
6. Mọi con số dùng `font-variant-numeric: tabular-nums lining-nums` (class `.num`) và **căn phải**; nhãn căn trái.
7. Luôn ghi **kỳ** và **phạm vi**: `Q3/2026 · Hợp nhất`. Khi so sánh qua kỳ chuyển chuẩn mực, ghi `TT200 → TT99`.
8. Dòng tổng: viền trên; tổng cuối: gạch dưới kép.

### 2.2. Màu & Chế độ Sáng/Tối (Light / Dark / System)
1. **Cool Slate Light Mode (mặc định)** (`#F8FAFC` / `#FFFFFF`, viền `#E2E8F0`, chữ `#0F172A`) và **High-Contrast Dark Mode** (`#0B0F14` / `#111827`, viền `#232D3F`, chữ `#F1F5F9`).
2. **Không flash trắng (Zero-FOUC):** Script đồng bộ trong `<head>` đọc `finintel:theme:v1` và áp dụng `.dark` + `color-scheme` trước khi trình duyệt paint frame đầu tiên; người dùng có thể chuyển **Sáng / Tối / Hệ thống** ngay trên thanh công cụ trên cùng.
3. **Một họ màu nhấn xanh mực** (`accent` cho chữ/link/đường chính, `primary` cho nút đặc). Không gradient. Không màu neon.
4. **Hệ hướng** (`up`/`down`/`unchanged`) chỉ cho biến động số liệu, theo quy ước thị trường VN: xanh tăng, đỏ giảm, vàng không đổi.
5. **Hệ đánh giá** (`risk-*`) cho tốt/xấu. Nợ vay tăng hoặc xác suất kiệt quệ tăng → dùng `risk-high`, không dùng `up`.
6. **Cấm màu tím và xanh lơ (violet/purple/cyan)** trên biểu đồ và bảng vì nhà đầu tư Việt Nam liên tưởng tới giá trần/sàn.
7. **Tương phản tối thiểu WCAG AA (`>= 4.5:1`)** cho toàn bộ cặp chữ/nền ở cả Light Mode lẫn Dark Mode, luôn kèm chữ và biểu tượng (✓ An toàn, ▲ Vùng xám, ■ Nguy cơ).

### 2.3. Chữ & Tự host Font (Offline-Ready)
1. **Tự host `@fontsource/ibm-plex-sans` (400, 500, 600) và `@fontsource/ibm-plex-mono` (400, 500)** để `next build` chạy xanh 100% kể cả khi offline/air-gapped (không gọi `fonts.googleapis.com`, không dùng `Source Serif 4`).
2. Không chữ in hoa giãn cách để trang trí. Nhãn viết hoa chữ cái đầu câu.
3. Không cắt cụt tên DN tiếng Việt giữa từ; khi buộc phải cắt, có tooltip tên đầy đủ.

### 2.4. Biểu đồ (ECharts Unified Theme)
1. Mọi biểu đồ dùng chung `src/lib/echarts-theme.ts` và bọc qua `EChartBox` — **tuyệt đối không hardcode mã màu hex** trong từng file chart.
2. Animation chuẩn `250ms` (`cubicOut`) và tự động tắt (`animation: false`, `0ms`) khi người dùng bật `@media (prefers-reduced-motion: reduce)`.
3. Tiêu đề biểu đồ **nói kết luận**, phụ đề ghi chỉ số/đơn vị, chân thẻ ghi `Nguồn: BCTC đã công bố · FinIntel tính toán`.
4. **Cấm:** biểu đồ tròn/donut cho so sánh, 3D, **hai trục Y khác đơn vị**, quá 5 chuỗi màu, chú giải tách xa dữ liệu. Nếu cần xem hai chỉ số khác đơn vị (như `Altman Z″` và `Xác suất kiệt quệ %`), **tách thành 2 biểu đồ đơn trục độc lập**.
5. DN đang xem = `chart-company`; trung vị ngành = `chart-peer-median` (nét đứt); dải P25–P75 = `chart-peer-band`. Nhóm so sánh thiếu DN (< 5 DN hoặc mở rộng chuẩn mực) phải vẽ vùng cảnh báo và chú thích rõ bằng tiếng Việt.

### 2.5. Bố cục & Hình khối
1. Khung: thanh bên trái cố định (thu gọn được) + thanh trên (tìm kiếm `Ctrl+K`, chọn kỳ, loại kỳ TTM/Năm, phạm vi Hợp nhất/Riêng lẻ, công tắc Sáng/Tối/Hệ thống). Nội dung tối đa `1440px`.
2. Hồ sơ DN: **Kết luận → Lý do → Bằng chứng → Nguồn**. Khối kết luận nằm trong màn hình đầu tiên ở `1366×768`.
3. Thẻ & nút: viền `1px`, bo góc **`8px` (`--radius-lg`)**, nâng nhẹ **`shadow-xs`**, chuyển trạng thái `150ms cubic-bezier(0.2, 0, 0, 1)`.
4. Mật độ: mặc định *compact* cho bảng (hàng `32px`), có công tắc *thoải mái* (`40px`).

### 2.6. Ngôn ngữ
1. Tiếng Việt nghiệp vụ, câu ngắn, chủ động. Không lộ token nội bộ (`BS_TOTAL_ASSETS`, `REGIME_FALLBACK`, `snapshot_id`, `.parquet`).
2. Tên phương pháp có trích dẫn: *Altman (1968, biến thể Z″ thị trường mới nổi)*, *Piotroski (2000)*, *Beneish (1999, 8 biến)*.
3. Chân trang mọi trang: *"Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư."*

## 3. Danh sách CẤM (Zero AI-Slop per `ADR-UI-01`)

| Cấm | Lý do |
|---|---|
| Gradient (đặc biệt tím → xanh), nền phát sáng, orb mờ | Dấu hiệu template AI, giảm độ tin cậy tài chính |
| Glassmorphism (`backdrop-blur`, thẻ trong suốt viền sáng) | Giảm tương phản số liệu, rối mắt |
| Biểu tượng ✨, nhãn "AI", "Smart", "Powered by AI" | Sản phẩm trình bày **mô hình định lượng có phương pháp**, không quảng cáo AI |
| Hero section, khẩu hiệu, số liệu giả ("10.000+ nhà đầu tư tin dùng") | Đây là công cụ làm việc định chế, không phải landing page |
| `hover:scale-*`, nảy, xoay, hiệu ứng vào trang dài | Gây xao nhãng khi đọc số |
| Emoji trong giao diện | Không chuyên nghiệp |
| Màu tím (`#6B4C9A`) và xanh lơ (cyan) trong biểu đồ/bảng | Nhà đầu tư VN liên tưởng giá trần/sàn |
| Hai trục Y khác đơn vị trên cùng một biểu đồ | Gây hiểu lầm quy mô biến động |
| Bo góc quá lớn (`>= 12px`) cho thẻ dữ liệu hoặc spinner toàn trang | Dùng `8px` và skeleton đúng hình dạng nội dung |

## 4. Thành phần đặc thù (phải dùng lại, không tự chế biến thể)

| Thành phần | Mô tả ngắn | Trạng thái bắt buộc |
|---|---|---|
| `AppShell` | Thanh bên + thanh trên (`Ctrl+K`, kỳ, công tắc Light/Dark/System) + chân trang miễn trừ | thu gọn/mở rộng, mobile drawer |
| `CommandSearch` | `Ctrl+K`, tìm theo mã, tên có/không dấu | rỗng, không tìm thấy, điều hướng bàn phím |
| `SourceDrawer` | Ngăn kéo "Số này từ đâu?": công thức, tử/mẫu, chỉ tiêu tiếng Việt, kỳ, TT96, mã hồ sơ | thiếu nguồn |
| `EChartBox` | Khung biểu đồ ECharts đồng bộ Light/Dark + bảng dữ liệu thay thế a11y + `250ms` reduced-motion | loading, empty, chart, table |
| `VerdictCard` / `RiskBadge` | Điểm chính + nhãn đánh giá (chữ + biểu tượng + màu) + ngưỡng + delta | thiếu dữ liệu, không áp dụng |
| `PercentileBar` / `PeerBandChart` | Thanh ngũ phân vị 0–100 & biểu đồ dải P25–P75 | chiều đảo ngược, nhóm nhỏ |
| `ExplainWaterfallChart` | Waterfall TreeSHAP + câu diễn giải theo quy tắc cố định | không có mô hình cho kỳ |
| `FinancialTable` | BCTC nhiều kỳ, cột kỳ ghim, dòng tổng, ngoặc âm | tỷ trọng/tăng trưởng |

## 5. Trạng thái màn hình & Khả năng tiếp cận

Mọi khối dữ liệu có đủ 4 trạng thái: `loading` (skeleton) · `ready` · `empty` · `error` (đã làm sạch).
Điều hướng bàn phím đầy đủ, `focus-visible` rõ `2px`, bảng có `<th scope>`, biểu đồ có nút `"Xem dạng bảng"`, tôn trọng `prefers-reduced-motion`, `axe-core` `0` lỗi nghiêm trọng ở cả **Light Mode** và **Dark Mode** trên `1440×900`, `1366×768`, `375×812`.
