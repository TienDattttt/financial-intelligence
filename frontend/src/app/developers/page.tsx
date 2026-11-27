"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, RefreshCw, ShieldAlert, Trash2, Webhook } from "lucide-react";
import { PageSkeleton } from "@/components/layout/StateBlocks";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { KpiCell, KpiStrip } from "@/components/ui/kpi-strip";
import { Badge, Input, Select } from "@/components/ui/primitives";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { useAuthSession } from "@/hooks/use-auth-workspace";
import { b2bApi } from "@/lib/auth-api";
import { COMPLIANCE_DISCLAIMER_VI } from "@/lib/compliance";
import { formatDateVi } from "@/lib/format";
import type { ApiKeyMetadataItem, ApiUsageDailyPoint } from "@/types/api";

type SnippetLang = "python" | "curl";

const buildSnippets = (baseUrl: string): Record<SnippetLang, string> => ({
  python: `from finintel_sdk import FinIntelClient, StressScenarioInput, verify_webhook_signature

with FinIntelClient(api_key="fi_res_YOUR_KEY", base_url="${baseUrl}") as client:
    risk = client.get_company_risk("HPG")
    screener = client.screen_market(exchange="HOSE", risk_tier="R4", limit=20)
    stress = client.run_stress_test(StressScenarioInput(interest_rate_shock_bps=200.0, tickers=["HPG"]))`,
  curl: `curl -sS -H "X-API-Key: fi_res_YOUR_KEY" \\
  "${baseUrl}/api/v3/open/companies/HPG/risk?statement_scope=CONSOLIDATED&period_view=TTM"
# Tái sử dụng ETag để nhận 304 Not Modified:
curl -i -H "X-API-Key: fi_res_YOUR_KEY" -H 'If-None-Match: "snap_id"' \\
  "${baseUrl}/api/v3/open/screener?limit=20"`,
});

function summarizeDailyUsage(points: readonly ApiUsageDailyPoint[]): Array<{ date: string; requests: number; cacheHits: number }> {
  const byDate = new Map<string, { requests: number; cacheHits: number }>();
  for (const p of points) {
    const cur = byDate.get(p.usage_date) ?? { requests: 0, cacheHits: 0 };
    byDate.set(p.usage_date, { requests: cur.requests + p.request_count, cacheHits: cur.cacheHits + p.cache_hits });
  }
  return Array.from(byDate.entries())
    .map(([date, val]) => ({ date, requests: val.requests, cacheHits: val.cacheHits }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

export default function DevelopersPortalPage() {
  const queryClient = useQueryClient();
  const authSession = useAuthSession();
  const isAuthenticated = authSession.data?.authenticated === true;
  const user = authSession.data?.user ?? null;
  const canUseOpenApi = isAuthenticated && (user?.role === "RESEARCHER" || user?.role === "INTERNAL_OPS");

  const [keyName, setKeyName] = React.useState("");
  const [prefixMode, setPrefixMode] = React.useState<"res" | "live">("res");
  const [oneTimeSecret, setOneTimeSecret] = React.useState<string | null>(null);
  const [copiedSecret, setCopiedSecret] = React.useState(false);
  const [whLabel, setWhLabel] = React.useState("");
  const [whUrl, setWhUrl] = React.useState("");
  const [whSecret, setWhSecret] = React.useState("");
  const [oneTimeWhSecret, setOneTimeWhSecret] = React.useState<string | null>(null);
  const [snippetLang, setSnippetLang] = React.useState<SnippetLang>("python");
  const [copiedSnippet, setCopiedSnippet] = React.useState(false);
  const [feedback, setFeedback] = React.useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [nowMs] = React.useState<number>(() => Date.now());

  const keysQuery = useQuery({ queryKey: ["b2b", "keys"], queryFn: ({ signal }) => b2bApi.listKeys(signal), enabled: canUseOpenApi });
  const refNowMs = keysQuery.dataUpdatedAt > 0 ? keysQuery.dataUpdatedAt : nowMs;
  const usageQuery = useQuery({ queryKey: ["b2b", "usage"], queryFn: ({ signal }) => b2bApi.getUsage(undefined, undefined, signal), enabled: canUseOpenApi });
  const webhooksQuery = useQuery({ queryKey: ["b2b", "webhooks"], queryFn: ({ signal }) => b2bApi.listWebhooks(signal), enabled: canUseOpenApi });

  const createKeyMut = useMutation({
    mutationFn: () => b2bApi.createKey({ name: keyName.trim(), prefix_mode: prefixMode, scopes: ["read:derived_risk", "write:webhooks"] }),
    onSuccess: (res) => {
      setOneTimeSecret(res.plaintext_key); setCopiedSecret(false); setKeyName("");
      setFeedback({ kind: "ok", text: `Đã tạo API Key "${res.key.name}". Hãy sao chép khóa bí mật ngay bây giờ.` });
      void queryClient.invalidateQueries({ queryKey: ["b2b", "keys"] });
    },
    onError: (err: Error) => setFeedback({ kind: "err", text: err.message }),
  });

  const rotateKeyMut = useMutation({
    mutationFn: (keyId: string) => b2bApi.rotateKey(keyId),
    onSuccess: (res) => {
      setOneTimeSecret(res.plaintext_key); setCopiedSecret(false);
      setFeedback({ kind: "ok", text: `Đã xoay khóa "${res.key.name}". Khóa cũ đã bị thu hồi tức thì.` });
      void queryClient.invalidateQueries({ queryKey: ["b2b", "keys"] });
    },
    onError: (err: Error) => setFeedback({ kind: "err", text: err.message }),
  });

  const revokeKeyMut = useMutation({
    mutationFn: (keyId: string) => b2bApi.revokeKey(keyId),
    onSuccess: () => {
      setFeedback({ kind: "ok", text: "Đã thu hồi API Key. Mọi yêu cầu tiếp theo dùng khóa này sẽ nhận 401." });
      void queryClient.invalidateQueries({ queryKey: ["b2b", "keys"] });
    },
    onError: (err: Error) => setFeedback({ kind: "err", text: err.message }),
  });

  const createWhMut = useMutation({
    mutationFn: () => b2bApi.createWebhook({ label: whLabel.trim(), url: whUrl.trim(), secret_hmac: whSecret.trim() || null }),
    onSuccess: (res) => {
      setOneTimeWhSecret(res.secret_hmac); setWhLabel(""); setWhUrl(""); setWhSecret("");
      setFeedback({ kind: "ok", text: "Đã đăng ký Webhook nhận AlertEvent." });
      void queryClient.invalidateQueries({ queryKey: ["b2b", "webhooks"] });
    },
    onError: (err: Error) => setFeedback({ kind: "err", text: err.message }),
  });

  const deleteWhMut = useMutation({
    mutationFn: (id: string) => b2bApi.deleteWebhook(id),
    onSuccess: () => {
      setFeedback({ kind: "ok", text: "Đã xóa Webhook endpoint." });
      void queryClient.invalidateQueries({ queryKey: ["b2b", "webhooks"] });
    },
    onError: (err: Error) => setFeedback({ kind: "err", text: err.message }),
  });

  const testWhMut = useMutation({
    mutationFn: (id: string) => b2bApi.testWebhook(id),
    onSuccess: (res) => {
      setFeedback({ kind: res.delivered ? "ok" : "err", text: res.detail });
      void queryClient.invalidateQueries({ queryKey: ["b2b", "webhooks"] });
    },
    onError: (err: Error) => setFeedback({ kind: "err", text: err.message }),
  });

  if (authSession.isLoading) return <PageSkeleton />;

  if (!isAuthenticated || !user) {
    return (
      <div className="mx-auto max-w-xl py-10">
        <Card>
          <CardHeader><CardTitle className="text-base">Cổng Open Research API (/developers)</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-xs text-ink-muted">
            <p>Vui lòng đăng nhập bằng tài khoản Nhà nghiên cứu (RESEARCHER) hoặc Vận hành nội bộ (INTERNAL_OPS) để tạo API Key và tích hợp Python SDK.</p>
            <div className="flex flex-wrap gap-2">
              <Link href="/auth/login?next=/developers" className={buttonVariants({ variant: "default", size: "sm" })}>Đăng nhập</Link>
              <Link href="/auth/register" className={buttonVariants({ variant: "outline", size: "sm" })}>Đăng ký tài khoản</Link>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!canUseOpenApi) {
    return (
      <div className="mx-auto max-w-xl py-10">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base text-risk-mid">
              <ShieldAlert className="size-4" aria-hidden="true" />
              Cần quyền Nhà nghiên cứu (RESEARCHER) để cấp API Key
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-xs text-ink-muted">
            <p>Tài khoản hiện tại thuộc nhóm Người dùng tiêu chuẩn (USER). Để bảo vệ hạ tầng miễn phí và nguồn dữ liệu gốc, Open Research API chỉ cấp phát miễn phí cho giảng viên, sinh viên tài chính và nhà nghiên cứu đã được duyệt vai trò RESEARCHER.</p>
            <p className="rounded-md border border-border bg-surface-muted px-3 py-2 text-ink">
              Hướng dẫn xét duyệt: Gửi email học thuật (.edu.vn) hoặc đề cương nghiên cứu tới ban quản trị nội bộ kèm mã tài khoản trong trang Hồ sơ (/profile) để nâng vai trò sang RESEARCHER.
            </p>
            <div className="flex flex-wrap gap-2">
              <Link href="/profile" className={buttonVariants({ variant: "default", size: "sm" })}>Mở Hồ sơ cá nhân</Link>
              <Link href="/" className={buttonVariants({ variant: "outline", size: "sm" })}>Quay lại Tổng quan</Link>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const keys: readonly ApiKeyMetadataItem[] = keysQuery.data?.items ?? [];
  const isKeyExpired = (k: ApiKeyMetadataItem): boolean =>
    Boolean(k.expires_at && Date.parse(k.expires_at) <= refNowMs);
  const isKeyActive = (k: ApiKeyMetadataItem): boolean =>
    k.is_active && !k.revoked_at && !isKeyExpired(k);
  const activeKeyCount = keys.filter(isKeyActive).length;
  const dailySummary = summarizeDailyUsage(usageQuery.data?.daily ?? []);
  const maxDayReq = Math.max(1, ...dailySummary.map((d) => d.requests));
  const totalReq = usageQuery.data?.total_requests ?? 0;
  const totalHits = usageQuery.data?.total_cache_hits ?? 0;
  const rawEnabled = keysQuery.data?.raw_redistribution_enabled === true;
  const apiBaseUrl = typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8000";
  const snippets = buildSnippets(apiBaseUrl);

  const copyText = async (val: string, kind: "secret" | "snippet") => {
    try {
      await navigator.clipboard.writeText(val);
      if (kind === "secret") setCopiedSecret(true);
      else setCopiedSnippet(true);
    } catch {
      setFeedback({ kind: "err", text: "Không thể truy cập clipboard trình duyệt." });
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink">Cổng Open Research API &amp; Python SDK</h1>
          <p className="text-xs text-ink-muted">Quản lý API Key miễn phí, giới hạn lưu lượng bảo vệ hạ tầng và Webhook sự kiện rủi ro.</p>
        </div>
        <Badge className={rawEnabled ? "border border-border bg-risk-low-soft text-risk-low" : "border border-border bg-surface-muted text-ink"}>
          {rawEnabled ? "Phạm vi: Phái sinh + BCTC gốc" : "Phạm vi: Chỉ chỉ báo phái sinh (read:derived_risk)"}
        </Badge>
      </div>

      {feedback ? (
        <div role={feedback.kind === "err" ? "alert" : "status"} className={feedback.kind === "err" ? "rounded-md border border-risk-high/40 bg-risk-high-soft px-3 py-2 text-xs text-risk-high" : "rounded-md border border-risk-low/40 bg-risk-low-soft px-3 py-2 text-xs text-risk-low"}>
          {feedback.text}
        </div>
      ) : null}

      {oneTimeSecret ? (
        <div role="status" aria-live="polite" className="space-y-2 rounded-lg border border-border-strong bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <strong className="text-xs font-semibold text-ink">Khóa bí mật API mới (chỉ hiển thị duy nhất một lần)</strong>
            <Button type="button" size="xs" variant="outline" onClick={() => setOneTimeSecret(null)}>Đã lưu khóa</Button>
          </div>
          <p className="text-[11px] text-risk-mid">Sao chép và lưu khóa vào biến môi trường ngay bây giờ. Sau khi tải lại trang, hệ thống chỉ hiển thị tiền tố vì khóa được băm SHA-256.</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-md border border-border bg-canvas px-2.5 py-1.5 font-mono text-xs text-ink">{oneTimeSecret}</code>
            <Button type="button" size="xs" onClick={() => void copyText(oneTimeSecret, "secret")}>
              {copiedSecret ? <Check className="mr-1 size-3.5" aria-hidden="true" /> : <Copy className="mr-1 size-3.5" aria-hidden="true" />}
              {copiedSecret ? "Đã sao chép" : "Sao chép khóa"}
            </Button>
          </div>
        </div>
      ) : null}

      <KpiStrip ariaLabel="Thống kê Open Research API">
        <KpiCell>
          <span className="text-[11px] text-ink-muted">Lượt gọi (14 ngày)</span>
          <span className="num text-lg font-semibold text-ink">{totalReq.toLocaleString("vi-VN")}</span>
        </KpiCell>
        <KpiCell>
          <span className="text-[11px] text-ink-muted">Cache ETag (304)</span>
          <span className="num text-lg font-semibold text-ink">{totalHits.toLocaleString("vi-VN")}</span>
        </KpiCell>
        <KpiCell>
          <span className="text-[11px] text-ink-muted">API Key đang hoạt động</span>
          <span className="num text-lg font-semibold text-ink">{activeKeyCount.toLocaleString("vi-VN")}</span>
        </KpiCell>
        <KpiCell>
          <span className="text-[11px] text-ink-muted">Ngưỡng bảo vệ miễn phí</span>
          <span className="num text-sm font-semibold text-ink">
            {(keysQuery.data?.default_rate_limit_per_min ?? 30).toLocaleString("vi-VN")} lần/phút · {(keysQuery.data?.default_daily_limit ?? 1000).toLocaleString("vi-VN")} lần/ngày
          </span>
        </KpiCell>
      </KpiStrip>

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-7">
          <CardHeader className="py-3"><CardTitle className="flex items-center gap-1.5 text-sm"><KeyRound className="size-4" aria-hidden="true" />Tạo &amp; Quản lý API Key</CardTitle></CardHeader>
          <CardContent className="space-y-4 p-4 text-xs">
            <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (keyName.trim()) createKeyMut.mutate(); }}>
              <label className="min-w-[180px] flex-1 space-y-1">
                <span className="text-[11px] font-medium text-ink-muted">Tên khóa nghiên cứu</span>
                <Input aria-label="Tên API Key" placeholder="VD: Luận văn rủi ro HOSE" value={keyName} onChange={(e) => setKeyName(e.target.value)} />
              </label>
              <label className="space-y-1">
                <span className="text-[11px] font-medium text-ink-muted">Tiền tố</span>
                <Select aria-label="Loại tiền tố khóa" value={prefixMode} onChange={(e) => setPrefixMode(e.target.value as "res" | "live")}>
                  <option value="res">fi_res_ (Nghiên cứu)</option>
                  <option value="live">fi_live_ (Tích hợp)</option>
                </Select>
              </label>
              <Button type="submit" size="sm" disabled={!keyName.trim() || createKeyMut.isPending}>Tạo API Key</Button>
            </form>

            {keys.length === 0 ? (
              <p className="rounded-md border border-border bg-canvas px-3 py-3 text-ink-muted">Chưa có API Key nào. Tạo khóa đầu tiên để bắt đầu gọi /api/v3/open/*.</p>
            ) : (
              <div className="overflow-x-auto rounded-md border border-border">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-border bg-surface-muted text-ink-muted">
                      <th className="px-2.5 py-2 text-left">Tên / Tiền tố</th>
                      <th className="px-2.5 py-2 text-left">Trạng thái</th>
                      <th className="px-2.5 py-2 text-right">Giới hạn</th>
                      <th className="px-2.5 py-2 text-right">Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {keys.map((k) => {
                      const expired = isKeyExpired(k);
                      const active = isKeyActive(k);
                      const statusLabel = active ? "Đang hoạt động" : expired && !k.revoked_at ? "Đã hết hạn" : "Đã thu hồi";
                      return (
                        <tr key={k.key_id} className="border-b border-border last:border-b-0">
                          <td className="px-2.5 py-2">
                            <div className="font-medium text-ink">{k.name}</div>
                            <div className="font-mono text-[11px] text-ink-subtle">{k.key_prefix}_••••••••</div>
                          </td>
                          <td className="px-2.5 py-2">
                            <Badge className={active ? "bg-risk-low-soft text-risk-low" : "bg-surface-muted text-ink-subtle"}>
                              {statusLabel}
                            </Badge>
                          </td>
                          <td className="num px-2.5 py-2 text-right text-ink-muted">{k.rate_limit_per_min} lần/phút · {k.daily_limit.toLocaleString("vi-VN")} lần/ngày</td>
                          <td className="px-2.5 py-2 text-right">
                            {active ? (
                              <div className="inline-flex gap-1.5">
                                <Button type="button" size="xs" variant="outline" disabled={rotateKeyMut.isPending} onClick={() => rotateKeyMut.mutate(k.key_id)} aria-label={`Xoay khóa ${k.name}`}>
                                  <RefreshCw className="mr-1 size-3" aria-hidden="true" />Xoay
                                </Button>
                                <Button type="button" size="xs" variant="outline" disabled={revokeKeyMut.isPending} onClick={() => revokeKeyMut.mutate(k.key_id)} aria-label={`Thu hồi khóa ${k.name}`}>
                                  Thu hồi
                                </Button>
                              </div>
                            ) : <span className="text-[11px] text-ink-subtle">{k.revoked_at ? formatDateVi(k.revoked_at) : "—"}</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-5">
          <CardHeader className="py-3"><CardTitle className="text-sm">Biểu đồ lưu lượng theo ngày</CardTitle></CardHeader>
          <CardContent className="space-y-3 p-4 text-xs">
            {dailySummary.length === 0 ? (
              <p className="rounded-md border border-border bg-canvas px-3 py-4 text-ink-muted">Chưa ghi nhận lượt gọi API trong 14 ngày qua.</p>
            ) : (
              <div className="space-y-2" aria-label="Biểu đồ cột lưu lượng API theo ngày">
                {dailySummary.map((d) => {
                  const pct = Math.max(4, Math.round((d.requests / maxDayReq) * 100));
                  return (
                    <div key={d.date} className="space-y-1">
                      <div className="flex items-center justify-between text-[11px]">
                        <span className="font-mono text-ink-muted">{formatDateVi(d.date)}</span>
                        <span className="num text-ink">{d.requests.toLocaleString("vi-VN")} req ({d.cacheHits.toLocaleString("vi-VN")} cache)</span>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-sm bg-surface-muted">
                        <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-6">
          <CardHeader className="py-3"><CardTitle className="flex items-center gap-1.5 text-sm"><Webhook className="size-4" aria-hidden="true" />Webhook nhận AlertEvent (HMAC-SHA256)</CardTitle></CardHeader>
          <CardContent className="space-y-3 p-4 text-xs">
            {oneTimeWhSecret ? (
              <div role="status" className="rounded-md border border-border-strong bg-canvas px-3 py-2">
                <span className="font-medium text-ink">HMAC Secret (chỉ hiện 1 lần): </span>
                <code className="font-mono text-[11px] text-ink">{oneTimeWhSecret}</code>
              </div>
            ) : null}
            <form className="grid gap-2 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); if (whUrl.trim()) createWhMut.mutate(); }}>
              <Input aria-label="Nhãn Webhook" placeholder="Nhãn (VD: Discord Relay)" value={whLabel} onChange={(e) => setWhLabel(e.target.value)} />
              <Input aria-label="URL HTTPS Webhook" placeholder="https://example.com/webhooks/finintel" value={whUrl} onChange={(e) => setWhUrl(e.target.value)} />
              <Input aria-label="HMAC Secret tùy chọn" placeholder="HMAC secret (tùy chọn >= 16 ký tự)" value={whSecret} onChange={(e) => setWhSecret(e.target.value)} className="sm:col-span-2" />
              <div className="sm:col-span-2 flex justify-end"><Button type="submit" size="xs" disabled={!whUrl.trim() || createWhMut.isPending}>Đăng ký Webhook</Button></div>
            </form>
            {(webhooksQuery.data?.items ?? []).map((wh) => (
              <div key={wh.endpoint_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-canvas px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-ink">{wh.label || "Webhook Endpoint"}</div>
                  <div className="truncate font-mono text-[11px] text-ink-muted">{wh.url}</div>
                </div>
                <div className="flex items-center gap-1.5">
                  <Button type="button" size="xs" variant="outline" onClick={() => testWhMut.mutate(wh.endpoint_id)}>Kiểm tra</Button>
                  <Button type="button" size="xs" variant="outline" aria-label={`Xóa webhook ${wh.label || wh.url}`} onClick={() => deleteWhMut.mutate(wh.endpoint_id)}><Trash2 className="size-3" aria-hidden="true" /></Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card className="lg:col-span-6">
          <CardHeader className="flex flex-row items-center justify-between gap-2 py-3">
            <CardTitle className="text-sm">Mã mẫu Python SDK &amp; cURL</CardTitle>
            <SegmentedControl
              ariaLabel="Chọn ngôn ngữ mã mẫu"
              value={snippetLang}
              onChange={setSnippetLang}
              size="xs"
              options={[{ value: "python", label: "Python SDK" }, { value: "curl", label: "cURL" }]}
            />
          </CardHeader>
          <CardContent className="space-y-2 p-4 text-xs">
            <pre className="overflow-x-auto rounded-md border border-border bg-canvas p-3 font-mono text-[11px] leading-5 text-ink">{snippets[snippetLang]}</pre>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-ink-subtle">{COMPLIANCE_DISCLAIMER_VI}</span>
              <Button type="button" size="xs" variant="outline" onClick={() => void copyText(snippets[snippetLang], "snippet")}>
                {copiedSnippet ? "Đã chép mẫu" : "Sao chép mã"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
