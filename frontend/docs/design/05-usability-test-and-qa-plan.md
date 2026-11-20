# 05 — Kế hoạch kiểm thử khả dụng (Usability Testing) & Cổng chất lượng tự động

> Thiết kế tốt không được chứng minh bằng cảm giác "trông đẹp", mà bằng **bằng chứng từ người dùng thật** và **cổng kiểm tra tự động lặp lại được**. Tài liệu này dùng làm phụ lục kiểm chứng chất lượng khi bảo vệ đồ án.

---

## 1. Kiểm thử khả dụng với 5 người dùng (Phương pháp Jakob Nielsen)

Theo nghiên cứu của Nielsen, kiểm thử với **5 người đại diện** phát hiện khoảng **85% vấn đề khả dụng**. Thực hiện **2 vòng**:
- **Vòng 1 (trước hoặc ngay khi xong bản MVP trên mock):** 5 người (3 chuyên viên phân tích/tín dụng hoặc sinh viên năm cuối tài chính–kế toán; 2 nhà đầu tư cá nhân).
- **Vòng 2 (sau khi sửa lỗi nặng của vòng 1):** 2–3 người để xác nhận lỗi đã hết.

### 1.1. Quy trình mỗi buổi (35–40 phút, 1-1)
1. **Mở đầu (5 phút):** Giải thích *"Chúng ta đang kiểm tra phần mềm, không kiểm tra anh/chị. Nếu có chỗ khó hiểu, đó là lỗi thiết kế."* Xin phép ghi màn hình/ghi chú.
2. **Hỏi bối cảnh (5 phút):** Công việc, công cụ BCTC hay dùng nhất hiện nay.
3. **Thực hiện 5 nhiệm vụ — Think-aloud (20 phút):** Người dùng tự thao tác và **nói to suy nghĩ**. Người điều phối **không gợi ý** trừ khi kẹt quá 2 phút (và ghi nhận là "hoàn thành có gợi ý").
4. **Chấm SEQ & SUS (5 phút):** Sau mỗi nhiệm vụ hỏi 1 câu SEQ (*"Từ 1 rất khó đến 5 rất dễ, nhiệm vụ vừa rồi ở mức nào?"*). Cuối buổi điền bảng 10 câu **SUS (System Usability Scale)**.

### 1.2. 5 kịch bản nhiệm vụ chuẩn

| # | Nhiệm vụ giao cho người dùng | Tiêu chí đạt | Mục tiêu thời gian |
|---|---|---|---|
| T1 | *"Bạn vừa được giao xem nhanh mã HPG. Hãy mở trang của HPG và cho biết trong 1 phút: doanh nghiệp này có dấu hiệu rủi ro tài chính hay kế toán không?"* | Tìm đúng mã, đọc đúng vùng Altman/Piotroski/Beneish/Xác suất | < 60 giây |
| T2 | *"Mô hình đánh giá một mã rủi ro cao (chọn 1 mã có xác suất > 20%). Hãy chỉ ra 2 nguyên nhân tài chính chính khiến rủi ro tăng và giải thích bằng lời của bạn."* | Đọc đúng Waterfall + câu diễn giải tiếng Việt | < 60 giây |
| T3 | *"Hãy tìm các doanh nghiệp thuộc ngành Bất động sản đang ở vùng nguy cơ kiệt quệ hoặc có dấu hiệu nghi vấn điều chỉnh lợi nhuận, rồi chọn 2 mã để so sánh."* | Lọc đúng trên `/screener`, mở được `/compare` | < 90 giây |
| T4 | *"Giả sử lãi suất tăng thêm 200 điểm cơ bản (+2%) và doanh thu giảm 10%. Cho biết tỷ lệ nợ rủi ro toàn thị trường thay đổi thế nào và ngành nào chịu ảnh hưởng nặng nhất?"* | Nhập/kéo đúng 2 tham số trên `/stress-test`, đọc đúng ngành | < 90 giây |
| T5 | *"Trên trang của HPG, bạn muốn kiểm chứng xem chỉ số ROE (hoặc Nợ/VCSH) được tính từ những chỉ tiêu BCTC nào và thuộc hồ sơ công bố ngày nào."* | Mở được `SourceDrawer` từ con số, đọc được công thức + kỳ + mã hồ sơ | < 45 giây |

### 1.3. Công thức chấm điểm SUS & Ngưỡng đạt

- Câu lẻ (1, 3, 5, 7, 9): `điểm_quy_đổi = điểm_chọn − 1`
- Câu chẵn (2, 4, 6, 8, 10): `điểm_quy_đổi = 5 − điểm_chọn`
- `SUS = tổng(10 câu quy đổi) × 2,5` (thang 0–100; trung bình ngành = 68; **mục tiêu FinIntel ≥ 75**).
- Các ngưỡng khác: tỉ lệ hoàn thành không cần gợi ý **≥ 80%**; SEQ trung bình **≥ 4,0/5**; ít nhất **4/5** người giải thích đúng 2 nguyên nhân rủi ro ở T2.

### 1.4. Mẫu ghi nhận lỗi (Issue Log)

| Mức độ | Định nghĩa | Hành động |
|---|---|---|
| **P0 — Chặn luồng** | Người dùng không thể hoàn thành nhiệm vụ | Sửa bắt buộc trước khi tiếp tục |
| **P1 — Khó chịu lớn** | Hiểu nhầm số liệu, bấm nhầm nhiều lần, không hiểu thuật ngữ | Sửa trong vòng hiện tại |
| **P2 — Nhỏ** | Thẩm mỹ, canh lề, khoảng cách | Gom vào đợt `polish` |

---

## 2. Cổng kiểm tra tự động (Automated Quality Gates)

Mọi Pull Request / màn hình hoàn thiện trên `frontend/` phải vượt qua đủ **5 cổng** dưới đây:

### Cổng 1 — Kiểm tra tĩnh (`npm run check`)
- `eslint` không có lỗi/cảnh báo nghiêm trọng.
- `tsc --noEmit` (TypeScript strict, không `any`, khớp 100% với `src/types/api.generated.ts`).
- `next build` thành công không lỗi.

### Cổng 2 — Unit test nghiệp vụ tài chính (`vitest`)
Bắt buộc có test bao phủ các trường hợp biên:
- `formatVndBillions`: số dương lớn (`12345678900000` → `12.345,7`), số âm BCTC (`-1250300000000` → `(1.250,3)`), số `0` → `–`, `null`/`undefined`/`NaN`/`Infinity` → `N/A`.
- `formatPercent` / `formatMultiple`: dấu trừ chuẩn `−` (U+2212), không hiển thị `-0,0%`.
- `sanitizeCsvCell`: trung hòa tiền tố `=`, `+`, `-`, `@`, `\t`, `\r` (CSV Injection Guard, giữ nhất quán với backend).
- `buildShapNarrativeVi`: kiểm tra câu diễn giải khi đủ `peer_median`, khi thiếu `peer_median`, khi toàn bộ yếu tố đều âm/dương, khi không có driver.
- `getPercentileTone`: đảo thang màu chính xác cho nhóm `LOWER_IS_BETTER` (ví dụ `debt_to_equity`, `accruals_to_assets`, `dso`).

### Cổng 3 — Kiểm thử E2E & 4 trạng thái (`playwright`)
Chạy trên ứng dụng thật (với MSW mock hoặc FastAPI test server):
- Luồng `Ctrl+K` tìm `hoa phat` (không dấu) → vào `/companies/HPG` → bấm mở `SourceDrawer` → `Esc` đóng.
- Luồng `/screener` chọn preset → kiểm tra URL query cập nhật → reload trang giữ nguyên bộ lọc → xuất CSV không chứa công thức độc hại.
- Luồng `/stress-test` thay đổi thanh trượt → debounce → gọi `POST /api/v1/stress-test` → cập nhật KPI & bảng; mô phỏng HTTP `503` → tự retry thành công.
- Kiểm tra `empty state` và `error state` (HTTP 404 / 500) **không bao giờ** hiển thị đường dẫn đĩa, `.parquet`, `.py`, hay `Traceback`.
- Kiểm tra **0 lỗi console / 0 uncaught pageerror / 0 tràn ngang** (`scrollWidth <= clientWidth`) ở cả `1366×768`, `1440×900` và `375×812`.

### Cổng 4 — Tiếp cận (`@axe-core/playwright`)
- **0 lỗi `critical` và `serious`** trên tất cả 7 route chính.
- Kiểm tra điều hướng bàn phím thuần (`Tab`, `Enter`, `Esc`) trên `CommandSearch`, `SourceDrawer`, bộ lọc `/screener` và bảng.

### Cổng 5 — Kiểm tra "Chống giao diện AI" (Anti-AI-Slop Audit)
Rà soát tự động bằng grep / ESLint rule trên `frontend/src/`:
- **Cấm xuất hiện:** `bg-gradient-to-`, `from-purple-`, `from-indigo-`, `backdrop-blur`, `animate-bounce`, `hover:scale-`, ký tự `✨`, chuỗi `"AI-powered"` / `"Smart insight"` / `"Lorem ipsum"` / `"Acme"`.
- Kiểm tra mọi `<td>` chứa số đều có class `tabular-nums text-right`.
- Chụp ảnh màn hình đối chiếu ở `1366×768` để xác nhận dải **Kết luận (4 VerdictCard)** nằm trọn trong màn hình đầu tiên (above the fold).
