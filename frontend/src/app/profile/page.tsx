"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Dialog } from "@base-ui/react/dialog";
import { LogOut, ShieldAlert, Trash2, UserCheck } from "lucide-react";
import { EmptyState, TableSkeleton } from "@/components/layout/StateBlocks";
import { QuotaBadge } from "@/components/quota/QuotaBadge";
import { Badge, Input } from "@/components/ui/primitives";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useAuthSession,
  useAuthSessionsList,
  useInvalidateAuthAndWorkspace,
  useMyAuditActivity,
  useMyQuota,
} from "@/hooks/use-auth-workspace";
import { ApiError } from "@/lib/api-client";
import { authApi } from "@/lib/auth-api";
import { PDPL_CONSENT_SUMMARY_VI } from "@/lib/compliance";
import { formatDateVi } from "@/lib/format";
import { formatUserRoleLabel } from "@/lib/watchlist-store";
import type { ConsentPurpose } from "@/types/api";

const CONSENT_LABELS: Record<ConsentPurpose, { title: string; detail: string }> = {
  ACCOUNT_CORE: {
    title: "Dữ liệu tài khoản cốt lõi",
    detail: "Lưu email và phiên đăng nhập bảo mật (bắt buộc để duy trì tài khoản).",
  },
  ALERT_NOTIFICATIONS: {
    title: "Thông báo cảnh báo rủi ro",
    detail: "Gửi tóm tắt biến động rủi ro cho các mã trong danh mục theo dõi qua email/Telegram.",
  },
  CROSS_BORDER_LLM_RAG: {
    title: "Đối chiếu thuyết minh BCTC (Điều 20 Luật 91/2025)",
    detail: "Gửi câu hỏi tài chính đã loại bỏ thông tin định danh cá nhân tới mô hình phân tích.",
  },
};

export default function ProfilePage() {
  const router = useRouter();
  const invalidateAll = useInvalidateAuthAndWorkspace();
  const sessionQuery = useAuthSession();
  const isAuthenticated = sessionQuery.data?.authenticated === true;
  const user = sessionQuery.data?.user ?? null;

  const sessionsQuery = useAuthSessionsList(isAuthenticated);
  const activityQuery = useMyAuditActivity(isAuthenticated);
  const quotaQuery = useMyQuota(isAuthenticated);

  const [displayNameDraft, setDisplayNameDraft] = React.useState<string | null>(null);
  const displayName = displayNameDraft ?? user?.display_name ?? "";
  const [statusMsg, setStatusMsg] = React.useState<string | null>(null);
  const [errorMsg, setErrorMsg] = React.useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [pwError, setPwError] = React.useState<string | null>(null);
  const [pwSuccess, setPwSuccess] = React.useState<string | null>(null);

  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);
  const [deletePassword, setDeletePassword] = React.useState("");
  const [deleteReason, setDeleteReason] = React.useState("");
  const [deleteError, setDeleteError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  if (sessionQuery.isLoading) {
    return <TableSkeleton rows={6} />;
  }

  if (!isAuthenticated || !user) {
    return (
      <EmptyState
        title="Bạn chưa đăng nhập"
        description="Đăng nhập để quản lý hồ sơ cá nhân, quyền đồng ý dữ liệu theo Luật 91/2025/QH15 và các phiên làm việc."
        action={
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/auth/login?next=/profile"
              className={buttonVariants({ variant: "default", size: "sm" })}
            >
              Đăng nhập
            </Link>
            <Link
              href="/auth/register"
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              Đăng ký tài khoản
            </Link>
          </div>
        }
      />
    );
  }

  const handleSaveName = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErrorMsg(null);
    setStatusMsg(null);
    setBusy(true);
    try {
      await authApi.updateProfile({ display_name: displayName.trim() });
      await invalidateAll();
      setStatusMsg("Đã cập nhật tên hiển thị.");
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : "Không thể cập nhật hồ sơ.");
    } finally {
      setBusy(false);
    }
  };

  const handleToggleConsent = async (purpose: ConsentPurpose, nextGranted: boolean) => {
    if (purpose === "ACCOUNT_CORE") return;
    setErrorMsg(null);
    setStatusMsg(null);
    setBusy(true);
    try {
      await authApi.updateProfile({ consents: { [purpose]: nextGranted } });
      await invalidateAll();
      setStatusMsg("Đã cập nhật lựa chọn đồng ý xử lý dữ liệu.");
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : "Không thể cập nhật đồng ý.");
    } finally {
      setBusy(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setPwError(null);
    setPwSuccess(null);
    if (!currentPassword || newPassword.length < 12) {
      setPwError("Vui lòng nhập mật khẩu hiện tại và mật khẩu mới tối thiểu 12 ký tự.");
      return;
    }
    setBusy(true);
    try {
      const res = await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setPwSuccess(res.message);
      setCurrentPassword("");
      setNewPassword("");
    } catch (err) {
      setPwError(err instanceof ApiError ? err.message : "Đổi mật khẩu thất bại.");
    } finally {
      setBusy(false);
    }
  };

  const handleRevokeSession = async (sessionId: string) => {
    setBusy(true);
    try {
      await authApi.revokeSession(sessionId);
      await invalidateAll();
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : "Không thể thu hồi phiên.");
    } finally {
      setBusy(false);
    }
  };

  const handleLogoutAll = async () => {
    setBusy(true);
    try {
      await authApi.logoutAll();
      await invalidateAll();
      router.push("/auth/login");
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : "Không thể đăng xuất tất cả.");
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteAccount = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setDeleteError(null);
    if (!deletePassword) {
      setDeleteError("Vui lòng nhập mật khẩu hiện tại để xác nhận xóa tài khoản.");
      return;
    }
    setBusy(true);
    try {
      await authApi.deleteAccount({
        current_password: deletePassword,
        reason: deleteReason.trim() || null,
      });
      setDeleteDialogOpen(false);
      await invalidateAll();
      router.push("/");
    } catch (err) {
      setDeleteError(
        err instanceof ApiError ? err.message : "Không thể xóa tài khoản lúc này.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Hồ sơ cá nhân &amp; Quyền dữ liệu
          </h1>
          <p className="mt-0.5 text-xs text-ink-muted">
            Quản lý tài khoản, lựa chọn đồng ý theo Luật 91/2025/QH15 và phiên đăng nhập.
          </p>
          <QuotaBadge
            quota={quotaQuery.data}
            isLoading={quotaQuery.isLoading}
            className="mt-2"
          />
        </div>
        <div className="flex items-center gap-2">
          <Badge className="border border-border bg-surface-muted text-ink">
            <UserCheck className="size-3.5 text-accent-ink" aria-hidden="true" />
            <span>{formatUserRoleLabel(user.role)}</span>
          </Badge>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={async () => {
              await authApi.logout();
              await invalidateAll();
              router.push("/");
            }}
          >
            <LogOut className="size-3.5" aria-hidden="true" />
            <span>Đăng xuất</span>
          </Button>
        </div>
      </div>

      {statusMsg ? (
        <div
          role="status"
          className="rounded-md border border-risk-low/30 bg-risk-low-soft px-3 py-2 text-xs text-risk-low"
        >
          {statusMsg}
        </div>
      ) : null}
      {errorMsg ? (
        <div
          role="alert"
          className="rounded-md border border-risk-high/30 bg-risk-high-soft px-3 py-2 text-xs text-risk-high"
        >
          {errorMsg}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Thông tin tài khoản</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSaveName} className="space-y-3">
              <div className="space-y-1">
                <label htmlFor="profile-email" className="block text-xs font-medium text-ink">
                  Email đăng ký
                </label>
                <Input id="profile-email" value={user.email} disabled readOnly />
              </div>
              <div className="space-y-1">
                <label htmlFor="profile-name" className="block text-xs font-medium text-ink">
                  Tên hiển thị
                </label>
                <Input
                  id="profile-name"
                  value={displayName}
                  maxLength={80}
                  onChange={(e) => setDisplayNameDraft(e.target.value)}
                  placeholder="Nhập tên hiển thị"
                />
              </div>
              <Button type="submit" size="sm" disabled={busy}>
                Lưu thông tin
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Đổi mật khẩu</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleChangePassword} className="space-y-3">
              {pwSuccess ? (
                <p role="status" className="text-xs text-risk-low">
                  {pwSuccess}
                </p>
              ) : null}
              {pwError ? (
                <p id="pw-change-err" role="alert" className="text-xs text-risk-high">
                  {pwError}
                </p>
              ) : null}
              <div className="space-y-1">
                <label htmlFor="pw-curr" className="block text-xs font-medium text-ink">
                  Mật khẩu hiện tại
                </label>
                <Input
                  id="pw-curr"
                  type="password"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  aria-describedby={pwError ? "pw-change-err" : undefined}
                />
              </div>
              <div className="space-y-1">
                <label htmlFor="pw-new" className="block text-xs font-medium text-ink">
                  Mật khẩu mới (tối thiểu 12 ký tự)
                </label>
                <Input
                  id="pw-new"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  aria-describedby={pwError ? "pw-change-err" : undefined}
                />
              </div>
              <Button type="submit" size="sm" disabled={busy}>
                Cập nhật mật khẩu
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Quyền đồng ý dữ liệu cá nhân (Luật 91/2025/QH15)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-ink-muted">{PDPL_CONSENT_SUMMARY_VI}</p>
          <div className="divide-y divide-border rounded-md border border-border">
            {user.consents.map((item) => {
              const meta = CONSENT_LABELS[item.purpose];
              const isCore = item.purpose === "ACCOUNT_CORE";
              const inputId = `consent-toggle-${item.purpose}`;
              return (
                <div
                  key={item.purpose}
                  className="flex flex-wrap items-center justify-between gap-3 px-3.5 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <label htmlFor={inputId} className="block text-xs font-semibold text-ink">
                      {meta.title}
                    </label>
                    <p className="text-[11px] text-ink-muted">{meta.detail}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] text-ink-subtle">
                      v{item.policy_version}
                    </span>
                    <input
                      id={inputId}
                      type="checkbox"
                      checked={item.active}
                      disabled={isCore || busy}
                      onChange={(e) => handleToggleConsent(item.purpose, e.target.checked)}
                      aria-label={meta.title}
                      className="size-4 rounded border-border-strong accent-primary disabled:opacity-60"
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Phiên đăng nhập đang hoạt động</CardTitle>
            <Button
              type="button"
              variant="outline"
              size="xs"
              disabled={busy}
              onClick={handleLogoutAll}
            >
              Thu hồi tất cả
            </Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {(sessionsQuery.data?.sessions ?? []).map((sess) => (
              <div
                key={sess.session_id}
                className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-xs"
              >
                <div>
                  <div className="font-medium text-ink">
                    {sess.is_current ? "Phiên hiện tại" : "Phiên khác"}
                  </div>
                  <div className="text-[11px] text-ink-muted">
                    Tạo: {formatDateVi(sess.created_at.slice(0, 10))} · Hết hạn:{" "}
                    {formatDateVi(sess.expires_at.slice(0, 10))}
                  </div>
                </div>
                {!sess.is_current ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    disabled={busy}
                    onClick={() => handleRevokeSession(sess.session_id)}
                  >
                    Thu hồi
                  </Button>
                ) : (
                  <Badge className="bg-risk-low-soft text-risk-low">Đang dùng</Badge>
                )}
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Nhật ký hoạt động bảo mật gần đây</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {(activityQuery.data?.items ?? []).slice(0, 8).map((ev) => (
              <div
                key={ev.audit_id}
                className="flex items-center justify-between gap-2 border-b border-border/60 py-1.5 text-xs last:border-b-0"
              >
                <span className="font-medium text-ink">{ev.action}</span>
                <span className="num text-[11px] text-ink-muted">
                  {ev.outcome} · {formatDateVi(ev.created_at.slice(0, 10))}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card className="border-risk-high/35">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-risk-high">
            <ShieldAlert className="size-4" aria-hidden="true" />
            <span>Xóa tài khoản &amp; Dữ liệu cá nhân (Luật 91/2025/QH15)</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-2xl text-xs text-ink-muted">
            Xóa vĩnh viễn email, danh mục theo dõi, ghi chú phân tích, bộ lọc đã lưu và ẩn danh
            hóa toàn bộ nhật ký bảo mật liên quan. Thao tác này không thể hoàn tác.
          </p>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={() => {
              setDeleteError(null);
              setDeletePassword("");
              setDeleteDialogOpen(true);
            }}
          >
            <Trash2 className="size-3.5" aria-hidden="true" />
            <span>Xóa tài khoản</span>
          </Button>
        </CardContent>
      </Card>

      <Dialog.Root open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-ink/40 dark:bg-black/60" />
          <Dialog.Viewport className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <Dialog.Popup
              aria-modal="true"
              className="w-full max-w-md rounded-lg border border-border-strong bg-surface p-5 text-ink shadow-popover outline-none"
            >
              <Dialog.Title className="text-base font-semibold text-risk-high">
                Xác nhận xóa tài khoản vĩnh viễn
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-xs text-ink-muted">
                Vui lòng nhập lại mật khẩu hiện tại để xác thực quyền xóa dữ liệu cá nhân.
              </Dialog.Description>
              <form onSubmit={handleDeleteAccount} className="mt-4 space-y-3">
                {deleteError ? (
                  <p id="del-acc-err" role="alert" className="text-xs text-risk-high">
                    {deleteError}
                  </p>
                ) : null}
                <div className="space-y-1">
                  <label htmlFor="del-pw" className="block text-xs font-medium text-ink">
                    Mật khẩu hiện tại *
                  </label>
                  <Input
                    id="del-pw"
                    type="password"
                    autoComplete="current-password"
                    value={deletePassword}
                    onChange={(e) => setDeletePassword(e.target.value)}
                    aria-invalid={Boolean(deleteError)}
                    aria-describedby={deleteError ? "del-acc-err" : undefined}
                  />
                </div>
                <div className="space-y-1">
                  <label htmlFor="del-reason" className="block text-xs font-medium text-ink">
                    Lý do (tùy chọn)
                  </label>
                  <Input
                    id="del-reason"
                    type="text"
                    maxLength={300}
                    value={deleteReason}
                    onChange={(e) => setDeleteReason(e.target.value)}
                  />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <Dialog.Close
                    type="button"
                    className={buttonVariants({ variant: "outline", size: "sm" })}
                  >
                    Hủy
                  </Dialog.Close>
                  <Button type="submit" variant="destructive" size="sm" disabled={busy}>
                    {busy ? "Đang xóa..." : "Xác nhận xóa vĩnh viễn"}
                  </Button>
                </div>
              </form>
            </Dialog.Popup>
          </Dialog.Viewport>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
