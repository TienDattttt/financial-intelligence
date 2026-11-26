import type { UseQueryResult } from "@tanstack/react-query";
import { formatCreditTrio, getRiskTierMeta } from "@/lib/domain-labels";
import {
  buildCsvContent,
  formatNumber,
  formatPercent,
  formatPeriodLabel,
  formatRatioValue,
  type RatioDirection,
} from "@/lib/format";
import type {
  CompanyBenchmarksSummaryResponse,
  CompanyProfileResponse,
  MLScoreItem,
  RatioName,
} from "@/types/api";

export type CompareMetricKey =
  | "altman_z_double_prime"
  | "calibrated_distress_prob"
  | "roe_total"
  | "ebit_margin"
  | "debt_to_equity"
  | "interest_coverage"
  | "current_ratio"
  | "ocf_to_net_income";

export const CHART_METRICS: {
  key: CompareMetricKey;
  label: string;
  isMl: boolean;
  isPct: boolean;
  decimals: number;
  unitSuffix?: string;
}[] = [
  { key: "altman_z_double_prime", label: "Altman Z″", isMl: true, isPct: false, decimals: 2 },
  {
    key: "calibrated_distress_prob",
    label: "Xác suất kiệt quệ (%)",
    isMl: true,
    isPct: true,
    decimals: 1,
  },
  { key: "roe_total", label: "ROE (%)", isMl: false, isPct: true, decimals: 1 },
  { key: "ebit_margin", label: "Biên EBIT (%)", isMl: false, isPct: true, decimals: 1 },
  {
    key: "debt_to_equity",
    label: "Nợ vay / VCSH (lần)",
    isMl: false,
    isPct: false,
    decimals: 2,
    unitSuffix: "x",
  },
  {
    key: "interest_coverage",
    label: "Khả năng trả lãi EBIT/Lãi vay (lần)",
    isMl: false,
    isPct: false,
    decimals: 2,
    unitSuffix: "x",
  },
  {
    key: "current_ratio",
    label: "Thanh toán hiện hành (lần)",
    isMl: false,
    isPct: false,
    decimals: 2,
    unitSuffix: "x",
  },
  {
    key: "ocf_to_net_income",
    label: "CFO / Lợi nhuận sau thuế (lần)",
    isMl: false,
    isPct: false,
    decimals: 2,
    unitSuffix: "x",
  },
];

export function getComparePeriodKey(
  periodEndDate: string,
  periodType: string,
  fiscalYear?: unknown,
): string {
  if (periodType === "ANNUAL") {
    if (typeof fiscalYear === "number" && Number.isFinite(fiscalYear)) {
      return String(Math.trunc(fiscalYear));
    }
    return periodEndDate.slice(0, 4);
  }
  return periodEndDate;
}

export function formatCompareAxisLabel(periodKey: string, periodType: string): string {
  if (periodType === "ANNUAL") {
    return `Năm ${periodKey}`;
  }
  return formatPeriodLabel(periodKey, periodType);
}

export interface CompareEntry {
  ticker: string;
  idx: number;
  profileQuery: UseQueryResult<CompanyProfileResponse> | undefined;
  summaryQuery: UseQueryResult<CompanyBenchmarksSummaryResponse> | undefined;
}

export interface CompareMatrixData {
  ratioList: Map<
    RatioName,
    {
      name: string;
      formatType: "PERCENT" | "MULTIPLE" | "DAYS" | "CURRENCY_PER_SHARE" | "NUMBER";
      direction: RatioDirection;
    }
  >;
  latestMl: (MLScoreItem | null)[];
  latestFlagsCount: number[];
}

export function buildCompareCsv(
  activeTickers: string[],
  activeEntries: CompareEntry[],
  matrixData: CompareMatrixData,
  matrixMode: "VALUE" | "PERCENTILE",
): string {
  const headers = ["Chỉ tiêu", ...activeTickers];
  const rows: unknown[][] = [];
  rows.push([
    "Altman Z″",
    ...matrixData.latestMl.map((m) =>
      m?.altman_z_double_prime !== null && m?.altman_z_double_prime !== undefined
        ? formatNumber(m.altman_z_double_prime, 2)
        : "N/A",
    ),
  ]);
  rows.push([
    "Piotroski F-Score",
    ...matrixData.latestMl.map((m) =>
      m?.piotroski_f_score !== null && m?.piotroski_f_score !== undefined
        ? `${m.piotroski_f_score}/9`
        : "N/A",
    ),
  ]);
  rows.push([
    "Beneish M-Score",
    ...matrixData.latestMl.map((m) =>
      m?.beneish_m8_score !== null && m?.beneish_m8_score !== undefined
        ? formatNumber(m.beneish_m8_score, 2)
        : "N/A",
    ),
  ]);
  rows.push([
    "Bậc rủi ro (R1–R5)",
    ...matrixData.latestMl.map((m) => getRiskTierMeta(m?.risk_tier).shortLabel),
  ]);
  rows.push([
    "Vị thế rủi ro cùng kỳ",
    ...matrixData.latestMl.map(
      (m) =>
        formatCreditTrio({
          risk_tier: m?.risk_tier,
          risk_market_percentile: m?.risk_market_percentile,
          calibrated_distress_prob: m?.calibrated_distress_prob,
          relative_risk_lift: m?.relative_risk_lift,
          market_base_rate: m?.market_base_rate,
        }).topPercentLabel,
    ),
  ]);
  rows.push([
    "Xác suất kiệt quệ 1 năm",
    ...matrixData.latestMl.map((m) => formatPercent(m?.calibrated_distress_prob)),
  ]);
  rows.push([
    "Hệ số rủi ro so nền thị trường",
    ...matrixData.latestMl.map(
      (m) =>
        formatCreditTrio({
          relative_risk_lift: m?.relative_risk_lift,
        }).liftLabel ?? "N/A",
    ),
  ]);

  for (const [metricName, meta] of matrixData.ratioList.entries()) {
    rows.push([
      meta.name,
      ...activeEntries.map(({ summaryQuery }) => {
        const item = summaryQuery?.data?.items.find((x) => x.metric_name === metricName);
        if (!item) return "N/A";
        return matrixMode === "PERCENTILE"
          ? item.percentile_rank !== null && item.percentile_rank !== undefined
            ? `${formatNumber(item.percentile_rank, 0)}%`
            : "N/A"
          : formatRatioValue(item.company_value, item.format_type);
      }),
    ]);
  }

  return buildCsvContent(headers, rows);
}
