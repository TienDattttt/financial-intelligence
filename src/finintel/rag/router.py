"""Standalone FastAPI APIRouter for Port 2 Grounded Document AI (UI-08b).

This router is intentionally NOT mounted in ``src/finintel/serving/api.py`` on this branch
to maintain 0% merge conflict with parallel Wave 5 serving work. All ``fastapi`` / ``pydantic``
imports are lazy inside ``create_rag_router`` so CI ``mvp-checks`` stays green (M-03).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from finintel.rag.investigator import (
    GroundedDocumentInvestigator,
    PromptInjectionError,
)
from finintel.rag.store import PgVectorRagStore

if TYPE_CHECKING:
    from fastapi import APIRouter


def create_rag_router(
    *,
    get_store: Callable[[], PgVectorRagStore],
    get_investigator: Callable[[], GroundedDocumentInvestigator],
) -> APIRouter:
    """Build standalone FastAPI router for ``/api/v2/documents/{ticker}/*`` endpoints."""
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, Field

    class InvestigateRequest(BaseModel):
        question: str = Field(..., min_length=3, max_length=600)
        fiscal_year: int | None = Field(default=None, ge=2000, le=2100)
        statement_scope: str | None = Field(default="CONSOLIDATED")
        top_k: int = Field(default=6, ge=1, le=12)
        use_cache: bool = Field(default=True)

    class DocumentFileItem(BaseModel):
        filing_id: str
        issuer_id: str
        ticker: str
        fiscal_year: int
        fiscal_quarter: int | None
        statement_scope: str
        doc_type: str
        cohort: str
        source_title: str
        source_url: str
        pdf_sha256: str
        file_size_bytes: int
        total_pages: int
        digital_pages: int
        ocr_pages: int
        chunk_count: int

    class DocumentListResponse(BaseModel):
        ticker: str
        total_documents: int
        documents: list[DocumentFileItem]

    class CitationItem(BaseModel):
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

    class InvestigateResponse(BaseModel):
        ticker: str
        question: str
        answer: str
        abstained: bool
        abstention_reason: str | None
        citations: list[CitationItem]
        retrieved_chunks_count: int
        top_vector_similarity: float
        cached: bool
        latency_ms: float
        model_used: str | None
        disclaimer: str

    router = APIRouter(prefix="/api/v2/documents", tags=["documents-rag"])

    @router.get("/{ticker}/list", response_model=DocumentListResponse)
    async def list_company_documents(ticker: str) -> DocumentListResponse:
        clean_ticker = ticker.strip().upper()
        if not clean_ticker or not clean_ticker.isalnum() or len(clean_ticker) > 16:
            raise HTTPException(status_code=422, detail="Invalid ticker symbol.")
        store = await asyncio.to_thread(get_store)
        docs = await asyncio.to_thread(store.list_documents, clean_ticker)
        return DocumentListResponse(
            ticker=clean_ticker,
            total_documents=len(docs),
            documents=[
                DocumentFileItem(
                    filing_id=d.filing_id,
                    issuer_id=d.issuer_id,
                    ticker=d.ticker,
                    fiscal_year=d.fiscal_year,
                    fiscal_quarter=d.fiscal_quarter,
                    statement_scope=d.statement_scope,
                    doc_type=d.doc_type,
                    cohort=d.cohort,
                    source_title=d.source_title,
                    source_url=d.source_url,
                    pdf_sha256=d.pdf_sha256,
                    file_size_bytes=d.file_size_bytes,
                    total_pages=d.total_pages,
                    digital_pages=d.digital_pages,
                    ocr_pages=d.ocr_pages,
                    chunk_count=d.chunk_count,
                )
                for d in docs
            ],
        )

    async def investigate_company_documents(
        ticker: str,
        body: InvestigateRequest,
    ) -> InvestigateResponse:
        clean_ticker = ticker.strip().upper()
        if not clean_ticker or not clean_ticker.isalnum() or len(clean_ticker) > 16:
            raise HTTPException(status_code=422, detail="Invalid ticker symbol.")
        investigator = await asyncio.to_thread(get_investigator)
        try:
            result = await investigator.investigate(
                ticker=clean_ticker,
                question=body.question,
                statement_scope=body.statement_scope,
                fiscal_year=body.fiscal_year,
                top_k=body.top_k,
                use_cache=body.use_cache,
            )
        except PromptInjectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except TimeoutError:
            raise HTTPException(
                status_code=503,
                detail="Hệ thống điều tra tài liệu đang quá tải, vui lòng thử lại sau.",
                headers={"Retry-After": "5"},
            ) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        payload: dict[str, Any] = result.to_dict()
        return InvestigateResponse(**payload)

    # Resolve local Pydantic annotations eagerly so FastAPI builds OpenAPI schemas
    # cleanly even with ``from __future__ import annotations``.
    investigate_company_documents.__annotations__["ticker"] = str
    investigate_company_documents.__annotations__["body"] = InvestigateRequest
    investigate_company_documents.__annotations__["return"] = InvestigateResponse
    router.add_api_route(
        "/{ticker}/investigate",
        investigate_company_documents,
        methods=["POST"],
        response_model=InvestigateResponse,
    )

    return router


__all__ = ["create_rag_router"]
