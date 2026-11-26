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
  { path: "/methodology", label: "Methodology Dictionary" },
];

const AUDIT_VIEWPORTS: readonly { name: string; width: number; height: number }[] = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "720x450-zoom200", width: 720, height: 450 },
  { name: "320x800", width: 320, height: 800 },
];

const WCAG_22_AA_TAGS = [
  "wcag2a",
  "wcag2aa",
  "wcag21a",
  "wcag21aa",
  "wcag22aa",
];

type ThemeMode = "light" | "dark";

interface CwvSnapshot {
  lcpMs: number;
  cls: number;
  inpMs: number;
}

declare global {
  interface Window {
    __finintelCwv?: CwvSnapshot;
  }
}

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

test.describe("FinIntel Light & Dark WCAG 2.2 AA Accessibility Audit (axe-core)", () => {
  test.setTimeout(240_000);

  for (const mode of ["light", "dark"] as const) {
    for (const vp of AUDIT_VIEWPORTS) {
      test(`zero critical/serious wcag22aa violations & zero horizontal overflow in ${mode.toUpperCase()} mode @ ${vp.name}`, async ({
        page,
      }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.goto("/", { waitUntil: "networkidle" });
        await setTheme(page, mode);

        const allFailures: string[] = [];

        for (const route of AUDIT_ROUTES) {
          await page.goto(route.path, { waitUntil: "networkidle" });
          await page.waitForTimeout(300);

          if (mode === "dark") {
            await expect(page.locator("html")).toHaveClass(/\bdark\b/);
          } else {
            await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);
          }

          const overflow = await page.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
          }));
          if (overflow.scrollWidth > overflow.clientWidth + 2) {
            allFailures.push(
              `${mode.toUpperCase()} ${vp.name} ${route.path}: horizontal overflow (${overflow.scrollWidth} > ${overflow.clientWidth})`,
            );
          }

          const axeResults = await new AxeBuilder({ page })
            .withTags(WCAG_22_AA_TAGS)
            .analyze();
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
          `Found WCAG 2.2 AA or reflow violations in ${mode.toUpperCase()} @ ${vp.name}:\n${allFailures.join("\n\n")}`,
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

test.describe("FinIntel Local CWV Budgets (LCP <= 2500ms, INP <= 200ms, CLS <= 0.10)", () => {
  test.setTimeout(120_000);

  test("CWV budgets hold across core analytical routes under real browser interaction", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => {
      const state: CwvSnapshot = { lcpMs: 0, cls: 0, inpMs: 0 };
      window.__finintelCwv = state;

      try {
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            if (entry.startTime > state.lcpMs) {
              state.lcpMs = entry.startTime;
            }
          }
        }).observe({ type: "largest-contentful-paint", buffered: true });
      } catch {
        // Unsupported observer in environment
      }

      try {
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            const shift = entry as PerformanceEntry & {
              hadRecentInput?: boolean;
              value?: number;
            };
            if (!shift.hadRecentInput && typeof shift.value === "number") {
              state.cls += shift.value;
            }
          }
        }).observe({ type: "layout-shift", buffered: true });
      } catch {
        // Unsupported observer in environment
      }

      try {
        const eventInit: PerformanceObserverInit & { durationThreshold?: number } = {
          type: "event",
          buffered: true,
          durationThreshold: 0,
        };
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            if (entry.duration > state.inpMs) {
              state.inpMs = entry.duration;
            }
          }
        }).observe(eventInit);
      } catch {
        // Unsupported observer in environment
      }
    });

    const cwvRoutes = ["/", "/screener", "/companies/FPT", "/methodology"] as const;

    for (const route of cwvRoutes) {
      await page.goto(route, { waitUntil: "networkidle" });

      // Trigger real user interaction to exercise INP & layout stability
      if (route === "/methodology") {
        await page.getByRole("tab", { name: "Chất lượng dữ liệu" }).click();
      } else if (route === "/companies/FPT") {
        await page.getByRole("tab", { name: "Tỷ số & Ngành" }).click();
      } else {
        await page.getByRole("button", { name: "Giao diện Tối" }).click();
        await page.getByRole("button", { name: "Giao diện Sáng" }).click();
      }

      await page.evaluate(
        () =>
          new Promise<void>((resolve) => {
            requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
          }),
      );

      const metrics = await page.evaluate(
        () => window.__finintelCwv ?? { lcpMs: 0, cls: 0, inpMs: 0 },
      );

      expect(
        metrics.lcpMs,
        `LCP missing (0ms) on ${route}`,
      ).toBeGreaterThan(0);
      expect(
        metrics.lcpMs,
        `LCP exceeded 2500ms on ${route}: ${metrics.lcpMs.toFixed(1)}ms`,
      ).toBeLessThanOrEqual(2500);
      expect(
        metrics.inpMs,
        `INP exceeded 200ms on ${route}: ${metrics.inpMs.toFixed(1)}ms`,
      ).toBeLessThanOrEqual(200);
      expect(
        metrics.cls,
        `CLS exceeded 0.10 on ${route}: ${metrics.cls.toFixed(4)}`,
      ).toBeLessThanOrEqual(0.1);
    }
  });
});
