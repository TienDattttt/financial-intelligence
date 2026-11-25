// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const initCalls: HTMLElement[] = [];
const disposeCalls: HTMLElement[] = [];

vi.mock("echarts", () => {
  return {
    init: (el: HTMLElement) => {
      initCalls.push(el);
      // Simulate real ECharts DOM mutation inside container element
      const wrapper = document.createElement("div");
      wrapper.setAttribute("data-echarts-canvas", "true");
      el.appendChild(wrapper);
      return {
        on: vi.fn(),
        off: vi.fn(),
        setOption: vi.fn(),
        resize: vi.fn(),
        getDataURL: vi.fn(() => "data:image/png;base64,AA=="),
        getZr: () => ({ on: vi.fn(), off: vi.fn() }),
        containPixel: vi.fn(() => false),
        convertFromPixel: vi.fn(() => [0, 0]),
        dispose: () => {
          disposeCalls.push(el);
          // Real echarts.dispose() clears the container's innerHTML
          el.innerHTML = "";
        },
      };
    },
  };
});

import { EChartBox } from "@/components/charts/EChartBox";
import { LIGHT_CHART_PALETTE, useChartTheme } from "@/lib/echarts-theme";

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

describe("EChartBox table toggle regression", () => {
  beforeEach(() => {
    initCalls.length = 0;
    disposeCalls.length = 0;
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders table rows on toggle and switches back to chart without removeChild DOM error", () => {
    render(
      React.createElement(EChartBox, {
        title: "So sánh Nợ vay / VCSH",
        option: { xAxis: { type: "category", data: ["Q4/2025"] }, yAxis: { type: "value" }, series: [] },
        tableHeaders: ["Kỳ", "HPG", "FPT", "VNM"],
        tableRows: [
          ["Q3/2025", "0,71x", "0,55x", "0,22x"],
          ["Q4/2025", "0,68x", "0,52x", "0,21x"],
        ],
      }),
    );

    expect(initCalls.length).toBe(1);
    expect(document.querySelectorAll("table").length).toBe(0);

    // Click "Xem dạng bảng" -> must render actual table and dispose detached chart container
    fireEvent.click(screen.getByRole("button", { name: /Xem dạng bảng/i }));
    expect(disposeCalls.length).toBe(1);
    const tables = document.querySelectorAll("table");
    expect(tables.length).toBe(1);
    expect(screen.getByText("0,71x")).toBeTruthy();
    expect(screen.getByText("0,22x")).toBeTruthy();

    // Click "Xem biểu đồ" -> must NOT throw NotFoundError: removeChild and must re-init chart
    fireEvent.click(screen.getByRole("button", { name: /Xem biểu đồ/i }));
    expect(document.querySelectorAll("table").length).toBe(0);
    expect(initCalls.length).toBe(2);

    // Toggle once more to ensure stability across repeated switches
    fireEvent.click(screen.getByRole("button", { name: /Xem dạng bảng/i }));
    expect(document.querySelectorAll("table").length).toBe(1);
    expect(disposeCalls.length).toBe(2);
  });
});

describe("useChartTheme hydration safety", () => {
  const prevActEnv = (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
    .IS_REACT_ACT_ENVIRONMENT;

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(() => {
    cleanup();
    document.documentElement.classList.remove("dark");
    document.documentElement.style.removeProperty("--accent-ink");
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
      prevActEnv;
  });

  it("hydrates without mismatch in light mode when CSS tokens use lowercase hex", async () => {
    const { renderToString } = await import("react-dom/server");
    const { hydrateRoot } = await import("react-dom/client");
    const { act } = await import("react");

    function Probe() {
      const { palette } = useChartTheme();
      return React.createElement("span", {
        "data-testid": "swatch",
        style: { backgroundColor: palette.series[0] },
      });
    }

    const serverHtml = renderToString(React.createElement(Probe));
    expect(serverHtml).toContain(LIGHT_CHART_PALETTE.series[0]);

    // Light mode globals.css defines lowercase #163a5f while LIGHT_CHART_PALETTE has #163A5F
    document.documentElement.style.setProperty("--accent-ink", "#163a5f");
    const container = document.createElement("div");
    container.innerHTML = serverHtml;
    document.body.appendChild(container);

    const onRecoverableError = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | null = null;
    await act(async () => {
      root = hydrateRoot(container, React.createElement(Probe), {
        onRecoverableError,
      });
    });

    expect(onRecoverableError).not.toHaveBeenCalled();

    await act(async () => {
      root?.unmount();
    });
    container.remove();
  });

  it("hydrates without mismatch when html has .dark and lowercase CSS tokens", async () => {
    const { renderToString } = await import("react-dom/server");
    const { hydrateRoot } = await import("react-dom/client");
    const { act } = await import("react");

    function Probe() {
      const { palette } = useChartTheme();
      return React.createElement("span", {
        "data-testid": "swatch",
        style: { backgroundColor: palette.series[0] },
      });
    }

    // 1. Server render (returns LIGHT_CHART_PALETTE)
    const serverHtml = renderToString(React.createElement(Probe));
    expect(serverHtml).toContain(LIGHT_CHART_PALETTE.series[0]);

    // 2. Client DOM before hydration: dark mode + lowercase CSS var
    document.documentElement.classList.add("dark");
    document.documentElement.style.setProperty("--accent-ink", "#60a5fa");
    const container = document.createElement("div");
    container.innerHTML = serverHtml;
    document.body.appendChild(container);

    const onRecoverableError = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | null = null;
    await act(async () => {
      root = hydrateRoot(container, React.createElement(Probe), {
        onRecoverableError,
      });
    });

    expect(onRecoverableError).not.toHaveBeenCalled();

    await act(async () => {
      root?.unmount();
    });
    container.remove();
  });
});
