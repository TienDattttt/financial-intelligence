# Spec: `v3-compliance-and-mrm` — Pháp Lý VN 2026 & Cổng Kiểm Định TT200→TT99 — V3.0

> **Module ID:** `v3-compliance-and-mrm`  
> **Wave / Build Order:** Phase 1 — **Wave 1A** (chạy song song với `v3-valuation-mart-and-screener`, `v3-infra-ha-storage`)  
> **Depends On:** `—`  
> **Consumers:** `v3-identity-and-workspace`, `v3-export-and-tearsheet`, `v3-fx-stress-and-bond-signals`  
> **MoSCoW / Audit Refs:** `#45` gap (`taxonomy_drift` live null), NĐ 88/2014/NĐ-CP, Luật CK 2019 (sửa đổi Luật 56/2024/QH15), Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15.

---

## 1. Objective

Khóa toàn bộ ranh giới pháp lý Việt Nam 2025–2026 và cổng quản trị rủi ro mô hình (MRM SR 11-7) cho quá trình chuyển đổi `TT200/2014 → TT99/2025` trước khi thương mại hóa:
1. **Triệt tiêu rủi ro xếp hạng tín nhiệm không phép (NĐ 88/2014/NĐ-CP):** Quét và thay thế 100% nhãn thương mại *"Bậc tín nhiệm" / "Xếp hạng tín nhiệm"* còn sót lại trên UI/docs/exports thành **"Nhóm cảnh báo rủi ro tài chính định lượng (`R1–R5`)"** (kế thừa phần dọn nhãn đã bắt đầu tại commit `b6851ca`).
2. **Tuân thủ Luật Chứng khoán 2019 (sửa đổi Luật 56/2024/QH15, Khoản 32 Điều 4):** Cung cấp hằng số tuyên bố miễn trừ pháp lý chuẩn hóa (`COMPLIANCE_DISCLAIMER_VI`) dùng chung cho mọi trang web, file xuất `.xlsx`/PDF Tear-Sheet và API metadata.
3. **Định nghĩa Hợp đồng Dữ liệu Cá nhân (Luật 91/2025/QH15, hiệu lực 01/01/2026):** Cung cấp schema versioned cho `ConsentRecord`, `DataDeletionRequest` và tài liệu kiểm soát chuyển dữ liệu xuyên biên giới (Điều 20 — ranh giới gọi API Gemini/LLM nước ngoài).
4. **Cổng kiểm định MRM `TT200 → TT99`:** Bổ sung đánh giá `PSI` thực tế, tỷ lệ `*_missing` và chế độ **Shadow-Scoring** cho BCTC `VAS_TT99_2025` năm 2026, đồng thời phơi bày rõ trạng thái cổng trên `/api/v2/meta/model-card` và `/methodology?tab=models` (chấm dứt trạng thái `taxonomy_drift = null` mờ nhạt).

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** `polars==1.35.2`, `numpy`, `pydantic==2.14.4` (không thêm dependency mới).
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4`, `typescript`, `vitest==4.0.0`.
- **Static Guard:** Mở rộng quy tắc kiểm tra tự động trong `scripts/check_constraints.py` (cấm chuỗi *"Xếp hạng tín nhiệm"* / *"Bậc tín nhiệm"* / *"Khuyến nghị mua"* trong `frontend/src/`).

---

## 3. Commands

```powershell
# Backend & Mechanical Constraints
.\.venv\Scripts\python.exe scripts/check_constraints.py --selftest
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/ml/mrm_tt99.py tests/unit/test_v3_compliance_mrm.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_compliance_mrm.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `src/finintel/ml/mrm_tt99.py` | Create (`<= 400` LOC) | Tính `PSI TT200→TT99`, kiểm tra ngưỡng `missing_rate_delta`, trạng thái `SHADOW_ONLY \| READY_FOR_GA \| DRIFT_BREACH` |
| `src/finintel/domain/compliance.py` | Create (`<= 200` LOC) | Pydantic/dataclass contract cho `ConsentPolicyVersion`, `ConsentPurpose`, `DataDeletionPolicy`, `ComplianceNotice` |
| `frontend/src/lib/compliance.ts` | Create (`<= 150` LOC) | Hằng số tuyên bố miễn trừ NĐ 88/2014 + Luật CK 56/2024 + Luật 91/2025 dùng chung cho mọi trang & export |
| `frontend/src/lib/domain-labels.ts` | Modify | Rà soát xóa sạch mọi chuỗi `"Bậc tín nhiệm"` / `"Xếp hạng tín nhiệm"` còn sót |
| `frontend/PRODUCT.md` | Modify | Cập nhật bảng từ vựng hiển thị (`risk_tier` → `"Nhóm cảnh báo rủi ro định lượng R1–R5"`) |
| `docs/compliance/VN_REGULATORY_COMPLIANCE.md` | Create | Ma trận tuân thủ NĐ 88/2014, Luật CK 56/2024, Luật 91/2025 (gồm quy trình TIA Điều 20 khi gọi LLM) |
| `scripts/check_constraints.py` | Modify (small rule) | Thêm static guard cấm chuỗi vi phạm NĐ 88/2014 & khuyến nghị mua/bán trong `frontend/src/` |
| `tests/unit/test_v3_compliance_mrm.py` | Create | Unit test cổng PSI TT99 (`READY_FOR_GA` / `SHADOW_ONLY` / `DRIFT_BREACH`) và contract compliance |
| `frontend/tests/unit/compliance_labels.test.ts` | Create | Test đảm bảo 0 chuỗi bị cấm và đủ disclaimer chuẩn trên domain labels |

---

## 5. Boundary Interfaces & Contracts

### 5.1. `TT99MrmGateStatus` (Cấp cho `/api/v2/meta/model-card` & `v3-export-and-tearsheet`)
```python
class TT99GateVerdict(StrEnum):
    INSUFFICIENT_TT99_SAMPLES = "INSUFFICIENT_TT99_SAMPLES"  # n_tt99 < 30 -> shadow banner
    SHADOW_MONITORING = "SHADOW_MONITORING"                  # 30 <= n_tt99 < 150
    READY_FOR_GA = "READY_FOR_GA"                            # n_tt99 >= 150 & max_psi < 0.25 & max_missing_jump < 0.15
    DRIFT_BREACH = "DRIFT_BREACH"                            # max_psi >= 0.25 or max_missing_jump >= 0.15 -> block auto-promote
```

### 5.2. `ConsentPurpose` Contract (Cấp cho `v3-identity-and-workspace` & `v3-scaled-rag-and-billing`)
```python
class ConsentPurpose(StrEnum):
    ACCOUNT_CORE = "ACCOUNT_CORE"                # Bắt buộc cho đăng nhập/lưu Watchlist
    ALERT_NOTIFICATIONS = "ALERT_NOTIFICATIONS"  # Opt-in gửi Email/Telegram/Webhook
    CROSS_BORDER_LLM_RAG = "CROSS_BORDER_LLM_RAG"  # Opt-in tường minh trước khi gửi truy vấn tới API LLM nước ngoài
```

---

## 6. Code Style & Architectural Invariants

- Mọi tệp Python mới dùng `from __future__ import annotations`, `mypy --strict`, `<= 400` LOC; không import thư viện `platform` ở top-level.
- Không tăng ròng quá `+10` dòng vào `src/finintel/ml/supervised/train.py` (uỷ quyền toàn bộ logic mới sang `src/finintel/ml/mrm_tt99.py`).
- Ví dụ chuẩn đánh giá cổng TT99:
```python
def evaluate_tt99_mrm_gate(
    *,
    tt99_sample_count: int,
    feature_psi: Mapping[str, float],
    missing_rate_delta: Mapping[str, float],
    min_ga_samples: int = 150,
    psi_breach_threshold: float = 0.25,
    missing_jump_threshold: float = 0.15,
) -> TT99MrmGateReport:
    ...
```

---

## 7. Testing Strategy

- **Backend unit (`pytest`):** Kiểm thử biên cho `evaluate_tt99_mrm_gate` (`n=0`, `n=29`, `n=100`, `n=200` với `PSI = 0.24` vs `0.26`, `missing_jump = 0.14` vs `0.16`, `NaN`/`inf` guard).
- **Static guard (`check_constraints.py --selftest`):** Test âm tính & dương tính cho regex cấm nhãn *"Xếp hạng tín nhiệm"* / *"Khuyến nghị mua/bán"* trong `frontend/src/`.
- **Frontend unit (`vitest`):** Xác nhận mọi nhãn `R1..R5` và `COMPLIANCE_DISCLAIMER_VI` không rò rỉ từ cấm.

---

## 8. Boundaries

- **Always:** Giữ nguyên giá trị enum nội bộ `R1..R5` và JSON keys hiện có để không vỡ API backward-compatibility; chỉ chuẩn hóa nhãn hiển thị tiếng Việt và thêm trường metadata mới.
- **Ask first:** Trước khi thay đổi ngưỡng `psi_breach_threshold` (`0.25`) hoặc `min_ga_samples` (`150`).
- **Never:** Không ghi/sửa `data/live_fhsc_universe/` hoặc `data/universe_600_800/`; không bịa số liệu TT99 giả để ép cổng về `READY_FOR_GA`.

---

## 9. Success Criteria

- [ ] `scripts/check_constraints.py` pass với quy tắc mới, xác nhận `0` chuỗi `"Xếp hạng tín nhiệm"` / `"Bậc tín nhiệm"` trong `frontend/src/`.
- [ ] `src/finintel/ml/mrm_tt99.py` tính toán chính xác 4 trạng thái `TT99GateVerdict` và được `model_card_meta` đọc sạch (không trả `null` vô nghĩa).
- [ ] Contract `ConsentPurpose` & `ComplianceNotice` sẵn sàng cho `v3-identity-and-workspace` và `v3-export-and-tearsheet` import trực tiếp.
- [ ] `docs/compliance/VN_REGULATORY_COMPLIANCE.md` ghi rõ checklist tuân thủ NĐ 88/2014, Luật CK 56/2024, Luật 91/2025 (kèm mẫu TIA Điều 20).

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-compliance-and-mrm -b feat/v3-compliance-and-mrm main
```
- Tách tối thiểu 3 atomic commits:
  1. `docs(compliance): add VN 2026 legal compliance matrix and TT99 MRM policy`
  2. `feat(ml,domain): add TT99 MRM drift gate and PDPL consent contracts`
  3. `feat(frontend,ci): enforce NĐ 88/2014 risk wording and compliance static guard`

---

## 11. Out of Scope (Deferred)

- Nộp hồ sơ giấy phép tổ chức xếp hạng tín nhiệm (Bộ Tài chính) hoặc giấy phép tư vấn đầu tư (UBCKNN) — ngoài phạm vi kỹ thuật phần mềm.
- Tự động huấn luyện lại mô hình trên TT99 (chỉ thực hiện khi thu thập đủ mẫu TT99 thật năm 2026–2027).

## 12. Open Questions

- Cần luật sư/đại diện pháp lý xác nhận wording chính thức của bản Điều khoản sử dụng & Chính sách quyền riêng tư (Luật 91/2025/QH15) trước ngày mở Public Beta `v3.0.0-beta.1`.
