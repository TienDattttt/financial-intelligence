"use client";

import * as React from "react";
import { FinancialTable } from "@/components/financial/FinancialTable";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { Select } from "@/components/ui/primitives";
import { SegmentedControl } from "@/components/ui/segmented-control";
import type {
  CompanyStatementsResponse,
  PeriodType,
  StatementType,
} from "@/types/api";

export function FinancialsTab({
  statementType,
  onChangeStatementType,
  periodType,
  periodsLimit,
  onChangePeriodsLimit,
  isLoading,
  isError,
  errorMessage,
  onRetry,
  data,
  compact,
}: {
  statementType: StatementType;
  onChangeStatementType: (next: StatementType) => void;
  periodType: PeriodType;
  periodsLimit: number;
  onChangePeriodsLimit: (next: number) => void;
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
  data: CompanyStatementsResponse | undefined;
  compact: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SegmentedControl
          ariaLabel="Chọn loại báo cáo tài chính"
          value={statementType}
          onChange={onChangeStatementType}
          options={[
            { value: "INCOME_STATEMENT", label: "Kết quả kinh doanh" },
            { value: "BALANCE_SHEET", label: "Cân đối kế toán" },
            { value: "CASH_FLOW", label: "Lưu chuyển tiền tệ" },
          ]}
        />

        <div className="flex flex-wrap items-center gap-2">
          {periodType === "QUARTER" ? (
            <span className="text-[11px] text-ink-muted">
              Hiển thị số liệu quý rời rạc (theo từng kỳ công bố)
            </span>
          ) : null}
          <Select
            aria-label="Số kỳ báo cáo hiển thị"
            value={String(periodsLimit)}
            onChange={(e) => onChangePeriodsLimit(Number(e.target.value) || 8)}
            className="h-8 text-xs"
          >
            <option value="8">8 kỳ gần nhất</option>
            <option value="12">12 kỳ gần nhất</option>
          </Select>
        </div>
      </div>

      {isLoading ? (
        <PageSkeleton />
      ) : isError ? (
        <ErrorState
          message={errorMessage ?? "Không thể tải bảng báo cáo tài chính."}
          onRetry={onRetry}
        />
      ) : !data || data.rows.length === 0 ? (
        <EmptyState
          title="Chưa có dữ liệu báo cáo tài chính"
          description="Không tìm thấy bảng báo cáo cho loại báo cáo và phạm vi đã chọn."
        />
      ) : (
        <FinancialTable
          statementType={data.statement_type}
          periodType={data.period_type}
          periods={data.periods}
          rows={data.rows}
          compact={compact}
        />
      )}
    </div>
  );
}
