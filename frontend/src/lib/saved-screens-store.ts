import { z } from "zod";
import type { RiskTone } from "@/lib/domain-labels";
import { formatNumber } from "@/lib/format";
import type {
  ScreenerItem,
  ScreenerQueryParams,
  ScreenerSortField,
  SortDirection,
} from "@/types/api";

const SAVED_SCREENS_KEY = "finintel:saved-screens:v1";
const COLUMN_PREFS_KEY = "finintel:screener-columns:v1";
const MAX_SAVED_SCREENS = 30;

export const VALID_RISK_TIERS = ["R1", "R2", "R3", "R4", "R5"] as const;
export type ScreenerRiskTier = (typeof VALID_RISK_TIERS)[number];

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
  "debt_to_equity",
] as const satisfies readonly ScreenerSortField[];

export const ScreenerFilterStateSchema = z.object({
  q: z.string().max(80).default(""),
  icb_code_level2: z.string().max(20).default(""),
  altman_zone: z.enum(["", ...VALID_ALTMAN_ZONES]).default(""),
  risk_tier: z.enum(["", ...VALID_RISK_TIERS]).default(""),
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
    .regex(/^$|^[0-9]$/)
    .default(""),
  max_red_flags: z
    .string()
    .regex(/^$|^[0-9]$/)
    .default(""),
  sort_by: z.enum(VALID_SORT_FIELDS).default("calibrated_distress_prob"),
  sort_dir: z.enum(["asc", "desc"] as const).default("desc"),
});

export type ScreenerFilterState = z.infer<typeof ScreenerFilterStateSchema>;

export const DEFAULT_SCREENER_FILTERS: ScreenerFilterState = {
  q: "",
  icb_code_level2: "",
  altman_zone: "",
  risk_tier: "",
  beneish_flag: "",
  min_f_score: "",
  min_distress_prob: "",
  max_distress_prob: "",
  min_red_flags: "",
  max_red_flags: "",
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
      "Nhóm doanh nghiệp thuộc vùng nguy cơ kiệt quệ và có cờ đỏ kế toán, xếp theo xác suất kiệt quệ giảm dần.",
    filters: {
      altman_zone: "DISTRESS_ZONE",
      min_red_flags: "1",
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
      "Vùng an toàn Altman, điểm Piotroski F-Score từ 6/9 trở lên và tối đa 1 cờ đỏ, xếp theo ROE.",
    filters: {
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
  "icb_code_level2",
  "min_f_score",
  "altman_zone",
  "risk_tier",
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

export type ScreenerColumnGroupId =
  | "credit_risk"
  | "models"
  | "forensic"
  | "financials";

export type ScreenerOptionalColumnId =
  | "distress"
  | "risk_tier"
  | "risk_market_percentile"
  | "relative_risk_lift"
  | "market_base_rate"
  | "altman"
  | "altman_em_score"
  | "f_score"
  | "beneish"
  | "cluster_archetype_name"
  | "anomaly_flag"
  | "red_flags"
  | "critical_red_flag_count"
  | "accruals_to_assets"
  | "core_operating_margin"
  | "non_core_distortion_to_assets"
  | "core_to_ebt_ratio"
  | "roe_total"
  | "roa"
  | "ebit_margin"
  | "debt_to_equity"
  | "interest_coverage";

export interface ScreenerColumnOption {
  id: ScreenerOptionalColumnId;
  label: string;
  group: ScreenerColumnGroupId;
  defaultVisible: boolean;
  align: "left" | "right";
}

export const SCREENER_COLUMN_GROUPS: readonly {
  id: ScreenerColumnGroupId;
  label: string;
}[] = [
  { id: "credit_risk", label: "Tín dụng & Xác suất kiệt quệ" },
  { id: "models", label: "Mô hình cổ điển & Phân cụm" },
  { id: "forensic", label: "Cờ đỏ & Chất lượng lợi nhuận" },
  { id: "financials", label: "Sinh lời & Đòn bẩy" },
];

export const SCREENER_OPTIONAL_COLUMNS: readonly ScreenerColumnOption[] = [
  {
    id: "distress",
    label: "Bộ 3 Tín dụng (1Y PD · R1–R5 · Top %)",
    group: "credit_risk",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "risk_tier",
    label: "Bậc tín nhiệm (R1–R5)",
    group: "credit_risk",
    defaultVisible: false,
    align: "left",
  },
  {
    id: "risk_market_percentile",
    label: "Vị thế rủi ro (Top X%)",
    group: "credit_risk",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "relative_risk_lift",
    label: "Hệ số so nền (× nền)",
    group: "credit_risk",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "market_base_rate",
    label: "Tỷ lệ nền thị trường (%)",
    group: "credit_risk",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "altman",
    label: "Altman Z″ & Phân vùng",
    group: "models",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "altman_em_score",
    label: "Altman Z″ (EM)",
    group: "models",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "f_score",
    label: "Piotroski F-Score",
    group: "models",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "beneish",
    label: "Beneish M-Score",
    group: "models",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "cluster_archetype_name",
    label: "Nhóm đặc trưng tài chính",
    group: "models",
    defaultVisible: false,
    align: "left",
  },
  {
    id: "anomaly_flag",
    label: "Ngoại lai thống kê",
    group: "models",
    defaultVisible: false,
    align: "left",
  },
  {
    id: "red_flags",
    label: "Tổng cờ đỏ",
    group: "forensic",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "critical_red_flag_count",
    label: "Cờ đỏ nghiêm trọng",
    group: "forensic",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "accruals_to_assets",
    label: "Dồn tích / Tổng TS",
    group: "forensic",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "core_operating_margin",
    label: "Biên LN lõi",
    group: "forensic",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "non_core_distortion_to_assets",
    label: "Biến dạng phi lõi / TS",
    group: "forensic",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "core_to_ebt_ratio",
    label: "LN lõi / EBT",
    group: "forensic",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "roe_total",
    label: "ROE",
    group: "financials",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "roa",
    label: "ROA",
    group: "financials",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "ebit_margin",
    label: "Biên EBIT",
    group: "financials",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "debt_to_equity",
    label: "Nợ/VCSH",
    group: "financials",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "interest_coverage",
    label: "EBIT/Lãi vay",
    group: "financials",
    defaultVisible: true,
    align: "right",
  },
];

export const DEFAULT_VISIBLE_SCREENER_COLUMNS: readonly ScreenerOptionalColumnId[] =
  SCREENER_OPTIONAL_COLUMNS.filter((c) => c.defaultVisible).map((c) => c.id);

export const ALL_SCREENER_OPTIONAL_COLUMN_IDS: readonly ScreenerOptionalColumnId[] =
  SCREENER_OPTIONAL_COLUMNS.map((c) => c.id);

const VALID_COLUMN_ID_SET = new Set<string>(ALL_SCREENER_OPTIONAL_COLUMN_IDS);
const ColumnPrefsSchema = z.array(z.string()).max(40);

// ---------------------------------------------------------------------------
// SSR-safe localStorage stores (useSyncExternalStore compatible)
// ---------------------------------------------------------------------------

const savedScreensListeners = new Set<() => void>();
const columnPrefsListeners = new Set<() => void>();

const EMPTY_SAVED_SCREENS: SavedScreen[] = [];
let cachedRawScreens: string | null | undefined = undefined;
let cachedScreens: SavedScreen[] = EMPTY_SAVED_SCREENS;

let cachedRawCols: string | null | undefined = undefined;
let cachedVisibleCols: ScreenerOptionalColumnId[] = [
  ...DEFAULT_VISIBLE_SCREENER_COLUMNS,
];

function notifySavedScreens() {
  for (const fn of savedScreensListeners) fn();
}

function notifyColumnPrefs() {
  for (const fn of columnPrefsListeners) fn();
}

export function subscribeSavedScreens(callback: () => void): () => void {
  savedScreensListeners.add(callback);
  return () => savedScreensListeners.delete(callback);
}

export function subscribeColumnPrefs(callback: () => void): () => void {
  columnPrefsListeners.add(callback);
  return () => columnPrefsListeners.delete(callback);
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

export function filtersToUrlPatch(
  filters: Partial<ScreenerFilterState>,
): Record<string, string | null> {
  const merged = { ...DEFAULT_SCREENER_FILTERS, ...filters };
  return {
    q: merged.q || null,
    icb_code_level2: merged.icb_code_level2 || null,
    altman_zone: merged.altman_zone || null,
    risk_tier: merged.risk_tier || null,
    beneish_flag: merged.beneish_flag || null,
    min_f_score: merged.min_f_score || null,
    min_distress_prob: merged.min_distress_prob || null,
    max_distress_prob: merged.max_distress_prob || null,
    min_red_flags: merged.min_red_flags || null,
    max_red_flags: merged.max_red_flags || null,
    sort_by: merged.sort_by || "calibrated_distress_prob",
    sort_dir: merged.sort_dir || "desc",
  };
}

// Column visibility store helpers
const DEFAULT_COLS_COPY: ScreenerOptionalColumnId[] = [
  ...DEFAULT_VISIBLE_SCREENER_COLUMNS,
];

export function parseVisibleColumnsJson(
  raw: string | null,
): ScreenerOptionalColumnId[] {
  if (!raw) return DEFAULT_COLS_COPY;
  try {
    const parsed = ColumnPrefsSchema.safeParse(JSON.parse(raw));
    if (!parsed.success) return DEFAULT_COLS_COPY;
    const valid = Array.from(
      new Set(
        parsed.data.filter((id): id is ScreenerOptionalColumnId =>
          VALID_COLUMN_ID_SET.has(id),
        ),
      ),
    );
    return valid.length > 0 ? valid : DEFAULT_COLS_COPY;
  } catch {
    return DEFAULT_COLS_COPY;
  }
}

export function getVisibleColumnsSnapshot(): ScreenerOptionalColumnId[] {
  if (typeof window === "undefined") return DEFAULT_COLS_COPY;
  try {
    const raw = window.localStorage.getItem(COLUMN_PREFS_KEY);
    if (raw === cachedRawCols) return cachedVisibleCols;
    cachedRawCols = raw;
    cachedVisibleCols = parseVisibleColumnsJson(raw);
    return cachedVisibleCols;
  } catch {
    cachedRawCols = null;
    cachedVisibleCols = DEFAULT_COLS_COPY;
    return DEFAULT_COLS_COPY;
  }
}

export function getVisibleColumnsServerSnapshot(): ScreenerOptionalColumnId[] {
  return DEFAULT_COLS_COPY;
}

export function setVisibleColumns(
  nextIds: readonly ScreenerOptionalColumnId[],
): void {
  const clean = Array.from(
    new Set(
      nextIds.filter((id): id is ScreenerOptionalColumnId =>
        VALID_COLUMN_ID_SET.has(id),
      ),
    ),
  );
  const effective = clean.length > 0 ? clean : [...DEFAULT_COLS_COPY];
  const serialized = JSON.stringify(effective);
  cachedVisibleCols = effective;
  cachedRawCols = serialized;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(COLUMN_PREFS_KEY, serialized);
    } catch {
      // Ignore storage errors
    }
  }
  notifyColumnPrefs();
}

export function toggleVisibleColumn(id: ScreenerOptionalColumnId): void {
  const current = getVisibleColumnsSnapshot();
  if (current.includes(id)) {
    if (current.length <= 1) return; // keep at least 1 optional metric visible
    setVisibleColumns(current.filter((c) => c !== id));
  } else {
    setVisibleColumns([...current, id]);
  }
}

export function toggleVisibleColumnGroup(group: ScreenerColumnGroupId): void {
  const groupIds = SCREENER_OPTIONAL_COLUMNS.filter(
    (c) => c.group === group,
  ).map((c) => c.id);
  const current = new Set(getVisibleColumnsSnapshot());
  const allEnabled = groupIds.every((id) => current.has(id));
  if (allEnabled) {
    const remaining = Array.from(current).filter(
      (id) => !groupIds.includes(id),
    );
    if (remaining.length > 0) {
      setVisibleColumns(remaining);
    }
  } else {
    setVisibleColumns([...current, ...groupIds]);
  }
}

export function resetVisibleColumns(): void {
  setVisibleColumns(DEFAULT_COLS_COPY);
}

export function showAllScreenerColumns(): void {
  setVisibleColumns(ALL_SCREENER_OPTIONAL_COLUMN_IDS);
}

export function getScreenerColumnOrderMap(): Map<
  ScreenerOptionalColumnId,
  number
> {
  return new Map(SCREENER_OPTIONAL_COLUMNS.map((c, idx) => [c.id, idx]));
}

export function sortScreenerColumns(
  cols: readonly ScreenerOptionalColumnId[],
): ScreenerOptionalColumnId[] {
  const order = getScreenerColumnOrderMap();
  return [...cols].sort(
    (a, b) => (order.get(a) ?? 999) - (order.get(b) ?? 999),
  );
}

// Re-export unused types cleanly for callers
export type { ScreenerItem, SortDirection };
