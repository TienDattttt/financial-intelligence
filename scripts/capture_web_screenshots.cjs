/**
 * Launch FastAPI (serving data/go_live_nfr_run) + Next.js standalone frontend,
 * capture clean high-DPI screenshots of all main web pages/tabs for Word thesis,
 * and generate external copy-pasteable captions in CHU_THICH_ANH_WORD.md.
 */
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(path.resolve(__dirname, '../frontend/node_modules/@playwright/test'));

const ROOT = path.resolve(__dirname, '..');
const OUT_DIR = path.resolve(ROOT, 'notebooks/outputs/web_screenshots');
const DATA_ROOT = path.resolve(ROOT, 'data/go_live_nfr_run');
const UVICORN_EXE = path.resolve(ROOT, '.venv/Scripts/uvicorn.exe');
const NEXT_BIN = path.resolve(ROOT, 'frontend/node_modules/next/dist/bin/next');
const NODE_EXE = process.execPath;

const API_URL = 'http://127.0.0.1:8000';
const WEB_URL = 'http://127.0.0.1:3000';

async function waitForHttp(url, timeoutMs = 45000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      // retry
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function killTree(proc) {
  if (!proc || proc.killed) return;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(proc.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      proc.kill('SIGTERM');
    }
  } catch {
    // ignore
  }
}

async function settle(page, ms = 1200) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(ms);
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  console.log('Starting FastAPI backend on :8000...');
  const apiProc = spawn(
    UVICORN_EXE,
    ['finintel.serving.api:app', '--host', '127.0.0.1', '--port', '8000', '--log-level', 'warning'],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        PYTHONPATH: path.resolve(ROOT, 'src'),
        FININTEL_DATA_ROOT: DATA_ROOT,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  );
  apiProc.stderr.on('data', (d) => process.stderr.write(`[API] ${d}`));

  console.log('Starting Next.js frontend on :3000...');
  const webProc = spawn(NODE_EXE, [NEXT_BIN, 'start', '-p', '3000', '-H', '127.0.0.1'], {
    cwd: path.resolve(ROOT, 'frontend'),
    env: {
      ...process.env,
      FININTEL_API_ORIGIN: API_URL,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  webProc.stderr.on('data', (d) => process.stderr.write(`[WEB] ${d}`));

  let browser;
  const captions = [];

  try {
    await waitForHttp(`${API_URL}/api/v1/health`, 45000);
    console.log('FastAPI healthy.');
    await waitForHttp(WEB_URL, 30000);
    console.log('Next.js healthy.');

    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 2,
    });
    const page = await context.newPage();

    // Pre-populate watchlist in localStorage so Home & Watchlist pages show real tracked companies
    await page.goto(WEB_URL, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => {
      localStorage.setItem('finintel_watchlist_v1', JSON.stringify(['FPT', 'HPG', 'VNM', 'CIG']));
    });

    const capture = async (filename, title, caption, url, action) => {
      console.log(`Capturing ${filename} -> ${url}`);
      await page.goto(url, { waitUntil: 'networkidle' });
      await settle(page, 1800);
      if (action) {
        await action(page);
        await settle(page, 1500);
      }
      const dest = path.join(OUT_DIR, filename);
      await page.screenshot({ path: dest, fullPage: true });
      captions.push({ filename, title, caption });
      console.log(`  Saved ${filename}`);
    };

    await capture(
      '01_trang_chu_tong_quan.png',
      'Giao diện Tổng quan thị trường và Bản đồ nhiệt rủi ro ngành',
      'Hình X.1. Giao diện Tổng quan thị trường FinIntel hiển thị các chỉ số tổng hợp toàn thị trường (375 doanh nghiệp), bản đồ nhiệt rủi ro theo ngành ICB và biến động danh mục theo dõi.',
      `${WEB_URL}/`
    );

    await capture(
      '02_sang_loc_doanh_nghiep_bang.png',
      'Giao diện Bộ lọc và Sàng lọc rủi ro doanh nghiệp (Dạng bảng)',
      'Hình X.2. Giao diện Bộ lọc và Sàng lọc rủi ro (Screener) cho phép tra cứu, lọc đa điều kiện và sắp xếp doanh nghiệp theo điểm Altman Z\'\', Beneish M-Score, xác suất kiệt quệ ML và số lượng cờ đỏ kế toán.',
      `${WEB_URL}/screener`
    );

    await capture(
      '03_sang_loc_doanh_nghiep_phan_tan.png',
      'Giao diện Sàng lọc rủi ro trực quan (Biểu đồ phân tán)',
      'Hình X.3. Biểu đồ phân tán trực quan trên trang Sàng lọc thể hiện mối tương quan giữa điểm Altman Z\'\' và xác suất kiệt quệ tài chính dự báo trên toàn bộ tập doanh nghiệp.',
      `${WEB_URL}/screener`,
      async (p) => {
        const btn = p.getByRole('button', { name: /Phân tán|Biểu đồ/i }).first();
        if (await btn.count()) await btn.click();
      }
    );

    await capture(
      '04_chi_tiet_doanh_nghiep_tong_quan.png',
      'Hồ sơ phân tích doanh nghiệp — Tab Tổng quan 1 trang (FPT)',
      'Hình X.4. Giao diện Hồ sơ doanh nghiệp (Tab Tổng quan 1 trang) của mã FPT tổng hợp kết luận nhanh (Verdict), các chỉ số sinh lời/thanh khoản/đòn bẩy cốt lõi, điểm Altman Z\'\', Piotroski F-Score, Beneish M-Score và xác suất kiệt quệ.',
      `${WEB_URL}/companies/FPT?tab=overview`
    );

    await capture(
      '05_bao_cao_tai_chinh.png',
      'Hồ sơ doanh nghiệp — Tab Báo cáo tài chính chuẩn hóa (FPT)',
      'Hình X.5. Giao diện Tab Báo cáo tài chính chuẩn hóa hiển thị bảng cân đối kế toán, kết quả kinh doanh và lưu chuyển tiền tệ theo chuẩn VAS đã chuẩn hóa đơn vị và kỳ báo cáo.',
      `${WEB_URL}/companies/FPT?tab=statements`
    );

    await capture(
      '06_ty_so_va_chuan_nganh.png',
      'Hồ sơ doanh nghiệp — Tab Tỷ số tài chính và So sánh chuẩn ngành (FPT)',
      'Hình X.6. Giao diện Tab Tỷ số & Ngành đối chiếu các chỉ số tài chính của doanh nghiệp với phân vị chuẩn ngành ICB (P25, Trung vị, P75) theo thời điểm.',
      `${WEB_URL}/companies/FPT?tab=ratios`
    );

    await capture(
      '07_chat_luong_loi_nhuan_co_do.png',
      'Hồ sơ doanh nghiệp — Tab Chất lượng lợi nhuận và Cờ đỏ kế toán (CIG)',
      'Hình X.7. Giao diện Tab Chất lượng lợi nhuận & Cờ đỏ kiểm tra các dấu hiệu bất thường kế toán (Beneish M-Score, dồn tích cao, bất đối xứng dòng tiền) của doanh nghiệp có cảnh báo rủi ro.',
      `${WEB_URL}/companies/CIG?tab=quality`
    );

    await capture(
      '08_mo_hinh_ml_va_shap.png',
      'Hồ sơ doanh nghiệp — Tab Mô hình học máy & Giải thích TreeSHAP (CIG)',
      'Hình X.8. Giao diện Tab Mô hình & SHAP hiển thị xác suất kiệt quệ tài chính đã hiệu chỉnh Platt, phân cụm hình thái tài chính và biểu đồ thác nước TreeSHAP giải thích từng nhân tố rủi ro cụ thể.',
      `${WEB_URL}/companies/CIG?tab=model`
    );

    await capture(
      '09_truy_xuat_nguon_du_lieu.png',
      'Hồ sơ doanh nghiệp — Tab Nguồn dữ liệu và Kiểm chứng chất lượng (FPT)',
      'Hình X.9. Giao diện Tab Nguồn dữ liệu cung cấp lịch sử phiên bản báo cáo tài chính (filings manifest), thời điểm công bố thực tế (EXACT_FILING) và trạng thái cổng kiểm định chất lượng QG1.',
      `${WEB_URL}/companies/FPT?tab=lineage`
    );

    await capture(
      '10_so_sanh_doanh_nghiep.png',
      'Giao diện So sánh đa doanh nghiệp (FPT, HPG, VNM)',
      'Hình X.10. Giao diện So sánh doanh nghiệp (Compare) cho phép đối chiếu song song diễn biến lịch sử và chỉ số hiện tại giữa nhiều doanh nghiệp niêm yết (ví dụ FPT, HPG, VNM).',
      `${WEB_URL}/compare?tickers=FPT,HPG,VNM`
    );

    await capture(
      '11_kiem_tra_suc_chiu_dung_stress_test.png',
      'Giao diện Mô phỏng kiểm tra sức chịu đựng tài chính (Stress Test)',
      'Hình X.11. Giao diện Kiểm tra sức chịu đựng (Stress Test) mô phỏng tác động của cú sốc kinh tế (suy giảm doanh thu, thu hẹp biên lợi nhuận gộp, tăng lãi suất) lên khả năng trả lãi và mức độ rủi ro theo từng ngành.',
      `${WEB_URL}/stress-test`,
      async (p) => {
        const presetBtn = p.getByRole('button', { name: /Sốc|Căng thẳng|Nặng|Vừa/i }).first();
        if (await presetBtn.count()) await presetBtn.click();
      }
    );

    await capture(
      '12_danh_muc_theo_doi_watchlist.png',
      'Giao diện Danh mục theo dõi (Watchlist)',
      'Hình X.12. Giao diện Danh mục theo dõi (Watchlist) cho phép người dùng lưu và giám sát nhanh trạng thái rủi ro, cờ đỏ kế toán và biến động điểm số của các mã quan tâm.',
      `${WEB_URL}/watchlist`
    );

    await capture(
      '13_phuong_phap_luan_va_tu_dien.png',
      'Giao diện Phương pháp luận và Từ điển chỉ số',
      'Hình X.13. Giao diện Phương pháp luận & Từ điển tài chính giải thích chi tiết công thức tính toán, ngưỡng cảnh báo (Altman Z\'\', Piotroski, Beneish) và quy trình hiệu chỉnh mô hình học máy.',
      `${WEB_URL}/methodology`
    );

    const mdLines = [
      '# Chú thích ảnh màn hình hệ thống web FinIntel (dùng để copy vào Word)',
      '',
      'Toàn bộ ảnh trong thư mục `notebooks/outputs/web_screenshots/` không chứa chữ chú thích bên trong ảnh.',
      'Bạn có thể chèn ảnh vào Word và copy phần chú thích tương ứng dưới đây:',
      '',
      ...captions.flatMap((c) => [
        `### ${c.filename} — ${c.title}`,
        '',
        `> ${c.caption}`,
        '',
      ]),
    ];
    fs.writeFileSync(path.join(OUT_DIR, 'CHU_THICH_ANH_WORD.md'), mdLines.join('\n'), 'utf8');
    console.log('All 13 screenshots and CHU_THICH_ANH_WORD.md generated successfully.');
  } finally {
    if (browser) await browser.close().catch(() => {});
    killTree(webProc);
    killTree(apiProc);
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
