"""Hybrid pgvector + PostgreSQL unaccent FTS retriever with Reciprocal Rank Fusion (UI-08b)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from finintel.ports.documents import DOCUMENT_STATEMENT_SCOPES, DocumentChunk
from finintel.rag.embedder import BgeM3Embedder
from finintel.rag.store import PgVectorRagStore

DEFAULT_RRF_K = 60
DEFAULT_CANDIDATE_K = 50

_FTS_TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ỹ]+")
_EXACT_VIETNAMESE_STOPWORDS: frozenset[str] = frozenset({"tại"})
_VIETNAMESE_STOPWORDS: frozenset[str] = frozenset(
    {
        "la",
        "cua",
        "va",
        "cho",
        "trong",
        "co",
        "khong",
        "nao",
        "bao",
        "nhieu",
        "nam",
        "ve",
        "voi",
        "duoc",
        "cac",
        "nhung",
        "mot",
        "hay",
        "khi",
        "den",
        "tu",
        "bi",
        "gi",
        "the",
        "nhu",
        "nay",
        "do",
        "sao",
        "theo",
        "bctc",
        "hop",
        "nhat",
        "kiem",
        "toan",
        "cong",
        "ty",
        "ngay",
    }
)


def _strip_vietnamese_diacritics(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    without_marks = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return without_marks.replace("đ", "d").replace("Đ", "D")


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """Rich hybrid retrieval hit carrying RRF score, vector similarity, FTS rank, and source title."""

    chunk: DocumentChunk
    source_title: str
    rrf_score: float
    vector_similarity: float
    fts_rank: float
    vector_position: int | None
    fts_position: int | None


def reciprocal_rank_fusion(
    *,
    vector_ranking: Sequence[str],
    fts_ranking: Sequence[str],
    k: int = DEFAULT_RRF_K,
    vector_weight: float = 1.0,
    fts_weight: float = 1.0,
) -> list[tuple[str, float]]:
    """Fuse two ranked lists of chunk IDs using Reciprocal Rank Fusion (RRF)."""
    scores: dict[str, float] = {}
    for rank_idx, chunk_key in enumerate(vector_ranking, start=1):
        scores[chunk_key] = scores.get(chunk_key, 0.0) + (
            vector_weight / (k + rank_idx)
        )
    for rank_idx, chunk_key in enumerate(fts_ranking, start=1):
        scores[chunk_key] = scores.get(chunk_key, 0.0) + (fts_weight / (k + rank_idx))
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def build_or_tsquery_expression(query: str, *, max_terms: int = 24) -> str:
    """Build a safe OR-joined PostgreSQL tsquery string from natural-language Vietnamese queries."""
    tokens = [tok.lower() for tok in _FTS_TOKEN_RE.findall(query)]
    filtered: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        if len(tok) < 2 or tok in _EXACT_VIETNAMESE_STOPWORDS:
            continue
        unaccented = _strip_vietnamese_diacritics(tok)
        if (
            len(unaccented) < 2
            or unaccented in _VIETNAMESE_STOPWORDS
            or unaccented in seen
        ):
            continue
        seen.add(unaccented)
        filtered.append(tok)
        if len(filtered) >= max_terms:
            break
    if not filtered:
        for tok in tokens:
            if tok and tok not in seen:
                seen.add(tok)
                filtered.append(tok)
                if len(filtered) >= max_terms:
                    break
    return " | ".join(filtered)


def _build_phrase_tsquery_expression(
    query: str,
    *,
    excluded_terms: frozenset[str] = frozenset(),
    max_phrases: int = 12,
) -> str:
    """Build adjacent-bigram ``<->`` tsquery clauses to boost exact Vietnamese accounting phrases."""
    raw_tokens = [tok.lower() for tok in _FTS_TOKEN_RE.findall(query)]
    phrases: list[str] = []
    seen: set[str] = set()
    general_stop = _VIETNAMESE_STOPWORDS - {"kiem", "toan"}
    for left, right in zip(raw_tokens, raw_tokens[1:], strict=False):
        l_un = _strip_vietnamese_diacritics(left)
        r_un = _strip_vietnamese_diacritics(right)
        if not l_un or not r_un:
            continue
        if l_un in excluded_terms or r_un in excluded_terms:
            continue
        if left in _EXACT_VIETNAMESE_STOPWORDS or right in _EXACT_VIETNAMESE_STOPWORDS:
            continue
        # Keep short "ý" when paired in "ý kiến", otherwise require >= 2 chars.
        if (len(l_un) < 2 and l_un != "y") or len(r_un) < 2:
            continue
        if l_un in general_stop or r_un in general_stop:
            continue
        pair = f"{left} <-> {right}"
        pair_key = f"{l_un}<->{r_un}"
        if pair_key in seen:
            continue
        seen.add(pair_key)
        phrases.append(pair)
        if len(phrases) >= max_phrases:
            break
    return " | ".join(phrases)


class HybridPgVectorRetriever:
    """Port 2 ``DocumentRetriever`` implementation combining ``pgvector`` HNSW + ``unaccent`` FTS via RRF."""

    def __init__(
        self,
        store: PgVectorRagStore,
        embedder: BgeM3Embedder,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        candidate_k: int = DEFAULT_CANDIDATE_K,
        vector_weight: float = 1.0,
        fts_weight: float = 1.0,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.rrf_k = max(1, int(rrf_k))
        self.candidate_k = max(5, int(candidate_k))
        self.vector_weight = float(vector_weight)
        self.fts_weight = float(fts_weight)

    def retrieve_hits(
        self,
        *,
        issuer_id: str,
        query: str,
        statement_scope: str | None = None,
        fiscal_year: int | None = None,
        fiscal_quarter: int | None = None,
        limit: int = 6,
    ) -> list[RetrievalHit]:
        """Run scope-isolated vector + unaccent FTS search and fuse via RRF."""
        clean_issuer = issuer_id.strip().upper()
        clean_query = query.strip()
        if not clean_issuer or not clean_query:
            return []

        scope_filter: str | None = None
        if statement_scope is not None:
            scope_filter = statement_scope.strip().upper()
            if scope_filter not in DOCUMENT_STATEMENT_SCOPES:
                raise ValueError(
                    f"Invalid statement_scope {statement_scope!r}; expected one of {sorted(DOCUMENT_STATEMENT_SCOPES)}."
                )

        query_vec = self.embedder.embed_query(clean_query)
        excluded_fts_terms: set[str] = {clean_issuer.lower()}
        if fiscal_year is not None:
            excluded_fts_terms.add(str(int(fiscal_year)))
        raw_or_expr = build_or_tsquery_expression(clean_query)
        or_terms = [
            tok
            for tok in raw_or_expr.split(" | ")
            if tok and _strip_vietnamese_diacritics(tok) not in excluded_fts_terms
        ]
        or_expr = " | ".join(or_terms) if or_terms else raw_or_expr
        phrase_expr = _build_phrase_tsquery_expression(
            clean_query, excluded_terms=frozenset(excluded_fts_terms)
        )

        where_clauses = ["c.ticker = %s"]
        base_params: list[Any] = [clean_issuer]
        if scope_filter is not None:
            where_clauses.append("c.statement_scope = %s")
            base_params.append(scope_filter)
        if fiscal_year is not None:
            where_clauses.append("c.fiscal_year = %s")
            base_params.append(int(fiscal_year))
        if fiscal_quarter is not None:
            where_clauses.append("c.fiscal_quarter = %s")
            base_params.append(int(fiscal_quarter))
        where_sql = " AND ".join(where_clauses)

        vec_rows: list[tuple[Any, ...]] = []
        fts_rows: list[tuple[Any, ...]] = []

        with self.store._connect() as conn:
            is_psycopg_conn = type(conn).__module__.startswith("psycopg")
            with conn.cursor() as cur:
                if is_psycopg_conn:
                    try:
                        cur.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
                    except Exception:
                        pass
                # 1. Dense vector search (pgvector HNSW cosine distance)
                cur.execute(
                    f"""
                    SELECT
                        c.content_sha256,
                        c.filing_id,
                        c.issuer_id,
                        c.fiscal_year,
                        c.fiscal_quarter,
                        c.statement_scope,
                        c.doc_type,
                        c.page_no,
                        c.section_title,
                        c.unit_scale,
                        c.text_content,
                        f.source_title,
                        GREATEST(0.0, 1.0 - (c.embedding <=> %s::vector)) AS vector_sim
                    FROM document_chunks c
                    JOIN document_files f ON f.filing_id = c.filing_id
                    WHERE {where_sql}
                    ORDER BY c.embedding <=> %s::vector ASC
                    LIMIT %s
                    """,
                    (query_vec, *base_params, query_vec, self.candidate_k),
                )
                vec_rows = list(cur.fetchall())

                # 2. Full-Text Search with unaccent (strict plainto_tsquery first, OR+phrase fallback)
                cur.execute(
                    f"""
                    SELECT
                        c.content_sha256,
                        c.filing_id,
                        c.issuer_id,
                        c.fiscal_year,
                        c.fiscal_quarter,
                        c.statement_scope,
                        c.doc_type,
                        c.page_no,
                        c.section_title,
                        c.unit_scale,
                        c.text_content,
                        f.source_title,
                        ts_rank_cd(c.search_tsv, plainto_tsquery('simple', unaccent(%s)), 32) AS fts_score
                    FROM document_chunks c
                    JOIN document_files f ON f.filing_id = c.filing_id
                    WHERE {where_sql}
                      AND c.search_tsv @@ plainto_tsquery('simple', unaccent(%s))
                    ORDER BY fts_score DESC
                    LIMIT %s
                    """,
                    (clean_query, *base_params, clean_query, self.candidate_k),
                )
                fts_rows = list(cur.fetchall())

                if not fts_rows and or_expr:
                    if phrase_expr:
                        rank_sql = (
                            "ts_rank_cd(c.search_tsv, to_tsquery('simple', unaccent(%s)), 32) "
                            "+ 2.5 * ts_rank_cd(c.search_tsv, to_tsquery('simple', unaccent(%s)), 32)"
                        )
                        match_sql = f"({or_expr}) | ({phrase_expr})"
                        fts_params: tuple[Any, ...] = (
                            or_expr,
                            phrase_expr,
                            *base_params,
                            match_sql,
                            self.candidate_k,
                        )
                    else:
                        rank_sql = "ts_rank_cd(c.search_tsv, to_tsquery('simple', unaccent(%s)), 32)"
                        fts_params = (
                            or_expr,
                            *base_params,
                            or_expr,
                            self.candidate_k,
                        )
                    cur.execute(
                        f"""
                        SELECT
                            c.content_sha256,
                            c.filing_id,
                            c.issuer_id,
                            c.fiscal_year,
                            c.fiscal_quarter,
                            c.statement_scope,
                            c.doc_type,
                            c.page_no,
                            c.section_title,
                            c.unit_scale,
                            c.text_content,
                            f.source_title,
                            {rank_sql} AS fts_score
                        FROM document_chunks c
                        JOIN document_files f ON f.filing_id = c.filing_id
                        WHERE {where_sql}
                          AND c.search_tsv @@ to_tsquery('simple', unaccent(%s))
                        ORDER BY fts_score DESC
                        LIMIT %s
                        """,
                        fts_params,
                    )
                    fts_rows = list(cur.fetchall())

        if not vec_rows and not fts_rows:
            return []

        payload_by_sha: dict[str, tuple[Any, ...]] = {}
        vec_sim_by_sha: dict[str, float] = {}
        fts_rank_by_sha: dict[str, float] = {}
        vec_pos_by_sha: dict[str, int] = {}
        fts_pos_by_sha: dict[str, int] = {}

        vector_ranking: list[str] = []
        for idx, row in enumerate(vec_rows, start=1):
            sha = str(row[0]).strip()
            vector_ranking.append(sha)
            payload_by_sha[sha] = row
            vec_sim_by_sha[sha] = float(row[12])
            vec_pos_by_sha[sha] = idx

        fts_ranking: list[str] = []
        for idx, row in enumerate(fts_rows, start=1):
            sha = str(row[0]).strip()
            fts_ranking.append(sha)
            payload_by_sha.setdefault(sha, row)
            fts_rank_by_sha[sha] = float(row[12])
            fts_pos_by_sha[sha] = idx

        fused = reciprocal_rank_fusion(
            vector_ranking=vector_ranking,
            fts_ranking=fts_ranking,
            k=self.rrf_k,
            vector_weight=self.vector_weight,
            fts_weight=self.fts_weight,
        )

        hits: list[RetrievalHit] = []
        for sha, rrf_score in fused[: max(1, int(limit))]:
            row = payload_by_sha[sha]
            chunk = DocumentChunk(
                filing_id=str(row[1]),
                issuer_id=str(row[2]),
                fiscal_year=int(row[3]),
                fiscal_quarter=int(row[4]) if row[4] is not None else None,
                statement_scope=str(row[5]),
                doc_type=str(row[6]),
                page_no=int(row[7]),
                section_title=str(row[8]),
                unit_scale=str(row[9]),
                text_content=str(row[10]),
                content_sha256=sha,
                score=round(rrf_score, 6),
            )
            hits.append(
                RetrievalHit(
                    chunk=chunk,
                    source_title=str(row[11]),
                    rrf_score=round(rrf_score, 6),
                    vector_similarity=round(vec_sim_by_sha.get(sha, 0.0), 4),
                    fts_rank=round(fts_rank_by_sha.get(sha, 0.0), 4),
                    vector_position=vec_pos_by_sha.get(sha),
                    fts_position=fts_pos_by_sha.get(sha),
                )
            )
        return hits

    def retrieve(
        self,
        *,
        issuer_id: str,
        query: str,
        statement_scope: str | None = None,
        fiscal_year: int | None = None,
        fiscal_quarter: int | None = None,
        limit: int = 5,
    ) -> Sequence[DocumentChunk]:
        """Implement Port 2 ``DocumentRetriever`` protocol returning grounded ``DocumentChunk`` items."""
        hits = self.retrieve_hits(
            issuer_id=issuer_id,
            query=query,
            statement_scope=statement_scope,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            limit=limit,
        )
        return [hit.chunk for hit in hits]


__all__ = [
    "DEFAULT_CANDIDATE_K",
    "DEFAULT_RRF_K",
    "HybridPgVectorRetriever",
    "RetrievalHit",
    "build_or_tsquery_expression",
    "reciprocal_rank_fusion",
]
