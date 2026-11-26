# Spec: `v3-analyst-depth-and-ops-qa` — Peer Auto-Suggest, DuPont Tree, Local QoQ & Ops QA Workflow — V3.0

> **Module ID:** `v3-analyst-depth-and-ops-qa`  
> **Wave / Build Order:** Phase 2 — **Wave 2** (chạy song song với `v3-early-warning-alerts`, `v3-scaled-rag-and-billing`)  
> **Depends On:** `v3-valuation-mart-and-screener`, `v3-identity-and-workspace`  
> **Consumers:** `v3-private-issuer-and-hitl`, Chuyên viên phân tích & Đội Data Ops  
> **MoSCoW / Audit Refs:** `#7` Ops đồng bộ vòng đời DN, `#19` tăng trưởng `QoQ` cục bộ có cảnh báo mùa vụ, `#32` top `|Z-score|` giải thích ngoại lai đa biến, `#34` sơ đồ cây DuPont tương tác, `#39` tự động gợi ý đối thủ cùng ngành ICB L3, `#43` Data QA Queue duyệt bản ghi đính chính có phiên bản.

---

## 1. Objective

Nâng cấp chiều sâu công việc hàng ngày cho 2 persona chủ lực (**Chuyên viên Phân tích** và **Đội Data Ops vận hành 685→1.600 DN**):
1. **Tự động gợi ý đối thủ ngang hàng (`#39`):** Trên `/compare`, bổ sung nút *"Gợi ý 4 DN cùng ngành ICB L3 quy mô tương đương"* (dựa trên `icb_code_level3` + khoảng cách `log(BS_TOTAL_ASSETS)` hoặc `market_cap_vnd` từ `pit_valuation_mart`, fallback sang `icb_code_level2` nếu nhóm L3 `< 5` mã).
2. **Sơ đồ cây DuPont tương tác (`#34`):** Trực quan hóa chuỗi DuPont 5 & 6 nhân tố vốn đã tính trong `src/finintel/analytics/dupont.py` thành sơ đồ cây trực quan (`ROE = Gánh nặng thuế × Gánh nặng lãi vay × Biên EBIT × Vòng quay TS × Đòn bẩy`), bấm vào từng nút mở ngay `SourceDrawer` truy vết công thức & BCTC gốc.
3. **Tăng trưởng `QoQ` cục bộ có cảnh báo mùa vụ (`#19`) & Top Z-score cho Ngoại lai đa biến (`#32`):**
   - Bổ sung tăng trưởng `QoQ` cục bộ **chỉ trên bảng BCTC quý rời rạc & biểu đồ `GrowthComboChart`** (tuyệt đối giữ nguyên quy tắc `M-04`: thanh kỳ toàn cục chỉ gồm `TTM` vs `Annual`), luôn gắn nhãn cảnh báo tính mùa vụ Q4 của doanh nghiệp VN.
   - Khi doanh nghiệp có `anomaly_percentile_rank >= 95%`, trả thêm danh sách top 3 tỷ số có `|Z-score|` lớn nhất so với ngành để người dùng hiểu ngay *vì sao hồ sơ tài chính này đặc thù* mà không cần giải mã hộp đen IsolationForest.
4. **Cổng Data Ops QA Queue & Vòng đời Doanh nghiệp (`#7`, `#43` — chỉ `INTERNAL_OPS`):**
   - Biến danh sách `audit_quarantine` từ chỉ-xem thành workflow xử lý bất biến: giao việc, gắn ghi chú nguyên nhân, nạp **bản ghi đính chính có nguồn & `revision_no` mới** (tuyệt đối cấm sửa đè số cũ!), duyệt `APPROVE_REINGEST` hoặc `KEEP_QUARANTINED`, ghi 100% vào `user_audit_logs`.
   - Màn hình Ops theo dõi vòng đời mã (IPO mới, chuyển sàn HOSE/HNX/UPCOM, hủy niêm yết, tháng kết thúc niên độ FY-end).

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** `polars==1.35.2`, `duckdb==1.4.2`, `fastapi==0.165.0`, `pydantic==2.14.4`, `psycopg[binary]==3.3.9`. Không thêm thư viện mới.
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4`, `echarts==^6.0.0`, `@base-ui/react==^1.3.0`.

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/ops src/finintel/analytics/qoq_and_peers.py tests/unit/test_v3_analyst_ops.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_analyst_ops.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `config/migrations/app_v3_004_ops_qa.sql` | Create | DDL `ops_quarantine_tickets`, `ops_revision_submissions`, `issuer_lifecycle_events` |
| `src/finintel/analytics/qoq_and_peers.py` | Create (`<= 350` LOC) | Tính `revenue_growth_qoq`, `net_income_parent_growth_qoq` (chỉ cho quý rời rạc liên tiếp, không vượt biên `TT200→TT99`) + hàm xếp hạng 4 peer gần nhất cùng ngành ICB L3 |
| `src/finintel/ops/` (`qa_service.py`, `lifecycle.py`, `router.py`, `schemas.py`) | Create (mỗi file `< 400` LOC) | Workflow xử lý `audit_quarantine` (append-only revision, cấm UPDATE đè fact), đồng bộ trạng thái niêm yết (`INTERNAL_OPS` RBAC) |
| `src/finintel/serving/api.py` | Modify (`<= 2` dòng) | Mount `ops_router` và endpoint gợi ý peer |
| `frontend/src/components/charts/DuPontTreeChart.tsx` | Create (`<= 350` LOC) | Sơ đồ cây DuPont 5/6 nhân tố tương tác, Light/Dark mode, bàn phím WCAG 2.2 AA, click mở `SourceDrawer` |
| `frontend/src/app/compare/page.tsx` | Modify | Thêm nút *"Gợi ý đối thủ cùng ngành"* gọi `/api/v3/companies/{ticker}/suggested-peers` |
| `frontend/src/app/ops/qa/page.tsx` | Create (`<= 450` LOC) | Giao diện Data Ops QA Queue & Issuer Lifecycle (chặn truy cập nếu role != `INTERNAL_OPS`) |
| `tests/unit/test_v3_analyst_ops.py` | Create | Test QoQ boundary (`TT200→TT99` trả `NULL`), peer ranking ICB L3→L2 fallback, Ops QA immutability & RBAC 403 cho user thường |

---

## 5. Boundary Interfaces & Contracts

### 5.1. Analyst Endpoints
- `GET /api/v3/companies/{ticker}/suggested-peers?limit=4` → trả danh sách tối đa 4 mã cùng `icb_code_level3` (fallback L2) kèm `peer_basis`, `size_ratio`, `risk_tier`, `market_cap_vnd`.
- `GET /api/v3/companies/{ticker}/anomaly-drivers` → trả top 3 tỷ số có `|sector_z|` lớn nhất (kèm chiều cao/thấp hơn trung vị ngành) khi DN thuộc ngoại lai đa biến.

### 5.2. Ops QA Endpoints (`/api/v3/ops/*` — Bắt buộc `UserRole.INTERNAL_OPS`)
- `GET /api/v3/ops/qa-queue?status=OPEN|IN_REVIEW|RESOLVED`
- `POST /api/v3/ops/qa-queue/{ticket_id}/submit-revision` — Bắt buộc đính kèm `source_url` hoặc `raw_file_sha256` + `reason`, tạo phiên bản `revision_no + 1` (tuyệt đối không ghi đè bản ghi gốc) và trigger re-normalize qua Dagster.
- `POST /api/v3/ops/qa-queue/{ticket_id}/decision` (`APPROVE_REINGEST | KEEP_QUARANTINED`) + ghi `user_audit_logs`.
- `GET /api/v3/ops/issuers/lifecycle` & `POST /api/v3/ops/issuers/sync-request`.

---

## 6. Code Style & Architectural Invariants

- **Kỷ luật QoQ (`M-04` & VAS):** Chỉ tính `QoQ` trên quý rời rạc liên tiếp (`period_days` thuộc `[80, 100]` và khoảng cách 2 ngày kết thúc quý `<= 100` ngày); trả `NULL` nếu vượt biên `TT200 → TT99`, mẫu số $\le 0$, hoặc trộn `CONSOLIDATED`/`SEPARATE`.
- **Kỷ luật Bất biến Ops QA:** Cấm hoàn toàn mọi câu lệnh SQL/Parquet `UPDATE` đè số liệu BCTC gốc trong `RawArchive` hay `published_financial_marts`.
- **Kỷ luật Anti-AI-Slop UI:** `DuPontTreeChart.tsx` dùng token màu của `echarts-theme.ts` (Light/Dark >= 4.5:1), không dùng tím/cyan, hỗ trợ chế độ bảng dữ liệu thay thế cho screen reader.

---

## 7. Testing Strategy

- **Backend (`pytest`):**
  - Test `QoQ` trên quý chuyển giao Q4/2025 (`TT200`) → Q1/2026 (`TT99`): bắt buộc trả `NULL` (`MIXED_TAXONOMY`).
  - Test `suggested-peers`: ngành có 20 DN chọn đúng 4 DN quy mô gần nhất; ngành L3 chỉ có 2 DN tự fallback L2 và gắn cờ `peer_basis = "ICB_LEVEL_2"`.
  - Test `ops_router`: user `PRO_ANALYST` gọi `/api/v3/ops/qa-queue` nhận `403 Forbidden`; `INTERNAL_OPS` duyệt revision sinh `revision_no = 2` mà vẫn giữ nguyên `revision_no = 1`.
- **Frontend (`vitest`):** Test click từng nút trên `DuPontTreeChart` kích hoạt callback `onSelectMetric` mở đúng `SourceDrawer`; test nút gợi ý đối thủ nạp đúng 4 ticker vào `/compare`.

---

## 8. Boundaries

- **Always:** Mọi hành động trên `/api/v3/ops/*` đều phải ghi vết vào `user_audit_logs` từ `v3-identity-and-workspace`.
- **Ask first:** Trước khi thay đổi công thức 5/6 nhân tố DuPont hiện hành trong `src/finintel/analytics/dupont.py`.
- **Never:** Không mở công tắc `"Quarterly"` trên thanh kỳ toàn cục; không cho phép Ops sửa tay con số mà không có bằng chứng nguồn (`source_url` / `raw_file_sha256`).

---

## 9. Success Criteria

- [ ] Người dùng bấm 1 nút trên `/compare` tự động so sánh được với 4 đối thủ cùng ngành quy mô gần nhất.
- [ ] `DuPontTreeChart` hiển thị đầy đủ chuỗi 5/6 nhân tố trên `RatiosTab`, bấm từng nút mở `SourceDrawer` truy vết BCTC gốc.
- [ ] `QoQ` hiển thị cục bộ kèm nhãn cảnh báo mùa vụ, `0%` tính vượt biên `TT200→TT99`.
- [ ] Đội `INTERNAL_OPS` xử lý được hàng đợi `audit_quarantine` qua cơ chế revision bất biến có đầy đủ audit log; `0` lỗi `check`, `pytest`, `check_constraints.py`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-analyst-depth-and-ops-qa -b feat/v3-analyst-depth-and-ops-qa main
```
- Tách atomic commits:
  1. `feat(analytics): add taxonomy-safe discrete QoQ growth, peer similarity, and anomaly Z-drivers`
  2. `feat(ops): add RBAC-gated immutable revision QA queue and issuer lifecycle endpoints`
  3. `feat(frontend): add interactive DuPont tree, peer auto-suggest, local QoQ, and Ops QA UI`

---

## 11. Out of Scope (Deferred)

- Biểu đồ dòng chảy tài chính Sankey (`#54` — Polish marketing sau GA).
- Biểu đồ quỹ đạo dịch chuyển cụm nhiều năm (`#50` — Low ROI).

## 12. Open Questions

- Xác nhận tiêu chí sắp xếp khoảng cách quy mô cho `suggested-peers`: ưu tiên `market_cap_vnd` (khi có dữ liệu giá EOD) rồi fallback `BS_TOTAL_ASSETS`, hay dùng cố định `BS_TOTAL_ASSETS` để không phụ thuộc biến động giá ngắn hạn.
