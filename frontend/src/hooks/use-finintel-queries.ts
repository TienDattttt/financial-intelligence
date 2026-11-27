"use client";

import {
  keepPreviousData,
  useQueries,
  useQuery,
} from "@tanstack/react-query";
import { ApiError, apiClient } from "@/lib/api-client";
import type {
  IssuerItem,
  PeriodType,
  PeriodView,
  RatioName,
  ScreenerQueryParams,
  StatementScope,
  StatementType,
} from "@/types/api";

const SNAPSHOT_STALE_MS = 1000 * 60 * 60; // Immutable snapshot data pinned by snapshotId
const HEALTH_STALE_MS = 5 * 60_000;

export function shouldRetryFinintelQuery(
  failureCount: number,
  error: unknown,
): boolean {
  if (
    error instanceof ApiError &&
    (error.status === 404 || error.status === 422)
  ) {
    return false;
  }
  return failureCount < 1;
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => apiClient.getHealth(signal),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: HEALTH_STALE_MS,
  });
}

export function useSnapshotOverview(snapshotId?: string | null) {
  return useQuery({
    queryKey: ["snapshot-overview", snapshotId ?? "latest"],
    queryFn: ({ signal }) => apiClient.getSnapshotOverview(signal),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useMetaPeriods(snapshotId?: string | null) {
  return useQuery({
    queryKey: ["meta-periods", snapshotId ?? "latest"],
    queryFn: ({ signal }) => apiClient.getMetaPeriods(signal),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useMetaDictionary(snapshotId?: string | null) {
  return useQuery({
    queryKey: ["meta-dictionary", snapshotId ?? "latest"],
    queryFn: ({ signal }) => apiClient.getMetaDictionary(signal),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useModelCard(
  snapshotId?: string | null,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ["model-card", snapshotId ?? "latest"],
    queryFn: ({ signal }) => apiClient.getModelCard(signal),
    enabled: options?.enabled ?? true,
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useAllIssuers(snapshotId?: string | null) {
  return useQuery({
    queryKey: ["all-issuers", snapshotId ?? "latest"],
    queryFn: async ({ signal }): Promise<IssuerItem[]> => {
      const first = await apiClient.getIssuersPage(200, 0, signal);
      if (first.total <= first.items.length) {
        return first.items;
      }
      const pagesNeeded = Math.ceil((first.total - first.items.length) / 200);
      const rest = await Promise.all(
        Array.from({ length: pagesNeeded }, (_, idx) =>
          apiClient.getIssuersPage(200, (idx + 1) * 200, signal),
        ),
      );
      return [...first.items, ...rest.flatMap((page) => page.items)];
    },
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
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
    queryFn: ({ signal }) =>
      apiClient.getCompanyProfile(
        cleanTicker,
        {
          statement_scope: statementScope,
          period_view: periodView,
        },
        signal,
      ),
    enabled: cleanTicker.length > 0,
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
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
    queryFn: ({ signal }) =>
      apiClient.getCompanyBenchmarks(
        cleanTicker,
        {
          metric_name: opts.metricName,
          statement_scope: opts.statementScope,
          period_type: opts.periodType,
          period_end_date: opts.periodEndDate,
          include_history: true,
          peer_limit: 200,
        },
        signal,
      ),
    enabled: (opts.enabled ?? true) && cleanTicker.length > 0,
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
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
    queryFn: ({ signal }) =>
      apiClient.getCompanyBenchmarksSummary(
        cleanTicker,
        {
          statement_scope: opts.statementScope,
          period_type: opts.periodType,
          period_end_date: opts.periodEndDate,
          metrics: opts.metrics,
        },
        signal,
      ),
    enabled: (opts.enabled ?? true) && cleanTicker.length > 0,
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
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
    queryFn: ({ signal }) =>
      apiClient.getCompanyStatements(
        cleanTicker,
        {
          statement_type: opts.statementType,
          statement_scope: opts.statementScope,
          period_type: opts.periodType,
          periods_limit: opts.periodsLimit ?? 8,
        },
        signal,
      ),
    enabled: (opts.enabled ?? true) && cleanTicker.length > 0,
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
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
    queryFn: ({ signal }) =>
      apiClient.getCompanyFilings(
        cleanTicker,
        {
          statement_scope: opts?.statementScope,
        },
        signal,
      ),
    enabled: (opts?.enabled ?? true) && cleanTicker.length > 0,
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useCompanyMarketOverlay(
  ticker: string,
  statementScope: StatementScope = "CONSOLIDATED",
  snapshotId?: string | null,
  asOf?: string | null,
) {
  const cleanTicker = ticker.trim().toUpperCase();
  return useQuery({
    queryKey: [
      "company-market-overlay",
      snapshotId ?? "latest",
      cleanTicker,
      statementScope,
      asOf ?? "latest",
    ],
    queryFn: ({ signal }) =>
      apiClient.getCompanyMarketOverlay(cleanTicker, statementScope, asOf, signal),
    enabled: cleanTicker.length > 0,
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useCompanyDocuments(
  ticker: string,
  options?: { enabled?: boolean },
) {
  const cleanTicker = ticker.trim().toUpperCase();
  return useQuery({
    queryKey: ["company-documents", cleanTicker],
    queryFn: ({ signal }) => apiClient.getCompanyDocuments(cleanTicker, signal),
    enabled: (options?.enabled ?? true) && cleanTicker.length > 0,
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 503) {
        return false;
      }
      return shouldRetryFinintelQuery(failureCount, error);
    },
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
    queryFn: ({ signal }) => {
      if (!opts) throw new Error("Missing lineage lookup parameters");
      return apiClient.getRatioLineage(
        ticker,
        {
          ratio_name: opts.ratioName,
          statement_scope: opts.statementScope,
          period_type: opts.periodType,
          period_end_date: opts.periodEndDate,
        },
        signal,
      );
    },
    enabled: Boolean(opts && (opts.enabled ?? true) && ticker),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useScreener(
  params: ScreenerQueryParams,
  snapshotId?: string | null,
) {
  return useQuery({
    queryKey: ["screener", snapshotId ?? "latest", params],
    queryFn: ({ signal }) => apiClient.getScreener(params, signal),
    placeholderData: keepPreviousData,
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
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
    queryFn: ({ signal }) =>
      apiClient.getSectorRiskSummary(
        {
          statement_scope: opts.statementScope,
          period_type: opts.periodType,
          period_end_date: opts.periodEndDate,
        },
        signal,
      ),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
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
    queryFn: ({ signal }) =>
      apiClient.getMarketChanges(
        {
          tickers: opts.tickers,
          statement_scope: opts.statementScope,
          period_type: opts.periodType,
          limit: opts.limit ?? 30,
        },
        signal,
      ),
    enabled: opts.enabled ?? true,
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
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
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        apiClient.getCompanyProfile(
          ticker,
          {
            statement_scope: statementScope,
            period_view: periodView,
          },
          signal,
        ),
      retry: (failureCount: number, error: Error) =>
        shouldRetryFinintelQuery(failureCount, error),
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
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        apiClient.getCompanyBenchmarksSummary(
          ticker,
          {
            statement_scope: statementScope,
            period_type: periodType,
            period_end_date: periodEndDate,
          },
          signal,
        ),
      retry: (failureCount: number, error: Error) =>
        shouldRetryFinintelQuery(failureCount, error),
      staleTime: SNAPSHOT_STALE_MS,
    })),
  });

  return { profiles, summaries };
}

export function useQualitySummary(limit = 50, offset = 0, snapshotId?: string | null) {
  return useQuery({
    queryKey: ["quality-summary", snapshotId ?? "latest", limit, offset],
    queryFn: ({ signal }) => apiClient.getQualitySummary(limit, offset, signal),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useQualityQuarantine(limit = 50, offset = 0, snapshotId?: string | null) {
  return useQuery({
    queryKey: ["quality-quarantine", snapshotId ?? "latest", limit, offset],
    queryFn: ({ signal }) => apiClient.getQualityQuarantine(limit, offset, signal),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useSuggestedPeers(
  ticker: string,
  opts: {
    statement_scope?: StatementScope;
    period_type?: PeriodType;
    period_end_date?: string | null;
    limit?: number;
    enabled?: boolean;
  } = {},
  snapshotId?: string | null,
) {
  const cleanTicker = ticker.trim().toUpperCase();
  return useQuery({
    queryKey: [
      "suggested-peers",
      snapshotId ?? "latest",
      cleanTicker,
      opts.statement_scope ?? "CONSOLIDATED",
      opts.period_type ?? "QUARTER",
      opts.period_end_date ?? "latest",
      opts.limit ?? 4,
    ],
    queryFn: ({ signal }) =>
      apiClient.getSuggestedPeers(cleanTicker, opts, signal),
    enabled: (opts.enabled ?? true) && Boolean(cleanTicker),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}

export function useAnomalyDrivers(
  ticker: string,
  opts: {
    statement_scope?: StatementScope;
    period_type?: PeriodType;
    period_end_date?: string | null;
    top_k?: number;
    enabled?: boolean;
  } = {},
  snapshotId?: string | null,
) {
  const cleanTicker = ticker.trim().toUpperCase();
  return useQuery({
    queryKey: [
      "anomaly-drivers",
      snapshotId ?? "latest",
      cleanTicker,
      opts.statement_scope ?? "CONSOLIDATED",
      opts.period_type ?? "QUARTER",
      opts.period_end_date ?? "latest",
      opts.top_k ?? 3,
    ],
    queryFn: ({ signal }) =>
      apiClient.getAnomalyDrivers(cleanTicker, opts, signal),
    enabled: (opts.enabled ?? true) && Boolean(cleanTicker),
    retry: (failureCount, error) => shouldRetryFinintelQuery(failureCount, error),
    staleTime: SNAPSHOT_STALE_MS,
  });
}
