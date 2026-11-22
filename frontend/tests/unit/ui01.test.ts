import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  applyChartThemeDefaults,
  DARK_CHART_PALETTE,
  getChartMotionOptions,
  LIGHT_CHART_PALETTE,
  resolveThemePreference,
} from "@/lib/echarts-theme";

function relativeLuminance(hex: string): number {
  const clean = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => {
    const channel = parseInt(clean.slice(i, i + 2), 16) / 255;
    return channel <= 0.03928
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(fgHex: string, bgHex: string): number {
  const l1 = relativeLuminance(fgHex);
  const l2 = relativeLuminance(bgHex);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

describe("UI-01 Design System 2026 tokens, contrast, ECharts theme, and offline fonts", () => {
  it("satisfies WCAG AA (>= 4.5:1) contrast in Light Mode and Dark Mode", () => {
    const lightPairs: [string, string, string][] = [
      ["light ink / surface", LIGHT_CHART_PALETTE.ink, LIGHT_CHART_PALETTE.surface],
      ["light inkMuted / surface", LIGHT_CHART_PALETTE.inkMuted, LIGHT_CHART_PALETTE.surface],
      ["light inkSubtle / surface", LIGHT_CHART_PALETTE.inkSubtle, LIGHT_CHART_PALETTE.surface],
      ["light inkSubtle / surfaceMuted", LIGHT_CHART_PALETTE.inkSubtle, LIGHT_CHART_PALETTE.surfaceMuted],
      ["light company / surface", LIGHT_CHART_PALETTE.company, LIGHT_CHART_PALETTE.surface],
      ["light riskLow / soft", "#1F6E4E", "#E6F4ED"],
      ["light riskMid / soft", "#9A5B13", "#FBF0D9"],
      ["light riskHigh / soft", "#A61B1B", "#FBEAE9"],
      ["light primary / white", "#1E3A5F", "#FFFFFF"],
    ];

    const darkPairs: [string, string, string][] = [
      ["dark ink / surface", DARK_CHART_PALETTE.ink, DARK_CHART_PALETTE.surface],
      ["dark inkMuted / surface", DARK_CHART_PALETTE.inkMuted, DARK_CHART_PALETTE.surface],
      ["dark inkMuted / surfaceMuted", DARK_CHART_PALETTE.inkMuted, DARK_CHART_PALETTE.surfaceMuted],
      ["dark inkSubtle / surface", DARK_CHART_PALETTE.inkSubtle, DARK_CHART_PALETTE.surface],
      ["dark inkSubtle / surfaceMuted", DARK_CHART_PALETTE.inkSubtle, DARK_CHART_PALETTE.surfaceMuted],
      ["dark company / surface", DARK_CHART_PALETTE.company, DARK_CHART_PALETTE.surface],
      ["dark company / accentSoft", DARK_CHART_PALETTE.company, "#17253B"],
      ["dark riskLow / soft", "#34D399", "#0F2E24"],
      ["dark riskMid / soft", "#FBBF24", "#33240B"],
      ["dark riskHigh / soft", "#F87171", "#3B1518"],
      ["dark primary / white", "#1D4ED8", "#FFFFFF"],
    ];

    for (const [label, fg, bg] of [...lightPairs, ...darkPairs]) {
      const ratio = contrastRatio(fg, bg);
      expect(ratio, `${label} contrast (${fg} on ${bg})`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("bans purple (#6B4C9A) and cyan colors from chart series palettes", () => {
    const forbidden = new Set(["#6B4C9A", "#00FFFF", "#06B6D4", "#22D3EE"]);
    for (const color of [...LIGHT_CHART_PALETTE.series, ...DARK_CHART_PALETTE.series]) {
      expect(forbidden.has(color.toUpperCase())).toBe(false);
    }
    expect(LIGHT_CHART_PALETTE.series).toHaveLength(5);
    expect(DARK_CHART_PALETTE.series).toHaveLength(5);
  });

  it("resolves Light / Dark / System theme preferences deterministically", () => {
    expect(resolveThemePreference("light", true)).toBe("light");
    expect(resolveThemePreference("dark", false)).toBe("dark");
    expect(resolveThemePreference("system", false)).toBe("light");
    expect(resolveThemePreference("system", true)).toBe("dark");
  });

  it("enables 250ms ECharts animation normally and disables it when reduced-motion is active", () => {
    expect(getChartMotionOptions(false)).toEqual({
      animation: true,
      animationDuration: 250,
      animationDurationUpdate: 250,
      animationEasing: "cubicOut",
    });
    expect(getChartMotionOptions(true)).toEqual({
      animation: false,
      animationDuration: 0,
      animationDurationUpdate: 0,
    });

    const darkThemed = applyChartThemeDefaults(
      {
        tooltip: { trigger: "axis" },
        legend: { top: 4 },
        xAxis: { type: "category", data: ["Q1/2025"] },
        yAxis: { type: "value" },
        series: [{ type: "line", data: [1] }],
      },
      "dark",
      false,
    );
    expect(darkThemed.animation).toBe(true);
    expect(darkThemed.animationDuration).toBe(250);
    expect(darkThemed.backgroundColor).toBe("transparent");
    expect((darkThemed.tooltip as { backgroundColor?: string }).backgroundColor).toBe(
      DARK_CHART_PALETTE.tooltipBg,
    );

    const reducedThemed = applyChartThemeDefaults(
      {
        xAxis: { type: "category", data: ["Q1/2025"] },
        yAxis: { type: "value" },
        series: [{ type: "line", data: [1] }],
      },
      "light",
      true,
    );
    expect(reducedThemed.animation).toBe(false);
    expect(reducedThemed.animationDuration).toBe(0);
  });

  it("self-hosts IBM Plex fonts without next/font/google, Source Serif 4, #6B4C9A, or dual-Y-axis charts", () => {
    const root = path.resolve(__dirname, "../..");
    const layoutSrc = fs.readFileSync(path.join(root, "src/app/layout.tsx"), "utf8");
    const globalsSrc = fs.readFileSync(path.join(root, "src/app/globals.css"), "utf8");
    const compareSrc = fs.readFileSync(path.join(root, "src/app/compare/page.tsx"), "utf8");
    const companySrc = fs.readFileSync(
      path.join(root, "src/app/companies/[ticker]/page.tsx"),
      "utf8",
    );

    expect(layoutSrc).toContain("@fontsource/ibm-plex-sans/400.css");
    expect(layoutSrc).toContain("@fontsource/ibm-plex-mono/400.css");
    expect(layoutSrc).not.toContain("next/font/google");
    expect(layoutSrc).not.toContain("Source_Serif_4");
    expect(globalsSrc).not.toContain("Source Serif 4");
    expect(globalsSrc).toContain("--canvas: #0b0f14;");
    expect(globalsSrc).toContain("--canvas: #f8fafc;");
    expect(compareSrc).not.toContain("#6B4C9A");
    expect(companySrc).not.toMatch(/yAxis:\s*\[/);
  });
});
