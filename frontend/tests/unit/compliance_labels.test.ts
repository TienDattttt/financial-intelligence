import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  COMPLIANCE_DISCLAIMER_VI,
  COMPLIANCE_LAW_REFERENCES_VI,
  RISK_TIER_LEGAL_LABEL_VI,
  TT99_GATE_VERDICT_LABELS,
  getTt99GateVerdictMeta,
} from "@/lib/compliance";
import {
  RISK_TIER_META,
  RISK_TIER_ORDER,
  getDistressRiskBadge,
  getRiskTierMeta,
} from "@/lib/domain-labels";
import {
  BUILTIN_SCREENER_PRESETS,
  SCREENER_RISK_TIER_META,
  getScreenerRiskTierMeta,
} from "@/lib/saved-screens-store";

const FRONTEND_ROOT = path.resolve(__dirname, "../..");
const FORBIDDEN_ND88_RE =
  /xếp\s+hạng\s+tín\s+nhiệm|(?:xếp\s+)?bậc\s+tín\s+nhiệm|phân\s+tầng\s+tín\s+nhiệm|\btín\s+nhiệm\b|khuyến\s+nghị\s+(?:mua|bán)(?:\s*\/\s*(?:mua|bán))?/i;

function collectSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...collectSourceFiles(full));
    } else if (entry.isFile() && /\.(ts|tsx)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

describe("V3-03 NĐ 88/2014 & Luật CK 56/2024 Compliance Labels", () => {
  it("exports canonical COMPLIANCE_DISCLAIMER_VI and RISK_TIER_LEGAL_LABEL_VI", () => {
    expect(COMPLIANCE_DISCLAIMER_VI).toContain("88/2014/NĐ-CP");
    expect(COMPLIANCE_DISCLAIMER_VI).toContain("56/2024/QH15");
    expect(COMPLIANCE_DISCLAIMER_VI).not.toMatch(FORBIDDEN_ND88_RE);

    expect(RISK_TIER_LEGAL_LABEL_VI).toBe(
      "Nhóm cảnh báo rủi ro tài chính định lượng (R1–R5)",
    );
    expect(RISK_TIER_LEGAL_LABEL_VI).not.toMatch(FORBIDDEN_ND88_RE);
    expect(COMPLIANCE_LAW_REFERENCES_VI.length).toBeGreaterThanOrEqual(3);

    expect(Object.keys(TT99_GATE_VERDICT_LABELS)).toEqual([
      "INSUFFICIENT_TT99_SAMPLES",
      "SHADOW_MONITORING",
      "READY_FOR_GA",
      "DRIFT_BREACH",
    ]);
    expect(getTt99GateVerdictMeta("READY_FOR_GA").tone).toBe("low");
    expect(getTt99GateVerdictMeta("DRIFT_BREACH").tone).toBe("high");
    expect(getTt99GateVerdictMeta("SHADOW_MONITORING").tone).toBe("mid");
    expect(getTt99GateVerdictMeta("INSUFFICIENT_TT99_SAMPLES").tone).toBe("neutral");
  });

  it("removes all forbidden NĐ 88/2014 phrases from domain-labels and saved-screens-store", () => {
    for (const tier of RISK_TIER_ORDER) {
      const meta = RISK_TIER_META[tier];
      expect(meta.label).not.toMatch(FORBIDDEN_ND88_RE);
      expect(meta.shortLabel).not.toMatch(FORBIDDEN_ND88_RE);
      expect(meta.description).not.toMatch(FORBIDDEN_ND88_RE);

      const screenerMeta = SCREENER_RISK_TIER_META[tier];
      expect(screenerMeta.label).not.toMatch(FORBIDDEN_ND88_RE);
      expect(screenerMeta.description).not.toMatch(FORBIDDEN_ND88_RE);
    }

    expect(getRiskTierMeta(null).description).not.toMatch(FORBIDDEN_ND88_RE);
    expect(getScreenerRiskTierMeta(null).description).not.toMatch(FORBIDDEN_ND88_RE);
    expect(getDistressRiskBadge(0.05).description).not.toMatch(FORBIDDEN_ND88_RE);

    for (const preset of BUILTIN_SCREENER_PRESETS) {
      expect(preset.name).not.toMatch(FORBIDDEN_ND88_RE);
      expect(preset.description).not.toMatch(FORBIDDEN_ND88_RE);
    }
  });

  it("contains zero forbidden NĐ 88/2014 or buy/sell wording across all frontend/src files", () => {
    const files = collectSourceFiles(path.join(FRONTEND_ROOT, "src"));
    expect(files.length).toBeGreaterThan(10);
    for (const filePath of files) {
      const content = fs.readFileSync(filePath, "utf8");
      const match = FORBIDDEN_ND88_RE.exec(content);
      expect(
        match,
        `Forbidden compliance wording "${match?.[0] ?? ""}" found in ${path.relative(FRONTEND_ROOT, filePath)}`,
      ).toBeNull();
    }

    const productDoc = fs.readFileSync(
      path.join(FRONTEND_ROOT, "PRODUCT.md"),
      "utf8",
    );
    expect(productDoc).not.toMatch(/bậc\s+tín\s+nhiệm|xếp\s+hạng\s+tín\s+nhiệm/i);
    expect(productDoc).toContain(RISK_TIER_LEGAL_LABEL_VI);
  });
});
