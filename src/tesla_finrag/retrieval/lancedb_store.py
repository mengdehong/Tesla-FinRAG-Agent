"""LanceDB-backed retrieval store for persistent vector search.

Implements the :class:`RetrievalStore` contract using a file-backed LanceDB
database under ``data/processed/lancedb``. Rows are segment-level vectors with
lineage metadata pointing back to source processed chunks.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import lancedb
import pandas as pd

from tesla_finrag.ingestion.index_segmentation import ChunkSegment
from tesla_finrag.models import SectionChunk, TableChunk
from tesla_finrag.repositories import RetrievalStore

logger = logging.getLogger(__name__)

# Arrow schema for the chunks table.
_CHUNKS_TABLE = "chunks"
_METADATA_FILE = "_index_metadata.json"


def _safe_str(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    return str(value)


def _safe_int(value: object | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, float) and value != value:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class LanceDBRetrievalStore(RetrievalStore):
    """Persistent vector store backed by LanceDB.

    Parameters:
        db_path: Filesystem path where the LanceDB database is stored.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._db_path))
        self._table: lancedb.table.Table | None = None
        self._open_table()

    def _open_table(self) -> None:
        """Open the chunks table if it exists."""
        try:
            self._table = self._db.open_table(_CHUNKS_TABLE)
        except Exception:
            logger.debug("No existing chunks table found, will create on first upsert")
            self._table = None

    def _ensure_table(self, data: list[dict]) -> None:
        """Create the table on first write or add to existing."""
        if self._table is None:
            self._table = self._db.create_table(_CHUNKS_TABLE, data=data)
        else:
            self._table.add(data)

    def delete_doc(self, doc_id: UUID | str) -> None:
        """Remove all rows for a filing from the persisted index."""
        if self._table is None:
            return
        doc_id_str = str(doc_id)
        try:
            self._table.delete(
                f'doc_id = "{doc_id_str}" OR source_doc_id = "{doc_id_str}"'
            )
        except Exception:
            # Backward-compatible fallback for older table schema.
            self._table.delete(f'doc_id = "{doc_id_str}"')

    def clear(self) -> None:
        """Remove every indexed row while keeping the DB directory in place."""
        if self._table is None:
            return
        self._table.delete("true")

    # -- RetrievalStore interface -------------------------------------------

    def index_section_chunk(self, chunk: SectionChunk, embedding: list[float]) -> None:
        """Add or update a section chunk with its pre-computed embedding."""
        self.index_chunk_segments(
            chunk,
            [
                ChunkSegment(
                    text=chunk.text,
                    segment_index=0,
                    segment_count=1,
                )
            ],
            [embedding],
        )

    def index_table_chunk(self, chunk: TableChunk, embedding: list[float]) -> None:
        """Add or update a table chunk with its pre-computed embedding."""
        self.index_chunk_segments(
            chunk,
            [
                ChunkSegment(
                    text=chunk.raw_text,
                    segment_index=0,
                    segment_count=1,
                )
            ],
            [embedding],
        )

    def index_chunk_segments(
        self,
        chunk: SectionChunk | TableChunk,
        segments: list[ChunkSegment],
        embeddings: list[list[float]],
        *,
        replace_existing: bool = True,
    ) -> None:
        """Insert segmented vector rows for one processed source chunk."""
        if len(segments) != len(embeddings):
            raise ValueError(
                "segments/embeddings length mismatch: "
                f"{len(segments)} != {len(embeddings)}"
            )
        if not segments:
            return

        source_chunk_id = str(chunk.chunk_id)
        source_doc_id = str(chunk.doc_id)
        source_kind = "section" if isinstance(chunk, SectionChunk) else "table"

        if replace_existing and self._table is not None:
            try:
                self._table.delete(
                    f'source_chunk_id = "{source_chunk_id}" OR chunk_id = "{source_chunk_id}"'
                )
            except Exception:
                # Backward-compatible fallback for old schema rows.
                self._table.delete(f'chunk_id = "{source_chunk_id}"')

        rows: list[dict[str, object]] = []
        for segment, embedding in zip(segments, embeddings):
            row_chunk_id = f"{source_chunk_id}:{segment.segment_index}"
            rows.append(
                {
                    "chunk_id": row_chunk_id,
                    "doc_id": source_doc_id,
                    "kind": source_kind,
                    "section_title": chunk.section_title,
                    "display_text": segment.text,
                    "source_chunk_id": source_chunk_id,
                    "source_doc_id": source_doc_id,
                    "source_kind": source_kind,
                    "segment_id": row_chunk_id,
                    "segment_index": segment.segment_index,
                    "segment_count": segment.segment_count,
                    "vector": embedding,
                }
            )
        self._ensure_table(rows)

    def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 8,
        doc_ids: list[UUID] | None = None,
    ) -> list[tuple[SectionChunk | TableChunk, float]]:
        """Return the top-k chunks most similar to ``query_embedding``.

        Reconstructs lightweight chunk objects from stored metadata.
        """
        if self._table is None:
            return []

        total_rows = max(self.chunk_count, top_k)
        row_limit = min(total_rows, max(top_k * 4, top_k))
        best_hits: dict[str, tuple[SectionChunk | TableChunk, float]] = {}

        while True:
            query = self._table.search(query_embedding).limit(row_limit)
            if doc_ids is not None:
                id_strs = [str(d) for d in doc_ids]
                filter_expr = " OR ".join(
                    f'(doc_id = "{did}" OR source_doc_id = "{did}")' for did in id_strs
                )
                query = query.where(filter_expr)

            try:
                results = query.to_pandas()
            except Exception:
                logger.exception("LanceDB search failed")
                return []

            best_hits = self._collect_best_hits(results)
            if len(best_hits) >= top_k or row_limit >= total_rows or len(results) < row_limit:
                break
            row_limit = min(total_rows, max(row_limit * 2, row_limit + top_k * 4))

        return sorted(best_hits.values(), key=lambda item: item[1], reverse=True)[:top_k]

    def _collect_best_hits(
        self,
        results: pd.DataFrame,
    ) -> dict[str, tuple[SectionChunk | TableChunk, float]]:
        best_hits: dict[str, tuple[SectionChunk | TableChunk, float]] = {}
        for _, row in results.iterrows():
            source_chunk_id = _safe_str(row.get("source_chunk_id")) or _safe_str(
                row.get("chunk_id")
            )
            source_doc_id = _safe_str(row.get("source_doc_id")) or _safe_str(row.get("doc_id"))
            kind = _safe_str(row.get("source_kind")) or _safe_str(row.get("kind"))
            score = float(1.0 - row.get("_distance", 0.0))
            if not source_chunk_id or not source_doc_id:
                continue
            try:
                chunk_id = UUID(source_chunk_id)
                doc_id = UUID(source_doc_id)
            except ValueError:
                logger.warning(
                    "Skipping LanceDB row with invalid lineage ids: chunk=%s, doc=%s",
                    source_chunk_id,
                    source_doc_id,
                )
                continue

            if kind == "section":
                chunk: SectionChunk | TableChunk = SectionChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    section_title=_safe_str(row.get("section_title")),
                    text=_safe_str(row.get("display_text")),
                    token_count=0,
                )
            else:
                chunk = TableChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    section_title=_safe_str(row.get("section_title")),
                    caption="",
                    headers=[],
                    rows=[],
                    raw_text=_safe_str(row.get("display_text")),
                )
            existing = best_hits.get(source_chunk_id)
            if existing is None or score > existing[1]:
                best_hits[source_chunk_id] = (chunk, score)
        return best_hits

    def fetch_lineage_rows(self) -> list[dict[str, object]]:
        """Return lineage-aware row metadata for runtime validation."""
        if self._table is None:
            return []
        try:
            results = self._table.to_pandas()
        except Exception:
            logger.exception("Failed to read LanceDB rows for lineage validation")
            return []

        rows: list[dict[str, object]] = []
        for _, row in results.iterrows():
            source_chunk_id = _safe_str(row.get("source_chunk_id")) or _safe_str(
                row.get("chunk_id")
            )
            source_doc_id = _safe_str(row.get("source_doc_id")) or _safe_str(row.get("doc_id"))
            source_kind = _safe_str(row.get("source_kind")) or _safe_str(row.get("kind"))
            rows.append(
                {
                    "row_chunk_id": _safe_str(row.get("chunk_id")),
                    "source_chunk_id": source_chunk_id,
                    "source_doc_id": source_doc_id,
                    "source_kind": source_kind,
                    "segment_index": _safe_int(row.get("segment_index"), 0),
                    "segment_count": max(1, _safe_int(row.get("segment_count"), 1)),
                }
            )
        return rows

    # -- Metadata -----------------------------------------------------------

    def save_metadata(self, info: dict) -> None:
        """Persist index metadata as a JSON sidecar."""
        info.setdefault("built_at", datetime.now(UTC).isoformat())
        path = self._db_path / _METADATA_FILE
        path.write_text(json.dumps(info, indent=2), encoding="utf-8")

    def load_metadata(self) -> dict | None:
        """Read persisted index metadata, or None if absent."""
        path = self.metadata_path
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to read LanceDB metadata from %s", path)
            return None

    @property
    def metadata_path(self) -> Path:
        return self._db_path / _METADATA_FILE

    @property
    def has_table(self) -> bool:
        return self._table is not None

    @property
    def chunk_count(self) -> int:
        """Return the number of indexed vector rows."""
        if self._table is None:
            return 0
        try:
            return self._table.count_rows()
        except Exception:
            return 0
