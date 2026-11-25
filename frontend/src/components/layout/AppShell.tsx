"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Dialog } from "@base-ui/react/dialog";
import {
  Activity,
  Bookmark,
  BookOpen,
  Building2,
  Filter,
  GitCompare,
  LayoutDashboard,
  Menu,
  Monitor,
  Moon,
  Search,
  Sun,
  X,
} from "lucide-react";
import { CommandSearch } from "@/components/layout/CommandSearch";
import { SourceDrawer } from "@/components/layout/SourceDrawer";
import { useAppState } from "@/components/providers/app-providers";
import { Select } from "@/components/ui/primitives";
import { useAllIssuers } from "@/hooks/use-finintel-queries";
import {
  PERIOD_TYPE_LABELS,
  STATEMENT_SCOPE_LABELS,
} from "@/lib/domain-labels";
import type { ThemePreference } from "@/lib/echarts-theme";
import { formatDateVi, formatPeriodLabel } from "@/lib/format";
import {
  getLastViewedTickerServerSnapshot,
  getLastViewedTickerSnapshot,
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  subscribePreferences,
} from "@/lib/watchlist-store";
import { cn } from "@/lib/utils";
import type { PeriodType, StatementScope } from "@/types/api";

const THEME_OPTIONS: readonly {
  id: ThemePreference;
  label: string;
  ariaLabel: string;
  Icon: typeof Sun;
}[] = [
  { id: "light", label: "Sáng", ariaLabel: "Giao diện Sáng", Icon: Sun },
  { id: "dark", label: "Tối", ariaLabel: "Giao diện Tối", Icon: Moon },
  { id: "system", label: "Hệ thống", ariaLabel: "Theo hệ thống", Icon: Monitor },
];

const subscribeNoop = () => () => {};

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
    snapshotId,
    closeSourceDrawer,
    setCommandOpen,
    themePreference,
    setThemePreference,
  } = useAppState();

  React.useEffect(() => {
    closeSourceDrawer();
  }, [pathname, closeSourceDrawer]);

  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);
  const mounted = React.useSyncExternalStore(
    subscribeNoop,
    () => true,
    () => false,
  );

  const watchlist = React.useSyncExternalStore(
    subscribePreferences,
    getWatchlistSnapshot,
    getWatchlistServerSnapshot,
  );
  const lastViewedTicker = React.useSyncExternalStore(
    subscribePreferences,
    getLastViewedTickerSnapshot,
    getLastViewedTickerServerSnapshot,
  );
  const issuersQuery = useAllIssuers(snapshotId);

  const currentRouteTicker = React.useMemo(() => {
    if (!pathname.startsWith("/companies/")) return null;
    const raw = pathname.slice("/companies/".length).split("/")[0] ?? "";
    const clean = decodeURIComponent(raw).trim().toUpperCase();
    return /^[A-Z0-9]{1,10}$/.test(clean) ? clean : null;
  }, [pathname]);

  const companyProfileHref = React.useMemo(() => {
    const preferred =
      currentRouteTicker ??
      lastViewedTicker ??
      issuersQuery.data?.[0]?.ticker ??
      null;
    return preferred ? `/companies/${encodeURIComponent(preferred)}` : "/screener";
  }, [currentRouteTicker, issuersQuery.data, lastViewedTicker]);

  const navItems = React.useMemo(
    () => [
      { id: "overview", href: "/", label: "Tổng quan", icon: LayoutDashboard },
      { id: "screener", href: "/screener", label: "Sàng lọc DN", icon: Filter },
      {
        id: "company-profile",
        href: companyProfileHref,
        label: "Hồ sơ DN",
        icon: Building2,
        matchPrefix: "/companies/",
      },
      {
        id: "watchlist",
        href: "/watchlist",
        label: `Theo dõi (${watchlist.length})`,
        icon: Bookmark,
      },
      { id: "compare", href: "/compare", label: "So sánh", icon: GitCompare },
      { id: "stress-test", href: "/stress-test", label: "Kịch bản sốc", icon: Activity },
      { id: "methodology", href: "/methodology", label: "Dữ liệu & Phương pháp", icon: BookOpen },
    ],
    [companyProfileHref, watchlist.length],
  );

  const isActive = (href: string, matchPrefix?: string) => {
    if (matchPrefix) return pathname.startsWith(matchPrefix);
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  return (
    <div className="flex min-h-screen flex-col bg-canvas text-ink">
      {/* Skip-to-content link (WCAG 2.4.1) */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-2 focus:z-[60] focus:rounded-md focus:bg-primary focus:px-3 focus:py-1.5 focus:text-xs font-medium focus:text-primary-foreground focus:shadow-popover focus:outline-none"
      >
        Chuyển đến nội dung chính
      </a>

      {/* Mobile Navigation Drawer (@base-ui/react Dialog with focus trap & Escape) */}
      <Dialog.Root open={mobileMenuOpen} onOpenChange={setMobileMenuOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-ink/40 transition-opacity duration-150 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 dark:bg-black/60 lg:hidden" />
          <Dialog.Viewport className="fixed inset-0 z-50 flex lg:hidden">
            <Dialog.Popup
              aria-modal="true"
              aria-label="Menu điều hướng FinIntel"
              className="flex h-full w-64 flex-col border-r border-border bg-surface text-ink shadow-popover outline-none"
            >
              <Dialog.Title className="sr-only">Menu điều hướng FinIntel</Dialog.Title>
              <div className="flex h-[52px] items-center justify-between border-b border-border px-3">
                <Link
                  href="/"
                  onClick={() => setMobileMenuOpen(false)}
                  className="flex items-center gap-2 font-semibold text-ink"
                >
                  <span className="inline-flex size-6 items-center justify-center rounded-md bg-primary font-mono text-xs font-semibold text-primary-foreground">
                    FI
                  </span>
                  <span className="text-[15px]">FinIntel</span>
                </Link>
                <Dialog.Close
                  type="button"
                  aria-label="Đóng menu"
                  className="rounded-md p-1 text-ink-muted hover:bg-surface-hover hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <X className="size-4" />
                </Dialog.Close>
              </div>
              <nav className="flex-1 space-y-0.5 overflow-y-auto p-2" aria-label="Điều hướng di động">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  const active = isActive(item.href, item.matchPrefix);
                  return (
                    <Link
                      key={item.id}
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      onClick={() => setMobileMenuOpen(false)}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] font-medium transition-colors duration-150",
                        active
                          ? "bg-accent-soft text-accent-ink font-semibold"
                          : "text-ink-muted hover:bg-surface-hover hover:text-ink",
                      )}
                    >
                      <Icon className="size-4 shrink-0" aria-hidden="true" />
                      <span>{item.label}</span>
                    </Link>
                  );
                })}
              </nav>
            </Dialog.Popup>
          </Dialog.Viewport>
        </Dialog.Portal>
      </Dialog.Root>

      {/* Top Sticky Header: Row 1 Brand + Global Controls, Row 2 Horizontal Top Navigation */}
      <header className="sticky top-0 z-30 border-b border-border bg-surface">
        {/* Row 1: Brand + Search + Global Period/Scope + Theme */}
        <div className="mx-auto flex min-h-[50px] max-w-[1440px] flex-wrap items-center justify-between gap-2 px-4 py-1.5 sm:px-6">
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              aria-label="Mở menu điều hướng"
              onClick={() => setMobileMenuOpen(true)}
              className="rounded-md border border-border p-1.5 text-ink-muted transition-colors duration-150 hover:bg-surface-hover lg:hidden"
            >
              <Menu className="size-4" />
            </button>

            <Link
              href="/"
              className="flex shrink-0 items-center gap-2 font-semibold text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-md"
            >
              <span className="inline-flex size-6 items-center justify-center rounded-md bg-primary font-mono text-xs font-semibold text-primary-foreground">
                FI
              </span>
              <span className="tracking-tight text-[15px]">FinIntel</span>
            </Link>

            <button
              type="button"
              onClick={() => setCommandOpen(true)}
              className="flex h-8 w-44 items-center justify-between gap-2 rounded-md border border-border-strong bg-canvas px-2.5 text-left text-[13px] text-ink-muted transition-colors duration-150 hover:border-accent-ink/50 hover:text-ink sm:w-64 md:w-72"
            >
              <span className="flex items-center gap-1.5 truncate">
                <Search className="size-3.5 shrink-0" aria-hidden="true" />
                <span className="truncate">Tìm mã hoặc tên DN...</span>
              </span>
              <kbd className="hidden rounded-sm border border-border bg-surface px-1.5 py-0.5 font-mono text-[10px] text-ink-subtle sm:inline">
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

            <div
              role="group"
              aria-label="Chế độ hiển thị"
              className="inline-flex h-8 items-center rounded-md border border-border bg-surface-muted p-0.5"
            >
              {THEME_OPTIONS.map(({ id, label, ariaLabel, Icon }) => {
                const active = mounted ? themePreference === id : id === "light";
                return (
                  <button
                    key={id}
                    type="button"
                    aria-label={ariaLabel}
                    aria-pressed={active}
                    title={ariaLabel}
                    onClick={() => setThemePreference(id)}
                    className={cn(
                      "inline-flex h-7 items-center gap-1 rounded-sm px-2 text-xs font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      active
                        ? "bg-primary text-primary-foreground shadow-xs"
                        : "text-ink-muted hover:text-ink",
                    )}
                  >
                    <Icon className="size-3.5 shrink-0" aria-hidden="true" />
                    <span className="hidden xl:inline">{label}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Row 2: Desktop Horizontal Top Navigation Bar + Freshness Indicator */}
        <div className="hidden border-t border-border/80 bg-surface lg:block">
          <div className="mx-auto flex h-[38px] max-w-[1440px] items-center justify-between gap-4 px-4 sm:px-6">
            <nav
              className="flex h-full items-center gap-1 overflow-x-auto"
              aria-label="Điều hướng chính"
            >
              {navItems.map((item) => {
                const Icon = item.icon;
                const active = isActive(item.href, item.matchPrefix);
                return (
                  <Link
                    key={item.id}
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "relative inline-flex h-full shrink-0 items-center gap-1.5 border-b-2 px-2.5 text-[13px] font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                      active
                        ? "border-accent-ink text-accent-ink font-semibold"
                        : "border-transparent text-ink-muted hover:bg-surface-hover/60 hover:text-ink",
                    )}
                  >
                    <Icon className="size-3.5 shrink-0" aria-hidden="true" />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </nav>

            <span className="hidden shrink-0 text-[11px] text-ink-muted xl:inline">
              Cập nhật{" "}
              <strong className="font-medium text-ink">
                {publishedAt ? formatDateVi(publishedAt) : "kỳ mới nhất"}
              </strong>{" "}
              · BCTC niêm yết VN
            </span>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main
        id="main-content"
        tabIndex={-1}
        className="mx-auto w-full max-w-[1440px] flex-1 p-4 outline-none sm:p-6"
      >
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

      <CommandSearch />
      <SourceDrawer />
    </div>
  );
}
