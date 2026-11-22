# PRODUCT.md — FinIntel Web (v2.0)

> Nguồn sự thật về **sản phẩm** cho mọi người và mọi AI agent làm việc trong `frontend/`.
> Thiết kế hình ảnh và token Light/Dark nằm ở [`DESIGN.md`](./DESIGN.md). Lộ trình & quyết định kiến trúc nằm ở [`docs/refactor/03_FRONTEND_UX_AND_DIFFERENTIATION_PLAN.md`](../docs/refactor/03_FRONTEND_UX_AND_DIFFERENTIATION_PLAN.md).
> Trạng thái: **v2.0 — Đồng bộ lộ trình `v1.1` (`ADR-UI-01..04`)**.

## 1. Sản phẩm là gì (một câu)

FinIntel giúp người phân tích **phát hiện sớm rủi ro tài chính và dấu hiệu bất thường kế toán của doanh nghiệp phi tài chính niêm yết Việt Nam trong dưới 1 phút**, với mọi kết luận **truy được về chỉ tiêu gốc trên báo cáo tài chính** và **giải thích được bằng quy tắc định lượng tái lập được**.

## 2. Không phải là gì

- Không phải bảng giá realtime, không phải công cụ khớp lệnh giao dịch.
- Không đưa khuyến nghị mua/bán. Mọi trang có dòng: *"Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư."*
- Không có chatbot hay nhãn quảng cáo "AI-Powered". Mọi diễn giải TreeSHAP dùng **quy tắc cố định (`src/lib/narrative.ts`)**; mọi tra cứu Thuyết minh PDF ở lộ trình `UI-08` bắt buộc **trích dẫn đúng trang gốc và từ chối trung thực khi thiếu căn cứ**.
- Không mở rộng sang Ngân hàng / Chứng khoán / Bảo hiểm (`ADR-UI-04`): phạm vi khóa chặt doanh nghiệp phi tài chính (`CORPORATE`, chuẩn VAS `TT200 → TT99`).

## 3. Người dùng

| Vai trò | Persona | Mức chịu phức tạp | Ưu tiên |
|---|---|---|---|
| **Chính** | **Chuyên viên phân tích / thẩm định tín dụng** — đọc BCTC hằng ngày, phải trình bày căn cứ cho cấp trên | Cao | Tốc độ ra kết luận, căn cứ truy nguồn, so ngành công bằng |
| Phụ | **Nhà đầu tư cá nhân có kiến thức** — đầu tư dài hạn, muốn tránh "mìn" kế toán | Trung bình | Dễ hiểu, giải nghĩa thuật ngữ tại chỗ |
| Phụ | **Kiểm toán viên / giảng viên – sinh viên kế toán** | Cao | Truy nguồn từng con số, chuẩn mực `TT200/TT99`, điều chỉnh hồi tố |

Khi có xung đột thiết kế, **ưu tiên persona chính** nhưng không được để persona phụ "lạc đường": luôn có giải nghĩa thuật ngữ và đường dẫn phương pháp.

## 4. Việc người dùng cần làm xong (JTBD) — xếp theo ưu tiên

| ID | Khi… | Tôi muốn… | Để… |
|---|---|---|---|
| J1 | nhận một mã DN cần đánh giá | biết trong 1 phút DN có dấu hiệu rủi ro tài chính/kế toán không | quyết định đào sâu hay loại |
| J3 | mô hình nói DN rủi ro cao | biết **vì sao** bằng chỉ tiêu tài chính | trình bày và bảo vệ kết luận |
| J2 | thấy một chỉ số bất thường | biết nó tệ cỡ nào **so với ngành** và **xu hướng** qua các kỳ | không đánh giá sai do đặc thù ngành |
| J4 | nghi ngờ một con số | truy về chỉ tiêu gốc trên BCTC, kỳ, chuẩn mực, nguồn | tự kiểm chứng |
| J5 | cần danh sách DN theo tiêu chí | lọc nhanh, dùng bộ lọc mẫu, lưu bộ lọc | theo dõi định kỳ |
| J6 | lãi suất/doanh thu có thể sốc | biết ngành/DN/danh mục chịu trận thế nào | điều chỉnh hạn mức/danh mục |
| J7 | có BCTC kỳ mới | thấy ngay DN mình theo dõi xấu đi và đối chiếu diễn biến giá lịch sử | kiểm chứng cảnh báo sớm |
| J8 | phân tích xong | xuất dữ liệu/báo cáo gọn | gửi cấp trên/lưu hồ sơ |

## 5. Nguyên tắc sản phẩm

1. **Kết luận → Lý do → Bằng chứng → Nguồn.** Mọi màn hình đi từ tổng quát đến chi tiết.
2. **Không con số nào đứng một mình.** Mỗi điểm số có ngưỡng, so ngành hoặc lịch sử đi kèm.
3. **Mọi con số truy nguồn được.** Bấm vào số → chỉ tiêu BCTC, công thức, tử/mẫu số, kỳ, chuẩn mực, ngày công bố thực tế (TT96).
4. **Ngữ nghĩa kỳ trung thực (`M-04`).** Thanh công cụ toàn cục chỉ chuyển **`TTM (4 quý trượt)`** và **`Theo năm (Annual)`** — không gây hiểu lầm các chỉ số trượt 4 quý là số liệu quý đơn lẻ; quý rời rạc chỉ hiển thị cục bộ tại bảng BCTC và chuỗi tăng trưởng.
5. **Trung thực về giới hạn.** Nhóm so sánh nhỏ, dữ liệu thiếu, doanh nghiệp tài chính ngoài phạm vi → nói rõ bằng tiếng Việt, không giấu và không lộ token kỹ thuật.
6. **Người dùng không cần tài khoản.** Danh sách theo dõi, bộ lọc và tùy chọn Light/Dark/System lưu trên trình duyệt; backend giữ nguyên chỉ đọc.

## 6. Phạm vi sản phẩm v2 (MoSCoW đồng bộ lộ trình `v1.1`)

| Must (Core & Design System v2) | Should (`P1` High ROI) | Could (`P2` Có kiểm soát) | Won't |
|---|---|---|---|
| Khung ứng dụng + tìm DN (`Ctrl+K`) + **Light/Dark/System Mode** không FOUC | Visual Storytelling: Tăng trưởng, Cơ cấu vốn, Treemap ngành, Cross-filter quý ↔ SHAP (`UI-04`) | Điều tra Thuyết minh PDF trích dẫn đúng trang từ gói 30–50 BCTC chọn lọc (`UI-08`) | Bảng giá khớp lệnh realtime |
| Hồ sơ DN: kết luận, giải thích TreeSHAP, chỉ số so ngành, chất lượng LN, BCTC nhiều kỳ, truy nguồn | Stress Test theo 1 DN, Watchlist & so sánh A vs B (`UI-06`) | Nâng cấp có chọn lọc `@base-ui` (`Tooltip` Portal, `Combobox`, `Drawer`) (`UI-02`) | Khuyến nghị mua/bán cổ phiếu |
| Sàng lọc DN + So sánh 2–5 DN + Danh sách theo dõi cục bộ | Tích hợp Bộ 3 Tín dụng `R1–R5` TTC + `Top X%` PIT + `/methodology` Model Card tương tác (`UI-07`) | Xuất Excel `.xlsx` & Print Tear-Sheet (`UI-05b`) | Đăng nhập tài khoản, cộng đồng mạng xã hội |
| Từ điển chỉ số & phương pháp | Lớp phủ giá EOD (`close_raw` cho PIT Vốn hóa/P/E/P/B; `close_adj` cho đường giá & mốc `R4/R5`) (`UI-09`) | Lưu bộ lọc (`Saved Screens`) & Export CSV 100% tập lọc (`UI-05a`) | Văn bản marketing / sinh câu nhận định tự do bằng LLM |

## 7. Chỉ số thành công

| Chỉ số | Mục tiêu |
|---|---|
| Thời gian trả lời "DN này có rủi ro không, vì sao?" | < 60 giây |
| Tỉ lệ hoàn thành nhiệm vụ kiểm thử | ≥ 80% |
| Điểm SUS | ≥ 75 |
| Người dùng giải thích đúng 2 yếu tố rủi ro hàng đầu | ≥ 4/5 |
| Lỗi accessibility nghiêm trọng (`axe-core`) trên cả Light & Dark Mode | 0 |

## 8. Từ vựng hiển thị (backend → giao diện)

| Backend | Hiển thị trên UI |
|---|---|
| `SAFE_ZONE` / `GREY_ZONE` / `DISTRESS_ZONE` | An toàn / Vùng xám / Nguy cơ kiệt quệ |
| `risk_tier` (`R1`..`R5`, lộ trình `UI-07`) | Bậc tín nhiệm `R1` (Thấp nhất) → `R5` (Rất cao) |
| `calibrated_distress_prob` | Xác suất kiệt quệ tài chính (mô hình) |
| `beneish_flag` | Dấu hiệu điều chỉnh lợi nhuận (Beneish) |
| `piotroski_f_score` | Điểm sức khỏe Piotroski (0–9) |
| `peer_group_level = ICB_LEVEL_3` | So với ngành cấp 3: {tên ngành} ({n} DN) |
| `peer_group_level = ICB_LEVEL_2` | So với ngành cấp 2 (ngành cấp 3 quá ít DN) |
| `peer_group_level = REGIME_FALLBACK` | So với toàn bộ DN cùng chuẩn mực (ngành quá ít DN) — **luôn kèm cảnh báo** |
| `snapshot_id`, `published_at` | "Dữ liệu cập nhật đến {ngày}" |
| `statement_scope = CONSOLIDATED` / `SEPARATE` | Hợp nhất / Riêng lẻ |
| `period_type = QUARTER` / `ANNUAL` (toàn cục) | `TTM (4 quý trượt)` / `Theo năm (Annual)` |
| `model_version`, `model_role` | "Mô hình {version}" trong phần Phương pháp, không ở tiêu đề |
| `audit_quarantine`, `quality_status` | Không hiển thị cho người dùng cuối, trừ trang "Dữ liệu & Phương pháp" |
