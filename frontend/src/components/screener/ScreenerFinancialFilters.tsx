"use client";

import * as React from "react";
import { X } from "lucide-react";
import { Input, Select } from "@/components/ui/primitives";
import type { ScreenerFilterState } from "@/lib/saved-screens-store";

export type UpdateScreenerParams = (
  patch: Record<string, string | null>,
  resetPage?: boolean,
) => void;

interface RangeFieldConfig {
  id: string;
  label: string;
  minKey: keyof ScreenerFilterState;
  maxKey: keyof ScreenerFilterState;
  scale: number;
  step?: string;
  placeholderMin?: string;
  placeholderMax?: string;
}

const PROFITABILITY_AND_GROWTH_FIELDS: readonly RangeFieldConfig[] = [
  {
    id: "scr-roe-parent",
    label: "ROE CĐ mẹ (%)",
    minKey: "min_roe_parent",
    maxKey: "max_roe_parent",
    scale: 100,
    step: "1",
    placeholderMin: "Từ %",
    placeholderMax: "Đến %",
  },
  {
    id: "scr-roa",
    label: "ROA (%)",
    minKey: "min_roa",
    maxKey: "max_roa",
    scale: 100,
    step: "1",
    placeholderMin: "Từ %",
    placeholderMax: "Đến %",
  },
  {
    id: "scr-eps",
    label: "EPS TTM (VND/cp)",
    minKey: "min_eps_ttm_estimated",
    maxKey: "max_eps_ttm_estimated",
    scale: 1,
    step: "100",
    placeholderMin: "Từ",
    placeholderMax: "Đến",
  },
  {
    id: "scr-rev-yoy",
    label: "TT DT YoY (%)",
    minKey: "min_revenue_growth_yoy",
    maxKey: "max_revenue_growth_yoy",
    scale: 100,
    step: "1",
    placeholderMin: "Từ %",
    placeholderMax: "Đến %",
  },
];

const LEVERAGE_AND_CASHFLOW_FIELDS: readonly RangeFieldConfig[] = [
  {
    id: "scr-liab-assets",
    label: "Nợ / Tổng TS (%)",
    minKey: "min_liabilities_to_assets",
    maxKey: "max_liabilities_to_assets",
    scale: 100,
    step: "1",
    placeholderMin: "Từ %",
    placeholderMax: "Đến %",
  },
  {
    id: "scr-dte",
    label: "Nợ / VCSH (x)",
    minKey: "min_debt_to_equity",
    maxKey: "max_debt_to_equity",
    scale: 1,
    step: "0.1",
    placeholderMin: "Từ",
    placeholderMax: "Đến",
  },
  {
    id: "scr-cfo",
    label: "CFO TTM (Tỷ VND)",
    minKey: "min_cfo_ttm",
    maxKey: "max_cfo_ttm",
    scale: 1e-9,
    step: "10",
    placeholderMin: "Từ tỷ",
    placeholderMax: "Đến tỷ",
  },
  {
    id: "scr-ocf-liab",
    label: "OCF / Tổng nợ (x)",
    minKey: "min_ocf_to_total_liabilities",
    maxKey: "max_ocf_to_total_liabilities",
    scale: 1,
    step: "0.05",
    placeholderMin: "Từ",
    placeholderMax: "Đến",
  },
];

const VALUATION_FIELDS: readonly RangeFieldConfig[] = [
  {
    id: "scr-pe",
    label: "P/E TTM (x)",
    minKey: "min_pe_ttm",
    maxKey: "max_pe_ttm",
    scale: 1,
    step: "0.5",
    placeholderMin: "Từ",
    placeholderMax: "Đến",
  },
  {
    id: "scr-pb",
    label: "P/B (x)",
    minKey: "min_pb_ratio",
    maxKey: "max_pb_ratio",
    scale: 1,
    step: "0.1",
    placeholderMin: "Từ",
    placeholderMax: "Đến",
  },
  {
    id: "scr-mcap",
    label: "Vốn hóa (Tỷ VND)",
    minKey: "min_market_cap_bn_vnd",
    maxKey: "max_market_cap_bn_vnd",
    scale: 1,
    step: "100",
    placeholderMin: "Từ tỷ",
    placeholderMax: "Đến tỷ",
  },
];

const ALL_RANGE_FIELDS: readonly RangeFieldConfig[] = [
  ...PROFITABILITY_AND_GROWTH_FIELDS,
  ...LEVERAGE_AND_CASHFLOW_FIELDS,
  ...VALUATION_FIELDS,
];

function toDisplayString(raw: string, scale: number): string {
  if (!raw) return "";
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return "";
  const scaled = parsed * scale;
  return String(Math.round(scaled * 10000) / 10000);
}

function toBackendString(display: string, scale: number): string | null {
  const trimmed = display.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) return null;
  const raw = parsed / scale;
  if (!Number.isFinite(raw)) return null;
  return String(Math.round(raw * 1_000_000) / 1_000_000);
}

function ScaledNumericInput({
  id,
  ariaLabel,
  value,
  scale,
  step,
  placeholder,
  onCommit,
}: {
  id: string;
  ariaLabel: string;
  value: string;
  scale: number;
  step?: string;
  placeholder?: string;
  onCommit: (backendValue: string | null) => void;
}) {
  const externalDisplay = toDisplayString(value, scale);
  const [draft, setDraft] = React.useState<string | null>(null);

  return (
    <Input
      id={id}
      type="number"
      step={step ?? "any"}
      aria-label={ariaLabel}
      placeholder={placeholder}
      value={draft ?? externalDisplay}
      onChange={(e) => {
        const next = e.target.value;
        setDraft(next);
        if (!next.trim()) {
          onCommit(null);
          return;
        }
        const parsed = toBackendString(next, scale);
        if (parsed !== null) {
          onCommit(parsed);
        }
      }}
      onBlur={() => setDraft(null)}
      className="h-8 text-xs tabular-nums"
    />
  );
}

function RangeFilterPair({
  config,
  filters,
  updateParams,
}: {
  config: RangeFieldConfig;
  filters: ScreenerFilterState;
  updateParams: UpdateScreenerParams;
}) {
  const minVal = String(filters[config.minKey] ?? "");
  const maxVal = String(filters[config.maxKey] ?? "");

  return (
    <div className="space-y-1">
      <span className="block text-[11px] font-semibold text-ink-muted">
        {config.label}
      </span>
      <div className="grid grid-cols-2 gap-1.5">
        <ScaledNumericInput
          id={`${config.id}-min`}
          ariaLabel={`${config.label} tối thiểu`}
          value={minVal}
          scale={config.scale}
          step={config.step}
          placeholder={config.placeholderMin}
          onCommit={(v) => updateParams({ [String(config.minKey)]: v })}
        />
        <ScaledNumericInput
          id={`${config.id}-max`}
          ariaLabel={`${config.label} tối đa`}
          value={maxVal}
          scale={config.scale}
          step={config.step}
          placeholder={config.placeholderMax}
          onCommit={(v) => updateParams({ [String(config.maxKey)]: v })}
        />
      </div>
    </div>
  );
}

export function ScreenerFinancialFilters({
  filters,
  updateParams,
}: {
  filters: ScreenerFilterState;
  updateParams: UpdateScreenerParams;
}) {
  return (
    <div className="space-y-3 border-t border-border pt-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {PROFITABILITY_AND_GROWTH_FIELDS.map((cfg) => (
          <RangeFilterPair
            key={cfg.id}
            config={cfg}
            filters={filters}
            updateParams={updateParams}
          />
        ))}
        {LEVERAGE_AND_CASHFLOW_FIELDS.map((cfg) => (
          <RangeFilterPair
            key={cfg.id}
            config={cfg}
            filters={filters}
            updateParams={updateParams}
          />
        ))}
        {VALUATION_FIELDS.map((cfg) => (
          <RangeFilterPair
            key={cfg.id}
            config={cfg}
            filters={filters}
            updateParams={updateParams}
          />
        ))}
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <label
              htmlFor="scr-cluster-id"
              className="block text-[11px] font-semibold text-ink-muted"
            >
              Cụm ML
            </label>
            <Select
              id="scr-cluster-id"
              value={filters.cluster_id}
              onChange={(e) =>
                updateParams({ cluster_id: e.target.value || null })
              }
              className="h-8 text-xs"
            >
              <option value="">Tất cả cụm</option>
              <option value="0">Cụm 0</option>
              <option value="1">Cụm 1</option>
              <option value="2">Cụm 2</option>
              <option value="3">Cụm 3</option>
              <option value="4">Cụm 4</option>
            </Select>
          </div>
          <div className="space-y-1">
            <label
              htmlFor="scr-anomaly-flag"
              className="block text-[11px] font-semibold text-ink-muted"
            >
              Ngoại lai
            </label>
            <Select
              id="scr-anomaly-flag"
              value={filters.anomaly_flag}
              onChange={(e) =>
                updateParams({ anomaly_flag: e.target.value || null })
              }
              className="h-8 text-xs"
            >
              <option value="">Tất cả</option>
              <option value="true">Có ngoại lai</option>
              <option value="false">Bình thường</option>
            </Select>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ScreenerV3FilterChips({
  filters,
  updateParams,
}: {
  filters: ScreenerFilterState;
  updateParams: UpdateScreenerParams;
}) {
  return (
    <>
      {ALL_RANGE_FIELDS.map((cfg) => {
        const minRaw = String(filters[cfg.minKey] ?? "");
        const maxRaw = String(filters[cfg.maxKey] ?? "");
        if (!minRaw && !maxRaw) return null;
        const minDisp = minRaw ? toDisplayString(minRaw, cfg.scale) : "−∞";
        const maxDisp = maxRaw ? toDisplayString(maxRaw, cfg.scale) : "+∞";
        return (
          <button
            key={cfg.id}
            type="button"
            onClick={() =>
              updateParams({
                [String(cfg.minKey)]: null,
                [String(cfg.maxKey)]: null,
              })
            }
            className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
          >
            <span>
              {cfg.label}: {minDisp}–{maxDisp}
            </span>
            <X className="size-3 text-ink-muted" />
          </button>
        );
      })}
      {filters.cluster_id ? (
        <button
          type="button"
          onClick={() => updateParams({ cluster_id: null })}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
        >
          <span>Cụm ML: {filters.cluster_id}</span>
          <X className="size-3 text-ink-muted" />
        </button>
      ) : null}
      {filters.anomaly_flag ? (
        <button
          type="button"
          onClick={() => updateParams({ anomaly_flag: null })}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink hover:bg-surface-hover"
        >
          <span>
            Ngoại lai:{" "}
            {filters.anomaly_flag === "true" ? "Có ngoại lai" : "Bình thường"}
          </span>
          <X className="size-3 text-ink-muted" />
        </button>
      ) : null}
    </>
  );
}
