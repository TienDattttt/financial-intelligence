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

  test("interactive flows: live screener, FPT profile, and live stress-test POST", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // 1. Live Screener shows real issuers from FastAPI
    await page.goto("/screener", { waitUntil: "networkidle" });
    await expect(page.locator("table")).toBeVisible();
    const rowCount = await page.locator("tbody tr").count();
    expect(rowCount).toBeGreaterThan(5);

    // 2. Live Company Profile (/companies/FPT) renders real KPIs
    await page.goto("/companies/FPT", { waitUntil: "networkidle" });
    await expect(page.getByText("FPT", { exact: false }).first()).toBeVisible();

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
});
