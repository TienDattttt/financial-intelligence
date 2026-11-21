"use client";

import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
} from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import type {
  IssuerItem,
  PeriodType,
  PeriodView,
  RatioName,
  ScreenerQueryParams,
  StatementScope,
  StatementType,
  StressScenarioRequest,
} from "@/types/api";

const SNAPSHOT_STALE_MS = 1000 * 60 * 60; // Immutable snapshot data pinned by snapshotId

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: apiClient.getHealth,
    staleTime: 30_000,
  });
}

export function useSnapshotOverview(snapshotId?: string | null) {
  return useQuery({
    queryKey: ["snapshot-overview", snapshotId ?? "latest"],
    queryFn: apiClient.getSnapshotOverview,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useMetaPeriods(snapshotId?: string | null) {
  return useQuery({
    queryKey: ["meta-periods", snapshotId ?? "latest"],
    queryFn: apiClient.getMetaPeriods,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useMetaDictionary(snapshotId?: string | null) {
  return useQuery({
    queryKey: ["meta-dictionary", snapshotId ?? "latest"],
    queryFn: apiClient.getMetaDictionary,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useAllIssuers(snapshotId?: string | null) {
  return useQuery({
    queryKey: ["all-issuers", snapshotId ?? "latest"],
    queryFn: async (): Promise<IssuerItem[]> => {
      const first = await apiClient.getIssuersPage(200, 0);
      if (first.total <= first.items.length) {
        return first.items;
      }
      const pagesNeeded = Math.ceil((first.total - first.items.length) / 200);
      const rest = await Promise.all(
        Array.from({ length: pagesNeeded }, (_, idx) =>
          apiClient.getIssuersPage(200, (idx + 1) * 200),
        ),
      );
      return [...first.items, ...rest.flatMap((page) => page.items)];
    },
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useCompanyProfile(
  ticker: string,
  statementScope: StatementScope,
  periodView: PeriodView,
  snapshotId?: string | null,
) {
  const cleanTicker = ticker.trim().toUpperCase();
  return useQuery({
    queryKey: [
      "company-profile",
      snapshotId ?? "latest",
      cleanTicker,
      statementScope,
      periodView,
    ],
    queryFn: () =>
      apiClient.getCompanyProfile(cleanTicker, {
        statement_scope: statementScope,
        period_view: periodView,
      }),
    enabled: cleanTicker.length > 0,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useCompanyBenchmarks(
  ticker: string,
  opts: {
    metricName: RatioName;
    statementScope: StatementScope;
    periodType: PeriodType;
    periodEndDate?: string | null;
    enabled?: boolean;
  },
  snapshotId?: string | null,
) {
  const cleanTicker = ticker.trim().toUpperCase();
  return useQuery({
    queryKey: [
      "company-benchmarks",
      snapshotId ?? "latest",
      cleanTicker,
      opts.metricName,
      opts.statementScope,
      opts.periodType,
      opts.periodEndDate ?? "latest",
    ],
    queryFn: () =>
      apiClient.getCompanyBenchmarks(cleanTicker, {
        metric_name: opts.metricName,
        statement_scope: opts.statementScope,
        period_type: opts.periodType,
        period_end_date: opts.periodEndDate,
        include_history: true,
        peer_limit: 200,
      }),
    enabled: (opts.enabled ?? true) && cleanTicker.length > 0,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useCompanyBenchmarksSummary(
  ticker: string,
  opts: {
    statementScope: StatementScope;
    periodType: PeriodType;
    periodEndDate?: string | null;
    metrics?: readonly RatioName[];
    enabled?: boolean;
  },
  snapshotId?: string | null,
) {
  const cleanTicker = ticker.trim().toUpperCase();
  return useQuery({
    queryKey: [
      "company-benchmarks-summary",
      snapshotId ?? "latest",
      cleanTicker,
      opts.statementScope,
      opts.periodType,
      opts.periodEndDate ?? "latest",
      opts.metrics?.join(",") ?? "core",
    ],
    queryFn: () =>
      apiClient.getCompanyBenchmarksSummary(cleanTicker, {
        statement_scope: opts.statementScope,
        period_type: opts.periodType,
        period_end_date: opts.periodEndDate,
        metrics: opts.metrics,
      }),
    enabled: (opts.enabled ?? true) && cleanTicker.length > 0,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useCompanyStatements(
  ticker: string,
  opts: {
    statementType: StatementType;
    statementScope: StatementScope;
    periodType: PeriodType;
    periodsLimit?: number;
    enabled?: boolean;
  },
  snapshotId?: string | null,
) {
  const cleanTicker = ticker.trim().toUpperCase();
  return useQuery({
    queryKey: [
      "company-statements",
      snapshotId ?? "latest",
      cleanTicker,
      opts.statementType,
      opts.statementScope,
      opts.periodType,
      opts.periodsLimit ?? 8,
    ],
    queryFn: () =>
      apiClient.getCompanyStatements(cleanTicker, {
        statement_type: opts.statementType,
        statement_scope: opts.statementScope,
        period_type: opts.periodType,
        periods_limit: opts.periodsLimit ?? 8,
      }),
    enabled: (opts.enabled ?? true) && cleanTicker.length > 0,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useCompanyFilings(
  ticker: string,
  opts?: { statementScope?: StatementScope; enabled?: boolean },
  snapshotId?: string | null,
) {
  const cleanTicker = ticker.trim().toUpperCase();
  return useQuery({
    queryKey: [
      "company-filings",
      snapshotId ?? "latest",
      cleanTicker,
      opts?.statementScope ?? "ALL",
    ],
    queryFn: () =>
      apiClient.getCompanyFilings(cleanTicker, {
        statement_scope: opts?.statementScope,
      }),
    enabled: (opts?.enabled ?? true) && cleanTicker.length > 0,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useRatioLineage(
  opts: {
    ticker: string;
    ratioName: RatioName;
    statementScope: StatementScope;
    periodType: PeriodType;
    periodEndDate?: string | null;
    enabled?: boolean;
  } | null,
  snapshotId?: string | null,
) {
  const ticker = opts?.ticker.trim().toUpperCase() ?? "";
  return useQuery({
    queryKey: [
      "ratio-lineage",
      snapshotId ?? "latest",
      ticker,
      opts?.ratioName ?? "",
      opts?.statementScope ?? "CONSOLIDATED",
      opts?.periodType ?? "QUARTER",
      opts?.periodEndDate ?? "latest",
    ],
    queryFn: () => {
      if (!opts) throw new Error("Missing lineage lookup parameters");
      return apiClient.getRatioLineage(ticker, {
        ratio_name: opts.ratioName,
        statement_scope: opts.statementScope,
        period_type: opts.periodType,
        period_end_date: opts.periodEndDate,
      });
    },
    enabled: Boolean(opts && (opts.enabled ?? true) && ticker),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useScreener(
  params: ScreenerQueryParams,
  snapshotId?: string | null,
) {
  return useQuery({
    queryKey: ["screener", snapshotId ?? "latest", params],
    queryFn: () => apiClient.getScreener(params),
    placeholderData: keepPreviousData,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useSectorRiskSummary(
  opts: {
    statementScope: StatementScope;
    periodType: PeriodType;
    periodEndDate?: string | null;
  },
  snapshotId?: string | null,
) {
  return useQuery({
    queryKey: [
      "sector-risk-summary",
      snapshotId ?? "latest",
      opts.statementScope,
      opts.periodType,
      opts.periodEndDate ?? "latest",
    ],
    queryFn: () =>
      apiClient.getSectorRiskSummary({
        statement_scope: opts.statementScope,
        period_type: opts.periodType,
        period_end_date: opts.periodEndDate,
      }),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useMarketChanges(
  opts: {
    tickers?: readonly string[];
    statementScope: StatementScope;
    periodType: PeriodType;
    limit?: number;
    enabled?: boolean;
  },
  snapshotId?: string | null,
) {
  return useQuery({
    queryKey: [
      "market-changes",
      snapshotId ?? "latest",
      opts.tickers?.join(",") ?? "ALL",
      opts.statementScope,
      opts.periodType,
      opts.limit ?? 30,
    ],
    queryFn: () =>
      apiClient.getMarketChanges({
        tickers: opts.tickers,
        statement_scope: opts.statementScope,
        period_type: opts.periodType,
        limit: opts.limit ?? 30,
      }),
    enabled: opts.enabled ?? true,
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useCompareCompanies(
  tickers: readonly string[],
  statementScope: StatementScope,
  periodType: PeriodType,
  periodEndDate: string | null,
  snapshotId?: string | null,
) {
  const periodView: PeriodView = periodType === "ANNUAL" ? "Annual" : "TTM";
  const profiles = useQueries({
    queries: tickers.map((ticker) => ({
      queryKey: [
        "company-profile",
        snapshotId ?? "latest",
        ticker,
        statementScope,
        periodView,
      ],
      queryFn: () =>
        apiClient.getCompanyProfile(ticker, {
          statement_scope: statementScope,
          period_view: periodView,
        }),
      staleTime: SNAPSHOT_STALE_MS,
    })),
  });

  const summaries = useQueries({
    queries: tickers.map((ticker) => ({
      queryKey: [
        "company-benchmarks-summary",
        snapshotId ?? "latest",
        ticker,
        statementScope,
        periodType,
        periodEndDate ?? "latest",
        "core",
      ],
      queryFn: () =>
        apiClient.getCompanyBenchmarksSummary(ticker, {
          statement_scope: statementScope,
          period_type: periodType,
          period_end_date: periodEndDate,
        }),
      staleTime: SNAPSHOT_STALE_MS,
    })),
  });

  return { profiles, summaries };
}

export function useQualitySummary(limit = 50, offset = 0, snapshotId?: string | null) {
  return useQuery({
    queryKey: ["quality-summary", snapshotId ?? "latest", limit, offset],
    queryFn: () => apiClient.getQualitySummary(limit, offset),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useQualityQuarantine(limit = 50, offset = 0, snapshotId?: string | null) {
  return useQuery({
    queryKey: ["quality-quarantine", snapshotId ?? "latest", limit, offset],
    queryFn: () => apiClient.getQualityQuarantine(limit, offset),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useStressTestMutation() {
  return useMutation({
    mutationFn: (req: { payload: StressScenarioRequest; signal?: AbortSignal }) =>
      apiClient.postStressTest(req.payload, req.signal),
  });
}
