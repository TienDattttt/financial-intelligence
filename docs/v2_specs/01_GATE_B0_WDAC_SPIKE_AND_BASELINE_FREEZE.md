# ĐẶC TẢ TRIỂN KHAI WAVE 0 — GATE B0: KIỂM CHỨNG WINDOWS WDAC, `ReadWriteLock` MULTIPROCESS, CI/MAKEFILE & KHÓA GÓI `platform`

* **Mã đặc tả:** `SPEC-V2-01-GATE-B0-v1.1`
* **Wave:** `Wave 0` (Bắt buộc hoàn tất trước khi mở Wave 1C `04_STREAM_B1`, Wave 3 `07_STREAM_B2`, Wave 4 `08_STREAM_B3`)
* **Nhánh Git / Worktree:** `v2/b0-spike` (`../finintel-wt-b0-spike`)
* **Tiền điều kiện bắt buộc (Con người hoàn tất trước Wave 0):**
  1. Đã ký `AGENT.md` v2.0 hoặc Miễn trừ §4.2 cho nhóm gói `platform` (xem `00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md` §1.2).
  2. Đã gỡ chặn đường dẫn track cho `docs/evidence/option_b_spike_report.json` (và bộ đặc tả) trước khi tách worktree.
  3. Đã tạo tag mốc nền sạch `v1.5-baseline-before-v2` trên `v2/integration`.

---

## 1. RÀNG BUỘC AN TOÀN & QUYỀN SỞ HỮU FILE

### 1.1. Bảo vệ Tuyệt đối Phiên `ed48af80` Đang Chạy
* **CẤM** chạm vào `data/live_fhsc_universe/` hoặc gọi FHSC Open API.
* **Có thể chạy Wave 0 ngay trong lúc `ed48af80` còn đang chạy** bằng cách làm việc trên `git worktree` cô lập và môi trường ảo tạm `%TEMP%\finintel-b0-spike-venv` (`uv venv %TEMP%\finintel-b0-spike-venv --python 3.11`) — **TUYỆT ĐỐI KHÔNG chạy `uv sync` lên `.venv` của kho gốc** khi tiến trình Windows đang chạy!

### 1.2. Phạm vi Sở hữu File (`STRICT FILE OWNERSHIP`)
* **Owned Files (Chỉ được phép tạo/sửa):**
  * `scripts/spike_option_b.py` *(mới — script khảo sát thực nghiệm tái lập)*
  * `docs/evidence/option_b_spike_report.json` *(mới — bằng chứng Gate B0 được track trong Git)*
  * `pyproject.toml` *(CHỈ bổ sung `[dependency-groups] platform` với phiên bản ghim cứng `==`; KHÔNG sửa phiên bản sẵn có trong `dependencies`, `dev`, `ml`, `spike`!)*
  * `uv.lock` *(cập nhật sạch bằng `uv lock` sau khi sửa `pyproject.toml`)*
  * `.github/workflows/ci.yml` *(bổ sung job CI cho `platform`, đưa `dashboard` vào `ruff`, kích hoạt thêm nhánh `v2/integration`)*
  * `Makefile` *(sửa dòng `git status --porcelain AGENT.md` thay cho `CLAUDE.md AGENTS.md` không tồn tại, bổ sung `ruff format --check` và `dashboard`)*
* **Read-Only Contract Files:** `docs/00_ENGINEERING_STANDARDS_AND_SPIKE.md`, `AGENT.md`.
* **Forbidden Files:** Toàn bộ `src/`, `dashboard/`, `config/`, `tests/`, `AGENT.md`, `.gitignore`, `data/live_fhsc_universe/`.

---

## 2. NGUỒN TÀI LIỆU CHÍNH THỨC BẮT BUỘC TRUY CẬP & ĐỐI CHIẾU TRƯỚC KHI CODE

1. **DuckLake Official Docs & GitHub:**
   * `https://ducklake.select/docs/stable/`
   * `https://ducklake.select/docs/stable/duckdb/maintenance/expire_snapshots`
   * `https://github.com/duckdb/ducklake`
   * *Trọng tâm kiểm chứng:* `INSTALL ducklake; LOAD ducklake; INSTALL sqlite; LOAD sqlite;`, `ATTACH 'ducklake:sqlite:...' AS finintel_lake (DATA_PATH '...')`, `ATTACH ... (READ_ONLY)`, `ALTER TABLE ... SET SORTED BY (...)`, `ducklake_merge_adjacent_files`, `ducklake_snapshots()`, `ducklake_expire_snapshots(..., versions => BIGINT[])` (**LƯU Ý: `versions` là mảng ID snapshot cần xóa, KHÔNG phải số bản giữ lại và KHÔNG gộp chung với `older_than`!**), `ducklake_cleanup_old_files`, và đặc tính SQLite final `COMMIT` không tự retry lock error.
2. **DuckDB Extensions, Python API & Concurrency Docs (`duckdb==1.4.2`):**
   * `https://duckdb.org/docs/stable/extensions/overview`
   * `https://duckdb.org/docs/stable/clients/python/overview` (xác nhận `duckdb.connect(":memory:", read_only=True)` ném `InvalidInputException` — phải dùng `:memory:` + `ATTACH ... (READ_ONLY)`)
   * `https://duckdb.org/docs/stable/connect/concurrency`
3. **`filelock` Official Docs (`filelock==4.2.10`):**
   * `https://py-filelock.readthedocs.io/` · `https://github.com/tox-dev/filelock`
   * *Trọng tâm kiểm chứng:* `ReadWriteLock(lock_file, is_singleton=False)` sử dụng SQLite backend trên local filesystem (đã vá `CVE-2026-112913` / `CVE-2026-112910` từ bản `>= 4.2.1`, yêu cầu thư mục lock không phải thư mục dùng chung công cộng).
4. **Hồ sơ tiền lệ WDAC nội bộ:** `docs/00_ENGINEERING_STANDARDS_AND_SPIKE.md` (ghi nhận WDAC từng chặn `_core.pyd` của `vnstock==6.0.1`).

---

## 3. ĐẶC TẢ KỸ THUẬT CHI TIẾT (`scripts/spike_option_b.py` + CI/Makefile)

Xây dựng `scripts/spike_option_b.py` sạch (`mypy --strict`, `ruff`), chạy hoàn toàn trong `tempfile.TemporaryDirectory()` (thư mục cục bộ riêng của user, không dùng thư mục public) và ghi kết quả đo thực tế vào `docs/evidence/option_b_spike_report.json`:

### 3.1. Thử nghiệm 1 — DuckLake + SQLite Catalog trên Windows (`duckdb==1.4.2`)
1. Khởi tạo `duckdb.connect(":memory:")`, thực thi:
   ```sql
   INSTALL ducklake; LOAD ducklake;
   INSTALL sqlite; LOAD sqlite;
   ```
   * **Phân loại lỗi chặt chẽ (`BLK-11`):** Phân biệt rõ **lỗi chính sách Windows WDAC / Win32 binary load** (`is_wdac_block = True`, ví dụ mã lỗi Win32 `4551` / `ERROR_ACCESS_DISABLED_BY_POLICY` / `Application Control policy has blocked`) với **lỗi mạng tạm thời** (`network_error = True` — KHÔNG được kích hoạt fallback kiến trúc chỉ vì rớt mạng!).
   * Ghi lại đường dẫn tệp `.duckdb_extension` đã tải và mã băm SHA-256 của từng file extension (`extension_hashes`) để kiểm soát chuỗi cung ứng (`AGENT.md` §2.2) và làm rõ yêu cầu cache ngoại tuyến cho GitHub Actions CI (`HI-17`).
2. Attach `ducklake:sqlite:<tmp>/catalog.sqlite` AS `finintel_lake` (`DATA_PATH '<tmp>/lake/'`), cấu hình SQLite catalog `WAL` + `busy_timeout`, kiểm tra:
   * **Multi-Table Atomic Snapshot:** Tạo 2 bảng (`t_facts`, `t_ratios`) kèm `ALTER TABLE ... SET SORTED BY (issuer_id, period_end_date)` và ghi dữ liệu trong **1 transaction duy nhất (`BEGIN TRANSACTION; ... COMMIT;`)** $\rightarrow$ xác nhận cả 2 bảng dùng chung đúng 1 `snapshot_id`.
   * **Read-Only Attach + Predicate Pushdown + Post-Compaction Sort Preservation:** Mở kết nối thứ 2 `:memory:` + `ATTACH 'ducklake:sqlite:...' AS finintel_lake (READ_ONLY)`, chạy `EXPLAIN` xác nhận đẩy lọc `issuer_id` xuống Parquet scan cả **trước và sau** `CALL ducklake_merge_adjacent_files('finintel_lake')`.
   * **Exact Snapshot Expiration Semantics (`MISS-02`):** Ghi 4 phiên bản liên tiếp $\rightarrow$ truy vấn `ducklake_snapshots('finintel_lake')` $\rightarrow$ tính chính xác danh sách `BIGINT[]` các phiên bản cũ hơn ngưỡng tuổi VÀ nằm ngoài `keep_snapshots = 2` mới nhất $\rightarrow$ gọi `CALL ducklake_expire_snapshots('finintel_lake', versions => [...])` + `CALL ducklake_cleanup_old_files(...)` $\rightarrow$ xác nhận 2 phiên bản mới nhất vẫn đọc bình thường.
   * **Đa Tiến Trình Song Song trên Windows (`subprocess` thật):**
     - Tiến trình A (Reader): Mở kết nối `READ_ONLY` lặp 30 lần đọc 2 bảng `t_facts` + `t_ratios` ghim cùng `AT (VERSION => v)` kiểm tra bất biến `count(t_facts) == 10 * count(t_ratios)` (0 read-skew).
     - Tiến trình B (Writer, tuần tự hóa qua `filelock` ghi duy nhất theo `MISS-04`): Ghi 3 phiên bản liên tiếp + `merge_adjacent_files` + `expire_snapshots` + `cleanup_old_files`.
     - Xác nhận `0` `PermissionError`, `0` `IOException`, `0` `FileNotFoundError`, `0` read-skew.
   * **Zero-Copy Arrow sang Polars:** Xác nhận `con.sql("SELECT ...").pl()` hoạt động mượt với `polars==1.35.2` & `pyarrow==22.0.0`.

### 3.2. Thử nghiệm 2 — Kiểm chứng Đa Tiến Trình Windows cho `filelock==4.2.10` `ReadWriteLock` (`HI-08`)
* **Bắt buộc chạy ở mọi trường hợp** (vì `parquet` + `ReadWriteLock` vừa là legacy fallback mặc định vừa là phương án dự phòng chính nếu DuckLake bị WDAC chặn — **KHÔNG dùng `delta-rs`**!):
  1. Khởi chạy 3 `subprocess` Reader dùng `filelock.ReadWriteLock(lock_path, is_singleton=False).read_lock()` đồng thời giữ khóa bằng barrier file/event để chứng minh đa tiến trình đọc song song không chặn nhau trên Windows.
  2. Khởi chạy 1 `subprocess` Writer thử `write_lock(timeout=0)` trong lúc Reader giữ khóa (xác nhận ném `Timeout`), sau đó gọi `write_lock(timeout=10)` và xác nhận lấy được khóa sạch ngay khi các Reader nhả khóa (`0` `sqlite3.OperationalError`, `0` deadlock).

### 3.3. Thử nghiệm 3 — Khóa Tổ hợp Gói `platform` & Cập nhật CI / Makefile (`BLK-04`, `HI-17`)
1. Kiểm tra resolve sạch bằng `uv lock` với `Python 3.11`, `polars==1.35.2`, `pyarrow==22.0.0`, `duckdb==1.4.2`, `streamlit==1.45.1`, `xgboost==2.1.4`, `scikit-learn==1.7.2`, `shap==0.49.1` cho `[dependency-groups] platform` (ghim cứng `==` toàn bộ):
   * `fastapi==<exact>`
   * `uvicorn==<exact>`
   * `pydantic==<exact>`
   * `httpx==0.28.1`
   * `dagster==<exact>`
   * `dagster-webserver==<exact>`
   * `dagster-duckdb==<exact>`
   * *(Ghi rõ `sqlmesh` và `deltalake` KHÔNG được cài; ghi nhận phiên bản transitive `sqlalchemy` trong báo cáo spike để kiểm soát minh bạch.)*
2. **Cập nhật `.github/workflows/ci.yml`:**
   * Thêm nhánh `v2/integration` vào trigger `push` & `pull_request` (cùng `main`).
   * Tách rõ 2 cấu hình hoặc bổ sung job `platform-checks`:
     - Job `mvp-checks`: giữ nguyên `uv sync --locked --group ml --dev` chạy kiểm tra tương thích ngược các module MVP không phụ thuộc `platform`.
     - Job `platform-checks`: chạy `uv sync --locked --group ml --group platform --dev`, `ruff format --check src scripts tests dashboard`, `ruff check src scripts tests dashboard`, `mypy --strict src/finintel scripts/spike_vnstock6.py scripts/spike_option_b.py tests/unit`, và `pytest -q`.
3. **Cập nhật `Makefile`:**
   * Đổi dòng 7 từ `git status --porcelain CLAUDE.md AGENTS.md` sang `git status --porcelain AGENT.md`.
   * Bổ sung `ruff format --check src scripts tests dashboard`, `ruff check src scripts tests dashboard`, và `scripts/spike_option_b.py` vào target `lint`.

---

## 4. CỔNG QUYẾT ĐỊNH & TIÊU CHÍ NGHIỆM THU (`DEFINITION OF DONE`)

1. Tồn tại `docs/evidence/option_b_spike_report.json` với đầy đủ các trường đo thực tế (cấm bịa số):
   * `python_version`, `duckdb_version`, `polars_version`, `pyarrow_version`, `filelock_version`, `os_platform`
   * `ducklake_status` (`"PASS"` | `"BLOCKED_BY_WDAC"` | `"NETWORK_ERROR"` | `"ERROR"`), `extension_hashes`, `ducklake_multiprocess_errors` (`0` nếu `PASS`), `post_compaction_pushdown_verified` (`bool`), `expire_versions_list_verified` (`bool`)
   * `rwlock_windows_subprocess_status` (`"PASS"` | `"FAIL"`), `rwlock_errors` (`0`)
   * `selected_storage_backend` (`"ducklake"` nếu Thử nghiệm 1 `PASS` và CI có thể nạp/cache extension; `"parquet"` nếu WDAC chặn — **tuyệt đối không `"delta_rs"`**)
   * `pinned_platform_packages` (`{package: exact_version}`), `transitive_sqlalchemy_version`
   * `explain_pushdown_before_compaction`, `explain_pushdown_after_compaction`
2. `git status --porcelain AGENT.md` trả về rỗng.
3. Chạy xanh 100% trên môi trường đã đồng bộ:
   * `ruff format --check src scripts tests dashboard`
   * `ruff check src scripts tests dashboard`
   * `mypy --strict src/finintel scripts/spike_vnstock6.py scripts/spike_option_b.py tests/unit`
   * `pytest -q` (100% bài test hiện hữu xanh).

---

## 5. PROMPT GIAO VIỆC MẪU CHO CODING AGENT (COPY-PASTE READY)

```text
Đọc AGENT.md, docs/v2_specs/00_MASTER_PROGRAM_AND_AGENT_GOVERNANCE.md và docs/v2_specs/01_GATE_B0_WDAC_SPIKE_AND_BASELINE_FREEZE.md.

Thực hiện ĐÚNG phạm vi Wave 0 (Gate B0) trên git worktree riêng v2/b0-spike:
1. Chỉ tạo/sửa các tệp trong mục "Owned Files" của 01_GATE_B0. CẤM sửa src/, dashboard/, config/, tests/, AGENT.md, .gitignore hay chạm vào data/live_fhsc_universe/.
2. Đọc đối chiếu DuckLake Docs (https://ducklake.select/docs/stable/ và expire_snapshots), DuckDB Python Docs, và filelock ReadWriteLock Docs; ghi khối Verified Sources trong docstring của scripts/spike_option_b.py.
3. Nếu phiên thu thập nền còn chạy, chạy thử nghiệm trên venv cô lập %TEMP%\finintel-b0-spike-venv (không đụng .venv của kho gốc).
4. Chạy thật cả 3 thử nghiệm (DuckLake multi-process + SET SORTED BY + explicit versions list expiry + ReadWriteLock Windows subprocess + uv lock resolution cho fastapi/uvicorn/pydantic/httpx/dagster — KHÔNG cài sqlmesh/deltalake), ghi số đo thật vào docs/evidence/option_b_spike_report.json.
5. Cập nhật pyproject.toml, uv.lock, .github/workflows/ci.yml, Makefile và xác nhận ruff format --check, ruff check, mypy --strict, pytest -q xanh 100%.
```
