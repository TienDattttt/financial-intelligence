"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Lock, Play, ShieldAlert, Trash2 } from "lucide-react";
import { RedFlagChecklist } from "@/components/financial/RedFlagChecklist";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { PageSkeleton } from "@/components/layout/StateBlocks";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/primitives";
import { useAuthSession } from "@/hooks/use-auth-workspace";
import { privateCreditApi } from "@/lib/auth-api";
import { COMPLIANCE_DISCLAIMER_VI } from "@/lib/compliance";
import { ALTMAN_ZONE_META, BENEISH_FLAG_META, getRiskTierMeta, STATEMENT_SCOPE_LABELS, TAXONOMY_LABELS } from "@/lib/domain-labels";
import { formatDateVi, formatMultiple, formatNumber, formatPercent, formatVndBillions } from "@/lib/format";
import type { PrivateDossierPreviewResponse, PrivateScoreResponse, RedFlagItem } from "@/types/api";

const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;

const MISSING_FEATURE_LABELS_VI: Record<string, string> = {
  overdue_or_late_count_4q: "Lịch sử chậm nộp BCTC 4 quý (TT96)",
  cross_sectional_asset_size_pct: "Phân vị quy mô tài sản toàn thị trường",
  sector_z_asset_turnover: "Z-score ngành — Vòng quay tài sản",
  sector_z_cash_conversion_cycle: "Z-score ngành — Chu kỳ tiền mặt",
  sector_z_debt_to_equity: "Z-score ngành — Nợ / VCSH",
  sector_z_ebit_margin: "Z-score ngành — Biên EBIT",
  merton_dtd: "Khoảng cách vỡ nợ thị trường (Merton DTD)",
  equity_volatility_1y: "Biến động giá cổ phiếu 1 năm",
  naive_edf: "Xác suất vỡ nợ kỳ vọng thị trường (EDF)",
};

function formatMissingFeatureLabel(code: string): string {
  return MISSING_FEATURE_LABELS_VI[code] ?? code;
}

function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export default function PrivateScoringPage() {
  const queryClient = useQueryClient();
  const authSession = useAuthSession();
  const role = authSession.data?.user?.role;
  const isAllowed =
    authSession.data?.authenticated === true &&
    (role === "RESEARCHER" || role === "INTERNAL_OPS");

  const [companyName, setCompanyName] = React.useState("");
  const [taxCode, setTaxCode] = React.useState("");
  const [icbCodeLevel2, setIcbCodeLevel2] = React.useState("");
  const [statementScope, setStatementScope] = React.useState<"SEPARATE" | "CONSOLIDATED">("SEPARATE");
  const [taxonomyVersion, setTaxonomyVersion] = React.useState<"AUTO" | "VAS_TT99_2025" | "VAS_TT200_2014">("AUTO");
  const [unitMultiplier, setUnitMultiplier] = React.useState("1");
  const [selectedFile, setSelectedFile] = React.useState<File | null>(null);
  const [rateShockBps, setRateShockBps] = React.useState("200");
  const [revShockPct, setRevShockPct] = React.useState("-15");
  const [preview, setPreview] = React.useState<PrivateDossierPreviewResponse | null>(null);
  const [scoreResult, setScoreResult] = React.useState<PrivateScoreResponse | null>(null);
  const [errorMsg, setErrorMsg] = React.useState<string | null>(null);
  const [statusMsg, setStatusMsg] = React.useState<string | null>(null);
  const [downloadingTpl, setDownloadingTpl] = React.useState<string | null>(null);

  const dossiersQuery = useQuery({
    queryKey: ["private-credit-dossiers"],
    queryFn: ({ signal }) => privateCreditApi.listDossiers(signal),
    enabled: isAllowed,
  });

  const parseMutation = useMutation({
    mutationFn: (formData: FormData) => privateCreditApi.parseWorkbook(formData),
    onSuccess: (data) => {
      setPreview(data);
      setScoreResult(null);
      setErrorMsg(null);
      setStatusMsg(
        data.qg1.can_score
          ? "Đã kiểm tra cân bằng QG1 thành công. Sẵn sàng chấm điểm cục bộ."
          : "Tài liệu chưa đạt kiểm định cân bằng QG1; vui lòng kiểm tra lại số liệu.",
      );
      void queryClient.invalidateQueries({ queryKey: ["private-credit-dossiers"] });
    },
    onError: (err) => setErrorMsg(err instanceof Error ? err.message : "Không thể phân tích file Excel tải lên."),
  });

  const scoreMutation = useMutation({
    mutationFn: ({ dossierId }: { dossierId: string }) =>
      privateCreditApi.scoreDossier(dossierId, {
        interest_rate_shock_bps: Number.parseFloat(rateShockBps) || 200,
        revenue_shock_pct: (Number.parseFloat(revShockPct) || -15) / 100,
      }),
    onSuccess: (data) => {
      setScoreResult(data);
      setErrorMsg(null);
      setStatusMsg(`Đã hoàn tất chấm điểm độc lập (${formatNumber(data.elapsed_ms, 0)} ms) ở chế độ ACCOUNTING_ONLY_MODE.`);
      void queryClient.invalidateQueries({ queryKey: ["private-credit-dossiers"] });
    },
    onError: (err) => setErrorMsg(err instanceof Error ? err.message : "Không thể chạy mô hình chấm điểm cho hồ sơ."),
  });

  const deleteMutation = useMutation({
    mutationFn: (dossierId: string) => privateCreditApi.deleteDossier(dossierId),
    onSuccess: (_res, dossierId) => {
      if (preview?.dossier_id === dossierId) {
        setPreview(null);
        setScoreResult(null);
      }
      setErrorMsg(null);
      setStatusMsg("Đã xóa hồ sơ doanh nghiệp chưa niêm yết khỏi bộ nhớ cục bộ.");
      void queryClient.invalidateQueries({ queryKey: ["private-credit-dossiers"] });
    },
    onError: (err) => setErrorMsg(err instanceof Error ? err.message : "Không thể xóa hồ sơ."),
  });

  const handleDownloadTemplate = async (taxonomy: "VAS_TT99_2025" | "VAS_TT200_2014") => {
    setDownloadingTpl(taxonomy);
    setErrorMsg(null);
    try {
      const blob = await privateCreditApi.downloadTemplate(taxonomy);
      triggerBlobDownload(blob, `finintel_mau_${taxonomy.toLowerCase()}.xlsx`);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Không thể tải file mẫu Excel chuẩn VAS.");
    } finally {
      setDownloadingTpl(null);
    }
  };

  const handleUploadSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMsg(null);
    setStatusMsg(null);
    const trimmedCompany = companyName.trim();
    if (trimmedCompany.length < 2) {
      setErrorMsg("Vui lòng nhập tên doanh nghiệp tối thiểu 2 ký tự.");
      return;
    }
    if (!selectedFile) {
      setErrorMsg("Vui lòng chọn tệp báo cáo tài chính định dạng .xlsx.");
      return;
    }
    if (!selectedFile.name.toLowerCase().endsWith(".xlsx")) {
      setErrorMsg("Chỉ chấp nhận tệp Excel chuẩn .xlsx (từ chối .xlsm hoặc định dạng khác).");
      return;
    }
    if (selectedFile.size > MAX_UPLOAD_BYTES) {
      setErrorMsg("Dung lượng tệp vượt quá giới hạn bảo mật 15 MB.");
      return;
    }
    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("company_name", trimmedCompany);
    if (taxCode.trim()) formData.append("tax_code", taxCode.trim());
    if (icbCodeLevel2.trim()) formData.append("icb_code_level2", icbCodeLevel2.trim());
    formData.append("statement_scope", statementScope);
    formData.append("taxonomy_version", taxonomyVersion);
    formData.append("unit_multiplier", unitMultiplier);
    parseMutation.mutate(formData);
  };

  const handleLoadDossier = async (dossierId: string) => {
    setErrorMsg(null);
    try {
      const loaded = await privateCreditApi.getDossier(dossierId);
      setPreview(loaded);
      setScoreResult(null);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Không thể tải hồ sơ đã lưu.");
    }
  };

  if (authSession.isLoading) return <PageSkeleton />;

  if (!isAllowed) {
    return (
      <div className="mx-auto max-w-2xl py-8">
        <Card className="border-border">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Lock className="size-4 text-ink-muted" aria-hidden="true" />
              <span>Không gian chấm điểm doanh nghiệp chưa niêm yết</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-ink-muted">
            <p>
              Tính năng tải BCTC Excel chuẩn VAS (TT200/TT99) và chấm điểm độc lập chỉ dành cho tài khoản{" "}
              <strong>RESEARCHER</strong> hoặc <strong>INTERNAL_OPS</strong>. Dữ liệu được xử lý cục bộ trong phiên
              làm việc riêng và không ghi vào kho thị trường công khai.
            </p>
            <div className="flex flex-wrap gap-2">
              <Link href="/auth/login" className="inline-flex h-9 items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground">
                Đăng nhập tài khoản phân tích
              </Link>
              <Link href="/companies" className="inline-flex h-9 items-center rounded-md border border-border bg-surface px-3 text-xs font-medium text-ink">
                Quay lại thị trường niêm yết
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const ml = scoreResult?.ml_assessment;
  const tierMeta = getRiskTierMeta(ml?.risk_tier);
  const altmanMeta = scoreResult?.classical.altman_zone ? ALTMAN_ZONE_META[scoreResult.classical.altman_zone] : undefined;
  const beneishMeta = scoreResult?.classical.beneish_flag ? BENEISH_FLAG_META[scoreResult.classical.beneish_flag] : undefined;
  const mappedFlags: RedFlagItem[] = (scoreResult?.red_flags ?? []).map((f) => ({
    period_end_date: f.period_end_date,
    flag_code: f.flag_code,
    severity: f.severity,
    flag_title_vn: f.flag_title_vn,
    explanation_text_vn: f.explanation_text_vn,
    evidence_json: f.evidence_json,
  }));

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 border-b border-border pb-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-ink">Chấm điểm BCTC Doanh nghiệp chưa niêm yết</h1>
          <p className="mt-1 text-xs text-ink-muted">
            Xử lý cục bộ chuẩn VAS (TT200/2014 &amp; TT99/2025) · Kiểm định cân bằng QG1 · Chế độ kế toán thuần túy (cô lập hoàn toàn khỏi kho dữ liệu thị trường công khai).
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" disabled={downloadingTpl !== null} onClick={() => void handleDownloadTemplate("VAS_TT99_2025")}>
            <Download className="size-3.5" aria-hidden="true" />
            <span>Tải mẫu Excel TT99/2025 (.xlsx)</span>
          </Button>
          <Button type="button" variant="outline" size="sm" disabled={downloadingTpl !== null} onClick={() => void handleDownloadTemplate("VAS_TT200_2014")}>
            <Download className="size-3.5" aria-hidden="true" />
            <span>Tải mẫu Excel TT200/2014 (.xlsx)</span>
          </Button>
        </div>
      </div>

      {errorMsg ? (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-risk-high/40 bg-risk-high-soft/40 p-3 text-xs text-risk-high">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{errorMsg}</span>
        </div>
      ) : null}

      {statusMsg ? (
        <div role="status" className="flex items-start gap-2 rounded-md border border-risk-low/40 bg-risk-low-soft/40 p-3 text-xs text-risk-low">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{statusMsg}</span>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-5">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <FileSpreadsheet className="size-4 text-accent-ink" aria-hidden="true" />
              <span>1. Tải lên &amp; Phân tích BCTC (.xlsx)</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleUploadSubmit} className="space-y-3 text-xs">
              <div className="space-y-1">
                <label htmlFor="priv-company-name" className="block font-medium text-ink">Tên doanh nghiệp</label>
                <Input id="priv-company-name" value={companyName} onChange={(e) => setCompanyName(e.target.value)} placeholder="Ví dụ: Công ty TNHH Sản xuất Minh Long" required />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1"><label htmlFor="priv-tax-code" className="block font-medium text-ink">Mã số thuế (tùy chọn)</label><Input id="priv-tax-code" value={taxCode} onChange={(e) => setTaxCode(e.target.value)} placeholder="0301234567" /></div>
                <div className="space-y-1"><label htmlFor="priv-icb-code" className="block font-medium text-ink">Mã ngành ICB cấp 2</label><Input id="priv-icb-code" value={icbCodeLevel2} onChange={(e) => setIcbCodeLevel2(e.target.value)} placeholder="Ví dụ: 1700" /></div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div className="space-y-1"><label htmlFor="priv-scope" className="block font-medium text-ink">Phạm vi BCTC</label><Select id="priv-scope" value={statementScope} onChange={(e) => setStatementScope(e.target.value as "SEPARATE" | "CONSOLIDATED")}><option value="SEPARATE">Riêng lẻ</option><option value="CONSOLIDATED">Hợp nhất</option></Select></div>
                <div className="space-y-1"><label htmlFor="priv-taxonomy" className="block font-medium text-ink">Chuẩn VAS</label><Select id="priv-taxonomy" value={taxonomyVersion} onChange={(e) => setTaxonomyVersion(e.target.value as "AUTO" | "VAS_TT99_2025" | "VAS_TT200_2014")}><option value="AUTO">Tự nhận diện</option><option value="VAS_TT99_2025">TT99/2025</option><option value="VAS_TT200_2014">TT200/2014</option></Select></div>
                <div className="space-y-1"><label htmlFor="priv-unit" className="block font-medium text-ink">Đơn vị số liệu</label><Select id="priv-unit" value={unitMultiplier} onChange={(e) => setUnitMultiplier(e.target.value)}><option value="1">Đồng (VND)</option><option value="1000">Nghìn đồng</option><option value="1000000">Triệu đồng</option><option value="1000000000">Tỷ đồng</option></Select></div>
              </div>
              <div className="space-y-1">
                <label htmlFor="priv-file-input" className="block font-medium text-ink">Tệp Excel BCTC (tối đa 15 MB, chỉ .xlsx)</label>
                <input id="priv-file-input" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)} className="block w-full rounded border border-border bg-canvas px-2.5 py-2 text-xs text-ink file:mr-2 file:rounded file:border-0 file:bg-surface-muted file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-ink" />
              </div>
              <Button type="submit" size="sm" disabled={parseMutation.isPending}>{parseMutation.isPending ? "Đang kiểm tra QG1..." : "Tải lên & Kiểm tra QG1"}</Button>
            </form>
            {(dossiersQuery.data?.items?.length ?? 0) > 0 ? (
              <div className="mt-4 space-y-2 border-t border-border pt-3">
                <p className="text-xs font-semibold text-ink">Hồ sơ trong phiên cá nhân</p>
                <div className="space-y-1.5">
                  {dossiersQuery.data?.items.slice(0, 5).map((item) => (
                    <div key={item.dossier_id} className="flex items-center justify-between gap-2 rounded border border-border bg-canvas px-2.5 py-1.5 text-xs">
                      <button type="button" onClick={() => void handleLoadDossier(item.dossier_id)} className="min-w-0 flex-1 text-left hover:text-accent-ink">
                        <span className="block truncate font-medium text-ink">{item.company_name}</span>
                        <span className="text-[11px] text-ink-muted">{TAXONOMY_LABELS[item.taxonomy_version] ?? item.taxonomy_version} · QG1: {item.qg1_status}</span>
                      </button>
                      <Button type="button" variant="ghost" size="xs" aria-label={`Xóa hồ sơ ${item.company_name}`} onClick={() => deleteMutation.mutate(item.dossier_id)}><Trash2 className="size-3.5 text-ink-muted" aria-hidden="true" /></Button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card className="lg:col-span-7">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">2. Kết quả Kiểm định Kế toán QG1 &amp; Chạy Mô hình</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-xs">
            {!preview ? (
              <p className="rounded border border-dashed border-border p-6 text-center text-ink-muted">
                Chưa có hồ sơ được nạp. Hãy tải mẫu Excel chuẩn VAS ở góc trên phải hoặc tải tệp .xlsx của doanh nghiệp để kiểm tra cân bằng CĐKT trước khi chấm điểm.
              </p>
            ) : (
              <>
                <div className="flex flex-wrap items-start justify-between gap-3 rounded border border-border bg-canvas p-3">
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-ink">{preview.company_name}</p>
                    <p className="text-[11px] text-ink-muted">
                      {preview.tax_code ? `MST ${preview.tax_code} · ` : ""}
                      {STATEMENT_SCOPE_LABELS[preview.statement_scope] ?? preview.statement_scope} ·{" "}
                      {TAXONOMY_LABELS[preview.taxonomy_version] ?? preview.taxonomy_version} · Kỳ: {preview.periods.join(", ")}
                    </p>
                    <p className="text-[11px] text-ink-subtle">
                      Lưu cục bộ 30 ngày (tới {formatDateVi(preview.retention_expires_at)}) · SHA-256:{" "}
                      <span className="font-mono">{preview.source_sha256.slice(0, 12)}</span>
                    </p>
                  </div>
                  <RiskBadge tone={preview.qg1.status === "PASS" ? "low" : preview.qg1.status === "WARNING" ? "mid" : "high"} label={`QG1: ${preview.qg1.status}`} />
                </div>

                {(preview.qg1.discrepancies ?? []).length > 0 ? (
                  <div role="alert" className="space-y-2 rounded border border-risk-high/40 bg-risk-high-soft/30 p-3">
                    <p className="flex items-center gap-1.5 font-semibold text-risk-high">
                      <ShieldAlert className="size-4" aria-hidden="true" />
                      <span>Phát hiện sai lệch kế toán QG1 — Chặn chấm điểm</span>
                    </p>
                    <ul className="list-disc space-y-1 pl-4 text-ink">
                      {(preview.qg1.discrepancies ?? []).map((d, idx) => (
                        <li key={`${d.rule_code}-${idx}`}>{d.message_vi}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <div className="space-y-1">
                    <label htmlFor="priv-rate-shock" className="block font-medium text-ink">Cú sốc lãi suất Stress Test (bps)</label>
                    <Input id="priv-rate-shock" type="number" value={rateShockBps} onChange={(e) => setRateShockBps(e.target.value)} />
                  </div>
                  <div className="space-y-1">
                    <label htmlFor="priv-rev-shock" className="block font-medium text-ink">Cú sốc doanh thu Stress Test (%)</label>
                    <Input id="priv-rev-shock" type="number" value={revShockPct} onChange={(e) => setRevShockPct(e.target.value)} />
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" size="sm" disabled={!preview.qg1.can_score || scoreMutation.isPending} onClick={() => scoreMutation.mutate({ dossierId: preview.dossier_id })}>
                    <Play className="size-3.5" aria-hidden="true" />
                    <span>{scoreMutation.isPending ? "Đang tính toán Layer 4..." : "Chấm điểm định lượng (ACCOUNTING_ONLY_MODE)"}</span>
                  </Button>
                  <Button type="button" variant="outline" size="sm" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate(preview.dossier_id)}>
                    <Trash2 className="size-3.5" aria-hidden="true" />
                    <span>Xóa hồ sơ cục bộ</span>
                  </Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {scoreResult ? (
        <div className="space-y-4">
          <div role="note" className="rounded-md border border-risk-mid/40 bg-risk-mid-soft/30 p-3 text-xs text-ink">
            <p className="font-semibold text-risk-mid">Chế độ đánh giá: {scoreResult.ml_assessment.ml_mode} (Chỉ sử dụng dữ liệu kế toán)</p>
            <p className="mt-1 text-ink-muted">{scoreResult.ml_assessment.coverage_warning_vi}</p>
            <p className="mt-1 text-[11px] text-ink-subtle">
              Độ phủ đặc trưng kế toán: <strong className="num">{formatPercent(scoreResult.ml_assessment.accounting_feature_coverage, { decimals: 0 })}</strong> · Nhóm chỉ báo thị trường/TT96 không áp dụng:{" "}
              {scoreResult.ml_assessment.missing_market_tt96_features.map(formatMissingFeatureLabel).join(", ")}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Card>
              <CardContent className="space-y-1 p-3.5">
                <p className="text-[11px] text-ink-muted">Phân nhóm &amp; Xác suất căng thẳng</p>
                <div className="flex items-center gap-2">
                  <RiskBadge tone={tierMeta.tone} label={tierMeta.badgeText} />
                  <span className="num text-base font-bold text-ink">{formatPercent(ml?.calibrated_distress_prob, { decimals: 2 })}</span>
                </div>
                <p className="text-[11px] text-ink-subtle">{tierMeta.label}</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="space-y-1 p-3.5">
                <p className="text-[11px] text-ink-muted">Altman Z″ (Thị trường mới nổi)</p>
                <div className="flex items-center gap-2">
                  <span className="num text-base font-bold text-ink">{formatNumber(scoreResult.classical.altman_z_double_prime, 2)}</span>
                  {altmanMeta ? <RiskBadge tone={altmanMeta.tone} label={altmanMeta.shortLabel} /> : null}
                </div>
                <p className="text-[11px] text-ink-subtle">
                  Piotroski F: <strong className="num">{scoreResult.classical.piotroski_f_score ?? "N/A"}</strong> ({scoreResult.classical.piotroski_valid_components_count ?? 0}/9)
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="space-y-1 p-3.5">
                <p className="text-[11px] text-ink-muted">Sinh lời &amp; Đòn bẩy (Layer 4A)</p>
                <p className="num text-sm font-semibold text-ink">
                  ROA {formatPercent(scoreResult.ratios.roa, { decimals: 1 })} · ROE {formatPercent(scoreResult.ratios.roe_total, { decimals: 1 })}
                </p>
                <p className="num text-[11px] text-ink-muted">
                  Nợ/VCSH {formatMultiple(scoreResult.ratios.debt_to_equity, 2)} · Khả năng trả lãi {formatMultiple(scoreResult.ratios.interest_coverage, 2)}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="space-y-1 p-3.5">
                <p className="text-[11px] text-ink-muted">Stress Test IMF WP/15/216</p>
                <p className="num text-sm font-semibold text-ink">EBIT sau sốc: {formatVndBillions(scoreResult.stress_test.stressed_ebit_vnd)}</p>
                <p className="num text-[11px] text-ink-muted">
                  ICR sau sốc: {formatMultiple(scoreResult.stress_test.stressed_interest_coverage, 2)} · {beneishMeta ? `Beneish: ${beneishMeta.shortLabel}` : "Beneish: N/A"}
                </p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Cờ đỏ bất thường kế toán VAS &amp; Phản hồi chuyên gia</CardTitle>
            </CardHeader>
            <CardContent>
              <RedFlagChecklist
                dictionary={[]}
                triggeredFlags={mappedFlags}
                latestPeriodEndDate={scoreResult.scored_period_end_date}
                ticker={scoreResult.tax_code || "PRIVATE"}
                icbCodeLevel2={preview?.icb_code_level2 ?? null}
              />
            </CardContent>
          </Card>

          <p className="text-[11px] text-ink-subtle">{COMPLIANCE_DISCLAIMER_VI}</p>
        </div>
      ) : null}
    </div>
  );
}
