# CONSTRAINTS.md — FinIntel V2.0 Mechanical Quality Contract

## Floor (Bất Khả Xâm Phạm Trên Mọi PR)
- Zero new `# type: ignore`, `@ts-ignore`, `@ts-expect-error`, `eslint-disable`, hoặc `# noqa` chưa đăng ký ngoại lệ.
- Zero `.skip`, `@pytest.mark.skip`, `xit`, `xdescribe` mới hoặc xóa ca kiểm thử hiện có để ép xanh CI.
- Zero `.fill_null(0)` mù quáng lên chỉ tiêu kế toán, zero `iter_rows(` trong đường nóng tính toán Polars, zero tính toán công thức tài chính ở `serving/` hoặc `frontend/`.
- Zero ghi/sửa/xóa vào `data/live_fhsc_universe/` và `data/universe_600_800/` trong mọi tác vụ phát triển và kiểm thử tự động (mọi test đều chạy offline trên `tmp_path`).
- Zero trộn chung commit `Behavior-Preserving` với commit `Behavior-Changing`.
- Không bao giờ sửa tệp `CONSTRAINTS.md` để hạ ngưỡng trong cùng một PR tính năng.

## Enforced With Numbers (Áp Dụng Ngay Từ TASK-00)

| Chiều kiểm soát | Ngưỡng bắt buộc | Lệnh kiểm chứng đa nền tảng (Windows & Linux) | Giai đoạn chạy |
|---|---|---|---|
| Định dạng & Lint Python | `0` lỗi | `uv run --no-sync python -m ruff format --check src scripts tests && uv run --no-sync python -m ruff check src scripts tests` | `BUILD` (`< 5s`) |
| Kiểu tĩnh Python (Core & ML) | `0` lỗi | `uv run --no-sync python -m mypy --strict src/finintel/domain src/finintel/ingestion src/finintel/normalization src/finintel/storage src/finintel/analytics src/finintel/ml src/finintel/quality src/finintel/pipeline scripts/spike_vnstock6.py scripts/spike_option_b.py tests/unit/test_layer4_analytics.py tests/unit/test_layer4_forensic_and_stress.py tests/unit/test_layer6_quality.py` | `VERIFY` (`< 30s`) |
| Kiểu tĩnh Python (Toàn kho) | `0` lỗi | `uv run --no-sync python -m mypy --strict src/finintel scripts/spike_vnstock6.py scripts/spike_option_b.py tests/unit` *(chạy với `--group platform`)* | `CI` |
| Kiểm thử đơn vị & tích hợp Python | `100%` PASS (`0` failed) | `uv run --no-sync python -m pytest -q` | `VERIFY` (theo module `< 60s`) / `CI` (toàn bộ) |
| Kiểu tĩnh & Lint Frontend | `0` lỗi | `npm --prefix frontend run check` | `BUILD` (`< 15s`) |
| Kiểm thử đơn vị Frontend | `100%` PASS (`0` failed) | `npm --prefix frontend run test:unit` | `VERIFY` (`< 15s`) |
| Ràng buộc kích thước Module & Floor Guard | Module mới `<= 1.000` dòng (`serving/queries/*` `< 1.000` dòng); file legacy `> 1.000` dòng không tăng ròng quá `+10` dòng | `uv run --no-sync python scripts/check_constraints.py` | `BUILD` (`< 3s`) |
| Tốc độ gọi mạng FHSC OAPI | `<= 0.6 req/s` (`min_interval >= 1.75s` liên tiến trình) | `uv run --no-sync python -m pytest tests/unit/test_fhsc_pacing_lease.py -q` *(bật từ `TASK-03`)* | `VERIFY` |
| Độ trễ API đọc (`p95`) | L1/Valkey ấm `<= 50 ms`; DuckLake lạnh `<= 200 ms`; Stress-Test `<= 300 ms` | `uv run --no-sync python -m pytest tests/unit/test_serving_latency_budget.py -q` *(nâng từ Staged ở `TASK-09`)* | `VERIFY` (`< 15s`) |
| WCAG 2.2 AA (`axe-core`) | `0` `critical` VÀ `0` `serious` (Light & Dark; `1440x900`, `720x450` 200% zoom, `320x800` không tràn ngang) | `npm --prefix frontend run test:e2e` *(nâng từ Staged ở `TASK-12`)* | `E2E` |
| Web Performance (CWV cục bộ) | `LCP <= 2500 ms`, `INP <= 200 ms`, `CLS <= 0.10` | `npm --prefix frontend run test:e2e -- --grep "CWV"` *(nâng từ Staged ở `TASK-12`)* | `E2E` (`< 15s`) |

## Staged Gates (Đã Nâng Đủ 3 Cổng Lên Enforced Ở TASK-09 & TASK-12)

> Toàn bộ 3 cổng phân kỳ (`Độ trễ API đọc p95`, `WCAG 2.2 AA axe-core`, `Web Performance CWV cục bộ`) đã vượt ngưỡng đo thực nghiệm và chuyển sang bảng **Enforced With Numbers** phía trên.

## Ratchets (Chốt Chặn Không Lùi)
- **Kích thước file (`code-simplification`):** Sau `TASK-01`, toàn bộ các tệp trong `src/finintel/serving/queries/` bắt buộc `< 1.000` dòng/file. Mọi logic mới thuộc `cap-01..cap-04` phải tách thành module chuyên biệt (`ssrf_guard.py`, `restatements.py`, `merton_dtd.py`, `freshness.py`) thay vì phình to `intermediates.py`, `incremental.py` hay `api.py`.
- **Ranh giới Serving (`test_layer6_quality.py`):** Không một file `*.py` nào trong `src/finintel/serving/**` được import `finintel.normalization`, `finintel.ingestion`, `streamlit`, `dashboard` hoặc chứa chuỗi `"data/raw"` / `"data/curated"` (kể cả trong docstring/comment) hay import trực tiếp thư viện nhóm `platform` ở top-level trong `serving/queries/`.

## Exceptions (Ngoại Lệ Có Thời Hạn)
| Vị trí | Quy tắc miễn trừ tạm | Lý do kỹ thuật | Điều kiện gỡ bỏ |
|---|---|---|---|
| `src/finintel/serving/api.py` & `src/finintel/transform/incremental.py` (cùng các module legacy `> 1.000` LOC tại mốc rẽ nhánh `main`) | Cho phép tồn tại `> 1.000` LOC nhưng cấm tăng ròng `> +10` LOC | Tệp hiện hữu đã ổn định qua kiểm thử Red-Team v1.1 | Tách router/module con ở đợt bảo trì V2.1 sau khi khóa xong 5 năng lực V2.0 |
