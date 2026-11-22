"use client";

import * as React from "react";
import Link from "next/link";
import { Dialog } from "@base-ui/react/dialog";
import { BookOpen, FileText, X } from "lucide-react";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { ErrorState } from "@/components/layout/StateBlocks";
import { useAppState } from "@/components/providers/app-providers";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/primitives";
import { useMetaDictionary, useRatioLineage } from "@/hooks/use-finintel-queries";
import {
  DERIVATION_METHOD_LABELS,
  formatFormulaVi,
  formatSourceProviderLabel,
  getRatioStatusFlagMeta,
  getStatementMetricLabel,
  PUBLISHED_AT_BASIS_LABELS,
  STATEMENT_SCOPE_LABELS,
  TAXONOMY_LABELS,
} from "@/lib/domain-labels";
import {
  formatDateVi,
  formatPeriodLabel,
  formatRatioValue,
  formatVndBillions,
  formatVndPerShare,
} from "@/lib/format";
import type { LineageComponentItem } from "@/types/api";

const DENOMINATOR_MODE_LABELS: Record<string, string> = {
  AVERAGE_STOCK: "Bình quân đầu kỳ & cuối kỳ",
  POINT_IN_TIME_STOCK: "Tại thời điểm cuối kỳ",
  FLOW: "Lưu chuyển kỳ/TTM",
  PRIOR_YEAR_FLOW: "So với cùng kỳ năm trước",
  COMPOSITE: "Chỉ số tổng hợp",
};

function ComponentList({
  title,
  items,
}: {
  title: string;
  items: readonly LineageComponentItem[];
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold text-ink-muted">{title}</h3>
      {items.length === 0 ? (
        <p className="rounded border border-dashed border-border px-3 py-2 text-xs text-ink-subtle">
          Không có thành phần ghi nhận cho lát cắt này.
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((comp, idx) => {
            const isPerShare = comp.metric_id.includes("EPS");
            const formattedVal = isPerShare
              ? formatVndPerShare(comp.value)
              : formatVndBillions(comp.value, { accountingNegative: true });
            const metricLabel = getStatementMetricLabel(comp.metric_id);
            const derivationLabel =
              DERIVATION_METHOD_LABELS[comp.derivation_method] ??
              "Theo phương pháp chuẩn hóa VAS";
            const providerLabel = formatSourceProviderLabel(comp.source_provider);
            const basisLabel = comp.published_at_basis
              ? (PUBLISHED_AT_BASIS_LABELS[comp.published_at_basis] ??
                "Theo hồ sơ công bố")
              : null;

            return (
              <li
                key={`${comp.metric_id}-${comp.period_end_date}-${idx}`}
                className="rounded border border-border bg-surface-muted/40 p-2.5 text-xs"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="font-medium text-ink">{metricLabel}</span>
                  <span className="num shrink-0 font-semibold text-ink">
                    {formattedVal} {isPerShare ? "đ/cp" : "tỷ"}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-ink-muted">
                  <span>
                    Kỳ: {formatPeriodLabel(comp.period_end_date, comp.period_type)}
                  </span>
                  <span>
                    Chuẩn: {TAXONOMY_LABELS[comp.taxonomy_version] ?? comp.taxonomy_version}
                  </span>
                  {comp.published_at ? (
                    <span>Công bố: {formatDateVi(comp.published_at)}</span>
                  ) : null}
                  {comp.effective_available_at ? (
                    <span>Hiệu lực PIT: {formatDateVi(comp.effective_available_at)}</span>
                  ) : null}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-ink-muted">
                  <span>Phương thức: {derivationLabel}</span>
                  <span>Nguồn: {providerLabel}</span>
                  {basisLabel ? <span>Căn cứ ngày: {basisLabel}</span> : null}
                </div>
                <div className="mt-1 flex items-center justify-between text-[11px] text-ink-subtle">
                  <span className="truncate font-mono" title={comp.filing_id}>
                    Hồ sơ: {comp.filing_id}
                  </span>
                  {comp.canonical_content_sha256 ? (
                    <span
                      className="font-mono"
                      title={comp.canonical_content_sha256}
                    >
                      SHA: {comp.canonical_content_sha256.slice(0, 8)}
                    </span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function SourceDrawer() {
  const { sourceTarget, closeSourceDrawer, snapshotId } = useAppState();
  const lineageQuery = useRatioLineage(sourceTarget, snapshotId);
  const dictQuery = useMetaDictionary(snapshotId);

  const card = lineageQuery.data;
  const ratioDictItem = sourceTarget
    ? dictQuery.data?.ratios.find(
        (r) => r.ratio_name === (card?.ratio_name ?? sourceTarget.ratioName),
      )
    : undefined;
  const statusMeta = getRatioStatusFlagMeta(card?.status_flag);
  const formattedRatioValue = card
    ? formatRatioValue(card.ratio_value, ratioDictItem?.format_type ?? "NUMBER")
    : "N/A";

  return (
    <Dialog.Root
      open={Boolean(sourceTarget)}
      onOpenChange={(open) => {
        if (!open) closeSourceDrawer();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-ink/35 transition-opacity duration-150 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 motion-reduce:transition-none dark:bg-black/55" />
        <Dialog.Popup
          aria-modal="true"
          aria-label="Nguồn dữ liệu chỉ số"
          className="fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-[440px] flex-col border-l border-border-strong bg-surface text-ink shadow-[0_8px_32px_rgba(15,23,42,0.18)] transition-transform duration-150 ease-[cubic-bezier(0.2,0,0,1)] focus:outline-none data-[starting-style]:translate-x-full data-[ending-style]:translate-x-full motion-reduce:transition-none dark:shadow-[0_8px_32px_rgba(0,0,0,0.55)]"
        >
          {sourceTarget ? (
            <>
              <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3.5">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-accent-ink">
                    <FileText className="size-3.5" aria-hidden="true" />
                    <span>Nguồn dữ liệu chỉ số</span>
                  </div>
                  <Dialog.Title className="mt-0.5 truncate text-[15px] font-semibold text-ink">
                    {card?.display_name_vn ??
                      ratioDictItem?.display_name_vn ??
                      "Chi tiết chỉ số tài chính"}
                  </Dialog.Title>
                  <Dialog.Description className="mt-0.5 text-xs text-ink-muted">
                    {sourceTarget.ticker} ·{" "}
                    {card
                      ? formatPeriodLabel(card.period_end_date, card.period_type)
                      : formatPeriodLabel(
                          sourceTarget.periodEndDate,
                          sourceTarget.periodType,
                        )}{" "}
                    ·{" "}
                    {STATEMENT_SCOPE_LABELS[sourceTarget.statementScope] ??
                      sourceTarget.statementScope}
                    {card
                      ? ` · ${TAXONOMY_LABELS[card.taxonomy_version] ?? card.taxonomy_version}`
                      : ""}
                  </Dialog.Description>
                </div>
                <Dialog.Close
                  render={
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Đóng ngăn kéo nguồn"
                    />
                  }
                >
                  <X className="size-4" aria-hidden="true" />
                </Dialog.Close>
              </div>

              <div className="flex-1 space-y-4 overflow-y-auto p-4">
                {lineageQuery.isLoading ? (
                  <div className="space-y-3">
                    <Skeleton className="h-16 w-full" />
                    <Skeleton className="h-28 w-full" />
                    <Skeleton className="h-28 w-full" />
                  </div>
                ) : lineageQuery.isError ? (
                  <ErrorState
                    message={lineageQuery.error.message}
                    onRetry={() => lineageQuery.refetch()}
                  />
                ) : card ? (
                  <>
                    <div className="rounded border border-border bg-canvas p-3">
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <span className="text-xs text-ink-muted">Giá trị công bố</span>
                          <p className="num mt-0.5 text-xl font-semibold text-ink">
                            {formattedRatioValue}
                          </p>
                        </div>
                        <RiskBadge tone={statusMeta.tone} label={statusMeta.label} />
                      </div>
                      <p className="mt-2 text-xs text-ink">
                        <span className="font-medium text-ink-muted">Công thức: </span>
                        {formatFormulaVi(card.formula)}
                      </p>
                      <p className="mt-1 text-[11px] text-ink-muted">
                        Phương thức mẫu số:{" "}
                        {DENOMINATOR_MODE_LABELS[card.denominator_mode] ??
                          "Chuẩn hóa theo kỳ"}
                      </p>
                    </div>

                    <ComponentList
                      title="Thành phần tử số"
                      items={card.numerator_components}
                    />
                    <ComponentList
                      title="Thành phần mẫu số"
                      items={card.denominator_components}
                    />

                    <section className="space-y-1 rounded border border-border p-3 text-xs">
                      <p className="font-semibold text-ink-muted">
                        Hồ sơ neo &amp; kiểm chứng
                      </p>
                      <p className="font-mono text-[11px] text-ink">
                        Hồ sơ neo: {card.anchor_filing_id}
                      </p>
                      <p className="text-[11px] text-ink-muted">
                        Số hồ sơ nguồn tham gia: {card.source_filing_ids.length}
                      </p>
                      {card.orphan_filing_ids.length > 0 ? (
                        <p className="text-[11px] text-risk-mid">
                          Lưu ý: {card.orphan_filing_ids.length} hồ sơ tham chiếu chưa khớp đầy đủ trong bảng chi tiết.
                        </p>
                      ) : null}
                    </section>
                  </>
                ) : null}
              </div>

              <div className="border-t border-border bg-surface-muted/40 px-4 py-2.5">
                <Link
                  href="/methodology"
                  onClick={closeSourceDrawer}
                  className="inline-flex items-center gap-1.5 text-xs font-medium text-accent-ink hover:underline"
                >
                  <BookOpen className="size-3.5" aria-hidden="true" />
                  Xem định nghĩa đầy đủ trong Từ điển chỉ số →
                </Link>
              </div>
            </>
          ) : null}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
