import { describe, expect, it } from "vitest";
import {
  buildCsvContent,
  formatDateVi,
  formatDays,
  formatMultiple,
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatPeriodLabel,
  formatRatioByType,
  formatVndBillions,
  formatVndPerShare,
  getPercentileQuintile,
  MINUS_SIGN,
  normalizeSearchText,
  sanitizeCsvCell,
} from "@/lib/format";

describe("format.ts financial formatting", () => {
  it("formats numbers in vi-VN with mathematical minus sign", () => {
    expect(formatNumber(1234.567, 2)).toBe("1.234,57");
    expect(formatNumber(-42.5, 2)).toBe(`${MINUS_SIGN}42,50`);
    expect(formatNumber(0, 2)).toBe("0");
    expect(formatNumber(-0.0001, 2)).toBe("0");
    expect(formatNumber(null)).toBe("N/A");
    expect(formatNumber(Number.NaN)).toBe("N/A");
  });

  it("formats VND billions with accounting parentheses option", () => {
    expect(formatVndBillions(12_345_600_000_000)).toBe("12.345,6");
    expect(formatVndBillions(-2_500_000_000_000, { accountingNegative: true })).toBe(
      "(2.500,0)",
    );
    expect(formatVndBillions(-2_500_000_000_000, { accountingNegative: false })).toBe(
      `${MINUS_SIGN}2.500,0`,
    );
    expect(formatVndBillions(0)).toBe("–");
    expect(formatVndBillions(0, { zeroAsZero: true })).toBe("0");
    expect(formatVndBillions(undefined)).toBe("N/A");
    expect(formatVndBillions(null, { zeroAsZero: true })).toBe("N/A");
  });

  it("formats VND per share", () => {
    expect(formatVndPerShare(3456)).toBe("3.456");
    expect(formatVndPerShare(-500)).toBe(`${MINUS_SIGN}500`);
    expect(formatVndPerShare(0)).toBe("–");
    expect(formatVndPerShare(null)).toBe("N/A");
  });

  it("formats percentages and percentage points from decimal inputs", () => {
    expect(formatPercent(0.1534)).toBe("15,3%");
    expect(formatPercent(-0.042)).toBe(`${MINUS_SIGN}4,2%`);
    expect(formatPercent(0.085, { signed: true })).toBe("+8,5%");
    expect(formatPercentPoints(0.025, 1)).toBe("+2,5 điểm %");
    expect(formatPercentPoints(-0.014, 1)).toBe(`${MINUS_SIGN}1,4 điểm %`);
    expect(formatPercentPoints(0, 1)).toBe("0,0 điểm %");
  });

  it("formats ratios dynamically via formatRatioByType (U3)", () => {
    expect(formatRatioByType(0.1534, "PERCENT")).toBe("15,3%");
    expect(formatRatioByType(0.1534, "RATIO_PERCENT")).toBe("15,3%");
    expect(formatRatioByType(0.025, "PERCENT_POINTS")).toBe("+2,5 điểm %");
    expect(formatRatioByType(82.5, "PERCENTILE")).toBe("82,5%");
    expect(formatRatioByType(45.4, "DAYS")).toBe("45 ngày");
    expect(formatRatioByType(2.456, "MULTIPLE")).toBe("2,46x");
    expect(formatRatioByType(2.456, "TIMES")).toBe("2,46x");
    expect(formatRatioByType(1.5, "ZSCORE")).toBe("+1,50 σ");
    expect(formatRatioByType(-1.25, "ZSCORE")).toBe(`${MINUS_SIGN}1,25 σ`);
    expect(formatRatioByType(0, "ZSCORE")).toBe("0,00 σ");
    expect(formatRatioByType(3.64, "SIGMA")).toBe("3,64 σ");
    expect(formatRatioByType(3456, "CURRENCY_PER_SHARE")).toBe("3.456 đ/cp");
    expect(formatRatioByType(0, "CURRENCY_PER_SHARE")).toBe("0 đ/cp");
    expect(formatRatioByType(3, "QUARTERS")).toBe("3 quý");
    expect(formatRatioByType(7, "SCORE_9")).toBe("7/9");
    expect(formatRatioByType(1.234, "NUMBER")).toBe("1,23");
    expect(formatRatioByType(null, "PERCENT")).toBe("N/A");
    expect(formatRatioByType(undefined, "MULTIPLE")).toBe("N/A");
  });

  it("formats multiples, days, periods and Vietnamese dates", () => {
    expect(formatMultiple(2.456)).toBe("2,46x");
    expect(formatMultiple(-1.2)).toBe(`${MINUS_SIGN}1,20x`);
    expect(formatDays(45.4)).toBe("45 ngày");
    expect(formatDays(-3)).toBe(`${MINUS_SIGN}3 ngày`);
    expect(formatPeriodLabel("2026-09-30", "QUARTER")).toBe("Q3/2026");
    expect(formatPeriodLabel("2025-12-31", "ANNUAL")).toBe("Năm 2025");
    expect(formatDateVi("2026-09-30T10:15:00Z")).toBe("30/09/2026");
  });

  it("normalizes Vietnamese search strings cleanly", () => {
    expect(normalizeSearchText("  Tập đoàn Hòa Phát (HPG) - Đường sắt ")).toBe(
      "tap doan hoa phat (hpg) - duong sat",
    );
  });

  it("neutralizes spreadsheet formula prefixes and writes UTF-8 BOM CSV", () => {
    expect(sanitizeCsvCell("=SUM(A1:A2)")).toBe("'=SUM(A1:A2)");
    expect(sanitizeCsvCell("+cmd|'/C calc'!A0")).toBe("'+cmd|'/C calc'!A0");
    expect(sanitizeCsvCell("-100")).toBe("'-100");
    expect(sanitizeCsvCell("@HYPERLINK")).toBe("'@HYPERLINK");
    expect(sanitizeCsvCell('Thép "Hòa Phát", VN')).toBe('"Thép ""Hòa Phát"", VN"');

    const csv = buildCsvContent(["Mã", "Tên"], [["HPG", "=1+1"]]);
    expect(csv.startsWith("\uFEFFMã,Tên\r\nHPG,'=1+1")).toBe(true);
  });

  it("computes percentile quintiles honoring metric direction", () => {
    expect(getPercentileQuintile(90, "HIGHER_IS_BETTER")).toBe(5);
    expect(getPercentileQuintile(10, "HIGHER_IS_BETTER")).toBe(1);
    expect(getPercentileQuintile(10, "LOWER_IS_BETTER")).toBe(5);
    expect(getPercentileQuintile(90, "LOWER_IS_BETTER")).toBe(1);
    expect(getPercentileQuintile(85, "NEUTRAL")).toBe(3);
    expect(getPercentileQuintile(null, "HIGHER_IS_BETTER")).toBeNull();
  });
});
