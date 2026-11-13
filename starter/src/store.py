"""PostgreSQL + pgvector persistent store with idempotent upsert and SQL metadata filtering."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg

from .models import Chunk

_ALLOWED_FILTERS: dict[str, tuple[str, type]] = {
    "doc_type": ("doc_type", str),
    "year": ("year", int),
    "document_id": ("document_id", str),
    "chapter": ("chapter", str),
}


def _format_vector(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


class PgVectorStore:
    """Vector store backed by PostgreSQL + pgvector using exact cosine distance (<=>)."""

    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("database_url must be provided")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.database_url = database_url
        self.dimensions = dimensions

    def create_schema(self) -> None:
        """Create pgvector table and supporting indexes for strategy and metadata filtering."""
        ddl = f"""
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE TABLE IF NOT EXISTS chunks (
            strategy TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            source TEXT NOT NULL,
            page INT NOT NULL,
            doc_type TEXT NOT NULL,
            year INT NOT NULL,
            chapter TEXT,
            article TEXT,
            content_hash TEXT NOT NULL,
            text TEXT NOT NULL,
            metadata JSONB NOT NULL,
            embedding vector({self.dimensions}) NOT NULL,
            PRIMARY KEY (strategy, chunk_id)
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_strategy_doc ON chunks (strategy, document_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_filters ON chunks (strategy, doc_type, year);
        """
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Validate dimensions, delete obsolete chunks for updated docs, and upsert idempotently."""
        if len(chunks) != len(vectors):
            raise ValueError(f"Chunk/vector count mismatch: {len(chunks)} vs {len(vectors)}")
        for vec in vectors:
            if len(vec) != self.dimensions:
                raise ValueError(f"Expected vector dim {self.dimensions}, got {len(vec)}")

        if not chunks:
            return

        doc_to_ids: dict[str, list[str]] = {}
        for c in chunks:
            doc_to_ids.setdefault(c.document_id, []).append(c.chunk_id)

        upsert_sql = """
        INSERT INTO chunks (
            strategy, chunk_id, document_id, source, page,
            doc_type, year, chapter, article, content_hash,
            text, metadata, embedding
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
        ON CONFLICT (strategy, chunk_id) DO UPDATE SET
            document_id = EXCLUDED.document_id,
            source = EXCLUDED.source,
            page = EXCLUDED.page,
            doc_type = EXCLUDED.doc_type,
            year = EXCLUDED.year,
            chapter = EXCLUDED.chapter,
            article = EXCLUDED.article,
            content_hash = EXCLUDED.content_hash,
            text = EXCLUDED.text,
            metadata = EXCLUDED.metadata,
            embedding = EXCLUDED.embedding;
        """

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                for doc_id, keep_ids in doc_to_ids.items():
                    cur.execute(
                        "DELETE FROM chunks WHERE strategy = %s AND document_id = %s AND NOT (chunk_id = ANY(%s))",
                        (strategy, doc_id, keep_ids),
                    )

                for chunk, vec in zip(chunks, vectors):
                    meta = dict(chunk.metadata)
                    meta.update(
                        {
                            "document_id": chunk.document_id,
                            "source": chunk.source,
                            "page": chunk.page,
                        }
                    )
                    content_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()[:16]
                    cur.execute(
                        upsert_sql,
                        (
                            strategy,
                            chunk.chunk_id,
                            chunk.document_id,
                            chunk.source,
                            chunk.page,
                            str(meta.get("doc_type", "")),
                            int(meta.get("year", 0)),
                            meta.get("chapter"),
                            meta.get("article"),
                            content_hash,
                            chunk.text,
                            json.dumps(meta, ensure_ascii=False),
                            _format_vector(vec),
                        ),
                    )
            conn.commit()

    def count_chunks(self, strategy: str) -> int:
        """Return total stored chunks for a strategy."""
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chunks WHERE strategy = %s", (strategy,))
                row = cur.fetchone()
                return int(row[0]) if row else 0

    def get_chunks_for_page(self, strategy: str, document_id: str, page: int) -> list[dict[str, Any]]:
        """Fetch stored chunks for a given strategy/document/page (for manual coherence audit)."""
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT chunk_id, document_id, source, page, text, metadata
                    FROM chunks
                    WHERE strategy = %s AND document_id = %s AND page = %s
                    ORDER BY chunk_id ASC
                    """,
                    (strategy, document_id, page),
                )
                rows = cur.fetchall()
        return [
            {
                "chunk_id": r[0],
                "document_id": r[1],
                "source": r[2],
                "page": r[3],
                "text": r[4],
                "metadata": r[5],
            }
            for r in rows
        ]

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter permitted metadata in SQL, rank by exact cosine distance, return top-k."""
        if len(query_vector) != self.dimensions:
            raise ValueError(f"Expected query dim {self.dimensions}, got {len(query_vector)}")

        where_clauses = ["strategy = %s"]
        qvec_str = _format_vector(query_vector)
        filter_params: list[Any] = [strategy]

        if filters:
            for key, raw_val in filters.items():
                if key not in _ALLOWED_FILTERS:
                    raise ValueError(f"Unsupported metadata filter field: {key}")
                col_name, expected_type = _ALLOWED_FILTERS[key]
                where_clauses.append(f"{col_name} = %s")
                filter_params.append(expected_type(raw_val))

        sql = f"""
        SELECT
            chunk_id,
            document_id,
            source,
            page,
            text,
            metadata,
            1 - (embedding <=> %s::vector) AS cosine_similarity
        FROM chunks
        WHERE {' AND '.join(where_clauses)}
        ORDER BY embedding <=> %s::vector ASC
        LIMIT %s
        """
        params = [qvec_str, *filter_params, qvec_str, top_k]

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        return [
            {
                "chunk_id": row[0],
                "document_id": row[1],
                "source": row[2],
                "page": row[3],
                "text": row[4],
                "metadata": row[5],
                "score": round(float(row[6]), 6),
            }
            for row in rows
        ]
