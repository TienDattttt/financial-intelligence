"use client";

import { AlertTriangle, CheckCircle2, ExternalLink, Landmark } from "lucide-react";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { ErrorState } from "@/components/layout/StateBlocks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatDateVi,
  formatMultiple,
  formatVndBillions,
} from "@/lib/format";
import type { CompanyBondSignalsResponse } from "@/types/api";

const PRESSURE_META: Record<
  CompanyBondSignalsResponse["liquidity_pressure_status"],
  { label: string; tone: "low" | "mid" | "high" | "neutral" }
> = {
  NO_BONDS: { label: "Không dư nợ", tone: "neutral" },
  COVERED: { label: "Tiền mặt bao phủ", tone: "low" },
  WATCH: { label: "Theo dõi đáo hạn", tone: "mid" },
  HIGH_PRESSURE: { label: "Áp lực đáo hạn cao", tone: "high" },
  PAYMENT_DELAY: { label: "Chậm trả gốc/lãi", tone: "high" },
  MISSING_CASH_DATA: { label: "Thiếu dữ liệu tiền mặt", tone: "mid" },
};

const PROVIDER_LABELS: Record<string, string> = {
  hnx_cbonds: "HNX Cbonds",
  fiindata_bond: "FiinData Bond",
  wifeed_bond: "WiFeed Bond",
};

export function BondMaturityCard({
  data,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: {
  data?: CompanyBondSignalsResponse;
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
}) {
  const pressure = data
    ? (PRESSURE_META[data.liquidity_pressure_status] ?? PRESSURE_META.NO_BONDS)
    : PRESSURE_META.NO_BONDS;
  const providerLabel =
    (data?.source_provider && PROVIDER_LABELS[data.source_provider]) ||
    "HNX Cbonds";
  const safeSourceUrl =
    data?.source_url && data.source_url.startsWith("https://")
      ? data.source_url
      : null;

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2 py-2.5">
        <CardTitle className="flex items-center gap-1.5 text-[14px]">
          <Landmark className="size-4 text-accent-ink" aria-hidden="true" />
          <span>Áp lực đáo hạn trái phiếu 12 tháng</span>
        </CardTitle>
        {data && data.has_outstanding_bonds ? (
          <RiskBadge tone={pressure.tone} label={pressure.label} />
        ) : null}
      </CardHeader>

      <CardContent className="space-y-3 p-3.5 text-xs">
        {isLoading ? (
          <div className="space-y-2" aria-label="Đang tải tín hiệu trái phiếu">
            <div className="h-4 w-2/3 animate-pulse rounded bg-surface-muted" />
            <div className="h-12 w-full animate-pulse rounded bg-surface-muted" />
          </div>
        ) : isError ? (
          <ErrorState
            message={errorMessage ?? "Không thể tải dữ liệu trái phiếu doanh nghiệp."}
            onRetry={onRetry}
          />
        ) : !data || !data.has_outstanding_bonds ? (
          <div className="flex items-center gap-2 rounded-md border border-border bg-surface-muted/40 p-3 text-xs text-ink-muted">
            <CheckCircle2 className="size-4 shrink-0 text-risk-low" aria-hidden="true" />
            <span>Không có dư nợ trái phiếu lưu hành</span>
          </div>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-2.5">
              <div className="rounded border border-border bg-canvas/50 p-2.5">
                <dt className="text-[11px] text-ink-muted">
                  Tổng dư nợ trái phiếu (tỷ)
                </dt>
                <dd className="num mt-1 text-sm font-semibold text-ink">
                  {formatVndBillions(data.outstanding_bond_vnd, {
                    decimals: 1,
                    zeroAsZero: true,
                  })}{" "}
                  tỷ
                </dd>
              </div>
              <div className="rounded border border-border bg-canvas/50 p-2.5">
                <dt className="text-[11px] text-ink-muted">Đáo hạn 12 tháng (tỷ)</dt>
                <dd className="num mt-1 text-sm font-semibold text-ink">
                  {formatVndBillions(data.bond_maturity_12m_vnd, {
                    decimals: 1,
                    zeroAsZero: true,
                  })}{" "}
                  tỷ
                </dd>
              </div>
              <div className="rounded border border-border bg-canvas/50 p-2.5">
                <dt className="text-[11px] text-ink-muted">
                  Tiền &amp; ĐTNH hiện có (tỷ)
                </dt>
                <dd className="num mt-1 text-sm font-semibold text-ink">
                  {formatVndBillions(data.total_cash_and_st_investments_vnd, {
                    decimals: 1,
                    zeroAsZero: true,
                  })}{" "}
                  tỷ
                </dd>
              </div>
              <div className="rounded border border-border bg-canvas/50 p-2.5">
                <dt className="text-[11px] text-ink-muted">Đáo hạn 12T / Tiền mặt</dt>
                <dd
                  className={
                    (data.bond_maturity_to_cash_ratio ?? 0) >= 1
                      ? "num mt-1 text-sm font-semibold text-risk-high"
                      : "num mt-1 text-sm font-semibold text-ink"
                  }
                >
                  {formatMultiple(data.bond_maturity_to_cash_ratio, 2)}
                </dd>
              </div>
            </dl>

            {data.bond_payment_delay_flag ? (
              <div className="flex items-start gap-2 rounded-md border border-risk-high/35 bg-risk-high-soft p-2.5 text-[11px] text-risk-high">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                <span>
                  Ghi nhận công bố chậm thanh toán gốc/lãi trái phiếu trên chuyên trang thông tin trái phiếu doanh nghiệp.
                </span>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2 text-[11px] text-ink-subtle">
              <span>
                Cập nhật:{" "}
                {data.as_of_date ? formatDateVi(data.as_of_date) : "Chưa rõ"}
              </span>
              {safeSourceUrl ? (
                <a
                  href={safeSourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 font-medium text-accent-ink hover:underline"
                >
                  <span>Nguồn: {providerLabel}</span>
                  <ExternalLink className="size-3" aria-hidden="true" />
                </a>
              ) : (
                <span>Nguồn: {providerLabel}</span>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
