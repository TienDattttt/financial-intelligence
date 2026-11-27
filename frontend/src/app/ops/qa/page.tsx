"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileWarning, RefreshCw, ShieldAlert } from "lucide-react";
import { PageSkeleton } from "@/components/layout/StateBlocks";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/primitives";
import { useAuthSession } from "@/hooks/use-auth-workspace";
import { opsApi } from "@/lib/auth-api";
import { formatDateVi } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { OpsQuarantineTicketItem } from "@/types/api";

type OpsTab = "qa-queue" | "lifecycle";

const STATUS_BADGE: Record<OpsQuarantineTicketItem["status"], string> = {
  OPEN: "border-red-500/40 bg-red-50/80 text-red-800 dark:bg-red-950/40 dark:text-red-200",
  IN_REVIEW:
    "border-amber-500/40 bg-amber-50/80 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200",
  RESOLVED:
    "border-emerald-500/40 bg-emerald-50/80 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200",
};

export default function OpsQaPage() {
  const queryClient = useQueryClient();
  const authSession = useAuthSession();
  const isOps =
    authSession.data?.authenticated === true &&
    authSession.data?.user?.role === "INTERNAL_OPS";

  const [tab, setTab] = React.useState<OpsTab>("qa-queue");
  const [statusFilter, setStatusFilter] = React.useState<string>("");
  const [selectedTicketId, setSelectedTicketId] = React.useState<string>("");
  const [assignNote, setAssignNote] = React.useState("");
  const [sourceUrl, setSourceUrl] = React.useState("");
  const [rawSha256, setRawSha256] = React.useState("");
  const [revReason, setRevReason] = React.useState("");
  const [decisionNote, setDecisionNote] = React.useState("");
  const [actionMsg, setActionMsg] = React.useState<string | null>(null);
  const [actionErr, setActionErr] = React.useState<string | null>(null);

  const [lcSearch, setLcSearch] = React.useState("");
  const [lcExchange, setLcExchange] = React.useState("");
  const [syncTicker, setSyncTicker] = React.useState("");
  const [syncExchange, setSyncExchange] = React.useState<
    "HOSE" | "HNX" | "UPCOM" | "DELISTED"
  >("HOSE");
  const [syncEventType, setSyncEventType] = React.useState<
    "IPO_LISTING" | "EXCHANGE_TRANSFER" | "DELISTING" | "FY_END_CHANGE" | "METADATA_SYNC"
  >("METADATA_SYNC");
  const [syncDate, setSyncDate] = React.useState("2026-04-01");
  const [syncFyMonth, setSyncFyMonth] = React.useState("12");
  const [syncSourceUrl, setSyncSourceUrl] = React.useState("");
  const [syncNotes, setSyncNotes] = React.useState("");

  const queueQuery = useQuery({
    queryKey: ["ops-qa-queue", statusFilter || "ALL"],
    queryFn: ({ signal }) =>
      opsApi.getQaQueue(
        (statusFilter || null) as "OPEN" | "IN_REVIEW" | "RESOLVED" | null,
        signal,
      ),
    enabled: isOps,
  });

  const lifecycleQuery = useQuery({
    queryKey: ["ops-issuer-lifecycle", lcExchange, lcSearch],
    queryFn: ({ signal }) =>
      opsApi.getIssuerLifecycle(
        {
          exchange: lcExchange || undefined,
          q: lcSearch.trim() || undefined,
          limit: 50,
          offset: 0,
        },
        signal,
      ),
    enabled: isOps && tab === "lifecycle",
  });

  const tickets = React.useMemo(
    () => queueQuery.data?.items ?? [],
    [queueQuery.data?.items],
  );

  const activeTicket =
    tickets.find((t) => t.ticket_id === selectedTicketId) ?? tickets[0] ?? null;

  const invalidateQueue = () =>
    queryClient.invalidateQueries({ queryKey: ["ops-qa-queue"] });

  const assignMutation = useMutation({
    mutationFn: (ticketId: string) =>
      opsApi.assignTicket(ticketId, { root_cause_note: assignNote }),
    onSuccess: (res) => {
      setActionErr(null);
      setActionMsg(`Đã nhận xử lý phiếu ${res.ticket.ticker}.`);
      void invalidateQueue();
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setActionErr(err.message);
    },
  });

  const revisionMutation = useMutation({
    mutationFn: (ticketId: string) =>
      opsApi.submitRevision(ticketId, {
        source_url: sourceUrl.trim() || null,
        raw_file_sha256: rawSha256.trim() || null,
        reason: revReason.trim(),
      }),
    onSuccess: (res) => {
      setActionErr(null);
      setActionMsg(
        `Đã nối phiên bản đính chính #${res.ticket.latest_revision_no} cho ${res.ticket.ticker} (bản gốc #1 giữ nguyên bất biến).`,
      );
      setSourceUrl("");
      setRawSha256("");
      setRevReason("");
      void invalidateQueue();
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setActionErr(err.message);
    },
  });

  const decideMutation = useMutation({
    mutationFn: ({
      ticketId,
      decision,
    }: {
      ticketId: string;
      decision: "APPROVE_REINGEST" | "KEEP_QUARANTINED";
    }) => opsApi.decideTicket(ticketId, { decision, note: decisionNote }),
    onSuccess: (res) => {
      setActionErr(null);
      setActionMsg(`Đã chốt quyết định cho ${res.ticket.ticker}.`);
      setDecisionNote("");
      void invalidateQueue();
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setActionErr(err.message);
    },
  });

  const syncMutation = useMutation({
    mutationFn: () =>
      opsApi.requestIssuerSync({
        ticker: syncTicker.trim().toUpperCase(),
        exchange: syncExchange,
        event_type: syncEventType,
        effective_date: syncDate,
        fy_end_month: Number(syncFyMonth) || 12,
        source_url: syncSourceUrl.trim(),
        notes: syncNotes.trim(),
      }),
    onSuccess: (res) => {
      setActionErr(null);
      setActionMsg(
        `Đã ghi nhận sự kiện vòng đời cho ${res.event.ticker} (${res.event.exchange}).`,
      );
      setSyncTicker("");
      setSyncSourceUrl("");
      setSyncNotes("");
      void queryClient.invalidateQueries({ queryKey: ["ops-issuer-lifecycle"] });
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setActionErr(err.message);
    },
  });

  if (authSession.isLoading) {
    return <PageSkeleton />;
  }

  if (!isOps) {
    return (
      <div className="mx-auto max-w-xl py-10">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base text-risk-high">
              <ShieldAlert className="size-4" aria-hidden="true" />
              Khu vực kiểm duyệt dữ liệu nội bộ (403 — Chỉ dành cho INTERNAL_OPS)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-xs text-ink-muted">
            <p>
              Tài khoản hiện tại không có vai trò vận hành dữ liệu nội bộ để truy cập hàng đợi cách ly chất lượng và cập nhật vòng đời niêm yết.
            </p>
            <Link
              href="/"
              className="inline-flex rounded border border-border px-3 py-1.5 font-medium text-ink hover:bg-surface-hover"
            >
              Quay lại Tổng quan
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink">
            Kiểm duyệt chất lượng dữ liệu &amp; Vòng đời niêm yết
          </h1>
          <p className="text-xs text-ink-muted">
            Hàng đợi cách ly BCTC và nhật ký sự kiện doanh nghiệp — Mọi thao tác đính chính đều ghi nối phiên bản bất biến và lưu vết kiểm toán.
          </p>
        </div>
        <div className="inline-flex rounded-lg border border-border bg-surface p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setTab("qa-queue")}
            className={cn(
              "rounded-md px-3 py-1.5 font-medium",
              tab === "qa-queue" ? "bg-primary text-primary-foreground" : "text-ink-muted",
            )}
          >
            Hàng đợi QA ({queueQuery.data?.total ?? 0})
          </button>
          <button
            type="button"
            onClick={() => setTab("lifecycle")}
            className={cn(
              "rounded-md px-3 py-1.5 font-medium",
              tab === "lifecycle" ? "bg-primary text-primary-foreground" : "text-ink-muted",
            )}
          >
            Vòng đời niêm yết
          </button>
        </div>
      </div>

      {actionMsg ? (
        <div className="flex items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-50/70 px-3 py-2 text-xs text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">
          <CheckCircle2 className="size-3.5 shrink-0" aria-hidden="true" />
          <span>{actionMsg}</span>
        </div>
      ) : null}
      {actionErr ? (
        <div role="alert" className="rounded-md border border-red-500/40 bg-red-50/70 px-3 py-2 text-xs text-red-900 dark:bg-red-950/30 dark:text-red-200">
          {actionErr}
        </div>
      ) : null}

      {tab === "qa-queue" ? (
        <div className="grid gap-4 lg:grid-cols-12">
          <Card className="lg:col-span-5">
            <CardHeader className="flex flex-row items-center justify-between gap-2 py-2.5">
              <CardTitle className="flex items-center gap-1.5 text-sm">
                <FileWarning className="size-4 text-amber-700" aria-hidden="true" />
                Phiếu cách ly chất lượng
              </CardTitle>
              <Select
                aria-label="Lọc trạng thái phiếu QA"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="h-7 text-xs"
              >
                <option value="">Tất cả trạng thái</option>
                <option value="OPEN">OPEN</option>
                <option value="IN_REVIEW">IN_REVIEW</option>
                <option value="RESOLVED">RESOLVED</option>
              </Select>
            </CardHeader>
            <CardContent className="space-y-2 p-3">
              {tickets.length === 0 ? (
                <p className="text-xs text-ink-muted">Không có phiếu cách ly nào khớp bộ lọc.</p>
              ) : (
                tickets.map((t) => (
                  <button
                    key={t.ticket_id}
                    type="button"
                    onClick={() => setSelectedTicketId(t.ticket_id)}
                    className={cn(
                      "w-full rounded-md border p-2.5 text-left text-xs transition-colors",
                      activeTicket?.ticket_id === t.ticket_id
                        ? "border-blue-700 bg-blue-50/60 dark:bg-blue-950/25"
                        : "border-border hover:bg-surface-hover/50",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono font-semibold text-ink">
                        {t.ticker} · {t.rule_code}
                      </span>
                      <span className={cn("rounded border px-1.5 py-0.5 text-[10px] font-medium", STATUS_BADGE[t.status])}>
                        {t.status}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center justify-between text-[11px] text-ink-muted">
                      <span>Kỳ {formatDateVi(t.period_end_date)}</span>
                      <span className="num">Phiên bản #{t.latest_revision_no}</span>
                    </div>
                  </button>
                ))
              )}
            </CardContent>
          </Card>

          <Card className="lg:col-span-7">
            <CardHeader className="py-2.5">
              <CardTitle className="text-sm">
                {activeTicket
                  ? `Chi tiết xử lý: ${activeTicket.ticker} (${activeTicket.filing_id})`
                  : "Chọn một phiếu để xem chi tiết"}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 p-3.5 text-xs">
              {activeTicket ? (
                <>
                  <div className="space-y-1.5 rounded-md border border-border bg-canvas p-2.5">
                    <div className="font-semibold text-ink">Lịch sử phiên bản bất biến</div>
                    {(activeTicket.revisions ?? []).map((rev) => (
                      <div key={rev.submission_id} className="border-t border-border/60 pt-1.5 text-[11px]">
                        <div className="flex items-center justify-between font-medium text-ink">
                          <span>Phiên bản #{rev.revision_no} · {rev.reingest_status}</span>
                          <span className="text-ink-muted">{formatDateVi(rev.submitted_at)}</span>
                        </div>
                        <p className="text-ink-muted">{rev.reason}</p>
                        {rev.source_url ? <p className="truncate text-blue-700">{rev.source_url}</p> : null}
                        {rev.raw_file_sha256 ? (
                          <p className="font-mono text-[10px] text-ink-subtle">SHA-256: {rev.raw_file_sha256}</p>
                        ) : null}
                      </div>
                    ))}
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      aria-label="Ghi chú nguyên nhân gốc"
                      placeholder="Ghi chú nguyên nhân gốc..."
                      value={assignNote}
                      onChange={(e) => setAssignNote(e.target.value)}
                      className="h-8 flex-1 text-xs"
                    />
                    <Button
                      type="button"
                      size="xs"
                      variant="outline"
                      disabled={assignMutation.isPending}
                      onClick={() => assignMutation.mutate(activeTicket.ticket_id)}
                    >
                      Nhận xử lý
                    </Button>
                  </div>

                  <div className="space-y-2 rounded-md border border-border p-3">
                    <div className="font-semibold text-ink">
                      Nạp phiên bản đính chính #{activeTicket.latest_revision_no + 1} (bắt buộc kèm nguồn gốc)
                    </div>
                    <Input
                      aria-label="URL công bố gốc"
                      placeholder="https://congbothongtin.ssc.gov.vn/..."
                      value={sourceUrl}
                      onChange={(e) => setSourceUrl(e.target.value)}
                      className="h-8 text-xs"
                    />
                    <Input
                      aria-label="Mã băm SHA-256 tệp gốc"
                      placeholder="SHA-256 (64 ký tự hex, tùy chọn nếu đã có URL)"
                      value={rawSha256}
                      onChange={(e) => setRawSha256(e.target.value)}
                      className="h-8 font-mono text-xs"
                    />
                    <Input
                      aria-label="Lý do đính chính"
                      placeholder="Lý do đính chính (tối thiểu 5 ký tự)..."
                      value={revReason}
                      onChange={(e) => setRevReason(e.target.value)}
                      className="h-8 text-xs"
                    />
                    <Button
                      type="button"
                      size="xs"
                      disabled={revisionMutation.isPending}
                      onClick={() => revisionMutation.mutate(activeTicket.ticket_id)}
                    >
                      Gửi phiên bản đính chính
                    </Button>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      aria-label="Ghi chú quyết định"
                      placeholder="Ghi chú phê duyệt / giữ cách ly..."
                      value={decisionNote}
                      onChange={(e) => setDecisionNote(e.target.value)}
                      className="h-8 flex-1 text-xs"
                    />
                    <Button
                      type="button"
                      size="xs"
                      disabled={decideMutation.isPending}
                      onClick={() =>
                        decideMutation.mutate({
                          ticketId: activeTicket.ticket_id,
                          decision: "APPROVE_REINGEST",
                        })
                      }
                    >
                      Duyệt nạp lại
                    </Button>
                    <Button
                      type="button"
                      size="xs"
                      variant="outline"
                      disabled={decideMutation.isPending}
                      onClick={() =>
                        decideMutation.mutate({
                          ticketId: activeTicket.ticket_id,
                          decision: "KEEP_QUARANTINED",
                        })
                      }
                    >
                      Giữ cách ly
                    </Button>
                  </div>
                </>
              ) : null}
            </CardContent>
          </Card>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-12">
          <Card className="lg:col-span-7">
            <CardHeader className="flex flex-wrap flex-row items-center justify-between gap-2 py-2.5">
              <CardTitle className="text-sm">Danh sách doanh nghiệp &amp; niên độ</CardTitle>
              <div className="flex items-center gap-2">
                <Input
                  aria-label="Tìm mã doanh nghiệp"
                  placeholder="Tìm mã..."
                  value={lcSearch}
                  onChange={(e) => setLcSearch(e.target.value)}
                  className="h-7 w-28 text-xs"
                />
                <Select
                  aria-label="Lọc sàn niêm yết"
                  value={lcExchange}
                  onChange={(e) => setLcExchange(e.target.value)}
                  className="h-7 text-xs"
                >
                  <option value="">Tất cả sàn</option>
                  <option value="HOSE">HOSE</option>
                  <option value="HNX">HNX</option>
                  <option value="UPCOM">UPCOM</option>
                  <option value="DELISTED">DELISTED</option>
                </Select>
              </div>
            </CardHeader>
            <CardContent className="overflow-x-auto p-0">
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                    <th className="px-3 py-2 text-left">Mã</th>
                    <th className="px-3 py-2 text-left">Sàn</th>
                    <th className="px-3 py-2 text-right">Tháng kết thúc NĐ</th>
                    <th className="px-3 py-2 text-right">Sự kiện gần đây</th>
                  </tr>
                </thead>
                <tbody>
                  {(lifecycleQuery.data?.items ?? []).map((item) => (
                    <tr key={item.ticker} className="border-b border-border/60">
                      <td className="px-3 py-2 font-mono font-semibold text-ink">{item.ticker}</td>
                      <td className="px-3 py-2 text-ink-muted">{item.exchange}</td>
                      <td className="num px-3 py-2 text-right">Tháng {item.fy_end_month}</td>
                      <td className="num px-3 py-2 text-right">
                        {(item.recent_events ?? []).length}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>

          <Card className="lg:col-span-5">
            <CardHeader className="py-2.5">
              <CardTitle className="flex items-center gap-1.5 text-sm">
                <RefreshCw className="size-3.5 text-blue-700" aria-hidden="true" />
                Ghi nhận sự kiện vòng đời / đồng bộ
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5 p-3.5 text-xs">
              <Input
                aria-label="Mã chứng khoán vòng đời"
                placeholder="Mã CK (VD: HPG)"
                value={syncTicker}
                onChange={(e) => setSyncTicker(e.target.value)}
                className="h-8 text-xs"
              />
              <div className="grid grid-cols-2 gap-2">
                <Select
                  aria-label="Sàn giao dịch mới"
                  value={syncExchange}
                  onChange={(e) =>
                    setSyncExchange(e.target.value as "HOSE" | "HNX" | "UPCOM" | "DELISTED")
                  }
                  className="h-8 text-xs"
                >
                  <option value="HOSE">HOSE</option>
                  <option value="HNX">HNX</option>
                  <option value="UPCOM">UPCOM</option>
                  <option value="DELISTED">DELISTED</option>
                </Select>
                <Select
                  aria-label="Loại sự kiện vòng đời"
                  value={syncEventType}
                  onChange={(e) =>
                    setSyncEventType(
                      e.target.value as
                        | "IPO_LISTING"
                        | "EXCHANGE_TRANSFER"
                        | "DELISTING"
                        | "FY_END_CHANGE"
                        | "METADATA_SYNC",
                    )
                  }
                  className="h-8 text-xs"
                >
                  <option value="METADATA_SYNC">Đồng bộ dữ liệu</option>
                  <option value="IPO_LISTING">Niêm yết mới (IPO)</option>
                  <option value="EXCHANGE_TRANSFER">Chuyển sàn</option>
                  <option value="DELISTING">Hủy niêm yết</option>
                  <option value="FY_END_CHANGE">Đổi tháng kết thúc niên độ</option>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Input
                  type="date"
                  aria-label="Ngày hiệu lực"
                  value={syncDate}
                  onChange={(e) => setSyncDate(e.target.value)}
                  className="h-8 text-xs"
                />
                <Input
                  type="number"
                  min={1}
                  max={12}
                  aria-label="Tháng kết thúc niên độ"
                  value={syncFyMonth}
                  onChange={(e) => setSyncFyMonth(e.target.value)}
                  className="h-8 text-xs"
                />
              </div>
              <Input
                aria-label="URL công bố sự kiện"
                placeholder="URL công bố Sở (hsx.vn / hnx.vn)..."
                value={syncSourceUrl}
                onChange={(e) => setSyncSourceUrl(e.target.value)}
                className="h-8 text-xs"
              />
              <Input
                aria-label="Ghi chú sự kiện"
                placeholder="Ghi chú chi tiết..."
                value={syncNotes}
                onChange={(e) => setSyncNotes(e.target.value)}
                className="h-8 text-xs"
              />
              <Button
                type="button"
                size="xs"
                disabled={!syncTicker.trim() || syncMutation.isPending}
                onClick={() => syncMutation.mutate()}
              >
                Gửi yêu cầu đồng bộ
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
