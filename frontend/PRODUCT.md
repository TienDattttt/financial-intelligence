# PRODUCT.md — FinIntel Web

> Nguồn sự thật về **sản phẩm** cho mọi người và mọi AI agent làm việc trong `frontend/`.
> Thiết kế hình ảnh nằm ở [`DESIGN.md`](./DESIGN.md). Quy trình và bằng chứng nằm ở [`docs/design/`](./docs/design/).
> Trạng thái: **v0.1 — giả định đã chốt, chờ kiểm chứng bằng phỏng vấn/kiểm thử người dùng** (xem `docs/design/01-decisions.md`).

## 1. Sản phẩm là gì (một câu)

FinIntel giúp người phân tích **phát hiện sớm rủi ro tài chính và dấu hiệu bất thường kế toán của doanh nghiệp niêm yết Việt Nam trong dưới 1 phút**, với mọi kết luận **truy được về chỉ tiêu gốc trên báo cáo tài chính** và **giải thích được bằng ngôn ngữ tài chính**.

## 2. Không phải là gì

- Không phải bảng giá, không phải công cụ giao dịch, không có dữ liệu giá realtime.
- Không đưa khuyến nghị mua/bán. Mọi trang có dòng: *"Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư."*
- Không có chatbot, không có "AI insight", không có văn bản sinh bởi mô hình ngôn ngữ. Mọi câu diễn giải là **quy tắc cố định, tái lập được**.
- Không định giá cổ phiếu (P/E, DCF) ở phiên bản này — pipeline chưa có dữ liệu giá.

## 3. Người dùng

| Vai trò | Persona | Mức chịu phức tạp | Ưu tiên |
|---|---|---|---|
| **Chính** | **Chuyên viên phân tích / thẩm định tín dụng** — đọc BCTC hằng ngày, phải trình bày căn cứ cho cấp trên | Cao | Tốc độ ra kết luận, căn cứ truy nguồn, so ngành công bằng |
| Phụ | **Nhà đầu tư cá nhân có kiến thức** — đầu tư giá trị dài hạn, muốn tránh "mìn" kế toán | Trung bình | Dễ hiểu, giải nghĩa thuật ngữ tại chỗ |
| Phụ | **Kiểm toán viên / giảng viên – sinh viên kế toán** | Cao | Truy nguồn từng con số, chuẩn mực TT200/TT99, điều chỉnh hồi tố |

Khi có xung đột thiết kế, **ưu tiên persona chính** nhưng không được để persona phụ "lạc đường": luôn có giải nghĩa thuật ngữ và đường dẫn phương pháp.

## 4. Việc người dùng cần làm xong (JTBD) — xếp theo ưu tiên

| ID | Khi… | Tôi muốn… | Để… |
|---|---|---|---|
| J1 | nhận một mã DN cần đánh giá | biết trong 1 phút DN có dấu hiệu rủi ro tài chính/kế toán không | quyết định đào sâu hay loại |
| J3 | mô hình nói DN rủi ro cao | biết **vì sao** bằng chỉ tiêu tài chính | trình bày và bảo vệ kết luận |
| J2 | thấy một chỉ số bất thường | biết nó tệ cỡ nào **so với ngành** và **xu hướng** qua các kỳ | không đánh giá sai do đặc thù ngành |
| J4 | nghi ngờ một con số | truy về chỉ tiêu gốc trên BCTC, kỳ, chuẩn mực, nguồn | tự kiểm chứng |
| J5 | cần danh sách DN theo tiêu chí | lọc nhanh, dùng bộ lọc mẫu, lưu bộ lọc | theo dõi định kỳ |
| J6 | lãi suất/doanh thu có thể sốc | biết ngành/DN nào chịu trận | điều chỉnh hạn mức/danh mục |
| J7 | có BCTC kỳ mới | thấy ngay DN mình theo dõi xấu đi | không bỏ lỡ |
| J8 | phân tích xong | xuất báo cáo gọn | gửi cấp trên/lưu hồ sơ |

## 5. Nguyên tắc sản phẩm

1. **Kết luận → Lý do → Bằng chứng → Nguồn.** Mọi màn hình đi từ tổng quát đến chi tiết.
2. **Không con số nào đứng một mình.** Mỗi điểm số có ngưỡng, so ngành hoặc lịch sử đi kèm.
3. **Mọi con số truy nguồn được.** Bấm vào số → chỉ tiêu BCTC, công thức, kỳ, chuẩn mực, ngày công bố.
4. **Trung thực về giới hạn.** Nhóm so sánh nhỏ, dữ liệu thiếu, mô hình không áp dụng (ví dụ ngân hàng) → nói rõ, không giấu.
5. **Ngôn ngữ nghiệp vụ tiếng Việt.** Không lộ mã kỹ thuật (`REGIME_FALLBACK`, `snapshot_id`) ra giao diện người dùng.
6. **Người dùng không cần tài khoản.** Danh sách theo dõi và bộ lọc lưu trên trình duyệt; backend giữ nguyên chỉ đọc.

## 6. Phạm vi phiên bản 1 (MoSCoW)

| Must | Should | Could | Won't (v1) |
|---|---|---|---|
| Khung ứng dụng + tìm DN (Ctrl+K) | So sánh 2–5 DN | Xuất Excel từ API | Định giá, biểu đồ giá |
| Hồ sơ DN: kết luận, giải thích, chỉ số so ngành, chất lượng lợi nhuận, BCTC nhiều kỳ, nguồn dữ liệu | Heatmap rủi ro ngành ở trang chủ | Dark mode | Tin tức, cộng đồng, bảng giá |
| Sàng lọc DN + bộ lọc mẫu | Lịch sử điều chỉnh hồi tố | In/PDF trang hồ sơ | Đăng nhập, đồng bộ đa thiết bị |
| Kịch bản sốc (stress test) | Danh sách theo dõi (lưu cục bộ) + "Biến động kỳ mới" | | Văn bản sinh bởi LLM |
| Từ điển chỉ số & phương pháp | | | |

## 7. Chỉ số thành công

| Chỉ số | Mục tiêu |
|---|---|
| Thời gian trả lời "DN này có rủi ro không, vì sao?" (kiểm thử) | < 60 giây |
| Tỉ lệ hoàn thành nhiệm vụ kiểm thử | ≥ 80% |
| Điểm SUS | ≥ 75 |
| Người dùng giải thích đúng 2 yếu tố rủi ro hàng đầu sau khi xem trang giải thích | ≥ 4/5 |
| Lỗi a11y nghiêm trọng (axe) | 0 |

## 8. Từ vựng hiển thị (backend → giao diện)

| Backend | Hiển thị |
|---|---|
| `SAFE_ZONE` / `GREY_ZONE` / `DISTRESS_ZONE` | An toàn / Vùng xám / Nguy cơ kiệt quệ |
| `calibrated_distress_prob` | Xác suất kiệt quệ tài chính (mô hình) |
| `beneish_flag` | Dấu hiệu điều chỉnh lợi nhuận (Beneish) |
| `piotroski_f_score` | Điểm sức khỏe Piotroski (0–9) |
| `peer_group_level = ICB_LEVEL_3` | So với ngành cấp 3: {tên ngành} ({n} DN) |
| `peer_group_level = ICB_LEVEL_2` | So với ngành cấp 2 (ngành cấp 3 quá ít DN) |
| `peer_group_level = REGIME_FALLBACK` | So với toàn bộ DN cùng chuẩn mực (ngành quá ít DN) — **luôn kèm cảnh báo** |
| `snapshot_id`, `published_at` | "Dữ liệu cập nhật đến {ngày}" |
| `statement_scope = CONSOLIDATED` / `SEPARATE` | Hợp nhất / Riêng lẻ |
| `period_type = QUARTER` / `ANNUAL` | Quý / Năm |
| `model_version`, `model_role` | "Mô hình {version}" trong phần Phương pháp, không ở tiêu đề |
| `audit_quarantine`, `quality_status` | Không hiển thị cho người dùng cuối, trừ trang "Dữ liệu & Phương pháp" |
