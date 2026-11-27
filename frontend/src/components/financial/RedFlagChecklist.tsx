"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { Dialog } from "@base-ui/react/dialog";
import {
  CheckCircle2,
  FileSearch,
  MessageSquarePlus,
  ShieldAlert,
  X,
} from "lucide-react";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { getRedFlagPresetQuestion } from "@/components/rag/DocumentInvestigationPanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { hitlApi } from "@/lib/auth-api";
import {
  AUDIT_OPINION_LABELS,
  formatRedFlagCodeLabel,
  RED_FLAG_EVIDENCE_META,
  SEVERITY_META,
} from "@/lib/domain-labels";
import {
  formatDateVi,
  formatDays,
  formatMultiple,
  formatNumber,
  formatPercent,
  formatVndBillions,
} from "@/lib/format";
import type {
  HitlReasonCategory,
  HitlVerdict,
  RedFlagDictionaryItem,
  RedFlagItem,
} from "@/types/api";

const HITL_REASON_OPTIONS: ReadonlyArray<{
  value: HitlReasonCategory;
  label: string;
}> = [
  {
    value: "CONSTRUCTION_PROGRESS_BILLING",
    label: "Đặc thù nghiệm thu / tiến độ xây dựng",
  },
  {
    value: "HOLDING_ASSOCIATE_DIVIDENDS",
    label: "Cổ tức công ty liên kết / mô hình holding",
  },
  {
    value: "SEASONAL_WORKING_CAPITAL",
    label: "Biến động vốn lưu động theo mùa vụ",
  },
  {
    value: "TRANSIENT_CAPEX_EXPANSION",
    label: "Mở rộng CapEx đầu tư ngắn hạn",
  },
  {
    value: "ACCOUNTING_RECLASSIFICATION",
    label: "Phân loại lại khoản mục kế toán",
  },
  {
    value: "CONFIRMED_FUNDAMENTAL_RISK",
    label: "Rủi ro nền tảng thực sự",
  },
  {
    value: "OTHER",
    label: "Lý do nghiệp vụ khác",
  },
];

export function formatRedFlagEvidence(
  rawJson: string | null | undefined,
): [string, string][] {
  if (!rawJson) return [];
  try {
    const parsed = JSON.parse(rawJson) as Record<string, unknown>;
    const entries: [string, string][] = [];
    for (const [k, v] of Object.entries(parsed)) {
      if (k === "source_filing_ids") continue;
      const meta = RED_FLAG_EVIDENCE_META[k];
      if (!meta) continue;

      let formatted = "N/A";
      if (meta.format === "AUDIT_OPINION") {
        const key = typeof v === "string" ? v : "";
        formatted = AUDIT_OPINION_LABELS[key] ?? "Ngoại lệ kiểm toán";
      } else if (meta.format === "BOOLEAN") {
        formatted = v === true ? "Có" : v === false ? "Không" : "N/A";
      } else if (typeof v === "number" && Number.isFinite(v)) {
        switch (meta.format) {
          case "VND_BILLIONS":
            formatted = `${formatVndBillions(v, { accountingNegative: true })} tỷ`;
            break;
          case "PERCENT":
            formatted = formatPercent(v, { decimals: 1 });
            break;
          case "MULTIPLE":
            formatted = formatMultiple(v, 2);
            break;
          case "DAYS":
            formatted = formatDays(v, 0);
            break;
          default:
            formatted = formatNumber(v, Math.abs(v) < 2 ? 3 : 1);
            break;
        }
      }
      entries.push([meta.label, formatted]);
      if (entries.length >= 6) break;
    }
    return entries;
  } catch {
    return [];
  }
}

export function RedFlagChecklist({
  dictionary,
  triggeredFlags,
  latestPeriodEndDate,
  onInvestigateRedFlag,
  ticker,
  icbCodeLevel2,
}: {
  dictionary: readonly RedFlagDictionaryItem[];
  triggeredFlags: readonly RedFlagItem[];
  latestPeriodEndDate?: string | null;
  onInvestigateRedFlag?: (flagCode: string, presetQuestion: string) => void;
  ticker?: string;
  icbCodeLevel2?: string | null;
}) {
  const pathname = usePathname();
  const resolvedTicker = React.useMemo(() => {
    const cleanProp = ticker?.trim().toUpperCase();
    if (cleanProp && /^[A-Z0-9_-]{1,20}$/.test(cleanProp)) {
      return cleanProp;
    }
    const match = pathname?.match(/\/companies\/([^/?#]+)/i);
    if (match?.[1]) {
      const fromRoute = decodeURIComponent(match[1]).trim().toUpperCase();
      if (/^[A-Z0-9_-]{1,20}$/.test(fromRoute)) {
        return fromRoute;
      }
    }
    return "PRIVATE";
  }, [pathname, ticker]);

  const [dialogFlag, setDialogFlag] = React.useState<RedFlagItem | null>(null);
  const [verdict, setVerdict] = React.useState<HitlVerdict>("BENIGN_DOMAIN_EXCEPTION");
  const [reasonCategory, setReasonCategory] = React.useState<HitlReasonCategory>(
    "CONSTRUCTION_PROGRESS_BILLING",
  );
  const [note, setNote] = React.useState("");
  const [citationPage, setCitationPage] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [feedbackMessage, setFeedbackMessage] = React.useState<string | null>(null);
  const [feedbackError, setFeedbackError] = React.useState<string | null>(null);

  const openFeedbackDialog = (flag: RedFlagItem) => {
    setDialogFlag(flag);
    setVerdict("BENIGN_DOMAIN_EXCEPTION");
    setReasonCategory("CONSTRUCTION_PROGRESS_BILLING");
    setNote("");
    setCitationPage("");
    setFeedbackError(null);
  };

  const handleSubmitFeedback = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!dialogFlag) return;
    const trimmedNote = note.trim();
    if (trimmedNote.length < 3) {
      setFeedbackError("Vui lòng nhập ghi chú chuyên môn tối thiểu 3 ký tự.");
      return;
    }
    const parsedPage = citationPage.trim()
      ? Number.parseInt(citationPage.trim(), 10)
      : null;
    if (
      parsedPage !== null &&
      (!Number.isFinite(parsedPage) || parsedPage < 1 || parsedPage > 5000)
    ) {
      setFeedbackError("Số trang thuyết minh tham chiếu phải nằm trong khoảng 1–5000.");
      return;
    }

    setSubmitting(true);
    setFeedbackError(null);
    try {
      await hitlApi.submitFeedback({
        ticker: resolvedTicker,
        icb_code_level2: icbCodeLevel2 ?? undefined,
        period_end_date: dialogFlag.period_end_date,
        signal_code: dialogFlag.flag_code,
        verdict,
        reason_category: reasonCategory,
        note: trimmedNote,
        citation_page: parsedPage ?? undefined,
      });
      setFeedbackMessage(
        `Đã ghi nhận phản hồi chuyên gia cho ${formatRedFlagCodeLabel(dialogFlag.flag_code)} (không làm thay đổi nhãn huấn luyện ML).`,
      );
      setDialogFlag(null);
    } catch (err) {
      const msg =
        err instanceof Error && err.message
          ? err.message
          : "Không thể gửi phản hồi chuyên gia. Vui lòng đăng nhập và thử lại.";
      setFeedbackError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const activeForLatest = latestPeriodEndDate
    ? triggeredFlags.filter((f) => f.period_end_date === latestPeriodEndDate)
    : triggeredFlags;
  const activeCodes = new Set(activeForLatest.map((f) => f.flag_code));

  const passedRules = dictionary.filter((rule) => !activeCodes.has(rule.flag_code));

  return (
    <div className="space-y-4">
      {feedbackMessage ? (
        <div
          role="status"
          className="flex items-center justify-between gap-2 rounded border border-risk-low/30 bg-risk-low-soft/40 px-3 py-2 text-xs text-risk-low"
        >
          <span>{feedbackMessage}</span>
          <button
            type="button"
            onClick={() => setFeedbackMessage(null)}
            className="text-ink-muted hover:text-ink"
            aria-label="Đóng thông báo phản hồi"
          >
            <X className="size-3.5" aria-hidden="true" />
          </button>
        </div>
      ) : null}
      <div className="space-y-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink">
          <ShieldAlert className="size-4 text-risk-high" aria-hidden="true" />
          <span>
            Quy tắc phát hiện bất thường đang kích hoạt ({activeForLatest.length}/
            {dictionary.length || 10})
          </span>
        </h3>

        {activeForLatest.length === 0 ? (
          <Card className="border-risk-low/30 bg-risk-low-soft/40">
            <CardContent className="flex items-center gap-2 p-3 text-[13px] text-risk-low">
              <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
              <span>
                Không ghi nhận cờ đỏ bất thường kế toán nào trong kỳ báo cáo mới nhất.
              </span>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-2.5">
            {activeForLatest.map((flag) => {
              const sev = SEVERITY_META[flag.severity] ?? SEVERITY_META.MEDIUM;
              const evidence = formatRedFlagEvidence(flag.evidence_json);
              const dictMatch = dictionary.find(
                (rule) => rule.flag_code === flag.flag_code,
              );
              const shortCode = flag.flag_code.split("_").slice(0, 2).join("_");
              const presetQuestion = getRedFlagPresetQuestion(shortCode);
              return (
                <Card
                  key={`${flag.flag_code}-${flag.period_end_date}`}
                  className="border-risk-high/25"
                >
                  <CardContent className="space-y-2 p-3.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-surface-muted px-1.5 py-0.5 font-mono text-[11px] font-medium text-ink">
                          {formatRedFlagCodeLabel(flag.flag_code)}
                        </span>
                        <span className="text-sm font-semibold text-ink">
                          {flag.flag_title_vn ??
                            dictMatch?.title_vn ??
                            "Dấu hiệu bất thường kế toán"}
                        </span>
                      </div>
                      <RiskBadge tone={sev.tone} label={sev.label} />
                    </div>
                    {flag.explanation_text_vn ? (
                      <p className="text-[13px] leading-5 text-ink-muted">
                        {flag.explanation_text_vn}
                      </p>
                    ) : null}
                    {evidence.length > 0 ? (
                      <div className="flex flex-wrap gap-2 pt-1">
                        {evidence.map(([label, val]) => (
                          <span
                            key={label}
                            className="rounded border border-border bg-canvas px-2 py-0.5 text-[11px] text-ink"
                          >
                            {label}: <strong className="num">{val}</strong>
                          </span>
                        ))}
                      </div>
                    ) : null}
                    <div className="flex flex-wrap items-center justify-between gap-2 pt-0.5">
                      <p className="text-[11px] text-ink-subtle">
                        Kỳ phát hiện: {formatDateVi(flag.period_end_date)}
                      </p>
                      <div className="flex flex-wrap items-center gap-2">
                        {presetQuestion && onInvestigateRedFlag ? (
                          <Button
                            type="button"
                            variant="outline"
                            size="xs"
                            onClick={() =>
                              onInvestigateRedFlag(shortCode, presetQuestion)
                            }
                          >
                            <FileSearch className="size-3" aria-hidden="true" />
                            <span>Kiểm tra Thuyết minh gốc</span>
                          </Button>
                        ) : null}
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          onClick={() => openFeedbackDialog(flag)}
                        >
                          <MessageSquarePlus className="size-3" aria-hidden="true" />
                          <span>Phản hồi chuyên gia</span>
                        </Button>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {passedRules.length > 0 ? (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-ink-muted">
            Các quy tắc đạt chuẩn trong kỳ ({passedRules.length})
          </h3>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {passedRules.map((rule) => (
              <div
                key={rule.flag_code}
                className="flex items-start gap-2 rounded border border-border bg-surface p-2.5 text-xs"
              >
                <CheckCircle2
                  className="mt-0.5 size-3.5 shrink-0 text-risk-low"
                  aria-hidden="true"
                />
                <div className="min-w-0">
                  <p className="font-medium text-ink">
                    <span className="mr-1.5 font-mono text-[11px] text-ink-subtle">
                      {formatRedFlagCodeLabel(rule.flag_code)}
                    </span>
                    {rule.title_vn}
                  </p>
                  <p className="mt-0.5 text-[11px] text-ink-muted">
                    {rule.description_vn}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <Dialog.Root
        open={dialogFlag !== null}
        onOpenChange={(open) => {
          if (!open) setDialogFlag(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-ink/40" />
          <Dialog.Popup
            aria-label="Phản hồi chuyên gia về tín hiệu bất thường"
            className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,30rem)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-4 shadow-lg focus:outline-none"
          >
            <div className="flex items-start justify-between gap-3 border-b border-border pb-3">
              <div>
                <Dialog.Title className="text-sm font-semibold text-ink">
                  Phản hồi chuyên gia về tín hiệu bất thường
                </Dialog.Title>
                {dialogFlag ? (
                  <p className="mt-0.5 text-xs text-ink-muted">
                    {resolvedTicker} ·{" "}
                    <span className="font-mono">
                      {formatRedFlagCodeLabel(dialogFlag.flag_code)}
                    </span>{" "}
                    · Kỳ {formatDateVi(dialogFlag.period_end_date)}
                  </p>
                ) : null}
              </div>
              <Dialog.Close
                aria-label="Đóng hộp thoại phản hồi"
                className="rounded p-1 text-ink-muted hover:bg-surface-hover hover:text-ink"
              >
                <X className="size-4" aria-hidden="true" />
              </Dialog.Close>
            </div>

            <form onSubmit={handleSubmitFeedback} className="mt-3 space-y-3 text-xs">
              <div className="space-y-1">
                <label
                  htmlFor="hitl-verdict-select"
                  className="block font-medium text-ink"
                >
                  Kết luận chuyên gia
                </label>
                <select
                  id="hitl-verdict-select"
                  value={verdict}
                  onChange={(e) => setVerdict(e.target.value as HitlVerdict)}
                  className="h-9 w-full rounded border border-border bg-canvas px-2.5 text-xs text-ink focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="BENIGN_DOMAIN_EXCEPTION">
                    Ngoại lệ đặc thù ngành lành tính
                  </option>
                  <option value="CONFIRMED_RISK">
                    Xác nhận rủi ro thực sự
                  </option>
                </select>
              </div>

              <div className="space-y-1">
                <label
                  htmlFor="hitl-reason-select"
                  className="block font-medium text-ink"
                >
                  Nhóm nguyên nhân nghiệp vụ
                </label>
                <select
                  id="hitl-reason-select"
                  value={reasonCategory}
                  onChange={(e) =>
                    setReasonCategory(e.target.value as HitlReasonCategory)
                  }
                  className="h-9 w-full rounded border border-border bg-canvas px-2.5 text-xs text-ink focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  {HITL_REASON_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1">
                <label
                  htmlFor="hitl-citation-page"
                  className="block font-medium text-ink"
                >
                  Trang thuyết minh tham chiếu (tùy chọn)
                </label>
                <input
                  id="hitl-citation-page"
                  type="number"
                  min={1}
                  max={5000}
                  value={citationPage}
                  onChange={(e) => setCitationPage(e.target.value)}
                  placeholder="Ví dụ: 24"
                  className="h-9 w-full rounded border border-border bg-canvas px-2.5 text-xs text-ink focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>

              <div className="space-y-1">
                <label htmlFor="hitl-note-input" className="block font-medium text-ink">
                  Lập luận chuyên môn / bằng chứng BCTC
                </label>
                <textarea
                  id="hitl-note-input"
                  rows={3}
                  required
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Nêu rõ khoản mục thuyết minh hoặc lý do nghiệp vụ..."
                  className="w-full rounded border border-border bg-canvas p-2.5 text-xs text-ink focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>

              {feedbackError ? (
                <p
                  role="alert"
                  className="rounded border border-risk-high/30 bg-risk-high-soft/40 px-2.5 py-1.5 text-xs text-risk-high"
                >
                  {feedbackError}
                </p>
              ) : null}

              <p className="text-[11px] text-ink-subtle">
                Lưu ý quản trị mô hình: Phản hồi được lưu tách biệt để Bộ phận Kiểm định
                (Ops) đánh giá ngoại lệ theo ngành, không ghi đè nhãn huấn luyện ML.
              </p>

              <div className="flex items-center justify-end gap-2 pt-1">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setDialogFlag(null)}
                >
                  Hủy
                </Button>
                <Button type="submit" size="sm" disabled={submitting}>
                  {submitting ? "Đang gửi..." : "Lưu phản hồi"}
                </Button>
              </div>
            </form>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
