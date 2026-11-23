"""Hybrid Vietnamese BCTC PDF parser (PyMuPDF digital + 200 DPI Gemini Vision OCR + disk cache)."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from finintel.rag._gemini import (
    GeminiClientBundle,
    create_verified_gemini_bundle,
)
from finintel.rag.chunker import ChunkInputPage
from finintel.rag.pdf_catalog import CuratedPdfRecord

logger = logging.getLogger("finintel.rag.parser")

ExtractionMethod = Literal["digital", "vision_ocr", "local_gpu_ocr", "empty"]
OcrBackend = Literal["auto", "gemini", "easyocr"]

DEFAULT_OCR_DPI = 200
DEFAULT_MIN_DIGITAL_ALNUM_CHARS = 100
DEFAULT_OCR_CONCURRENCY = 8
DEFAULT_OCR_BATCH_SIZE = 5
DEFAULT_OCR_MAX_RETRIES = 4
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PARSED_CACHE_DIR = _REPO_ROOT / "data" / "curated_pdfs" / "parsed_cache"

_LEGACY_FONT_MARKERS: tuple[str, ...] = (
    "vni-",
    "vni_",
    ".vn",
    "vntime",
    "tcvn",
    "vnarial",
    "vnhelve",
    "abc",
)
_CID_GARBAGE_RE = re.compile(r"\(cid:\d+\)")
_MOJIBAKE_CHAR_RE = re.compile(
    r"[¨«®¸µ¶·¹¾»¼½ÆÇÈÉÊÌÐÑÒÓÔÕÖØÙÚÛÜÝÞßãäåæçèéêëìíîïðñòóôõöøùúûüýþ]"
)
_VN_UNICODE_DIACRITICS_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]"
)
_WHITESPACE_RE = re.compile(r"\s+")

_UNIT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"đơn\s*vị\s*tính\s*[:\-]?\s*(tỷ\s*đồng|ty\s*dong)", re.IGNORECASE),
        "Tỷ đồng",
    ),
    (
        re.compile(
            r"đơn\s*vị\s*tính\s*[:\-]?\s*(triệu\s*đồng|trieu\s*dong|triệu\s*vnđ|triệu\s*vnd)",
            re.IGNORECASE,
        ),
        "Triệu đồng",
    ),
    (
        re.compile(
            r"đơn\s*vị\s*tính\s*[:\-]?\s*(nghìn\s*đồng|nghin\s*đồng|ngàn\s*đồng)",
            re.IGNORECASE,
        ),
        "Nghìn đồng",
    ),
    (re.compile(r"đơn\s*vị\s*tính\s*[:\-]?\s*(usd|đô\s*la)", re.IGNORECASE), "USD"),
    (re.compile(r"đơn\s*vị\s*tính\s*[:\-]?\s*(vnđ|vnd|đồng)", re.IGNORECASE), "VND"),
    (
        re.compile(r"đvt\s*[:\-]?\s*(triệu\s*đồng|trieu\s*dong)", re.IGNORECASE),
        "Triệu đồng",
    ),
    (re.compile(r"đvt\s*[:\-]?\s*(vnđ|vnd|đồng)", re.IGNORECASE), "VND"),
)

_NOTES_SECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:bản\s*)?thuyết\s*minh\s*báo\s*cáo\s*tài\s*chính(?:\s*hợp\s*nhất)?",
        re.IGNORECASE,
    ),
    re.compile(r"mẫu\s*số\s*b\s*09\b", re.IGNORECASE),
)
_SECTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"báo\s*cáo\s*kiểm\s*toán\s*độc\s*lập", re.IGNORECASE),
        "Báo cáo kiểm toán độc lập",
    ),
    (
        re.compile(r"bảng\s*cân\s*đối\s*kế\s*toán\s*hợp\s*nhất", re.IGNORECASE),
        "Bảng cân đối kế toán hợp nhất",
    ),
    (
        re.compile(
            r"báo\s*cáo\s*kết\s*quả\s*hoạt\s*động\s*kinh\s*doanh\s*hợp\s*nhất",
            re.IGNORECASE,
        ),
        "Báo cáo kết quả hoạt động kinh doanh hợp nhất",
    ),
    (
        re.compile(r"báo\s*cáo\s*lưu\s*chuyển\s*tiền\s*tệ\s*hợp\s*nhất", re.IGNORECASE),
        "Báo cáo lưu chuyển tiền tệ hợp nhất",
    ),
    (
        re.compile(
            r"thuyết\s*minh\s*báo\s*cáo\s*tài\s*chính(?:\s*hợp\s*nhất)?", re.IGNORECASE
        ),
        "Thuyết minh Báo cáo tài chính hợp nhất",
    ),
    (
        re.compile(
            r"báo\s*cáo\s*của\s*(ban\s*tổng\s*giám\s*đốc|ban\s*giám\s*đốc|hội\s*đồng\s*quản\s*trị)",
            re.IGNORECASE,
        ),
        "Báo cáo của Ban Giám đốc / HĐQT",
    ),
)
_NOTE_HEADING_RE = re.compile(
    r"^\s*(?:thuyết\s*minh\s*)?((?:[IVX]{1,4}\.\d{1,2}|\d{1,2}(?:\.\d{1,2})?))\s*[\.\-\)]\s*([A-ZÀ-Ỹ][^\n]{4,95})$",
    re.MULTILINE,
)
_PRIMARY_STATEMENT_CONTINUATION_CUES: dict[
    str, tuple[re.Pattern[str], re.Pattern[str]]
] = {
    "Bảng cân đối kế toán hợp nhất": (
        re.compile(
            r"(?:nguồn\s*vốn|tài\s*sản\s*ngắn\s*hạn|tài\s*sản\s*dài\s*hạn|tổng\s*cộng\s*nguồn\s*vốn|nợ\s*phải\s*trả|vốn\s*chủ\s*sở\s*hữu|\|\s*(?:270|300|400|440)\s*\|)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:doanh\s*thu\s*bán\s*hàng|doanh\s*thu\s*cung\s*cấp\s*dịch\s*vụ|thuyết\s*minh|chính\s*sách\s*kế\s*toán|mẫu\s*số\s*b\s*09)",
            re.IGNORECASE,
        ),
    ),
    "Báo cáo kết quả hoạt động kinh doanh hợp nhất": (
        re.compile(
            r"(?:doanh\s*thu\s*thuần|lợi\s*nhuận\s*gộp|lợi\s*nhuận\s*sau\s*thuế|lãi\s*cơ\s*bản\s*trên\s*cổ\s*phiếu)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:thuyết\s*minh|chính\s*sách\s*kế\s*toán|mẫu\s*số\s*b\s*09|chi\s*tiết\s*doanh\s*thu)",
            re.IGNORECASE,
        ),
    ),
    "Báo cáo lưu chuyển tiền tệ hợp nhất": (
        re.compile(
            r"(?:lưu\s*chuyển\s*tiền|hoạt\s*động\s*kinh\s*doanh|hoạt\s*động\s*đầu\s*tư|hoạt\s*động\s*tài\s*chính|tiền\s*và\s*tương\s*đương\s*tiền\s*cuối\s*kỳ)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:thuyết\s*minh|chính\s*sách\s*kế\s*toán|mẫu\s*số\s*b\s*09)",
            re.IGNORECASE,
        ),
    ),
    "Báo cáo kiểm toán độc lập": (
        re.compile(
            r"(?:ý\s*kiến\s*(?:của\s*)?kiểm\s*toán|trách\s*nhiệm\s*của|vấn\s*đề\s*cần\s*nhấn\s*mạnh|cơ\s*sở\s*của\s*ý\s*kiến|kiểm\s*toán\s*viên)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:thuyết\s*minh\s*báo\s*cáo\s*tài\s*chính|chính\s*sách\s*kế\s*toán|mẫu\s*số\s*b\s*09)",
            re.IGNORECASE,
        ),
    ),
}

_VISION_OCR_PROMPT = (
    "Bạn là chuyên gia số hóa Báo cáo tài chính kiểm toán Việt Nam (chuẩn VAS TT200/TT99).\n"
    "Hãy chuyển đổi CHÍNH XÁC toàn bộ nội dung trang ảnh này sang Markdown tiếng Việt có dấu chuẩn Unicode:\n"
    "1. Giữ NGUYÊN mọi tiêu đề mục, số thuyết minh, ý kiến kiểm toán, chữ ký/tên công ty kiểm toán nếu có.\n"
    "2. Với mọi bảng số liệu: xuất bảng Markdown chuẩn (| ... |) bảo toàn ĐẦY ĐỦ tiêu đề cột "
    "(ví dụ: Chỉ tiêu | Mã số | Thuyết minh | Số cuối năm | Số đầu năm hoặc Năm nay | Năm trước), "
    "đơn vị tính (VND / Triệu đồng) và nguyên dạng dấu ngoặc đơn cho số âm.\n"
    "3. TUYỆT ĐỐI KHÔNG tóm tắt, KHÔNG bỏ sót dòng, KHÔNG tự tính toán hay suy diễn số liệu.\n"
    "4. Chỉ xuất nội dung Markdown trích xuất, không thêm lời bình luận."
)


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """Single parsed page from a curated PDF document."""

    page_no: int
    text: str
    extraction_method: ExtractionMethod
    char_count: int
    has_tables: bool
    detected_unit_scale: str
    detected_section: str
    ocr_reason: str | None = None

    def to_chunk_input(self) -> ChunkInputPage:
        """Convert to ``ChunkInputPage`` for the VAS-safe chunker."""
        return ChunkInputPage(
            page_no=self.page_no,
            text=self.text,
            section_title=self.detected_section,
            unit_scale=self.detected_unit_scale,
        )


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Full parsed PDF document with per-page provenance and extraction counts."""

    filing_id: str
    issuer_id: str
    ticker: str
    fiscal_year: int
    fiscal_quarter: int | None
    statement_scope: str
    doc_type: str
    pdf_sha256: str
    source_title: str
    total_pages: int
    digital_pages: int
    ocr_pages: int
    empty_pages: int
    pages: tuple[ParsedPage, ...]


def build_filing_id(record: CuratedPdfRecord) -> str:
    """Construct a deterministic filing_id for a curated PDF record."""
    period = (
        f"FY{record.fiscal_year}"
        if record.fiscal_quarter is None
        else f"FY{record.fiscal_year}Q{record.fiscal_quarter}"
    )
    return f"{record.issuer_id}_{period}_{record.statement_scope}_{record.content_sha256[:12]}"


def detect_unit_scale(text: str, default: str = "VND") -> str:
    """Detect Vietnamese financial statement currency unit scale from page text."""
    for pattern, label in _UNIT_PATTERNS:
        if pattern.search(text):
            return label
    return default


def _strip_markdown_line_markers(line: str) -> str:
    stripped = line.strip()
    stripped = re.sub(r"^[#>*\s]+", "", stripped)
    stripped = re.sub(r"[*\s]+$", "", stripped)
    return stripped.strip()


def detect_section_title(
    text: str, previous_section: str = "Báo cáo tài chính hợp nhất"
) -> str:
    """Detect VAS financial report section or footnote heading from page text."""
    head = text[:1800]
    cleaned_lines = [
        _strip_markdown_line_markers(raw)
        for raw in head.splitlines()[:25]
        if _strip_markdown_line_markers(raw)
    ]

    # 1. Explicit footnote / notes header takes priority over incidental prose references.
    top_slice = "\n".join(cleaned_lines[:10])
    for notes_pat in _NOTES_SECTION_PATTERNS:
        if notes_pat.search(top_slice):
            return "Thuyết minh Báo cáo tài chính hợp nhất"

    # 2. Match explicit section headings on standalone heading lines (avoid mid-sentence prose).
    for line in cleaned_lines[:12]:
        if len(line) > 110:
            continue
        for pattern, label in _SECTION_PATTERNS:
            if pattern.search(line):
                return label

    # 3. Match numbered / Roman-numeral footnote subheadings (including Markdown headings).
    normalized_head = "\n".join(cleaned_lines)
    note_match = _NOTE_HEADING_RE.search(normalized_head)
    if note_match:
        note_num = note_match.group(1).strip()
        note_title = " ".join(note_match.group(2).strip().split())
        if (
            len(note_title) >= 5
            and not note_title.replace(".", "").replace(",", "").isdigit()
        ):
            return f"Thuyết minh {note_num}. {note_title[:90]}"

    # 4. Continuation guard: short primary statements must not bleed onto footnote/note pages.
    cues = _PRIMARY_STATEMENT_CONTINUATION_CUES.get(previous_section)
    if cues is not None:
        positive_cue, negative_cue = cues
        if negative_cue.search(head) or not positive_cue.search(text):
            return "Thuyết minh Báo cáo tài chính hợp nhất"

    return previous_section


def has_legacy_vietnamese_encoding(
    text: str,
    font_names: tuple[str, ...] = (),
) -> tuple[bool, str | None]:
    """Detect VNI/TCVN3 legacy fonts or broken character encoding on a PDF page."""
    lowered_fonts = [name.lower() for name in font_names]
    for font in lowered_fonts:
        if any(marker in font for marker in _LEGACY_FONT_MARKERS):
            return True, f"legacy_font:{font}"

    if not text.strip():
        return False, None

    if text.count("\ufffd") >= 5:
        return True, "unicode_replacement_chars"

    cid_hits = len(_CID_GARBAGE_RE.findall(text))
    if cid_hits >= 5:
        return True, f"cid_garbage:{cid_hits}"

    mojibake_hits = len(_MOJIBAKE_CHAR_RE.findall(text))
    vn_unicode_hits = len(_VN_UNICODE_DIACRITICS_RE.findall(text))
    if mojibake_hits >= 8 and vn_unicode_hits <= 3:
        return True, f"vni_tcvn3_mojibake:{mojibake_hits}"

    return False, None


def needs_vision_ocr(
    text: str,
    *,
    font_names: tuple[str, ...] = (),
    image_count: int = 0,
    min_alnum_chars: int = DEFAULT_MIN_DIGITAL_ALNUM_CHARS,
) -> tuple[bool, str | None]:
    """Classify whether a PDF page requires 200 DPI Vision OCR instead of digital text extraction."""
    legacy_bad, legacy_reason = has_legacy_vietnamese_encoding(text, font_names)
    if legacy_bad:
        return True, legacy_reason

    alnum_count = sum(1 for ch in text if ch.isalnum())
    if alnum_count < min_alnum_chars and image_count > 0:
        return True, f"scanned_low_text:{alnum_count}_chars_{image_count}_imgs"

    return False, None


def _table_rows_to_markdown(rows: list[list[str | None]]) -> str:
    cleaned_rows: list[list[str]] = []
    for row in rows:
        cells = [
            _WHITESPACE_RE.sub(" ", (cell or "")).strip().replace("|", "/")
            for cell in row
        ]
        if any(cells):
            cleaned_rows.append(cells)
    if not cleaned_rows:
        return ""
    max_cols = max(len(r) for r in cleaned_rows)
    padded = [r + [""] * (max_cols - len(r)) for r in cleaned_rows]
    header = "| " + " | ".join(padded[0]) + " |"
    sep = "| " + " | ".join(["---"] * max_cols) + " |"
    body = ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join([header, sep, *body])


def _extract_digital_page_text(page: Any) -> tuple[str, bool]:
    """Extract clean text and Markdown tables from a digital PyMuPDF page."""
    raw_text = str(page.get_text("text") or "").strip()
    table_markdowns: list[str] = []

    try:
        finder = page.find_tables()
        tables = list(getattr(finder, "tables", []) or [])
        for table in tables:
            extracted = table.extract()
            if extracted and len(extracted) >= 2:
                md = _table_rows_to_markdown(extracted)
                if md and len(md) >= 30:
                    table_markdowns.append(md)
    except Exception:
        # Table heuristic is best-effort; raw_text preserves full content.
        table_markdowns = []

    if not table_markdowns:
        return raw_text, False

    # Keep raw_text for headings/footnotes and append structured Markdown tables if they add structure.
    combined_tables = "\n\n".join(table_markdowns)
    if len(raw_text) <= len(combined_tables) * 1.4:
        # Extract non-table header/footer lines (top/bottom text) + structured tables.
        non_empty_lines = [
            line.strip() for line in raw_text.splitlines() if line.strip()
        ]
        header_lines = non_empty_lines[:6]
        prefix = "\n".join(header_lines)
        return f"{prefix}\n\n{combined_tables}".strip(), True
    return f"{raw_text}\n\n{combined_tables}".strip(), True


def _cache_page_path(cache_dir: Path, pdf_sha256: str, page_no: int) -> Path:
    return cache_dir / pdf_sha256[:2] / pdf_sha256 / f"page_{page_no:04d}.json"


def _load_cached_page(
    cache_dir: Path, pdf_sha256: str, page_no: int
) -> ParsedPage | None:
    path = _cache_page_path(cache_dir, pdf_sha256, page_no)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ParsedPage(
            page_no=int(payload["page_no"]),
            text=str(payload["text"]),
            extraction_method=payload["extraction_method"],
            char_count=int(payload["char_count"]),
            has_tables=bool(payload["has_tables"]),
            detected_unit_scale=str(payload["detected_unit_scale"]),
            detected_section=str(payload["detected_section"]),
            ocr_reason=payload.get("ocr_reason"),
        )
    except Exception:
        return None


def _save_cached_page(cache_dir: Path, pdf_sha256: str, page: ParsedPage) -> None:
    path = _cache_page_path(cache_dir, pdf_sha256, page.page_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(asdict(page), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _render_page_png_bytes(page: Any, dpi: int = DEFAULT_OCR_DPI) -> bytes:
    pymupdf: Any = importlib.import_module("pymupdf")  # Lazy import (M-03)

    scale = dpi / 72.0
    matrix = pymupdf.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return bytes(pix.tobytes("png"))


def _call_gemini_vision_ocr_sync(
    bundle: GeminiClientBundle,
    png_bytes: bytes,
    *,
    max_retries: int = DEFAULT_OCR_MAX_RETRIES,
) -> str:
    types: Any = importlib.import_module("google.genai.types")  # Lazy import (M-03)

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = bundle.client.models.generate_content(
                model=bundle.model,
                contents=[
                    types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                    _VISION_OCR_PROMPT,
                ],
                config=types.GenerateContentConfig(temperature=0.0),
            )
            return str(getattr(response, "text", "") or "").strip()
        except Exception as exc:
            last_exc = exc
            wait_s = min(30.0, (2.0**attempt) * 1.5)
            logger.warning(
                "Vision OCR attempt %d/%d failed (%s); retrying in %.1fs.",
                attempt + 1,
                max_retries,
                type(exc).__name__,
                wait_s,
            )
            time.sleep(wait_s)
    raise RuntimeError(
        f"Vision OCR failed after {max_retries} attempts: {type(last_exc).__name__}: {last_exc}"
    )


_BATCH_VISION_OCR_PROMPT = (
    "Bạn là chuyên gia số hóa Báo cáo tài chính kiểm toán Việt Nam (chuẩn VAS TT200/TT99).\n"
    "Bạn nhận được nhiều trang ảnh PDF, mỗi ảnh có nhãn `page_no=X` ngay phía trước.\n"
    "Hãy chuyển đổi CHÍNH XÁC từng trang sang Markdown tiếng Việt có dấu chuẩn Unicode:\n"
    "1. Giữ NGUYÊN mọi tiêu đề mục, số thuyết minh, ý kiến kiểm toán, tên công ty kiểm toán.\n"
    "2. Với mọi bảng số liệu: xuất bảng Markdown chuẩn (| ... |) bảo toàn ĐẦY ĐỦ tiêu đề cột "
    "(Số cuối năm / Số đầu năm / Năm nay / Năm trước / Mã số / Thuyết minh), đơn vị tính (VND / Triệu đồng) "
    "và dấu ngoặc đơn cho số âm.\n"
    "3. TUYỆT ĐỐI KHÔNG tóm tắt, KHÔNG bỏ sót dòng, KHÔNG tự tính toán hay bịa số.\n"
    "4. Trả về DUY NHẤT JSON hợp lệ có cấu trúc:\n"
    '{"pages": [{"page_no": 1, "markdown": "..."}, ...]}'
)


def _call_gemini_vision_ocr_batch_sync(
    bundle: GeminiClientBundle,
    batch: list[tuple[int, bytes, str | None]],
    *,
    max_retries: int = DEFAULT_OCR_MAX_RETRIES,
) -> dict[int, str]:
    types: Any = importlib.import_module("google.genai.types")  # Lazy import (M-03)

    if len(batch) == 1:
        pno, png_bytes, _ = batch[0]
        return {
            pno: _call_gemini_vision_ocr_sync(
                bundle, png_bytes, max_retries=max_retries
            )
        }

    contents: list[Any] = []
    for pno, png_bytes, _ in batch:
        contents.append(f"=== TRANG page_no={pno} ===")
        contents.append(types.Part.from_bytes(data=png_bytes, mime_type="image/png"))
    contents.append(_BATCH_VISION_OCR_PROMPT)

    expected_pages = {pno for pno, _, _ in batch}
    parsed_map: dict[int, str] = {}

    for attempt in range(max_retries):
        try:
            response = bundle.client.models.generate_content(
                model=bundle.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
            raw_text = str(getattr(response, "text", "") or "").strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)
            payload = json.loads(raw_text)
            pages_list = payload.get("pages") if isinstance(payload, dict) else None
            if isinstance(pages_list, list):
                for item in pages_list:
                    if not isinstance(item, dict):
                        continue
                    pno_val = item.get("page_no")
                    md_val = item.get("markdown")
                    if (
                        isinstance(pno_val, int)
                        and pno_val in expected_pages
                        and isinstance(md_val, str)
                    ):
                        parsed_map[pno_val] = md_val.strip()
                if parsed_map:
                    break
        except Exception as exc:
            wait_s = min(20.0, (2.0**attempt) * 1.5)
            logger.warning(
                "Batch Vision OCR (%d pages) attempt %d/%d failed (%s); retrying in %.1fs.",
                len(batch),
                attempt + 1,
                max_retries,
                type(exc).__name__,
                wait_s,
            )
            time.sleep(wait_s)

    # Single-page fallback for any page dropped by the batch response.
    for pno, png_bytes, _ in batch:
        if pno not in parsed_map:
            parsed_map[pno] = _call_gemini_vision_ocr_sync(
                bundle, png_bytes, max_retries=max_retries
            )
    return parsed_map


async def _ocr_pages_async(
    bundle: GeminiClientBundle,
    ocr_tasks: list[tuple[int, bytes, str | None]],
    *,
    concurrency: int = DEFAULT_OCR_CONCURRENCY,
    batch_size: int = DEFAULT_OCR_BATCH_SIZE,
    max_retries: int = DEFAULT_OCR_MAX_RETRIES,
    cache_dir: Path | None = None,
    pdf_sha256: str | None = None,
) -> dict[int, str]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: dict[int, str] = {}
    eff_batch = max(1, int(batch_size))
    batches = [
        ocr_tasks[i : i + eff_batch] for i in range(0, len(ocr_tasks), eff_batch)
    ]

    async def _worker(batch: list[tuple[int, bytes, str | None]]) -> None:
        async with semaphore:
            batch_out = await asyncio.to_thread(
                _call_gemini_vision_ocr_batch_sync,
                bundle,
                batch,
                max_retries=max_retries,
            )
            for pno, _, reason in batch:
                text = batch_out.get(pno, "").strip()
                results[pno] = text
                if cache_dir is not None and pdf_sha256 is not None:
                    interim = ParsedPage(
                        page_no=pno,
                        text=text,
                        extraction_method="vision_ocr" if text else "empty",
                        char_count=len(text),
                        has_tables=("|" in text and "---" in text),
                        detected_unit_scale=detect_unit_scale(text, default="VND"),
                        detected_section=detect_section_title(text),
                        ocr_reason=reason,
                    )
                    _save_cached_page(cache_dir, pdf_sha256, interim)

    await asyncio.gather(*(_worker(b) for b in batches))
    return results


def _reconstruct_ocr_lines_from_boxes(
    boxes: list[tuple[Any, str, float]],
) -> str:
    """Reconstruct reading-order lines and column-separated table rows from EasyOCR boxes."""
    items: list[tuple[float, float, float, float, str]] = []
    for bbox, text, conf in boxes:
        cleaned = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
        if not cleaned or float(conf) < 0.15:
            continue
        xs = [float(pt[0]) for pt in bbox]
        ys = [float(pt[1]) for pt in bbox]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        y_center = (y_min + y_max) / 2.0
        height = max(8.0, y_max - y_min)
        items.append((y_center, x_min, x_max, height, cleaned))

    if not items:
        return ""

    heights = sorted(it[3] for it in items)
    median_h = heights[len(heights) // 2]
    y_tol = max(10.0, median_h * 0.55)

    items.sort(key=lambda it: (it[0], it[1]))
    lines: list[list[tuple[float, float, float, float, str]]] = []
    for item in items:
        if not lines or abs(item[0] - lines[-1][0][0]) > y_tol:
            lines.append([item])
        else:
            lines[-1].append(item)

    rendered_lines: list[str] = []
    for line in lines:
        line.sort(key=lambda it: it[1])
        parts: list[str] = [line[0][4]]
        prev_x_max = line[0][2]
        has_wide_gap = False
        for _, x_min, x_max, _, text in line[1:]:
            gap = x_min - prev_x_max
            if gap > max(40.0, median_h * 1.8):
                parts.append("|")
                has_wide_gap = True
            parts.append(text)
            prev_x_max = max(prev_x_max, x_max)
        joined = " ".join(parts).replace(" | ", " | ")
        if has_wide_gap:
            rendered_lines.append(f"| {joined} |")
        else:
            rendered_lines.append(joined)
    return "\n".join(rendered_lines).strip()


class LocalGpuOcrEngine:
    """Lazy-loading EasyOCR Vietnamese/English engine running on CUDA GPU (fallback when API quota is exhausted)."""

    def __init__(self, *, gpu: bool = True) -> None:
        self._prefer_gpu = gpu
        self._reader: Any | None = None

    def _ensure_loaded(self) -> Any:
        if self._reader is not None:
            return self._reader
        easyocr: Any = importlib.import_module("easyocr")  # Lazy import (M-03)
        torch: Any = importlib.import_module("torch")

        use_gpu = bool(self._prefer_gpu and torch.cuda.is_available())
        self._reader = easyocr.Reader(["vi", "en"], gpu=use_gpu, verbose=False)
        logger.info("Initialized LocalGpuOcrEngine (EasyOCR vi+en, gpu=%s)", use_gpu)
        return self._reader

    def ocr_png_bytes(self, png_bytes: bytes) -> str:
        """Run EasyOCR on PNG bytes and reconstruct reading-order Vietnamese text/tables."""
        reader = self._ensure_loaded()
        raw_boxes = reader.readtext(
            png_bytes,
            detail=1,
            batch_size=64,
            canvas_size=1280,
            mag_ratio=1.0,
            decoder="greedy",
        )
        return _reconstruct_ocr_lines_from_boxes(list(raw_boxes))

    def release(self) -> None:
        """Release GPU VRAM held by EasyOCR before loading BGE-M3."""
        self._reader = None
        try:
            torch: Any = importlib.import_module("torch")

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def parse_curated_pdf(
    pdf_path: Path,
    *,
    record: CuratedPdfRecord,
    cache_dir: Path | None = DEFAULT_PARSED_CACHE_DIR,
    gemini_bundle: GeminiClientBundle | None = None,
    local_ocr_engine: LocalGpuOcrEngine | None = None,
    ocr_backend: OcrBackend = "auto",
    enable_vision_ocr: bool = True,
    ocr_dpi: int = DEFAULT_OCR_DPI,
    ocr_concurrency: int = DEFAULT_OCR_CONCURRENCY,
    ocr_batch_size: int = DEFAULT_OCR_BATCH_SIZE,
    min_digital_alnum_chars: int = DEFAULT_MIN_DIGITAL_ALNUM_CHARS,
) -> ParsedDocument:
    """Parse a curated PDF using hybrid PyMuPDF digital + 200 DPI Gemini Vision OCR."""
    pymupdf: Any = importlib.import_module("pymupdf")  # Lazy import (M-03)

    filing_id = build_filing_id(record)
    pages_by_no: dict[int, ParsedPage] = {}
    pending_ocr: list[tuple[int, bytes, str | None]] = []

    with pymupdf.open(str(pdf_path)) as doc:
        total_pages = len(doc)
        running_unit = "VND"
        running_section = "Báo cáo tài chính hợp nhất"

        for idx in range(total_pages):
            page_no = idx + 1
            if cache_dir is not None:
                cached = _load_cached_page(cache_dir, record.content_sha256, page_no)
                if cached is not None:
                    pages_by_no[page_no] = cached
                    running_unit = cached.detected_unit_scale or running_unit
                    running_section = cached.detected_section or running_section
                    continue

            page = doc[idx]
            raw_text = str(page.get_text("text") or "")
            font_names = tuple(
                str(f[3]) for f in (page.get_fonts() or []) if len(f) > 3
            )
            image_count = len(page.get_images(full=False) or [])

            requires_ocr, ocr_reason = needs_vision_ocr(
                raw_text,
                font_names=font_names,
                image_count=image_count,
                min_alnum_chars=min_digital_alnum_chars,
            )

            if requires_ocr and enable_vision_ocr:
                png_bytes = _render_page_png_bytes(page, dpi=ocr_dpi)
                pending_ocr.append((page_no, png_bytes, ocr_reason))
            else:
                extracted_text, has_tables = _extract_digital_page_text(page)
                method: ExtractionMethod = (
                    "digital" if extracted_text.strip() else "empty"
                )
                running_unit = detect_unit_scale(extracted_text, default=running_unit)
                running_section = detect_section_title(
                    extracted_text, previous_section=running_section
                )
                parsed_page = ParsedPage(
                    page_no=page_no,
                    text=extracted_text,
                    extraction_method=method,
                    char_count=len(extracted_text),
                    has_tables=has_tables,
                    detected_unit_scale=running_unit,
                    detected_section=running_section,
                    ocr_reason=ocr_reason if requires_ocr else None,
                )
                pages_by_no[page_no] = parsed_page
                if cache_dir is not None:
                    _save_cached_page(cache_dir, record.content_sha256, parsed_page)

    if pending_ocr:
        ocr_texts: dict[int, str] = {}
        used_method: ExtractionMethod = "vision_ocr"

        if ocr_backend == "easyocr":
            engine = local_ocr_engine or LocalGpuOcrEngine(gpu=True)
            used_method = "local_gpu_ocr"
            for pno, png_bytes, reason in pending_ocr:
                text = engine.ocr_png_bytes(png_bytes)
                ocr_texts[pno] = text
                if cache_dir is not None:
                    interim = ParsedPage(
                        page_no=pno,
                        text=text,
                        extraction_method="local_gpu_ocr" if text else "empty",
                        char_count=len(text),
                        has_tables=("|" in text),
                        detected_unit_scale=detect_unit_scale(text, default="VND"),
                        detected_section=detect_section_title(text),
                        ocr_reason=reason,
                    )
                    _save_cached_page(cache_dir, record.content_sha256, interim)
        else:
            try:
                active_bundle = gemini_bundle or create_verified_gemini_bundle()
                ocr_texts = asyncio.run(
                    _ocr_pages_async(
                        active_bundle,
                        pending_ocr,
                        concurrency=ocr_concurrency,
                        batch_size=ocr_batch_size,
                        cache_dir=cache_dir,
                        pdf_sha256=record.content_sha256,
                    )
                )
            except Exception as exc:
                if ocr_backend == "gemini":
                    raise
                logger.warning(
                    "Gemini Vision OCR unavailable (%s); falling back to LocalGpuOcrEngine.",
                    type(exc).__name__,
                )
                engine = local_ocr_engine or LocalGpuOcrEngine(gpu=True)
                used_method = "local_gpu_ocr"
                for pno, png_bytes, reason in pending_ocr:
                    if cache_dir is not None:
                        already = _load_cached_page(
                            cache_dir, record.content_sha256, pno
                        )
                        if already is not None and already.text.strip():
                            ocr_texts[pno] = already.text
                            continue
                    text = engine.ocr_png_bytes(png_bytes)
                    ocr_texts[pno] = text
                    if cache_dir is not None:
                        interim = ParsedPage(
                            page_no=pno,
                            text=text,
                            extraction_method="local_gpu_ocr" if text else "empty",
                            char_count=len(text),
                            has_tables=("|" in text),
                            detected_unit_scale=detect_unit_scale(text, default="VND"),
                            detected_section=detect_section_title(text),
                            ocr_reason=reason,
                        )
                        _save_cached_page(cache_dir, record.content_sha256, interim)

        running_unit = "VND"
        running_section = "Báo cáo tài chính hợp nhất"
        ocr_reasons = {pno: reason for pno, _, reason in pending_ocr}

        for page_no in range(1, total_pages + 1):
            if page_no in pages_by_no:
                running_unit = pages_by_no[page_no].detected_unit_scale or running_unit
                running_section = (
                    pages_by_no[page_no].detected_section or running_section
                )
                continue
            ocr_text = ocr_texts.get(page_no, "").strip()
            running_unit = detect_unit_scale(ocr_text, default=running_unit)
            running_section = detect_section_title(
                ocr_text, previous_section=running_section
            )
            parsed_page = ParsedPage(
                page_no=page_no,
                text=ocr_text,
                extraction_method=used_method if ocr_text else "empty",
                char_count=len(ocr_text),
                has_tables=("|" in ocr_text),
                detected_unit_scale=running_unit,
                detected_section=running_section,
                ocr_reason=ocr_reasons.get(page_no),
            )
            pages_by_no[page_no] = parsed_page
            if cache_dir is not None:
                _save_cached_page(cache_dir, record.content_sha256, parsed_page)

    ordered_pages = tuple(pages_by_no[pno] for pno in range(1, total_pages + 1))
    digital_count = sum(1 for p in ordered_pages if p.extraction_method == "digital")
    ocr_count = sum(
        1
        for p in ordered_pages
        if p.extraction_method in ("vision_ocr", "local_gpu_ocr")
    )
    empty_count = sum(1 for p in ordered_pages if p.extraction_method == "empty")

    return ParsedDocument(
        filing_id=filing_id,
        issuer_id=record.issuer_id,
        ticker=record.issuer_id,
        fiscal_year=record.fiscal_year,
        fiscal_quarter=record.fiscal_quarter,
        statement_scope=record.statement_scope,
        doc_type=record.doc_type,
        pdf_sha256=record.content_sha256,
        source_title=record.source_title,
        total_pages=total_pages,
        digital_pages=digital_count,
        ocr_pages=ocr_count,
        empty_pages=empty_count,
        pages=ordered_pages,
    )


__all__ = [
    "DEFAULT_MIN_DIGITAL_ALNUM_CHARS",
    "DEFAULT_OCR_BATCH_SIZE",
    "DEFAULT_OCR_CONCURRENCY",
    "DEFAULT_OCR_DPI",
    "DEFAULT_PARSED_CACHE_DIR",
    "ExtractionMethod",
    "LocalGpuOcrEngine",
    "OcrBackend",
    "ParsedDocument",
    "ParsedPage",
    "build_filing_id",
    "detect_section_title",
    "detect_unit_scale",
    "has_legacy_vietnamese_encoding",
    "needs_vision_ocr",
    "parse_curated_pdf",
]
