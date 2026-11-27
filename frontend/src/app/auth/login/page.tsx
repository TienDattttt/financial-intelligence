"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Eye, EyeOff, LogIn } from "lucide-react";
import { AuthFormShell } from "@/components/auth/AuthFormShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import { useInvalidateAuthAndWorkspace } from "@/hooks/use-auth-workspace";
import { ApiError } from "@/lib/api-client";
import { authApi } from "@/lib/auth-api";
import { validateSafeNextPath } from "@/lib/watchlist-store";

function LoginFormInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const invalidateAll = useInvalidateAuthAndWorkspace();

  const nextPath = validateSafeNextPath(searchParams.get("next"));
  const justRegistered = searchParams.get("registered") === "1";

  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [emailError, setEmailError] = React.useState<string | null>(null);
  const [passwordError, setPasswordError] = React.useState<string | null>(null);
  const [formError, setFormError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const emailRef = React.useRef<HTMLInputElement | null>(null);
  const passwordRef = React.useRef<HTMLInputElement | null>(null);
  const formAlertRef = React.useRef<HTMLDivElement | null>(null);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setFormError(null);
    let hasError = false;
    const trimmedEmail = email.trim();

    if (!trimmedEmail || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(trimmedEmail)) {
      setEmailError("Vui lòng nhập địa chỉ email hợp lệ.");
      if (!hasError) emailRef.current?.focus();
      hasError = true;
    } else {
      setEmailError(null);
    }

    if (!password) {
      setPasswordError("Vui lòng nhập mật khẩu.");
      if (!hasError) passwordRef.current?.focus();
      hasError = true;
    } else {
      setPasswordError(null);
    }

    if (hasError) return;

    setSubmitting(true);
    try {
      await authApi.login({ email: trimmedEmail, password });
      await invalidateAll();
      router.push(nextPath);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : "Không thể đăng nhập lúc này. Vui lòng thử lại sau.";
      setFormError(msg);
      requestAnimationFrame(() => formAlertRef.current?.focus());
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthFormShell
      title="Đăng nhập FinIntel"
      subtitle="Phiên đăng nhập được bảo vệ bằng cookie HttpOnly bảo mật, không lưu mã phiên trên trình duyệt."
      footerLinks={
        <>
          <Link href="/auth/register" className="text-accent-ink hover:underline">
            Chưa có tài khoản? Đăng ký →
          </Link>
          <Link href="/auth/reset-password" className="text-ink-muted hover:text-ink hover:underline">
            Quên mật khẩu?
          </Link>
        </>
      }
    >
      {justRegistered ? (
        <div
          role="status"
          className="rounded-md border border-risk-low/30 bg-risk-low-soft px-3 py-2 text-xs text-risk-low"
        >
          Đăng ký tài khoản thành công. Vui lòng đăng nhập để mở không gian làm việc.
        </div>
      ) : null}

      {formError ? (
        <div
          ref={formAlertRef}
          tabIndex={-1}
          role="alert"
          className="rounded-md border border-risk-high/30 bg-risk-high-soft px-3 py-2 text-xs text-risk-high outline-none"
        >
          {formError}
        </div>
      ) : null}

      <form onSubmit={handleSubmit} noValidate className="space-y-3.5">
        <div className="space-y-1">
          <label htmlFor="login-email" className="block text-xs font-medium text-ink">
            Email
          </label>
          <Input
            ref={emailRef}
            id="login-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              if (emailError) setEmailError(null);
            }}
            aria-invalid={Boolean(emailError)}
            aria-describedby={emailError ? "login-email-error" : undefined}
            placeholder="ten@congty.vn"
            className="h-9"
          />
          {emailError ? (
            <p id="login-email-error" role="alert" className="text-xs text-risk-high">
              {emailError}
            </p>
          ) : null}
        </div>

        <div className="space-y-1">
          <label htmlFor="login-password" className="block text-xs font-medium text-ink">
            Mật khẩu
          </label>
          <div className="relative flex items-center">
            <Input
              ref={passwordRef}
              id="login-password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (passwordError) setPasswordError(null);
              }}
              aria-invalid={Boolean(passwordError)}
              aria-describedby={passwordError ? "login-password-error" : undefined}
              placeholder="Nhập mật khẩu"
              className="h-9 pr-10"
            />
            <button
              type="button"
              aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
              aria-pressed={showPassword}
              onClick={() => setShowPassword((prev) => !prev)}
              className="absolute right-1.5 inline-flex size-7 items-center justify-center rounded-sm text-ink-muted hover:bg-surface-hover hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {showPassword ? (
                <EyeOff className="size-3.5" aria-hidden="true" />
              ) : (
                <Eye className="size-3.5" aria-hidden="true" />
              )}
            </button>
          </div>
          {passwordError ? (
            <p id="login-password-error" role="alert" className="text-xs text-risk-high">
              {passwordError}
            </p>
          ) : null}
        </div>

        <Button type="submit" disabled={submitting} className="h-9 w-full">
          <LogIn className="size-3.5" aria-hidden="true" />
          <span>{submitting ? "Đang xác thực..." : "Đăng nhập"}</span>
        </Button>
      </form>
    </AuthFormShell>
  );
}

export default function LoginPage() {
  return (
    <React.Suspense
      fallback={
        <div className="mx-auto max-w-md py-8 text-xs text-ink-muted">
          Đang tải biểu mẫu đăng nhập...
        </div>
      }
    >
      <LoginFormInner />
    </React.Suspense>
  );
}
