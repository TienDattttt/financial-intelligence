# Sổ Tay Trực Chiến Hệ Thống Tự Trị FinIntel V2.0 (`docs/runbooks/autonomous_pipeline.md`)

> Tài liệu vận hành chuẩn giúp kỹ sư trực chiến trả lời **3 câu hỏi sống còn trong `< 30` giây**:
> 1. Luồng thu thập tự động đang sống, bị chặn rate-limit (`429`) hay bị treo `dagster-daemon`?
> 2. Snapshot đang phục vụ trên UI (`X-Snapshot-ID`) trễ bao nhiêu giờ so với kỳ công bố BCTC/giá EOD gần nhất và có bị lệch con trỏ `state.sqlite` ↔ `PostgreSQL` không?
> 3. Tỷ lệ doanh nghiệp bị cách ly ở `QG1/QG2` hoặc lệch số liệu chéo `FHSC ↔ Vietcap IQ` trong kỳ chạy vừa qua là bao nhiêu?

---

## 1. `DaemonHeartbeatStale` — Tiến trình `dagster-daemon` ngừng gửi nhịp tim quá 180 giây

- **Triệu chứng:** Cảnh báo `DaemonHeartbeatStale` bật đỏ hoặc lịch quét `seasonal_ingestion_schedule` (`30m`) / `bctc_disclosure_feed_sensor` (`15m`) không sinh tick mới quá `45` phút; chỉ số `finintel_snapshot_freshness_age_seconds{port="bctc_snapshot"}` tăng đơn điệu.
- **Lệnh kiểm tra (`< 30s`):**
  ```powershell
  docker compose ps dagster-daemon dagster-webserver
  docker compose exec dagster-daemon dagster-daemon liveness-check
  docker compose logs --tail=80 dagster-daemon
  ```
- **Hành động khắc phục:** Kiểm tra kết nối `dagster_state` trên PostgreSQL và dung lượng ổ đĩa chứa `FININTEL_DATA_ROOT`; khởi động lại dịch vụ bằng `docker compose restart dagster-daemon`, sau đó xác nhận `configure_concurrency_pools` vẫn giữ `fhsc_api_pool=1`, `public_disclosure_pool=3`, `pipeline_write_pool=1`.

---

## 2. `ProviderRateLimitCircuitOpen` — Nhà cung cấp dữ liệu trả HTTP `429` / bảo trì hoặc mở cầu chì hạn ngạch

- **Triệu chứng:** Tốc độ tăng `rate(finintel_ingestion_requests_total{outcome="rate_limited"}[15m]) > 0` hoặc khóa `fhsc_cooldown:api.fiinquant.vn` xuất hiện trong bảng trạng thái điều phối; tiến trình thu thập tạm ngưng gọi mạng.
- **Lệnh kiểm tra (`< 30s`):**
  ```powershell
  uv run --no-sync python -c "import sqlite3, os, time; p = os.environ.get('FININTEL_DATA_ROOT', 'data') + '/state/state.sqlite'; c = sqlite3.connect(p); print('Now:', time.time(), 'Pacing:', c.execute('SELECT host, next_allowed_at_epoch FROM provider_rate_limits').fetchall())"
  curl -s http://127.0.0.1:8000/metrics | Select-String "finintel_ingestion_requests_total"
  ```
- **Hành động khắc phục:** **Không** xóa khóa `provider_rate_limits` để ép gọi lại khi máy chủ nguồn đang bảo trì hoặc khóa IP; xác nhận không có script CLI ngoài luồng chạy song song không đi qua `acquire_host_pacing_slot()` (`min_interval >= 1.75s` cho FHSC), chờ hết thời gian cooldown tự động và để Vietcap IQ / dữ liệu đã lưu trong kho CAS tiếp tục phục vụ snapshot hiện hành.

---

## 3. `QualityGateCircuitBreakerTripped` — Cổng chất lượng `QG1`/`QG2` chặn xuất bản snapshot mới

- **Triệu chứng:** `finintel_wap_publish_total{outcome="quarantined"}` tăng hoặc `CriticalDataQualityError` xuất hiện trong log `entry_point="dagster_sensor"` (`max_critical_issuer_ratio` vượt ngưỡng hoặc phát hiện `orphan filing_id` ở `QG2`); `downstream_publish_sensor` dừng thử lại sau `3` lần cho cùng `upstream_key`.
- **Lệnh kiểm tra (`< 30s`):**
  ```powershell
  uv run --no-sync python -m finintel.pipeline.cli health
  curl -s http://127.0.0.1:8000/metrics | Select-String "finintel_provider_discrepancy_total|finintel_wap_publish_total"
  ```
- **Hành động khắc phục:** Kiểm tra bảng `audit_quarantine` và các mã có `CROSS_PROVIDER_DISCREPANCY_WARN` (`delta > 0.5%` giữa `FHSC` và `Vietcap IQ`) trong thư mục staging của kỳ chạy lỗi; UI vẫn an toàn phục vụ snapshot `ACTIVE` liền trước. Sau khi khoanh vùng hồ sơ lỗi chuẩn mực kế toán VAS hoặc cập nhật quy tắc chuẩn hóa, phát hành kỳ chạy mới với `upstream_key` mới.

---

## 4. `ServingPointerSplitBrain` — Lệch con trỏ snapshot giữa `state.sqlite` và PostgreSQL `finintel_serving_pointer`

- **Triệu chứng:** Header `X-Snapshot-ID` trả về từ `/api/v1/overview` khác với `active_snapshot_id` trong `state.sqlite`, hoặc `reconcile_unpromoted_snapshots()` cảnh báo có phiên bản DuckLake đã ghi trên đĩa nhưng chưa được đồng bộ lên PostgreSQL.
- **Lệnh kiểm tra (`< 30s`):**
  ```powershell
  curl -i -s http://127.0.0.1:8000/health/ready
  uv run --no-sync python -c "import sqlite3, os; p = os.environ.get('FININTEL_DATA_ROOT', 'data') + '/state/state.sqlite'; c = sqlite3.connect(p); print(c.execute('SELECT pointer_name, snapshot_id, ducklake_version FROM serving_pointers').fetchall())"
  ```
- **Hành động khắc phục:** Đảm bảo `FININTEL_DATA_ROOT` giống hệt nhau giữa container `api`, `dagster-daemon` và môi trường CLI; kích hoạt hàm đồng bộ tiến một chiều (`forward-only` theo `ducklake_version` cao nhất đã vượt `QG2`) để upsert lại `finintel_serving_pointer` trên PostgreSQL và làm sạch khóa đệm Valkey tương ứng.
