import type { CompanyMarketOverlayResponse } from "@/lib/market-overlay-types";
import type {
  AuditQuarantinePageResponse,
  CompanyBenchmarksResponse,
  CompanyBenchmarksSummaryResponse,
  CompanyFilingsResponse,
  CompanyProfileResponse,
  CompanyStatementsResponse,
  HealthResponse,
  IssuersPageResponse,
  MarketChangesResponse,
  MetaDictionaryResponse,
  MetaPeriodsResponse,
  ModelCardResponse,
  PeriodType,
  PeriodView,
  QualitySummaryPageResponse,
  RatioLineageResponse,
  RatioName,
  ScreenerQueryParams,
  ScreenerResponse,
  SectorRiskSummaryResponse,
  SnapshotOverviewResponse,
  StatementScope,
  StatementType,
  StressScenarioRequest,
  StressTestResponse,
} from "@/types/api";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const INTERNAL_LEAK_RE = /([A-Za-z]:\\|\/(?:src|data|Users)\/|\.parquet\b|\.sqlite\b|\.py\b|Traceback)/i;

function sanitizeClientMessage(raw: string, status: number): string {
  const cleaned = raw.replace(/\s+/g, " ").trim();
  if (/missing required stress-test intermediate columns/i.test(cleaned)) {
    return "Bản phát hành dữ liệu hiện tại là phiên bản cũ chưa bao gồm các cột trung gian phục vụ mô phỏng kịch bản sốc (cần bản phát hành V2).";
  }
  if (/not found/i.test(cleaned) || status === 404) {
    return "Không tìm thấy dữ liệu cho yêu cầu này trong kỳ hiện hành.";
  }
  if (!cleaned || INTERNAL_LEAK_RE.test(cleaned)) {
    if (status === 422) return "Tham số truy vấn không hợp lệ.";
    if (status === 503) return "Hệ thống đang xử lý mô phỏng, vui lòng thử lại sau giây lát.";
    return "Không thể tải dữ liệu từ máy chủ phân tích.";
  }
  return cleaned.slice(0, 200);
}

function buildUrl(
  path: string,
  params?: Record<string, string | number | boolean | null | undefined | readonly string[]>,
): string {
  if (!params) return path;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item) search.append(key, item);
      }
    } else {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `${path}?${qs}` : path;
}

async function requestJson<T>(
  input: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let detail = "";
    try {
      const payload = (await res.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        detail = payload.detail;
      }
    } catch {
      detail = "";
    }
    throw new ApiError(res.status, sanitizeClientMessage(detail, res.status));
  }

  return (await res.json()) as T;
}

export const apiClient = {
  getHealth: () => requestJson<HealthResponse>("/api/v1/health"),

  getSnapshotOverview: () =>
    requestJson<SnapshotOverviewResponse>("/api/v1/snapshot/overview"),

  getIssuersPage: (limit = 200, offset = 0) =>
    requestJson<IssuersPageResponse>(
      buildUrl("/api/v1/issuers", { limit, offset }),
    ),

  getMetaPeriods: () =>
    requestJson<MetaPeriodsResponse>("/api/v2/meta/periods"),

  getMetaDictionary: () =>
    requestJson<MetaDictionaryResponse>("/api/v2/meta/dictionary"),

  getModelCard: () =>
    requestJson<ModelCardResponse>("/api/v2/meta/model-card"),

  getCompanyProfile: (
    ticker: string,
    opts?: { statement_scope?: StatementScope; period_view?: PeriodView },
  ) =>
    requestJson<CompanyProfileResponse>(
      buildUrl(`/api/v1/companies/${encodeURIComponent(ticker.toUpperCase())}/profile`, {
        statement_scope: opts?.statement_scope,
        period_view: opts?.period_view,
      }),
    ),

  getCompanyBenchmarks: (
    ticker: string,
    opts?: {
      metric_name?: RatioName;
      statement_scope?: StatementScope;
      period_type?: PeriodType;
      period_end_date?: string | null;
      include_history?: boolean;
      peer_limit?: number;
    },
  ) =>
    requestJson<CompanyBenchmarksResponse>(
      buildUrl(`/api/v1/companies/${encodeURIComponent(ticker.toUpperCase())}/benchmarks`, {
        metric_name: opts?.metric_name,
        statement_scope: opts?.statement_scope,
        period_type: opts?.period_type,
        period_end_date: opts?.period_end_date,
        include_history: opts?.include_history ?? true,
        peer_limit: opts?.peer_limit ?? 200,
      }),
    ),

  getCompanyBenchmarksSummary: (
    ticker: string,
    opts?: {
      statement_scope?: StatementScope;
      period_type?: PeriodType;
      period_end_date?: string | null;
      metrics?: readonly RatioName[];
    },
  ) =>
    requestJson<CompanyBenchmarksSummaryResponse>(
      buildUrl(
        `/api/v2/companies/${encodeURIComponent(ticker.toUpperCase())}/benchmarks/summary`,
        {
          statement_scope: opts?.statement_scope,
          period_type: opts?.period_type,
          period_end_date: opts?.period_end_date,
          metrics: opts?.metrics,
        },
      ),
    ),

  getCompanyStatements: (
    ticker: string,
    opts?: {
      statement_type?: StatementType;
      statement_scope?: StatementScope;
      period_type?: PeriodType;
      periods_limit?: number;
    },
  ) =>
    requestJson<CompanyStatementsResponse>(
      buildUrl(`/api/v2/companies/${encodeURIComponent(ticker.toUpperCase())}/statements`, {
        statement_type: opts?.statement_type,
        statement_scope: opts?.statement_scope,
        period_type: opts?.period_type,
        periods_limit: opts?.periods_limit ?? 8,
      }),
    ),

  getCompanyFilings: (
    ticker: string,
    opts?: { statement_scope?: StatementScope; limit?: number },
  ) =>
    requestJson<CompanyFilingsResponse>(
      buildUrl(`/api/v2/companies/${encodeURIComponent(ticker.toUpperCase())}/filings`, {
        statement_scope: opts?.statement_scope,
        limit: opts?.limit ?? 100,
      }),
    ),

  getCompanyMarketOverlay: (
    ticker: string,
    statementScope?: StatementScope,
    asOf?: string | null,
  ) =>
    requestJson<CompanyMarketOverlayResponse>(
      buildUrl(`/api/v2/companies/${encodeURIComponent(ticker.toUpperCase())}/market-overlay`, {
        statement_scope: statementScope,
        as_of: asOf,
      }),
    ),

  getRatioLineage: (
    ticker: string,
    opts: {
      ratio_name: RatioName;
      statement_scope?: StatementScope;
      period_type?: PeriodType;
      period_end_date?: string | null;
    },
  ) =>
    requestJson<RatioLineageResponse>(
      buildUrl(`/api/v1/companies/${encodeURIComponent(ticker.toUpperCase())}/lineage`, {
        ratio_name: opts.ratio_name,
        statement_scope: opts.statement_scope,
        period_type: opts.period_type,
        period_end_date: opts.period_end_date,
      }),
    ),

  getScreener: (params: ScreenerQueryParams) =>
    requestJson<ScreenerResponse>(
      buildUrl("/api/v1/screener", params as Record<string, string | number | undefined | null>),
    ),

  getSectorRiskSummary: (opts?: {
    statement_scope?: StatementScope;
    period_type?: PeriodType;
    period_end_date?: string | null;
  }) =>
    requestJson<SectorRiskSummaryResponse>(
      buildUrl("/api/v2/sectors/risk-summary", {
        statement_scope: opts?.statement_scope,
        period_type: opts?.period_type,
        period_end_date: opts?.period_end_date,
      }),
    ),

  getMarketChanges: (opts?: {
    tickers?: readonly string[];
    statement_scope?: StatementScope;
    period_type?: PeriodType;
    limit?: number;
  }) =>
    requestJson<MarketChangesResponse>(
      buildUrl("/api/v2/market/changes", {
        tickers: opts?.tickers && opts.tickers.length > 0 ? opts.tickers.join(",") : undefined,
        statement_scope: opts?.statement_scope,
        period_type: opts?.period_type,
        limit: opts?.limit ?? 30,
      }),
    ),

  getQualitySummary: (limit = 50, offset = 0) =>
    requestJson<QualitySummaryPageResponse>(
      buildUrl("/api/v1/quality/summary", { limit, offset }),
    ),

  getQualityQuarantine: (limit = 50, offset = 0) =>
    requestJson<AuditQuarantinePageResponse>(
      buildUrl("/api/v1/quality/quarantine", { limit, offset }),
    ),

  postStressTest: async (
    payload: StressScenarioRequest & { tickers?: string[] | null },
    signal?: AbortSignal,
  ): Promise<StressTestResponse> => {
    const attempt = async () =>
      fetch("/api/v1/stress-test", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
        signal,
      });

    let res = await attempt();
    if (res.status === 503 && !signal?.aborted) {
      const retryAfterSec = Number(res.headers.get("Retry-After") ?? "1");
      const delayMs = Math.min(2000, Math.max(300, (Number.isFinite(retryAfterSec) ? retryAfterSec : 1) * 1000));
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      if (!signal?.aborted) {
        res = await attempt();
      }
    }

    if (!res.ok) {
      let detail = "";
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        detail = "";
      }
      throw new ApiError(res.status, sanitizeClientMessage(detail, res.status));
    }
    return (await res.json()) as StressTestResponse;
  },
};
