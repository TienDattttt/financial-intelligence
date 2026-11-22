import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const AUDIT_ROUTES: readonly { path: string; label: string }[] = [
  { path: "/", label: "Home" },
  { path: "/screener", label: "Screener" },
  { path: "/companies/FPT", label: "Company Profile FPT" },
  { path: "/compare", label: "Compare" },
  { path: "/stress-test", label: "Stress Test" },
  { path: "/watchlist", label: "Watchlist" },
  { path: "/methodology?tab=models", label: "Methodology Model Card" },
];

const AUDIT_VIEWPORTS: readonly { name: string; width: number; height: number }[] = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1366x768", width: 1366, height: 768 },
  { name: "375x812", width: 375, height: 812 },
];

type ThemeMode = "light" | "dark";

async function setTheme(page: Page, mode: ThemeMode): Promise<void> {
  const buttonName = mode === "dark" ? "Giao diện Tối" : "Giao diện Sáng";
  const btn = page.getByRole("button", { name: buttonName });
  await expect(btn).toBeVisible();
  await btn.click();
  if (mode === "dark") {
    await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  } else {
    await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);
  }
}

function formatViolations(
  violations: Awaited<ReturnType<AxeBuilder["analyze"]>>["violations"],
): string {
  return violations
    .map((v) => {
      const sample = v.nodes
        .slice(0, 3)
        .map((n) => `${n.target.join(" ")} :: ${n.failureSummary ?? n.html}`)
        .join(" | ");
      return `[${v.impact ?? "unknown"}] ${v.id} (${v.nodes.length} nodes): ${sample}`;
    })
    .join("\n");
}

test.describe("FinIntel Light & Dark Accessibility Audit (axe-core)", () => {
  test.setTimeout(180_000);

  for (const mode of ["light", "dark"] as const) {
    for (const vp of AUDIT_VIEWPORTS) {
      test(`zero critical/serious axe violations (including color-contrast) in ${mode.toUpperCase()} mode @ ${vp.name}`, async ({
        page,
      }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.goto("/", { waitUntil: "networkidle" });
        await setTheme(page, mode);

        const allFailures: string[] = [];

        for (const route of AUDIT_ROUTES) {
          await page.goto(route.path, { waitUntil: "networkidle" });
          await page.waitForTimeout(350);

          if (mode === "dark") {
            await expect(page.locator("html")).toHaveClass(/\bdark\b/);
          } else {
            await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);
          }

          const axeResults = await new AxeBuilder({ page }).analyze();
          const blocking = axeResults.violations.filter(
            (v) => v.impact === "critical" || v.impact === "serious",
          );

          if (blocking.length > 0) {
            allFailures.push(
              `${mode.toUpperCase()} ${vp.name} ${route.path}:\n${formatViolations(blocking)}`,
            );
          }
        }

        expect(
          allFailures,
          `Found critical/serious axe-core violations in ${mode.toUpperCase()} @ ${vp.name}:\n${allFailures.join("\n\n")}`,
        ).toEqual([]);
      });
    }
  }

  test("InfoTooltip renders via Portal and is never clipped inside horizontally scrollable tables", async ({
    page,
  }) => {
    for (const vp of [
      { name: "1440x900", width: 1440, height: 900 },
      { name: "375x812", width: 375, height: 812 },
    ]) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto("/companies/FPT", { waitUntil: "networkidle" });

      // Locate an InfoTooltip trigger inside the overflow-x-auto peer benchmark table
      const tableWrapper = page
        .locator("div.overflow-x-auto")
        .filter({ has: page.locator('button[aria-label^="Giải thích "]') })
        .first();
      await expect(tableWrapper).toBeVisible();

      const trigger = tableWrapper
        .locator('button[aria-label^="Giải thích "]')
        .first();
      await trigger.scrollIntoViewIfNeeded();
      await trigger.focus();
      await trigger.hover();

      const tooltip = page.locator('[role="tooltip"]').first();
      await expect(tooltip).toBeVisible();

      const portalAndBounds = await page.evaluate(() => {
        const tip = document.querySelector('[role="tooltip"]');
        if (!tip) return null;
        const rect = tip.getBoundingClientRect();
        const insideOverflowTable = Boolean(
          tip.closest("div.overflow-x-auto"),
        );
        return {
          insideOverflowTable,
          width: rect.width,
          height: rect.height,
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
        };
      });

      expect(portalAndBounds, `Tooltip missing @ ${vp.name}`).not.toBeNull();
      expect(
        portalAndBounds!.insideOverflowTable,
        `Tooltip must be portaled outside overflow-x-auto table @ ${vp.name}`,
      ).toBe(false);
      expect(portalAndBounds!.width).toBeGreaterThan(20);
      expect(portalAndBounds!.height).toBeGreaterThan(12);
      expect(portalAndBounds!.left).toBeGreaterThanOrEqual(-1);
      expect(portalAndBounds!.right).toBeLessThanOrEqual(
        portalAndBounds!.viewportWidth + 1,
      );
      expect(portalAndBounds!.top).toBeGreaterThanOrEqual(-1);
      expect(portalAndBounds!.bottom).toBeLessThanOrEqual(
        portalAndBounds!.viewportHeight + 1,
      );

      await page.keyboard.press("Escape");
      await expect(tooltip).toBeHidden();
    }
  });
});
