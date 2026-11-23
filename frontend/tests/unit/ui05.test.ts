import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildScreenerScatterOption,
  DEFAULT_SCATTER_X_AXIS,
  DEFAULT_SCATTER_Y_AXIS,
  formatScatterMetricValue,
  getScatterAxisMetric,
  getScatterPlottablePoints,
  SCATTER_AXIS_METRICS,
} from "@/components/charts/ScreenerScatterChart";
import { DARK_CHART_PALETTE, LIGHT_CHART_PALETTE } from "@/lib/echarts-theme";
import { MINUS_SIGN } from "@/lib/format";
import {
  ALL_SCREENER_OPTIONAL_COLUMN_IDS,
  BUILTIN_SCREENER_PRESETS,
  buildScreenerExportUrl,
  DEFAULT_VISIBLE_SCREENER_COLUMNS,
  deleteSavedScreen,
  filtersToUrlPatch,
  formatAnomalyFlagMeta,
  formatRelativeRiskLiftLabel,
  formatScreenerExportError,
  formatTopRiskPercentileLabel,
  getSavedScreensServerSnapshot,
  getSavedScreensSnapshot,
  getScreenerRiskTierMeta,
  getVisibleColumnsServerSnapshot,
  getVisibleColumnsSnapshot,
  parseContentDispositionFilename,
  parseSavedScreensJson,
  parseVisibleColumnsJson,
  renameSavedScreen,
  resetVisibleColumns,
  saveScreenerView,
  showAllScreenerColumns,
  toggleVisibleColumn,
  toggleVisibleColumnGroup,
} from "@/lib/saved-screens-store";
import type { ScreenerItem, ScreenerQueryParams } from "@/types/api";

function makeMemoryLocalStorage(initial?: Record<string, string>) {
  const store = new Map<string, string>(Object.entries(initial ?? {}));
  return {
    store,
    storage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    },
  };
}

function makeScreenerItem(
  ticker: string,
  overrides: Partial<ScreenerItem> = {},
): ScreenerItem {
  return {
    issuer_id: `ISS_${ticker}`,
    ticker,
    company_name_vn: `Công ty ${ticker}`,
    exchange: "HOSE",
    accounting_regime: "CORPORATE",
    icb_code_level2: "1700",
    icb_name_level2: "Tài nguyên cơ bản",
    icb_code_level3: "1750",
    icb_name_level3: "Thép",
    statement_scope: "CONSOLIDATED",
    period_type: "QUARTER",
    period_end_date: "2026-03-31",
    taxonomy_version: "VAS_TT200_2014",
    roe_total: 0.145,
    roa: 0.068,
    ebit_margin: 0.112,
    debt_to_equity: 0.85,
    interest_coverage: 4.2,
    accruals_to_assets: -0.025,
    core_operating_margin: 0.105,
    non_core_distortion_to_assets: 0.008,
    core_to_ebt_ratio: 0.92,
    red_flag_count: 1,
    critical_red_flag_count: 0,
    altman_z_double_prime: 2.85,
    altman_em_score: 5.4,
    altman_zone: "SAFE_ZONE",
    piotroski_f_score: 7,
    beneish_m8_score: -2.35,
    beneish_flag: "VALID",
    cluster_id: 2,
    cluster_archetype_name: "Sinh lời ổn định, đòn bẩy vừa phải",
    anomaly_flag: null,
    distress_risk_score: -2.1,
    calibrated_distress_prob: 0.018,
    risk_tier: "R2",
    risk_market_percentile: 28.5,
    market_base_rate: 0.024,
    relative_risk_lift: 0.75,
    model_role: "CALIBRATED_PD_MODEL",
    model_version: "v2.1.0",
    ...overrides,
  };
}

describe("UI-05a Screener export URL builder, saved screens store, columns & scatter axes", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds full /api/v1/screener/export URL with all active filters and zero limit/offset cap", () => {
    const params: ScreenerQueryParams & { page?: number } = {
      period_type: "QUARTER",
      period_end_date: "2026-03-31",
      statement_scope: "CONSOLIDATED",
      icb_code_level2: "1700",
      min_f_score: 5,
      altman_zone: "DISTRESS_ZONE",
      risk_tier: "R5",
      beneish_flag: "HIGH_MANIPULATION_RISK_SCREEN",
      max_distress_prob: 0.85,
      min_distress_prob: 0.05,
      max_red_flags: 6,
      min_red_flags: 2,
      q: "HPG",
      sort_by: "calibrated_distress_prob",
      sort_dir: "desc",
      limit: 50,
      offset: 100,
      page: 3,
    };

    const url = buildScreenerExportUrl(params);
    const parsed = new URL(url, "https://finintel.local");

    expect(parsed.pathname).toBe("/api/v1/screener/export");
    expect(parsed.searchParams.has("limit")).toBe(false);
    expect(parsed.searchParams.has("offset")).toBe(false);
    expect(parsed.searchParams.has("page")).toBe(false);

    expect(parsed.searchParams.get("period_type")).toBe("QUARTER");
    expect(parsed.searchParams.get("period_end_date")).toBe("2026-03-31");
    expect(parsed.searchParams.get("statement_scope")).toBe("CONSOLIDATED");
    expect(parsed.searchParams.get("icb_code_level2")).toBe("1700");
    expect(parsed.searchParams.get("min_f_score")).toBe("5");
    expect(parsed.searchParams.get("altman_zone")).toBe("DISTRESS_ZONE");
    expect(parsed.searchParams.get("risk_tier")).toBe("R5");
    expect(parsed.searchParams.get("beneish_flag")).toBe(
      "HIGH_MANIPULATION_RISK_SCREEN",
    );
    expect(parsed.searchParams.get("max_distress_prob")).toBe("0.85");
    expect(parsed.searchParams.get("min_distress_prob")).toBe("0.05");
    expect(parsed.searchParams.get("max_red_flags")).toBe("6");
    expect(parsed.searchParams.get("min_red_flags")).toBe("2");
    expect(parsed.searchParams.get("q")).toBe("HPG");
    expect(parsed.searchParams.get("sort_by")).toBe("calibrated_distress_prob");
    expect(parsed.searchParams.get("sort_dir")).toBe("desc");

    expect(buildScreenerExportUrl({ q: null, min_f_score: undefined })).toBe(
      "/api/v1/screener/export",
    );
  });

  it("parses Content-Disposition filenames safely and sanitizes export error messages", () => {
    expect(
      parseContentDispositionFilename(
        'attachment; filename="finintel-screener.csv"',
      ),
    ).toBe("finintel-screener.csv");
    expect(
      parseContentDispositionFilename(
        "attachment; filename*=UTF-8''finintel-bo-loc.csv",
      ),
    ).toBe("finintel-bo-loc.csv");
    expect(parseContentDispositionFilename(null)).toBe("finintel-screener.csv");

    expect(formatScreenerExportError(null)).toContain("Không thể kết nối");
    expect(formatScreenerExportError(422)).toContain("tham số không hợp lệ");
    const leaked = formatScreenerExportError(
      500,
      "Traceback C:\\Users\\dat\\src\\finintel\\api.py failed on .parquet",
    );
    expect(leaked).not.toContain(".parquet");
    expect(leaked).not.toContain("Traceback");
    expect(leaked).not.toContain("C:\\");
  });

  it("provides SSR-safe defaults and recovers cleanly from corrupted localStorage JSON", () => {
    // Node environment without window
    expect(getSavedScreensServerSnapshot()).toEqual([]);
    expect(getSavedScreensSnapshot()).toEqual([]);
    expect(getVisibleColumnsServerSnapshot()).toEqual(
      DEFAULT_VISIBLE_SCREENER_COLUMNS,
    );
    expect(getVisibleColumnsSnapshot()).toEqual(
      DEFAULT_VISIBLE_SCREENER_COLUMNS,
    );

    expect(parseSavedScreensJson("{invalid json")).toEqual([]);
    expect(parseSavedScreensJson(JSON.stringify({ bad: true }))).toEqual([]);
    expect(
      parseSavedScreensJson(
        JSON.stringify([{ id: "", name: "", filters: { sort_by: "BAD" } }]),
      ),
    ).toEqual([]);
    expect(parseVisibleColumnsJson("{bad json")).toEqual(
      DEFAULT_VISIBLE_SCREENER_COLUMNS,
    );
    expect(parseVisibleColumnsJson(JSON.stringify(["unknown_col"]))).toEqual(
      DEFAULT_VISIBLE_SCREENER_COLUMNS,
    );

    const { storage } = makeMemoryLocalStorage({
      "finintel:saved-screens:v1": "corrupted{{{",
      "finintel:screener-columns:v1": '["not_a_column"]',
    });
    vi.stubGlobal("window", { localStorage: storage });

    expect(getSavedScreensSnapshot()).toEqual([]);
    expect(getVisibleColumnsSnapshot()).toEqual(
      DEFAULT_VISIBLE_SCREENER_COLUMNS,
    );
  });

  it("supports full Saved Screens CRUD with Zod validation and built-in presets", () => {
    const { store, storage } = makeMemoryLocalStorage();
    vi.stubGlobal("window", { localStorage: storage });

    // Built-in professional presets
    expect(BUILTIN_SCREENER_PRESETS.map((p) => p.name)).toEqual([
      "Rủi ro cao R4–R5",
      "Cảnh báo dòng tiền & đòn bẩy",
      "Sức khỏe ổn định R1–R2",
    ]);
    const patch = filtersToUrlPatch(BUILTIN_SCREENER_PRESETS[0].filters);
    expect(patch.altman_zone).toBe("DISTRESS_ZONE");
    expect(patch.sort_by).toBe("calibrated_distress_prob");

    // Empty name rejected
    const invalidRes = saveScreenerView("   ", { risk_tier: "R5" });
    expect(invalidRes.ok).toBe(false);

    // Save custom filter
    const saved = saveScreenerView("Thép đòn bẩy cao", {
      icb_code_level2: "1700",
      risk_tier: "R4",
      min_red_flags: "2",
      sort_by: "debt_to_equity",
      sort_dir: "desc",
    });
    expect(saved.ok).toBe(true);
    if (!saved.ok) throw new Error("Expected save to succeed");

    expect(getSavedScreensSnapshot()).toHaveLength(1);
    expect(store.get("finintel:saved-screens:v1")).toContain("Thép đòn bẩy cao");

    // Rename
    const renamed = renameSavedScreen(saved.screen.id, "Thép rủi ro R4");
    expect(renamed.ok).toBe(true);
    expect(getSavedScreensSnapshot()[0].name).toBe("Thép rủi ro R4");

    // Reject blank rename
    expect(renameSavedScreen(saved.screen.id, "").ok).toBe(false);

    // Delete
    deleteSavedScreen(saved.screen.id);
    expect(getSavedScreensSnapshot()).toEqual([]);
  });

  it("unlocks all ScreenerItem analytical columns and persists column preferences", () => {
    const expectedUnlocked: readonly string[] = [
      "roa",
      "accruals_to_assets",
      "core_operating_margin",
      "non_core_distortion_to_assets",
      "core_to_ebt_ratio",
      "critical_red_flag_count",
      "altman_em_score",
      "cluster_archetype_name",
      "anomaly_flag",
      "risk_tier",
      "risk_market_percentile",
      "relative_risk_lift",
      "market_base_rate",
    ];
    for (const colId of expectedUnlocked) {
      expect(ALL_SCREENER_OPTIONAL_COLUMN_IDS).toContain(colId);
    }

    const { storage } = makeMemoryLocalStorage();
    vi.stubGlobal("window", { localStorage: storage });

    expect(getVisibleColumnsSnapshot()).toEqual(
      DEFAULT_VISIBLE_SCREENER_COLUMNS,
    );

    toggleVisibleColumn("roa");
    expect(getVisibleColumnsSnapshot()).toContain("roa");

    toggleVisibleColumnGroup("forensic");
    expect(getVisibleColumnsSnapshot()).toContain("accruals_to_assets");
    expect(getVisibleColumnsSnapshot()).toContain("core_operating_margin");

    showAllScreenerColumns();
    expect(getVisibleColumnsSnapshot()).toEqual(ALL_SCREENER_OPTIONAL_COLUMN_IDS);

    resetVisibleColumns();
    expect(getVisibleColumnsSnapshot()).toEqual(
      DEFAULT_VISIBLE_SCREENER_COLUMNS,
    );
  });

  it("formats R1–R5 tier, Top X% riskiest, lift, and anomaly flags without technical leaks", () => {
    expect(getScreenerRiskTierMeta("R5")).toMatchObject({
      tier: "R5",
      shortLabel: "R5 · Rất cao",
      tone: "high",
    });
    expect(getScreenerRiskTierMeta("R1")).toMatchObject({
      tier: "R1",
      shortLabel: "R1 · Rất thấp",
      tone: "low",
    });
    expect(getScreenerRiskTierMeta(null).tone).toBe("neutral");

    expect(formatTopRiskPercentileLabel(98.3)).toBe("Top 1,7% rủi ro nhất");
    expect(formatTopRiskPercentileLabel(80)).toBe("Top 20% rủi ro nhất");
    expect(formatTopRiskPercentileLabel(null)).toBeNull();

    expect(formatRelativeRiskLiftLabel(14.6)).toBe("14,6× nền");
    expect(formatRelativeRiskLiftLabel(undefined)).toBeNull();

    expect(formatAnomalyFlagMeta(null)).toEqual({
      label: "Bình thường",
      tone: "low",
    });
    expect(
      formatAnomalyFlagMeta(
        "Hồ sơ tài chính đặc thù (Ngoại lai đa biến — Tiêu cực)",
      ),
    ).toEqual({
      label: "Ngoại lai tiêu cực",
      tone: "high",
    });
    expect(formatAnomalyFlagMeta("OUTLIER_RAW_TOKEN").label).not.toContain(
      "OUTLIER_RAW_TOKEN",
    );
  });

  it("supports dynamic Scatter X/Y axes, R1–R5 theme grouping, and preserves raw tooltip values", () => {
    const keys = SCATTER_AXIS_METRICS.map((m) => m.key);
    expect(keys).toContain("calibrated_distress_prob");
    expect(keys).toContain("altman_z_double_prime");
    expect(keys).toContain("piotroski_f_score");
    expect(keys).toContain("beneish_m8_score");
    expect(keys).toContain("roe_total");
    expect(keys).toContain("roa");
    expect(keys).toContain("ebit_margin");
    expect(keys).toContain("debt_to_equity");
    expect(keys).toContain("interest_coverage");
    expect(keys).toContain("red_flag_count");
    expect(keys).toContain("accruals_to_assets");
    expect(keys).toContain("core_operating_margin");

    const items: ScreenerItem[] = [
      makeScreenerItem("FPT", {
        risk_tier: "R1",
        calibrated_distress_prob: 0.006,
        roe_total: 0.285,
        debt_to_equity: 0.35,
      }),
      makeScreenerItem("HBC", {
        risk_tier: "R5",
        altman_z_double_prime: -9.5, // clamped to -6 on axis, preserved in rawX
        calibrated_distress_prob: 0.42,
        risk_market_percentile: 99.1,
        relative_risk_lift: 18.2,
        roe_total: -1.45, // clamped to -100 on % axis, preserved in rawX
        debt_to_equity: 15.2,
      }),
      makeScreenerItem("SKIP", {
        roe_total: null,
        debt_to_equity: 1.2,
      }),
    ];

    const defaultX = getScatterAxisMetric(DEFAULT_SCATTER_X_AXIS);
    const defaultY = getScatterAxisMetric(DEFAULT_SCATTER_Y_AXIS);
    const defaultPts = getScatterPlottablePoints(items, defaultX, defaultY);
    expect(defaultPts).toHaveLength(3);
    const hbcDefault = defaultPts.find((p) => p.ticker === "HBC");
    expect(hbcDefault?.value[0]).toBe(-6);
    expect(hbcDefault?.rawX).toBe(-9.5);
    expect(hbcDefault?.value[1]).toBe(42);
    expect(hbcDefault?.topRiskLabel).toBe("Top 0,9% rủi ro nhất");

    const roeMetric = getScatterAxisMetric("roe_total");
    const deMetric = getScatterAxisMetric("debt_to_equity");
    const customPts = getScatterPlottablePoints(items, roeMetric, deMetric);
    expect(customPts.map((p) => p.ticker)).toEqual(["FPT", "HBC"]);
    expect(formatScatterMetricValue(customPts[1].rawX, roeMetric)).toBe(
      `${MINUS_SIGN}145,0%`,
    );

    const lightOpt = buildScreenerScatterOption(
      customPts,
      roeMetric,
      deMetric,
      LIGHT_CHART_PALETTE,
    );
    const darkOpt = buildScreenerScatterOption(
      customPts,
      roeMetric,
      deMetric,
      DARK_CHART_PALETTE,
    );
    const lightSeries = lightOpt.series as { name: string; itemStyle: { color: string } }[];
    const darkSeries = darkOpt.series as { name: string; itemStyle: { color: string } }[];

    expect(lightSeries.map((s) => s.name)).toEqual([
      "R1 · Rất thấp",
      "R2 · Thấp",
      "R3 · Theo dõi",
      "R4 · Cao",
      "R5 · Rất cao",
    ]);
    expect(lightSeries[0].itemStyle.color).toBe(LIGHT_CHART_PALETTE.riskLow);
    expect(darkSeries[0].itemStyle.color).toBe(DARK_CHART_PALETTE.riskLow);
    expect(lightSeries[4].itemStyle.color).toBe(LIGHT_CHART_PALETTE.riskHigh);
    expect(darkSeries[4].itemStyle.color).toBe(DARK_CHART_PALETTE.riskHigh);
  });

  it("contains zero getDistressRiskBadge, zero hardcoded PD thresholds, zero leaked tokens, and zero hardcoded hex in ScatterChart", () => {
    const root = path.resolve(__dirname, "../..");
    const screenerSrc = fs.readFileSync(
      path.join(root, "src/app/screener/page.tsx"),
      "utf8",
    );
    const scatterSrc = fs.readFileSync(
      path.join(root, "src/components/charts/ScreenerScatterChart.tsx"),
      "utf8",
    );
    const storeSrc = fs.readFileSync(
      path.join(root, "src/lib/saved-screens-store.ts"),
      "utf8",
    );

    expect(screenerSrc).not.toContain("getDistressRiskBadge");
    expect(screenerSrc).not.toContain("buildCsvContent");
    expect(scatterSrc).not.toMatch(/#[0-9a-fA-F]{6}/);

    for (const src of [screenerSrc, scatterSrc]) {
      expect(src).not.toContain(".parquet");
    }
    for (const src of [screenerSrc, scatterSrc, storeSrc]) {
      expect(src).not.toContain("< 3%");
      expect(src).not.toContain(">= 8%");
      expect(src).not.toContain("BS_TOTAL_ASSETS");
      expect(src).not.toContain("REGIME_FALLBACK");
      expect(src).not.toContain("NEGATIVE_EARNINGS");
    }
  });
});
