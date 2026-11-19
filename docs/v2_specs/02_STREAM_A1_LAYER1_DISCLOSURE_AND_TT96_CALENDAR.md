# ĐẶC TẢ TRIỂN KHAI WAVE 1A — STREAM A1: TẦNG 1 INGESTION, LÀM GIÀU NGÀY CÔNG BỐ AN TOÀN PIT, LỊCH LỄ TT96, RATE PACING & EARLY-STOP

* **Mã đặc tả:** `SPEC-V2-02-STREAM-A1-v1.1`
* **Wave:** `Wave 1A` (Chạy **song song** với Wave 1B `03_STREAM_A2` và Wave 1C `04_STREAM_B1` trên `git worktree` riêng — 0 file trùng lặp!)
* **Nhánh Git / Worktree:** `v2/a1-layer1` (`../finintel-wt-a1-layer1`)
* **Lỗ hổng & Hạng mục giải quyết:**
  1. **`GAP-07` (`P1 — HIGH`):** Sửa lỗi ưu tiên ngày nhập liệu muộn của Vietcap IQ hơn ngày PDF gốc CafeF với **kiểm soát đồng hạng kiểm toán chống Look-Ahead Bias (`HI-01`)**; bổ sung bảng ngày nghỉ lễ Việt Nam đã ban hành giai đoạn `2016–2026` (bao gồm cuối tuần, Tết Nguyên Đán, Giỗ Tổ, 30/4–1/5, Quốc khánh 2 ngày theo BLLĐ 2019 Điều 112) trong `_statutory_deadline` (`MED-01`); bảo toàn `audit_status` an toàn với giá trị `None` (`BLK-06`).
  2. **`GAP-08` (Phần Ingestion — tương thích ngược `BLK-07`):** Bổ sung cơ chế opt-in (`infer_calendar_schedule: false` mặc định) sinh lịch kỳ kế toán có `issuer_id`, `statement_scope` tường minh (CẤM sinh `UNKNOWN`), và `fiscal_year_overrides`; nối `disclosure_enricher.py` vào `IngestHandler` với bộ lọc `tickers` tránh $O(N^2)$ và ràng buộc bảo mật SSRF/path confinement (`HI-03`).
  3. **Tối ưu Tầng 1 (`HI-02`, phân tách Rate Limit vs Concurrency Limit):** Bật `PRAGMA synchronous = NORMAL` sau `WAL` trong `state_store.py`; bổ sung **request-rate pacing (`0.6 req/s`)** và trạng thái **`"EARLY_STOPPED"`** vào `FhscOapiAdapter` (tắt mặc định ở `BACKFILL`, chỉ kích hoạt khi đã có watermark `COMPLETE` trước đó).

---

## 1. QUYỀN SỞ HỮU FILE TUYỆT ĐỐI (`STRICT FILE OWNERSHIP`)

* **Owned Files (Chỉ được phép sửa các file sau — mọi đường dẫn đều repo-relative):**
  * `src/finintel/domain/schemas.py`
  * `src/finintel/ingestion/disclosure_enricher.py`
  * `src/finintel/ingestion/fhsc_oapi_adapter.py`
  * `src/finintel/pipeline/ingest.py`
  * `src/finintel/pipeline/state_store.py`
  * `tests/unit/test_layer1_ingestion.py` *(được ủy quyền tường minh cập nhật 4 mốc cuối tuần của `test_statutory_lag` L300–303; cấm sửa yếu các test khác!)*
  * `tests/unit/test_layer4_disclosure_enrichment.py`
* **Read-Only Contract Files:** `docs/layers/LAYER_1_INGESTION.md`, `src/finintel/ingestion/archive.py`, `src/finintel/pipeline/runner.py`.
* **Forbidden Files:** Toàn bộ `config/`, `src/finintel/pipeline/runner.py`, `src/finintel/normalization/`, `src/finintel/storage/`, `src/finintel/analytics/`, `src/finintel/ml/`, `dashboard/`, `data/live_fhsc_universe/`, `AGENT.md`.

---

## 2. NGUỒN TÀI LIỆU & PHÁP LÝ BẮT BUỘC ĐỐI CHIẾU TRƯỚC KHI CODE

1. **Thông tư 96/2020/TT-BTC (Điều 10 & Điều 14) & Thông tư 68/2024/TT-BTC:**
   * `https://staticfile.hsx.vn/Uploads/LocalFiles/c79e12a5dbf749b6983b63e3242326a3/20210101_Thong%20tu%2096.2020%20TT-BTC.pdf`
   * Thời hạn tối đa BCTC hợp nhất/công ty mẹ: Quý tự lập `+30 ngày`, Quý soát xét `+45 ngày`, Bán niên/Q2 soát xét `+60 ngày`, Năm kiểm toán `+90 ngày`. *(Ghi nhận trong docstring rằng BCTC riêng lẻ `SEPARATE` có hạn ngắn hơn ở một số trường hợp như 20d/45d — hệ thống giữ cận ngoài bảo thủ TT96 để chống Look-Ahead Bias khi `published_at` khuyết.)*
2. **Bộ luật Dân sự 2015 (Điều 147 & Điều 148 khoản 5) & Bộ luật Lao động 2019 (Điều 112):**
   * Khi ngày cuối cùng của thời hạn rơi vào ngày nghỉ cuối tuần hoặc ngày nghỉ lễ (Tết Dương lịch `01/01`, Tết Nguyên Đán 5 ngày làm việc, Giỗ Tổ Hùng Vương `10/03` ÂL, `30/04`, `01/05`, Quốc khánh `02/09` + 1 ngày liền kề), thời hạn kết thúc tại ngày làm việc kế tiếp.
3. **SQLite Official Documentation — WAL Mode & `PRAGMA synchronous = NORMAL`:**
   * `https://www.sqlite.org/wal.html`
   * `https://www.sqlite.org/pragma.html#pragma_synchronous`

---

## 3. ĐẶC TẢ KỸ THUẬT CHI TIẾT

### 3.1. Hạn Nộp Luật định & Bảng Ngày Nghỉ Lễ Việt Nam (`src/finintel/domain/schemas.py`, `L220-L255`)
* Xây dựng hàm thuần túy không phụ thuộc bên ngoài `_roll_to_next_vn_business_day(target: date) -> date`:
  * **Bảng ngày nghỉ lễ cố định dương lịch (`_VN_FIXED_HOLIDAYS`):** `(1, 1)`, `(4, 30)`, `(5, 1)`, `(9, 2)`.
  * **Bảng ngày nghỉ lễ đã ban hành theo Thông báo Bộ LĐ-TB&XH / Chính phủ giai đoạn `2016–2026` (`_VN_GAZETTED_HOLIDAYS: frozenset[date]`):** Bao gồm các kỳ nghỉ **Tết Nguyên Đán** (đặc biệt giải quyết ca `2024-Q4` hạn `2025-01-30` rơi vào Mùng 2 Tết Ất Tỵ!), **Giỗ Tổ Hùng Vương (`10/03` ÂL)**, và **ngày nghỉ liền kề Quốc khánh (`01/09` hoặc `03/09` từ 2021 trở đi)** cho từng năm `2016..2026`. Với các năm ngoài bảng (`> 2026` hoặc `< 2016`), tự động áp dụng quy tắc tất định cuối tuần (`weekday() >= 5`) + `_VN_FIXED_HOLIDAYS` và ghi rõ giới hạn trong docstring.
  * Vòng lặp dịch chuyển ngày kết thúc **BẮT BUỘC đặt trong khối `try ... except OverflowError`** của `_statutory_deadline` (`schemas.py:235-240`) để bảo toàn ngoại lệ `ContractViolationError` tại biên `date.max`.
  * **Tính an toàn PIT:** Dịch hạn luật định sang ngày làm việc tiếp theo chỉ làm `effective_available_at` (khi `published_at is None`) thận trọng hơn (KHÔNG BAO GIỜ sớm hơn) — 100% chống Look-Ahead Bias.
* **Cập nhật `PaginationStatus` (`domain/schemas.py:58-60`):** Bổ sung giá trị hợp lệ **`"EARLY_STOPPED"`** vào `Literal[...]` (`HI-02`).

### 3.2. Gộp Ngày Công bố An toàn PIT & Bảo toàn `audit_status` (`src/finintel/ingestion/disclosure_enricher.py`)
1. **Trong `merge_disclosure_records(vietcap_records, cafef_records, *, period_end_dates: Mapping[DisclosureSlot, date] | None = None)` (`BLK-07` `SEAM-01`, `HI-01`, `BLK-06`):**
   * **Bảo toàn cơ chế đếm `skipped_pre_period_end` (`SEAM-01`):** KHÔNG tự ý xóa bỏ bản ghi duy nhất của một slot khi nó có `published_at < period_end_date` — hãy để `enrich_fhsc_manifest_disclosures()` tiếp tục là nơi từ chối và tăng `skipped_pre_period_end` (giữ nguyên hành vi `test_layer4_disclosure_enrichment.py:306-353`).
   * **Quy tắc ưu tiên CafeF vs Vietcap IQ trên cùng 1 `DisclosureSlot` (Chống Look-Ahead Bias — `HI-01`):**
     * Xác định ngày kết thúc kỳ ước tính hoặc chính xác `slot_end` (từ `period_end_dates` nếu có, hoặc mặc định cuối quý/năm dương lịch của `(fiscal_year, fiscal_quarter)`).
     * **Cấm bản ghi CafeF bất hợp lệ ghi đè Vietcap IQ hợp lệ:** Chỉ xét `cafef_rec` là ứng viên hợp lệ khi `cafef_rec.published_at >= slot_end` (và nếu `vci_rec.published_at < slot_end` còn `cafef_rec.published_at >= slot_end` thì chọn `cafef_rec`).
     * **Kiểm soát đồng hạng kiểm toán (`Audit-Tier PIT Guard` — `HI-01`):**
       - Với `PeriodType.ANNUAL`: `cafef_rec` chỉ được thay thế `vci_rec` sớm hơn (`cafef_rec.published_at < vci_rec.published_at`) khi **cùng hạng kiểm toán** (`cafef_rec.audit_status == AuditStatus.AUDITED` hoặc cả hai đều không khẳng định `UNAUDITED` đối lập với `AUDITED`) — tuyệt đối KHÔNG để ngày BCTC tự lập/chưa kiểm toán gắn lên số liệu BCTC kiểm toán năm!
       - Với `PeriodType.QUARTER` quý 2 (`fiscal_quarter == 2`): nếu `vci_rec.audit_status == AuditStatus.REVIEWED`, không để `cafef_rec.audit_status == AuditStatus.UNAUDITED` ghi đè ngày sớm hơn.
       - Với `PeriodType.QUARTER` (Q1, Q3, Q4 — bao gồm Q4 bị VCI ghi đè bởi ngày kiểm toán năm): khi `cafef_rec` hợp lệ và `cafef_rec.published_at < vci_rec.published_at` (dùng chặt `<`, bỏ mâu thuẫn `<=`), chọn `cafef_rec`.
     * **Hợp nhất `audit_status` an toàn với `None` (`BLK-06`):** Khi hai nguồn cùng hạng hợp lệ và một bên có `audit_status is not None and audit_status != AuditStatus.UNKNOWN` trong khi bên được chọn là `None` hoặc `AuditStatus.UNKNOWN`, chỉ kế thừa `audit_status` khi điều đó **không** làm nâng khống hạng kiểm toán của bản ghi tự lập sớm hơn (`UNAUDITED` không bao giờ biến thành `AUDITED`!).
2. **Trong `enrich_fhsc_manifest_disclosures()` (`L515-L568`, `BLK-06`, `HI-01`, `HI-03`, `MED-01`):**
   * Bổ sung tham số tùy chọn **`tickers: Sequence[str] | None = None`** để lọc SQL `WHERE ticker_at_filing IN (...)` (triệt tiêu $O(N^2)$ quét toàn bộ manifest khi gọi theo từng mã!).
   * **Bảo vệ `revision_no >= 2` (`HI-01`):** Không ghi đè `published_at` của bản ghi đính chính (`revision_no >= 2`) bằng ngày công bố của bản ghi gốc (`revision_no == 1`) trừ khi bản ghi disclosure chỉ định rõ revision tương ứng.
   * **Cập nhật `audit_status` an toàn `mypy --strict` (`BLK-06`):**
     ```python
     next_audit_status = (
         rec.audit_status
         if (
             meta.audit_status == AuditStatus.UNKNOWN
             and rec.audit_status is not None
             and rec.audit_status != AuditStatus.UNKNOWN
         )
         else meta.audit_status
     )
     enriched_meta = replace(
         meta,
         published_at=rec.published_at,
         audit_status=next_audit_status,
     )
     ```
   * **Cập nhật lại `is_filed_late` khi luật lịch lễ thay đổi (`MED-01`):** So sánh cả `is_filed_late` đã lưu trong `filings_manifest` với `enriched_meta.is_filed_late`; nếu khác (do áp dụng lịch nghỉ lễ TT96 mới), vẫn cập nhật lại cột `is_filed_late` / `effective_available_at` trên manifest một cách tất định.
   * **Kiểm tra khớp mã CK trong `parse_cafef_bctc_disclosures()` (`HI-03`):** Xác minh mã CK / tên file trên bảng HTML CafeF tương ứng với `ticker` đang phân tích trước khi phát sinh `DisclosureRecord`.

### 3.3. `StateStore`, FHSC Request-Rate Pacing + `EARLY_STOPPED`, và `IngestHandler`
1. **`src/finintel/pipeline/state_store.py` (`L85-L94`, `LOW-01`):**
   * Bổ sung `conn.execute("PRAGMA synchronous = NORMAL")` ngay sau `conn.execute("PRAGMA journal_mode = WAL")` (khớp đúng tên biến `conn` hiện hữu!).
2. **`src/finintel/ingestion/fhsc_oapi_adapter.py` (`L149-L201`, `HI-02`, Phân tách Rate Limit vs Concurrency Limit):**
   * **Phân tách rõ Rate-Limit Pacing (tại Adapter) khỏi Concurrency Pool (tại Dagster):**
     * Bổ sung tham số tùy chọn `min_request_interval_seconds: float | None = None` (mặc định tính từ `1.0 / source.spec.rate_per_second`, tức `~1.67s` cho `0.6 req/s`, và nhận `sleeper: Callable[[float], None]` / `clock: Callable[[], float]` để unit tests chạy tức thì không tốn thời gian sleep!). Đảm bảo khoảng cách giữa 2 lần gọi `source.raw(...)` liên tiếp luôn $\ge \text{min\_request\_interval\_seconds}$.
   * **Cơ chế Early-Stop An toàn (`HI-02`):**
     * Bổ sung tham số tùy chọn `early_stop_predicate: Callable[[tuple[RawFiling, ...]], bool] | None = None` (mặc định `None` — **tắt hoàn toàn** trong `TriggerMode.BACKFILL`!).
     * Trong vòng lặp phân trang `fetch()`:
       1. Kiểm tra `THROTTLED` **TRƯỚC TIÊN** (không bao giờ nuốt HTTP 429!).
       2. Chỉ đánh giá `early_stop_predicate(filings)` khi `status == "IN_PROGRESS"` (chưa `THROTTLED`, chưa `empty`), `bool(filings) is True`, và **`not deferred`** (không bao giờ dừng sớm trên trang còn kỳ bị `deferred`!).
       3. Nếu predicate trả `True` $\rightarrow$ đặt `status = "EARLY_STOPPED"`, `yield RawFilingBundle(payload, filings, deferred, "EARLY_STOPPED")`, reset `_next_page = 1`, `_seen.clear()`, và `return`.
     * Điều kiện kích hoạt predicate trong `IngestHandler` (chỉ ở `SCHEDULED_CRON` / `CLI_MANUAL` khi bật `incremental_lookback_years: int = 2` với tham số `as_of_year: int` được inject — không hardcode `date.today()`):
       - Tổ chức phát hành **phải có watermark đã hoàn tất `COMPLETE` trước đó** cho `(ticker, statement_type, period_type)` (nếu trước đó từng bị ngắt giữa chừng khi backfill, KHÔNG dừng sớm để không mất lịch sử trang 4+!).
       - Mọi filing trong trang đều đã tồn tại trong `filings_manifest` với cùng `canonical_content_sha256` VÀ có `fiscal_year < as_of_year - incremental_lookback_years`.
3. **`src/finintel/pipeline/ingest.py` (`L179-L200`, `BLK-05`, `BLK-07`, `HI-03`):**
   * **Tương thích ngược `infer_calendar_schedule` (`BLK-07`):**
     * Mặc định **`infer_calendar_schedule: bool = False`** (giữ nguyên 100% hành vi `StepStatus.INCOMPLETE` trên `test_layer1_ingestion.py:1086-1118` khi `fhsc_filings: []`!).
     * Khi cấu hình bật tường minh `ingestion.infer_calendar_schedule: true`: bắt buộc khai báo `default_statement_scope` thuộc `{"CONSOLIDATED", "SEPARATE"}` (**CẤM `StatementScope.UNKNOWN`** vì sẽ gây deadlock ở `DisclosureRecord` và Layer 2/4A!), nhận bảng `issuer_id_map` (hoặc `issuers.parquet`), và hỗ trợ `fiscal_year_overrides: {ticker: "MM-DD"}` cho các doanh nghiệp năm tài chính lệch (`HSG`, `CTD`, `TIX`).
   * **Tích hợp `disclosure_enricher.py` An toàn Bảo mật (`BLK-05`, `HI-03`):**
     * Khi `ingestion.disclosure_enrichment.enabled: true`:
       - Kiểm tra đường dẫn `raw_disclosures_dir` qua `_resolve_confined()`, từ chối symlink, giới hạn kích thước tệp đọc ($\le 20\text{ MiB}$/file), chuẩn hóa quy ước tên file `{TICKER}.json` / `{TICKER}.html` cùng `source_url` nguồn gốc.
       - Nếu dùng DI fetcher (từ Dagster hoặc CLI online): bắt buộc kiểm tra URL nằm trong HTTPS host allowlist (`{"iq.vietcap.com.vn", "s.cafef.vn", "cafef.vn"}`), `follow_redirects=False`, timeout $\le 15\text{s}$, chính sách lỗi cấu hình được (`on_error: "warn" | "fail"`, mặc định `"warn"` để không làm mất dữ liệu FHSC đã tải).
       - Gọi `enrich_fhsc_manifest_disclosures(..., tickers=ingested_tickers)` **1 lần cuối bước ingest** (không gọi lặp trong vòng for từng mã!).
       - Ghi thống kê (`matched_filings`, `upgraded_filings`, `skipped_pre_period_end`) qua **`logger.info("Disclosure enrichment completed", extra={"event": "disclosure_enrichment_completed", "run_id": context.run_id, "stage": context.stage, "details": {...}})`** — **KHÔNG** ghi vào `StepOutcome.details` (vì `StepOutcome` trong `runner.py:122-128` không có trường `details`!).
   * Đảm bảo `IngestHandler` công nhận `PaginationStatus == "EARLY_STOPPED"` là hoàn tất thành công (`StepStatus.SUCCEEDED`), không đánh dấu `INCOMPLETE`.

---

## 4. KIỂM THỬ TDD & TIÊU CHÍ NGHIỆM THU (`DEFINITION OF DONE`)

1. **`tests/unit/test_layer1_ingestion.py`:**
   * Cập nhật có chủ đích 4 mốc rơi vào T7/CN của `test_statutory_lag` (`L300-303`: `2026-02-14` T7 $\rightarrow$ `2026-02-16` T2; `2026-03-01` CN $\rightarrow$ `2026-03-02` T2) và bổ sung test lễ `2025-Q1` (`2025-04-30` $\rightarrow$ `2025-05-02`), `2026-Q1` (`2026-04-30` $\rightarrow$ `2026-05-04`), **`2024-Q4` Tết Ất Tỵ (`2025-01-30` $\rightarrow$ `2025-02-03`)**, và `OverflowError` gần `date.max`.
   * Test `PRAGMA synchronous` của `StateStore` bằng `1` (`NORMAL`).
   * Test `FhscOapiAdapter`: request-rate pacing (xác minh `sleeper` được gọi đúng khoảng `1/0.6s` giữa các trang), `EARLY_STOPPED` dừng đúng sau trang đầu khi thỏa predicate + `not deferred` + có watermark `COMPLETE`, KHÔNG nuốt `THROTTLED` (HTTP 429), KHÔNG dừng sớm khi `BACKFILL` hoặc thiếu watermark `COMPLETE`.
   * Test `IngestHandler`: `fhsc_filings: []` mặc định vẫn trả `StepStatus.INCOMPLETE`; khi bật `infer_calendar_schedule: true` với `CONSOLIDATED` & `fiscal_year_overrides`, ingest thành công và chạy `disclosure_enricher` 1 lần với path confinement + SSRF host rejection test.
2. **`tests/unit/test_layer4_disclosure_enrichment.py`:**
   * Giữ nguyên 100% test `skipped_pre_period_end == 1` (`L353`).
   * Test `merge_disclosure_records()`: CafeF đúng hạn thắng VCI trễ trên Q1/Q3/Q4/Annual cùng hạng kiểm toán; **CafeF `UNAUDITED` KHÔNG được thắng VCI `AUDITED` trên `ANNUAL` hoặc `REVIEWED` trên `Q2`** (`HI-01`); CafeF `published_at < period_end_date` không ghi đè VCI hợp lệ; `rec.audit_status = None` không gây lỗi `mypy` hay runtime crash (`BLK-06`); `revision_no >= 2` không bị ghi đè ngày của rev 1.
   * Test `tickers` filter chỉ quét đúng mã được chọn và cập nhật lại `is_filed_late` khi lịch lễ thay đổi.
3. **Cổng chất lượng bắt buộc:** `ruff format --check src tests`, `ruff check src tests`, `mypy --strict src/finintel tests/unit`, `pytest -q` xanh 100%.

---

## 5. PROMPT GIAO VIỆC MẪU CHO CODING AGENT (COPY-PASTE READY)

```text
Đọc AGENT.md, docs/v2_specs/00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md và docs/v2_specs/02_STREAM_A1_LAYER1_DISCLOSURE_AND_TT96_CALENDAR.md.

Thực hiện ĐÚNG phạm vi Wave 1A (Stream A1) trên git worktree riêng v2/a1-layer1:
1. Chỉ chỉnh sửa các tệp trong mục "Owned Files" của 02_STREAM_A1 (KHÔNG sửa runner.py, config/, archive.py, AGENT.md; ghi log enrichment bằng logger.info extra={"details": ...}, không dùng StepOutcome.details).
2. Đối chiếu Thông tư 96/2020/TT-BTC, BLDS 2015 Điều 147–148, BLLĐ 2019 Điều 112 và tài liệu SQLite WAL synchronous=NORMAL; ghi khối Verified Sources trong docstring.
3. Đảm bảo: (a) guard rec.audit_status is not None; (b) bảng ngày lễ VN 2016–2026 gồm Tết & Quốc khánh 2 ngày; (c) audit-tier matching chống PIT look-ahead khi gộp CafeF vs VCI; (d) giữ infer_calendar_schedule=False mặc định & giữ skipped_pre_period_end trong enrich_fhsc_manifest_disclosures; (e) request-rate pacing 0.6 req/s + PaginationStatus "EARLY_STOPPED" kiểm tra sau THROTTLED & watermark COMPLETE; (f) confinement + HTTPS allowlist + tickers filter.
4. Chạy xanh 100%: ruff format --check, ruff check, mypy --strict, pytest -q. Nộp git diff --name-only và log chạy lệnh thật.
```
