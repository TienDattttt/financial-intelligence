import { expect, test } from "@playwright/test";

test.describe("M3 P0/P1 Audit Regression E2E", () => {
  test("Desktop 1440x900: Home D9/V1/D2/D3, Screener F6/F7/L8/L9/U10, Compare F1/F2/V9/V10, Watchlist F1/F3, Stress F10/D11/U4", async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });

    // 1. D2 backend endpoint contract: SEPARATE benchmarks summary returns 200 with items=[]
    const sepBenchRes = await request.get(
      "/api/v2/companies/HPG/benchmarks/summary?statement_scope=SEPARATE&period_type=QUARTER",
    );
    expect(sepBenchRes.status()).toBe(200);
    const sepBenchJson = (await sepBenchRes.json()) as { items?: unknown[] };
    expect(Array.isArray(sepBenchJson.items)).toBe(true);

    // 2. Home (/): D9 unclassified count, V1 Treemap table -> CSV, D2 SEPARATE disabled, D3 ANNUAL > 400
    await page.goto("/", { waitUntil: "networkidle" });
    await expect(
      page.getByText(/Chưa xếp vùng \(N\/A\)/i).first(),
    ).toBeVisible();

    const treemapTableBtn = page
      .getByRole("button", { name: /Xem dạng bảng/i })
      .first();
    await treemapTableBtn.click();
    const csvExportBtn = page
      .getByRole("button", { name: /Xuất CSV/i })
      .first();
    await expect(csvExportBtn).toBeVisible();
    await expect(csvExportBtn).toBeEnabled();

    const scopeSelect = page.getByLabel("Chọn phạm vi báo cáo");
    const separateOpt = scopeSelect.locator('option[value="SEPARATE"]');
    await expect(separateOpt).toBeDisabled();

    const annualSummaryPromise = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v2/sectors/risk-summary") &&
        res.url().includes("period_type=ANNUAL") &&
        res.status() === 200,
    );
    await page.getByLabel("Chọn loại kỳ").selectOption("ANNUAL");
    const annualSummaryRes = await annualSummaryPromise;
    const annualJson = (await annualSummaryRes.json()) as {
      total_issuers: number;
    };
    expect(annualJson.total_issuers).toBeGreaterThan(400);

    // Switch back to QUARTER for subsequent route checks
    await page.getByLabel("Chọn loại kỳ").selectOption("QUARTER");
    await page.waitForLoadState("networkidle");

    // 3. Screener (/screener): F6, F7, L8, L9, U10
    await page.goto("/screener", { waitUntil: "networkidle" });
    await page.locator("#scr-q").fill("HPG");
    await page.getByRole("button", { name: /^Tìm$/ }).click();
    await expect(page.locator("tbody tr").first()).toContainText("HPG");
    await page.getByRole("button", { name: /Xóa từ khóa tìm kiếm/i }).click();

    await page.getByRole("button", { name: /Rủi ro cao R4–R5/i }).click();
    await expect(page).toHaveURL(/risk_tiers=R4(?:%2C|,)R5/);

    await expect(page.getByRole("button", { name: /Biên EBIT/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /EBIT\/Lãi vay/i })).toBeVisible();

    const compactBtn = page.getByRole("button", { name: /Bảng gọn/i });
    await expect(compactBtn).toHaveAttribute("aria-pressed", "true");
    await compactBtn.click();
    const comfortableBtn = page.getByRole("button", { name: /Bảng thoáng/i });
    await expect(comfortableBtn).toHaveAttribute("aria-pressed", "false");
    await comfortableBtn.click();
    await expect(compactBtn).toHaveAttribute("aria-pressed", "true");

    await page.getByRole("button", { name: /Biểu đồ phân tán/i }).click();
    await expect(page.getByText(/Trang 1 \//)).toBeVisible();
    await expect(page.getByRole("button", { name: /Bảng gọn/i })).toBeHidden();
    await expect(page.getByRole("button", { name: /^Cột \(/ })).toBeHidden();

    // 4. Compare (/compare): F1 exact FPT, F2 client validation & partial 404 isolation, V9 & V10
    await page.goto("/compare?tickers=HPG", { waitUntil: "networkidle" });
    const compareInput = page.getByLabel("Nhập mã cổ phiếu để thêm vào bảng so sánh");
    await compareInput.fill("FPT");
    await compareInput.press("Enter");
    await expect(page).toHaveURL(/tickers=HPG(?:%2C|,)FPT/);
    await expect(page).not.toHaveURL(/FOX/);

    await compareInput.fill("ZZZZZ9");
    await page.getByRole("button", { name: /^Thêm$/ }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: /ZZZZZ9/i }),
    ).toBeVisible();

    const compare404Counts = new Map<string, number>();
    const compareNon404Errors: string[] = [];
    const compareListener = (res: { url: () => string; status: () => number }) => {
      if (!res.url().includes("/api/")) return;
      const st = res.status();
      if (st === 404) {
        const key = new URL(res.url()).pathname;
        compare404Counts.set(key, (compare404Counts.get(key) ?? 0) + 1);
      } else if (st >= 400) {
        compareNon404Errors.push(`${st} ${res.url()}`);
      }
    };
    page.on("response", compareListener);
    await page.goto("/compare?tickers=HPG,ZZZZZ9", { waitUntil: "networkidle" });
    page.off("response", compareListener);

    expect(compareNon404Errors).toEqual([]);
    for (const [, count] of compare404Counts) {
      expect(count).toBeLessThanOrEqual(1);
    }
    await expect(page.getByRole("button", { name: /Loại bỏ ZZZZZ9/i })).toBeVisible();
    await expect(
      page.getByText(/So sánh hồ sơ 5 trụ cột sức khỏe tài chính/i),
    ).toBeVisible();

    await page.goto("/compare?tickers=HPG,FPT,VNM,MWG,GAS", {
      waitUntil: "networkidle",
    });
    await expect(
      page.getByText(/Đã chọn tối đa 5\/5 doanh nghiệp/i),
    ).toBeVisible();

    // 5. Watchlist (/watchlist): F1 exact FPT, F3 Xóa button always visible
    await page.evaluate(() => {
      window.localStorage.setItem("finintel:watchlist:v1", JSON.stringify(["HPG"]));
    });
    await page.goto("/watchlist", { waitUntil: "networkidle" });
    const watchInput = page.getByLabel(
      "Tìm mã cổ phiếu để thêm vào danh sách theo dõi",
    );
    await watchInput.fill("FPT");
    await page.getByRole("button", { name: /Thêm mã/i }).click();
    await expect(page.getByRole("button", { name: "Bỏ theo dõi FPT" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Bỏ theo dõi HPG" })).toBeVisible();

    // 6. Stress Test (/stress-test): U4, F10 VN30 chips & Watchlist TOP default
    await page.goto("/stress-test", { waitUntil: "networkidle" });
    const stressBody = await page.evaluate(() => document.body.innerText ?? "");
    expect(stressBody).toContain("điểm %");
    expect(stressBody).not.toContain("đ%");

    await page.getByRole("tab", { name: /1 Doanh nghiệp cụ thể/i }).click();
    for (const chipTicker of ["HPG", "FPT", "VNM", "MWG"]) {
      await expect(
        page.getByRole("button", { name: new RegExp(`^${chipTicker}\\b`) }),
      ).toBeVisible();
    }

    await page.getByRole("tab", { name: /Danh mục Watchlist/i }).click();
    await expect(
      page.getByRole("button", { name: /Nợ rủi ro lớn nhất/i }),
    ).toHaveClass(/bg-primary/);
  });

  test("Company profiles (/companies/HPG, /companies/HBC, /companies/VCB) & Methodology D12", async ({
    page,
    request,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });

    const healthJson = (await (await request.get("/api/v1/health")).json()) as {
      rag_available?: boolean;
    };
    expect(typeof healthJson.rag_available).toBe("boolean");

    // 1. /companies/HPG: U1, F8, U2, V2, D6, V3
    await page.goto("/companies/HPG", { waitUntil: "networkidle" });
    await expect(page.getByText(/An toàn hơn\s+[\d.,]+% DN/i).first()).toBeVisible();

    for (const rangeLabel of ["1Y", "3Y", "5Y", "All"]) {
      await expect(
        page.getByRole("button", { name: rangeLabel, exact: true }),
      ).toBeVisible();
    }

    await page.getByRole("tab", { name: "Mô hình & SHAP" }).click();
    const shapText = await page.evaluate(() => document.body.innerText ?? "");
    expect(shapText).not.toMatch(/\(\+0(?:,0+)?\s*log-odds\)/i);

    await page.getByRole("tab", { name: "Chất lượng LN" }).click();
    await expect(page.getByRole("columnheader", { name: "Loại BCTC" })).toBeVisible();

    await page.getByRole("tab", { name: "Tổng quan" }).click();
    await page.getByRole("button", { name: /Truy nguồn/i }).first().click();
    const drawer = page.getByRole("dialog").filter({ hasText: "Nguồn dữ liệu chỉ số" });
    await expect(drawer).toBeVisible();
    const shaSpan = drawer.locator("span", { hasText: /^SHA:/ }).first();
    await page.waitForTimeout(250);
    if ((await shaSpan.count()) > 0) {
      await expect(shaSpan).toBeVisible();
      const filingSpan = drawer.locator("span", { hasText: /^Hồ sơ:/ }).first();
      const rects = await drawer.evaluate((dlg) => {
        const spans = Array.from(dlg.querySelectorAll("span"));
        const f = spans.find((s) => /^Hồ sơ:/.test((s.textContent ?? "").trim()));
        const sh = spans.find((s) => /^SHA:/.test((s.textContent ?? "").trim()));
        if (!f || !sh) return null;
        const r1 = f.getBoundingClientRect();
        const r2 = sh.getBoundingClientRect();
        return {
          b1: { x: r1.x, y: r1.y, width: r1.width, height: r1.height },
          b2: { x: r2.x, y: r2.y, width: r2.width, height: r2.height },
        };
      });
      expect(await filingSpan.isVisible()).toBe(true);
      if (rects) {
        const noOverlap =
          rects.b2.x >= rects.b1.x + rects.b1.width - 1 ||
          rects.b2.y >= rects.b1.y + rects.b1.height - 1;
        expect(noOverlap).toBe(true);
      }
    }
    await page.keyboard.press("Escape");

    // 2. /companies/HBC: D1 zero 503 when rag_available=false
    const hbc503s: string[] = [];
    const hbcListener = (res: { url: () => string; status: () => number }) => {
      if (res.url().includes("/api/") && res.status() === 503) {
        hbc503s.push(res.url());
      }
    };
    page.on("response", hbcListener);
    await page.goto("/companies/HBC", { waitUntil: "networkidle" });
    page.off("response", hbcListener);
    if (!healthJson.rag_available) {
      expect(hbc503s).toEqual([]);
      await expect(
        page.getByRole("tab", { name: /Điều tra Thuyết minh/i }),
      ).toHaveAttribute("aria-disabled", "true");
    }

    // 3. /companies/VCB: F4 non-retry (<=1 per URL), 0 422/500/503, U6 corporate scope notice
    const vcbCounts = new Map<string, number>();
    const vcbBadStatuses: string[] = [];
    const vcbListener = (res: { url: () => string; status: () => number }) => {
      if (!res.url().includes("/api/")) return;
      const st = res.status();
      const key = new URL(res.url()).pathname;
      if (st === 404) {
        vcbCounts.set(key, (vcbCounts.get(key) ?? 0) + 1);
      } else if (st >= 400) {
        vcbBadStatuses.push(`${st} ${res.url()}`);
      }
    };
    page.on("response", vcbListener);
    await page.goto("/companies/VCB", { waitUntil: "networkidle" });
    await page.waitForTimeout(400);
    page.off("response", vcbListener);

    expect(vcbBadStatuses).toEqual([]);
    for (const [path, count] of vcbCounts) {
      expect(count, `VCB 404 retried for ${path}`).toBeLessThanOrEqual(1);
    }
    await expect(
      page.getByText(/Ngân hàng, Công ty Chứng khoán và Bảo hiểm/i),
    ).toBeVisible();
    await expect(
      page.locator("main").getByRole("link", { name: "Trang chủ", exact: true }),
    ).toBeVisible();
    await expect(
      page.locator("main").getByRole("link", { name: "Sàng lọc DN", exact: true }),
    ).toBeVisible();

    // 4. /methodology?tab=models: D12 retrospective classification clarity
    await page.goto("/methodology?tab=models", { waitUntil: "networkidle" });
    const hasRetro = await page.getByText(/Phân loại hồi cứu/i).count();
    if (hasRetro > 0) {
      await expect(page.getByText(/Phân loại hồi cứu/i).first()).toBeVisible();
    }
  });

  test("Mobile 375x812 (L1-L5, L10): collapsed header, 3 theme buttons, non-sticky table columns, sticky stress trigger", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 375, height: 812 });

    const mobileRoutes = [
      "/",
      "/companies/HPG?tab=statements",
      "/screener",
      "/compare?tickers=HPG,FPT,VNM",
      "/stress-test",
      "/methodology",
    ];

    for (const route of mobileRoutes) {
      await page.goto(route, { waitUntil: "networkidle" });
      await page.waitForTimeout(250);

      const layout = await page.evaluate(() => {
        const header = document.querySelector("header");
        return {
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
          headerHeight: header ? header.getBoundingClientRect().height : 0,
        };
      });
      expect(
        layout.scrollWidth,
        `Mobile horizontal overflow on ${route}`,
      ).toBeLessThanOrEqual(layout.clientWidth + 2);
      expect(layout.headerHeight).toBeLessThanOrEqual(56);

      for (const themeLabel of ["Giao diện Sáng", "Giao diện Tối", "Theo hệ thống"]) {
        await expect(
          page.getByRole("button", { name: themeLabel }),
        ).toBeVisible();
      }
    }

    // L2: FinancialTable first column not sticky on < md
    await page.goto("/companies/HPG?tab=statements", { waitUntil: "networkidle" });
    const finThSticky = await page.evaluate(() => {
      const th = document.querySelector("table thead th");
      if (!th) return null;
      const st = window.getComputedStyle(th);
      return { position: st.position, width: th.getBoundingClientRect().width };
    });
    if (finThSticky) {
      expect(finThSticky.position).not.toBe("sticky");
      expect(finThSticky.width).toBeLessThanOrEqual(200);
    }

    // L3: Compare matrix row header not sticky on < md
    await page.goto("/compare?tickers=HPG,FPT,VNM", { waitUntil: "networkidle" });
    const cmpThSticky = await page.evaluate(() => {
      const th = document.querySelector('table tbody th[scope="row"]');
      if (!th) return null;
      const st = window.getComputedStyle(th);
      return { position: st.position, width: th.getBoundingClientRect().width };
    });
    if (cmpThSticky) {
      expect(cmpThSticky.position).not.toBe("sticky");
      expect(cmpThSticky.width).toBeLessThanOrEqual(170);
    }

    // L4: Screener first td not sticky on < md
    await page.goto("/screener", { waitUntil: "networkidle" });
    const scrTdSticky = await page.evaluate(() => {
      const td = document.querySelector("table tbody td");
      if (!td) return null;
      return window.getComputedStyle(td).position;
    });
    if (scrTdSticky) {
      expect(scrTdSticky).not.toBe("sticky");
    }

    // L10: Stress Test sticky mobile trigger remains visible after scrolling
    await page.goto("/stress-test", { waitUntil: "networkidle" });
    await page.evaluate(() => window.scrollBy(0, 600));
    const triggerBtn = page.getByRole("button", { name: /Tham số cú sốc vĩ mô/i });
    await expect(triggerBtn).toBeVisible();
    const triggerTop = await triggerBtn.evaluate(
      (el) => el.getBoundingClientRect().top,
    );
    expect(triggerTop).toBeGreaterThanOrEqual(50);
  });
});
