"""GPU-accelerated BGE-M3 dense embedder with safe CPU fallback and lazy imports (UI-08b)."""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("finintel.rag.embedder")

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DIMENSION = 1024
DEFAULT_GPU_BATCH_SIZE = 16
DEFAULT_CPU_BATCH_SIZE = 8
DEFAULT_MAX_SEQ_LENGTH = 1024


@dataclass(frozen=True, slots=True)
class EmbeddingBenchmarkStats:
    """Measured runtime telemetry for a batch embedding pass."""

    model_name: str
    device: str
    gpu_name: str | None
    dtype: str
    batch_size: int
    total_texts: int
    elapsed_seconds: float
    chunks_per_second: float


class BgeM3Embedder:
    """Lazy-loading ``BAAI/bge-m3`` (1024-dim, normalized) embedder optimized for 6 GB VRAM GPUs."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        device: str | None = None,
        batch_size: int | None = None,
        max_seq_length: int = DEFAULT_MAX_SEQ_LENGTH,
        use_fp16_on_cuda: bool = True,
    ) -> None:
        self.model_name = model_name
        self._requested_device = device
        self._requested_batch_size = batch_size
        self.max_seq_length = max_seq_length
        self.use_fp16_on_cuda = use_fp16_on_cuda

        self._model: Any | None = None
        self._device: str = "uninitialized"
        self._gpu_name: str | None = None
        self._dtype: str = "float32"
        self._batch_size: int = batch_size or DEFAULT_GPU_BATCH_SIZE

    @property
    def device(self) -> str:
        """Return active device (``cuda`` or ``cpu``), initializing if needed."""
        self._ensure_loaded()
        return self._device

    @property
    def gpu_name(self) -> str | None:
        """Return CUDA device name when running on GPU."""
        self._ensure_loaded()
        return self._gpu_name

    @property
    def dtype(self) -> str:
        """Return active tensor precision (``float16`` on CUDA or ``float32`` on CPU)."""
        self._ensure_loaded()
        return self._dtype

    @property
    def batch_size(self) -> int:
        """Return active batch size tuned for the detected device."""
        self._ensure_loaded()
        return self._batch_size

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model

        torch: Any = importlib.import_module("torch")  # Lazy import (M-03)
        st_mod: Any = importlib.import_module("sentence_transformers")
        SentenceTransformer = st_mod.SentenceTransformer

        cuda_available = bool(torch.cuda.is_available())
        if self._requested_device is not None:
            resolved_device = self._requested_device
        else:
            resolved_device = "cuda" if cuda_available else "cpu"

        if resolved_device.startswith("cuda") and not cuda_available:
            logger.warning("CUDA requested but unavailable; falling back to CPU.")
            resolved_device = "cpu"

        self._device = resolved_device
        if resolved_device.startswith("cuda"):
            self._gpu_name = str(torch.cuda.get_device_name(0))
            self._batch_size = self._requested_batch_size or DEFAULT_GPU_BATCH_SIZE
            model_kwargs: dict[str, Any] = {}
            if self.use_fp16_on_cuda:
                model_kwargs["torch_dtype"] = torch.float16
                self._dtype = "float16"
            else:
                self._dtype = "float32"
            model = SentenceTransformer(
                self.model_name,
                device=resolved_device,
                model_kwargs=model_kwargs,
            )
            if self.use_fp16_on_cuda:
                model = model.half()
        else:
            self._gpu_name = None
            self._dtype = "float32"
            self._batch_size = self._requested_batch_size or DEFAULT_CPU_BATCH_SIZE
            model = SentenceTransformer(self.model_name, device="cpu")

        model.max_seq_length = self.max_seq_length
        self._model = model
        logger.info(
            "Loaded %s on device=%s gpu=%s dtype=%s batch_size=%d",
            self.model_name,
            self._device,
            self._gpu_name,
            self._dtype,
            self._batch_size,
        )
        return self._model

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
        show_progress_bar: bool = False,
    ) -> tuple[list[list[float]], EmbeddingBenchmarkStats]:
        """Embed a sequence of document chunks and return 1024-dim L2-normalized vectors + stats."""
        model = self._ensure_loaded()
        effective_batch = batch_size or self._batch_size
        if not texts:
            return [], EmbeddingBenchmarkStats(
                model_name=self.model_name,
                device=self._device,
                gpu_name=self._gpu_name,
                dtype=self._dtype,
                batch_size=effective_batch,
                total_texts=0,
                elapsed_seconds=0.0,
                chunks_per_second=0.0,
            )

        started = time.perf_counter()
        raw_embeddings = model.encode(
            list(texts),
            batch_size=effective_batch,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=show_progress_bar,
        )
        elapsed = max(1e-6, time.perf_counter() - started)
        vectors: list[list[float]] = [
            [float(x) for x in row] for row in raw_embeddings.tolist()
        ]
        if vectors and len(vectors[0]) != EMBEDDING_DIMENSION:
            raise RuntimeError(
                f"Expected {EMBEDDING_DIMENSION}-dim embeddings, got {len(vectors[0])}."
            )

        stats = EmbeddingBenchmarkStats(
            model_name=self.model_name,
            device=self._device,
            gpu_name=self._gpu_name,
            dtype=self._dtype,
            batch_size=effective_batch,
            total_texts=len(vectors),
            elapsed_seconds=round(elapsed, 4),
            chunks_per_second=round(len(vectors) / elapsed, 2),
        )
        return vectors, stats

    def embed_query(self, query: str) -> list[float]:
        """Embed a single user query into a 1024-dim normalized vector."""
        vectors, _ = self.embed_texts([query], batch_size=1, show_progress_bar=False)
        return vectors[0]


__all__ = [
    "BgeM3Embedder",
    "DEFAULT_CPU_BATCH_SIZE",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_GPU_BATCH_SIZE",
    "DEFAULT_MAX_SEQ_LENGTH",
    "EMBEDDING_DIMENSION",
    "EmbeddingBenchmarkStats",
]
