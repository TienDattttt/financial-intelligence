"use client";

import * as React from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Download,
  Pin,
  PinOff,
  RotateCcw,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { StressSectorBarChart } from "@/components/charts/StressSectorBarChart";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/primitives";
import { PERIOD_TYPE_LABELS, STATEMENT_SCOPE_LABELS } from "@/lib/domain-labels";
import { apiClient } from "@/lib/api-client";
import {
  buildCsvContent,
  formatMultiple,
  formatNumber,
  formatPercent,
  formatPercentPoints,
  formatVndBillions,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type { StressScenarioRequest, StressTestResponse } from "@/types/api";

interface ShockUiState {
  scenarioName: string;
  revPct: number; // [-80..50]
  gmPp: number; // [-50..20]
  rateBps: number; // [-500..1500]
  dsoDays: number; // [-90..180]
  dioDays: number; // [-90..180]
}

const DEFAULT_SHOCKS: ShockUiState = {
  scenarioName: "Cơ sở (Không cú sốc)",
  revPct: 0,
  gmPp: 0,
  rateBps: 0,
  dsoDays: 0,
  dioDays: 0,
};

const PRESETS: { label: string; values: ShockUiState }[] = [
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
    },
  },
  {
    label: "Suy giảm cầu (DT −15%, biên −2 đ%)",
    values: {
      scenarioName: "Suy giảm cầu",
      revPct: -15,
      gmPp: -2,
      rateBps: 0,
      dsoDays: 0,
      dioDays: 0,
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
    },
  },
];

function clamp(v: number, min: number, max: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.max(min, Math.min(max, v));
}

function DualShockControl({
  id,
  label,
  unit,
  min,
  max,
  step,
  value,
  onChange,
}: {
  id: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (next: number) => void;
}) {
  return (
    <div className="space-y-1.5 border-b border-border/60 pb-2.5 last:border-b-0 last:pb-0">
      <div className="flex items-center justify-between gap-2 text-xs">
        <label htmlFor={id} className="font-medium text-ink">
          {label}
        </label>
        <div className="flex items-center gap-1">
          <Input
            id={id}
            type="number"
            min={min}
            max={max}
            step={step}
            value={value}
            onChange={(e) => onChange(clamp(Number(e.target.value), min, max))}
            className="num h-7 w-20 px-1.5 text-right text-xs"
          />
          <span className="w-8 text-[11px] text-ink-muted">{unit}</span>
        </div>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={`${label} thanh trượt`}
        onChange={(e) => onChange(clamp(Number(e.target.value), min, max))}
        className="w-full accent-accent-ink"
      />
      <div className="num flex justify-between text-[10px] text-ink-subtle">
        <span>{min}</span>
        <span>0</span>
        <span>+{max}</span>
      </div>
    </div>
  );
}

export default function StressTestPage() {
  const { statementScope, periodType, periodEndDate } = useAppState();
  const [shocks, setShocks] = React.useState<ShockUiState>(PRESETS[1].values);
  const [result, setResult] = React.useState<StressTestResponse | null>(null);
  const [pinnedA, setPinnedA] = React.useState<StressTestResponse | null>(null);
  const [isCalculating, setIsCalculating] = React.useState(false);
  const [errorMsg, setErrorMsg] = React.useState<string | null>(null);
  const [selectedSectorCode, setSelectedSectorCode] = React.useState<string | null>(null);
  const [activeTab, setActiveTab] = React.useState<"NEWLY" | "TOP">("NEWLY");
  const [mobileControlsOpen, setMobileControlsOpen] = React.useState(false);

  React.useEffect(() => {
    if (!mobileControlsOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileControlsOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileControlsOpen]);

  const payload = React.useMemo<StressScenarioRequest>(
    () => ({
      scenario_name: shocks.scenarioName.trim() || "Custom Stress",
      statement_scope: statementScope,
      period_type: periodType,
      period_end_date: periodEndDate,
      revenue_shock_pct: Number((shocks.revPct / 100).toFixed(4)),
      gross_margin_shock_pp: Number((shocks.gmPp / 100).toFixed(4)),
      interest_rate_shock_bps: shocks.rateBps,
      dso_shock_days: shocks.dsoDays,
      dio_shock_days: shocks.dioDays,
      top_n: 100,
    }),
    [periodEndDate, periodType, shocks, statementScope],
  );

  React.useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setIsCalculating(true);
      setErrorMsg(null);
      try {
        const res = await apiClient.postStressTest(payload, controller.signal);
        if (!controller.signal.aborted) {
          setResult(res);
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          setErrorMsg(
            err instanceof Error
              ? err.message
              : "Không thể chạy mô phỏng kịch bản sốc.",
          );
        }
      } finally {
        if (!controller.signal.aborted) {
          setIsCalculating(false);
        }
      }
    }, 250);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [payload]);

  const isExtreme =
    shocks.revPct <= -40 || shocks.rateBps >= 600 || shocks.dsoDays >= 90;

  const issuerList = React.useMemo(() => {
    if (!result) return [];
    const base =
      activeTab === "NEWLY"
        ? result.newly_distressed_issuers
        : result.top_stressed_issuers;
    if (!selectedSectorCode) return base;
    return base.filter((it) => it.icb_code_level2 === selectedSectorCode);
  }, [activeTab, result, selectedSectorCode]);

  const selectedSectorName = React.useMemo(() => {
    if (!selectedSectorCode || !result) return null;
    return (
      result.sector_summary.find((s) => s.icb_code_level2 === selectedSectorCode)
        ?.icb_name_level2 ?? selectedSectorCode
    );
  }, [result, selectedSectorCode]);

  const handleExportCsv = () => {
    const headers = [
      "Mã",
      "Doanh nghiệp",
      "Ngành",
      "Tổng nợ vay (tỷ)",
      "ICR cơ sở (x)",
      "ICR sau sốc (x)",
      "EBIT TTM (tỷ)",
      "EBIT sau sốc (tỷ)",
      "CFO TTM (tỷ)",
      "CFO sau sốc (tỷ)",
    ];
    const rows = issuerList.map((it) => [
      it.ticker,
      it.company_name_vn ?? "",
      it.icb_name_level2 ?? "",
      it.total_debt !== null && it.total_debt !== undefined
        ? (it.total_debt / 1e9).toFixed(1)
        : "",
      it.interest_coverage ?? "",
      it.stressed_interest_coverage ?? "",
      it.ebit_ttm !== null && it.ebit_ttm !== undefined
        ? (it.ebit_ttm / 1e9).toFixed(1)
        : "",
      it.stressed_ebit_ttm !== null && it.stressed_ebit_ttm !== undefined
        ? (it.stressed_ebit_ttm / 1e9).toFixed(1)
        : "",
      it.cfo_ttm !== null && it.cfo_ttm !== undefined
        ? (it.cfo_ttm / 1e9).toFixed(1)
        : "",
      it.stressed_cfo_ttm !== null && it.stressed_cfo_ttm !== undefined
        ? (it.stressed_cfo_ttm / 1e9).toFixed(1)
        : "",
    ]);

    const csv = buildCsvContent(headers, rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `finintel-stress-test.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Mô phỏng kịch bản sốc tài chính (IMF WP/15/216)
          </h1>
          <p className="mt-0.5 text-xs text-ink-muted">
            Phạm vi {STATEMENT_SCOPE_LABELS[statementScope] ?? statementScope} ·{" "}
            {PERIOD_TYPE_LABELS[periodType] ?? periodType} · Nợ rủi ro được định nghĩa khi hệ số khả năng trả lãi ICR &lt; 1,0x
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {PRESETS.map((p) => (
            <Button
              key={p.label}
              type="button"
              variant="outline"
              size="xs"
              onClick={() => setShocks(p.values)}
            >
              {p.label}
            </Button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 lg:hidden">
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-expanded={mobileControlsOpen}
          aria-controls="stress-controls-drawer"
          onClick={() => setMobileControlsOpen(true)}
        >
          <SlidersHorizontal className="size-3.5" />
          <span>Tham số cú sốc vĩ mô</span>
        </Button>
      </div>

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        {/* Left Shock Controls (Desktop) / Collapsible Drawer (Mobile < lg) */}
        <div
          id="stress-controls-drawer"
          role={mobileControlsOpen ? "dialog" : undefined}
          aria-modal={mobileControlsOpen ? true : undefined}
          aria-label={mobileControlsOpen ? "Tham số cú sốc vĩ mô" : undefined}
          className={cn(
            "lg:block lg:w-72 lg:shrink-0",
            mobileControlsOpen
              ? "fixed inset-0 z-40 flex bg-ink/30 lg:static lg:inset-auto lg:z-auto lg:bg-transparent"
              : "hidden",
          )}
          onClick={() => setMobileControlsOpen(false)}
        >
          <Card
            className="flex h-full w-80 max-w-[85vw] flex-col overflow-y-auto rounded-none border-r border-border lg:h-auto lg:w-full lg:max-w-none lg:overflow-visible lg:rounded lg:border"
            onClick={(e) => e.stopPropagation()}
          >
            <CardHeader className="py-2.5">
              <CardTitle className="text-[14px]">Tham số cú sốc vĩ mô</CardTitle>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => setShocks(DEFAULT_SHOCKS)}
                >
                  <RotateCcw className="size-3" />
                  Về 0
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="lg:hidden"
                  aria-label="Đóng bảng tham số sốc"
                  onClick={() => setMobileControlsOpen(false)}
                >
                  <X className="size-4" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 p-3.5">
            <DualShockControl
              id="shock-rev"
              label="Biến động doanh thu"
              unit="%"
              min={-80}
              max={50}
              step={1}
              value={shocks.revPct}
              onChange={(v) =>
                setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", revPct: v }))
              }
            />
            <DualShockControl
              id="shock-gm"
              label="Biến động biên gộp"
              unit="đ%"
              min={-50}
              max={20}
              step={0.5}
              value={shocks.gmPp}
              onChange={(v) =>
                setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", gmPp: v }))
              }
            />
            <DualShockControl
              id="shock-rate"
              label="Cú sốc lãi suất vay"
              unit="bps"
              min={-500}
              max={1500}
              step={25}
              value={shocks.rateBps}
              onChange={(v) =>
                setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", rateBps: v }))
              }
            />
            <DualShockControl
              id="shock-dso"
              label="Tăng số ngày thu tiền (DSO)"
              unit="ngày"
              min={-90}
              max={180}
              step={5}
              value={shocks.dsoDays}
              onChange={(v) =>
                setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", dsoDays: v }))
              }
            />
            <DualShockControl
              id="shock-dio"
              label="Tăng số ngày tồn kho (DIO)"
              unit="ngày"
              min={-90}
              max={180}
              step={5}
              value={shocks.dioDays}
              onChange={(v) =>
                setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", dioDays: v }))
              }
            />

            {isExtreme ? (
              <div className="flex items-start gap-1.5 rounded border border-risk-mid/35 bg-risk-mid-soft p-2 text-[11px] text-risk-mid">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                <span>
                  Mức sốc cực đoan so với lịch sử doanh nghiệp niêm yết Việt Nam; kết quả phản ánh kịch bản căng thẳng hệ thống.
                </span>
              </div>
            ) : null}

            <div className="flex items-center gap-2 pt-1">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="flex-1"
                disabled={!result}
                onClick={() => result && setPinnedA(result)}
              >
                <Pin className="size-3.5" />
                {pinnedA ? "Cập nhật kịch bản A" : "Ghim kịch bản A"}
              </Button>
              {pinnedA ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label="Bỏ ghim kịch bản A"
                  onClick={() => setPinnedA(null)}
                >
                  <PinOff className="size-3.5" />
                </Button>
              ) : null}
            </div>
            <div className="pt-1 lg:hidden">
              <Button
                type="button"
                size="sm"
                className="w-full"
                onClick={() => setMobileControlsOpen(false)}
              >
                Xem kết quả mô phỏng
              </Button>
            </div>
          </CardContent>
        </Card>
        </div>

        {/* Results Panel */}
        <div className="min-w-0 flex-1 space-y-4">
          {errorMsg && !result ? (
            <ErrorState message={errorMsg} />
          ) : !result ? (
            <PageSkeleton />
          ) : (
            <div className="relative space-y-4">
              {isCalculating ? (
                <div className="flex items-center justify-end">
                  <span className="rounded border border-accent-ink/30 bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent-ink">
                    Đang tính lại...
                  </span>
                </div>
              ) : null}

              {errorMsg ? <ErrorState message={errorMsg} /> : null}

              {/* Top KPI Cards */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <Card>
                  <CardContent className="p-3.5">
                    <p className="text-xs font-medium text-ink-muted">
                      Tỷ trọng nợ rủi ro (ICR &lt; 1)
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-risk-high">
                      {formatPercent(result.stressed_debt_at_risk_pct)}
                    </p>
                    <p className="num mt-1 text-[11px] text-ink-muted">
                      Cơ sở: {formatPercent(result.baseline_debt_at_risk_pct)} · Thay đổi{" "}
                      {formatPercentPoints(
                        result.stressed_debt_at_risk_pct -
                          result.baseline_debt_at_risk_pct,
                        1,
                      )}
                    </p>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-3.5">
                    <p className="text-xs font-medium text-ink-muted">
                      DN mới gặp khó khăn trả lãi
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-ink">
                      {formatNumber(result.newly_distressed_count, 0)}
                    </p>
                    <p className="num mt-1 text-[11px] text-ink-muted">
                      Trên tổng {formatNumber(result.total_issuers_evaluated, 0)} DN được đánh giá
                    </p>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-3.5">
                    <p className="text-xs font-medium text-ink-muted">
                      Tổng dư nợ vay đánh giá
                    </p>
                    <p className="num mt-1 text-2xl font-semibold text-ink">
                      {formatVndBillions(result.total_debt_sum, { decimals: 0 })}
                    </p>
                    <p className="mt-1 text-[11px] text-ink-muted">
                      Đơn vị: tỷ đồng
                    </p>
                  </CardContent>
                </Card>
              </div>

              {pinnedA ? (
                <Card className="border-accent-ink/40 bg-accent-soft/30">
                  <CardContent className="flex flex-wrap items-center justify-between gap-3 p-3 text-xs">
                    <div>
                      <span className="font-semibold text-accent-ink">
                        So sánh Kịch bản A ({pinnedA.scenario_name}) vs Hiện tại:
                      </span>{" "}
                      <span className="num text-ink">
                        Nợ rủi ro {formatPercent(pinnedA.stressed_debt_at_risk_pct)} →{" "}
                        {formatPercent(result.stressed_debt_at_risk_pct)} (
                        {formatPercentPoints(
                          result.stressed_debt_at_risk_pct -
                            pinnedA.stressed_debt_at_risk_pct,
                          1,
                        )}
                        ) · DN mới khó khăn: {pinnedA.newly_distressed_count} →{" "}
                        {result.newly_distressed_count}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              ) : null}

              <StressSectorBarChart
                sectors={result.sector_summary}
                selectedSectorCode={selectedSectorCode}
                onToggleSector={(code) =>
                  setSelectedSectorCode((prev) => (prev === code ? null : code))
                }
              />

              <Card>
                <CardHeader className="flex-wrap gap-2 py-2.5">
                  <div className="inline-flex rounded border border-border bg-canvas p-0.5 text-xs">
                    <button
                      type="button"
                      onClick={() => setActiveTab("NEWLY")}
                      className={cn(
                        "rounded px-2.5 py-1 font-medium",
                        activeTab === "NEWLY"
                          ? "bg-accent-ink text-white"
                          : "text-ink-muted hover:text-ink",
                      )}
                    >
                      DN mới gặp khó ({result.newly_distressed_issuers.length})
                    </button>
                    <button
                      type="button"
                      onClick={() => setActiveTab("TOP")}
                      className={cn(
                        "rounded px-2.5 py-1 font-medium",
                        activeTab === "TOP"
                          ? "bg-accent-ink text-white"
                          : "text-ink-muted hover:text-ink",
                      )}
                    >
                      Nợ rủi ro lớn nhất ({result.top_stressed_issuers.length})
                    </button>
                  </div>

                  <div className="flex items-center gap-2">
                    {selectedSectorName ? (
                      <Button
                        type="button"
                        variant="secondary"
                        size="xs"
                        onClick={() => setSelectedSectorCode(null)}
                      >
                        Đang lọc: {selectedSectorName} ×
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      disabled={issuerList.length === 0}
                      onClick={handleExportCsv}
                    >
                      <Download className="size-3" />
                      Xuất CSV
                    </Button>
                  </div>
                </CardHeader>

                <CardContent className="p-0">
                  {issuerList.length === 0 ? (
                    <div className="p-6">
                      <EmptyState
                        title="Không có doanh nghiệp trong nhóm này"
                        description="Thử tăng mức cú sốc vĩ mô hoặc bỏ bộ lọc ngành để xem danh sách."
                      />
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full border-collapse text-xs">
                        <thead>
                          <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                            <th scope="col" className="px-3 py-2 text-left font-semibold">
                              Mã
                            </th>
                            <th scope="col" className="px-3 py-2 text-left font-semibold">
                              Ngành
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              Tổng nợ (tỷ)
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              ICR (Cơ sở → Sốc)
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              EBIT TTM (tỷ)
                            </th>
                            <th scope="col" className="px-3 py-2 text-right font-semibold">
                              CFO TTM (tỷ)
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {issuerList.map((it) => (
                            <tr
                              key={it.issuer_id}
                              className="border-b border-border/60 hover:bg-surface-muted/40"
                            >
                              <td className="px-3 py-2">
                                <Link
                                  href={`/companies/${it.ticker}`}
                                  className="font-mono font-semibold text-accent-ink hover:underline"
                                >
                                  {it.ticker}
                                </Link>
                                <div className="max-w-[180px] truncate text-[11px] text-ink-muted">
                                  {it.company_name_vn ?? ""}
                                </div>
                              </td>
                              <td className="max-w-[150px] truncate px-3 py-2 text-ink-muted">
                                {it.icb_name_level2 ?? "—"}
                              </td>
                              <td className="num px-3 py-2 text-right">
                                {formatVndBillions(it.total_debt, { decimals: 0 })}
                              </td>
                              <td className="num px-3 py-2 text-right">
                                <span className="text-ink-muted">
                                  {formatMultiple(it.interest_coverage)} →{" "}
                                </span>
                                <strong
                                  className={
                                    (it.stressed_interest_coverage ?? 0) < 1
                                      ? "text-risk-high"
                                      : "text-ink"
                                  }
                                >
                                  {formatMultiple(it.stressed_interest_coverage)}
                                </strong>
                              </td>
                              <td className="num px-3 py-2 text-right">
                                <span className="text-ink-muted">
                                  {formatVndBillions(it.ebit_ttm, { accountingNegative: true })} →{" "}
                                </span>
                                <span>
                                  {formatVndBillions(it.stressed_ebit_ttm, { accountingNegative: true })}
                                </span>
                              </td>
                              <td className="num px-3 py-2 text-right">
                                <span className="text-ink-muted">
                                  {formatVndBillions(it.cfo_ttm, { accountingNegative: true })} →{" "}
                                </span>
                                <span>
                                  {formatVndBillions(it.stressed_cfo_ttm, { accountingNegative: true })}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
