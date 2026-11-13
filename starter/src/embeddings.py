"""OpenAI embedding provider adapter using official openai SDK (no mock fallback)."""
from __future__ import annotations

import os
import time
from typing import Protocol

from openai import OpenAI

_MODEL_PRICE_PER_1M_TOKENS_USD: dict[str, float] = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
}
_MODEL_DEFAULT_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Real OpenAI embedding adapter with token/cost tracking and retry handling."""

    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
        batch_size: int = 64,
        dimensions: int | None = None,
    ) -> None:
        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_key:
            raise RuntimeError("OPENAI_API_KEY is missing. Set it in starter/.env before running.")

        self.model_name = (model_name or os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small").strip()
        self.batch_size = batch_size
        self.dimensions = dimensions or _MODEL_DEFAULT_DIMS.get(self.model_name, 1536)
        self._custom_dimensions = dimensions
        self.client = OpenAI(api_key=resolved_key)
        self.api_calls = 0
        self.total_chars = 0
        self.total_tokens = 0

    @property
    def estimated_cost_usd(self) -> float:
        price = _MODEL_PRICE_PER_1M_TOKENS_USD.get(self.model_name, 0.0)
        return round((self.total_tokens / 1_000_000) * price, 6)

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        kwargs: dict[str, object] = {
            "model": self.model_name,
            "input": batch,
        }
        if self._custom_dimensions is not None:
            kwargs["dimensions"] = self._custom_dimensions

        last_err: Exception | None = None
        for attempt in range(4):
            try:
                response = self.client.embeddings.create(**kwargs)
                ordered = sorted(response.data, key=lambda item: item.index)
                vectors = [list(item.embedding) for item in ordered]
                if len(vectors) != len(batch):
                    raise RuntimeError(
                        f"Embedding count mismatch: expected {len(batch)}, got {len(vectors)}"
                    )
                self.dimensions = len(vectors[0])
                self.api_calls += 1
                self.total_chars += sum(len(t) for t in batch)
                if response.usage and response.usage.prompt_tokens:
                    self.total_tokens += int(response.usage.prompt_tokens)
                return vectors
            except Exception as exc:
                last_err = exc
                if attempt == 3:
                    break
                time.sleep(2 ** attempt)

        raise RuntimeError(f"OpenAI embedding API failed after retries: {last_err}") from last_err

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed document chunks in batches using the configured OpenAI model."""
        if not texts:
            return []
        all_vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            all_vectors.extend(self._embed_batch(batch))
        return all_vectors

    def embed_query(self, question: str) -> list[float]:
        """Embed a single search query with the same OpenAI embedding model."""
        return self._embed_batch([question])[0]
