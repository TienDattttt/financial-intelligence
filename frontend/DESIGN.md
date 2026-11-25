# FinIntel Design System v3.0 — Institutional FinTech Terminal

> Hệ thống thiết kế chuẩn hóa cho `frontend/` (Next.js 16 + Tailwind v4 + `@base-ui/react` + Apache ECharts 6).
> Định vị: **Institutional FinTech Terminal** — sắc nét, điềm tĩnh, mật độ thông tin cao, truy được nguồn gốc kế toán VAS (`TT200 -> TT99`), đạt WCAG 2.2 AA trên cả Light Mode và Dark Mode.

---

## 1. Nguyên Lý Thiết Kế Cốt Lõi

1. **Flat Hairline Grid (Không Card-in-Card):** Phân tách vùng bằng đường kẻ `1px` (`--border`) và 3 tầng bề mặt (`canvas → surface → surface-muted`). Không lồng nhiều khung viền vào nhau.
2. **Phân cấp 4 tầng rõ rệt:**
   - **(1) Executive Strip:** Dải kết luận & 4–5 KPI then chốt chia bằng `divide-x divide-border`.
   - **(2) Visual Breakdown:** Biểu đồ ECharts chính nằm ngay trong vùng nhìn đầu (`above the fold`).
   - **(3) Forensic Evidence:** Cờ đỏ VAS, giải thích TreeSHAP, trích dẫn PDF thuyết minh gốc.
   - **(4) Raw Ledger:** Bảng BCTC và tỷ số chuẩn mật độ `32px` (chế độ gọn) / `40px` (thoải mái).
3. **Kỷ luật số liệu Việt Nam (`vi-VN`):**
   - 100% số dùng `.num` (`font-variant-numeric: tabular-nums lining-nums`), căn phải.
   - Dấu phẩy `,` cho thập phân, dấu chấm `.` cho hàng nghìn (kể cả trục & tooltip ECharts qua `formatChartNumber` / `formatChartPercent`).
   - Số âm BCTC đặt trong ngoặc đơn `(1.250,3)`, tỷ lệ âm dùng dấu trừ chuẩn `−` (`U+2212`), thiếu dữ liệu ghi `N/A`.
   - Luôn ghi rõ đơn vị (`tỷ đồng`, `%`, `lần`, `ngày`) ở tiêu đề cột hoặc tiêu đề KPI.

---

## 2. Hệ Token Màu Sắc (Light & Dark Mode — WCAG AA/AAA)

| Token CSS | Light Mode | Dark Mode | Vai trò |
|---|---|---|---|
| `--canvas` | `#F8FAFC` | `#0B0F14` | Nền toàn trang |
| `--surface` | `#FFFFFF` | `#11161F` | Bề mặt panel, bảng, thanh công cụ |
| `--surface-muted` | `#F1F5F9` | `#171E2B` | Header bảng, chip tĩnh, hàng xen kẽ |
| `--surface-hover` | `#E9EFF6` | `#1E293B` | Trạng thái hover (cấm hardcode hex) |
| `--border-color` | `#E2E8F0` | `#222B3A` | Đường kẻ `1px` hairline chủ đạo |
| `--border-strong-color` | `#CBD5E1` | `#334155` | Viền input, nút phụ, dòng tổng cộng BCTC |
| `--ink` | `#0F172A` | `#F1F5F9` | Chữ và số liệu chính |
| `--ink-muted` | `#475569` | `#94A3B8` | Nhãn phụ, tiêu đề cột |
| `--ink-subtle` | `#5B6B82` | `#8B9BB2` | Đơn vị, chú thích nguồn |
| `--accent-ink` | `#163A5F` | `#60A5FA` | Link, đường biểu đồ chính, tab đang chọn |
| `--primary` / `hover` | `#163A5F` / `#0F2A47` | `#1D4ED8` / `#2563EB` | Nút hành động chính |
| `--disabled-bg` / `fg` | `#E2E8F0` / `#64748B` | `#1E293B` / `#64748B` | Nút/ô bị vô hiệu hóa phân biệt rõ |
| `--risk-low` / `soft` | `#166534` / `#EAF7EF` | `#34D399` / `#0D2B22` | Bậc R1–R2 (An toàn) |
| `--risk-mid` / `soft` | `#92400E` / `#FEF3DC` | `#FBBF24` / `#2E210A` | Bậc R3 (Theo dõi / Vùng xám) |
| `--risk-high` / `soft` | `#991B1B` / `#FDECEC` | `#F87171` / `#381418` | Bậc R4–R5 (Cảnh báo cao / Kiệt quệ) |
| `--shadow-popover-token` | `0 4px 16px rgba(15,23,42,0.10)` | `0 8px 24px rgba(0,0,0,0.50)` | Đổ bóng duy nhất cho Popover/Dialog/Tooltip |

---

## 3. Typography, Radius & Danh Sách CẤM

- **Font tự host (`@fontsource`):** `IBM Plex Sans` (UI & bảng) và `IBM Plex Mono` (mã CK, kỳ báo cáo, công thức).
- **Bo góc thống nhất:**
  - `rounded-sm` (`4px`): Badge rủi ro (`R1`–`R5`, luôn `whitespace-nowrap`), tab item nội bộ.
  - `rounded-md` (`6px`): Button, Input, Select, Chip lọc.
  - `rounded-lg` (`8px`): Panel/Card, Dialog, Popover.
- **Danh sách CẤM tuyệt đối:**
  - Cấm gradient tím-xanh, màu tím (`#6B4C9A`) hoặc neon/cyan trên biểu đồ, glassmorphism, icon `✨`, cụm từ `"AI-Powered"`, emoji trang trí.
  - Cấm bọc `<Link><Button>` (vi phạm WCAG 4.1.2 `nested-interactive`) — dùng `<Link className={buttonVariants(...)}>`.
  - Cấm hardcode hex hover (`#E5E2DB`, `#173D60`, `#EED0CD`) hoặc stroke `#1F4E79` trên SVG.
  - Cấm lộ token kỹ thuật nội bộ (`REGIME_FALLBACK`, `RF_01..09`, `BS_*`, `snapshot_id`, UUID 36 ký tự, `.parquet`, traceback) ra UI.

---

## 4. Thành Phần Chuẩn Hóa

- `src/components/ui/button.tsx`: Xuất `Button` + `buttonVariants`, dùng 100% CSS tokens cho hover và disabled.
- `src/components/ui/segmented-control.tsx`: Bộ Tabs & Segmented Control thống nhất (`variant="pill"` và `variant="underline"`, chuẩn WAI-ARIA `role="tablist"` / `role="tab"`).
- `src/components/ui/kpi-strip.tsx`: Dải KPI phẳng `KpiStrip` / `KpiCell` chia bằng `divide-x divide-border`.
- `src/components/layout/AppShell.tsx`: Skip-to-content (`#main-content`), thanh điều hướng trái sticky, mobile drawer bằng `@base-ui/react/dialog`.
- `src/components/layout/StateBlocks.tsx`: `LoadingSkeleton`, `EmptyState`, `ErrorState` (`role="alert"`).
- `src/lib/echarts-theme.ts`: `ChartPalette` (bao gồm `companyAreaTop/Bottom`, `riskHighBandFill`), `formatChartNumber`, `formatChartPercent`.
