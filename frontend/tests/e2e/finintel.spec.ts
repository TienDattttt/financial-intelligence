import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const FORBIDDEN_TECHNICAL_LEAKS: readonly RegExp[] = [
  /\bREGIME_FALLBACK\b/,
  /\bsnapshot_id\b/,
  /\baudit_quarantine\b/,
  /\bquality_summary\b/,
  /\bBS_TOTAL_EQUITY\b/,
  /\bBS_TOTAL_ASSETS\b/,
  /\bSCREENING_INDICATOR\b/,
  /\bDERIVED_Q4_INPUT\b/,
  /\bBS_IDENTITY_MISMATCH\b/,
  /\bValkey\b/i,
  /look-ahead/i,
  /\bRF_0[1-9]\b/,
  /\.parquet\b/i,
  /\.sqlite\b/i,
  /\bTraceback\b/,
  /[A-Za-z]:\\/,
];

const CORE_ROUTES: readonly { path: string; label: string }[] = [
  { path: "/", label: "Tong quan thi truong" },
  { path: "/screener", label: "Bo loc co phieu" },
  { path: "/companies/FPT", label: "Ho so FPT" },
  { path: "/compare", label: "So sanh doanh nghiep" },
  { path: "/stress-test", label: "Mo phong cu soc" },
  { path: "/watchlist", label: "Danh muc theo doi" },
  { path: "/methodology", label: "Phuong phap luan" },
];

const VIEWPORTS: readonly { name: string; width: number; height: number }[] = [
  { name: "1366x768", width: 1366, height: 768 },
  { name: "1440x900", width: 1440, height: 900 },
  { name: "375x812", width: 375, height: 812 },
];

test.describe("FinIntel V2 Frontend Real-API E2E", () => {
  for (const vp of VIEWPORTS) {
    test(`renders all 7 routes cleanly with zero overflow, console errors, leaks, or critical axe issues @ ${vp.name}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      const consoleErrors: string[] = [];
      const pageErrors: string[] = [];
      const apiFailures: string[] = [];

      page.on("console", (msg) => {
        if (msg.type() === "error") {
          consoleErrors.push(msg.text());
        }
      });
      page.on("pageerror", (err) => {
        pageErrors.push(err.message);
      });
      page.on("response", (res) => {
        if (res.url().includes("/api/") && res.status() >= 400) {
          apiFailures.push(`${res.status()} ${res.url()}`);
        }
      });

      for (const route of CORE_ROUTES) {
        await page.goto(route.path, { waitUntil: "networkidle" });
        await page.waitForTimeout(350);

        const bodyText = await page.evaluate(() => document.body.innerText ?? "");
        expect(
          bodyText.trim().length,
          `Route ${route.path} rendered empty body`,
        ).toBeGreaterThan(40);

        for (const pattern of FORBIDDEN_TECHNICAL_LEAKS) {
          expect(
            pattern.test(bodyText),
            `Route ${route.path} leaked forbidden internal pattern ${pattern}`,
          ).toBe(false);
        }

        const overflow = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        }));
        expect(
          overflow.scrollWidth,
          `Horizontal overflow on ${route.path} @ ${vp.name}: ${overflow.scrollWidth} > ${overflow.clientWidth}`,
        ).toBeLessThanOrEqual(overflow.clientWidth + 2);

        if (vp.name === "1440x900" || route.path === "/" || route.path === "/companies/FPT") {
          const axeResults = await new AxeBuilder({ page }).analyze();
          const critical = axeResults.violations.filter(
            (v) => v.impact === "critical",
          );
          expect(
            critical,
            `Critical axe violations on ${route.path} @ ${vp.name}: ${critical.map((c) => c.id).join(", ")}`,
          ).toEqual([]);
        }
      }

      expect(consoleErrors, `Console errors @ ${vp.name}`).toEqual([]);
      expect(pageErrors, `Page errors @ ${vp.name}`).toEqual([]);
      expect(apiFailures, `Failed /api/* responses @ ${vp.name}`).toEqual([]);
    });
  }

  test("supports Light / Dark / System toggle persistence and passes axe checks in Dark Mode", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto("/", { waitUntil: "networkidle" });

      const darkBtn = page.getByRole("button", { name: "Giao diện Tối" });
      await expect(darkBtn).toBeVisible();
      await darkBtn.click();

      await expect(page.locator("html")).toHaveClass(/\bdark\b/);
      const bg = await page.evaluate(
        () => window.getComputedStyle(document.body).backgroundColor,
      );
      expect(bg).toBe("rgb(11, 15, 20)");

      // Reload persists dark mode immediately (no FOUC reset)
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page.locator("html")).toHaveClass(/\bdark\b/);
      await page.waitForLoadState("networkidle");

      for (const routePath of ["/", "/screener", "/companies/FPT", "/compare"]) {
        await page.goto(routePath, { waitUntil: "networkidle" });
        await page.waitForTimeout(300);

        const overflow = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        }));
        expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 2);

        if (vp.name === "1440x900" || routePath === "/" || routePath === "/companies/FPT") {
          const axeResults = await new AxeBuilder({ page }).analyze();
          const critical = axeResults.violations.filter(
            (v) => v.impact === "critical",
          );
          expect(
            critical,
            `Dark mode critical axe violations on ${routePath} @ ${vp.name}: ${critical.map((c) => c.id).join(", ")}`,
          ).toEqual([]);
        }
      }

      // Verify System mode & Light mode toggle
      await page.emulateMedia({ colorScheme: "light" });
      await page.getByRole("button", { name: "Theo hệ thống" }).click();
      await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);

      await page.emulateMedia({ colorScheme: "dark" });
      await expect(page.locator("html")).toHaveClass(/\bdark\b/);

      await page.getByRole("button", { name: "Giao diện Sáng" }).click();
      await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);
    }
  });

  test("interactive flows: live screener, FPT profile + split Model tab charts, and live stress-test POST", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // 1. Live Screener shows real issuers from FastAPI
    await page.goto("/screener", { waitUntil: "networkidle" });
    await expect(page.locator("table")).toBeVisible();
    const rowCount = await page.locator("tbody tr").count();
    expect(rowCount).toBeGreaterThan(5);

    // 2. Live Company Profile (/companies/FPT) renders real KPIs and 2 separate single-axis charts in Model tab
    await page.goto("/companies/FPT", { waitUntil: "networkidle" });
    await expect(page.getByText("FPT", { exact: false }).first()).toBeVisible();
    await page.getByRole("tab", { name: "Mô hình & SHAP" }).click();
    await expect(
      page.getByText("Diễn biến lịch sử điểm Altman Z″ (EM)"),
    ).toBeVisible();
    await expect(
      page.getByText("Diễn biến lịch sử Xác suất kiệt quệ mô hình"),
    ).toBeVisible();

    // 3. Live Stress-Test triggers real POST /api/v1/stress-test
    const stressPromise = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v1/stress-test") &&
        res.request().method() === "POST" &&
        res.status() === 200,
    );
    await page.goto("/stress-test", { waitUntil: "networkidle" });
    const stressRes = await stressPromise;
    const stressJson = (await stressRes.json()) as {
      total_issuers_evaluated?: number;
    };
    expect(stressJson.total_issuers_evaluated ?? 0).toBeGreaterThan(50);

    // 4. Error-state handling: unknown ticker returns graceful Vietnamese message without leak
    await page.goto("/companies/ZZZZZ9", { waitUntil: "networkidle" });
    const errText = await page.evaluate(() => document.body.innerText ?? "");
    expect(errText.length).toBeGreaterThan(20);
    for (const pattern of FORBIDDEN_TECHNICAL_LEAKS) {
      expect(pattern.test(errText)).toBe(false);
    }

    // 5. Mobile (375x812) collapsible filter drawer on /screener & /stress-test
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/screener", { waitUntil: "networkidle" });
    const scrFilterBtn = page.getByRole("button", { name: /Bộ lọc doanh nghiệp/i });
    await expect(scrFilterBtn).toBeVisible();
    await scrFilterBtn.click();
    await expect(page.getByRole("button", { name: /Đóng bộ lọc/i })).toBeVisible();
    await page.getByRole("button", { name: /Đóng bộ lọc/i }).click();

    await page.goto("/stress-test", { waitUntil: "networkidle" });
    const stressFilterBtn = page.getByRole("button", { name: /Tham số cú sốc vĩ mô/i });
    await expect(stressFilterBtn).toBeVisible();
    await stressFilterBtn.click();
    await expect(page.getByRole("button", { name: /Đóng bảng tham số sốc/i })).toBeVisible();
  });

  test("Wave 1-6 Home (/): Sector Risk Treemap, 4 KPI cards with QoQ delta, sector click navigates to /screener with matching count", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => pageErrors.push(err.message));

    const summaryPromise = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v2/sectors/risk-summary") &&
        res.status() === 200,
    );
    await page.goto("/", { waitUntil: "networkidle" });
    const summaryRes = await summaryPromise;
    const summaryJson = (await summaryRes.json()) as {
      total_issuers: number;
      high_red_flag_count: number;
      sectors: Array<{
        icb_code_level2: string;
        icb_name_level2: string;
        issuer_count: number;
      }>;
    };

    // 4 KPI cards with QoQ delta text ("so với ...")
    const kpiSection = page.locator('section[aria-label="Chỉ số tổng hợp rủi ro"]');
    const kpiLinks = kpiSection.locator("a");
    await expect(kpiLinks).toHaveCount(4);
    for (let i = 0; i < 4; i++) {
      const cardText = await kpiLinks.nth(i).innerText();
      expect(cardText).toMatch(/so với\s+/i);
    }

    // Sector Risk Treemap renders canvas and accessible table view
    const treemapCard = page
      .locator("div.overflow-hidden")
      .filter({ has: page.locator('select[aria-label="Chọn chỉ tiêu tô màu bản đồ ngành"]') })
      .first();
    await expect(treemapCard).toBeVisible();
    const treemapCanvas = treemapCard.locator("canvas").first();
    await expect(treemapCanvas).toBeVisible();

    // Verify no technical leaks on Home
    const homeText = await page.evaluate(() => document.body.innerText ?? "");
    for (const pattern of FORBIDDEN_TECHNICAL_LEAKS) {
      expect(pattern.test(homeText)).toBe(false);
    }

    // Click top-left area of the Treemap (largest sector tile) to navigate to /screener?icb_code_level2=...
    const box = await treemapCanvas.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.click(box!.x + box!.width * 0.2, box!.y + box!.height * 0.3);
    await page.waitForURL(/\/screener\?icb_code_level2=/, { timeout: 15_000 });
    await page.waitForLoadState("networkidle");

    const landedUrl = new URL(page.url());
    const clickedCode = landedUrl.searchParams.get("icb_code_level2");
    expect(clickedCode).toBeTruthy();
    const matchedSector = summaryJson.sectors.find(
      (s) => s.icb_code_level2 === clickedCode,
    );
    expect(matchedSector).toBeDefined();

    // Verify Screener total count matches the clicked sector's issuer_count
    await expect(
      page.locator("p").filter({ hasText: /Tìm thấy\s+\d+\s+doanh nghiệp/ }).first(),
    ).toContainText(`Tìm thấy ${matchedSector!.issuer_count} doanh nghiệp`);

    expect(consoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
  });

  test("Wave 1-6 Screener (/screener): Base UI Combobox filter, Saved Screen reload persistence, Column Customizer, and Export CSV > 50 rows", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/screener", { waitUntil: "networkidle" });
    await expect(page.locator("table")).toBeVisible();

    // 1. @base-ui/react Combobox filter (Risk Tier R1-R5)
    const riskTierTrigger = page.locator("#scr-risk-tier");
    await expect(riskTierTrigger).toBeVisible();
    await riskTierTrigger.click();
    const optionR4 = page.getByRole("option", { name: /R4 — Rủi ro cao/i });
    await expect(optionR4).toBeVisible();
    await optionR4.click();
    await expect(page).toHaveURL(/risk_tier=R4/);

    // 2. Save & load Saved Screen persisting across page.reload()
    const uniqueName = "E2E R4 Watch";
    await page.getByRole("button", { name: /Lưu bộ lọc/i }).click();
    const nameInput = page.getByLabel("Tên bộ lọc muốn lưu");
    await expect(nameInput).toBeVisible();
    await nameInput.fill(uniqueName);
    await page.getByRole("button", { name: /^Lưu$/ }).click();
    await expect(page.getByRole("button", { name: uniqueName, exact: true })).toBeVisible();

    // Reset filters, reload page, verify Saved Screen persists and restores URL on click
    await page.getByRole("button", { name: "Đặt lại", exact: true }).click();
    await expect(page).not.toHaveURL(/risk_tier=R4/);
    await page.reload({ waitUntil: "networkidle" });
    const savedChip = page.getByRole("button", { name: uniqueName, exact: true });
    await expect(savedChip).toBeVisible();
    await savedChip.click();
    await expect(page).toHaveURL(/risk_tier=R4/);

    // Clean up saved screen & reset filters so full set (> 50 rows) is active for CSV export
    await page.getByRole("button", { name: `Xóa bộ lọc ${uniqueName}` }).click();
    await expect(savedChip).toBeHidden();
    await page.getByRole("button", { name: "Đặt lại", exact: true }).click();
    await expect(page).not.toHaveURL(/risk_tier=R4/);
    await expect(page.locator("table")).toBeVisible();
    await expect(page.locator("thead th").first()).toBeVisible();

    // 3. Column Customizer opens and toggles additional columns
    const colBtn = page.getByRole("button", { name: /^Cột \(/ });
    await colBtn.click();
    const colDialog = page.getByRole("dialog", { name: "Tùy chỉnh cột phân tích" });
    await expect(colDialog).toBeVisible();
    const beforeCols = await page.locator("thead th").count();
    await colDialog.getByRole("button", { name: "Hiện tất cả" }).click();
    const afterCols = await page.locator("thead th").count();
    expect(afterCols).toBeGreaterThan(beforeCols);
    await colDialog.getByRole("button", { name: "Mặc định" }).click();
    await page.keyboard.press("Escape");

    // 4. Export CSV calls /api/v1/screener/export returning HTTP 200 with > 50 rows
    const exportBtn = page.getByRole("button", { name: /Xuất CSV \(\d+\)/ });
    await expect(exportBtn).toBeVisible();
    const exportPromise = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v1/screener/export") &&
        res.request().method() === "GET",
    );
    await exportBtn.click();
    const exportRes = await exportPromise;
    expect(exportRes.status()).toBe(200);
    const csvText = await exportRes.text();
    const nonEmptyLines = csvText
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l.length > 0);
    // Header + > 50 data rows
    expect(nonEmptyLines.length).toBeGreaterThan(51);
  });

  test("Wave 1-6 Company Profile (/companies/FPT): Credit Trio, Growth+Capital, PriceOverlay, Cross-Filter TreeSHAP, Radar+BoxPlot, FinancialTable, SourceDrawer focus-trap", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    const overlayPromise = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v2/companies/FPT/market-overlay") &&
        res.status() === 200,
    );
    await page.goto("/companies/FPT", { waitUntil: "networkidle" });
    await overlayPromise;

    // 1. R1-R5 + Top X% + 1Y PD credit trio
    await expect(page.getByText("Xác suất kiệt quệ 1 năm (1Y PD)")).toBeVisible();
    await expect(
      page.getByText(/\bR[1-5]\b|Chưa xếp hạng/).first(),
    ).toBeVisible();
    await expect(
      page.getByText(/Top\s+[\d.,]+%|Chưa có vị thế kỳ/i).first(),
    ).toBeVisible();

    // 2. Price + Early Warning R4/R5 chart (PriceRiskOverlayChart)
    await expect(
      page.getByText(/Đường giá điều chỉnh chia tách\/cổ tức/i),
    ).toBeVisible();

    // 3. Growth Combo + Capital Structure charts
    const growthSection = page.locator(
      'section[aria-label="Diễn biến tăng trưởng và cơ cấu vốn"]',
    );
    await expect(growthSection).toBeVisible();
    await expect(growthSection.locator("canvas")).toHaveCount(2);

    // 4. Cross-Filter: click an earlier quarter on historical distress prob chart to update TreeSHAP
    const riskHistoryCard = page
      .locator("div.overflow-hidden")
      .filter({
        hasText: "Diễn biến Xác suất kiệt quệ theo kỳ — Nhấp kỳ để xem TreeSHAP",
      })
      .first();
    const riskCanvas = riskHistoryCard.locator("canvas").first();
    await riskCanvas.scrollIntoViewIfNeeded();
    const rBox = await riskCanvas.boundingBox();
    expect(rBox).not.toBeNull();

    // Click an earlier x-position inside the plot grid (left-third -> earlier quarter) so TreeSHAP updates
    let crossFiltered = false;
    const plotLeft = rBox!.x + 56;
    const plotWidth = Math.max(40, rBox!.width - 80);
    for (const xFrac of [0.15, 0.3, 0.45]) {
      for (const yFrac of [0.5, 0.75, 0.85]) {
        await page.mouse.click(
          plotLeft + plotWidth * xFrac,
          rBox!.y + rBox!.height * yFrac,
        );
        const resetBtn = page.getByRole("button", { name: /Về kỳ mới nhất/i }).first();
        if (await resetBtn.isVisible({ timeout: 500 }).catch(() => false)) {
          crossFiltered = true;
          break;
        }
      }
      if (crossFiltered) break;
    }
    expect(crossFiltered, "Clicking a historical risk quarter should update TreeSHAP").toBe(true);
    await page.getByRole("button", { name: /Về kỳ mới nhất/i }).first().click();
    await expect(page.getByRole("button", { name: /Về kỳ mới nhất/i })).toHaveCount(0);

    // 5. SourceDrawer focus-trap (open via "Truy nguồn" button)
    const sourceBtn = page.getByRole("button", { name: /Truy nguồn/i }).first();
    await sourceBtn.scrollIntoViewIfNeeded();
    await sourceBtn.click();
    const drawer = page.getByRole("dialog").filter({ hasText: "Nguồn dữ liệu chỉ số" });
    await expect(drawer).toBeVisible();
    await expect(drawer).toHaveAttribute("aria-modal", "true");

    // Verify focus stays trapped inside the drawer across Tab presses
    await page.keyboard.press("Tab");
    const focusInsideDrawer = await page.evaluate(() => {
      const dlg = document.querySelector('[role="dialog"]');
      return Boolean(dlg && dlg.contains(document.activeElement));
    });
    expect(focusInsideDrawer).toBe(true);

    // Ensure no raw internal metric codes leak inside SourceDrawer
    const drawerText = await drawer.innerText();
    for (const pattern of FORBIDDEN_TECHNICAL_LEAKS) {
      expect(pattern.test(drawerText)).toBe(false);
    }
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();

    // 6. Ratios & Peers tab: Health Radar + Sector Box Plot
    await page.getByRole("tab", { name: "Tỷ số & Ngành" }).click();
    await expect(
      page.getByText(/Thang điểm 0–100 chuẩn hóa theo phân vị cùng ngành/i),
    ).toBeVisible();
    await expect(
      page.getByText(/Hộp thể hiện P25–P75, râu Tukey/i),
    ).toBeVisible();

    // 7. Financial Statements tab: Hierarchical FinancialTable
    await page.getByRole("tab", { name: "Báo cáo tài chính" }).click();
    await expect(page.locator("table").first()).toBeVisible();
    await expect(page.locator("tbody tr").nth(5)).toBeVisible({ timeout: 10_000 });
  });

  test("Wave 1-6 Stress Test (/stress-test): 3 scopes (Market, Single Company, Watchlist), Scenario A vs B comparison, and URL reload restoration", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // Seed watchlist so Watchlist scope has tickers
    await page.goto("/", { waitUntil: "networkidle" });
    await page.evaluate(() => {
      window.localStorage.setItem(
        "finintel:watchlist:v1",
        JSON.stringify(["FPT", "HPG"]),
      );
    });

    // 1. Scope 1: Full market
    const marketStress = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v1/stress-test") &&
        res.request().method() === "POST" &&
        res.status() === 200,
    );
    await page.goto("/stress-test", { waitUntil: "networkidle" });
    const marketJson = (await (await marketStress).json()) as {
      total_issuers_evaluated: number;
    };
    expect(marketJson.total_issuers_evaluated).toBeGreaterThan(50);

    // 2. Side-by-side Scenario A vs B comparison
    const pinBtn = page.getByRole("button", { name: /So sánh A vs B \(Ghim A\)/i });
    await expect(pinBtn).toBeVisible();
    await pinBtn.click();

    const revInput = page.locator("#shock-rev");
    const rateInput = page.locator("#shock-rate");
    await revInput.fill("-25");
    await rateInput.fill("350");

    await expect(
      page.getByText(/So sánh song song: Kịch bản A vs Kịch bản B/i).first(),
    ).toBeVisible({ timeout: 15_000 });

    // 3. URL reload restores shock parameters accurately
    await expect(page).toHaveURL(/rev=-25/);
    await expect(page).toHaveURL(/rate=350/);
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.locator("#shock-rev")).toHaveValue("-25");
    await expect(page.locator("#shock-rate")).toHaveValue("350");

    // 4. Scope 2: Single company (FPT)
    await page.getByRole("tab", { name: /1 Doanh nghiệp cụ thể/i }).click();
    const companySearch = page.getByLabel("Tìm doanh nghiệp để chạy Stress Test");
    await companySearch.fill("FPT");
    const singleStress = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v1/stress-test") &&
        res.request().method() === "POST" &&
        res.status() === 200,
    );
    await page
      .locator("button")
      .filter({ hasText: /^FPT/ })
      .first()
      .click();
    await singleStress;
    await expect(
      page.getByText(/Chi tiết tác động lên FPT/i),
    ).toBeVisible();

    // 5. Scope 3: Watchlist portfolio
    const watchlistStress = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v1/stress-test") &&
        res.request().method() === "POST" &&
        res.status() === 200,
    );
    await page.getByRole("tab", { name: /Danh mục Watchlist/i }).click();
    await watchlistStress;
    await expect(
      page.getByText(/Tổng nợ chịu rủi ro danh mục \(ICR < 1\)/i),
    ).toBeVisible();
  });

  test("Wave 1-6 Methodology (/methodology): loads /api/v2/meta/model-card, Walk-Forward chart, Tier R1->R5, and Case Study navigation", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const modelCardPromise = page.waitForResponse((res) =>
      res.url().includes("/api/v2/meta/model-card"),
    );
    await page.goto("/methodology?tab=models", { waitUntil: "networkidle" });
    const cardRes = await modelCardPromise;
    expect([200, 404]).toContain(cardRes.status());

    if (cardRes.status() === 200) {
      const cardJson = (await cardRes.json()) as {
        case_studies?: Array<{ ticker: string }>;
      };

      // Walk-Forward chart + Tier R1 -> R5 reliability chart
      await expect(
        page.getByText(/Kiểm định cuốn chiếu Walk-Forward/i),
      ).toBeVisible();
      await expect(
        page.getByText(/Độ tin cậy phân tầng tín nhiệm R1 → R5/i),
      ).toBeVisible();

      // Case Studies table & clicking a Case Study navigates to its Company Profile
      await expect(
        page.getByText(/Case Studies lịch sử Việt Nam/i),
      ).toBeVisible();
      const firstCaseTicker = cardJson.case_studies?.[0]?.ticker;
      expect(firstCaseTicker).toBeTruthy();

      const caseLink = page
        .locator(`a[href="/companies/${firstCaseTicker}"]`)
        .first();
      await expect(caseLink).toBeVisible();
      await caseLink.click();
      await page.waitForURL(new RegExp(`/companies/${firstCaseTicker}$`), {
        timeout: 15_000,
      });
      await expect(
        page.getByText(firstCaseTicker!, { exact: false }).first(),
      ).toBeVisible();
    } else {
      // Snapshot predates model_card_meta publication: verify clean Vietnamese fallback with zero leaks
      const bodyText = await page.evaluate(() => document.body.innerText ?? "");
      expect(bodyText.length).toBeGreaterThan(40);
      for (const pattern of FORBIDDEN_TECHNICAL_LEAKS) {
        expect(pattern.test(bodyText)).toBe(false);
      }
      // Verify Dictionary tab still renders real 38 ratios & 9 red flags
      await page.getByRole("button", { name: "Từ điển chỉ số & Cờ đỏ" }).click();
      await expect(page.getByText(/38 tỷ số tài chính chuẩn hóa/i)).toBeVisible();
      await expect(page.getByText(/9 quy tắc phát hiện bất thường kế toán/i)).toBeVisible();
    }
  });

  test("Wave 7B UI-08b (/companies/HBC): Điều tra Thuyết minh (PDF) Split-View, red-flag jump button, zero leaks & axe Light/Dark", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });

    const pageErrors: string[] = [];
    page.on("pageerror", (err) => pageErrors.push(err.message));

    await page.goto("/companies/HBC", { waitUntil: "networkidle" });
    await expect(page.getByText("HBC", { exact: false }).first()).toBeVisible();
    const docTabBtn = page.getByRole("tab", {
      name: "Điều tra Thuyết minh (PDF)",
    });
    await expect(docTabBtn).toBeVisible();

    // Navigate to Quality & Red Flags tab and test jump button on RF_03/RF_07/RF_09 when active
    await page.getByRole("tab", { name: "Chất lượng LN & Cờ đỏ" }).click();
    const listPromise = page.waitForResponse((res) =>
      res.url().includes("/api/v2/documents/HBC/list"),
    );

    const jumpBtn = page
      .getByRole("button", { name: /Kiểm tra Thuyết minh gốc/i })
      .first();
    let jumpedFromRedFlag = false;
    let invPromise: ReturnType<typeof page.waitForResponse> | null = null;
    if (await jumpBtn.isVisible({ timeout: 2_000 }).catch(() => false)) {
      jumpedFromRedFlag = true;
      invPromise = page.waitForResponse(
        (res) =>
          res.url().includes("/api/v2/documents/HBC/investigate") &&
          res.request().method() === "POST",
        { timeout: 45_000 },
      );
      await jumpBtn.click();
      await expect(page).toHaveURL(/tab=documents/);
    } else {
      await docTabBtn.click();
    }

    const listRes = await listPromise;
    expect([200, 503]).toContain(listRes.status());

    if (listRes.status() === 200) {
      const listJson = (await listRes.json()) as {
        total_documents: number;
      };
      if (listJson.total_documents > 0) {
        await expect(
          page.getByText("Truy vấn & Tổng hợp có trích dẫn"),
        ).toBeVisible();
        await expect(
          page.getByText("Bằng chứng PDF Gốc — Nguyên văn đoạn trích"),
        ).toBeVisible();
        await expect(page.locator("#rag-fiscal-year")).toBeVisible();

        const rf03Btn = page.getByRole("button", {
          name: /Phải thu ngoài khách hàng/i,
        });
        const rf07Btn = page.getByRole("button", {
          name: /Cảnh báo kiểm toán & Hoạt động liên tục/i,
        });
        const rf09Btn = page.getByRole("button", {
          name: /Xây dựng cơ bản dở dang/i,
        });
        await expect(rf03Btn).toBeVisible();
        await expect(rf07Btn).toBeVisible();
        await expect(rf09Btn).toBeVisible();

        const questionInput = page.locator("#rag-question-input");
        await expect(questionInput).toBeVisible();
        if (jumpedFromRedFlag) {
          await expect(questionInput).not.toHaveValue("");
        } else {
          invPromise = page.waitForResponse(
            (res) =>
              res.url().includes("/api/v2/documents/HBC/investigate") &&
              res.request().method() === "POST",
            { timeout: 45_000 },
          );
          await rf07Btn.click();
        }

        const invRes = await invPromise!;
        expect(invRes.status()).toBe(200);
        const invJson = (await invRes.json()) as {
          abstained: boolean;
          answer: string;
          abstention_reason: string | null;
          citations: Array<{ page_no: number }>;
        };
        expect(invJson.abstained).toBe(false);
        expect(invJson.citations.length).toBeGreaterThan(0);
        expect(invJson.answer).not.toMatch(/\bCHUNKS?\b/i);
        expect(invJson.abstention_reason ?? "").not.toMatch(/\bCHUNKS?\b/i);

        await expect(
          page.getByText(/Dữ liệu đệm sẵn \(Cache\)|Truy xuất mới/),
        ).toBeVisible({
          timeout: 15_000,
        });
        const firstBadge = page
          .getByRole("button", { name: /^\[1\]/ })
          .first();
        await expect(firstBadge).toBeVisible();
        await firstBadge.click();
      }
    }

    // Verify zero leaks, zero AI-slop, zero horizontal overflow, zero critical axe errors in Light & Dark Mode
    const bodyText = await page.evaluate(() => document.body.innerText ?? "");
    expect(bodyText).not.toContain("✨");
    for (const pattern of FORBIDDEN_TECHNICAL_LEAKS) {
      expect(pattern.test(bodyText)).toBe(false);
    }

    const overflowLight = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(overflowLight.scrollWidth).toBeLessThanOrEqual(
      overflowLight.clientWidth + 2,
    );

    const axeLight = await new AxeBuilder({ page }).analyze();
    expect(axeLight.violations.filter((v) => v.impact === "critical")).toEqual(
      [],
    );

    await page.getByRole("button", { name: "Giao diện Tối" }).click();
    await expect(page.locator("html")).toHaveClass(/\bdark\b/);
    const axeDark = await new AxeBuilder({ page }).analyze();
    expect(axeDark.violations.filter((v) => v.impact === "critical")).toEqual(
      [],
    );
    await page.getByRole("button", { name: "Giao diện Sáng" }).click();

    expect(pageErrors).toEqual([]);
  });
});
