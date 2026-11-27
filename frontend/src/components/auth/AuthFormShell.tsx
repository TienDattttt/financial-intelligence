"use client";

import * as React from "react";
import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { COMPLIANCE_DISCLAIMER_VI } from "@/lib/compliance";

export function AuthFormShell({
  title,
  subtitle,
  children,
  footerLinks,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footerLinks?: React.ReactNode;
}) {
  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-4 py-4 sm:py-8">
      <Card>
        <CardHeader className="flex-col items-start gap-1 border-b border-border px-5 py-4">
          <div className="flex items-center gap-2 text-xs font-medium text-accent-ink">
            <ShieldCheck className="size-4 shrink-0" aria-hidden="true" />
            <span>Tài khoản &amp; Không gian làm việc FinIntel</span>
          </div>
          <CardTitle className="text-lg font-semibold text-ink">{title}</CardTitle>
          <p className="text-xs leading-5 text-ink-muted">{subtitle}</p>
        </CardHeader>
        <CardContent className="space-y-4 px-5 py-4">{children}</CardContent>
      </Card>

      {footerLinks ? (
        <div className="flex flex-wrap items-center justify-between gap-2 px-1 text-xs text-ink-muted">
          {footerLinks}
        </div>
      ) : null}

      <p className="px-1 text-[11px] leading-4 text-ink-subtle">
        {COMPLIANCE_DISCLAIMER_VI}{" "}
        <Link href="/methodology" className="text-accent-ink hover:underline">
          Xem phương pháp &amp; miễn trừ
        </Link>
      </p>
    </div>
  );
}
