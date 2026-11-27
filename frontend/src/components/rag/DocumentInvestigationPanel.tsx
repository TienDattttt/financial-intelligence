"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  FileSearch,
  FileText,
  Loader2,
  Search,
  ShieldAlert,
} from "lucide-react";
import { EmptyState, ErrorState } from "@/components/layout/StateBlocks";
import {
  QuotaBadge,
  deriveRagQuotaGateState,
} from "@/components/quota/QuotaBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge, Select, Skeleton } from "@/components/ui/primitives";
import {
  MY_QUOTA_QUERY_KEY,
  useMyQuota,
  useOptionalQueryClient,
} from "@/hooks/use-auth-workspace";
import { useCompanyDocuments } from "@/hooks/use-finintel-queries";
import { apiClient } from "@/lib/api-client";
import { STATEMENT_SCOPE_LABELS } from "@/lib/domain-labels";
import { formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  CitationItem,
  DocumentFileItem,
} from "@/types/api";

export interface ForensicPresetQuery {
  flagCode: "RF_03" | "RF_07" | "RF_09" | "RF_10";
  shortLabel: string;
  question: string;
}

export const FORENSIC_PRESET_QUERIES: readonly ForensicPresetQuery[] = [
  {
    flagCode: "RF_03",
    shortLabel: "Phải thu ngoài khách hàng",
    question:
      "Thuyết minh phải thu ngắn hạn, phải thu khác và dự phòng phải thu khó đòi",
  },
  {
    flagCode: "RF_07",
    shortLabel: "Cảnh báo kiểm toán & Hoạt động liên tục",
    question:
      "Ý kiến kiểm toán viên độc lập, cơ sở ý kiến ngoại trừ và vấn đề cần nhấn mạnh hoạt động liên tục",
  },
  {
    flagCode: "RF_09",
    shortLabel: "Xây dựng cơ bản dở dang",
    question:
      "Chi phí xây dựng cơ bản dở dang dài hạn trên Bảng cân đối kế toán và thuyết minh",
  },
  {
    flagCode: "RF_10",
    shortLabel: "Điều chỉnh lợi nhuận hậu kiểm toán",
    question:
      "Thuyết minh chênh lệch lợi nhuận sau thuế trước và sau kiểm toán, điều chỉnh hồi tố và ý kiến kiểm toán viên",
  },
];

export const DEFAULT_FORENSIC_DISCLAIMER_VN =
  "Tổng hợp trích dẫn từ Báo cáo tài chính kiểm toán hợp nhất gốc (PDF). Hệ thống không tự tính toán chỉ số tài chính bằng mô hình ngôn ngữ.";

export function getRedFlagPresetQuestion(flagCode: string): string | null {
  const shortCode = flagCode.split("_").slice(0, 2).join("_").toUpperCase();
  const found = FORENSIC_PRESET_QUERIES.find((q) => q.flagCode === shortCode);
  return found ? found.question : null;
}

export function getSafeDocumentUrl(rawUrl: string | null | undefined): string | null {
  if (!rawUrl) return null;
  const trimmed = rawUrl.trim();
  if (!trimmed) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") {
      return parsed.toString();
    }
    return null;
  } catch {
    return null;
  }
}

export function extractFiscalYears(
  documents: readonly DocumentFileItem[],
): number[] {
  const years = new Set<number>();
  for (const doc of documents) {
    if (Number.isInteger(doc.fiscal_year)) {
      years.add(doc.fiscal_year);
    }
  }
  return Array.from(years).sort((a, b) => b - a);
}

export function sanitizeRagText(raw: string): string {
  return raw
    .replace(/<\/?\s*CHUNK\b[^>]*>/gi, "đoạn tài liệu")
    .replace(/\(\s*CHUNKS?(?:\s*#?\d+)?\s*\)/gi, "")
    .replace(/\bCHUNKS?\b/gi, "đoạn tài liệu")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/ +([.,;:?!])/g, "$1")
    .trim();
}

export function sanitizeAbstentionReason(
  reason: string | null | undefined,
): string | null {
  if (!reason) return null;
  const cleaned = sanitizeRagText(reason.replace(/\s+/g, " "));
  return cleaned || null;
}

export function formatLatencyMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "0 ms";
  return `${formatNumber(ms, ms < 100 ? 1 : 0)} ms`;
}

export function formatSimilarityScore(similarity: number): string {
  if (!Number.isFinite(similarity)) return "N/A";
  const clamped = Math.max(0, Math.min(1, similarity));
  return formatPercent(clamped, { decimals: 1 });
}

export type AnswerToken =
  | { type: "text"; value: string }
  | { type: "citation"; citationIndex: number; badgeNumber: number; pageNo: number };

const INTERNAL_FILING_ID_RE = /\b[A-Z0-9]{1,16}_FY\d{4}_[A-Z]+_[0-9a-f]{8,}\b/g;
const BRACKET_CITATION_RE = /\[([^\]]+)\]/g;
const PAGE_IN_BRACKET_RE = /(?:trang|tr\.?|page)\s*(\d{1,4})/i;
const INDEX_IN_BRACKET_RE = /^(\d{1,2})$/;

export function tokenizeAnswerWithCitations(
  answer: string,
  citations: readonly CitationItem[],
): AnswerToken[] {
  const sanitizedAnswer = sanitizeRagText(answer);
  if (!sanitizedAnswer) return [];
  const tokens: AnswerToken[] = [];
  let lastIndex = 0;

  for (const match of sanitizedAnswer.matchAll(BRACKET_CITATION_RE)) {
    const start = match.index ?? 0;
    const rawWhole = match[0];
    const inner = match[1] ?? "";

    let matchedCitationIdx = -1;
    const idxMatch = inner.trim().match(INDEX_IN_BRACKET_RE);
    if (idxMatch) {
      const oneBased = Number(idxMatch[1]);
      if (oneBased >= 1 && oneBased <= citations.length) {
        matchedCitationIdx = oneBased - 1;
      }
    } else {
      const pageMatch = inner.match(PAGE_IN_BRACKET_RE);
      if (pageMatch) {
        const pageNo = Number(pageMatch[1]);
        matchedCitationIdx = citations.findIndex((c) => c.page_no === pageNo);
      }
    }

    if (matchedCitationIdx >= 0) {
      if (start > lastIndex) {
        const textSegment = sanitizedAnswer
          .slice(lastIndex, start)
          .replace(INTERNAL_FILING_ID_RE, "Tài liệu BCTC");
        tokens.push({ type: "text", value: textSegment });
      }
      tokens.push({
        type: "citation",
        citationIndex: matchedCitationIdx,
        badgeNumber: matchedCitationIdx + 1,
        pageNo: citations[matchedCitationIdx].page_no,
      });
      lastIndex = start + rawWhole.length;
    }
  }

  if (lastIndex < sanitizedAnswer.length) {
    const tail = sanitizedAnswer
      .slice(lastIndex)
      .replace(INTERNAL_FILING_ID_RE, "Tài liệu BCTC");
    tokens.push({ type: "text", value: tail });
  }

  return tokens;
}

export interface DocumentInvestigationPreset {
  flagCode: string;
  question: string;
  requestNonce: number;
}

export interface DocumentInvestigationPanelProps {
  ticker: string;
  companyName?: string | null;
  initialPreset?: DocumentInvestigationPreset | null;
  ragAvailable?: boolean;
}

export function DocumentInvestigationPanel({
  ticker,
  companyName,
  initialPreset,
  ragAvailable = true,
}: DocumentInvestigationPanelProps) {
  const cleanTicker = ticker.trim().toUpperCase();
  const queryClient = useOptionalQueryClient();
  const docsQuery = useCompanyDocuments(cleanTicker, { enabled: ragAvailable });
  const quotaQuery = useMyQuota(ragAvailable);
  const quotaGate = React.useMemo(
    () => deriveRagQuotaGateState(quotaQuery.data, "rag_query"),
    [quotaQuery.data],
  );
  const documents = React.useMemo(
    () => docsQuery.data?.documents ?? [],
    [docsQuery.data?.documents],
  );
  const availableYears = React.useMemo(
    () => extractFiscalYears(documents),
    [documents],
  );

  const [selectedYear, setSelectedYear] = React.useState<string>("ALL");
  const [question, setQuestion] = React.useState<string>(
    () => initialPreset?.question ?? "",
  );
  const [validationError, setValidationError] = React.useState<string | null>(
    null,
  );
  const [submittedQuery, setSubmittedQuery] = React.useState<{
    question: string;
    fiscalYear: number | null;
    nonce: number;
  } | null>(null);
  const [selectedCitationIndex, setSelectedCitationIndex] =
    React.useState<number>(0);

  const evidenceRefs = React.useRef<Record<number, HTMLElement | null>>({});

  const docByFilingId = React.useMemo(() => {
    const map = new Map<string, DocumentFileItem>();
    for (const doc of documents) {
      map.set(doc.filing_id, doc);
    }
    return map;
  }, [documents]);

  const totalDocuments = docsQuery.data?.total_documents ?? 0;

  const effectiveQuery = React.useMemo(() => {
    if (submittedQuery) return submittedQuery;
    if (initialPreset && totalDocuments > 0) {
      const parsedYear =
        selectedYear !== "ALL" && /^\d{4}$/.test(selectedYear)
          ? Number(selectedYear)
          : null;
      return {
        question: initialPreset.question,
        fiscalYear: parsedYear,
        nonce: initialPreset.requestNonce,
      };
    }
    return null;
  }, [initialPreset, selectedYear, submittedQuery, totalDocuments]);

  const investigateQuery = useQuery({
    queryKey: [
      "company-investigate",
      cleanTicker,
      effectiveQuery?.question ?? "",
      effectiveQuery?.fiscalYear ?? "ALL",
      effectiveQuery?.nonce ?? 0,
    ],
    queryFn: ({ signal }) =>
      apiClient.postCompanyInvestigate(
        cleanTicker,
        {
          question: effectiveQuery!.question,
          fiscal_year: effectiveQuery!.fiscalYear,
          statement_scope: "CONSOLIDATED",
          top_k: 6,
          use_cache: true,
        },
        signal,
      ),
    enabled: Boolean(effectiveQuery && totalDocuments > 0),
    retry: false,
    staleTime: 1000 * 60 * 5,
  });

  const isInvestigating = investigateQuery.isFetching;
  const isQuotaBlocked = quotaGate.blocked;
  const investigateError =
    validationError ??
    (investigateQuery.isError ? investigateQuery.error.message : null);
  const result = investigateQuery.data ?? null;

  React.useEffect(() => {
    if (
      queryClient &&
      investigateQuery.isSuccess &&
      investigateQuery.data &&
      !investigateQuery.data.cached
    ) {
      void queryClient.invalidateQueries({ queryKey: MY_QUOTA_QUERY_KEY });
    }
  }, [
    investigateQuery.data,
    investigateQuery.dataUpdatedAt,
    investigateQuery.isSuccess,
    queryClient,
  ]);

  const submitInvestigation = React.useCallback(
    (rawQuestion: string, yearOverride?: string) => {
      const trimmed = rawQuestion.trim();
      if (trimmed.length < 3) {
        setValidationError(
          "Vui lòng nhập câu hỏi tối thiểu 3 ký tự để đối chiếu thuyết minh.",
        );
        return;
      }
      setValidationError(null);
      setSelectedCitationIndex(0);
      const yearVal = yearOverride ?? selectedYear;
      const parsedYear =
        yearVal !== "ALL" && /^\d{4}$/.test(yearVal) ? Number(yearVal) : null;
      setSubmittedQuery({
        question: trimmed,
        fiscalYear: parsedYear,
        nonce: Date.now(),
      });
    },
    [selectedYear],
  );

  const handleSelectCitation = React.useCallback((idx: number) => {
    setSelectedCitationIndex(idx);
    const target = evidenceRefs.current[idx];
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, []);

  const answerTokens = React.useMemo(
    () =>
      result
        ? tokenizeAnswerWithCitations(result.answer, result.citations)
        : [],
    [result],
  );

  if (!ragAvailable) {
    return (
      <EmptyState
        title="Dịch vụ tra cứu Thuyết minh PDF hiện chưa bật"
        description={`Kho đối chiếu Thuyết minh Báo cáo tài chính (PDF) hiện chưa được kích hoạt trên môi trường này. Các tab tổng quan, BCTC chuẩn hóa, 10 cờ đỏ VAS và giải thích TreeSHAP cho ${companyName ? `${cleanTicker} (${companyName})` : cleanTicker} vẫn hoạt động đầy đủ.`}
      />
    );
  }

  if (docsQuery.isLoading) {
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label="Đang tải danh sách Báo cáo tài chính kiểm toán"
        className="grid grid-cols-1 gap-4 lg:grid-cols-12"
      >
        <Card className="lg:col-span-6">
          <CardContent className="space-y-3 p-4">
            <Skeleton className="h-5 w-48" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-32 w-full" />
          </CardContent>
        </Card>
        <Card className="lg:col-span-6">
          <CardContent className="space-y-3 p-4">
            <Skeleton className="h-5 w-52" />
            <Skeleton className="h-36 w-full" />
            <Skeleton className="h-36 w-full" />
          </CardContent>
        </Card>
      </div>
    );
  }

  if (docsQuery.isError) {
    return (
      <ErrorState
        title="Dịch vụ tra cứu Thuyết minh PDF tạm thời chưa sẵn sàng"
        message={docsQuery.error.message}
        onRetry={() => docsQuery.refetch()}
      />
    );
  }

  if (totalDocuments === 0) {
    return (
      <EmptyState
        title={`${cleanTicker} chưa có trong Gói tài liệu kiểm toán tuyển chọn`}
        description={`Kho đối chiếu thuyết minh gốc hiện lưu trữ 99 Báo cáo tài chính kiểm toán hợp nhất (PDF) của 88 doanh nghiệp trọng điểm và các hồ sơ nghiên cứu điển hình (Case Studies) lịch sử. Hệ thống không thực hiện truy vấn khi chưa có tài liệu gốc cho ${companyName ? `${cleanTicker} (${companyName})` : cleanTicker} nhằm bảo đảm không suy diễn số liệu.`}
      />
    );
  }

  const cleanAbstentionReason = sanitizeAbstentionReason(
    result?.abstention_reason,
  );

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="grid grid-cols-1 divide-y divide-border lg:grid-cols-11 lg:divide-x lg:divide-y-0">
        {/* LEFT PANE (55%): Query & Cited Synthesis */}
        <section className="flex flex-col lg:col-span-6">
          <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
            <div className="min-w-0">
              <h2 className="flex items-center gap-2 text-[14px] font-semibold text-ink">
                <FileSearch
                  className="size-4 shrink-0 text-accent-ink"
                  aria-hidden="true"
                />
                <span>Truy vấn &amp; Tổng hợp có trích dẫn</span>
              </h2>
              <p className="mt-0.5 text-xs text-ink-muted">
                Đối chiếu trực tiếp {totalDocuments} Báo cáo tài chính kiểm toán hợp nhất của {cleanTicker}
              </p>
            </div>
            <Badge className="border border-border bg-surface-muted font-mono text-[11px] text-ink">
              {totalDocuments} PDF
            </Badge>
          </div>

          <div className="space-y-4 p-4">
            <QuotaBadge
              quota={quotaQuery.data}
              isLoading={quotaQuery.isLoading}
            />

            {/* Fiscal Year Filter */}
            <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:justify-between">
              <label
                htmlFor="rag-fiscal-year"
                className="text-xs font-semibold text-ink"
              >
                Kỳ báo cáo kiểm toán:
              </label>
              <Select
                id="rag-fiscal-year"
                value={selectedYear}
                onChange={(e) => setSelectedYear(e.target.value)}
                className="w-full text-xs sm:w-64"
                disabled={isInvestigating}
              >
                <option value="ALL">
                  Tất cả năm tài chính đã lưu ({availableYears.join(", ")})
                </option>
                {availableYears.map((yr) => {
                  const docForYr = documents.find((d) => d.fiscal_year === yr);
                  return (
                    <option key={yr} value={String(yr)}>
                      Năm tài chính {yr} · Hợp nhất
                      {docForYr ? ` (${docForYr.total_pages} trang)` : ""}
                    </option>
                  );
                })}
              </Select>
            </div>

            {/* 3 Quick Forensic Query Buttons */}
            <div className="space-y-1.5">
              <span className="block text-xs font-semibold text-ink">
                Truy vấn điều tra cờ đỏ nhanh:
              </span>
              <div className="flex flex-wrap gap-1.5">
                {FORENSIC_PRESET_QUERIES.map((preset) => {
                  const isActivePreset =
                    question.trim() === preset.question.trim();
                  return (
                    <Button
                      key={preset.flagCode}
                      type="button"
                      variant={isActivePreset ? "default" : "outline"}
                      size="xs"
                      disabled={isInvestigating || isQuotaBlocked}
                      onClick={() => {
                        setQuestion(preset.question);
                        submitInvestigation(preset.question);
                      }}
                      className="h-auto whitespace-normal py-1.5 text-left text-xs"
                    >
                      {preset.shortLabel}
                    </Button>
                  );
                })}
              </div>
            </div>

            {/* Analytical Question Input Form */}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                submitInvestigation(question);
              }}
              className="space-y-2.5"
            >
              <label
                htmlFor="rag-question-input"
                className="block text-xs font-semibold text-ink"
              >
                Câu hỏi phân tích thuyết minh chuyên sâu:
              </label>
              <textarea
                id="rag-question-input"
                rows={3}
                maxLength={600}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Ví dụ: Kiểm toán viên nêu vấn đề nhấn mạnh gì về lỗ lũy kế, nợ quá hạn và giả định hoạt động liên tục?"
                disabled={isInvestigating}
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-[13px] leading-5 text-ink placeholder:text-ink-subtle focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-60"
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="num text-[11px] text-ink-subtle">
                  {question.length}/600 ký tự · Phạm vi: Hợp nhất
                </span>
                <Button
                  type="submit"
                  variant="default"
                  size="sm"
                  disabled={
                    isInvestigating ||
                    isQuotaBlocked ||
                    question.trim().length < 3
                  }
                >
                  {isInvestigating ? (
                    <>
                      <Loader2
                        className="size-3.5 animate-spin"
                        aria-hidden="true"
                      />
                      <span>Đang truy xuất...</span>
                    </>
                  ) : (
                    <>
                      <Search className="size-3.5" aria-hidden="true" />
                      <span>Tra cứu Thuyết minh</span>
                    </>
                  )}
                </Button>
              </div>
            </form>

            {investigateError ? (
              <ErrorState
                title="Không thể hoàn tất truy vấn"
                message={investigateError}
                onRetry={() => submitInvestigation(question)}
              />
            ) : null}

            {/* Retrieving Skeleton */}
            {isInvestigating ? (
              <div
                role="status"
                aria-busy="true"
                aria-label="Đang truy xuất và kiểm chứng trích dẫn PDF"
                className="space-y-2.5 rounded-lg border border-border bg-canvas p-3.5"
              >
                <Skeleton className="h-4 w-44" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-11/12" />
                <Skeleton className="h-4 w-3/4" />
              </div>
            ) : null}

            {/* Grounded Answer or Honest Abstention */}
            {!isInvestigating && result ? (
              <section
                aria-label="Kết quả tổng hợp có trích dẫn"
                aria-live="polite"
                className="space-y-3"
              >
                {result.abstained ? (
                  <div
                    role="status"
                    className="space-y-1.5 rounded-lg border border-risk-mid/35 bg-risk-mid-soft/50 p-3.5 text-xs"
                  >
                    <div className="flex items-center gap-2 font-semibold text-risk-mid">
                      <AlertTriangle
                        className="size-4 shrink-0"
                        aria-hidden="true"
                      />
                      <span>
                        Từ chối trả lời trung thực — Chưa đủ căn cứ nguyên văn trong PDF
                      </span>
                    </div>
                    <p className="text-[13px] leading-5 text-ink">
                      {sanitizeRagText(result.answer)}
                    </p>
                    {cleanAbstentionReason ? (
                      <p className="text-[11px] leading-4 text-ink-muted">
                        Chi tiết kiểm chứng: {cleanAbstentionReason}
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <div className="space-y-3 rounded-lg border border-border bg-canvas p-3.5">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 pb-2">
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-risk-low">
                        <CheckCircle2
                          className="size-3.5 shrink-0"
                          aria-hidden="true"
                        />
                        <span>Tổng hợp đối chiếu từ BCTC kiểm toán gốc</span>
                      </span>
                      <span className="num text-[11px] font-medium text-ink-muted">
                        {result.citations.length} trích dẫn nguyên văn
                      </span>
                    </div>

                    <div className="whitespace-pre-line text-[13px] leading-6 text-ink">
                      {answerTokens.map((tok, idx) =>
                        tok.type === "text" ? (
                          <React.Fragment key={idx}>{tok.value}</React.Fragment>
                        ) : (
                          <button
                            key={idx}
                            type="button"
                            onClick={() =>
                              handleSelectCitation(tok.citationIndex)
                            }
                            aria-pressed={
                              selectedCitationIndex === tok.citationIndex
                            }
                            title={`Xem nguyên văn Trang ${tok.pageNo}`}
                            className={cn(
                              "mx-0.5 inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[11px] font-semibold transition-colors",
                              selectedCitationIndex === tok.citationIndex
                                ? "border-primary bg-primary text-primary-foreground"
                                : "border-border-strong bg-surface text-accent-ink hover:bg-accent-soft",
                            )}
                          >
                            [{tok.badgeNumber}]
                          </button>
                        ),
                      )}
                    </div>

                    {result.citations.length > 0 ? (
                      <div className="flex flex-wrap items-center gap-1.5 border-t border-border/70 pt-2.5">
                        <span className="text-[11px] font-medium text-ink-muted">
                          Trích dẫn:
                        </span>
                        {result.citations.map((cit, idx) => (
                          <button
                            key={`${cit.filing_id}-${cit.page_no}-${idx}`}
                            type="button"
                            onClick={() => handleSelectCitation(idx)}
                            aria-pressed={selectedCitationIndex === idx}
                            className={cn(
                              "inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[11px] font-medium transition-colors",
                              selectedCitationIndex === idx
                                ? "border-primary bg-primary text-primary-foreground"
                                : "border-border bg-surface text-accent-ink hover:bg-accent-soft",
                            )}
                          >
                            <span>[{idx + 1}]</span>
                            <span>Trang {cit.page_no}</span>
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                )}

                {/* Execution & Cache Metadata Bar */}
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface-muted/60 px-3 py-2 text-[11px] text-ink-muted">
                  <div className="flex flex-wrap items-center gap-3">
                    <span>
                      Độ trễ:{" "}
                      <strong className="num text-ink">
                        {formatLatencyMs(result.latency_ms)}
                      </strong>
                    </span>
                    <span>
                      Bộ nhớ đệm:{" "}
                      <strong
                        className={cn(
                          "font-medium",
                          result.cached ? "text-up" : "text-ink",
                        )}
                      >
                        {result.cached ? "Dữ liệu đệm sẵn (Cache)" : "Truy xuất mới"}
                      </strong>
                    </span>
                    <span>
                      Đoạn đối chiếu:{" "}
                      <strong className="num text-ink">
                        {result.retrieved_chunks_count}
                      </strong>
                    </span>
                  </div>
                </div>

                {/* Honest Forensic Disclaimer */}
                <p className="text-[11px] leading-4 text-ink-subtle">
                  {result.disclaimer || DEFAULT_FORENSIC_DISCLAIMER_VN}
                </p>
              </section>
            ) : null}
          </div>
        </section>

        {/* RIGHT PANE (45%): Original PDF Evidence — Verbatim Excerpt Viewer */}
        <section className="flex flex-col bg-canvas/35 lg:col-span-5">
          <div className="border-b border-border px-4 py-3">
            <div className="min-w-0">
              <h2 className="flex items-center gap-2 text-[14px] font-semibold text-ink">
                <FileText
                  className="size-4 shrink-0 text-accent-ink"
                  aria-hidden="true"
                />
                <span>Bằng chứng PDF Gốc — Nguyên văn đoạn trích</span>
              </h2>
              <p className="mt-0.5 text-xs text-ink-muted">
                Nhấp huy hiệu [1], [2] bên trái để đối chiếu nguyên văn và tờ khai gốc
              </p>
            </div>
          </div>

          <div className="flex-1 space-y-3 p-4">
            {isInvestigating ? (
              <div className="space-y-3">
                <Skeleton className="h-32 w-full" />
                <Skeleton className="h-32 w-full" />
              </div>
            ) : !result ? (
              /* Initial state: show curated PDF inventory for this issuer */
              <div className="space-y-3">
                <p className="text-xs text-ink-muted">
                  Danh mục Báo cáo tài chính kiểm toán gốc đã nạp cho{" "}
                  <strong className="text-ink">{cleanTicker}</strong>. Chọn một truy vấn nhanh hoặc nhập câu hỏi ở cột bên trái để bắt đầu kiểm chứng.
                </p>
                <div className="space-y-2">
                  {documents.map((doc) => {
                    const safeUrl = getSafeDocumentUrl(doc.source_url);
                    const scopeLabel =
                      STATEMENT_SCOPE_LABELS[doc.statement_scope] ??
                      "Hợp nhất";
                    return (
                      <div
                        key={doc.filing_id}
                        className="rounded-lg border border-border bg-canvas p-3 text-xs"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div className="min-w-0 space-y-1">
                            <p className="font-semibold text-ink">
                              {doc.source_title}
                            </p>
                            <p className="text-[11px] text-ink-muted">
                              Năm tài chính {doc.fiscal_year} · {scopeLabel} ·{" "}
                              <span className="num">{doc.total_pages}</span>{" "}
                              trang ({doc.digital_pages} văn bản,{" "}
                              {doc.ocr_pages} nhận dạng ảnh)
                            </p>
                          </div>
                          {safeUrl ? (
                            <a
                              href={safeUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex shrink-0 items-center gap-1 rounded border border-border bg-surface px-2 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-soft"
                            >
                              <span>Mở PDF gốc</span>
                              <ExternalLink
                                className="size-3"
                                aria-hidden="true"
                              />
                            </a>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : result.citations.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border p-6 text-center text-xs text-ink-muted">
                <ShieldAlert
                  className="mx-auto mb-2 size-6 text-ink-subtle"
                  aria-hidden="true"
                />
                <p className="font-semibold text-ink">
                  Không có trích dẫn nguyên văn đạt ngưỡng kiểm chứng
                </p>
                <p className="mt-1 leading-5">
                  Hệ thống chỉ hiển thị trích đoạn có thật trong Báo cáo tài chính kiểm toán gốc. Thử đổi năm tài chính hoặc điều chỉnh từ khóa truy vấn.
                </p>
              </div>
            ) : (
              <div className="divide-y divide-border overflow-hidden rounded-md border border-border bg-surface">
                {result.citations.map((cit, idx) => {
                  const isSelected = selectedCitationIndex === idx;
                  const sourceDoc = docByFilingId.get(cit.filing_id);
                  const safeUrl = getSafeDocumentUrl(sourceDoc?.source_url);
                  const scopeLabel =
                    STATEMENT_SCOPE_LABELS[cit.statement_scope] ?? "Hợp nhất";
                  return (
                    <article
                      key={`${cit.filing_id}-${cit.page_no}-${idx}`}
                      ref={(el) => {
                        evidenceRefs.current[idx] = el;
                      }}
                      aria-current={isSelected ? "true" : undefined}
                      onClick={() => setSelectedCitationIndex(idx)}
                      className={cn(
                        "cursor-pointer p-3.5 transition-colors",
                        isSelected
                          ? "bg-accent-soft/40"
                          : "hover:bg-surface-hover/50",
                      )}
                    >
                      {/* Citation Header + Source URL */}
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <span
                            className={cn(
                              "inline-flex shrink-0 items-center rounded-sm px-1.5 py-0.5 font-mono text-[11px] font-semibold",
                              isSelected
                                ? "bg-primary text-primary-foreground"
                                : "bg-surface-muted text-ink",
                            )}
                          >
                            [{idx + 1}]
                          </span>
                          <h3 className="truncate text-xs font-semibold text-ink">
                            {cit.document_title}
                          </h3>
                        </div>
                        {safeUrl ? (
                          <a
                            href={safeUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            className="inline-flex shrink-0 items-center gap-1 rounded-sm border border-border bg-surface px-2 py-0.5 text-[11px] font-medium text-accent-ink hover:bg-accent-soft"
                          >
                            <span>Tài liệu nguồn</span>
                            <ExternalLink
                              className="size-3"
                              aria-hidden="true"
                            />
                          </a>
                        ) : null}
                      </div>

                      {/* Single-line clean metadata strip (replaces 6 bordered pills) */}
                      <p className="mt-1.5 text-[11px] leading-4 text-ink-muted">
                        <span className="font-medium text-ink">FY{cit.fiscal_year}</span>
                        {" · "}
                        <span>{scopeLabel}</span>
                        {" · "}
                        <span className="font-mono font-semibold text-accent-ink">
                          Trang {cit.page_no}
                        </span>
                        {" · "}
                        <span>{cit.section_title}</span>
                        {" · "}
                        <span>ĐV: {cit.unit_scale}</span>
                        {" · "}
                        <span className="num">
                          Khớp {formatSimilarityScore(cit.vector_similarity)}
                        </span>
                      </p>

                      {/* Verbatim PDF Excerpt */}
                      <blockquote className="mt-2 border-l-2 border-accent-ink pl-3 text-xs leading-5 text-ink">
                        “{cit.verbatim_excerpt}”
                      </blockquote>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
