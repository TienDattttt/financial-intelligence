/**
 * Record an end-to-end interactive walkthrough video of the FinIntel web app
 * using Playwright's built-in video recorder (1440x900 .webm).
 */
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require(path.resolve(__dirname, '../frontend/node_modules/@playwright/test'));

const ROOT = path.resolve(__dirname, '..');
const OUT_DIR = path.resolve(ROOT, 'notebooks/outputs/web_demo');
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

async function smoothScroll(page, targetY, stepDelay = 500) {
  await page.evaluate((y) => window.scrollTo({ top: y, behavior: 'smooth' }), targetY);
  await page.waitForTimeout(stepDelay);
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const tempVideoDir = path.join(OUT_DIR, '_tmp_video');
  fs.mkdirSync(tempVideoDir, { recursive: true });

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
  let context;
  let videoPath;

  try {
    await waitForHttp(`${API_URL}/api/v1/health`, 45000);
    await waitForHttp(WEB_URL, 30000);
    console.log('Servers healthy. Starting video recording...');

    browser = await chromium.launch({ channel: 'msedge', headless: true });
    context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      recordVideo: {
        dir: tempVideoDir,
        size: { width: 1440, height: 900 },
      },
    });
    const page = await context.newPage();

    // 1. Trang chủ — Tổng quan thị trường
    console.log('1. Recording Market Overview...');
    await page.goto(`${WEB_URL}/`, { waitUntil: 'networkidle' });
    await settle(page, 2200);
    await smoothScroll(page, 520, 1800);

    // Quick-add tickers to Watchlist from Home
    for (const ticker of ['HPG', 'FPT', 'VNM']) {
      const addBtn = page.getByRole('button', { name: new RegExp(`\\+\\s*${ticker}`) }).first();
      if (await addBtn.count()) {
        await addBtn.click();
        await page.waitForTimeout(500);
      }
    }
    await smoothScroll(page, 0, 1000);

    // 2. Sàng lọc doanh nghiệp — Bảng & Biểu đồ phân tán
    console.log('2. Recording Screener...');
    await page.getByRole('link', { name: /Sàng lọc DN/i }).first().click();
    await settle(page, 2200);

    // Click quick filter "Rủi ro kiệt quệ cao"
    const highRiskBtn = page.getByRole('button', { name: /Rủi ro kiệt quệ cao/i }).first();
    if (await highRiskBtn.count()) {
      await highRiskBtn.click();
      await settle(page, 1800);
    }

    // Reset filter and switch to Scatter Plot
    const resetBtn = page.getByRole('button', { name: /Đặt lại/i }).first();
    if (await resetBtn.count()) {
      await resetBtn.click();
      await settle(page, 1400);
    }
    const scatterBtn = page.getByRole('button', { name: /Biểu đồ phân tán/i }).first();
    if (await scatterBtn.count()) {
      await scatterBtn.click();
      await settle(page, 2400);
    }

    // 3. Hồ sơ doanh nghiệp (FPT — các tab chính)
    console.log('3. Recording Company Profile (FPT)...');
    await page.goto(`${WEB_URL}/companies/FPT?tab=overview`, { waitUntil: 'networkidle' });
    await settle(page, 2200);
    await smoothScroll(page, 550, 1600);
    await smoothScroll(page, 0, 800);

    for (const tabLabel of ['Báo cáo tài chính', 'Tỷ số & Ngành', 'Nguồn dữ liệu']) {
      const tab = page.getByRole('button', { name: tabLabel }).or(page.getByRole('tab', { name: tabLabel })).first();
      if (await tab.count()) {
        await tab.click();
        await settle(page, 1800);
        await smoothScroll(page, 420, 1200);
        await smoothScroll(page, 0, 600);
      }
    }

    // 4. Hồ sơ doanh nghiệp rủi ro (CIG — Chất lượng LN & Mô hình TreeSHAP)
    console.log('4. Recording Risky Company ML & TreeSHAP (CIG)...');
    await page.goto(`${WEB_URL}/companies/CIG?tab=quality`, { waitUntil: 'networkidle' });
    await settle(page, 2000);
    await smoothScroll(page, 450, 1400);

    const modelTab = page.getByRole('button', { name: /Mô hình & SHAP/i }).or(page.getByRole('tab', { name: /Mô hình & SHAP/i })).first();
    if (await modelTab.count()) {
      await modelTab.click();
      await settle(page, 2200);
      await smoothScroll(page, 520, 2200);
    }

    // 5. So sánh đa doanh nghiệp
    console.log('5. Recording Multi-company Comparison...');
    await page.goto(`${WEB_URL}/compare?tickers=FPT,HPG,VNM`, { waitUntil: 'networkidle' });
    await settle(page, 2200);
    await smoothScroll(page, 550, 1800);

    // 6. Mô phỏng kịch bản sốc (Stress Test)
    console.log('6. Recording Stress Test Simulator...');
    await page.goto(`${WEB_URL}/stress-test`, { waitUntil: 'networkidle' });
    await settle(page, 1600);
    const rateBtn = page.getByRole('button', { name: /Tăng lãi suất/i }).first();
    if (await rateBtn.count()) {
      await rateBtn.click();
      await settle(page, 2200);
    }
    const crisisBtn = page.getByRole('button', { name: /Khủng hoảng kép/i }).first();
    if (await crisisBtn.count()) {
      await crisisBtn.click();
      await settle(page, 2200);
      await smoothScroll(page, 550, 1800);
    }

    // 7. Danh mục theo dõi (Watchlist)
    console.log('7. Recording Watchlist...');
    await page.goto(`${WEB_URL}/watchlist`, { waitUntil: 'networkidle' });
    await settle(page, 2000);

    // 8. Dữ liệu & Phương pháp luận
    console.log('8. Recording Methodology...');
    await page.goto(`${WEB_URL}/methodology`, { waitUntil: 'networkidle' });
    await settle(page, 1800);
    await smoothScroll(page, 600, 1600);

    videoPath = await page.video().path();
    await context.close();
    context = null;

    const finalVideo = path.join(OUT_DIR, 'finintel_demo_chuc_nang.webm');
    fs.copyFileSync(videoPath, finalVideo);
    fs.rmSync(tempVideoDir, { recursive: true, force: true });
    console.log(`Video saved to: ${finalVideo}`);
  } finally {
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    killTree(webProc);
    killTree(apiProc);
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
