import * as React from "react";
import type { EChartsOption } from "echarts";

export type ResolvedTheme = "light" | "dark";
export type ThemePreference = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "finintel:theme:v1";

/**
 * Shared Vietnamese locale number formatter for ECharts axes & tooltips
 * (e.g., 1234.5 -> "1.234,5"). Avoids mixing "." and "," decimal separators.
 */
export function formatChartNumber(
  value: number,
  decimals = 1,
  options?: { trimTrailingZeros?: boolean },
): string {
  if (!Number.isFinite(value)) return "N/A";
  const formatted = new Intl.NumberFormat("vi-VN", {
    minimumFractionDigits: options?.trimTrailingZeros ? 0 : decimals,
    maximumFractionDigits: decimals,
  }).format(value);
  return formatted;
}

export function formatChartPercent(
  value: number,
  decimals = 1,
  options?: { isRatio?: boolean; signed?: boolean },
): string {
  if (!Number.isFinite(value)) return "N/A";
  const pct = options?.isRatio ? value * 100 : value;
  const absFormatted = formatChartNumber(Math.abs(pct), decimals);
  if (pct < 0) return `−${absFormatted}%`;
  if (pct > 0 && options?.signed) return `+${absFormatted}%`;
  return `${absFormatted}%`;
}

export interface ChartPalette {
  theme: ResolvedTheme;
  fontFamily: string;
  monoFontFamily: string;
  surface: string;
  surfaceMuted: string;
  canvas: string;
  border: string;
  borderStrong: string;
  splitLine: string;
  ink: string;
  inkMuted: string;
  inkSubtle: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  company: string;
  companyAreaTop: string;
  companyAreaBottom: string;
  peerMedian: string;
  peerMedianMuted: string;
  peerBandFill: string;
  fallbackAreaFill: string;
  riskLow: string;
  riskMid: string;
  riskHigh: string;
  riskHighMuted: string;
  riskHighBandFill: string;
  riskHighBandBorder: string;
  neutral: string;
  waterfall: {
    base: string;
    increase: string;
    decrease: string;
    other: string;
    final: string;
  };
  /** Institutional 5-series palette — strictly NO purple/violet (#6B4C9A) or cyan. */
  series: readonly [string, string, string, string, string];
}

export const LIGHT_CHART_PALETTE: ChartPalette = {
  theme: "light",
  fontFamily: '"IBM Plex Sans", system-ui, -apple-system, sans-serif',
  monoFontFamily: '"IBM Plex Mono", ui-monospace, SFMono-Regular, monospace',
  surface: "#FFFFFF",
  surfaceMuted: "#F1F5F9",
  canvas: "#F8FAFC",
  border: "#E2E8F0",
  borderStrong: "#CBD5E1",
  splitLine: "#E2E8F0",
  ink: "#0F172A",
  inkMuted: "#475569",
  inkSubtle: "#5B6B82",
  tooltipBg: "#FFFFFF",
  tooltipBorder: "#CBD5E1",
  tooltipText: "#0F172A",
  company: "#163A5F",
  companyAreaTop: "rgba(22, 58, 95, 0.16)",
  companyAreaBottom: "rgba(22, 58, 95, 0.01)",
  peerMedian: "#475569",
  peerMedianMuted: "rgba(71, 85, 105, 0.28)",
  peerBandFill: "rgba(71, 85, 105, 0.14)",
  fallbackAreaFill: "rgba(146, 64, 14, 0.10)",
  riskLow: "#166534",
  riskMid: "#92400E",
  riskHigh: "#991B1B",
  riskHighMuted: "rgba(153, 27, 27, 0.28)",
  riskHighBandFill: "rgba(153, 27, 27, 0.12)",
  riskHighBandBorder: "rgba(153, 27, 27, 0.28)",
  neutral: "#5B6B82",
  waterfall: {
    base: "#475569",
    increase: "#991B1B",
    decrease: "#166534",
    other: "#5B6B82",
    final: "#163A5F",
  },
  series: ["#163A5F", "#166534", "#92400E", "#991B1B", "#475569"],
};

export const DARK_CHART_PALETTE: ChartPalette = {
  theme: "dark",
  fontFamily: '"IBM Plex Sans", system-ui, -apple-system, sans-serif',
  monoFontFamily: '"IBM Plex Mono", ui-monospace, SFMono-Regular, monospace',
  surface: "#11161F",
  surfaceMuted: "#171E2B",
  canvas: "#0B0F14",
  border: "#222B3A",
  borderStrong: "#334155",
  splitLine: "#1E293B",
  ink: "#F1F5F9",
  inkMuted: "#94A3B8",
  inkSubtle: "#8B9BB2",
  tooltipBg: "#11161F",
  tooltipBorder: "#334155",
  tooltipText: "#F1F5F9",
  company: "#60A5FA",
  companyAreaTop: "rgba(96, 165, 250, 0.22)",
  companyAreaBottom: "rgba(96, 165, 250, 0.02)",
  peerMedian: "#94A3B8",
  peerMedianMuted: "rgba(148, 163, 184, 0.28)",
  peerBandFill: "rgba(148, 163, 184, 0.16)",
  fallbackAreaFill: "rgba(251, 191, 36, 0.12)",
  riskLow: "#34D399",
  riskMid: "#FBBF24",
  riskHigh: "#F87171",
  riskHighMuted: "rgba(248, 113, 113, 0.28)",
  riskHighBandFill: "rgba(248, 113, 113, 0.18)",
  riskHighBandBorder: "rgba(248, 113, 113, 0.36)",
  neutral: "#94A3B8",
  waterfall: {
    base: "#94A3B8",
    increase: "#F87171",
    decrease: "#34D399",
    other: "#8B9BB2",
    final: "#60A5FA",
  },
  series: ["#60A5FA", "#34D399", "#FBBF24", "#F87171", "#94A3B8"],
};

function readCssToken(
  styles: CSSStyleDeclaration | null,
  varName: string,
  fallback: string,
): string {
  if (!styles) return fallback;
  const val = styles.getPropertyValue(varName).trim();
  return val || fallback;
}

export function getChartPalette(theme: ResolvedTheme): ChartPalette {
  const base = theme === "dark" ? DARK_CHART_PALETTE : LIGHT_CHART_PALETTE;
  if (typeof window === "undefined" || typeof document === "undefined") {
    return base;
  }

  const styles = window.getComputedStyle(document.documentElement);
  const surface = readCssToken(styles, "--surface", base.surface);
  const surfaceMuted = readCssToken(styles, "--surface-muted", base.surfaceMuted);
  const canvas = readCssToken(styles, "--canvas", base.canvas);
  const border = readCssToken(styles, "--border-color", base.border);
  const borderStrong = readCssToken(styles, "--border-strong-color", base.borderStrong);
  const splitLine = readCssToken(styles, "--chart-split-line", base.splitLine);
  const ink = readCssToken(styles, "--ink", base.ink);
  const inkMuted = readCssToken(styles, "--ink-muted", base.inkMuted);
  const inkSubtle = readCssToken(styles, "--ink-subtle", base.inkSubtle);
  const company = readCssToken(styles, "--accent-ink", base.company);
  const riskLow = readCssToken(styles, "--risk-low", base.riskLow);
  const riskMid = readCssToken(styles, "--risk-mid", base.riskMid);
  const riskHigh = readCssToken(styles, "--risk-high", base.riskHigh);

  return {
    ...base,
    surface,
    surfaceMuted,
    canvas,
    border,
    borderStrong,
    splitLine,
    ink,
    inkMuted,
    inkSubtle,
    tooltipBg: surface,
    tooltipBorder: borderStrong,
    tooltipText: ink,
    company,
    peerMedian: inkMuted,
    riskLow,
    riskMid,
    riskHigh,
    neutral: inkSubtle,
    waterfall: {
      base: inkMuted,
      increase: riskHigh,
      decrease: riskLow,
      other: inkSubtle,
      final: company,
    },
    series: [company, riskLow, riskMid, riskHigh, inkMuted],
  };
}

export function resolveThemePreference(
  preference: ThemePreference,
  systemPrefersDark: boolean,
): ResolvedTheme {
  if (preference === "dark") return "dark";
  if (preference === "light") return "light";
  return systemPrefersDark ? "dark" : "light";
}

export function getChartMotionOptions(reducedMotion: boolean): Pick<
  EChartsOption,
  "animation" | "animationDuration" | "animationDurationUpdate" | "animationEasing"
> {
  if (reducedMotion) {
    return {
      animation: false,
      animationDuration: 0,
      animationDurationUpdate: 0,
    };
  }
  return {
    animation: true,
    animationDuration: 250,
    animationDurationUpdate: 250,
    animationEasing: "cubicOut",
  };
}

function mergeAxisDefaults(
  axis: unknown,
  palette: ChartPalette,
): unknown {
  if (!axis) return axis;
  const applySingle = (ax: Record<string, unknown>) => {
    const axisLine = (ax.axisLine as Record<string, unknown> | undefined) ?? {};
    const axisLineStyle = (axisLine.lineStyle as Record<string, unknown> | undefined) ?? {};
    const axisLabel = (ax.axisLabel as Record<string, unknown> | undefined) ?? {};
    const nameTextStyle = (ax.nameTextStyle as Record<string, unknown> | undefined) ?? {};
    const splitLine = (ax.splitLine as Record<string, unknown> | undefined) ?? {};
    const splitLineStyle = (splitLine.lineStyle as Record<string, unknown> | undefined) ?? {};

    return {
      ...ax,
      axisLine: {
        ...axisLine,
        lineStyle: { color: palette.border, ...axisLineStyle },
      },
      axisLabel: {
        color: palette.inkMuted,
        fontSize: 11,
        fontFamily: palette.fontFamily,
        ...axisLabel,
      },
      nameTextStyle: {
        color: palette.inkMuted,
        fontSize: 11,
        fontFamily: palette.fontFamily,
        ...nameTextStyle,
      },
      splitLine: {
        ...splitLine,
        lineStyle: { color: palette.splitLine, ...splitLineStyle },
      },
    };
  };

  if (Array.isArray(axis)) {
    return axis.map((item) =>
      item && typeof item === "object" ? applySingle(item as Record<string, unknown>) : item,
    );
  }
  if (typeof axis === "object") {
    return applySingle(axis as Record<string, unknown>);
  }
  return axis;
}

export function applyChartThemeDefaults(
  option: EChartsOption,
  theme: ResolvedTheme,
  reducedMotion: boolean,
): EChartsOption {
  const palette = getChartPalette(theme);
  const motion = getChartMotionOptions(reducedMotion);
  const opt = option as Record<string, unknown>;

  const tooltip = opt.tooltip as Record<string, unknown> | undefined;
  const tooltipTextStyle = (tooltip?.textStyle as Record<string, unknown> | undefined) ?? {};

  const legend = opt.legend as Record<string, unknown> | undefined;
  const legendTextStyle = (legend?.textStyle as Record<string, unknown> | undefined) ?? {};

  const existingTextStyle = (opt.textStyle as Record<string, unknown> | undefined) ?? {};

  return {
    ...option,
    backgroundColor: "transparent",
    color: [...palette.series],
    textStyle: {
      fontFamily: palette.fontFamily,
      color: palette.ink,
      ...existingTextStyle,
    },
    ...motion,
    ...(tooltip
      ? {
          tooltip: {
            backgroundColor: palette.tooltipBg,
            borderColor: palette.tooltipBorder,
            borderWidth: 1,
            extraCssText:
              theme === "dark"
                ? "box-shadow: 0 8px 24px rgba(0, 0, 0, 0.50);"
                : "box-shadow: 0 4px 16px rgba(15, 23, 42, 0.10);",
            ...tooltip,
            textStyle: {
              color: palette.tooltipText,
              fontFamily: palette.fontFamily,
              fontSize: 12,
              ...tooltipTextStyle,
            },
          },
        }
      : {}),
    ...(legend
      ? {
          legend: {
            ...legend,
            textStyle: {
              color: palette.inkMuted,
              fontFamily: palette.fontFamily,
              fontSize: 11,
              ...legendTextStyle,
            },
          },
        }
      : {}),
    ...(opt.xAxis ? { xAxis: mergeAxisDefaults(opt.xAxis, palette) as EChartsOption["xAxis"] } : {}),
    ...(opt.yAxis ? { yAxis: mergeAxisDefaults(opt.yAxis, palette) as EChartsOption["yAxis"] } : {}),
  };
}

export function usePrefersReducedMotion(): boolean {
  // Always start with false to match SSR and prevent hydration mismatches;
  // synchronize immediately after hydration inside useEffect.
  const [reduced, setReduced] = React.useState<boolean>(false);

  React.useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  return reduced;
}

export function useResolvedDocumentTheme(): ResolvedTheme {
  // Always start with "light" on first render to match SSR and prevent hydration mismatches;
  // synchronize immediately after hydration inside useEffect.
  const [theme, setTheme] = React.useState<ResolvedTheme>("light");

  React.useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    const sync = () => {
      setTheme(root.classList.contains("dark") ? "dark" : "light");
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { attributes: true, attributeFilter: ["class", "data-theme"] });
    return () => observer.disconnect();
  }, []);

  return theme;
}

const subscribeNoop = () => () => {};

export function useChartTheme(): {
  theme: ResolvedTheme;
  palette: ChartPalette;
  reducedMotion: boolean;
} {
  const theme = useResolvedDocumentTheme();
  const reducedMotion = usePrefersReducedMotion();
  // useSyncExternalStore returns false on SSR and during the React hydration
  // pass, then true on the client — preventing inline-style hydration
  // mismatches from getComputedStyle() in dark mode or hex-case differences.
  const hydrated = React.useSyncExternalStore(
    subscribeNoop,
    () => true,
    () => false,
  );

  const palette = React.useMemo(
    () => (hydrated ? getChartPalette(theme) : LIGHT_CHART_PALETTE),
    [hydrated, theme],
  );
  return { theme, palette, reducedMotion };
}
