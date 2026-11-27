import { z } from "zod";

const COLUMN_PREFS_KEY = "finintel:screener-columns:v1";

export type ScreenerColumnGroupId =
  | "credit_risk"
  | "models"
  | "forensic"
  | "financials"
  | "valuation";

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
  | "roe_parent"
  | "roa"
  | "eps_ttm_estimated"
  | "ebit_margin"
  | "liabilities_to_assets"
  | "debt_to_equity"
  | "interest_coverage"
  | "cfo_ttm"
  | "fcf_ttm"
  | "ocf_to_total_liabilities"
  | "revenue_growth_yoy"
  | "pe_ttm"
  | "pb_ratio"
  | "market_cap_vnd";

export interface ScreenerColumnOption {
  id: ScreenerOptionalColumnId;
  label: string;
  group: ScreenerColumnGroupId;
  defaultVisible: boolean;
  align: "left" | "right";
  redundantWithDefault?: boolean;
}

export const SCREENER_COLUMN_GROUPS: readonly {
  id: ScreenerColumnGroupId;
  label: string;
}[] = [
  { id: "credit_risk", label: "Tín dụng & Xác suất kiệt quệ" },
  { id: "models", label: "Mô hình cổ điển & Phân cụm" },
  { id: "forensic", label: "Cờ đỏ & Chất lượng lợi nhuận" },
  { id: "financials", label: "Sinh lời, Đòn bẩy & Dòng tiền" },
  { id: "valuation", label: "Định giá & Quy mô PIT" },
];

export const SCREENER_OPTIONAL_COLUMNS: readonly ScreenerColumnOption[] = [
  {
    id: "distress",
    label: "Đánh giá rủi ro (XS · R1–R5 · Top %)",
    group: "credit_risk",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "risk_tier",
    label: "Bậc rủi ro (R1–R5 · tách riêng)",
    group: "credit_risk",
    defaultVisible: false,
    align: "left",
    redundantWithDefault: true,
  },
  {
    id: "risk_market_percentile",
    label: "Vị thế rủi ro (Top X% · tách riêng)",
    group: "credit_risk",
    defaultVisible: false,
    align: "right",
    redundantWithDefault: true,
  },
  {
    id: "relative_risk_lift",
    label: "Hệ số so nền (× nền · tách riêng)",
    group: "credit_risk",
    defaultVisible: false,
    align: "right",
    redundantWithDefault: true,
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
    id: "roe_parent",
    label: "ROE (CĐ mẹ)",
    group: "financials",
    defaultVisible: false,
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
    id: "eps_ttm_estimated",
    label: "EPS TTM",
    group: "financials",
    defaultVisible: true,
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
    id: "liabilities_to_assets",
    label: "Nợ/TS",
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
  {
    id: "cfo_ttm",
    label: "CFO (Tỷ)",
    group: "financials",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "fcf_ttm",
    label: "FCF (Tỷ)",
    group: "financials",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "ocf_to_total_liabilities",
    label: "OCF/Tổng nợ",
    group: "financials",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "revenue_growth_yoy",
    label: "TT DT YoY",
    group: "financials",
    defaultVisible: false,
    align: "right",
  },
  {
    id: "pe_ttm",
    label: "P/E",
    group: "valuation",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "pb_ratio",
    label: "P/B",
    group: "valuation",
    defaultVisible: true,
    align: "right",
  },
  {
    id: "market_cap_vnd",
    label: "Vốn hóa (Tỷ)",
    group: "valuation",
    defaultVisible: true,
    align: "right",
  },
];

export const DEFAULT_VISIBLE_SCREENER_COLUMNS: readonly ScreenerOptionalColumnId[] =
  SCREENER_OPTIONAL_COLUMNS.filter((c) => c.defaultVisible).map((c) => c.id);

export const ALL_SCREENER_OPTIONAL_COLUMN_IDS: readonly ScreenerOptionalColumnId[] =
  SCREENER_OPTIONAL_COLUMNS.map((c) => c.id);

const VALID_COLUMN_ID_SET = new Set<string>(ALL_SCREENER_OPTIONAL_COLUMN_IDS);
const ColumnPrefsSchema = z.array(z.string()).max(60);

const columnPrefsListeners = new Set<() => void>();

let cachedRawCols: string | null | undefined = undefined;
let cachedVisibleCols: ScreenerOptionalColumnId[] = [
  ...DEFAULT_VISIBLE_SCREENER_COLUMNS,
];

function notifyColumnPrefs() {
  for (const fn of columnPrefsListeners) fn();
}

export function subscribeColumnPrefs(callback: () => void): () => void {
  columnPrefsListeners.add(callback);
  return () => columnPrefsListeners.delete(callback);
}

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
