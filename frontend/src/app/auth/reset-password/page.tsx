"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { KeyRound, Mail } from "lucide-react";
import { AuthFormShell } from "@/components/auth/AuthFormShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { ApiError } from "@/lib/api-client";
import { authApi } from "@/lib/auth-api";

type ResetMode = "request" | "confirm";

function ResetPasswordInner() {
  const searchParams = useSearchParams();
  const initialToken = searchParams.get("token") ?? "";
  const [mode, setMode] = React.useState<ResetMode>(initialToken ? "confirm" : "request");

  const [email, setEmail] = React.useState("");
  const [token, setToken] = React.useState(initialToken);
  const [newPassword, setNewPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");

  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({});
  const [statusMessage, setStatusMessage] = React.useState<string | null>(null);
  const [formError, setFormError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const alertRef = React.useRef<HTMLDivElement | null>(null);

  const handleRequestSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setFormError(null);
    setStatusMessage(null);
    const cleanEmail = email.trim();
    if (!cleanEmail || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(cleanEmail)) {
      setFieldErrors({ email: "Vui lòng nhập địa chỉ email hợp lệ." });
      return;
    }
    setFieldErrors({});
    setSubmitting(true);
    try {
      const res = await authApi.requestPasswordReset({ email: cleanEmail });
      setStatusMessage(res.message);
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : "Không thể gửi yêu cầu đặt lại mật khẩu.",
      );
      requestAnimationFrame(() => alertRef.current?.focus());
    } finally {
      setSubmitting(false);
    }
  };

  const handleConfirmSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setFormError(null);
    setStatusMessage(null);
    const nextErr: Record<string, string> = {};
    if (token.trim().length < 10) {
      nextErr.token = "Vui lòng nhập mã đặt lại mật khẩu hợp lệ.";
    }
    if (
      newPassword.length < 12 ||
      !/[a-zA-ZÀ-ỹ]/.test(newPassword) ||
      !/[^a-zA-ZÀ-ỹ\s]/.test(newPassword)
    ) {
      nextErr.newPassword =
        "Mật khẩu mới tối thiểu 12 ký tự và kết hợp chữ cái với số hoặc ký tự đặc biệt.";
    }
    if (confirmPassword !== newPassword) {
      nextErr.confirmPassword = "Mật khẩu xác nhận chưa trùng khớp.";
    }
    setFieldErrors(nextErr);
    if (Object.keys(nextErr).length > 0) return;

    setSubmitting(true);
    try {
      const res = await authApi.confirmPasswordReset({
        token: token.trim(),
        new_password: newPassword,
      });
      setStatusMessage(res.message);
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : "Mã xác thực không hợp lệ hoặc đã hết hạn.",
      );
      requestAnimationFrame(() => alertRef.current?.focus());
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthFormShell
      title="Đặt lại mật khẩu"
      subtitle="Mã đặt lại mật khẩu có hiệu lực trong 15 phút và tự động thu hồi toàn bộ phiên cũ sau khi hoàn tất."
      footerLinks={
        <Link href="/auth/login" className="text-accent-ink hover:underline">
          ← Quay lại đăng nhập
        </Link>
      }
    >
      <SegmentedControl
        ariaLabel="Bước đặt lại mật khẩu"
        variant="pill"
        value={mode}
        onChange={(next) => {
          setMode(next);
          setFieldErrors({});
          setFormError(null);
          setStatusMessage(null);
        }}
        options={[
          { value: "request", label: "Gửi yêu cầu" },
          { value: "confirm", label: "Nhập mã & Đổi mật khẩu" },
        ]}
      />

      {statusMessage ? (
        <div
          role="status"
          className="rounded-md border border-risk-low/30 bg-risk-low-soft px-3 py-2 text-xs text-risk-low"
        >
          {statusMessage}
        </div>
      ) : null}

      {formError ? (
        <div
          ref={alertRef}
          tabIndex={-1}
          role="alert"
          className="rounded-md border border-risk-high/30 bg-risk-high-soft px-3 py-2 text-xs text-risk-high outline-none"
        >
          {formError}
        </div>
      ) : null}

      {mode === "request" ? (
        <form onSubmit={handleRequestSubmit} noValidate className="space-y-3.5">
          <div className="space-y-1">
            <label htmlFor="reset-email" className="block text-xs font-medium text-ink">
              Email đã đăng ký
            </label>
            <Input
              id="reset-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-invalid={Boolean(fieldErrors.email)}
              aria-describedby={fieldErrors.email ? "reset-email-error" : undefined}
              placeholder="ten@congty.vn"
              className="h-9"
            />
            {fieldErrors.email ? (
              <p id="reset-email-error" role="alert" className="text-xs text-risk-high">
                {fieldErrors.email}
              </p>
            ) : null}
          </div>
          <Button type="submit" disabled={submitting} className="h-9 w-full">
            <Mail className="size-3.5" aria-hidden="true" />
            <span>{submitting ? "Đang gửi..." : "Gửi hướng dẫn đặt lại"}</span>
          </Button>
        </form>
      ) : (
        <form onSubmit={handleConfirmSubmit} noValidate className="space-y-3.5">
          <div className="space-y-1">
            <label htmlFor="reset-token" className="block text-xs font-medium text-ink">
              Mã xác thực đặt lại
            </label>
            <Input
              id="reset-token"
              type="text"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              aria-invalid={Boolean(fieldErrors.token)}
              aria-describedby={fieldErrors.token ? "reset-token-error" : undefined}
              placeholder="Dán mã từ email"
              className="h-9 font-mono text-xs"
            />
            {fieldErrors.token ? (
              <p id="reset-token-error" role="alert" className="text-xs text-risk-high">
                {fieldErrors.token}
              </p>
            ) : null}
          </div>

          <div className="space-y-1">
            <label htmlFor="reset-new-pw" className="block text-xs font-medium text-ink">
              Mật khẩu mới
            </label>
            <Input
              id="reset-new-pw"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              aria-invalid={Boolean(fieldErrors.newPassword)}
              aria-describedby={fieldErrors.newPassword ? "reset-new-pw-error" : undefined}
              placeholder="Tối thiểu 12 ký tự"
              className="h-9"
            />
            {fieldErrors.newPassword ? (
              <p id="reset-new-pw-error" role="alert" className="text-xs text-risk-high">
                {fieldErrors.newPassword}
              </p>
            ) : null}
          </div>

          <div className="space-y-1">
            <label htmlFor="reset-confirm-pw" className="block text-xs font-medium text-ink">
              Xác nhận mật khẩu mới
            </label>
            <Input
              id="reset-confirm-pw"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              aria-invalid={Boolean(fieldErrors.confirmPassword)}
              aria-describedby={
                fieldErrors.confirmPassword ? "reset-confirm-pw-error" : undefined
              }
              placeholder="Nhập lại mật khẩu mới"
              className="h-9"
            />
            {fieldErrors.confirmPassword ? (
              <p id="reset-confirm-pw-error" role="alert" className="text-xs text-risk-high">
                {fieldErrors.confirmPassword}
              </p>
            ) : null}
          </div>

          <Button type="submit" disabled={submitting} className="h-9 w-full">
            <KeyRound className="size-3.5" aria-hidden="true" />
            <span>{submitting ? "Đang cập nhật..." : "Xác nhận đặt lại mật khẩu"}</span>
          </Button>
        </form>
      )}
    </AuthFormShell>
  );
}

export default function ResetPasswordPage() {
  return (
    <React.Suspense
      fallback={
        <div className="mx-auto max-w-md py-8 text-xs text-ink-muted">
          Đang tải trang đặt lại mật khẩu...
        </div>
      }
    >
      <ResetPasswordInner />
    </React.Suspense>
  );
}
