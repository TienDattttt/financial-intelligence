/**
 * Pure financial formatting, CSV injection guard, Vietnamese search normalization,
 * and percentile quintile helpers conforming to DESIGN.md §2.1.
 */

export const MINUS_SIGN = "\u2212";

export type RatioFormatType =
  | "PERCENT"
  | "MULTIPLE"
  | "DAYS"
  | "CURRENCY_PER_SHARE"
  | "NUMBER";

export type RatioDirection = "HIGHER_IS_BETTER" | "LOWER_IS_BETTER" | "NEUTRAL";

function isFiniteNum(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function makeViFormatter(decimals: number): Intl.NumberFormat {
  return new Intl.NumberFormat("vi-VN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function normalizeZero(value: number, decimals: number): number {
  const threshold = 0.5 * Math.pow(10, -decimals);
  return Math.abs(value) < threshold ? 0 : value;
}

export function formatNumber(
  value: number | null | undefined,
  decimals = 2,
): string {
  if (!isFiniteNum(value)) return "N/A";
  const clean = normalizeZero(value, decimals);
  if (clean === 0) return "0";
  const absFormatted = makeViFormatter(decimals).format(Math.abs(clean));
  return clean < 0 ? `${MINUS_SIGN}${absFormatted}` : absFormatted;
}

export function formatVndBillions(
  value: number | null | undefined,
  opts?: { accountingNegative?: boolean; decimals?: number; zeroAsZero?: boolean },
): string {
  if (!isFiniteNum(value)) return "N/A";
  const decimals = opts?.decimals ?? 1;
  const billions = normalizeZero(value / 1_000_000_000, decimals);
  if (billions === 0) return opts?.zeroAsZero ? "0" : "–";
  const absFormatted = makeViFormatter(decimals).format(Math.abs(billions));
  if (billions < 0) {
    return opts?.accountingNegative
      ? `(${absFormatted})`
      : `${MINUS_SIGN}${absFormatted}`;
  }
  return absFormatted;
}

export function formatVndPerShare(value: number | null | undefined): string {
  if (!isFiniteNum(value)) return "N/A";
  const clean = normalizeZero(value, 0);
  if (clean === 0) return "–";
  const absFormatted = makeViFormatter(0).format(Math.abs(clean));
  return clean < 0 ? `${MINUS_SIGN}${absFormatted}` : absFormatted;
}

export function formatPercent(
  decimalValue: number | null | undefined,
  opts?: { signed?: boolean; decimals?: number },
): string {
  if (!isFiniteNum(decimalValue)) return "N/A";
  const decimals = opts?.decimals ?? 1;
  const pct = normalizeZero(decimalValue * 100, decimals);
  const absFormatted = makeViFormatter(decimals).format(Math.abs(pct));
  if (pct === 0) return `${absFormatted}%`;
  if (pct < 0) return `${MINUS_SIGN}${absFormatted}%`;
  return opts?.signed ? `+${absFormatted}%` : `${absFormatted}%`;
}

export function formatPercentPoints(
  decimalDelta: number | null | undefined,
  decimals = 1,
): string {
  if (!isFiniteNum(decimalDelta)) return "N/A";
  const pp = normalizeZero(decimalDelta * 100, decimals);
  const absFormatted = makeViFormatter(decimals).format(Math.abs(pp));
  if (pp === 0) return `${absFormatted} điểm %`;
  if (pp < 0) return `${MINUS_SIGN}${absFormatted} điểm %`;
  return `+${absFormatted} điểm %`;
}

export function formatMultiple(
  value: number | null | undefined,
  decimals = 2,
): string {
  if (!isFiniteNum(value)) return "N/A";
  const clean = normalizeZero(value, decimals);
  const absFormatted = makeViFormatter(decimals).format(Math.abs(clean));
  return clean < 0 ? `${MINUS_SIGN}${absFormatted}x` : `${absFormatted}x`;
}

export function formatDays(
  value: number | null | undefined,
  decimals = 0,
): string {
  if (!isFiniteNum(value)) return "N/A";
  const clean = normalizeZero(value, decimals);
  const absFormatted = makeViFormatter(decimals).format(Math.abs(clean));
  return clean < 0
    ? `${MINUS_SIGN}${absFormatted} ngày`
    : `${absFormatted} ngày`;
}

export function formatRatioValue(
  value: number | null | undefined,
  formatType: RatioFormatType,
  opts?: { signed?: boolean },
): string {
  switch (formatType) {
    case "PERCENT":
      return formatPercent(value, { signed: opts?.signed, decimals: 1 });
    case "MULTIPLE":
      return formatMultiple(value, 2);
    case "DAYS":
      return formatDays(value, 0);
    case "CURRENCY_PER_SHARE":
      return formatVndPerShare(value);
    case "NUMBER":
    default:
      return formatNumber(value, 2);
  }
}

export function formatRatioByType(
  value: number | null | undefined,
  formatType?: string | null,
): string {
  if (!isFiniteNum(value)) return "N/A";
  const normalized = formatType?.trim().toUpperCase() ?? "NUMBER";
  switch (normalized) {
    case "PERCENT":
    case "RATIO_PERCENT":
      return formatPercent(value, { decimals: 1 });
    case "PERCENT_POINTS":
      return formatPercentPoints(value, 1);
    case "PERCENTILE":
    case "PERCENT_100":
      return `${formatNumber(value, 1)}%`;
    case "DAYS":
      return formatDays(value, 0);
    case "MULTIPLE":
    case "RATIO_TIMES":
    case "TIMES":
      return formatMultiple(value, 2);
    case "ZSCORE": {
      const clean = normalizeZero(value, 2);
      const absStr = makeViFormatter(2).format(Math.abs(clean));
      if (clean === 0) return `${absStr} σ`;
      return clean > 0 ? `+${absStr} σ` : `${MINUS_SIGN}${absStr} σ`;
    }
    case "STD":
    case "SIGMA": {
      const clean = normalizeZero(value, 2);
      const absStr = makeViFormatter(2).format(Math.abs(clean));
      if (clean === 0) return `${absStr} σ`;
      return clean < 0 ? `${MINUS_SIGN}${absStr} σ` : `${absStr} σ`;
    }
    case "CURRENCY_PER_SHARE": {
      const clean = normalizeZero(value, 0);
      if (clean === 0) return "0 đ/cp";
      return `${formatVndPerShare(clean)} đ/cp`;
    }
    case "QUARTERS":
      return `${formatNumber(Math.round(value), 0)} quý`;
    case "SCORE_9":
      return `${formatNumber(Math.round(value), 0)}/9`;
    case "NUMBER":
    default:
      return formatNumber(value, 2);
  }
}

export function formatPeriodLabel(
  periodEndDate: string | null | undefined,
  periodType: string = "QUARTER",
): string {
  if (!periodEndDate) return "N/A";
  const parts = periodEndDate.split("-");
  if (parts.length !== 3) return periodEndDate;
  const year = Number(parts[0]);
  const month = Number(parts[1]);
  if (!Number.isFinite(year) || !Number.isFinite(month)) return periodEndDate;
  if (periodType === "ANNUAL") {
    return `Năm ${year}`;
  }
  const quarter = Math.min(4, Math.max(1, Math.ceil(month / 3)));
  return `Q${quarter}/${year}`;
}

export function formatDateVi(isoDate: string | null | undefined): string {
  if (!isoDate) return "N/A";
  const datePart = isoDate.slice(0, 10);
  const parts = datePart.split("-");
  if (parts.length !== 3) return isoDate;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

export function normalizeSearchText(input: string): string {
  return input
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .trim();
}

const FORMULA_PREFIXES = new Set(["=", "+", "-", "@", "\t", "\r"]);

export function sanitizeCsvCell(raw: unknown): string {
  if (raw === null || raw === undefined) return "";
  const str = String(raw);
  if (str.length === 0) return "";
  const neutralized = FORMULA_PREFIXES.has(str[0]) ? `'${str}` : str;
  if (
    neutralized.includes(",") ||
    neutralized.includes('"') ||
    neutralized.includes("\n") ||
    neutralized.includes("\r")
  ) {
    return `"${neutralized.replace(/"/g, '""')}"`;
  }
  return neutralized;
}

export function buildCsvContent(
  headers: readonly string[],
  rows: readonly (readonly unknown[])[],
): string {
  const headerLine = headers.map((h) => sanitizeCsvCell(h)).join(",");
  const bodyLines = rows.map((row) =>
    row.map((cell) => sanitizeCsvCell(cell)).join(","),
  );
  return `\uFEFF${[headerLine, ...bodyLines].join("\r\n")}`;
}

export function getPercentileQuintile(
  percentile: number | null | undefined,
  direction: RatioDirection = "HIGHER_IS_BETTER",
): 1 | 2 | 3 | 4 | 5 | null {
  if (!isFiniteNum(percentile)) return null;
  const clamped = Math.max(0, Math.min(100, percentile));
  if (direction === "NEUTRAL") return 3;
  const effective = direction === "LOWER_IS_BETTER" ? 100 - clamped : clamped;
  if (effective < 20) return 1;
  if (effective < 40) return 2;
  if (effective < 60) return 3;
  if (effective < 80) return 4;
  return 5;
}
