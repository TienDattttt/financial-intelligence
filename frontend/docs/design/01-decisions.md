# 01 — Quyết định thiết kế đã chốt (ADR rút gọn)

> Mỗi quyết định: bối cảnh → lựa chọn → lý do → điều kiện xem lại. Các quyết định dựa trên nghiên cứu đối thủ, mã nguồn backend hiện có và nguyên tắc UCD; **chưa có dữ liệu phỏng vấn người dùng thật** — mục "Điều kiện xem lại" chỉ rõ khi nào phải đổi.

## D1. Persona chính: Chuyên viên phân tích / thẩm định tín dụng

- **Lý do:** điểm mạnh khác biệt của hệ thống (forensic RF_01–RF_09, Altman/Piotroski/Beneish, xác suất kiệt quệ + SHAP, stress test 5 tham số, lineage 38 chỉ số) phục vụ đúng người cần *căn cứ* để trình bày. Nhà đầu tư cá nhân là persona phụ → yêu cầu giải nghĩa thuật ngữ, không giảm độ sâu.
- **Hệ quả:** mật độ thông tin mặc định *compact*; mọi kết luận kèm nguồn; có xuất báo cáo.
- **Xem lại khi:** ≥ 60% người tham gia phỏng vấn/khảo sát thuộc nhóm NĐT cá nhân và gặp khó với mật độ thông tin.

## D2. "Không AI" = không mang dáng dấp giao diện AI, không tính năng AI quảng cáo

- Giữ các mô hình định lượng đã có, trình bày như **phương pháp có trích dẫn và phiên bản**.
- Câu diễn giải sinh bằng **quy tắc cố định** từ dữ liệu SHAP + trung vị ngành (tái lập, kiểm chứng được).
- Cấm danh sách ở `DESIGN.md §3`.

## D3. Không đăng nhập; backend giữ chỉ đọc

- **Bối cảnh:** API hiện tại chỉ đọc, CORS mặc định từ chối — tư thế bảo mật tốt. Watchlist/cảnh báo kiểu đối thủ đòi tài khoản + ghi dữ liệu.
- **Lựa chọn:** danh sách theo dõi và bộ lọc đã lưu nằm trong `localStorage` của trình duyệt (có xuất/nhập JSON). "Cảnh báo" được hiện thực thành API **chỉ đọc** "Biến động giữa hai kỳ dữ liệu" (`/changes`), FE lọc theo watchlist cục bộ.
- **Lợi ích:** không cần xác thực, không có dữ liệu cá nhân trên server, không đổi kiến trúc backend.
- **Xem lại khi:** cần dùng nhiều thiết bị hoặc nhiều người dùng chia sẻ danh sách.

## D4. Stack frontend: dựng trên nền `ai-website-cloner-template`

| Lớp | Chọn | Lý do |
|---|---|---|
| Khung | **Next.js 16 (App Router) + React 19 + TypeScript strict** (từ template) | Template đã cấu hình sẵn, kiểm tra `npm run check` (lint + typecheck + build) |
| UI | **shadcn/ui (Base UI) + Tailwind CSS v4** (từ template) | Sở hữu mã component, dễ chỉnh cho mật độ tài chính |
| Dữ liệu | **TanStack Query** | Cache theo khóa có `snapshot_id`, khớp chiến lược cache backend |
| Bảng | **TanStack Table** | ~400 DN → bảng headless đủ, không cần AG Grid |
| Biểu đồ | **Apache ECharts** (`echarts` + `echarts-for-react`) | Có sẵn waterfall, boxplot, band, heatmap; render canvas nhanh; tùy biến theme theo token |
| Hợp đồng API | **openapi-typescript** sinh type từ `/openapi.json` của FastAPI | Khớp DTO Pydantic, phát hiện lệch hợp đồng lúc biên dịch |
| Mock | **MSW** (Mock Service Worker) | Dựng UI trước khi endpoint mới có |
| Trạng thái URL | search params (`nuqs`) | Bộ lọc screener chia sẻ/bookmark được |
| Font | IBM Plex Sans / Mono (Google Fonts qua `next/font`) | Hỗ trợ tiếng Việt, chữ số tabular |
| Kiểm thử | Playwright (E2E + ảnh chụp), axe-core (a11y), Vitest (helper định dạng số) | Cổng chất lượng ở `DESIGN.md §7` |

- **Cách dùng template:** (1) làm **khung ứng dụng sản phẩm** trong `frontend/` — giữ cấu hình Next/Tailwind/shadcn/lint, bỏ phần dành riêng cho clone (thư mục `sites/`, banner tài trợ); (2) dựng riêng **`reference-lab/`** (bị gitignore) để chạy quy trình `clone-website` trên **trang công khai được phép** nhằm *học cơ chế bố cục*, không phát hành, không commit tài sản của bên thứ ba. Xem `04-reference-lab.md`.
- **Xem lại khi:** cần SSR/SEO không quan trọng và muốn đơn giản hơn → có thể chuyển `output: 'export'` (static) vì backend là API riêng.

## D5. Kết nối backend

- Dev: Next.js `rewrites` chuyển `/api/v1/*` → `http://127.0.0.1:<port>/api/v1/*` → **không cần mở CORS** trên backend.
- Prod (nếu có): cùng origin qua reverse proxy, hoặc cấu hình `cors_origins` tường minh.
- Mọi lỗi API hiển thị thông điệp đã làm sạch (giữ nguyên tắc `MED-06` của backend).

## D6. Màu theo quy ước thị trường VN + tách hệ đánh giá

Xem `DESIGN.md §2.2`. Lý do: người dùng VN đã quen xanh-tăng/đỏ-giảm từ bảng giá; nhưng trong phân tích rủi ro, "tăng" có thể là xấu → cần hệ màu đánh giá độc lập.

## D7. Phạm vi v1 và thứ tự xây

Theo `PRODUCT.md §6`. Thứ tự: Khung + design system → Hồ sơ DN → Sàng lọc → Kịch bản sốc → Trang chủ → Dữ liệu & Phương pháp → So sánh/Theo dõi.

## D8. Ngân sách thời gian giả định: 8 tuần

| Tuần | Nội dung |
|---|---|
| 1 | Kiểm chứng nhanh: 5 phỏng vấn + khảo sát; reference lab; chốt wireframe |
| 2 | Khởi tạo `frontend/`, design tokens, AppShell, helper định dạng, client API + MSW |
| 3–4 | Hồ sơ DN (tất cả tab) + endpoint F1, F2, F3, F13 |
| 5 | Sàng lọc + bộ lọc mẫu + watchlist cục bộ |
| 6 | Kịch bản sốc + trang chủ (heatmap F11) |
| 7 | Dữ liệu & Phương pháp, So sánh DN, `/changes` |
| 8 | Kiểm thử khả dụng 5 người, sửa, SUS, ảnh chụp báo cáo đồ án |
