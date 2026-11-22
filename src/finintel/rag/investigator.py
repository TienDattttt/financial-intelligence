"""Grounded Document AI Investigator with Bulkhead, Valkey L2 Cache, Injection Guard & Hard Citations."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import os
import re
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from finintel.rag._gemini import (
    GeminiClientBundle,
    create_verified_gemini_bundle,
)
from finintel.rag.retriever import HybridPgVectorRetriever, RetrievalHit

logger = logging.getLogger("finintel.rag.investigator")

DEFAULT_BULKHEAD_CONCURRENCY = 4
DEFAULT_MIN_VECTOR_SIMILARITY = 0.42
DEFAULT_VALKEY_RAG_URL = "redis://127.0.0.1:6379/0"
DEFAULT_RAG_CACHE_TTL_SECONDS = 86400
MAX_QUESTION_LENGTH = 600

ABSTENTION_FALLBACK_VN = (
    "Không tìm thấy căn cứ đủ độ tin cậy trong tài liệu PDF Báo cáo tài chính đã lưu "
    "cho doanh nghiệp và kỳ tài chính này."
)
FORENSIC_DISCLAIMER_VN = (
    "Tổng hợp trích dẫn từ Báo cáo tài chính kiểm toán hợp nhất gốc (PDF). "
    "Hệ thống không tự tính toán chỉ số tài chính bằng mô hình ngôn ngữ."
)

RED_FLAG_PREWARM_QUERIES: dict[str, str] = {
    "RF_03": (
        "Thuyết minh các khoản phải thu ngắn hạn của khách hàng, phải thu khó đòi, "
        "dự phòng phải thu ngắn hạn khó đòi và chênh lệch dòng tiền kinh doanh"
    ),
    "RF_07": (
        "Thuyết minh vay và nợ thuê tài chính ngắn hạn, dài hạn, nợ quá hạn, "
        "khả năng thanh toán và giả định hoạt động liên tục"
    ),
    "RF_09": (
        "Ý kiến của kiểm toán viên độc lập, vấn đề cần nhấn mạnh, cơ sở ý kiến ngoại trừ "
        "hoặc từ chối đưa ra ý kiến và giao dịch với các bên liên quan"
    ),
}

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE
    ),
    re.compile(
        r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE
    ),
    re.compile(
        r"(reveal|show|print|dump)\s+(your\s+)?(system\s+prompt|instructions?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"bỏ\s+qua\s+(toàn\s+bộ|mọi|tất\s+cả)\s+(hướng\s+dẫn|quy\s+tắc|chỉ\s+thị)",
        re.IGNORECASE,
    ),
    re.compile(
        r"hãy\s+(quên|xóa)\s+(các\s+)?(hướng\s+dẫn|quy\s+tắc)\s+trước", re.IGNORECASE
    ),
    re.compile(r"<\s*/?\s*(system|assistant|developer|instruction)\s*>", re.IGNORECASE),
    re.compile(r"jailbreak|dan\s+mode|override\s+safety", re.IGNORECASE),
)
_WHITESPACE_RE = re.compile(r"\s+")


class PromptInjectionError(ValueError):
    """Raised when a user question contains prompt-injection or instruction-override patterns."""


@dataclass(frozen=True, slots=True)
class CitationEvidence:
    """Verified citation pointing to an exact page and verbatim excerpt in the source PDF."""

    filing_id: str
    document_title: str
    fiscal_year: int
    statement_scope: str
    page_no: int
    section_title: str
    unit_scale: str
    verbatim_excerpt: str
    vector_similarity: float
    rrf_score: float


@dataclass(frozen=True, slots=True)
class InvestigationResult:
    """Structured citation-backed RAG investigation response."""

    ticker: str
    question: str
    answer: str
    abstained: bool
    abstention_reason: str | None
    citations: tuple[CitationEvidence, ...]
    retrieved_chunks_count: int
    top_vector_similarity: float
    cached: bool
    latency_ms: float
    model_used: str | None
    disclaimer: str = FORENSIC_DISCLAIMER_VN

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dictionary."""
        return {
            "ticker": self.ticker,
            "question": self.question,
            "answer": self.answer,
            "abstained": self.abstained,
            "abstention_reason": self.abstention_reason,
            "citations": [asdict(c) for c in self.citations],
            "retrieved_chunks_count": self.retrieved_chunks_count,
            "top_vector_similarity": self.top_vector_similarity,
            "cached": self.cached,
            "latency_ms": self.latency_ms,
            "model_used": self.model_used,
            "disclaimer": self.disclaimer,
        }


def sanitize_user_question(question: str) -> str:
    """Validate and sanitize a user investigation question against prompt injection."""
    if not isinstance(question, str):
        raise ValueError("Question must be a string.")
    cleaned = _WHITESPACE_RE.sub(" ", question).strip()
    if not cleaned:
        raise ValueError("Question must not be empty.")
    if len(cleaned) > MAX_QUESTION_LENGTH:
        cleaned = cleaned[:MAX_QUESTION_LENGTH].strip()
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            raise PromptInjectionError(
                "Câu hỏi chứa mẫu lệnh không hợp lệ (prompt injection) và đã bị từ chối."
            )
    return cleaned


def _normalize_for_match(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def verify_verbatim_excerpt(excerpt: str, chunk_text: str) -> tuple[bool, str]:
    """Verify that ``excerpt`` is genuinely grounded in ``chunk_text``."""
    clean_excerpt = _WHITESPACE_RE.sub(" ", excerpt).strip()
    if len(clean_excerpt) < 10:
        return False, ""

    norm_chunk = _normalize_for_match(chunk_text)
    norm_excerpt = _normalize_for_match(clean_excerpt)
    if norm_excerpt in norm_chunk:
        return True, clean_excerpt

    # Fallback: check word-level containment (handles minor markdown pipe/punctuation diffs).
    excerpt_words = [
        w for w in re.findall(r"[0-9A-Za-zÀ-ỹ]+", norm_excerpt) if len(w) >= 2
    ]
    if not excerpt_words:
        return False, ""
    chunk_word_set = set(re.findall(r"[0-9A-Za-zÀ-ỹ]+", norm_chunk))
    matched = sum(1 for w in excerpt_words if w in chunk_word_set)
    overlap_ratio = matched / len(excerpt_words)
    if overlap_ratio >= 0.78:
        return True, clean_excerpt
    return False, ""


def build_rag_cache_key(
    *,
    ticker: str,
    question: str,
    statement_scope: str | None = None,
    fiscal_year: int | None = None,
    top_k: int = 6,
) -> str:
    """Build deterministic Valkey L2 cache key for a RAG investigation request."""
    norm_q = _normalize_for_match(question)
    q_hash = hashlib.sha256(norm_q.encode("utf-8")).hexdigest()[:20]
    scope_part = (statement_scope or "ALL").upper()
    year_part = str(fiscal_year) if fiscal_year is not None else "ALL"
    return f"finintel:rag:v1:{ticker.strip().upper()}:{scope_part}:{year_part}:k{top_k}:{q_hash}"


class _RagValkeyCache:
    """Fail-open Valkey L2 cache with lazy ``redis`` import (M-03)."""

    def __init__(
        self,
        url: str | None = None,
        *,
        ttl_seconds: int = DEFAULT_RAG_CACHE_TTL_SECONDS,
    ) -> None:
        resolved_url = (
            url
            if url is not None
            else (
                os.getenv("FININTEL_VALKEY_URL")
                or os.getenv("FININTEL_REDIS_URL")
                or DEFAULT_VALKEY_RAG_URL
            )
        )
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._client: Any | None = None
        if resolved_url and resolved_url.strip():
            try:
                redis_mod: Any = importlib.import_module("redis")  # Lazy import (M-03)

                self._client = redis_mod.Redis.from_url(
                    resolved_url.strip(),
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
            except Exception as exc:
                logger.warning("RAG Valkey cache disabled: %s", type(exc).__name__)
                self._client = None

    def get(self, key: str) -> dict[str, Any] | None:
        if self._client is None:
            return None
        try:
            raw = self._client.get(key)
            if not raw:
                return None
            parsed = json.loads(str(raw))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def set(self, key: str, payload: dict[str, Any]) -> None:
        if self._client is None:
            return
        try:
            self._client.setex(
                key,
                self._ttl_seconds,
                json.dumps(payload, ensure_ascii=False),
            )
        except Exception:
            pass


_INVESTIGATOR_SYSTEM_PROMPT = (
    "Bạn là Chuyên viên Kiểm toán Pháp y & Phân tích Thuyết minh BCTC Việt Nam (FinIntel).\n"
    "QUY TẮC BẮT BUỘC (VI PHẠM = THẤT BẠI NGHIÊM TRỌNG):\n"
    "1. CHỈ sử dụng thông tin có mặt TRỰC TIẾP trong các đoạn <CHUNK> được cung cấp. "
    "TUYỆT ĐỐI KHÔNG dùng kiến thức bên ngoài, KHÔNG suy đoán, KHÔNG bịa số.\n"
    "2. KHÔNG tự tính toán các chỉ số tài chính (như ROE, ROA, P/E, biên lợi nhuận) nếu số đó không ghi trực tiếp trong tài liệu.\n"
    "3. NẾU câu hỏi hỏi về năm tài chính, sự kiện, cá nhân, hợp đồng hoặc chỉ tiêu KHÔNG có trong các đoạn <CHUNK> "
    "được cung cấp -> BẮT BUỘC đặt abstained = true, answer = lời từ chối trung thực nêu rõ không tìm thấy căn cứ trong tài liệu PDF đã lưu, citations = [].\n"
    "4. Nếu tìm thấy căn cứ -> abstained = false. MỌI nhận định và con số trong answer BẮT BUỘC kèm trích dẫn định dạng "
    "[Tài liệu, Trang X] (khớp chính xác page_no của chunk).\n"
    "5. Mỗi phần tử trong mảng citations PHẢI sao chép NGUYÊN VĂN (verbatim_excerpt, 30-240 ký tự) một đoạn có thật từ chunk tương ứng.\n"
    "6. Trả về DUY NHẤT một đối tượng JSON hợp lệ theo schema:\n"
    "{\n"
    '  "abstained": boolean,\n'
    '  "abstention_reason": string | null,\n'
    '  "answer": string,\n'
    '  "citations": [\n'
    "    {\n"
    '      "chunk_id": integer,\n'
    '      "page_no": integer,\n'
    '      "verbatim_excerpt": string\n'
    "    }\n"
    "  ]\n"
    "}"
)


def _build_grounded_user_prompt(
    *,
    ticker: str,
    question: str,
    hits: Sequence[RetrievalHit],
) -> str:
    chunk_blocks: list[str] = []
    for idx, hit in enumerate(hits, start=1):
        c = hit.chunk
        chunk_blocks.append(
            f'<CHUNK id="{idx}" filing_id="{c.filing_id}" title="{hit.source_title}" '
            f'fiscal_year="{c.fiscal_year}" scope="{c.statement_scope}" '
            f'page_no="{c.page_no}" section="{c.section_title}" unit="{c.unit_scale}">\n'
            f"{c.text_content}\n"
            f"</CHUNK>"
        )
    joined_chunks = "\n\n".join(chunk_blocks)
    return (
        f"Doanh nghiệp đang điều tra: {ticker}\n"
        f"Câu hỏi của chuyên viên phân tích: {question}\n\n"
        f"Các đoạn trích xuất từ PDF BCTC Kiểm toán gốc:\n{joined_chunks}\n\n"
        "Hãy trả lời bằng JSON theo đúng schema đã quy định."
    )


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON is not an object.")
    return parsed


class GroundedDocumentInvestigator:
    """Citation-backed PDF RAG investigator with dedicated Bulkhead semaphore and Valkey L2 cache."""

    def __init__(
        self,
        retriever: HybridPgVectorRetriever,
        *,
        gemini_bundle: GeminiClientBundle | None = None,
        valkey_url: str | None = None,
        cache_ttl_seconds: int = DEFAULT_RAG_CACHE_TTL_SECONDS,
        max_concurrency: int = DEFAULT_BULKHEAD_CONCURRENCY,
        min_vector_similarity: float = DEFAULT_MIN_VECTOR_SIMILARITY,
    ) -> None:
        self.retriever = retriever
        self._gemini_bundle = gemini_bundle
        self._cache = _RagValkeyCache(valkey_url, ttl_seconds=cache_ttl_seconds)
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self.min_vector_similarity = float(min_vector_similarity)

    def _get_bundle(self) -> GeminiClientBundle:
        if self._gemini_bundle is None:
            self._gemini_bundle = create_verified_gemini_bundle()
        return self._gemini_bundle

    def _call_llm_sync(self, prompt: str) -> tuple[dict[str, Any], str]:
        types: Any = importlib.import_module("google.genai.types")  # Lazy import (M-03)

        from finintel.rag._gemini import FALLBACK_GEMINI_MODELS

        bundle = self._get_bundle()
        candidate_models = tuple(dict.fromkeys((bundle.model, *FALLBACK_GEMINI_MODELS)))
        last_exc: Exception | None = None
        for model_name in candidate_models:
            for attempt in range(3):
                try:
                    response = bundle.client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=_INVESTIGATOR_SYSTEM_PROMPT,
                            temperature=0.0,
                            response_mime_type="application/json",
                        ),
                    )
                    raw_text = str(getattr(response, "text", "") or "").strip()
                    return _extract_json_object(raw_text), model_name
                except Exception as exc:
                    last_exc = exc
                    err_text = str(exc)
                    if "NOT_FOUND" in err_text or "404" in err_text:
                        break
                    if (
                        "503" in err_text
                        or "UNAVAILABLE" in err_text
                        or "429" in err_text
                        or "RESOURCE_EXHAUSTED" in err_text
                    ):
                        logger.warning(
                            "Gemini model=%s attempt %d/3 failed (%s); backing off.",
                            model_name,
                            attempt + 1,
                            type(exc).__name__,
                        )
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(
            f"Grounded synthesis failed: {type(last_exc).__name__}: {last_exc}"
        )

    def _reconcile_citations(
        self,
        raw_citations: Any,
        hits: Sequence[RetrievalHit],
    ) -> list[CitationEvidence]:
        if not isinstance(raw_citations, list):
            return []
        verified: list[CitationEvidence] = []
        seen_keys: set[tuple[str, int, str]] = set()
        for item in raw_citations:
            if not isinstance(item, dict):
                continue
            chunk_idx_raw = item.get("chunk_id")
            page_no_raw = item.get("page_no")
            excerpt_raw = str(item.get("verbatim_excerpt") or "").strip()

            matched_hit: RetrievalHit | None = None
            if isinstance(chunk_idx_raw, int) and 1 <= chunk_idx_raw <= len(hits):
                matched_hit = hits[chunk_idx_raw - 1]
            elif isinstance(page_no_raw, int):
                for h in hits:
                    if h.chunk.page_no == page_no_raw:
                        matched_hit = h
                        break
            if matched_hit is None:
                continue

            is_grounded, clean_excerpt = verify_verbatim_excerpt(
                excerpt_raw, matched_hit.chunk.text_content
            )
            if not is_grounded:
                continue

            dedup_key = (
                matched_hit.chunk.filing_id,
                matched_hit.chunk.page_no,
                clean_excerpt[:80].lower(),
            )
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            verified.append(
                CitationEvidence(
                    filing_id=matched_hit.chunk.filing_id,
                    document_title=matched_hit.source_title,
                    fiscal_year=matched_hit.chunk.fiscal_year,
                    statement_scope=matched_hit.chunk.statement_scope,
                    page_no=matched_hit.chunk.page_no,
                    section_title=matched_hit.chunk.section_title,
                    unit_scale=matched_hit.chunk.unit_scale,
                    verbatim_excerpt=clean_excerpt,
                    vector_similarity=matched_hit.vector_similarity,
                    rrf_score=matched_hit.rrf_score,
                )
            )
        return verified

    async def investigate(
        self,
        *,
        ticker: str,
        question: str,
        statement_scope: str | None = "CONSOLIDATED",
        fiscal_year: int | None = None,
        top_k: int = 6,
        use_cache: bool = True,
    ) -> InvestigationResult:
        """Run a bulkhead-isolated, citation-backed investigation on curated PDFs."""
        started = time.perf_counter()
        clean_ticker = ticker.strip().upper()
        clean_question = sanitize_user_question(question)

        cache_key = build_rag_cache_key(
            ticker=clean_ticker,
            question=clean_question,
            statement_scope=statement_scope,
            fiscal_year=fiscal_year,
            top_k=top_k,
        )
        if use_cache:
            cached_payload = self._cache.get(cache_key)
            if cached_payload is not None:
                elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
                citations = tuple(
                    CitationEvidence(**c) for c in cached_payload.get("citations", [])
                )
                return InvestigationResult(
                    ticker=str(cached_payload["ticker"]),
                    question=str(cached_payload["question"]),
                    answer=str(cached_payload["answer"]),
                    abstained=bool(cached_payload["abstained"]),
                    abstention_reason=cached_payload.get("abstention_reason"),
                    citations=citations,
                    retrieved_chunks_count=int(
                        cached_payload.get("retrieved_chunks_count", 0)
                    ),
                    top_vector_similarity=float(
                        cached_payload.get("top_vector_similarity", 0.0)
                    ),
                    cached=True,
                    latency_ms=elapsed_ms,
                    model_used=cached_payload.get("model_used"),
                )

        async with self._semaphore:
            hits = await asyncio.to_thread(
                self.retriever.retrieve_hits,
                issuer_id=clean_ticker,
                query=clean_question,
                statement_scope=statement_scope,
                fiscal_year=fiscal_year,
                limit=top_k,
            )

            top_vec_sim = max((h.vector_similarity for h in hits), default=0.0)
            has_fts_hit = any(h.fts_rank > 0.0 for h in hits)

            if not hits or (
                top_vec_sim < self.min_vector_similarity and not has_fts_hit
            ):
                elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
                reason = (
                    "Không có tài liệu PDF nào được nạp cho mã/kỳ này."
                    if not hits
                    else (
                        f"Độ tương đồng truy xuất tối đa ({top_vec_sim:.3f}) "
                        f"thấp hơn ngưỡng an toàn ({self.min_vector_similarity:.2f})."
                    )
                )
                result = InvestigationResult(
                    ticker=clean_ticker,
                    question=clean_question,
                    answer=ABSTENTION_FALLBACK_VN,
                    abstained=True,
                    abstention_reason=reason,
                    citations=(),
                    retrieved_chunks_count=len(hits),
                    top_vector_similarity=round(top_vec_sim, 4),
                    cached=False,
                    latency_ms=elapsed_ms,
                    model_used=None,
                )
                if use_cache:
                    self._cache.set(cache_key, result.to_dict())
                return result

            prompt = _build_grounded_user_prompt(
                ticker=clean_ticker,
                question=clean_question,
                hits=hits,
            )
            llm_payload, model_used = await asyncio.to_thread(
                self._call_llm_sync, prompt
            )

        abstained = bool(llm_payload.get("abstained", False))
        abstention_reason = (
            str(llm_payload["abstention_reason"]).strip()
            if llm_payload.get("abstention_reason")
            else None
        )
        answer = str(llm_payload.get("answer") or "").strip()
        verified_citations = self._reconcile_citations(
            llm_payload.get("citations"), hits
        )

        # Hard Citation Law: refuse if the model claimed an answer without any verifiable verbatim excerpt.
        if not abstained and not verified_citations:
            abstained = True
            abstention_reason = "Câu trả lời không vượt qua kiểm chứng trích dẫn nguyên văn (verbatim grounding check)."
            answer = ABSTENTION_FALLBACK_VN

        if abstained and not answer:
            answer = ABSTENTION_FALLBACK_VN

        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
        result = InvestigationResult(
            ticker=clean_ticker,
            question=clean_question,
            answer=answer,
            abstained=abstained,
            abstention_reason=abstention_reason,
            citations=tuple(verified_citations),
            retrieved_chunks_count=len(hits),
            top_vector_similarity=round(top_vec_sim, 4),
            cached=False,
            latency_ms=elapsed_ms,
            model_used=model_used,
        )
        if use_cache:
            self._cache.set(cache_key, result.to_dict())
        return result

    async def prewarm_red_flag_queries(
        self,
        tickers: Sequence[str],
        *,
        flag_codes: Sequence[str] = ("RF_03", "RF_07", "RF_09"),
    ) -> dict[str, dict[str, bool]]:
        """Pre-warm Valkey L2 cache for standard forensic red-flag questions."""
        summary: dict[str, dict[str, bool]] = {}
        for ticker in tickers:
            clean = ticker.strip().upper()
            summary[clean] = {}
            for code in flag_codes:
                query = RED_FLAG_PREWARM_QUERIES.get(code)
                if not query:
                    continue
                res = await self.investigate(
                    ticker=clean,
                    question=query,
                    statement_scope="CONSOLIDATED",
                    use_cache=True,
                )
                summary[clean][code] = not res.abstained
        return summary


__all__ = [
    "ABSTENTION_FALLBACK_VN",
    "CitationEvidence",
    "DEFAULT_BULKHEAD_CONCURRENCY",
    "DEFAULT_MIN_VECTOR_SIMILARITY",
    "DEFAULT_RAG_CACHE_TTL_SECONDS",
    "DEFAULT_VALKEY_RAG_URL",
    "FORENSIC_DISCLAIMER_VN",
    "GroundedDocumentInvestigator",
    "InvestigationResult",
    "PromptInjectionError",
    "RED_FLAG_PREWARM_QUERIES",
    "build_rag_cache_key",
    "sanitize_user_question",
    "verify_verbatim_excerpt",
]
