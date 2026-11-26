// @vitest-environment jsdom
import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatFreshnessStrip } from "@/components/layout/AppShell";
import { formatFilingReference } from "@/components/layout/SourceDrawer";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import { SegmentedControl } from "@/components/ui/segmented-control";

describe("TASK-11: WAI-ARIA SegmentedControl, 3-tier Freshness strip, and SourceDrawer hygiene", () => {
  afterEach(() => {
    cleanup();
  });

  it("implements WAI-ARIA roving tabIndex and Arrow/Home/End keyboard navigation for tabs and radiogroup", () => {
    const onChange = vi.fn();
    const options = [
      { value: "overview", label: "Tổng quan" },
      { value: "ratios", label: "Tỷ số & Ngành" },
      { value: "quality", label: "Chất lượng LN" },
    ] as const;

    const { rerender } = render(
      React.createElement(SegmentedControl<"overview" | "ratios" | "quality">, {
        ariaLabel: "Các mục hồ sơ",
        variant: "underline",
        value: "overview",
        onChange,
        idPrefix: "demo",
        controlsPanelId: (v) => `panel-${v}`,
        options,
      }),
    );

    const tablist = screen.getByRole("tablist", { name: "Các mục hồ sơ" });
    expect(tablist).toBeTruthy();

    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs[0].getAttribute("tabindex")).toBe("0");
    expect(tabs[1].getAttribute("tabindex")).toBe("-1");
    expect(tabs[2].getAttribute("tabindex")).toBe("-1");
    expect(tabs[0].getAttribute("aria-controls")).toBe("panel-overview");
    expect(tabs[0].className).toContain("min-h-6");

    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("ratios");
    expect(document.activeElement).toBe(tabs[1]);

    fireEvent.keyDown(tabs[1], { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith("quality");
    expect(document.activeElement).toBe(tabs[2]);

    fireEvent.keyDown(tabs[2], { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("overview");
    expect(document.activeElement).toBe(tabs[0]);

    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("quality");

    rerender(
      React.createElement(SegmentedControl<"overview" | "ratios" | "quality">, {
        ariaLabel: "Chế độ hiển thị bảng",
        semantics: "radiogroup",
        size: "xs",
        value: "ratios",
        onChange,
        options,
      }),
    );

    const rg = screen.getByRole("radiogroup", { name: "Chế độ hiển thị bảng" });
    expect(rg).toBeTruthy();
    const radios = screen.getAllByRole("radio");
    expect(radios[0].getAttribute("aria-checked")).toBe("false");
    expect(radios[0].getAttribute("tabindex")).toBe("-1");
    expect(radios[1].getAttribute("aria-checked")).toBe("true");
    expect(radios[1].getAttribute("tabindex")).toBe("0");
    expect(radios[1].className).toContain("min-h-6");
  });

  it("formats 3-tier Freshness items in Vietnamese without leaking snapshot_id or port digests", () => {
    const items = formatFreshnessStrip(
      {
        bctc_snapshot_id: "snap_secret_internal_001",
        bctc_published_at: "2026-11-25T08:30:00Z",
        bctc_latest_period_end_date: "2026-09-30",
        market_prices_latest_trade_date: "2026-11-24",
        regulatory_latest_event_date: "2026-11-18",
        curated_pdfs_count: 42,
        extension_ports_digest: "deadbeef1234",
      },
      "2026-11-25T08:30:00Z",
    );

    expect(items).toHaveLength(3);
    const joined = items.map((i) => `${i.label}: ${i.value}`).join(" | ");
    expect(joined).toContain("BCTC");
    expect(joined).toContain("30/09/2026");
    expect(joined).toContain("Giá EOD");
    expect(joined).toContain("24/11/2026");
    expect(joined).toContain("Pháp lý");
    expect(joined).toContain("18/11/2026");
    expect(joined).not.toContain("snap_secret");
    expect(joined).not.toContain("deadbeef");
  });

  it("sanitizes filing references in SourceDrawer and enforces >=24px target size on InfoTooltip", () => {
    expect(formatFilingReference("FIL_HPG_2025_Q4")).toBe("FIL_HPG_2025_Q4");
    expect(
      formatFilingReference("FIL_ISS_HPG_CONSOLIDATED_2025_Q4.parquet"),
    ).not.toContain(".parquet");
    expect(
      formatFilingReference("FIL_ISS_HPG_CONSOLIDATED_2025_Q4.parquet"),
    ).not.toContain("CONSOLIDATED");

    render(
      React.createElement(InfoTooltip, {
        text: "Giải thích thử nghiệm",
        label: "Giải thích thử",
      }),
    );
    const btn = screen.getByRole("button", { name: "Giải thích thử" });
    expect(btn.className).toContain("size-6");
  });
});
