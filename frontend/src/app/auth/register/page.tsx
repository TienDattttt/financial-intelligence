"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { UserPlus } from "lucide-react";
import { AuthFormShell } from "@/components/auth/AuthFormShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import { ApiError } from "@/lib/api-client";
import { authApi } from "@/lib/auth-api";
import { PDPL_CONSENT_SUMMARY_VI } from "@/lib/compliance";

const CONSENT_POLICY_VERSION = "2026.01";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [acceptCore, setAcceptCore] = React.useState(false);
  const [acceptAlerts, setAcceptAlerts] = React.useState(false);
  const [acceptRag, setAcceptRag] = React.useState(false);

  const [errors, setErrors] = React.useState<Record<string, string>>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const emailRef = React.useRef<HTMLInputElement | null>(null);
  const passwordRef = React.useRef<HTMLInputElement | null>(null);
  const confirmRef = React.useRef<HTMLInputElement | null>(null);
  const consentRef = React.useRef<HTMLInputElement | null>(null);
  const alertRef = React.useRef<HTMLDivElement | null>(null);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setFormError(null);
    const nextErrors: Record<string, string> = {};
    const cleanEmail = email.trim();

    if (!cleanEmail || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(cleanEmail)) {
      nextErrors.email = "Vui lòng nhập địa chỉ email hợp lệ.";
    }
    if (
      password.length < 12 ||
      !/[a-zA-ZÀ-ỹ]/.test(password) ||
      !/[^a-zA-ZÀ-ỹ\s]/.test(password)
    ) {
      nextErrors.password =
        "Mật khẩu tối thiểu 12 ký tự và phải kết hợp chữ cái với chữ số hoặc ký tự đặc biệt.";
    }
    if (confirmPassword !== password) {
      nextErrors.confirmPassword = "Mật khẩu xác nhận chưa trùng khớp.";
    }
    if (!acceptCore) {
      nextErrors.acceptCore =
        "Bạn cần đồng ý xử lý dữ liệu tài khoản cốt lõi theo Luật 91/2025/QH15.";
    }

    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      if (nextErrors.email) emailRef.current?.focus();
      else if (nextErrors.password) passwordRef.current?.focus();
      else if (nextErrors.confirmPassword) confirmRef.current?.focus();
      else if (nextErrors.acceptCore) consentRef.current?.focus();
      return;
    }

    setSubmitting(true);
    try {
      await authApi.register({
        email: cleanEmail,
        password,
        display_name: displayName.trim(),
        accepted_consent_version: CONSENT_POLICY_VERSION,
        accept_account_core: acceptCore,
        accept_alert_notifications: acceptAlerts,
        accept_cross_border_llm_rag: acceptRag,
      });
      router.push("/auth/login?registered=1");
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : "Không thể đăng ký tài khoản lúc này. Vui lòng thử lại sau.";
      setFormError(msg);
      requestAnimationFrame(() => alertRef.current?.focus());
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthFormShell
      title="Đăng ký tài khoản FinIntel"
      subtitle="Miễn phí cho người dùng và nghiên cứu định lượng phi thương mại. Tuân thủ Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15."
      footerLinks={
        <Link href="/auth/login" className="text-accent-ink hover:underline">
          Đã có tài khoản? Đăng nhập →
        </Link>
      }
    >
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

      <form onSubmit={handleSubmit} noValidate className="space-y-3.5">
        <div className="space-y-1">
          <label htmlFor="reg-email" className="block text-xs font-medium text-ink">
            Email *
          </label>
          <Input
            ref={emailRef}
            id="reg-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={Boolean(errors.email)}
            aria-describedby={errors.email ? "reg-email-error" : undefined}
            placeholder="ten@congty.vn"
            className="h-9"
          />
          {errors.email ? (
            <p id="reg-email-error" role="alert" className="text-xs text-risk-high">
              {errors.email}
            </p>
          ) : null}
        </div>

        <div className="space-y-1">
          <label htmlFor="reg-name" className="block text-xs font-medium text-ink">
            Tên hiển thị (tùy chọn)
          </label>
          <Input
            id="reg-name"
            type="text"
            autoComplete="name"
            maxLength={80}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="VD: Nguyễn Văn A"
            className="h-9"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="reg-password" className="block text-xs font-medium text-ink">
            Mật khẩu *
          </label>
          <Input
            ref={passwordRef}
            id="reg-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={Boolean(errors.password)}
            aria-describedby={
              errors.password ? "reg-password-hint reg-password-error" : "reg-password-hint"
            }
            placeholder="Tối thiểu 12 ký tự"
            className="h-9"
          />
          <p id="reg-password-hint" className="text-[11px] text-ink-subtle">
            Tối thiểu 12 ký tự, kết hợp chữ cái với số hoặc ký tự đặc biệt.
          </p>
          {errors.password ? (
            <p id="reg-password-error" role="alert" className="text-xs text-risk-high">
              {errors.password}
            </p>
          ) : null}
        </div>

        <div className="space-y-1">
          <label htmlFor="reg-confirm" className="block text-xs font-medium text-ink">
            Xác nhận mật khẩu *
          </label>
          <Input
            ref={confirmRef}
            id="reg-confirm"
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            aria-invalid={Boolean(errors.confirmPassword)}
            aria-describedby={errors.confirmPassword ? "reg-confirm-error" : undefined}
            placeholder="Nhập lại mật khẩu"
            className="h-9"
          />
          {errors.confirmPassword ? (
            <p id="reg-confirm-error" role="alert" className="text-xs text-risk-high">
              {errors.confirmPassword}
            </p>
          ) : null}
        </div>

        <fieldset className="space-y-2.5 rounded-md border border-border bg-canvas p-3">
          <legend className="px-1 text-xs font-semibold text-ink">
            Đồng ý xử lý dữ liệu cá nhân (Luật 91/2025/QH15 · v{CONSENT_POLICY_VERSION})
          </legend>
          <p className="text-[11px] leading-4 text-ink-muted">{PDPL_CONSENT_SUMMARY_VI}</p>

          <div className="space-y-1">
            <label
              htmlFor="consent-core"
              className="flex cursor-pointer items-start gap-2 text-xs text-ink"
            >
              <input
                ref={consentRef}
                id="consent-core"
                type="checkbox"
                checked={acceptCore}
                onChange={(e) => setAcceptCore(e.target.checked)}
                aria-invalid={Boolean(errors.acceptCore)}
                aria-describedby={errors.acceptCore ? "consent-core-error" : undefined}
                className="mt-0.5 size-4 shrink-0 rounded border-border-strong accent-primary"
              />
              <span>
                <strong>Bắt buộc:</strong> Đồng ý xử lý email &amp; phiên đăng nhập để lưu danh
                mục theo dõi và ghi chú phân tích cá nhân.
              </span>
            </label>
            {errors.acceptCore ? (
              <p id="consent-core-error" role="alert" className="pl-6 text-xs text-risk-high">
                {errors.acceptCore}
              </p>
            ) : null}
          </div>

          <label
            htmlFor="consent-alerts"
            className="flex cursor-pointer items-start gap-2 text-xs text-ink-muted"
          >
            <input
              id="consent-alerts"
              type="checkbox"
              checked={acceptAlerts}
              onChange={(e) => setAcceptAlerts(e.target.checked)}
              className="mt-0.5 size-4 shrink-0 rounded border-border-strong accent-primary"
            />
            <span>
              <strong>Tùy chọn:</strong> Nhận cảnh báo rủi ro qua email hoặc Telegram cho các mã
              trong danh mục theo dõi.
            </span>
          </label>

          <label
            htmlFor="consent-rag"
            className="flex cursor-pointer items-start gap-2 text-xs text-ink-muted"
          >
            <input
              id="consent-rag"
              type="checkbox"
              checked={acceptRag}
              onChange={(e) => setAcceptRag(e.target.checked)}
              className="mt-0.5 size-4 shrink-0 rounded border-border-strong accent-primary"
            />
            <span>
              <strong>Tùy chọn:</strong> Cho phép truy vấn đối chiếu thuyết minh BCTC qua mô hình
              ngôn ngữ (đã ẩn danh PII theo Điều 20 Luật 91/2025).
            </span>
          </label>
        </fieldset>

        <Button type="submit" disabled={submitting} className="h-9 w-full">
          <UserPlus className="size-3.5" aria-hidden="true" />
          <span>{submitting ? "Đang tạo tài khoản..." : "Tạo tài khoản"}</span>
        </Button>
      </form>
    </AuthFormShell>
  );
}
