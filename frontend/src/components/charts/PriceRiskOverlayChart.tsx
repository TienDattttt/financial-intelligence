"use client";

import * as React from "react";
import type { SeriesOption } from "echarts";
import { EChartBox, type EChartsOption } from "@/components/charts/EChartBox";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { ErrorState } from "@/components/layout/StateBlocks";
import { Card, CardContent } from "@/components/ui/card";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import {
  formatCreditTrio,
  formatDateBasisLabel,
  formatRegulatoryEventStage,
  formatRegulatoryEventType,
  formatSharesSourceLabel,
  getRiskTierMeta,
  getValuationFlagMeta,
} from "@/lib/domain-labels";
import { type ChartPalette, useChartTheme } from "@/lib/echarts-theme";
import {
  formatDateVi,
  formatMultiple,
  formatPercent,
  formatVndBillions,
  formatVndPerShare,
} from "@/lib/format";
import type {
  CompanyMarketOverlayResponse,
  DrawdownWindowMetrics,
  ModelRiskMarker,
  PriceOverlayPoint,
  RegulatoryEventMarker,
  WarningSummary,
} from "@/lib/market-overlay-types";

export interface LatestValuationCardsData {
  tradeDateLabel: string;
  closeRawLabel: string;
  marketCapLabel: string;
  marketCapSubtext: string;
  peLabel: string;
  peSubtext: string;
  peValid: boolean;
  pbLabel: string;
  pbSubtext: string;
  pbValid: boolean;
  sharesSourceLabel: string;
}

export function formatPctFromPercentValue(
  pctValue: number | null | undefined,
  decimals = 1,
): string {
  if (pctValue === null || pctValue === undefined || !Number.isFinite(pctValue)) {
    return "N/A";
  }
  return formatPercent(pctValue / 100, { signed: true, decimals });
}

export function formatLatestValuationCards(
  latest: PriceOverlayPoint | null | undefined,
): LatestValuationCardsData {
  if (!latest) {
    return {
      tradeDateLabel: "Chưa có phiên giao dịch",
      closeRawLabel: "N/A",
      marketCapLabel: "N/A",
      marketCapSubtext: "Chưa có dữ liệu vốn hóa Point-in-Time.",
      peLabel: "N/A",
      peSubtext: "Chưa có dữ liệu định giá P/E tại ngày giao dịch.",
      peValid: false,
      pbLabel: "N/A",
      pbSubtext: "Chưa có dữ liệu định giá P/B tại ngày giao dịch.",
      pbValid: false,
      sharesSourceLabel: "Chưa có dữ liệu cổ phiếu lưu hành",
    };
  }

  const sharesSourceLabel = formatSharesSourceLabel(latest.shares_source);
  const peFlagMeta = getValuationFlagMeta(latest.pe_flag);
  const pbFlagMeta = getValuationFlagMeta(latest.pb_flag);

  const hasMarketCap =
    typeof latest.market_cap_vnd === "number" && Number.isFinite(latest.market_cap_vnd);
  const marketCapLabel = hasMarketCap
    ? `${formatVndBillions(latest.market_cap_vnd, { decimals: 1 })} tỷ`
    : "N/A";
  const marketCapSubtext = hasMarketCap
    ? `Tính theo giá thực tế ${formatVndPerShare(latest.close_raw)} VND · ${sharesSourceLabel}`
    : getValuationFlagMeta(latest.shares_source === "MISSING_SHARES" ? "MISSING_SHARES" : null)
        .explanation;

  const hasPe =
    typeof latest.pe_ttm === "number" &&
    Number.isFinite(latest.pe_ttm) &&
    (latest.pe_flag === null || latest.pe_flag === "VALID");
  const peLabel = hasPe ? formatMultiple(latest.pe_ttm, 2) : "N/A";
  const peSubtext = hasPe
    ? "Lợi nhuận 4 quý gần nhất tại thời điểm giao dịch"
    : peFlagMeta.explanation;

  const hasPb =
    typeof latest.pb_ratio === "number" &&
    Number.isFinite(latest.pb_ratio) &&
    (latest.pb_flag === null || latest.pb_flag === "VALID");
  const pbLabel = hasPb ? formatMultiple(latest.pb_ratio, 2) : "N/A";
  const pbSubtext = hasPb
    ? "Vốn chủ sở hữu công ty mẹ công bố tại thời điểm giao dịch"
    : pbFlagMeta.explanation;

  return {
    tradeDateLabel: formatDateVi(latest.trade_date),
    closeRawLabel: `${formatVndPerShare(latest.close_raw)} VND`,
    marketCapLabel,
    marketCapSubtext,
    peLabel,
    peSubtext,
    peValid: hasPe,
    pbLabel,
    pbSubtext,
    pbValid: hasPb,
    sharesSourceLabel,
  };
}

export function formatWarningLabelVi(summary: WarningSummary): string {
  if (summary.warning_source === "MODEL_RISK_TIER") {
    const tierToken = summary.warning_label.replace(/^Model\s+/i, "").trim();
    const meta = getRiskTierMeta(tierToken);
    return meta.tier ? `Mô hình nâng lên ${meta.badgeText}` : "Mô hình cảnh báo rủi ro cao";
  }
  return formatRegulatoryEventType(summary.warning_label).label;
}

export interface WarningDrawdownMetricView {
  key: string;
  label: string;
  returnText: string;
  maxDrawdownText: string;
  endDateText: string | null;
  isNegative: boolean;
}

export interface WarningDrawdownSummaryView {
  sourceTitle: string;
  warningDateText: string;
  alignedDateText: string | null;
  basePriceText: string;
  latestPriceText: string;
  latestDateText: string | null;
  windows: WarningDrawdownMetricView[];
}

function buildWindowView(
  key: string,
  label: string,
  metrics: DrawdownWindowMetrics | null | undefined,
): WarningDrawdownMetricView {
  if (!metrics) {
    return {
      key,
      label,
      returnText: "N/A",
      maxDrawdownText: "N/A",
      endDateText: null,
      isNegative: false,
    };
  }
  return {
    key,
    label,
    returnText: formatPctFromPercentValue(metrics.return_pct),
    maxDrawdownText: formatPctFromPercentValue(metrics.drawdown_from_marker_pct),
    endDateText: formatDateVi(metrics.end_trade_date),
    isNegative: metrics.return_pct < 0,
  };
}

export function extractWarningDrawdownSummary(
  overlay: CompanyMarketOverlayResponse | null | undefined,
): WarningDrawdownSummaryView | null {
  const summary = overlay?.earliest_warning_summary;
  if (!summary) return null;

  const windows: WarningDrawdownMetricView[] = [
    buildWindowView("T+90d", "Biến động T+90 ngày", summary.horizons?.["T+90d"]),
    buildWindowView("T+365d", "Biến động T+365 ngày", summary.horizons?.["T+365d"]),
  ];

  if (summary.to_first_regulatory_event) {
    windows.push(
      buildWindowView(
        "to_reg",
        "Từ cảnh báo đến sự kiện pháp lý",
        summary.to_first_regulatory_event,
      ),
    );
  }
  windows.push(buildWindowView("to_current", "Từ cảnh báo đến hiện tại", summary.to_current));

  return {
    sourceTitle: formatWarningLabelVi(summary),
    warningDateText: formatDateVi(summary.warning_date),
    alignedDateText: summary.aligned_trade_date ? formatDateVi(summary.aligned_trade_date) : null,
    basePriceText:
      summary.base_close_adj !== null ? `${formatVndPerShare(summary.base_close_adj)} VND` : "N/A",
    latestPriceText:
      summary.latest_close_adj !== null
        ? `${formatVndPerShare(summary.latest_close_adj)} VND`
        : "N/A",
    latestDateText: summary.latest_trade_date ? formatDateVi(summary.latest_trade_date) : null,
    windows,
  };
}

export function buildPriceOverlayOption(
  overlay: CompanyMarketOverlayResponse | null | undefined,
  palette: ChartPalette,
): EChartsOption {
  const seriesPoints = overlay?.price_series ?? [];
  if (!overlay || seriesPoints.length === 0) {
    return {};
  }

  const dates = seriesPoints.map((p) => p.trade_date);
  const pointByDate = new Map<string, PriceOverlayPoint>(
    seriesPoints.map((p) => [p.trade_date, p]),
  );

  const modelMarkersByDate = new Map<string, ModelRiskMarker[]>();
  for (const mm of overlay.model_markers) {
    const dt = mm.aligned_trade_date;
    if (!dt) continue;
    const list = modelMarkersByDate.get(dt) ?? [];
    list.push(mm);
    modelMarkersByDate.set(dt, list);
  }

  const regMarkersByDate = new Map<string, RegulatoryEventMarker[]>();
  for (const rm of overlay.regulatory_markers) {
    const dt = rm.aligned_trade_date;
    if (!dt) continue;
    const list = regMarkersByDate.get(dt) ?? [];
    list.push(rm);
    regMarkersByDate.set(dt, list);
  }

  const modelScatterData = overlay.model_markers
    .filter((mm) => mm.aligned_trade_date && mm.close_adj_at_marker !== null)
    .map((mm) => ({
      value: [mm.aligned_trade_date as string, mm.close_adj_at_marker as number],
      marker: mm,
      itemStyle: {
        color: mm.risk_tier === "R5" ? palette.riskHigh : palette.riskMid,
        borderColor: palette.surface,
        borderWidth: 2,
      },
      label: {
        show: true,
        position: "top" as const,
        formatter: `Cảnh báo ${mm.risk_tier}`,
        color: mm.risk_tier === "R5" ? palette.riskHigh : palette.riskMid,
        fontSize: 10,
        fontWeight: 600,
      },
    }));

  const regScatterData = overlay.regulatory_markers
    .filter((rm) => rm.aligned_trade_date && rm.close_adj_at_marker !== null)
    .map((rm) => {
      const evMeta = formatRegulatoryEventType(rm.event_type);
      return {
        value: [rm.aligned_trade_date as string, rm.close_adj_at_marker as number],
        marker: rm,
        itemStyle: {
          color: palette.riskHigh,
          borderColor: palette.surface,
          borderWidth: 2,
        },
        label: {
          show: true,
          position: "bottom" as const,
          formatter: evMeta.shortLabel,
          color: palette.riskHigh,
          fontSize: 10,
          fontWeight: 600,
        },
      };
    });

  const earliestDate = overlay.earliest_warning_summary?.aligned_trade_date;
  const earliestDrawdown =
    overlay.earliest_warning_summary?.to_current?.return_pct ??
    overlay.earliest_warning_summary?.horizons?.["T+365d"]?.return_pct ??
    null;

  const series: SeriesOption[] = [
    {
      name: "Giá đóng cửa điều chỉnh (VND)",
      type: "line",
      showSymbol: false,
      symbolSize: 4,
      lineStyle: { color: palette.company, width: 2 },
      itemStyle: { color: palette.company },
      areaStyle: {
        color: palette.theme === "dark" ? "rgba(102, 157, 246, 0.10)" : "rgba(30, 58, 95, 0.08)",
      },
      data: seriesPoints.map((p) => p.close_adj),
      ...(earliestDate && pointByDate.has(earliestDate)
        ? {
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { color: palette.riskHigh, type: "dashed", width: 1.25 },
              label: {
                show: true,
                position: "insideEndTop",
                color: palette.riskHigh,
                fontSize: 10,
                formatter:
                  earliestDrawdown !== null
                    ? `Mốc đầu: ${formatPctFromPercentValue(earliestDrawdown)}`
                    : "Mốc cảnh báo đầu",
              },
              data: [{ xAxis: earliestDate }],
            },
          }
        : {}),
    },
  ];

  if (modelScatterData.length > 0) {
    series.push({
      name: "Mô hình nâng bậc R4/R5",
      type: "scatter",
      symbol: "diamond",
      symbolSize: 13,
      z: 8,
      data: modelScatterData,
    });
  }

  if (regScatterData.length > 0) {
    series.push({
      name: "Sự kiện pháp lý / Kiểm toán",
      type: "scatter",
      symbol: "triangle",
      symbolSize: 14,
      z: 9,
      data: regScatterData,
    });
  }

  return {
    grid: { left: 68, right: 24, top: 44, bottom: 72 },
    legend: { top: 4, right: 8 },
    tooltip: {
      trigger: "axis",
      formatter: (raw: unknown) => {
        const list = Array.isArray(raw) ? raw : [raw];
        const first = list[0] as { axisValue?: string; name?: string } | undefined;
        const tradeDate = first?.axisValue ?? first?.name ?? "";
        const pt = pointByDate.get(tradeDate);
        if (!pt) return "";

        const lines: string[] = [
          `<div style="font-weight:600;margin-bottom:4px">Phiên ${formatDateVi(pt.trade_date)}</div>`,
          `<div>Giá điều chỉnh (đồ thị): <b>${formatVndPerShare(pt.close_adj)} VND</b></div>`,
          `<div>Giá thực tế PIT: <b>${formatVndPerShare(pt.close_raw)} VND</b></div>`,
          `<div>Drawdown từ đỉnh: <b>${formatPctFromPercentValue(pt.running_drawdown_pct)}</b></div>`,
        ];

        if (pt.market_cap_vnd !== null) {
          lines.push(
            `<div>Vốn hóa PIT: <b>${formatVndBillions(pt.market_cap_vnd, { decimals: 1 })} tỷ</b></div>`,
          );
        }
        if (pt.pe_ttm !== null && (pt.pe_flag === null || pt.pe_flag === "VALID")) {
          lines.push(`<div>P/E (TTM): <b>${formatMultiple(pt.pe_ttm, 2)}</b></div>`);
        }
        if (pt.pb_ratio !== null && (pt.pb_flag === null || pt.pb_flag === "VALID")) {
          lines.push(`<div>P/B: <b>${formatMultiple(pt.pb_ratio, 2)}</b></div>`);
        }

        const mms = modelMarkersByDate.get(tradeDate) ?? [];
        for (const mm of mms) {
          const trio = formatCreditTrio({
            risk_tier: mm.risk_tier,
            risk_market_percentile: mm.risk_market_percentile,
            calibrated_distress_prob: mm.calibrated_distress_prob,
            relative_risk_lift: mm.relative_risk_lift,
          });
          const t90 = mm.drawdowns?.horizons?.["T+90d"]?.return_pct;
          const t365 = mm.drawdowns?.horizons?.["T+365d"]?.return_pct;
          lines.push(
            `<div style="margin-top:6px;padding-top:4px;border-top:1px solid ${palette.border}">` +
              `<b style="color:${palette.riskHigh}">Mô hình nâng lên ${mm.risk_tier}</b> ` +
              `(${formatDateBasisLabel(mm.date_basis)}: ${formatDateVi(mm.disclosure_date)})<br/>` +
              `1Y PD: <b>${trio.pdLabel}</b> · ${trio.topPercentLabel}` +
              (trio.liftLabel ? ` · ${trio.liftLabel}` : "") +
              (t90 !== null && t90 !== undefined
                ? `<br/>T+90d: <b>${formatPctFromPercentValue(t90)}</b>`
                : "") +
              (t365 !== null && t365 !== undefined
                ? ` · T+365d: <b>${formatPctFromPercentValue(t365)}</b>`
                : "") +
              `</div>`,
          );
        }

        const rms = regMarkersByDate.get(tradeDate) ?? [];
        for (const rm of rms) {
          const evMeta = formatRegulatoryEventType(rm.event_type);
          const stageLabel = formatRegulatoryEventStage(rm.event_stage);
          const toCur = rm.drawdowns?.to_current?.return_pct;
          lines.push(
            `<div style="margin-top:6px;padding-top:4px;border-top:1px solid ${palette.border}">` +
              `<b style="color:${palette.riskHigh}">${evMeta.label}</b> (${stageLabel})<br/>` +
              `Ngày quyết định: ${formatDateVi(rm.event_date)}` +
              (toCur !== null && toCur !== undefined
                ? ` · Đến hiện tại: <b>${formatPctFromPercentValue(toCur)}</b>`
                : "") +
              `</div>`,
          );
        }

        return lines.join("");
      },
    },
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: false,
      axisLabel: {
        formatter: (val: string) => formatDateVi(val),
      },
    },
    yAxis: {
      type: "value",
      name: "Giá (VND)",
      scale: true,
      axisLabel: {
        formatter: (val: number) => formatVndPerShare(val),
      },
    },
    dataZoom: [
      { type: "inside", start: 0, end: 100 },
      {
        type: "slider",
        height: 22,
        bottom: 8,
        borderColor: palette.border,
        textStyle: { color: palette.inkMuted, fontSize: 10 },
      },
    ],
    series,
  };
}

export function buildPriceOverlayTableRows(
  overlay: CompanyMarketOverlayResponse | null | undefined,
  maxRows = 240,
): string[][] {
  const series = overlay?.price_series ?? [];
  if (!overlay || series.length === 0) return [];

  const priorityDates = new Set<string>();
  for (const mm of overlay.model_markers) {
    if (mm.aligned_trade_date) priorityDates.add(mm.aligned_trade_date);
  }
  for (const rm of overlay.regulatory_markers) {
    if (rm.aligned_trade_date) priorityDates.add(rm.aligned_trade_date);
  }

  const picked: PriceOverlayPoint[] = [];
  if (series.length <= maxRows) {
    picked.push(...[...series].reverse());
  } else {
    const step = Math.max(1, Math.floor(series.length / maxRows));
    const seen = new Set<string>();
    for (let i = series.length - 1; i >= 0; i -= 1) {
      const pt = series[i];
      if (i === series.length - 1 || i === 0 || priorityDates.has(pt.trade_date) || i % step === 0) {
        if (!seen.has(pt.trade_date)) {
          picked.push(pt);
          seen.add(pt.trade_date);
        }
      }
      if (picked.length >= maxRows) break;
    }
  }

  return picked.map((pt) => [
    formatDateVi(pt.trade_date),
    formatVndPerShare(pt.close_adj),
    formatVndPerShare(pt.close_raw),
    pt.market_cap_vnd !== null ? formatVndBillions(pt.market_cap_vnd, { decimals: 1 }) : "N/A",
    pt.pe_ttm !== null && (pt.pe_flag === null || pt.pe_flag === "VALID")
      ? formatMultiple(pt.pe_ttm, 2)
      : "N/A",
    pt.pb_ratio !== null && (pt.pb_flag === null || pt.pb_flag === "VALID")
      ? formatMultiple(pt.pb_ratio, 2)
      : "N/A",
    formatPctFromPercentValue(pt.running_drawdown_pct),
  ]);
}

export function PitValuationStrip({
  latestValuation,
  isLoading = false,
}: {
  latestValuation: PriceOverlayPoint | null | undefined;
  isLoading?: boolean;
}) {
  if (isLoading) {
    return (
      <section
        aria-label="Đang tải định giá Point-in-Time"
        className="grid grid-cols-1 gap-3 sm:grid-cols-3"
      >
        {[0, 1, 2].map((idx) => (
          <Card key={idx}>
            <CardContent className="p-3.5">
              <div className="h-3 w-28 animate-pulse rounded bg-surface-muted" />
              <div className="mt-2 h-7 w-24 animate-pulse rounded bg-surface-muted" />
              <div className="mt-2 h-3 w-full animate-pulse rounded bg-surface-muted" />
            </CardContent>
          </Card>
        ))}
      </section>
    );
  }

  const cards = formatLatestValuationCards(latestValuation);

  return (
    <section aria-label="Định giá Point-in-Time tại phiên gần nhất" className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <Card>
        <CardContent className="p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1 text-xs font-medium text-ink-muted">
              <span>Vốn hóa thị trường (PIT)</span>
              <InfoTooltip
                text="Tính từ giá đóng cửa thực tế chưa điều chỉnh chia tách nhân số cổ phiếu lưu hành cùng thời điểm."
                label="Giải thích Vốn hóa Point-in-Time"
              />
            </span>
            <span className="num text-[11px] text-ink-subtle">{cards.tradeDateLabel}</span>
          </div>
          <p className="num mt-1.5 text-[22px] font-semibold leading-7 text-ink">
            {cards.marketCapLabel}
          </p>
          <p className="mt-1.5 border-t border-border/60 pt-1.5 text-[11px] leading-4 text-ink-muted">
            {cards.marketCapSubtext}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1 text-xs font-medium text-ink-muted">
              <span>P/E (TTM · PIT)</span>
              <InfoTooltip
                text="Tỷ số Giá / Thu nhập 4 quý gần nhất khớp tiến ngày công bố BCTC (không nhìn trước tương lai)."
                label="Giải thích P/E Point-in-Time"
              />
            </span>
            <span className="num text-[11px] text-ink-subtle">Giá gốc {cards.closeRawLabel}</span>
          </div>
          <p className="num mt-1.5 text-[22px] font-semibold leading-7 text-ink">
            {cards.peLabel}
          </p>
          <p className="mt-1.5 border-t border-border/60 pt-1.5 text-[11px] leading-4 text-ink-muted">
            {cards.peSubtext}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1 text-xs font-medium text-ink-muted">
              <span>P/B (PIT)</span>
              <InfoTooltip
                text="Tỷ số Giá / Giá trị sổ sách VCSH công ty mẹ tính từ giá thực tế chưa điều chỉnh."
                label="Giải thích P/B Point-in-Time"
              />
            </span>
            <span className="num text-[11px] text-ink-subtle">{cards.tradeDateLabel}</span>
          </div>
          <p className="num mt-1.5 text-[22px] font-semibold leading-7 text-ink">
            {cards.pbLabel}
          </p>
          <p className="mt-1.5 border-t border-border/60 pt-1.5 text-[11px] leading-4 text-ink-muted">
            {cards.pbSubtext}
          </p>
        </CardContent>
      </Card>
    </section>
  );
}

export const PRICE_OVERLAY_CHART_SUBTITLE =
  "Đường giá điều chỉnh chia tách/cổ tức · Định giá P/E, P/B, Vốn hóa chuẩn thời điểm công bố (không nhìn trước)";

export function PriceRiskOverlayChart({
  ticker,
  overlay,
  isLoading = false,
  isError = false,
  errorMessage,
  onRetry,
}: {
  ticker: string;
  overlay: CompanyMarketOverlayResponse | null | undefined;
  isLoading?: boolean;
  isError?: boolean;
  errorMessage?: string;
  onRetry?: () => void;
}) {
  const { palette } = useChartTheme();

  const option = React.useMemo(
    () => buildPriceOverlayOption(overlay, palette),
    [overlay, palette],
  );
  const warningSummary = React.useMemo(
    () => extractWarningDrawdownSummary(overlay),
    [overlay],
  );
  const tableRows = React.useMemo(() => buildPriceOverlayTableRows(overlay), [overlay]);

  if (isLoading) {
    return (
      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="h-4 w-72 animate-pulse rounded bg-surface-muted" />
          <div className="h-[320px] w-full animate-pulse rounded bg-surface-muted" />
        </CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <ErrorState
        message={errorMessage ?? "Không thể tải lớp phủ giá cổ phiếu và mốc cảnh báo."}
        onRetry={onRetry}
      />
    );
  }

  const hasSeries = (overlay?.price_series.length ?? 0) > 0;
  const chartTitle = warningSummary
    ? `Lớp phủ Giá cổ phiếu & Cảnh báo sớm ${ticker}: ${warningSummary.sourceTitle} (${warningSummary.warningDateText})`
    : `Diễn biến giá cổ phiếu điều chỉnh & Giám sát rủi ro (${ticker})`;

  return (
    <div className="space-y-3">
      <EChartBox
        title={chartTitle}
        subtitle={PRICE_OVERLAY_CHART_SUBTITLE}
        sourceCaption="Nguồn: Dữ liệu giá giao dịch cuối ngày (EOD), BCTC công bố & Sổ sự kiện pháp lý · FinIntel"
        option={option}
        height={360}
        isEmpty={!hasSeries}
        emptyMessage={`Chưa có dữ liệu giá giao dịch lịch sử (EOD) cho mã ${ticker} trong lát cắt hiện tại (thường gặp ở cổ phiếu UPCOM hoặc mã hạn chế dữ liệu thị trường). Hệ thống vẫn giữ nguyên đầy đủ BCTC, 38 tỷ số và 9 cờ đỏ VAS.`}
        tableHeaders={[
          "Ngày GD",
          "Giá ĐC (VND)",
          "Giá thực tế (VND)",
          "Vốn hóa (tỷ)",
          "P/E (TTM)",
          "P/B",
          "Drawdown từ đỉnh",
        ]}
        tableRows={tableRows}
        headerRight={
          overlay && (overlay.model_markers.length > 0 || overlay.regulatory_markers.length > 0) ? (
            <div className="flex flex-wrap items-center gap-1.5">
              {overlay.model_markers.map((mm) => (
                <RiskBadge
                  key={`${mm.marker_kind}-${mm.period_end_date}`}
                  tone={mm.risk_tier === "R5" ? "high" : "mid"}
                  label={`Mô hình ${mm.risk_tier}: ${formatDateVi(mm.aligned_trade_date ?? mm.disclosure_date)}`}
                />
              ))}
              {overlay.regulatory_markers.slice(0, 2).map((rm, idx) => {
                const ev = formatRegulatoryEventType(rm.event_type);
                return (
                  <RiskBadge
                    key={`${rm.event_type}-${rm.event_date}-${idx}`}
                    tone={ev.tone}
                    label={`${ev.shortLabel}: ${formatDateVi(rm.event_date)}`}
                  />
                );
              })}
            </div>
          ) : null
        }
      />

      {warningSummary ? (
        <Card>
          <CardContent className="space-y-2.5 p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <div>
                <span className="font-semibold text-ink">
                  Kiểm chứng hậu cảnh báo — {warningSummary.sourceTitle}
                </span>
                <span className="ml-2 text-ink-muted">
                  Khớp phiên {warningSummary.alignedDateText ?? warningSummary.warningDateText} ·
                  Giá cơ sở {warningSummary.basePriceText}
                  {warningSummary.latestDateText
                    ? ` → Hiện tại (${warningSummary.latestDateText}): ${warningSummary.latestPriceText}`
                    : ""}
                </span>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
              {warningSummary.windows.map((w) => (
                <div
                  key={w.key}
                  className="rounded-lg border border-border bg-canvas p-2.5 text-xs"
                >
                  <p className="text-[11px] text-ink-muted">{w.label}</p>
                  <p
                    className={`num mt-1 text-base font-semibold ${
                      w.returnText === "N/A"
                        ? "text-ink-muted"
                        : w.isNegative
                          ? "text-risk-high"
                          : "text-risk-low"
                    }`}
                  >
                    {w.returnText}
                  </p>
                  <p className="num mt-0.5 text-[11px] text-ink-subtle">
                    Đáy sâu nhất: {w.maxDrawdownText}
                    {w.endDateText ? ` · đến ${w.endDateText}` : ""}
                  </p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
