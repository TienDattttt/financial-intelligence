import { z } from "zod";
import type { RiskTone } from "@/lib/domain-labels";
import { formatNumber } from "@/lib/format";
import type {
  ExchangeCode,
  PeriodType,
  ScreenerItem,
  ScreenerQueryParams,
  ScreenerSortField,
  SortDirection,
  StatementScope,
} from "@/types/api";

const SAVED_SCREENS_KEY = "finintel:saved-screens:v1";
const MAX_SAVED_SCREENS = 30;

export const VALID_RISK_TIERS = ["R1", "R2", "R3", "R4", "R5"] as const;
export type ScreenerRiskTier = (typeof VALID_RISK_TIERS)[number];
const VALID_RISK_TIER_SET = new Set<string>(VALID_RISK_TIERS);

export const VALID_EXCHANGES = ["HOSE", "HNX", "UPCOM"] as const satisfies readonly ExchangeCode[];
const VALID_EXCHANGE_SET = new Set<string>(VALID_EXCHANGES);

export const VALID_ALTMAN_ZONES = [
  "SAFE_ZONE",
  "GREY_ZONE",
  "DISTRESS_ZONE",
] as const;

export const VALID_BENEISH_FLAGS = [
  "VALID",
  "HIGH_MANIPULATION_RISK_SCREEN",
  "MIXED_TAXONOMY",
  "MISSING_PRIOR_YEAR_BASE",
  "MISSING_DEPRECIATION_DIRECT_CF",
  "MISSING_OR_INVALID_INPUT",
] as const;

export const VALID_SORT_FIELDS = [
  "ticker",
  "calibrated_distress_prob",
  "altman_z_double_prime",
  "piotroski_f_score",
  "beneish_m8_score",
  "red_flag_count",
  "roe_total",
  "roe_parent",
  "roa",
  "eps_ttm_estimated",
  "ebit_margin",
  "liabilities_to_assets",
  "debt_to_equity",
  "interest_coverage",
  "cfo_ttm",
  "ocf_to_total_liabilities",
  "revenue_growth_yoy",
  "pe_ttm",
  "pb_ratio",
  "market_cap_vnd",
] as const satisfies readonly ScreenerSortField[];

const OPT_NUM_STR = z
  .string()
  .max(24)
  .regex(/^$|^-?\d+(\.\d{1,6})?$/)
  .default("");

export const ScreenerFilterStateSchema = z.object({
  q: z.string().max(80).default(""),
  exchange: z.enum(["", ...VALID_EXCHANGES]).optional(),
  icb_code_level2: z.string().max(20).default(""),
  altman_zone: z.enum(["", ...VALID_ALTMAN_ZONES]).default(""),
  risk_tier: z.enum(["", ...VALID_RISK_TIERS]).default(""),
  risk_tiers: z.array(z.enum(VALID_RISK_TIERS)).max(5).optional(),
  beneish_flag: z.enum(["", ...VALID_BENEISH_FLAGS]).default(""),
  min_f_score: z
    .string()
    .regex(/^$|^[0-9]$/)
    .default(""),
  min_distress_prob: z
    .string()
    .regex(/^$|^(0(\.\d{1,4})?|1(\.0{1,4})?)$/)
    .default(""),
  max_distress_prob: z
    .string()
    .regex(/^$|^(0(\.\d{1,4})?|1(\.0{1,4})?)$/)
    .default(""),
  min_red_flags: z
    .string()
    .regex(/^$|^([0-9]|10)$/)
    .default(""),
  max_red_flags: z
    .string()
    .regex(/^$|^([0-9]|10)$/)
    .default(""),
  cluster_id: z
    .string()
    .regex(/^$|^\d{1,2}$/)
    .default(""),
  anomaly_flag: z.enum(["", "true", "false"]).default(""),
  min_roe_parent: OPT_NUM_STR,
  max_roe_parent: OPT_NUM_STR,
  min_roa: OPT_NUM_STR,
  max_roa: OPT_NUM_STR,
  min_eps_ttm_estimated: OPT_NUM_STR,
  max_eps_ttm_estimated: OPT_NUM_STR,
  min_liabilities_to_assets: OPT_NUM_STR,
  max_liabilities_to_assets: OPT_NUM_STR,
  min_debt_to_equity: OPT_NUM_STR,
  max_debt_to_equity: OPT_NUM_STR,
  min_cfo_ttm: OPT_NUM_STR,
  max_cfo_ttm: OPT_NUM_STR,
  min_ocf_to_total_liabilities: OPT_NUM_STR,
  max_ocf_to_total_liabilities: OPT_NUM_STR,
  min_revenue_growth_yoy: OPT_NUM_STR,
  max_revenue_growth_yoy: OPT_NUM_STR,
  min_pe_ttm: OPT_NUM_STR,
  max_pe_ttm: OPT_NUM_STR,
  min_pb_ratio: OPT_NUM_STR,
  max_pb_ratio: OPT_NUM_STR,
  min_market_cap_bn_vnd: OPT_NUM_STR,
  max_market_cap_bn_vnd: OPT_NUM_STR,
  sort_by: z.enum(VALID_SORT_FIELDS).default("calibrated_distress_prob"),
  sort_dir: z.enum(["asc", "desc"] as const).default("desc"),
});

export type ScreenerFilterState = z.infer<typeof ScreenerFilterStateSchema>;

export const DEFAULT_SCREENER_FILTERS: ScreenerFilterState = {
  q: "",
  exchange: "",
  icb_code_level2: "",
  altman_zone: "",
  risk_tier: "",
  risk_tiers: [],
  beneish_flag: "",
  min_f_score: "",
  min_distress_prob: "",
  max_distress_prob: "",
  min_red_flags: "",
  max_red_flags: "",
  cluster_id: "",
  anomaly_flag: "",
  min_roe_parent: "",
  max_roe_parent: "",
  min_roa: "",
  max_roa: "",
  min_eps_ttm_estimated: "",
  max_eps_ttm_estimated: "",
  min_liabilities_to_assets: "",
  max_liabilities_to_assets: "",
  min_debt_to_equity: "",
  max_debt_to_equity: "",
  min_cfo_ttm: "",
  max_cfo_ttm: "",
  min_ocf_to_total_liabilities: "",
  max_ocf_to_total_liabilities: "",
  min_revenue_growth_yoy: "",
  max_revenue_growth_yoy: "",
  min_pe_ttm: "",
  max_pe_ttm: "",
  min_pb_ratio: "",
  max_pb_ratio: "",
  min_market_cap_bn_vnd: "",
  max_market_cap_bn_vnd: "",
  sort_by: "calibrated_distress_prob",
  sort_dir: "desc",
};

export const SavedScreenSchema = z.object({
  id: z.string().min(1).max(64),
  name: z.string().trim().min(1).max(60),
  createdAt: z.string().min(1).max(40),
  updatedAt: z.string().min(1).max(40),
  filters: ScreenerFilterStateSchema,
});

export type SavedScreen = z.infer<typeof SavedScreenSchema>;

export const SavedScreensListSchema = z
  .array(SavedScreenSchema)
  .max(MAX_SAVED_SCREENS);

export interface ScreenerPreset {
  id: string;
  name: string;
  description: string;
  filters: Partial<ScreenerFilterState>;
}

export const BUILTIN_SCREENER_PRESETS: readonly ScreenerPreset[] = [
  {
    id: "preset-high-risk-r4-r5",
    name: "Rủi ro cao R4–R5",
    description:
      "Nhóm doanh nghiệp xếp bậc rủi ro R4–R5 theo mô hình, xếp theo xác suất kiệt quệ giảm dần.",
    filters: {
      risk_tiers: ["R4", "R5"],
      sort_by: "calibrated_distress_prob",
      sort_dir: "desc",
    },
  },
  {
    id: "preset-cashflow-leverage",
    name: "Cảnh báo dòng tiền & đòn bẩy",
    description:
      "Doanh nghiệp phát sinh từ 2 cờ đỏ trở lên, sắp xếp theo đòn bẩy Nợ/VCSH từ cao xuống thấp.",
    filters: {
      min_red_flags: "2",
      sort_by: "debt_to_equity",
      sort_dir: "desc",
    },
  },
  {
    id: "preset-healthy-r1-r2",
    name: "Sức khỏe ổn định R1–R2",
    description:
      "Nhóm doanh nghiệp bậc R1–R2, vùng an toàn Altman, F-Score từ 6/9 và tối đa 1 cờ đỏ, xếp theo ROE.",
    filters: {
      risk_tiers: ["R1", "R2"],
      altman_zone: "SAFE_ZONE",
      min_f_score: "6",
      max_red_flags: "1",
      sort_by: "roe_total",
      sort_dir: "desc",
    },
  },
];

// ---------------------------------------------------------------------------
// Risk tier R1-R5, PIT percentile, and anomaly display helpers (UI-05a)
// ---------------------------------------------------------------------------

export interface ScreenerRiskTierMeta {
  tier: ScreenerRiskTier | "UNRATED";
  label: string;
  shortLabel: string;
  tone: RiskTone;
  description: string;
}

export const SCREENER_RISK_TIER_META: Record<
  ScreenerRiskTier,
  ScreenerRiskTierMeta
> = {
  R1: {
    tier: "R1",
    label: "Bậc R1 · Rủi ro rất thấp",
    shortLabel: "R1 · Rất thấp",
    tone: "low",
    description: "Xếp hạng tín nhiệm nội bộ bậc thấp nhất về nguy cơ kiệt quệ.",
  },
  R2: {
    tier: "R2",
    label: "Bậc R2 · Rủi ro thấp",
    shortLabel: "R2 · Thấp",
    tone: "low",
    description: "Hồ sơ tài chính ổn định, biên an toàn đòn bẩy và thanh khoản tốt.",
  },
  R3: {
    tier: "R3",
    label: "Bậc R3 · Cần theo dõi",
    shortLabel: "R3 · Theo dõi",
    tone: "mid",
    description: "Mức rủi ro trung bình của thị trường, cần theo dõi diễn biến kỳ tới.",
  },
  R4: {
    tier: "R4",
    label: "Bậc R4 · Rủi ro cao",
    shortLabel: "R4 · Cao",
    tone: "high",
    description: "Xác suất kiệt quệ vượt trội nền thị trường, cần rà soát dòng tiền và nợ.",
  },
  R5: {
    tier: "R5",
    label: "Bậc R5 · Rủi ro rất cao",
    shortLabel: "R5 · Rất cao",
    tone: "high",
    description: "Nhóm cảnh báo sớm cao nhất theo thang bậc TTC của mô hình.",
  },
};

export function getScreenerRiskTierMeta(
  riskTier: string | null | undefined,
): ScreenerRiskTierMeta {
  if (
    riskTier === "R1" ||
    riskTier === "R2" ||
    riskTier === "R3" ||
    riskTier === "R4" ||
    riskTier === "R5"
  ) {
    return SCREENER_RISK_TIER_META[riskTier];
  }
  return {
    tier: "UNRATED",
    label: "Chưa xếp bậc",
    shortLabel: "N/A",
    tone: "neutral",
    description: "Chưa có dữ liệu xếp bậc tín nhiệm cho doanh nghiệp/kỳ này.",
  };
}

export function formatTopRiskPercentileLabel(
  riskMarketPercentile: number | null | undefined,
): string | null {
  if (
    riskMarketPercentile === null ||
    riskMarketPercentile === undefined ||
    !Number.isFinite(riskMarketPercentile)
  ) {
    return null;
  }
  const clamped = Math.max(0, Math.min(100, riskMarketPercentile));
  const topPct = Math.max(0.1, 100 - clamped);
  if (topPct > 50) {
    const saferPct = Math.min(99.9, topPct);
    const decimals = saferPct > 90 ? 1 : 0;
    return `An toàn hơn ${formatNumber(saferPct, decimals)}% DN`;
  }
  const decimals = topPct < 10 ? 1 : 0;
  return `Top ${formatNumber(topPct, decimals)}% rủi ro nhất`;
}

export function formatRelativeRiskLiftLabel(
  relativeRiskLift: number | null | undefined,
): string | null {
  if (
    relativeRiskLift === null ||
    relativeRiskLift === undefined ||
    !Number.isFinite(relativeRiskLift)
  ) {
    return null;
  }
  return `${formatNumber(relativeRiskLift, 1)}× nền`;
}

export function formatAnomalyFlagMeta(
  flag: string | null | undefined,
): { label: string; tone: RiskTone } {
  if (!flag) {
    return { label: "Bình thường", tone: "low" };
  }
  const normalized = flag.trim().toLowerCase();
  if (normalized === "normal" || normalized === "bình thường") {
    return { label: "Bình thường", tone: "low" };
  }
  if (normalized.includes("tích cực") || normalized.includes("positive")) {
    return { label: "Ngoại lai tích cực", tone: "low" };
  }
  if (normalized.includes("tiêu cực") || normalized.includes("negative")) {
    return { label: "Ngoại lai tiêu cực", tone: "high" };
  }
  if (
    normalized.includes("ngoại lai") ||
    normalized.includes("outlier") ||
    normalized.includes("anomaly")
  ) {
    return { label: "Ngoại lai đa biến", tone: "mid" };
  }
  if (/^[A-Z0-9_]+$/.test(flag.trim())) {
    return { label: "Cần lưu ý", tone: "mid" };
  }
  return { label: flag, tone: "mid" };
}

// ---------------------------------------------------------------------------
// Full CSV Export URL & response helpers (no 50-row limit/offset cap)
// ---------------------------------------------------------------------------

export type ScreenerExportParams = Omit<
  ScreenerQueryParams,
  "limit" | "offset"
>;

const EXPORT_PARAM_ORDER: readonly (keyof ScreenerExportParams)[] = [
  "period_type",
  "period_end_date",
  "statement_scope",
  "exchange",
  "icb_code_level2",
  "min_f_score",
  "altman_zone",
  "risk_tier",
  "risk_tiers",
  "beneish_flag",
  "max_distress_prob",
  "min_distress_prob",
  "max_red_flags",
  "min_red_flags",
  "q",
  "sort_by",
  "sort_dir",
];

export function buildScreenerExportUrl(
  params: ScreenerQueryParams | ScreenerExportParams,
): string {
  const search = new URLSearchParams();
  const record = params as Record<string, unknown>;
  for (const key of EXPORT_PARAM_ORDER) {
    const val = record[key];
    if (val === undefined || val === null || val === "") continue;
    if (typeof val === "number" && !Number.isFinite(val)) continue;
    search.set(key, String(val));
  }
  const qs = search.toString();
  return qs ? `/api/v1/screener/export?${qs}` : "/api/v1/screener/export";
}

export function parseContentDispositionFilename(
  header: string | null | undefined,
  fallback = "finintel-screener.csv",
): string {
  if (!header) return fallback;
  const utf8Match = header.match(/filename\*=UTF-8''([^;\s]+)/i);
  if (utf8Match?.[1]) {
    try {
      const decoded = decodeURIComponent(utf8Match[1]).replace(/[/\\]/g, "");
      if (decoded) return decoded;
    } catch {
      // Fallback below
    }
  }
  const quotedMatch = header.match(/filename="([^"]+)"/i);
  if (quotedMatch?.[1]) {
    const clean = quotedMatch[1].replace(/[/\\]/g, "").trim();
    if (clean) return clean;
  }
  const plainMatch = header.match(/filename=([^;\s]+)/i);
  if (plainMatch?.[1]) {
    const clean = plainMatch[1].replace(/[/\\"]/g, "").trim();
    if (clean) return clean;
  }
  return fallback;
}

const INTERNAL_LEAK_RE =
  /([A-Za-z]:\\|\/(?:src|data|Users)\/|\.parquet\b|\.sqlite\b|\.py\b|Traceback)/i;

export function formatScreenerExportError(
  status: number | null,
  detail?: string | null,
): string {
  if (status === null) {
    return "Không thể kết nối máy chủ để xuất tệp CSV. Vui lòng kiểm tra mạng và thử lại.";
  }
  if (status === 404) {
    return "Dịch vụ xuất toàn bộ danh sách CSV chưa sẵn sàng trên máy chủ hiện tại.";
  }
  if (status === 422) {
    return "Bộ lọc hiện tại có tham số không hợp lệ, không thể xuất CSV.";
  }
  const cleaned = (detail ?? "").replace(/\s+/g, " ").trim();
  if (cleaned && !INTERNAL_LEAK_RE.test(cleaned)) {
    return `Xuất CSV thất bại: ${cleaned.slice(0, 160)}`;
  }
  return "Xuất tệp CSV thất bại do lỗi máy chủ phân tích. Vui lòng thử lại sau.";
}

// ---------------------------------------------------------------------------
// Column Customizer Config & Persistence
// ---------------------------------------------------------------------------

export {
  type ScreenerColumnGroupId,
  type ScreenerOptionalColumnId,
  type ScreenerColumnOption,
  SCREENER_COLUMN_GROUPS,
  SCREENER_OPTIONAL_COLUMNS,
  DEFAULT_VISIBLE_SCREENER_COLUMNS,
  ALL_SCREENER_OPTIONAL_COLUMN_IDS,
  subscribeColumnPrefs,
  parseVisibleColumnsJson,
  getVisibleColumnsSnapshot,
  getVisibleColumnsServerSnapshot,
  setVisibleColumns,
  toggleVisibleColumn,
  toggleVisibleColumnGroup,
  resetVisibleColumns,
  showAllScreenerColumns,
  getScreenerColumnOrderMap,
  sortScreenerColumns,
} from "@/lib/screener-columns";

// ---------------------------------------------------------------------------
// SSR-safe localStorage stores (useSyncExternalStore compatible)
// ---------------------------------------------------------------------------

const savedScreensListeners = new Set<() => void>();

const EMPTY_SAVED_SCREENS: SavedScreen[] = [];
let cachedRawScreens: string | null | undefined = undefined;
let cachedScreens: SavedScreen[] = EMPTY_SAVED_SCREENS;

function notifySavedScreens() {
  for (const fn of savedScreensListeners) fn();
}

export function subscribeSavedScreens(callback: () => void): () => void {
  savedScreensListeners.add(callback);
  return () => savedScreensListeners.delete(callback);
}

export function parseSavedScreensJson(raw: string | null): SavedScreen[] {
  if (!raw) return EMPTY_SAVED_SCREENS;
  try {
    const parsedJson: unknown = JSON.parse(raw);
    const result = SavedScreensListSchema.safeParse(parsedJson);
    if (!result.success) return EMPTY_SAVED_SCREENS;
    return result.data;
  } catch {
    return EMPTY_SAVED_SCREENS;
  }
}

export function getSavedScreensSnapshot(): SavedScreen[] {
  if (typeof window === "undefined") return EMPTY_SAVED_SCREENS;
  try {
    const raw = window.localStorage.getItem(SAVED_SCREENS_KEY);
    if (raw === cachedRawScreens) return cachedScreens;
    cachedRawScreens = raw;
    cachedScreens = parseSavedScreensJson(raw);
    return cachedScreens;
  } catch {
    cachedRawScreens = null;
    cachedScreens = EMPTY_SAVED_SCREENS;
    return EMPTY_SAVED_SCREENS;
  }
}

export function getSavedScreensServerSnapshot(): SavedScreen[] {
  return EMPTY_SAVED_SCREENS;
}

function writeSavedScreens(next: SavedScreen[]): void {
  const validated = SavedScreensListSchema.safeParse(next);
  const clean = validated.success ? validated.data : EMPTY_SAVED_SCREENS;
  const serialized = JSON.stringify(clean);
  cachedScreens = clean;
  cachedRawScreens = serialized;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(SAVED_SCREENS_KEY, serialized);
    } catch {
      // Ignore storage quota / private mode errors
    }
  }
  notifySavedScreens();
}

function createScreenId(): string {
  if (
    typeof globalThis.crypto !== "undefined" &&
    typeof globalThis.crypto.randomUUID === "function"
  ) {
    return globalThis.crypto.randomUUID();
  }
  return `scr_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export function saveScreenerView(
  name: string,
  filters: Partial<ScreenerFilterState>,
): { ok: true; screen: SavedScreen } | { ok: false; error: string } {
  const cleanName = name.trim();
  if (!cleanName) {
    return { ok: false, error: "Vui lòng nhập tên bộ lọc cần lưu." };
  }
  if (cleanName.length > 60) {
    return { ok: false, error: "Tên bộ lọc tối đa 60 ký tự." };
  }
  const parsedFilters = ScreenerFilterStateSchema.safeParse({
    ...DEFAULT_SCREENER_FILTERS,
    ...filters,
  });
  if (!parsedFilters.success) {
    return { ok: false, error: "Tham số bộ lọc không hợp lệ." };
  }

  const now = new Date().toISOString();
  const current = getSavedScreensSnapshot();
  const existingIdx = current.findIndex(
    (s) => s.name.toLowerCase() === cleanName.toLowerCase(),
  );

  if (existingIdx >= 0) {
    const updated: SavedScreen = {
      ...current[existingIdx],
      name: cleanName,
      updatedAt: now,
      filters: parsedFilters.data,
    };
    const next = [...current];
    next[existingIdx] = updated;
    writeSavedScreens(next);
    return { ok: true, screen: updated };
  }

  if (current.length >= MAX_SAVED_SCREENS) {
    return {
      ok: false,
      error: `Đã đạt giới hạn tối đa ${MAX_SAVED_SCREENS} bộ lọc đã lưu.`,
    };
  }

  const created: SavedScreen = {
    id: createScreenId(),
    name: cleanName,
    createdAt: now,
    updatedAt: now,
    filters: parsedFilters.data,
  };
  writeSavedScreens([created, ...current]);
  return { ok: true, screen: created };
}

export function renameSavedScreen(
  id: string,
  nextName: string,
): { ok: true; screen: SavedScreen } | { ok: false; error: string } {
  const cleanName = nextName.trim();
  if (!cleanName) {
    return { ok: false, error: "Tên bộ lọc không được để trống." };
  }
  if (cleanName.length > 60) {
    return { ok: false, error: "Tên bộ lọc tối đa 60 ký tự." };
  }
  const current = getSavedScreensSnapshot();
  const idx = current.findIndex((s) => s.id === id);
  if (idx < 0) {
    return { ok: false, error: "Không tìm thấy bộ lọc đã lưu." };
  }
  const duplicate = current.some(
    (s) => s.id !== id && s.name.toLowerCase() === cleanName.toLowerCase(),
  );
  if (duplicate) {
    return { ok: false, error: "Đã tồn tại bộ lọc khác cùng tên này." };
  }
  const updated: SavedScreen = {
    ...current[idx],
    name: cleanName,
    updatedAt: new Date().toISOString(),
  };
  const next = [...current];
  next[idx] = updated;
  writeSavedScreens(next);
  return { ok: true, screen: updated };
}

export function deleteSavedScreen(id: string): void {
  const current = getSavedScreensSnapshot();
  const next = current.filter((s) => s.id !== id);
  if (next.length !== current.length) {
    writeSavedScreens(next);
  }
}

export interface ScreenerSearchParamsLike {
  get(name: string): string | null;
}

export function parseScreenerFilters(
  searchParams: ScreenerSearchParamsLike,
): ScreenerFilterState {
  const shape = ScreenerFilterStateSchema.shape;
  const parseField = <T>(
    schema: z.ZodType<T>,
    raw: unknown,
    fallback: T,
  ): T => {
    const res = schema.safeParse(raw);
    return res.success ? res.data : fallback;
  };

  const rawExchange = (searchParams.get("exchange") ?? "").trim().toUpperCase();
  const exchange: ScreenerFilterState["exchange"] = VALID_EXCHANGE_SET.has(
    rawExchange,
  )
    ? (rawExchange as ExchangeCode)
    : "";

  const rawTiers = (searchParams.get("risk_tiers") ?? "")
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter((item): item is ScreenerRiskTier => VALID_RISK_TIER_SET.has(item));
  const rawSingleTier = (searchParams.get("risk_tier") ?? "").trim().toUpperCase();
  const singleTier: ScreenerRiskTier | "" = VALID_RISK_TIER_SET.has(rawSingleTier)
    ? (rawSingleTier as ScreenerRiskTier)
    : "";

  const risk_tiers: ScreenerRiskTier[] =
    rawTiers.length > 0
      ? Array.from(new Set(rawTiers))
      : singleTier
        ? [singleTier]
        : [];

  const risk_tier: ScreenerFilterState["risk_tier"] =
    risk_tiers.length === 1 ? risk_tiers[0] : "";

  const rawQ = searchParams.get("q") ?? "";
  const rawIcb = searchParams.get("icb_code_level2") ?? "";

  return {
    q: parseField(shape.q, rawQ.slice(0, 80), DEFAULT_SCREENER_FILTERS.q),
    exchange,
    icb_code_level2: parseField(
      shape.icb_code_level2,
      rawIcb.slice(0, 20),
      DEFAULT_SCREENER_FILTERS.icb_code_level2,
    ),
    altman_zone: parseField(
      shape.altman_zone,
      searchParams.get("altman_zone") ?? "",
      DEFAULT_SCREENER_FILTERS.altman_zone,
    ),
    risk_tier,
    risk_tiers,
    beneish_flag: parseField(
      shape.beneish_flag,
      searchParams.get("beneish_flag") ?? "",
      DEFAULT_SCREENER_FILTERS.beneish_flag,
    ),
    min_f_score: parseField(
      shape.min_f_score,
      searchParams.get("min_f_score") ?? "",
      DEFAULT_SCREENER_FILTERS.min_f_score,
    ),
    min_distress_prob: parseField(
      shape.min_distress_prob,
      searchParams.get("min_distress_prob") ?? "",
      DEFAULT_SCREENER_FILTERS.min_distress_prob,
    ),
    max_distress_prob: parseField(
      shape.max_distress_prob,
      searchParams.get("max_distress_prob") ?? "",
      DEFAULT_SCREENER_FILTERS.max_distress_prob,
    ),
    min_red_flags: parseField(
      shape.min_red_flags,
      searchParams.get("min_red_flags") ?? "",
      DEFAULT_SCREENER_FILTERS.min_red_flags,
    ),
    max_red_flags: parseField(
      shape.max_red_flags,
      searchParams.get("max_red_flags") ?? "",
      DEFAULT_SCREENER_FILTERS.max_red_flags,
    ),
    cluster_id: parseField(
      shape.cluster_id,
      searchParams.get("cluster_id") ?? "",
      DEFAULT_SCREENER_FILTERS.cluster_id,
    ),
    anomaly_flag: parseField(
      shape.anomaly_flag,
      searchParams.get("anomaly_flag") ?? "",
      DEFAULT_SCREENER_FILTERS.anomaly_flag,
    ),
    min_roe_parent: parseField(shape.min_roe_parent, searchParams.get("min_roe_parent") ?? "", ""),
    max_roe_parent: parseField(shape.max_roe_parent, searchParams.get("max_roe_parent") ?? "", ""),
    min_roa: parseField(shape.min_roa, searchParams.get("min_roa") ?? "", ""),
    max_roa: parseField(shape.max_roa, searchParams.get("max_roa") ?? "", ""),
    min_eps_ttm_estimated: parseField(shape.min_eps_ttm_estimated, searchParams.get("min_eps_ttm_estimated") ?? "", ""),
    max_eps_ttm_estimated: parseField(shape.max_eps_ttm_estimated, searchParams.get("max_eps_ttm_estimated") ?? "", ""),
    min_liabilities_to_assets: parseField(shape.min_liabilities_to_assets, searchParams.get("min_liabilities_to_assets") ?? "", ""),
    max_liabilities_to_assets: parseField(shape.max_liabilities_to_assets, searchParams.get("max_liabilities_to_assets") ?? "", ""),
    min_debt_to_equity: parseField(shape.min_debt_to_equity, searchParams.get("min_debt_to_equity") ?? "", ""),
    max_debt_to_equity: parseField(shape.max_debt_to_equity, searchParams.get("max_debt_to_equity") ?? "", ""),
    min_cfo_ttm: parseField(shape.min_cfo_ttm, searchParams.get("min_cfo_ttm") ?? "", ""),
    max_cfo_ttm: parseField(shape.max_cfo_ttm, searchParams.get("max_cfo_ttm") ?? "", ""),
    min_ocf_to_total_liabilities: parseField(shape.min_ocf_to_total_liabilities, searchParams.get("min_ocf_to_total_liabilities") ?? "", ""),
    max_ocf_to_total_liabilities: parseField(shape.max_ocf_to_total_liabilities, searchParams.get("max_ocf_to_total_liabilities") ?? "", ""),
    min_revenue_growth_yoy: parseField(shape.min_revenue_growth_yoy, searchParams.get("min_revenue_growth_yoy") ?? "", ""),
    max_revenue_growth_yoy: parseField(shape.max_revenue_growth_yoy, searchParams.get("max_revenue_growth_yoy") ?? "", ""),
    min_pe_ttm: parseField(shape.min_pe_ttm, searchParams.get("min_pe_ttm") ?? "", ""),
    max_pe_ttm: parseField(shape.max_pe_ttm, searchParams.get("max_pe_ttm") ?? "", ""),
    min_pb_ratio: parseField(shape.min_pb_ratio, searchParams.get("min_pb_ratio") ?? "", ""),
    max_pb_ratio: parseField(shape.max_pb_ratio, searchParams.get("max_pb_ratio") ?? "", ""),
    min_market_cap_bn_vnd: parseField(shape.min_market_cap_bn_vnd, searchParams.get("min_market_cap_bn_vnd") ?? "", ""),
    max_market_cap_bn_vnd: parseField(shape.max_market_cap_bn_vnd, searchParams.get("max_market_cap_bn_vnd") ?? "", ""),
    sort_by: parseField(
      shape.sort_by,
      searchParams.get("sort_by") ?? DEFAULT_SCREENER_FILTERS.sort_by,
      DEFAULT_SCREENER_FILTERS.sort_by,
    ),
    sort_dir: parseField(
      shape.sort_dir,
      searchParams.get("sort_dir") ?? DEFAULT_SCREENER_FILTERS.sort_dir,
      DEFAULT_SCREENER_FILTERS.sort_dir,
    ),
  };
}

export function filtersToUrlPatch(
  filters: Partial<ScreenerFilterState>,
): Record<string, string | null> {
  const merged = { ...DEFAULT_SCREENER_FILTERS, ...filters };
  const explicitTiers = (filters.risk_tiers ?? []).filter(
    (t): t is ScreenerRiskTier => VALID_RISK_TIER_SET.has(t),
  );
  const dedupedTiers = Array.from(new Set(explicitTiers));
  const effectiveTiers: ScreenerRiskTier[] =
    dedupedTiers.length > 0
      ? dedupedTiers
      : filters.risk_tier && VALID_RISK_TIER_SET.has(filters.risk_tier)
        ? [filters.risk_tier]
        : [];

  const riskTierParam = effectiveTiers.length === 1 ? effectiveTiers[0] : null;
  const riskTiersParam =
    effectiveTiers.length >= 2 ? effectiveTiers.join(",") : null;

  return {
    q: merged.q || null,
    exchange: merged.exchange || null,
    icb_code_level2: merged.icb_code_level2 || null,
    altman_zone: merged.altman_zone || null,
    risk_tier: riskTierParam,
    risk_tiers: riskTiersParam,
    beneish_flag: merged.beneish_flag || null,
    min_f_score: merged.min_f_score || null,
    min_distress_prob: merged.min_distress_prob || null,
    max_distress_prob: merged.max_distress_prob || null,
    min_red_flags: merged.min_red_flags || null,
    max_red_flags: merged.max_red_flags || null,
    cluster_id: merged.cluster_id || null,
    anomaly_flag: merged.anomaly_flag || null,
    min_roe_parent: merged.min_roe_parent || null,
    max_roe_parent: merged.max_roe_parent || null,
    min_roa: merged.min_roa || null,
    max_roa: merged.max_roa || null,
    min_eps_ttm_estimated: merged.min_eps_ttm_estimated || null,
    max_eps_ttm_estimated: merged.max_eps_ttm_estimated || null,
    min_liabilities_to_assets: merged.min_liabilities_to_assets || null,
    max_liabilities_to_assets: merged.max_liabilities_to_assets || null,
    min_debt_to_equity: merged.min_debt_to_equity || null,
    max_debt_to_equity: merged.max_debt_to_equity || null,
    min_cfo_ttm: merged.min_cfo_ttm || null,
    max_cfo_ttm: merged.max_cfo_ttm || null,
    min_ocf_to_total_liabilities: merged.min_ocf_to_total_liabilities || null,
    max_ocf_to_total_liabilities: merged.max_ocf_to_total_liabilities || null,
    min_revenue_growth_yoy: merged.min_revenue_growth_yoy || null,
    max_revenue_growth_yoy: merged.max_revenue_growth_yoy || null,
    min_pe_ttm: merged.min_pe_ttm || null,
    max_pe_ttm: merged.max_pe_ttm || null,
    min_pb_ratio: merged.min_pb_ratio || null,
    max_pb_ratio: merged.max_pb_ratio || null,
    min_market_cap_bn_vnd: merged.min_market_cap_bn_vnd || null,
    max_market_cap_bn_vnd: merged.max_market_cap_bn_vnd || null,
    sort_by: merged.sort_by || "calibrated_distress_prob",
    sort_dir: merged.sort_dir || "desc",
  };
}

const numOrNull = (raw: string): number | null => {
  if (!raw) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
};

export function filtersToApiQueryParams(
  filters: ScreenerFilterState,
  meta: {
    statement_scope: StatementScope;
    period_type: PeriodType;
    period_end_date: string | null;
    limit: number;
    offset: number;
  },
): ScreenerQueryParams {
  const tiers = filters.risk_tiers ?? [];
  return {
    statement_scope: meta.statement_scope,
    period_type: meta.period_type,
    period_end_date: meta.period_end_date,
    q: filters.q || null,
    exchange: (filters.exchange || null) as ScreenerQueryParams["exchange"],
    icb_code_level2: filters.icb_code_level2 || null,
    altman_zone: (filters.altman_zone || null) as ScreenerQueryParams["altman_zone"],
    risk_tier: (tiers.length === 1 ? tiers[0] : null) as ScreenerQueryParams["risk_tier"],
    risk_tiers: tiers.length >= 2 ? tiers.join(",") : null,
    beneish_flag: (filters.beneish_flag || null) as ScreenerQueryParams["beneish_flag"],
    min_f_score: numOrNull(filters.min_f_score),
    min_distress_prob: numOrNull(filters.min_distress_prob),
    max_distress_prob: numOrNull(filters.max_distress_prob),
    min_red_flags: numOrNull(filters.min_red_flags),
    max_red_flags: numOrNull(filters.max_red_flags),
    cluster_id: numOrNull(filters.cluster_id),
    anomaly_flag:
      filters.anomaly_flag === "true"
        ? true
        : filters.anomaly_flag === "false"
          ? false
          : null,
    min_roe_parent: numOrNull(filters.min_roe_parent),
    max_roe_parent: numOrNull(filters.max_roe_parent),
    min_roa: numOrNull(filters.min_roa),
    max_roa: numOrNull(filters.max_roa),
    min_eps_ttm_estimated: numOrNull(filters.min_eps_ttm_estimated),
    max_eps_ttm_estimated: numOrNull(filters.max_eps_ttm_estimated),
    min_liabilities_to_assets: numOrNull(filters.min_liabilities_to_assets),
    max_liabilities_to_assets: numOrNull(filters.max_liabilities_to_assets),
    min_debt_to_equity: numOrNull(filters.min_debt_to_equity),
    max_debt_to_equity: numOrNull(filters.max_debt_to_equity),
    min_cfo_ttm: numOrNull(filters.min_cfo_ttm),
    max_cfo_ttm: numOrNull(filters.max_cfo_ttm),
    min_ocf_to_total_liabilities: numOrNull(filters.min_ocf_to_total_liabilities),
    max_ocf_to_total_liabilities: numOrNull(filters.max_ocf_to_total_liabilities),
    min_revenue_growth_yoy: numOrNull(filters.min_revenue_growth_yoy),
    max_revenue_growth_yoy: numOrNull(filters.max_revenue_growth_yoy),
    min_pe_ttm: numOrNull(filters.min_pe_ttm),
    max_pe_ttm: numOrNull(filters.max_pe_ttm),
    min_pb_ratio: numOrNull(filters.min_pb_ratio),
    max_pb_ratio: numOrNull(filters.max_pb_ratio),
    min_market_cap_bn_vnd: numOrNull(filters.min_market_cap_bn_vnd),
    max_market_cap_bn_vnd: numOrNull(filters.max_market_cap_bn_vnd),
    sort_by: filters.sort_by,
    sort_dir: filters.sort_dir,
    limit: meta.limit,
    offset: meta.offset,
  };
}

export function countActiveScreenerFilters(filters: ScreenerFilterState): number {
  const tiers = filters.risk_tiers ?? [];
  const entries: Array<string> = [
    filters.q,
    filters.exchange ?? "",
    filters.icb_code_level2,
    tiers.length > 0 ? tiers.join(",") : "",
    filters.altman_zone,
    filters.beneish_flag,
    filters.min_f_score,
    filters.min_distress_prob,
    filters.max_distress_prob,
    filters.min_red_flags,
    filters.max_red_flags,
    filters.cluster_id,
    filters.anomaly_flag,
    filters.min_roe_parent || filters.max_roe_parent,
    filters.min_roa || filters.max_roa,
    filters.min_eps_ttm_estimated || filters.max_eps_ttm_estimated,
    filters.min_liabilities_to_assets || filters.max_liabilities_to_assets,
    filters.min_debt_to_equity || filters.max_debt_to_equity,
    filters.min_cfo_ttm || filters.max_cfo_ttm,
    filters.min_ocf_to_total_liabilities || filters.max_ocf_to_total_liabilities,
    filters.min_revenue_growth_yoy || filters.max_revenue_growth_yoy,
    filters.min_pe_ttm || filters.max_pe_ttm,
    filters.min_pb_ratio || filters.max_pb_ratio,
    filters.min_market_cap_bn_vnd || filters.max_market_cap_bn_vnd,
  ];
  return entries.filter(Boolean).length;
}

// Re-export unused types cleanly for callers
export type { ScreenerItem, SortDirection };
