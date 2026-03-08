"""Retrieval infrastructure: in-memory repositories, lexical/vector search, hybrid fusion."""

from tesla_finrag.retrieval.hybrid import HybridRetrievalService
from tesla_finrag.retrieval.in_memory import (
    InMemoryCorpusRepository,
    InMemoryEvidenceRepository,
    InMemoryFactsRepository,
    InMemoryQueryPlanRepository,
    InMemoryRetrievalStore,
)
from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore
from tesla_finrag.retrieval.lexical import LexicalSearcher
from tesla_finrag.retrieval.vector import VectorSearcher

__all__ = [
    "HybridRetrievalService",
    "InMemoryCorpusRepository",
    "InMemoryEvidenceRepository",
    "InMemoryFactsRepository",
    "InMemoryQueryPlanRepository",
    "InMemoryRetrievalStore",
    "LanceDBRetrievalStore",
    "LexicalSearcher",
    "VectorSearcher",
]
