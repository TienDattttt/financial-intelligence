import { CheckCircle2, ShieldAlert } from "lucide-react";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { Card, CardContent } from "@/components/ui/card";
import { SEVERITY_META } from "@/lib/domain-labels";
import { formatDateVi, formatNumber } from "@/lib/format";
import type { RedFlagDictionaryItem, RedFlagItem } from "@/types/api";

function parseEvidence(rawJson: string | null | undefined): [string, string][] {
  if (!rawJson) return [];
  try {
    const parsed = JSON.parse(rawJson) as Record<string, unknown>;
    return Object.entries(parsed)
      .filter(([k]) => k !== "source_filing_ids")
      .slice(0, 6)
      .map(([k, v]) => {
        if (typeof v === "number" && Number.isFinite(v)) {
          return [k, formatNumber(v, Math.abs(v) < 2 ? 3 : 1)];
        }
        return [k, v === null || v === undefined ? "N/A" : String(v)];
      });
  } catch {
    return [];
  }
}

export function RedFlagChecklist({
  dictionary,
  triggeredFlags,
  latestPeriodEndDate,
}: {
  dictionary: readonly RedFlagDictionaryItem[];
  triggeredFlags: readonly RedFlagItem[];
  latestPeriodEndDate?: string | null;
}) {
  const activeForLatest = latestPeriodEndDate
    ? triggeredFlags.filter((f) => f.period_end_date === latestPeriodEndDate)
    : triggeredFlags;
  const activeCodes = new Set(activeForLatest.map((f) => f.flag_code));

  const passedRules = dictionary.filter((rule) => !activeCodes.has(rule.flag_code));

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink">
          <ShieldAlert className="size-4 text-risk-high" aria-hidden="true" />
          <span>
            Quy tắc phát hiện bất thường đang kích hoạt ({activeForLatest.length}/
            {dictionary.length || 9})
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
              const evidence = parseEvidence(flag.evidence_json);
              return (
                <Card
                  key={`${flag.flag_code}-${flag.period_end_date}`}
                  className="border-risk-high/25"
                >
                  <CardContent className="space-y-2 p-3.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-surface-muted px-1.5 py-0.5 font-mono text-[11px] font-medium text-ink">
                          {flag.flag_code.split("_").slice(0, 2).join("_")}
                        </span>
                        <span className="text-sm font-semibold text-ink">
                          {flag.flag_title_vn ?? flag.flag_code}
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
                        {evidence.map(([k, v]) => (
                          <span
                            key={k}
                            className="rounded border border-border bg-canvas px-2 py-0.5 font-mono text-[11px] text-ink"
                          >
                            {k}: <strong className="num">{v}</strong>
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {flag.filing_id ? (
                      <p className="font-mono text-[11px] text-ink-subtle">
                        Hồ sơ: {flag.filing_id} · Kỳ {formatDateVi(flag.period_end_date)}
                      </p>
                    ) : null}
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
                      {rule.flag_code.split("_").slice(0, 2).join("_")}
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
    </div>
  );
}
