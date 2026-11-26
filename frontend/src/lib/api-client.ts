import type { CompanyMarketOverlayResponse } from "@/lib/market-overlay-types";
import type {
  AuditQuarantinePageResponse,
  CompanyBenchmarksResponse,
  CompanyBenchmarksSummaryResponse,
  CompanyFilingsResponse,
  CompanyProfileResponse,
  CompanyStatementsResponse,
  DocumentListResponse,
  HealthResponse,
  InvestigateRequest,
  InvestigateResponse,
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

export interface DataFreshnessItem {
  bctc_snapshot_id?: string | null;
  bctc_published_at?: string | null;
  bctc_latest_period_end_date?: string | null;
  market_prices_latest_trade_date?: string | null;
  market_prices_updated_at?: string | null;
  regulatory_latest_event_date?: string | null;
  regulatory_updated_at?: string | null;
  curated_pdfs_count?: number;
  curated_pdfs_updated_at?: string | null;
  extension_ports_digest: string;
}

export type MetaPeriodsWithFreshness = MetaPeriodsResponse & {
  freshness?: DataFreshnessItem | null;
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface SnapshotSubscriberRecord {
  callback: (snapshotId: string) => void;
  debounceMs: number;
  lastDelivered: string | null;
  pending: string | null;
  timer: ReturnType<typeof setTimeout> | null;
}

const snapshotSubscribers = new Set<SnapshotSubscriberRecord>();

export function subscribeObservedSnapshotId(
  callback: (snapshotId: string) => void,
  debounceMs = 150,
): () => void {
  const record: SnapshotSubscriberRecord = {
    callback,
    debounceMs: Math.max(0, debounceMs),
    lastDelivered: null,
    pending: null,
    timer: null,
  };
  snapshotSubscribers.add(record);
  return () => {
    if (record.timer !== null) {
      clearTimeout(record.timer);
      record.timer = null;
    }
    snapshotSubscribers.delete(record);
  };
}

export function resetObservedSnapshotRegistryForTests(): void {
  for (const record of snapshotSubscribers) {
    if (record.timer !== null) {
      clearTimeout(record.timer);
      record.timer = null;
    }
  }
  snapshotSubscribers.clear();
}

export function observeResponseSnapshotHeader(res: Response): void {
  const raw = res.headers.get("X-Snapshot-ID");
  if (!raw) return;
  const cleaned = raw.trim();
  if (!cleaned) return;
  for (const record of snapshotSubscribers) {
    if (cleaned === record.lastDelivered && record.timer === null) {
      continue;
    }
    record.pending = cleaned;
    if (record.timer !== null) {
      clearTimeout(record.timer);
    }
    record.timer = setTimeout(() => {
      record.timer = null;
      const next = record.pending;
      if (next && next !== record.lastDelivered) {
        record.lastDelivered = next;
        record.callback(next);
      }
    }, record.debounceMs);
  }
}

const INTERNAL_LEAK_RE =
  /([A-Za-z]:\\|\/(?:src|data|Users)\/|\.(?:parquet|sqlite|ducklake|py)\b|Traceback|\b(?:postgres|postgresql|redis|valkey):\/\/|\b(?:REGIME_FALLBACK|audit_quarantine|quality_summary|issuer_id)\b|\b(?:BS|IS|CF)_[A-Z0-9_]+\b|\bRF_(?:0[1-9]|10)(?:_[A-Z0-9_]+)?\b|\bducklake_[A-Za-z0-9_]*\b|\bfinintel_[A-Za-z0-9_]*\b)/i;

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
    if (status === 503) return "Dịch vụ tra cứu hiện tạm thời chưa sẵn sàng, vui lòng thử lại sau giây lát.";
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
  observeResponseSnapshotHeader(res);

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
  getHealth: (signal?: AbortSignal) =>
    requestJson<HealthResponse>("/api/v1/health", { signal }),

  getSnapshotOverview: (signal?: AbortSignal) =>
    requestJson<SnapshotOverviewResponse>("/api/v1/snapshot/overview", { signal }),

  getIssuersPage: (limit = 200, offset = 0, signal?: AbortSignal) =>
    requestJson<IssuersPageResponse>(
      buildUrl("/api/v1/issuers", { limit, offset }),
      { signal },
    ),

  getMetaPeriods: (signal?: AbortSignal) =>
    requestJson<MetaPeriodsWithFreshness>("/api/v2/meta/periods", { signal }),

  getMetaDictionary: (signal?: AbortSignal) =>
    requestJson<MetaDictionaryResponse>("/api/v2/meta/dictionary", { signal }),

  getModelCard: async (signal?: AbortSignal): Promise<ModelCardResponse | null> => {
    try {
      return await requestJson<ModelCardResponse>("/api/v2/meta/model-card", {
        signal,
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        return null;
      }
      throw err;
    }
  },

  getCompanyProfile: (
    ticker: string,
    opts?: { statement_scope?: StatementScope; period_view?: PeriodView },
    signal?: AbortSignal,
  ) =>
    requestJson<CompanyProfileResponse>(
      buildUrl(`/api/v1/companies/${encodeURIComponent(ticker.toUpperCase())}/profile`, {
        statement_scope: opts?.statement_scope,
        period_view: opts?.period_view,
      }),
      { signal },
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
    signal?: AbortSignal,
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
      { signal },
    ),

  getCompanyBenchmarksSummary: (
    ticker: string,
    opts?: {
      statement_scope?: StatementScope;
      period_type?: PeriodType;
      period_end_date?: string | null;
      metrics?: readonly RatioName[];
    },
    signal?: AbortSignal,
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
      { signal },
    ),

  getCompanyStatements: (
    ticker: string,
    opts?: {
      statement_type?: StatementType;
      statement_scope?: StatementScope;
      period_type?: PeriodType;
      periods_limit?: number;
    },
    signal?: AbortSignal,
  ) =>
    requestJson<CompanyStatementsResponse>(
      buildUrl(`/api/v2/companies/${encodeURIComponent(ticker.toUpperCase())}/statements`, {
        statement_type: opts?.statement_type,
        statement_scope: opts?.statement_scope,
        period_type: opts?.period_type,
        periods_limit: opts?.periods_limit ?? 8,
      }),
      { signal },
    ),

  getCompanyFilings: (
    ticker: string,
    opts?: { statement_scope?: StatementScope; limit?: number },
    signal?: AbortSignal,
  ) =>
    requestJson<CompanyFilingsResponse>(
      buildUrl(`/api/v2/companies/${encodeURIComponent(ticker.toUpperCase())}/filings`, {
        statement_scope: opts?.statement_scope,
        limit: opts?.limit ?? 100,
      }),
      { signal },
    ),

  getCompanyMarketOverlay: (
    ticker: string,
    statementScope?: StatementScope,
    asOf?: string | null,
    signal?: AbortSignal,
  ) =>
    requestJson<CompanyMarketOverlayResponse>(
      buildUrl(`/api/v2/companies/${encodeURIComponent(ticker.toUpperCase())}/market-overlay`, {
        statement_scope: statementScope,
        as_of: asOf,
      }),
      { signal },
    ),

  getCompanyDocuments: (ticker: string, signal?: AbortSignal) =>
    requestJson<DocumentListResponse>(
      `/api/v2/documents/${encodeURIComponent(ticker.trim().toUpperCase())}/list`,
      { signal },
    ),

  postCompanyInvestigate: async (
    ticker: string,
    payload: InvestigateRequest,
    signal?: AbortSignal,
  ): Promise<InvestigateResponse> => {
    const res = await fetch(
      `/api/v2/documents/${encodeURIComponent(ticker.trim().toUpperCase())}/investigate`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
        signal,
      },
    );
    observeResponseSnapshotHeader(res);

    if (!res.ok) {
      let detail = "";
      try {
        const errBody = (await res.json()) as { detail?: unknown };
        if (typeof errBody.detail === "string") {
          detail = errBody.detail;
        }
      } catch {
        detail = "";
      }
      throw new ApiError(res.status, sanitizeClientMessage(detail, res.status));
    }

    return (await res.json()) as InvestigateResponse;
  },

  getRatioLineage: (
    ticker: string,
    opts: {
      ratio_name: RatioName;
      statement_scope?: StatementScope;
      period_type?: PeriodType;
      period_end_date?: string | null;
    },
    signal?: AbortSignal,
  ) =>
    requestJson<RatioLineageResponse>(
      buildUrl(`/api/v1/companies/${encodeURIComponent(ticker.toUpperCase())}/lineage`, {
        ratio_name: opts.ratio_name,
        statement_scope: opts.statement_scope,
        period_type: opts.period_type,
        period_end_date: opts.period_end_date,
      }),
      { signal },
    ),

  getScreener: (params: ScreenerQueryParams, signal?: AbortSignal) =>
    requestJson<ScreenerResponse>(
      buildUrl("/api/v1/screener", params as Record<string, string | number | undefined | null>),
      { signal },
    ),

  getScreenerExportUrl: (params: Omit<ScreenerQueryParams, "limit" | "offset">) => {
    const exportParams = { ...(params as Record<string, string | number | undefined | null>) };
    delete exportParams.limit;
    delete exportParams.offset;
    return buildUrl("/api/v1/screener/export", exportParams);
  },

  getSectorRiskSummary: (
    opts?: {
      statement_scope?: StatementScope;
      period_type?: PeriodType;
      period_end_date?: string | null;
    },
    signal?: AbortSignal,
  ) =>
    requestJson<SectorRiskSummaryResponse>(
      buildUrl("/api/v2/sectors/risk-summary", {
        statement_scope: opts?.statement_scope,
        period_type: opts?.period_type,
        period_end_date: opts?.period_end_date,
      }),
      { signal },
    ),

  getMarketChanges: (
    opts?: {
      tickers?: readonly string[];
      statement_scope?: StatementScope;
      period_type?: PeriodType;
      limit?: number;
    },
    signal?: AbortSignal,
  ) =>
    requestJson<MarketChangesResponse>(
      buildUrl("/api/v2/market/changes", {
        tickers: opts?.tickers && opts.tickers.length > 0 ? opts.tickers.join(",") : undefined,
        statement_scope: opts?.statement_scope,
        period_type: opts?.period_type,
        limit: opts?.limit ?? 30,
      }),
      { signal },
    ),

  getQualitySummary: (limit = 50, offset = 0, signal?: AbortSignal) =>
    requestJson<QualitySummaryPageResponse>(
      buildUrl("/api/v1/quality/summary", { limit, offset }),
      { signal },
    ),

  getQualityQuarantine: (limit = 50, offset = 0, signal?: AbortSignal) =>
    requestJson<AuditQuarantinePageResponse>(
      buildUrl("/api/v1/quality/quarantine", { limit, offset }),
      { signal },
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
    observeResponseSnapshotHeader(res);
    if (res.status === 503 && !signal?.aborted) {
      const retryAfterSec = Number(res.headers.get("Retry-After") ?? "1");
      const delayMs = Math.min(2000, Math.max(300, (Number.isFinite(retryAfterSec) ? retryAfterSec : 1) * 1000));
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      if (!signal?.aborted) {
        res = await attempt();
        observeResponseSnapshotHeader(res);
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
