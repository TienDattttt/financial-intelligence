# Ma Trận Tuân Thủ Pháp Lý Việt Nam 2025–2026 & Chính Sách MRM TT200→TT99

> **Phạm vi áp dụng:** Toàn bộ nền tảng FinIntel V3.0 (Backend API, Pipeline ML, Excel/PDF Export, Web UI và Tài liệu phương pháp).  
> **Cập nhật hiệu lực:** `2026-01-01` (Phiên bản chính sách consent: `2026.01`).

---

## 1. Ma Trận Quy Định Pháp Lý Bắt Buộc

| Văn bản pháp lý | Điều khoản trọng tâm | Rủi ro nếu vi phạm | Biện pháp kiểm soát kỹ thuật trong FinIntel | Module thực thi |
|---|---|---|---|---|
| **Nghị định 88/2014/NĐ-CP** (Về dịch vụ xếp hạng tín nhiệm) | Kinh doanh dịch vụ xếp hạng tín nhiệm phải được Bộ Tài chính cấp Giấy chứng nhận đủ điều kiện kinh doanh. | Cung cấp hoặc gắn nhãn gây hiểu nhầm là tổ chức xếp hạng tín nhiệm có phép. | Cấm tuyệt đối mọi chuỗi *"Xếp hạng tín nhiệm"*, *"Bậc tín nhiệm"*, *"Phân tầng tín nhiệm"* trên toàn bộ `frontend/src/`, `PRODUCT.md`, và file xuất `.xlsx`/PDF. Thay 100% bằng **"Nhóm cảnh báo rủi ro tài chính định lượng (`R1–R5`)"** kèm giải thích xác suất thống kê lịch sử (1Y PD). | `frontend/src/lib/compliance.ts`, `frontend/src/lib/domain-labels.ts`, `scripts/check_constraints.py` |
| **Luật Chứng khoán 54/2019/QH14** (sửa đổi bởi **Luật 56/2024/QH15**, Khoản 32 Điều 4) | Tư vấn đầu tư chứng khoán là hoạt động có điều kiện do công ty chứng khoán/quản lý quỹ được UBCKNN cấp phép thực hiện. | Tự động sinh lời khuyên mua/bán hoặc tư vấn đầu tư trái phép. | Cấm 100% chuỗi *"Khuyến nghị mua"*, *"Khuyến nghị bán"*; khóa cứng chỉ báo định lượng dựa trên quy tắc cố định (`narrative.ts`); bắt buộc gắn `COMPLIANCE_DISCLAIMER_VI` tại footer web, sheet xuất Excel và PDF Tear-Sheet. | `src/finintel/domain/compliance.py`, `frontend/src/lib/compliance.ts`, `scripts/check_constraints.py` |
| **Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15** (hiệu lực `01/01/2026`) | Yêu cầu đồng ý rõ ràng theo từng mục đích cụ thể (opt-in tách biệt), quyền rút lại đồng ý, quyền xóa dữ liệu cá nhân và đánh giá tác động chuyển dữ liệu xuyên biên giới (**Điều 20**). | Lưu trữ PII không phân tách mục đích, gửi dữ liệu/truy vấn ra API LLM nước ngoài khi chưa có sự đồng ý hoặc rò rỉ PII trong prompt. | Tách 3 mục đích `ConsentPurpose`: `ACCOUNT_CORE`, `ALERT_NOTIFICATIONS`, `CROSS_BORDER_LLM_RAG`. Lưu vết `ConsentRecord` theo `ConsentPolicyVersion` (`2026.01`). Thực thi `DataDeletionPolicy` (SLA ≤ 15 ngày, ẩn danh hóa `user_audit_logs`, xóa PII khỏi `finintel_app`). Bắt buộc opt-in `CROSS_BORDER_LLM_RAG` và lọc sạch PII trước khi gọi Gemini API. | `src/finintel/domain/compliance.py`, `src/finintel/auth/`, `src/finintel/rag/router.py` |
| **Thông tư 99/2025/TT-BTC** & **MRM SR 11-7** (Chuyển tiếp `TT200/2014 → TT99/2025`) | Quản trị rủi ro mô hình khi hệ thống tài khoản và chỉ tiêu BCTC thay đổi cấu trúc từ 2026. | Đẩy mô hình sang GA khi chưa đủ mẫu `TT99/2025` hoặc khi phân phối biến/tỷ lệ thiếu dữ liệu dịch chuyển đột biến gây cảnh báo giả `R4/R5`. | Cổng `evaluate_tt99_mrm_gate()` kiểm soát 4 trạng thái: `INSUFFICIENT_TT99_SAMPLES` (`n < 30`), `SHADOW_MONITORING` (`30 <= n < 150`), `READY_FOR_GA` (`n >= 150`, `max_psi < 0.25`, `max_missing_jump < 0.15`), `DRIFT_BREACH` (chặn auto-promote). Cấm bịa mẫu TT99 giả. | `src/finintel/ml/mrm_tt99.py`, `src/finintel/ml/tables.py`, `src/finintel/serving/_schema_extensions.py` |

---

## 2. Checklist Tuân Thủ Trước Khi Phát Hành (`Go-Live Gate`)

### 2.1. Nghị định 88/2014/NĐ-CP & Luật Chứng khoán 56/2024/QH15
- [ ] `scripts/check_constraints.py` đạt `0` vi phạm trên `frontend/src/` (không còn *"Xếp hạng tín nhiệm"*, *"Bậc tín nhiệm"*, *"Khuyến nghị mua/bán"*).
- [ ] Mọi trang web, `.xlsx` workbook, PDF Tear-Sheet và response metadata đều hiển thị `COMPLIANCE_DISCLAIMER_VI`.
- [ ] Nhãn hiển thị cho `R1–R5` sử dụng thống nhất `RISK_TIER_LEGAL_LABEL_VI` (*"Nhóm cảnh báo rủi ro tài chính định lượng (R1–R5)"*).
- [ ] Hệ thống RAG chỉ trích dẫn nguyên văn BCTC/Thuyết minh kèm số trang và từ chối khi thiếu căn cứ — tuyệt đối không suy diễn khuyến nghị đầu tư.

### 2.2. Luật Bảo vệ Dữ liệu Cá nhân 91/2025/QH15 (PDPL)
- [ ] Màn hình đăng ký chỉ thu thập dữ liệu tối thiểu cho `ACCOUNT_CORE` (`email`, `password_hash`, `role`) và ghi nhận `ConsentRecord(policy_version="2026.01")`.
- [ ] `ALERT_NOTIFICATIONS` (Email Digest / Telegram / Webhook) và `CROSS_BORDER_LLM_RAG` mặc định **chưa bật (`False`)**, chỉ kích hoạt khi người dùng tự nguyện opt-in.
- [ ] Trang `/profile` cung cấp cơ chế rút lại consent bất kỳ lúc nào và luồng yêu cầu xóa tài khoản (`DataDeletionRequest`, `sla_days = 15`, `anonymize_audit_subject = True`).
- [ ] Không lưu session token vào `localStorage` (100% `HttpOnly; Secure; SameSite=Lax` cookie lưu băm SHA-256 trên server).

### 2.3. Chính Sách Quản Trị Rủi Ro Mô Hình `TT200/2014 → TT99/2025`
- [ ] `evaluate_tt99_mrm_gate()` tính `max_feature_psi` và `max_missing_jump` không chứa `NaN`/`inf`.
- [ ] Khi `n_tt99 < 30`: trả trung thực `INSUFFICIENT_TT99_SAMPLES` và kích hoạt `shadow_mode_active = True`.
- [ ] Khi `30 <= n_tt99 < 150` (và không vượt ngưỡng drift): trả `SHADOW_MONITORING`, giữ `auto_promote_allowed = False`.
- [ ] Khi `max_psi >= 0.25` hoặc `max_missing_jump >= 0.15` (với `n_tt99 >= 30`): trả `DRIFT_BREACH`, chặn tự động nâng cấp lên GA.
- [ ] Chỉ trả `READY_FOR_GA` khi `n_tt99 >= 150`, `max_psi < 0.25` và `max_missing_jump < 0.15` trên dữ liệu thật.

---

## 3. Mẫu Đánh Giá Tác Động Chuyển Dữ Liệu Xuyên Biên Giới (TIA — Điều 20 Luật 91/2025/QH15) {#tia-dieu-20}

> **Mã hồ sơ mẫu:** `TIA-FININTEL-2026-01` · **Mục đích áp dụng:** `ConsentPurpose.CROSS_BORDER_LLM_RAG`

### A. Thông tin bên chuyển dữ liệu & bên tiếp nhận
| Hạng mục | Nội dung khai báo chuẩn |
|---|---|
| **Bên kiểm soát / Bên xử lý dữ liệu tại Việt Nam** | Đơn vị vận hành nền tảng FinIntel (môi trường phi thương mại / nghiên cứu định lượng) |
| **Bên tiếp nhận dữ liệu ở nước ngoài** | Google LLC / Google AI Studio (Gemini API endpoints ngoài lãnh thổ Việt Nam) |
| **Căn cứ pháp lý chuyển dữ liệu** | Sự đồng ý tường minh, tự nguyện của chủ thể dữ liệu theo từng mục đích (`ConsentPurpose.CROSS_BORDER_LLM_RAG`, Điều 20 Luật 91/2025/QH15) |
| **Thời điểm đánh giá định kỳ** | Trước khi mở Public Beta (`v3.0.0-beta.1`) và rà soát lại mỗi 12 tháng hoặc khi thay đổi nhà cung cấp LLM |

### B. Danh mục dữ liệu chuyển ra nước ngoài & Nguyên tắc tối thiểu hóa
1. **Dữ liệu ĐƯỢC PHÉP gửi tới API LLM:**
   - Câu hỏi phân tích doanh nghiệp đã được làm sạch (ví dụ: chỉ tiêu kế toán VAS, đoạn trích Thuyết minh BCTC công khai của doanh nghiệp niêm yết).
   - Mã chứng khoán công khai và kỳ báo cáo (`CONSOLIDATED` / `SEPARATE`, năm/quý).
2. **Dữ liệu CẤM TUYỆT ĐỐI đưa vào prompt (PII Stripping bắt buộc):**
   - `email`, họ tên, số điện thoại, `user_id`, địa chỉ IP, session cookie, API key cá nhân, nội dung `analyst_notes` riêng tư chứa thông tin định danh cá nhân.

### C. Biện pháp kỹ thuật & tổ chức bảo vệ dữ liệu
- **Opt-in Gate:** API `POST /api/v2/documents/{ticker}/investigate` từ chối lịch sự nếu người dùng chưa bật `ConsentPurpose.CROSS_BORDER_LLM_RAG`.
- **Mã hóa truyền tải:** Bắt buộc HTTPS / TLS 1.2+ khi gọi API Gemini; không lưu payload prompt chứa thông tin tài khoản vào log ứng dụng.
- **Kiểm soát hạn mức & ngắt mạch:** Áp đặt Fair-Use Quota theo vai trò (`USER` / `RESEARCHER`) và Global Circuit Breaker tại ngưỡng `85%` hạn mức miễn phí ngày.
- **Quyền rút lại đồng ý & xóa dữ liệu:** Người dùng có thể tắt `CROSS_BORDER_LLM_RAG` tại `/profile`; hiệu lực chặn áp dụng tức thì cho các yêu cầu RAG tiếp theo.

### D. Phê duyệt nội bộ & Tham vấn pháp lý
- [ ] Xác nhận rà soát bởi Phụ trách Kỹ thuật & An toàn dữ liệu (xác minh bộ lọc ẩn danh PII trước khi gọi LLM).
- [ ] Xác nhận wording Điều khoản sử dụng & Chính sách quyền riêng tư bởi luật sư / đại diện pháp lý trước ngày mở Public Beta `v3.0.0-beta.1`.
