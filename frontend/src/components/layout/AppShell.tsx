"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bookmark,
  BookOpen,
  Building2,
  Filter,
  GitCompare,
  LayoutDashboard,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  X,
} from "lucide-react";
import { CommandSearch } from "@/components/layout/CommandSearch";
import { SourceDrawer } from "@/components/layout/SourceDrawer";
import { useAppState } from "@/components/providers/app-providers";
import { Select } from "@/components/ui/primitives";
import {
  PERIOD_TYPE_LABELS,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import { formatDateVi, formatPeriodLabel } from "@/lib/format";
import {
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  subscribePreferences,
} from "@/lib/watchlist-store";
import { cn } from "@/lib/utils";
import type { PeriodType, StatementScope } from "@/types/api";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const {
    statementScope,
    setStatementScope,
    periodType,
    setPeriodType,
    periodEndDate,
    setPeriodEndDate,
    availableDates,
    publishedAt,
    setCommandOpen,
  } = useAppState();

  const [collapsed, setCollapsed] = React.useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);

  const watchlist = React.useSyncExternalStore(
    subscribePreferences,
    getWatchlistSnapshot,
    getWatchlistServerSnapshot,
  );

  const navItems = React.useMemo(
    () => [
      { href: "/", label: "Tổng quan", icon: LayoutDashboard },
      { href: "/screener", label: "Sàng lọc DN", icon: Filter },
      {
        href: "/companies/HPG",
        label: "Hồ sơ DN",
        icon: Building2,
        matchPrefix: "/companies/",
      },
      {
        href: "/watchlist",
        label: `Theo dõi (${watchlist.length})`,
        icon: Bookmark,
      },
      { href: "/compare", label: "So sánh", icon: GitCompare },
      { href: "/stress-test", label: "Kịch bản sốc", icon: Activity },
      { href: "/methodology", label: "Dữ liệu & Phương pháp", icon: BookOpen },
    ],
    [watchlist.length],
  );

  const isActive = (href: string, matchPrefix?: string) => {
    if (matchPrefix) return pathname.startsWith(matchPrefix);
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  return (
    <div className="flex min-h-screen bg-canvas text-ink">
      {/* Desktop Sidebar */}
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-150 lg:flex",
          collapsed ? "w-14" : "w-[220px]",
        )}
      >
        <div className="flex h-[52px] items-center justify-between border-b border-border px-3">
          {!collapsed ? (
            <Link href="/" className="flex items-center gap-2 font-semibold text-ink">
              <span className="inline-flex size-6 items-center justify-center rounded bg-accent-ink font-mono text-xs font-semibold text-white">
                FI
              </span>
              <span className="tracking-tight text-[15px]">FinIntel</span>
            </Link>
          ) : (
            <Link
              href="/"
              aria-label="FinIntel Trang chủ"
              className="mx-auto inline-flex size-7 items-center justify-center rounded bg-accent-ink font-mono text-xs font-semibold text-white"
            >
              FI
            </Link>
          )}
          {!collapsed ? (
            <button
              type="button"
              aria-label="Thu gọn thanh điều hướng"
              onClick={() => setCollapsed(true)}
              className="rounded p-1 text-ink-muted hover:bg-surface-muted hover:text-ink"
            >
              <PanelLeftClose className="size-4" />
            </button>
          ) : null}
        </div>

        <nav className="flex-1 space-y-0.5 p-2" aria-label="Điều hướng chính">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = isActive(item.href, item.matchPrefix);
            return (
              <Link
                key={item.href}
                href={item.href}
                title={collapsed ? item.label : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded px-2.5 py-2 text-[13px] font-medium transition-colors",
                  active
                    ? "bg-accent-soft text-accent-ink font-semibold"
                    : "text-ink-muted hover:bg-surface-muted hover:text-ink",
                  collapsed && "justify-center px-0",
                )}
              >
                <Icon className="size-4 shrink-0" aria-hidden="true" />
                {!collapsed ? <span className="truncate">{item.label}</span> : null}
              </Link>
            );
          })}
        </nav>

        {collapsed ? (
          <div className="border-t border-border p-2">
            <button
              type="button"
              aria-label="Mở rộng thanh điều hướng"
              onClick={() => setCollapsed(false)}
              className="flex w-full justify-center rounded p-1.5 text-ink-muted hover:bg-surface-muted hover:text-ink"
            >
              <PanelLeftOpen className="size-4" />
            </button>
          </div>
        ) : (
          <div className="border-t border-border p-3 text-[11px] leading-4 text-ink-subtle">
            Phân tích rủi ro tài chính &amp; kế toán điều tra DN niêm yết VN
          </div>
        )}
      </aside>

      {/* Mobile Drawer */}
      {mobileMenuOpen ? (
        <div
          className="fixed inset-0 z-50 flex bg-ink/40 lg:hidden"
          onClick={() => setMobileMenuOpen(false)}
        >
          <aside
            className="flex h-full w-64 flex-col border-r border-border bg-surface"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex h-[52px] items-center justify-between border-b border-border px-3">
              <span className="font-semibold text-ink">FinIntel</span>
              <button
                type="button"
                aria-label="Đóng menu"
                onClick={() => setMobileMenuOpen(false)}
                className="rounded p-1 text-ink-muted"
              >
                <X className="size-4" />
              </button>
            </div>
            <nav className="space-y-0.5 p-2">
              {navItems.map((item) => {
                const Icon = item.icon;
                const active = isActive(item.href, item.matchPrefix);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setMobileMenuOpen(false)}
                    className={cn(
                      "flex items-center gap-2.5 rounded px-2.5 py-2 text-[13px] font-medium",
                      active
                        ? "bg-accent-soft text-accent-ink font-semibold"
                        : "text-ink-muted hover:bg-surface-muted hover:text-ink",
                    )}
                  >
                    <Icon className="size-4 shrink-0" aria-hidden="true" />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </nav>
          </aside>
        </div>
      ) : null}

      {/* Main Column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex min-h-[52px] flex-wrap items-center justify-between gap-2 border-b border-border bg-surface px-4 py-1.5">
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Mở menu điều hướng"
              onClick={() => setMobileMenuOpen(true)}
              className="rounded border border-border p-1.5 text-ink-muted hover:bg-surface-muted lg:hidden"
            >
              <Menu className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => setCommandOpen(true)}
              className="flex h-8 w-52 items-center justify-between gap-2 rounded border border-border bg-canvas px-2.5 text-left text-[13px] text-ink-muted hover:border-border-strong sm:w-72"
            >
              <span className="flex items-center gap-1.5 truncate">
                <Search className="size-3.5 shrink-0" aria-hidden="true" />
                <span className="truncate">Tìm mã hoặc tên DN...</span>
              </span>
              <kbd className="hidden rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-[10px] text-ink-subtle sm:inline">
                Ctrl+K
              </kbd>
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Select
              aria-label="Chọn kỳ dữ liệu"
              value={periodEndDate ?? ""}
              onChange={(e) => setPeriodEndDate(e.target.value || null)}
              className="h-8 text-xs"
            >
              {availableDates.length === 0 ? (
                <option value="">Kỳ mới nhất</option>
              ) : (
                availableDates.map((d) => (
                  <option key={d} value={d}>
                    {formatPeriodLabel(d, periodType)} ({formatDateVi(d)})
                  </option>
                ))
              )}
            </Select>

            <Select
              aria-label="Chọn loại kỳ"
              value={periodType}
              onChange={(e) => setPeriodType(e.target.value as PeriodType)}
              className="h-8 text-xs"
            >
              {Object.entries(PERIOD_TYPE_LABELS).map(([k, lbl]) => (
                <option key={k} value={k}>
                  {lbl}
                </option>
              ))}
            </Select>

            <Select
              aria-label="Chọn phạm vi báo cáo"
              value={statementScope}
              onChange={(e) => setStatementScope(e.target.value as StatementScope)}
              className="h-8 text-xs"
            >
              {Object.entries(STATEMENT_SCOPE_LABELS).map(([k, lbl]) => (
                <option key={k} value={k}>
                  {lbl}
                </option>
              ))}
            </Select>
          </div>
        </header>

        <div className="border-b border-border/70 bg-surface-muted/50 px-4 py-1 text-[11px] text-ink-muted">
          <span>
            Dữ liệu cập nhật đến{" "}
            <strong className="font-medium text-ink">
              {publishedAt ? formatDateVi(publishedAt) : "kỳ công bố mới nhất"}
            </strong>{" "}
            · Nguồn: BCTC công bố của doanh nghiệp niêm yết
          </span>
        </div>

        <main className="mx-auto w-full max-w-[1440px] flex-1 p-4 sm:p-6">
          {children}
        </main>

        <footer className="border-t border-border bg-surface px-4 py-3 text-xs text-ink-muted">
          <div className="mx-auto flex max-w-[1440px] flex-col justify-between gap-1 sm:flex-row sm:items-center">
            <span>
              Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.
            </span>
            <Link href="/methodology" className="text-accent-ink hover:underline">
              Phương pháp &amp; Giới hạn mô hình →
            </Link>
          </div>
        </footer>
      </div>

      <CommandSearch />
      <SourceDrawer />
    </div>
  );
}
