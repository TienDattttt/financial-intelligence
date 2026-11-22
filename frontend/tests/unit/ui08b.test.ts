import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  extractFiscalYears,
  FORENSIC_PRESET_QUERIES,
  formatLatencyMs,
  formatSimilarityScore,
  getRedFlagPresetQuestion,
  getSafeDocumentUrl,
  sanitizeAbstentionReason,
  tokenizeAnswerWithCitations,
} from "@/components/rag/DocumentInvestigationPanel";
import { ApiError, apiClient } from "@/lib/api-client";
import type { CitationItem, DocumentFileItem } from "@/types/api";

const WORKSPACE_FRONTEND_ROOT = path.resolve(__dirname, "../..");

describe("UI-08b Split-View Audited PDF RAG Helpers, API Client & Anti-AI-Slop", () => {
  it("exposes all 3 forensic red-flag presets (RF_03, RF_07, RF_09) and resolves full flag codes", () => {
    expect(FORENSIC_PRESET_QUERIES.map((q) => q.flagCode)).toEqual([
      "RF_03",
      "RF_07",
      "RF_09",
    ]);
    expect(getRedFlagPresetQuestion("RF_03_OTHER_RECEIVABLES_ANOMALY")).toBe(
      FORENSIC_PRESET_QUERIES[0].question,
    );
    expect(getRedFlagPresetQuestion("RF_07_AUDIT_OR_FILING_WARNING")).toBe(
      FORENSIC_PRESET_QUERIES[1].question,
    );
    expect(getRedFlagPresetQuestion("RF_09_FROZEN_CIP_CAPITALIZATION")).toBe(
      FORENSIC_PRESET_QUERIES[2].question,
    );
    expect(getRedFlagPresetQuestion("RF_01_ACCRUAL_DIVERGENCE")).toBeNull();
  });

  it("validates source_url strictly to http/https and extracts sorted fiscal years", () => {
    expect(getSafeDocumentUrl("https://cafef1.mediacdn.vn/hbc_2023.pdf")).toBe(
      "https://cafef1.mediacdn.vn/hbc_2023.pdf",
    );
    expect(getSafeDocumentUrl("http://example.com/doc.pdf")).toBe(
      "http://example.com/doc.pdf",
    );
    expect(getSafeDocumentUrl("javascript:alert(1)")).toBeNull();
    expect(getSafeDocumentUrl("data:text/html,<script>alert(1)</script>")).toBeNull();
    expect(getSafeDocumentUrl("   ")).toBeNull();
    expect(getSafeDocumentUrl(null)).toBeNull();

    const docs: DocumentFileItem[] = [
      {
        filing_id: "HBC_FY2022_CONSOLIDATED_111111111111",
        issuer_id: "HBC",
        ticker: "HBC",
        fiscal_year: 2022,
        fiscal_quarter: null,
        statement_scope: "CONSOLIDATED",
        doc_type: "AUDITED_ANNUAL_CONSOLIDATED_FS",
        cohort: "case_study",
        source_title: "BCTC hợp nhất 2022",
        source_url: "https://cafef1.mediacdn.vn/2022.pdf",
        pdf_sha256: "a".repeat(64),
        file_size_bytes: 1_000_000,
        total_pages: 60,
        digital_pages: 10,
        ocr_pages: 50,
        chunk_count: 100,
      },
      {
        filing_id: "HBC_FY2023_CONSOLIDATED_b0f2ff7892bc",
        issuer_id: "HBC",
        ticker: "HBC",
        fiscal_year: 2023,
        fiscal_quarter: null,
        statement_scope: "CONSOLIDATED",
        doc_type: "AUDITED_ANNUAL_CONSOLIDATED_FS",
        cohort: "case_study",
        source_title: "BCTC hợp nhất 2023",
        source_url: "https://cafef1.mediacdn.vn/2023.pdf",
        pdf_sha256: "b".repeat(64),
        file_size_bytes: 2_000_000,
        total_pages: 72,
        digital_pages: 12,
        ocr_pages: 60,
        chunk_count: 140,
      },
      {
        filing_id: "HBC_FY2023_CONSOLIDATED_c0f2ff7892bd",
        issuer_id: "HBC",
        ticker: "HBC",
        fiscal_year: 2023,
        fiscal_quarter: null,
        statement_scope: "CONSOLIDATED",
        doc_type: "AUDITED_ANNUAL_CONSOLIDATED_FS",
        cohort: "case_study",
        source_title: "BCTC hợp nhất 2023 (bổ sung)",
        source_url: "https://cafef1.mediacdn.vn/2023b.pdf",
        pdf_sha256: "c".repeat(64),
        file_size_bytes: 2_000_000,
        total_pages: 72,
        digital_pages: 12,
        ocr_pages: 60,
        chunk_count: 140,
      },
    ];
    expect(extractFiscalYears(docs)).toEqual([2023, 2022]);
  });

  it("tokenizes inline bracket citations into clickable [1][2] badges without leaking internal filing_ids", () => {
    const citations: CitationItem[] = [
      {
        filing_id: "HBC_FY2023_CONSOLIDATED_b0f2ff7892bc",
        document_title: "Báo cáo tài chính hợp nhất năm 2023 (đã kiểm toán)",
        fiscal_year: 2023,
        statement_scope: "CONSOLIDATED",
        page_no: 7,
        section_title: "Báo cáo kiểm toán độc lập",
        unit_scale: "VND",
        verbatim_excerpt: "Tập đoàn có khoản lỗ lũy kế là 3.240,3 tỷ VND.",
        vector_similarity: 0.762,
        rrf_score: 0.032,
      },
      {
        filing_id: "HBC_FY2023_CONSOLIDATED_b0f2ff7892bc",
        document_title: "Báo cáo tài chính hợp nhất năm 2023 (đã kiểm toán)",
        fiscal_year: 2023,
        statement_scope: "CONSOLIDATED",
        page_no: 9,
        section_title: "Bảng cân đối kế toán hợp nhất",
        unit_scale: "VND",
        verbatim_excerpt: "Phải thu ngắn hạn của khách hàng: 7.232.690.258.971.",
        vector_similarity: 0.792,
        rrf_score: 0.031,
      },
    ];

    const answer =
      "Lỗ lũy kế 3.240,3 tỷ VND [HBC_FY2023_CONSOLIDATED_b0f2ff7892bc, trang 7] và phải thu khách hàng [Báo cáo tài chính hợp nhất năm 2023, Trang 9].";
    const tokens = tokenizeAnswerWithCitations(answer, citations);
    const citationTokens = tokens.filter((t) => t.type === "citation");
    expect(citationTokens).toEqual([
      { type: "citation", citationIndex: 0, badgeNumber: 1, pageNo: 7 },
      { type: "citation", citationIndex: 1, badgeNumber: 2, pageNo: 9 },
    ]);
    const joinedText = tokens
      .filter((t) => t.type === "text")
      .map((t) => t.value)
      .join("");
    expect(joinedText).not.toContain("HBC_FY2023_CONSOLIDATED_b0f2ff7892bc");
  });

  it("sanitizes abstention reasons and formats latency and semantic similarity in vi-VN", () => {
    expect(
      sanitizeAbstentionReason(
        "Không tìm thấy dữ liệu trong các đoạn <CHUNK> được cung cấp.",
      ),
    ).toBe("Không tìm thấy dữ liệu trong các đoạn đoạn tài liệu được cung cấp.");
    expect(formatLatencyMs(1.8)).toBe("1,8 ms");
    expect(formatLatencyMs(3760.27)).toBe("3.760 ms");
    expect(formatSimilarityScore(0.762)).toBe("76,2%");
  });

  describe("RAG API Client methods", () => {
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("fetches company documents list and posts grounded investigation cleanly", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({
              ticker: "HBC",
              total_documents: 1,
              documents: [{ filing_id: "HBC_FY2023", fiscal_year: 2023 }],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        )
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({
              ticker: "HBC",
              question: "Ý kiến kiểm toán?",
              answer: "Có đoạn nhấn mạnh [Tài liệu, Trang 7].",
              abstained: false,
              abstention_reason: null,
              citations: [],
              retrieved_chunks_count: 1,
              top_vector_similarity: 0.76,
              cached: true,
              latency_ms: 1.8,
              model_used: "gemini-2.5-flash",
              disclaimer: "Tổng hợp trích dẫn từ BCTC gốc.",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      vi.stubGlobal("fetch", fetchMock);

      const docs = await apiClient.getCompanyDocuments("hbc");
      expect(docs.ticker).toBe("HBC");
      expect(docs.total_documents).toBe(1);
      expect(fetchMock).toHaveBeenNthCalledWith(
        1,
        "/api/v2/documents/HBC/list",
        expect.any(Object),
      );

      const res = await apiClient.postCompanyInvestigate("hbc", {
        question: "Ý kiến kiểm toán?",
        fiscal_year: 2023,
      });
      expect(res.cached).toBe(true);
      expect(fetchMock).toHaveBeenNthCalledWith(
        2,
        "/api/v2/documents/HBC/investigate",
        expect.objectContaining({ method: "POST" }),
      );
    });

    it("surfaces clean Vietnamese ApiError on HTTP 503 and HTTP 400 without internal leaks", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          new Response(
            JSON.stringify({
              detail:
                "Dịch vụ tra cứu Thuyết minh PDF hiện tạm thời chưa sẵn sàng. Vui lòng thử lại sau.",
            }),
            { status: 503, headers: { "Content-Type": "application/json" } },
          ),
        ),
      );

      await expect(
        apiClient.postCompanyInvestigate("HBC", { question: "Test question" }),
      ).rejects.toMatchObject({
        name: "ApiError",
        status: 503,
      } satisfies Partial<ApiError>);
    });
  });

  it("enforces Anti-AI-Slop rules and red-flag-to-PDF tab wiring across UI-08b source files", () => {
    const files = [
      "src/components/rag/DocumentInvestigationPanel.tsx",
      "src/components/financial/RedFlagChecklist.tsx",
      "src/app/companies/[ticker]/page.tsx",
    ];
    const forbiddenPatterns: RegExp[] = [
      /✨/,
      /AI-Powered/i,
      /backdrop-blur/,
      /bg-gradient-/,
      /#6B4C9A/i,
      /hover:scale-/,
      /\bREGIME_FALLBACK\b/,
      /\bBS_TOTAL_ASSETS\b/,
      /\.parquet\b/i,
    ];

    for (const rel of files) {
      const content = fs.readFileSync(
        path.join(WORKSPACE_FRONTEND_ROOT, rel),
        "utf8",
      );
      for (const pat of forbiddenPatterns) {
        expect(content, `${rel} matched forbidden ${pat}`).not.toMatch(pat);
      }
    }

    const checklistSrc = fs.readFileSync(
      path.join(
        WORKSPACE_FRONTEND_ROOT,
        "src/components/financial/RedFlagChecklist.tsx",
      ),
      "utf8",
    );
    expect(checklistSrc).toContain("Kiểm tra Thuyết minh gốc");
    expect(checklistSrc).toContain("onInvestigateRedFlag");

    const companyPageSrc = fs.readFileSync(
      path.join(
        WORKSPACE_FRONTEND_ROOT,
        "src/app/companies/[ticker]/page.tsx",
      ),
      "utf8",
    );
    expect(companyPageSrc).toContain("Điều tra Thuyết minh (PDF)");
    expect(companyPageSrc).toContain("DocumentInvestigationPanel");
    expect(companyPageSrc).toContain("handleInvestigateRedFlag");
  });
});
