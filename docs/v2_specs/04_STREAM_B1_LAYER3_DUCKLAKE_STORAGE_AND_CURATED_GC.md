# ĐẶC TẢ TRIỂN KHAI WAVE 1C — STREAM B1: TẦNG 3 STORAGE — DUCKLAKE LAKEHOUSE / SORTED PARQUET + `ReadWriteLock`, PUSHDOWN, CRASH RECOVERY & CURATED GC

* **Mã đặc tả:** `SPEC-V2-04-STREAM-B1-v1.1`
* **Wave:** `Wave 1C` (Chạy **song song** với Wave 1A `02_STREAM_A1` và Wave 1B `03_STREAM_A2` sau khi có kết quả `selected_storage_backend` từ Gate B0 `01_GATE_B0`)
* **Nhánh Git / Worktree:** `v2/b1-layer3` (`../finintel-wt-b1-layer3`)
* **Mục tiêu cốt lõi:**
  1. **Hai Chế độ Lưu trữ Sạch (Không dùng `delta-rs` — đóng `BLK-11`):**
     * Backend `"ducklake"` (khi Gate B0 xác nhận `PASS`): Multi-Table Atomic Snapshot Isolation (`SQLite` catalog $\rightarrow$ `PostgreSQL` đa máy qua DuckDB `CREATE SECRET`), `SET SORTED BY` DDL, `ducklake_snapshots()` $\rightarrow$ explicit `BIGINT[]` `versions` expiry (`MISS-02`), và tuần tự hóa mọi writer bằng `pipeline.lock` (`MISS-04`).
     * Backend `"parquet"` (mặc định khi `storage:` chưa cấu hình — tương thích ngược 100%, đồng thời là phương án dự phòng chính nếu WDAC chặn DuckLake): Sorted Parquet (loại bỏ `shutil.copy2` bỏ qua sort) + **`filelock.ReadWriteLock(..., is_singleton=False)`** + grace period GC.
  2. **Hợp đồng `ParquetRepository` Hoàn chỉnh (`BLK-10`, `BLK-15`, `HI-10`):** Backend auto-discovery từ `data_root`, `*.parquet` $\rightarrow$ catalog table normalization, `KNOWN_TABLES` allowlist, `get_snapshot_metadata()`, `snapshot_session(connection=...)` ghim 1 phiên bản xuyên suốt request, và `read_table(columns, filters, connection=...)` an toàn chống SQLi.
  3. **Khử đọc lặp 4× & Dọn rác `data/curated/` An toàn Tuyệt đối (`BLK-12`, `HI-09`):** Shared DataFrame/DuckDB PIT helper cho `build_latest_facts_table` và `collect_curated_garbage()` bảo vệ mọi snapshot đang hoạt động/lưu giữ, đa nguồn, bằng chứng quarantine fail-closed, và path-confinement.

---

## 1. QUYỀN SỞ HỮU FILE TUYỆT ĐỐI (`STRICT FILE OWNERSHIP`)

* **Owned Files (Chỉ được phép sửa/tạo các file sau — mọi đường dẫn đều repo-relative):**
  * `config/settings.yaml` *(chỉ điền khối `storage:` đã khai báo ở commit nền)*
  * `src/finintel/storage/__init__.py` *(export `DuckLakeStore`, `collect_curated_garbage` — `BLK-05`)*
  * `src/finintel/storage/ducklake_store.py` *(mới)*
  * `src/finintel/storage/curated_gc.py` *(mới)*
  * `src/finintel/storage/repository.py`
  * `src/finintel/storage/snapshot_publisher.py`
  * `src/finintel/storage/manifest_export.py`
  * `src/finintel/pipeline/publish.py`
  * `src/finintel/pipeline/cli.py` *(parse khối `storage:` vào `Settings` và truyền sang `PublishHandler` — `BLK-05`)*
  * `tests/unit/test_layer3_storage.py` *(được ủy quyền cập nhật `L161-165` từ `FileLock` sang `ReadWriteLock(..., is_singleton=False).write_lock(timeout=0)` — `HI-08`)*
  * `tests/unit/test_layer3_ducklake_storage.py` *(mới)*
* **Read-Only Contract Files:** `docs/evidence/option_b_spike_report.json`, `src/finintel/storage/schemas.py`.
* **Forbidden Files:** `src/finintel/storage/schemas.py` *(thuộc Wave 2A)*, toàn bộ `src/finintel/{domain,ingestion,normalization,analytics,ml,quality}/`, `dashboard/`, `data/live_fhsc_universe/`, `AGENT.md`.

---

## 2. NGUỒN KHOA HỌC & TÀI LIỆU CHÍNH THỨC BẮT BUỘC ĐỐI CHIẾU TRƯỚC KHI CODE

1. **Moerkotte, G. (1998).** *"Small Materialized Aggregates: A Light Weight Index Structure for Data Warehousing."* **VLDB 1998**.
2. **Zeng, X. et al. (2023).** *"An Empirical Evaluation of Columnar Storage Formats."* **PVLDB Vol. 17, No. 2** · `https://github.com/XinyuZeng/EvaluationOfColumnarFormats`.
3. **Yang, Z. et al. (2020).** *"Qd-tree: Learning Data Layouts for Big Data Analytics."* **ACM SIGMOD 2020**.
4. **Armbrust, M. et al. (2020).** *"Delta Lake: High-Performance ACID Table Storage over Cloud Object Stores."* **PVLDB Vol. 13, No. 12**.
5. **Michael, M. M. (2004)** (*IEEE TPDS*, Vol. 15, No. 6) & **Hart, T. E. et al. (2007)** (*JPDC*, Vol. 67, No. 12) — hai pha thu hồi tài nguyên có grace period.
6. **Tài liệu chính thức bắt buộc đối chiếu:**
   * DuckLake: `https://ducklake.select/docs/stable/` & `https://ducklake.select/docs/stable/duckdb/maintenance/expire_snapshots` (xác nhận `versions => BIGINT[]` là danh sách ID cần xóa và không gộp với `older_than`; `SET SORTED BY` bảo toàn sắp xếp khi compaction; SQLite final `COMMIT` không retry lock error).
   * DuckDB Parquet Tips & Secrets: `https://duckdb.org/docs/stable/data/parquet/tips`, `https://duckdb.org/docs/stable/configuration/secrets_manager`.
   * `filelock.ReadWriteLock`: `https://py-filelock.readthedocs.io/`.

---

## 3. ĐẶC TẢ KỸ THUẬT CHI TIẾT

### 3.1. Quy chuẩn Bố cục Vật lý & Row Group Size Thực tế (`HI-07`)
Khai báo bảng hằng `TABLE_PHYSICAL_LAYOUT` dùng chung cho cả `DuckLakeStore` (`SET SORTED BY`) và `SnapshotPublisher` (loại bỏ `shutil.copy2` ở `snapshot_publisher.py:235` — luôn ghi Parquet đã sắp xếp kèm `row_group_size`!):

| Bảng (`KNOWN_TABLES` Allowlist) | Khóa Sắp xếp Vật lý (`ORDER BY` / `SET SORTED BY`) | `row_group_size` (Tối ưu cho 383–1,600 mã) |
|---|---|---|
| `financial_facts.parquet`, `latest_facts.parquet` | `["issuer_id", "statement_scope", "period_type", "period_end_date", "metric_id"]` | `25_000` |
| `financial_ratios.parquet` | `["issuer_id", "statement_scope", "period_type", "period_end_date"]` | `2_000` *(~10 row groups ở 383 mã $\implies$ skipping thực tế $> 85\%$!)* |
| `industry_benchmarks.parquet` | `["statement_scope", "period_type", "metric_or_ratio_name", "period_end_date", "peer_group_level", "icb_code_used", "issuer_id"]` | `25_000` *(đã thêm `statement_scope` — `HI-07`)* |
| `accounting_red_flags.parquet`, `ml_scores_and_explanations.parquet`, `quality_summary.parquet`, `audit_quarantine.parquet` | `["issuer_id", "period_end_date"]` | `2_000` |
| `filings_manifest.parquet` | `["filing_id"]` | `25_000` |
| `issuers.parquet` | `["ticker", "issuer_id"]` | `5_000` |
| `unmapped_metrics.parquet` *(chỉ khi `publish_unmapped: true`)* | `["issuer_id", "period_end_date"]` | `25_000` |

### 3.2. `DuckLakeStore` & Nhất quán Liên-CSDL + Crash Recovery (`src/finintel/storage/ducklake_store.py`)
1. **Cấu hình (`config/settings.yaml` dưới `storage:`):**
   * Khi khối `storage:` không có trong `settings.yaml`: mặc định **`backend = "parquet"`** (bảo toàn 100% tương thích ngược tests & CLI cũ — `BLK-10`).
   * Cấu hình DuckLake: `backend: "ducklake"`, `catalog.driver: "sqlite" | "postgres"`, `catalog.sqlite_path: "../data/metadata/ducklake_catalog.sqlite"`, `catalog.postgres_dsn_env: "FININTEL_DUCKLAKE_PG_DSN"` (dùng DuckDB `CREATE SECRET` từ biến môi trường, **CẤM** nhúng mật khẩu/DSN vào chuỗi SQL hay log — `HI-16`), `data_path: "../data/lakehouse/data"`, `catalog_alias: "finintel_lake"`, `retention: {keep_snapshots: 2, min_snapshot_age_seconds: 3600, curated_keep_runs: 2}`, `publish_unmapped: false`.
   * Khi dùng SQLite catalog: khởi tạo sẵn `PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL; PRAGMA busy_timeout = 5000;` trên file catalog SQLite.
2. **Hợp đồng Bảng Metadata `_snapshot_audit` trong DuckLake (`BLK-10`):**
   * Schema cố định: `(snapshot_id VARCHAR PRIMARY KEY, ducklake_version BIGINT NOT NULL, run_id VARCHAR NOT NULL, published_at VARCHAR NOT NULL, tables_json VARCHAR NOT NULL)` — ánh xạ chính xác `snap_<id>` sang `ducklake_version` nguyên tử và thời điểm `published_at` mà `DashboardQueryService` cần.
3. **Giao thức Xuất bản Nguyên tử 2 Pha, Tuần tự hóa Writer & Khôi phục sau Crash (`MISS-04`, Cross-DB Consistency):**
   * **Tuần tự hóa mọi writer (`pipeline.lock`):** Cả `publish_snapshot()`, `maintain()`, `rollback_snapshot()`, và `collect_curated_garbage()` đều phải giữ khóa ghi thống nhất (`pipeline.lock` hoặc `StorageWriteLock` chung) để không bao giờ xảy ra 2 writer đồng thời đụng lỗi SQLite final `COMMIT` không retry của DuckLake (`MISS-04`).
   * **Thứ tự 2 pha bất biến:**
     1. *Pre-validation:* Chạy `canonicalize_financial_facts`, `canonicalize_table`, `validate_analytics_table` trên toàn bộ bảng **TRƯỚC** khi mở transaction DuckLake.
     2. *Pha 1 (DuckLake Commit):* Trong **1 transaction DuckLake duy nhất (`BEGIN TRANSACTION; ... COMMIT;`)**, ghi toàn bộ bảng đã sắp xếp (áp `SET SORTED BY` khi tạo bảng) và chèn 1 dòng vào `_snapshot_audit` (lấy `ducklake_version` tương ứng).
     3. *Pha 2 (`state.sqlite` Promotion):* Gọi `state.publish_snapshot(run_id, snapshot_id)` (bắt buộc tồn tại `run_id` đang `RUNNING` trong `pipeline_runs` theo ràng buộc FK `state_store.py:63,183-191`).
   * **Khôi phục sau Crash & Rollback (`rollback_snapshot`):**
     * Readers **CHỈ** đọc `snapshot_id` đã được quảng bá tại `state.sqlite.active_snapshot` (nên nếu crash xảy ra giữa Pha 1 và Pha 2, readers vẫn đọc nguyên vẹn snapshot cũ nhất quán!).
     * Thêm `reconcile_unpromoted_snapshots()` (kiểm tra các dòng `_snapshot_audit` mồ côi chưa được `state.sqlite` quảng bá, chỉ ghi log kiểm toán & để chu kỳ `maintain()` dọn sau grace period).
     * Thêm `rollback_snapshot(target_snapshot_id, state)` xác minh `target_snapshot_id` vẫn còn trong `_snapshot_audit` & chưa bị expire, sau đó chuyển `state.sqlite.active_snapshot` về `target_snapshot_id` nguyên tử.
4. **Hàm Bảo trì & Hết hạn Snapshot Đúng Chuẩn API DuckLake (`MISS-02`):**
   * `maintain()` thực hiện tuần tự dưới write lock:
     1. `CALL ducklake_flush_inlined_data('finintel_lake')` (nếu Gate B0 xác nhận tồn tại).
     2. `CALL ducklake_merge_adjacent_files('finintel_lake')` (giữ nguyên thứ tự sắp xếp nhờ `SET SORTED BY`).
     3. **Tính tường minh danh sách `BIGINT[]` `versions` cần hết hạn (`MISS-02`):** Truy vấn `ducklake_snapshots('finintel_lake')` kết hợp `_snapshot_audit` và `state.sqlite.active_snapshot`; chỉ chọn các `snapshot_id` thỏa mãn **đồng thời**: (a) không phải `active_snapshot`, (b) không thuộc `keep_snapshots` phiên bản mới nhất, VÀ (c) `snapshot_time < now() - min_snapshot_age_seconds`. Nếu danh sách khác rỗng $\rightarrow$ gọi **`CALL ducklake_expire_snapshots('finintel_lake', versions => [...])`** (TUYỆT ĐỐI KHÔNG truyền `keep_snapshots` vào `versions` và KHÔNG gộp `versions` với `older_than`!).
     4. Gọi `CALL ducklake_cleanup_old_files('finintel_lake', older_than => now() - INTERVAL '<min_snapshot_age_seconds> seconds')`.

### 3.3. Hợp đồng `ParquetRepository` & `SnapshotPublisher` (`BLK-10`, `BLK-15`, `HI-08`, `HI-10`)
1. **Backend Auto-Discovery (`BLK-10`):**
   * `ParquetRepository(data_root=...)` tự kiểm tra xem tại `data_root` có tồn tại `metadata/ducklake_catalog.sqlite` (và dòng `active_snapshot` khớp `_snapshot_audit`) hay không; nếu có và cấu hình cho phép thì tự vận hành chế độ DuckLake; nếu không thì vận hành chế độ `"parquet"` như cũ (không bao giờ ném `"Active snapshot directory is missing"` khi snapshot đang ở DuckLake!).
   * Bổ sung phương thức **`get_snapshot_metadata() -> dict[str, object]`** (trả `snapshot_id`, `published_at`, `storage_backend`, `ducklake_version`) thay thế việc `dashboard/queries.py:511` tự đọc file `_snapshot_meta.json` trên đĩa!
   * Bổ sung hàm chuẩn hóa tên bảng `_normalize_table_name(table_name: str) -> str`: chấp nhận cả `"financial_ratios.parquet"` lẫn `"financial_ratios"`, đối chiếu chặt với hằng **`KNOWN_TABLES` allowlist** (từ chối bằng `ValueError` mọi tên lạ, path traversal hay ký tự đặc biệt!).
2. **`snapshot_session(connection=...)` & `read_table(columns, filters, connection=...)` (`BLK-15`, `HI-10`):**
   * Chữ ký chuẩn:
     ```python
     @contextmanager
     def snapshot_session(
         self, *, connection: duckdb.DuckDBPyConnection | None = None
     ) -> Iterator[ParquetRepository]: ...

     def read_table(
         self,
         table_name: str,
         *,
         columns: Sequence[str] | None = None,
         filters: Mapping[str, object] | None = None,
         connection: duckdb.DuckDBPyConnection | None = None,
     ) -> pl.DataFrame: ...
     ```
   * `connection` là **non-owning** (truyền từ `conn.cursor()` của FastAPI Wave 4 — repository KHÔNG đóng `connection` của caller!). Trong `snapshot_session(connection=...)`:
     - Ở chế độ `ducklake`: ghim đúng 1 `ducklake_version` từ `active_snapshot` $\leftrightarrow$ `_snapshot_audit` và thực thi mọi `read_table` qua `SELECT <quoted_cols> FROM finintel_lake.<tbl> AT (VERSION => ?) WHERE ...` (0 `FileLock`, 0 read-skew đa bảng!).
     - Ở chế độ `parquet`: giữ **`filelock.ReadWriteLock(lock_path, is_singleton=False).read_lock()`** trọn thời gian session (tuyệt đối không nhả khóa sớm!).
   * **Ngữ pháp `columns` & `filters` An toàn (`HI-10`):**
     - `columns`: cấm list rỗng (`[]`) và cấm trùng tên; mỗi cột phải khớp `^[a-zA-Z_][a-zA-Z0-9_]*$` VÀ thuộc schema bảng; luôn bọc dấu ngoặc kép SQL (`"value"`, `"issuer_id"`).
     - `filters`: phân biệt rõ scalar (`str`, `bytes`, `int`, `float`, `date`, `bool`) với collection (`list`, `tuple`, `set`, `frozenset`) — **KHÔNG coi `str` là `Sequence` (`IN ('F','P','T')`)!**; với `None` phát sinh `"col" IS NULL`; với collection rỗng trả DataFrame rỗng đúng schema; hỗ trợ lọc khoảng có kiểu tường minh `FilterRange(ge=..., le=..., gt=..., lt=...)` (phục vụ Wave 4 Screener!).
3. **Cập nhật `SnapshotPublisher` (`HI-07`, `HI-08`):**
   * Dùng `filelock.ReadWriteLock(lock_path, is_singleton=False).write_lock()` cho `publish()` và `collect_garbage()`.
   * Thêm `min_snapshot_age_seconds: int = 3600` (mặc định có thể đặt `0` trong unit test) cho `collect_garbage()` ở chế độ legacy Parquet.
   * Trong `_write_table()`: **thay bỏ `shutil.copy2` ở `L235`** — luôn sắp xếp theo `TABLE_PHYSICAL_LAYOUT` và ghi bằng `write_parquet(destination, row_group_size=...)`.

### 3.4. Khử Đọc Lặp 4× & Dọn Rác `data/curated/` Hardened (`BLK-12`, `HI-09`)
1. **`src/finintel/storage/manifest_export.py` & `src/finintel/pipeline/publish.py` (`HI-09`):**
   * Tách hàm dùng chung `_resolve_pit_latest_facts(facts: pl.DataFrame, *, as_of: date) -> pl.DataFrame` thực hiện đúng cửa sổ xếp hạng của `repository.py:319-343` (gồm `effective_available_at DESC, revision_no DESC, filing_id DESC` tie-breaker) trên `pl.DataFrame` đã nạp trong RAM.
   * Cho phép `build_latest_facts_table(facts_source: Path | pl.DataFrame, *, as_of: date)` và `build_filings_manifest_snapshot(state, facts_source: Path | pl.DataFrame)` nhận trực tiếp `pl.DataFrame` (giữ tương thích `Path`).
   * Sửa lỗi `manifest_export.py:80-108`: đưa `source_provider` vào `join_keys` khi gắn TTM để không ghi đè dữ liệu đa nguồn, và bảo toàn nguyên vẹn `comparable_across_taxonomy`, `source_filing_ids`, `is_filed_late`.
   * Trong `PublishHandler`: đọc `clean_financial_facts.parquet` **1 lần duy nhất**, tôn trọng `storage.publish_unmapped: false` mặc định, và gọi `DuckLakeStore` hoặc `SnapshotPublisher` dựa trên `settings.storage` được truyền từ `cli.py`.
2. **`src/finintel/storage/curated_gc.py` (`BLK-12`):**
   * Chữ ký: `collect_curated_garbage(data_root: Path, state: StateStore, *, keep_runs: int = 2, min_age_seconds: int = 3600, protected_run_ids: Collection[str] = ()) -> list[Path]`.
   * **Kiểm tra đầu vào & 7 Bất biến Bảo vệ Tuyệt đối:**
     1. Validate `keep_runs >= 1`, `min_age_seconds >= 0` (`ValueError` nếu vi phạm).
     2. Chỉ duyệt trong allowlist 3 thư mục con `{"financial_facts", "quality", "analytics"}` (bảo vệ tuyệt đối `curated/issuers.parquet` ở cấp trên!); kiểm tra `candidate.resolve().is_relative_to(curated_root.resolve())` và từ chối mọi symlink.
     3. Giữ `pipeline.lock` khi xóa; bỏ qua các thư mục staging đang hoạt động (`.normalize-*`, `.quality-*`, `.analytics-*`) mới hơn `min_age_seconds`.
     4. Bảo vệ mọi `run_id` thuộc: (a) trạng thái `RUNNING` trong `pipeline_runs`, (b) `active_snapshot.run_id` và mọi `run_id` gắn với `keep_snapshots` bản chụp đang được giữ lại (kể cả khi `status == 'FAILED'` sau khi đã publish!), (c) `protected_run_ids` (ví dụ base run của incremental normalization), (d) mới hơn `min_age_seconds`.
     5. Giữ tối thiểu `keep_runs` bản `full_history` gần nhất **theo từng cặp `(subdir, source)`** (`fhsc` và `file` độc lập — không xóa mất bản duy nhất của nguồn kia!).
     6. **Fail-closed với bằng chứng pháp y:** Khi kiểm tra `curated/quality/<run_id>/audit_quarantine.parquet`, nếu có `height > 0` HOẶC gặp bất kỳ lỗi đọc nào (`OSError`, `PolarsError`) $\rightarrow$ **KHÔNG XÓA** và ghi log cảnh báo!
     7. Tái sử dụng mẫu đổi tên nguyên tử rồi xóa có hoãn trên Windows (`_remove_snapshot` tại `snapshot_publisher.py:171-196`).

---

## 4. KIỂM THỬ TDD & TIÊU CHÍ NGHIỆM THU (`DEFINITION OF DONE`)

1. **`tests/unit/test_layer3_ducklake_storage.py`:**
   * **Zero Reader-Reader Blocking:** `threading.Barrier(8)` bên trong `snapshot_session()` trên 8 luồng reader đồng thời + bài test **đa tiến trình (`subprocess`) trên Windows** cho `ReadWriteLock` (`HI-08`).
   * **Concurrent Publish + Maintain + Zero Read-Skew:** 4 luồng reader liên tục đọc 5 bảng qua `snapshot_session(connection=cursor)` trong lúc 1 luồng writer xuất bản 5 snapshot liên tiếp + `maintain()` $\rightarrow$ `0` ngoại lệ, `0` cross-table read-skew; xác minh `ducklake_expire_snapshots` không bao giờ xóa `active_snapshot` hay `keep_snapshots` mới nhất (`MISS-02`).
   * **Crash Recovery & Rollback:** Mô phỏng crash sau Pha 1 (trước `state.sqlite` promotion) xác nhận readers vẫn thấy snapshot cũ; kiểm thử `rollback_snapshot()` chuyển về snapshot liền trước thành công.
   * **Predicate Pushdown, `str`/`None`/Range Filters & SQLi Guard:** Xác minh `EXPLAIN` đẩy lọc xuống scan; `filters={"ticker": "FPT"}` tạo `= ?` (không `IN ('F','P','T')`); `filters={"delisted_date": None}` tạo `IS NULL`; `"issuer_id; DROP TABLE ..."` và `columns=[]` ném `ValueError`; bảng ngoài `KNOWN_TABLES` ném `ValueError`.
   * **Curated GC Hardened Safety:** Kiểm thử 7 bất biến: run `RUNNING`, run `FAILED` đang là `active_snapshot`, 2 nguồn `fhsc` + `file` song song, tệp `audit_quarantine.parquet` có lỗi và tệp bị corrupt (fail-closed), symlink, và `curated/issuers.parquet` được bảo toàn 100%.
   * **Single-Read `PublishHandler` & Multi-Source `latest_facts`:** Xác minh bằng spy `PublishHandler` chỉ đọc `clean_financial_facts.parquet` đúng `1` lần và `build_latest_facts_table` giữ nguyên `source_provider` + `comparable_across_taxonomy`.
2. **Cổng chất lượng bắt buộc:** `ruff format --check`, `ruff check`, `mypy --strict`, `tests/unit/test_layer3_storage.py` (đã cập nhật `L161-165` sang `ReadWriteLock.write_lock(timeout=0)`), `tests/unit/test_layer3_ducklake_storage.py`, và `pytest -q` xanh 100%.

---

## 5. PROMPT GIAO VIỆC MẪU CHO CODING AGENT (COPY-PASTE READY)

```text
Đọc AGENT.md, docs/v2_specs/00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md, docs/evidence/option_b_spike_report.json và docs/v2_specs/04_STREAM_B1_LAYER3_DUCKLAKE_STORAGE_AND_CURATED_GC.md.

Thực hiện ĐÚNG phạm vi Wave 1C (Stream B1) trên git worktree riêng v2/b1-layer3:
1. Chỉ sửa/tạo các file trong mục "Owned Files" của 04_STREAM_B1 (được sửa cli.py & storage/__init__.py; CẤM sửa storage/schemas.py hay cài deltalake).
2. Đọc đối chiếu DuckLake Docs (đặc biệt expire_snapshots & sorted tables), DuckDB Parquet/Secrets Docs và filelock ReadWriteLock Docs; ghi Verified Sources trong docstring.
3. Chú ý các ràng buộc sống còn: (a) KHÔNG truyền keep_snapshots vào versions và KHÔNG gộp versions+older_than trong ducklake_expire_snapshots; (b) tuần tự hóa mọi writer bằng pipeline.lock; (c) truyền connection non-owning qua cả snapshot_session() và read_table(); (d) loại trừ str/bytes khỏi IN expansion & dùng IS NULL cho None; (e) thay shutil.copy2 trong SnapshotPublisher bằng sorted write_parquet; (f) mặc định backend="parquet" khi settings không khai báo storage:.
4. Viết đầy đủ TDD tests trong test_layer3_ducklake_storage.py và chạy xanh 100% ruff, mypy --strict, pytest -q.
```
