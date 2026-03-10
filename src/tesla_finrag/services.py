"""Abstract service interfaces for the Tesla FinRAG pipeline.

The pipeline follows the ingest -> retrieve -> calculate -> answer sequence.
Each stage is represented by a service interface here.  Concrete
implementations are provided by later changes.

Services may depend on repositories injected at construction time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from tesla_finrag.models import (
    AnswerPayload,
    EvidenceBundle,
    FactRecord,
    FilingDocument,
    QueryPlan,
)

# ---------------------------------------------------------------------------
# Ingestion service
# ---------------------------------------------------------------------------


class IngestionService(ABC):
    """Orchestrate the parsing, chunking, and indexing of a filing document."""

    @abstractmethod
    def ingest(self, filing: FilingDocument) -> None:
        """Parse ``filing`` and persist all chunks, facts, and embeddings.

        This method is idempotent: calling it twice for the same filing
        MUST NOT create duplicate records.

        Args:
            filing: Metadata record pointing to the source PDF/JSON.
        """

    @abstractmethod
    def ingest_batch(self, filings: list[FilingDocument]) -> None:
        """Ingest multiple filings sequentially or in parallel.

        Default implementations may delegate to :meth:`ingest` in a loop.
        """


# ---------------------------------------------------------------------------
# Query planning service
# ---------------------------------------------------------------------------


class QueryPlanningService(ABC):
    """Decompose a natural-language question into a structured query plan."""

    @abstractmethod
    def plan(self, question: str) -> QueryPlan:
        """Parse ``question`` and return a structured :class:`QueryPlan`.

        The plan identifies required fiscal periods, XBRL concepts, and
        whether an explicit numerical calculation is needed.

        Args:
            question: Raw user question string.

        Returns:
            A populated :class:`QueryPlan` instance.
        """


# ---------------------------------------------------------------------------
# Retrieval service
# ---------------------------------------------------------------------------


class RetrievalService(ABC):
    """Retrieve evidence for a given query plan."""

    @abstractmethod
    def retrieve(self, plan: QueryPlan) -> EvidenceBundle:
        """Fetch relevant chunks and facts for ``plan``.

        Args:
            plan: Structured query plan produced by :class:`QueryPlanningService`.

        Returns:
            An :class:`EvidenceBundle` containing ranked chunks and facts.
        """


# ---------------------------------------------------------------------------
# Calculation service
# ---------------------------------------------------------------------------


class CalculationService(ABC):
    """Perform explicit financial calculations over retrieved facts."""

    @abstractmethod
    def calculate(
        self,
        expression: str,
        facts: list[FactRecord],
    ) -> tuple[float, list[str]]:
        """Evaluate ``expression`` using values from ``facts``.

        Args:
            expression: A symbolic expression referencing XBRL concept names,
                e.g. ``"us-gaap:GrossProfit / us-gaap:Revenue"``.
            facts: The fact records from which to pull numeric values.

        Returns:
            A ``(result, trace)`` tuple where ``trace`` lists the
            arithmetic steps performed.
        """


# ---------------------------------------------------------------------------
# Answer generation service
# ---------------------------------------------------------------------------


class AnswerService(ABC):
    """Compose a final answer from a query plan and its evidence bundle."""

    @abstractmethod
    def answer(self, plan: QueryPlan, bundle: EvidenceBundle) -> AnswerPayload:
        """Generate a cited, structured answer.

        Args:
            plan: The original decomposed query plan.
            bundle: Retrieved evidence (chunks, facts) for the plan.

        Returns:
            A fully populated :class:`AnswerPayload`.
        """
