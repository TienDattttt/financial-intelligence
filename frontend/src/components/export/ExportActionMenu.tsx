"use client";

import * as React from "react";
import Link from "next/link";
import {
  ChevronDown,
  Download,
  FileSpreadsheet,
  FileText,
  Printer,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { observeResponseSnapshotHeader } from "@/lib/api-client";
import {
  buildScreenerExportUrl,
  parseContentDispositionFilename,
} from "@/lib/saved-screens-store";
import { cn } from "@/lib/utils";
import type {
  PeriodType,
  ScreenerQueryParams,
  StatementScope,
} from "@/types/api";

const XLSX_MIME_ACCEPT =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, application/json";

const INTERNAL_LEAK_RE =
  /([A-Za-z]:\\|\/(?:src|data|Users)\/|\.parquet\b|\.sqlite\b|\.py\b|Traceback)/i;

export function buildCompanyXlsxExportUrl(
  ticker: string,
  opts?: {
    statementScope?: StatementScope;
    periodType?: PeriodType;
    periodsLimit?: number;
  },
): string {
  const cleanTicker = ticker.trim().toUpperCase();
  const search = new URLSearchParams();
  if (opts?.statementScope) search.set("statement_scope", opts.statementScope);
  if (opts?.periodType) search.set("period_type", opts.periodType);
  if (opts?.periodsLimit) search.set("periods_limit", String(opts.periodsLimit));
  const qs = search.toString();
  const base = `/api/v3/exports/companies/${encodeURIComponent(cleanTicker)}.xlsx`;
  return qs ? `${base}?${qs}` : base;
}

export function buildScreenerXlsxExportUrl(params: ScreenerQueryParams): string {
  return buildScreenerExportUrl(params).replace(
    "/api/v1/screener/export",
    "/api/v3/exports/screener.xlsx",
  );
}

function formatXlsxError(status: number | null, detail?: string | null): string {
  if (status === null) {
    return "Không thể kết nối máy chủ để tải tệp Excel. Vui lòng thử lại.";
  }
  if (status === 404) {
    return "Không tìm thấy dữ liệu xuất Excel cho yêu cầu hiện tại.";
  }
  if (status === 422) {
    return "Tham số lọc không hợp lệ, không thể xuất Excel.";
  }
  const cleaned = (detail ?? "").replace(/\s+/g, " ").trim();
  if (cleaned && !INTERNAL_LEAK_RE.test(cleaned)) {
    return `Xuất Excel thất bại: ${cleaned.slice(0, 160)}`;
  }
  return "Xuất tệp Excel thất bại do lỗi máy chủ phân tích.";
}

export interface ExportActionMenuProps {
  xlsxUrl: string;
  xlsxFallbackFilename: string;
  onDownloadCsv?: () => Promise<void> | void;
  tearSheetHref?: string;
  onPrintReport?: () => void;
  disabled?: boolean;
  label?: string;
  className?: string;
  onError?: (message: string | null) => void;
}

export function ExportActionMenu({
  xlsxUrl,
  xlsxFallbackFilename,
  onDownloadCsv,
  tearSheetHref,
  onPrintReport,
  disabled = false,
  label = "Xuất báo cáo",
  className,
  onError,
}: ExportActionMenuProps) {
  const [open, setOpen] = React.useState(false);
  const [downloading, setDownloading] = React.useState(false);
  const [localError, setLocalError] = React.useState<string | null>(null);
  const rootRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onMouseDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("mousedown", onMouseDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("mousedown", onMouseDown);
    };
  }, [open]);

  const handleDownloadXlsx = React.useCallback(async () => {
    setOpen(false);
    setDownloading(true);
    setLocalError(null);
    onError?.(null);
    try {
      const res = await fetch(xlsxUrl, {
        headers: { Accept: XLSX_MIME_ACCEPT },
      });
      observeResponseSnapshotHeader(res);
      if (!res.ok) {
        let detail = "";
        try {
          const payload = (await res.json()) as { detail?: unknown };
          if (typeof payload.detail === "string") detail = payload.detail;
        } catch {
          detail = "";
        }
        const msg = formatXlsxError(res.status, detail);
        setLocalError(msg);
        onError?.(msg);
        return;
      }
      const blob = await res.blob();
      const filename = parseContentDispositionFilename(
        res.headers.get("Content-Disposition"),
        xlsxFallbackFilename,
      );
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(objectUrl);
    } catch {
      const msg = formatXlsxError(null);
      setLocalError(msg);
      onError?.(msg);
    } finally {
      setDownloading(false);
    }
  }, [onError, xlsxFallbackFilename, xlsxUrl]);

  return (
    <div ref={rootRef} className={cn("relative inline-block", className)}>
      <Button
        type="button"
        variant="outline"
        size="xs"
        disabled={disabled || downloading}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        <Download className="size-3.5" aria-hidden="true" />
        <span>{downloading ? "Đang xuất Excel..." : label}</span>
        <ChevronDown className="size-3 text-ink-muted" aria-hidden="true" />
      </Button>

      {open ? (
        <div
          role="menu"
          aria-label="Tùy chọn xuất báo cáo"
          className="absolute right-0 z-30 mt-1.5 w-60 max-w-[90vw] rounded-lg border border-border-strong bg-surface p-1 text-xs text-ink shadow-popover"
        >
          <button
            type="button"
            role="menuitem"
            disabled={downloading}
            onClick={() => {
              void handleDownloadXlsx();
            }}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left font-medium text-ink hover:bg-surface-hover disabled:opacity-50"
          >
            <FileSpreadsheet className="size-3.5 shrink-0 text-accent-ink" aria-hidden="true" />
            <span>Tải Excel (.xlsx)</span>
          </button>

          {onDownloadCsv ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                void onDownloadCsv();
              }}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left font-medium text-ink hover:bg-surface-hover"
            >
              <Download className="size-3.5 shrink-0 text-ink-muted" aria-hidden="true" />
              <span>Tải CSV (.csv)</span>
            </button>
          ) : null}

          {tearSheetHref ? (
            <Link
              href={tearSheetHref}
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left font-medium text-ink hover:bg-surface-hover"
            >
              <FileText className="size-3.5 shrink-0 text-accent-ink" aria-hidden="true" />
              <span>Mở Tear-Sheet (A4 / PDF)</span>
            </Link>
          ) : null}

          {onPrintReport ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onPrintReport();
              }}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left font-medium text-ink hover:bg-surface-hover"
            >
              <Printer className="size-3.5 shrink-0 text-ink-muted" aria-hidden="true" />
              <span>In trang hiện tại</span>
            </button>
          ) : null}
        </div>
      ) : null}

      {localError && !onError ? (
        <div
          role="alert"
          className="mt-1.5 flex items-center justify-between gap-2 rounded-md border border-risk-high/30 bg-risk-high-soft px-2.5 py-1.5 text-[11px] text-risk-high"
        >
          <span>{localError}</span>
          <button
            type="button"
            aria-label="Đóng thông báo lỗi xuất tệp"
            onClick={() => setLocalError(null)}
            className="rounded p-0.5 hover:bg-surface/40"
          >
            <X className="size-3" />
          </button>
        </div>
      ) : null}
    </div>
  );
}
