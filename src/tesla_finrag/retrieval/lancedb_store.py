"""LanceDB-backed retrieval store for persistent vector search.

Implements the :class:`RetrievalStore` contract using a file-backed LanceDB
database under ``data/processed/lancedb``.  Chunk rows are keyed by stable
``chunk_id`` and support filtered similarity search by ``doc_id`` and chunk
kind.

Index metadata (embedding model, dimensions, build timestamp) is persisted
as a JSON sidecar so the runtime can detect incompatible indexes at startup.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import lancedb

from tesla_finrag.models import SectionChunk, TableChunk
from tesla_finrag.repositories import RetrievalStore

logger = logging.getLogger(__name__)

# Arrow schema for the chunks table.
_CHUNKS_TABLE = "chunks"
_METADATA_FILE = "_index_metadata.json"


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
        self._table.delete(f'doc_id = "{doc_id_str}"')

    def clear(self) -> None:
        """Remove every indexed row while keeping the DB directory in place."""
        if self._table is None:
            return
        self._table.delete("true")

    # -- RetrievalStore interface -------------------------------------------

    def index_section_chunk(self, chunk: SectionChunk, embedding: list[float]) -> None:
        """Add or update a section chunk with its pre-computed embedding."""
        self._upsert_chunk(
            chunk_id=str(chunk.chunk_id),
            doc_id=str(chunk.doc_id),
            kind="section",
            section_title=chunk.section_title,
            display_text=chunk.text,
            embedding=embedding,
        )

    def index_table_chunk(self, chunk: TableChunk, embedding: list[float]) -> None:
        """Add or update a table chunk with its pre-computed embedding."""
        self._upsert_chunk(
            chunk_id=str(chunk.chunk_id),
            doc_id=str(chunk.doc_id),
            kind="table",
            section_title=chunk.section_title,
            display_text=chunk.raw_text,
            embedding=embedding,
        )

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

        query = self._table.search(query_embedding).limit(top_k)

        if doc_ids is not None:
            id_strs = [str(d) for d in doc_ids]
            filter_expr = " OR ".join(f'doc_id = "{did}"' for did in id_strs)
            query = query.where(filter_expr)

        try:
            results = query.to_pandas()
        except Exception:
            logger.exception("LanceDB search failed")
            return []

        hits: list[tuple[SectionChunk | TableChunk, float]] = []
        for _, row in results.iterrows():
            chunk_id = UUID(row["chunk_id"])
            doc_id = UUID(row["doc_id"])
            kind = row["kind"]
            score = float(1.0 - row.get("_distance", 0.0))

            if kind == "section":
                chunk: SectionChunk | TableChunk = SectionChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    section_title=row["section_title"],
                    text=row["display_text"],
                    token_count=0,
                )
            else:
                chunk = TableChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    section_title=row["section_title"],
                    caption="",
                    headers=[],
                    rows=[],
                    raw_text=row["display_text"],
                )
            hits.append((chunk, score))

        return hits

    # -- Upsert helper ------------------------------------------------------

    def _upsert_chunk(
        self,
        *,
        chunk_id: str,
        doc_id: str,
        kind: str,
        section_title: str,
        display_text: str,
        embedding: list[float],
    ) -> None:
        """Insert or replace a chunk row by chunk_id."""
        row = {
            "chunk_id": chunk_id,
            "doc_id": doc_id,
            "kind": kind,
            "section_title": section_title,
            "display_text": display_text,
            "vector": embedding,
        }
        # Remove existing row with the same chunk_id, then add
        if self._table is not None:
            try:
                self._table.delete(f'chunk_id = "{chunk_id}"')
            except Exception:
                pass  # Table might be empty or row might not exist
            self._table.add([row])
        else:
            self._ensure_table([row])

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
        """Return the number of indexed chunks."""
        if self._table is None:
            return 0
        try:
            return self._table.count_rows()
        except Exception:
            return 0
