import type {
  StressIssuerItem,
  StressSectorSummaryItem,
  StressTestResponse,
} from "@/types/api";

export type StressScope = "MARKET" | "COMPANY" | "WATCHLIST";

export interface ShockUiState {
  scenarioName: string;
  revPct: number; // [-80..50]
  gmPp: number; // [-50..20]
  rateBps: number; // [-500..1500]
  dsoDays: number; // [-90..180]
  dioDays: number; // [-90..180]
  fxPct: number; // [-30..50]
}

export interface StressUrlState {
  scope: StressScope;
  companyTicker: string | null;
  watchlistTickers: string[];
  shocks: ShockUiState;
}

export const SHOCK_BOUNDS = {
  revPct: { min: -80, max: 50, step: 1, defaultValue: 0 },
  gmPp: { min: -50, max: 20, step: 0.5, defaultValue: 0 },
  rateBps: { min: -500, max: 1500, step: 25, defaultValue: 0 },
  dsoDays: { min: -90, max: 180, step: 5, defaultValue: 0 },
  dioDays: { min: -90, max: 180, step: 5, defaultValue: 0 },
  fxPct: { min: -30, max: 50, step: 1, defaultValue: 0 },
} as const;

export const DEFAULT_SHOCKS: ShockUiState = {
  scenarioName: "Cơ sở (Không cú sốc)",
  revPct: 0,
  gmPp: 0,
  rateBps: 0,
  dsoDays: 0,
  dioDays: 0,
  fxPct: 0,
};

export const STRESS_PRESETS: { label: string; values: ShockUiState }[] = [
  { label: "Cơ sở (0)", values: DEFAULT_SHOCKS },
  {
    label: "Tăng lãi suất (+200 bps)",
    values: {
      scenarioName: "Tăng lãi suất +200 bps",
      revPct: 0,
      gmPp: 0,
      rateBps: 200,
      dsoDays: 0,
      dioDays: 0,
      fxPct: 0,
    },
  },
  {
    label: "Suy giảm cầu (DT −15%, biên −2 điểm %)",
    values: {
      scenarioName: "Suy giảm cầu",
      revPct: -15,
      gmPp: -2,
      rateBps: 0,
      dsoDays: 0,
      dioDays: 0,
      fxPct: 0,
    },
  },
  {
    label: "Khủng hoảng kép (DT −25%, LS +300 bps, DSO +30n)",
    values: {
      scenarioName: "Khủng hoảng kép",
      revPct: -25,
      gmPp: -3,
      rateBps: 300,
      dsoDays: 30,
      dioDays: 15,
      fxPct: 0,
    },
  },
];

const TICKER_RE = /^[A-Z0-9]{1,10}$/;
const MAX_URL_TICKERS = 50;

export function clampShockValue(
  raw: unknown,
  min: number,
  max: number,
  fallback = 0,
): number {
  const safeFallback = Number.isFinite(fallback)
    ? Math.max(min, Math.min(max, fallback))
    : Math.max(min, Math.min(max, 0));

  if (raw === null || raw === undefined) {
    return Object.is(safeFallback, -0) ? 0 : safeFallback;
  }
  if (typeof raw === "string" && raw.trim() === "") {
    return Object.is(safeFallback, -0) ? 0 : safeFallback;
  }

  const parsed = typeof raw === "number" ? raw : Number(String(raw).trim());
  if (!Number.isFinite(parsed)) {
    return Object.is(safeFallback, -0) ? 0 : safeFallback;
  }

  const clamped = Math.max(min, Math.min(max, parsed));
  return Object.is(clamped, -0) ? 0 : clamped;
}

export function normalizeTicker(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const clean = raw.trim().toUpperCase();
  return TICKER_RE.test(clean) ? clean : null;
}

export function normalizeTickerList(
  rawList: readonly unknown[],
  maxCount = MAX_URL_TICKERS,
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of rawList) {
    const clean = normalizeTicker(raw);
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    out.push(clean);
    if (out.length >= maxCount) break;
  }
  return out;
}

export function inferScenarioName(shocks: Omit<ShockUiState, "scenarioName">): string {
  const match = STRESS_PRESETS.find(
    (p) =>
      p.values.revPct === shocks.revPct &&
      p.values.gmPp === shocks.gmPp &&
      p.values.rateBps === shocks.rateBps &&
      p.values.dsoDays === shocks.dsoDays &&
      p.values.dioDays === shocks.dioDays &&
      p.values.fxPct === shocks.fxPct,
  );
  return match ? match.values.scenarioName : "Tùy chỉnh";
}

export interface SearchParamsLike {
  get(name: string): string | null;
}

export function parseStressUrlParams(
  searchParams: SearchParamsLike,
  fallbackShocks: ShockUiState = STRESS_PRESETS[1].values,
): { hasStressParams: boolean; state: StressUrlState } {
  const keys = ["scope", "tickers", "ticker", "rev", "gm", "rate", "dso", "dio", "fx", "name"];
  const hasStressParams = keys.some((k) => searchParams.get(k) !== null);

  if (!hasStressParams) {
    return {
      hasStressParams: false,
      state: {
        scope: "MARKET",
        companyTicker: null,
        watchlistTickers: [],
        shocks: { ...fallbackShocks },
      },
    };
  }

  const rawScope = searchParams.get("scope")?.trim().toUpperCase();
  const scope: StressScope =
    rawScope === "COMPANY" || rawScope === "WATCHLIST" ? rawScope : "MARKET";

  const rawTickersParam = searchParams.get("tickers") ?? searchParams.get("ticker") ?? "";
  const parsedTickers = normalizeTickerList(rawTickersParam.split(","));

  const revPct = clampShockValue(
    searchParams.get("rev"),
    SHOCK_BOUNDS.revPct.min,
    SHOCK_BOUNDS.revPct.max,
    0,
  );
  const gmPp = clampShockValue(
    searchParams.get("gm"),
    SHOCK_BOUNDS.gmPp.min,
    SHOCK_BOUNDS.gmPp.max,
    0,
  );
  const rateBps = clampShockValue(
    searchParams.get("rate"),
    SHOCK_BOUNDS.rateBps.min,
    SHOCK_BOUNDS.rateBps.max,
    0,
  );
  const dsoDays = clampShockValue(
    searchParams.get("dso"),
    SHOCK_BOUNDS.dsoDays.min,
    SHOCK_BOUNDS.dsoDays.max,
    0,
  );
  const dioDays = clampShockValue(
    searchParams.get("dio"),
    SHOCK_BOUNDS.dioDays.min,
    SHOCK_BOUNDS.dioDays.max,
    0,
  );
  const fxPct = clampShockValue(
    searchParams.get("fx"),
    SHOCK_BOUNDS.fxPct.min,
    SHOCK_BOUNDS.fxPct.max,
    0,
  );

  const rawName = searchParams.get("name")?.trim().slice(0, 60);
  const scenarioName =
    rawName && rawName.length > 0
      ? rawName
      : inferScenarioName({ revPct, gmPp, rateBps, dsoDays, dioDays, fxPct });

  return {
    hasStressParams: true,
    state: {
      scope,
      companyTicker: scope === "COMPANY" ? (parsedTickers[0] ?? null) : null,
      watchlistTickers: scope === "WATCHLIST" ? parsedTickers : [],
      shocks: {
        scenarioName,
        revPct,
        gmPp,
        rateBps,
        dsoDays,
        dioDays,
        fxPct,
      },
    },
  };
}

export function serializeStressUrlParams(state: StressUrlState): URLSearchParams {
  const params = new URLSearchParams();
  params.set("scope", state.scope);

  if (state.scope === "COMPANY") {
    const clean = normalizeTicker(state.companyTicker);
    if (clean) params.set("tickers", clean);
  } else if (state.scope === "WATCHLIST") {
    const cleanList = normalizeTickerList(state.watchlistTickers);
    if (cleanList.length > 0) params.set("tickers", cleanList.join(","));
  }

  params.set(
    "rev",
    String(
      clampShockValue(
        state.shocks.revPct,
        SHOCK_BOUNDS.revPct.min,
        SHOCK_BOUNDS.revPct.max,
        0,
      ),
    ),
  );
  params.set(
    "gm",
    String(
      clampShockValue(
        state.shocks.gmPp,
        SHOCK_BOUNDS.gmPp.min,
        SHOCK_BOUNDS.gmPp.max,
        0,
      ),
    ),
  );
  params.set(
    "rate",
    String(
      clampShockValue(
        state.shocks.rateBps,
        SHOCK_BOUNDS.rateBps.min,
        SHOCK_BOUNDS.rateBps.max,
        0,
      ),
    ),
  );
  params.set(
    "dso",
    String(
      clampShockValue(
        state.shocks.dsoDays,
        SHOCK_BOUNDS.dsoDays.min,
        SHOCK_BOUNDS.dsoDays.max,
        0,
      ),
    ),
  );
  params.set(
    "dio",
    String(
      clampShockValue(
        state.shocks.dioDays,
        SHOCK_BOUNDS.dioDays.min,
        SHOCK_BOUNDS.dioDays.max,
        0,
      ),
    ),
  );
  params.set(
    "fx",
    String(
      clampShockValue(
        state.shocks.fxPct,
        SHOCK_BOUNDS.fxPct.min,
        SHOCK_BOUNDS.fxPct.max,
        0,
      ),
    ),
  );

  const trimmedName = state.shocks.scenarioName.trim().slice(0, 60);
  if (trimmedName) {
    params.set("name", trimmedName);
  }

  return params;
}

export interface PortfolioStressMetrics {
  requestedCount: number;
  evaluatedCount: number;
  missingCount: number;
  totalDebtVnd: number;
  baselineDebtAtRiskVnd: number;
  stressedDebtAtRiskVnd: number;
  deltaDebtAtRiskVnd: number;
  baselineDebtAtRiskPct: number;
  stressedDebtAtRiskPct: number;
  deltaDebtAtRiskPct: number;
  newlyDistressedCount: number;
}

export function computePortfolioStressMetrics(
  result: StressTestResponse,
  requestedTickers: readonly string[],
): PortfolioStressMetrics {
  const cleanRequested = normalizeTickerList(requestedTickers);
  const totalDebtVnd = Number.isFinite(result.total_debt_sum) ? result.total_debt_sum : 0;
  const baselinePct = Number.isFinite(result.baseline_debt_at_risk_pct)
    ? result.baseline_debt_at_risk_pct
    : 0;
  const stressedPct = Number.isFinite(result.stressed_debt_at_risk_pct)
    ? result.stressed_debt_at_risk_pct
    : 0;

  const baselineDebtAtRiskVnd = totalDebtVnd * baselinePct;
  const stressedDebtAtRiskVnd = totalDebtVnd * stressedPct;
  const evaluatedCount = Math.max(0, result.total_issuers_evaluated);
  const missingCount = Math.max(0, cleanRequested.length - evaluatedCount);

  return {
    requestedCount: cleanRequested.length,
    evaluatedCount,
    missingCount,
    totalDebtVnd,
    baselineDebtAtRiskVnd,
    stressedDebtAtRiskVnd,
    deltaDebtAtRiskVnd: stressedDebtAtRiskVnd - baselineDebtAtRiskVnd,
    baselineDebtAtRiskPct: baselinePct,
    stressedDebtAtRiskPct: stressedPct,
    deltaDebtAtRiskPct: stressedPct - baselinePct,
    newlyDistressedCount: Math.max(0, result.newly_distressed_count),
  };
}

export interface SectorComparisonDeltaRow {
  icbCodeLevel2: string;
  icbNameLevel2: string;
  issuerCountA: number;
  issuerCountB: number;
  totalDebtA: number;
  totalDebtB: number;
  debtAtRiskPctA: number;
  debtAtRiskPctB: number;
  debtAtRiskPctDelta: number;
  debtAtRiskVndA: number;
  debtAtRiskVndB: number;
  debtAtRiskVndDelta: number;
  newlyDistressedA: number;
  newlyDistressedB: number;
  newlyDistressedDelta: number;
}

export type IssuerComparisonStatus = "ADDED_IN_B" | "REMOVED_IN_B" | "BOTH";

export interface IssuerComparisonDeltaRow {
  ticker: string;
  companyNameVn: string;
  icbNameLevel2: string;
  status: IssuerComparisonStatus;
  totalDebt: number | null;
  icrBaseline: number | null;
  icrA: number | null;
  icrB: number | null;
}

export interface ScenarioComparisonSummary {
  scenarioAName: string;
  scenarioBName: string;
  scopeMismatch: boolean;
  totalDebtA: number;
  totalDebtB: number;
  debtAtRiskPctA: number;
  debtAtRiskPctB: number;
  debtAtRiskPctDelta: number;
  debtAtRiskVndA: number;
  debtAtRiskVndB: number;
  debtAtRiskVndDelta: number;
  newlyDistressedCountA: number;
  newlyDistressedCountB: number;
  newlyDistressedCountDelta: number;
  addedDistressedCount: number;
  resolvedDistressedCount: number;
  sectorDeltas: SectorComparisonDeltaRow[];
  issuerDeltas: IssuerComparisonDeltaRow[];
}

export function computeScenarioComparison(
  scenarioA: StressTestResponse,
  scenarioB: StressTestResponse,
): ScenarioComparisonSummary {
  const debtAtRiskVndA = scenarioA.total_debt_sum * scenarioA.stressed_debt_at_risk_pct;
  const debtAtRiskVndB = scenarioB.total_debt_sum * scenarioB.stressed_debt_at_risk_pct;

  const sectorMapA = new Map<string, StressSectorSummaryItem>(
    scenarioA.sector_summary.map((s) => [s.icb_code_level2, s]),
  );
  const sectorMapB = new Map<string, StressSectorSummaryItem>(
    scenarioB.sector_summary.map((s) => [s.icb_code_level2, s]),
  );
  const allSectorCodes = Array.from(
    new Set([...sectorMapA.keys(), ...sectorMapB.keys()]),
  );

  const sectorDeltas: SectorComparisonDeltaRow[] = allSectorCodes
    .map((code) => {
      const a = sectorMapA.get(code);
      const b = sectorMapB.get(code);
      const totalDebtA = a?.total_debt_sum ?? 0;
      const totalDebtB = b?.total_debt_sum ?? 0;
      const pctA = a?.stressed_debt_at_risk_pct ?? 0;
      const pctB = b?.stressed_debt_at_risk_pct ?? 0;
      const vndA = totalDebtA * pctA;
      const vndB = totalDebtB * pctB;
      const newlyA = a?.newly_distressed_count ?? 0;
      const newlyB = b?.newly_distressed_count ?? 0;

      return {
        icbCodeLevel2: code,
        icbNameLevel2: b?.icb_name_level2 ?? a?.icb_name_level2 ?? code,
        issuerCountA: a?.issuer_count ?? 0,
        issuerCountB: b?.issuer_count ?? 0,
        totalDebtA,
        totalDebtB,
        debtAtRiskPctA: pctA,
        debtAtRiskPctB: pctB,
        debtAtRiskPctDelta: pctB - pctA,
        debtAtRiskVndA: vndA,
        debtAtRiskVndB: vndB,
        debtAtRiskVndDelta: vndB - vndA,
        newlyDistressedA: newlyA,
        newlyDistressedB: newlyB,
        newlyDistressedDelta: newlyB - newlyA,
      };
    })
    .sort((x, y) => {
      const diff = Math.abs(y.debtAtRiskPctDelta) - Math.abs(x.debtAtRiskPctDelta);
      if (Math.abs(diff) > 1e-9) return diff;
      return y.debtAtRiskPctB - x.debtAtRiskPctB;
    });

  const allLookupA = new Map<string, StressIssuerItem>();
  for (const item of scenarioA.top_stressed_issuers) {
    allLookupA.set(item.ticker, item);
  }
  for (const item of scenarioA.newly_distressed_issuers) {
    allLookupA.set(item.ticker, item);
  }

  const allLookupB = new Map<string, StressIssuerItem>();
  for (const item of scenarioB.top_stressed_issuers) {
    allLookupB.set(item.ticker, item);
  }
  for (const item of scenarioB.newly_distressed_issuers) {
    allLookupB.set(item.ticker, item);
  }

  const newlySetA = new Set(scenarioA.newly_distressed_issuers.map((it) => it.ticker));
  const newlySetB = new Set(scenarioB.newly_distressed_issuers.map((it) => it.ticker));
  const unionNewly = Array.from(new Set([...newlySetA, ...newlySetB]));

  let addedDistressedCount = 0;
  let resolvedDistressedCount = 0;

  const statusOrder: Record<IssuerComparisonStatus, number> = {
    ADDED_IN_B: 0,
    REMOVED_IN_B: 1,
    BOTH: 2,
  };

  const issuerDeltas: IssuerComparisonDeltaRow[] = unionNewly
    .map((ticker) => {
      const inA = newlySetA.has(ticker);
      const inB = newlySetB.has(ticker);
      let status: IssuerComparisonStatus = "BOTH";
      if (inB && !inA) {
        status = "ADDED_IN_B";
        addedDistressedCount += 1;
      } else if (inA && !inB) {
        status = "REMOVED_IN_B";
        resolvedDistressedCount += 1;
      }

      const itemA = allLookupA.get(ticker);
      const itemB = allLookupB.get(ticker);

      return {
        ticker,
        companyNameVn: itemB?.company_name_vn ?? itemA?.company_name_vn ?? ticker,
        icbNameLevel2: itemB?.icb_name_level2 ?? itemA?.icb_name_level2 ?? "—",
        status,
        totalDebt: itemB?.total_debt ?? itemA?.total_debt ?? null,
        icrBaseline: itemB?.interest_coverage ?? itemA?.interest_coverage ?? null,
        icrA:
          scenarioA.issuer_icrs &&
          Object.prototype.hasOwnProperty.call(scenarioA.issuer_icrs, ticker)
            ? (scenarioA.issuer_icrs[ticker] ?? null)
            : (itemA?.stressed_interest_coverage ?? itemB?.scenario_a_icr ?? null),
        icrB:
          itemB?.stressed_interest_coverage ??
          (scenarioB.issuer_icrs &&
          Object.prototype.hasOwnProperty.call(scenarioB.issuer_icrs, ticker)
            ? (scenarioB.issuer_icrs[ticker] ?? null)
            : null),
      };
    })
    .sort((x, y) => {
      const orderDiff = statusOrder[x.status] - statusOrder[y.status];
      if (orderDiff !== 0) return orderDiff;
      return (y.totalDebt ?? 0) - (x.totalDebt ?? 0);
    });

  const scopeMismatch =
    scenarioA.statement_scope !== scenarioB.statement_scope ||
    scenarioA.period_type !== scenarioB.period_type ||
    (scenarioA.period_end_date ?? null) !== (scenarioB.period_end_date ?? null) ||
    scenarioA.total_issuers_evaluated !== scenarioB.total_issuers_evaluated;

  return {
    scenarioAName: scenarioA.scenario_name,
    scenarioBName: scenarioB.scenario_name,
    scopeMismatch,
    totalDebtA: scenarioA.total_debt_sum,
    totalDebtB: scenarioB.total_debt_sum,
    debtAtRiskPctA: scenarioA.stressed_debt_at_risk_pct,
    debtAtRiskPctB: scenarioB.stressed_debt_at_risk_pct,
    debtAtRiskPctDelta:
      scenarioB.stressed_debt_at_risk_pct - scenarioA.stressed_debt_at_risk_pct,
    debtAtRiskVndA,
    debtAtRiskVndB,
    debtAtRiskVndDelta: debtAtRiskVndB - debtAtRiskVndA,
    newlyDistressedCountA: scenarioA.newly_distressed_count,
    newlyDistressedCountB: scenarioB.newly_distressed_count,
    newlyDistressedCountDelta:
      scenarioB.newly_distressed_count - scenarioA.newly_distressed_count,
    addedDistressedCount,
    resolvedDistressedCount,
    sectorDeltas,
    issuerDeltas,
  };
}
