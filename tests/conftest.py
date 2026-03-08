"""Shared fixtures for integration tests.

Provides a pre-populated in-memory corpus with Tesla filing data
suitable for testing the full pipeline.
"""

from __future__ import annotations

from datetime import date

import pytest

def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration", action="store_true", default=False, help="run integration tests"
    )
    parser.addoption(
        "--run-slow", action="store_true", default=False, help="run slow tests"
    )

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_integration = pytest.mark.skip(reason="need --run-integration option to run")
    skip_slow = pytest.mark.skip(reason="need --run-slow option to run")
    
    for item in items:
        if "integration" in item.keywords and not config.getoption("--run-integration"):
            item.add_marker(skip_integration)
        if "slow" in item.keywords and not config.getoption("--run-slow"):
            item.add_marker(skip_slow)

from tesla_finrag.answer.composer import GroundedAnswerComposer
from tesla_finrag.calculation.calculator import StructuredCalculator
from tesla_finrag.evidence.linker import EvidenceLinker
from tesla_finrag.models import (
    FactRecord,
    FilingDocument,
    FilingType,
    SectionChunk,
    TableChunk,
)
from tesla_finrag.planning.query_planner import RuleBasedQueryPlanner
from tesla_finrag.retrieval.hybrid import HybridRetrievalService
from tesla_finrag.retrieval.in_memory import (
    InMemoryCorpusRepository,
    InMemoryFactsRepository,
)

# ---------------------------------------------------------------------------
# Filing documents
# ---------------------------------------------------------------------------


def _make_filing(
    form: FilingType,
    period_end: date,
    fiscal_year: int,
    fiscal_quarter: int | None,
    source: str,
) -> FilingDocument:
    return FilingDocument(
        filing_type=form,
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        accession_number=f"0000950170-{fiscal_year}-{str(period_end.month).zfill(2)}",
        filed_at=date(period_end.year, period_end.month + 1 if period_end.month < 12 else 1, 15),
        source_path=source,
    )


@pytest.fixture()
def corpus_repo() -> InMemoryCorpusRepository:
    """Return an in-memory corpus repository populated with test data."""
    repo = InMemoryCorpusRepository()

    # --- Filings ---
    filings = [
        _make_filing(
            FilingType.QUARTERLY, date(2022, 3, 31), 2022, 1, "data/raw/Tesla_2022_Q1_10-Q.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2022, 6, 30), 2022, 2, "data/raw/Tesla_2022_Q2_10-Q.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2022, 9, 30), 2022, 3, "data/raw/Tesla_2022_Q3_10-Q.pdf"
        ),
        _make_filing(
            FilingType.ANNUAL, date(2022, 12, 31), 2022, None, "data/raw/Tesla_2022_Q4_10-K.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2023, 3, 31), 2023, 1, "data/raw/Tesla_2023_Q1_10-Q.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2023, 6, 30), 2023, 2, "data/raw/Tesla_2023_Q2_10-Q.pdf"
        ),
        _make_filing(
            FilingType.QUARTERLY, date(2023, 9, 30), 2023, 3, "data/raw/Tesla_2023_Q3_10-Q.pdf"
        ),
        _make_filing(
            FilingType.ANNUAL, date(2023, 12, 31), 2023, None, "data/raw/Tesla_2023_Q4_10-K.pdf"
        ),
    ]
    for f in filings:
        repo.upsert_filing(f)

    # --- Section chunks ---
    for filing in filings:
        # MD&A chunk for each filing
        repo.upsert_section_chunk(
            SectionChunk(
                doc_id=filing.doc_id,
                section_title="Management Discussion and Analysis",
                text=_mda_text(filing),
                token_count=50,
                page_number=10,
            )
        )
        # Risk factors chunk
        repo.upsert_section_chunk(
            SectionChunk(
                doc_id=filing.doc_id,
                section_title="Risk Factors",
                text=_risk_text(filing),
                token_count=40,
                page_number=20,
            )
        )

    # --- Table chunks ---
    for filing in filings:
        repo.upsert_table_chunk(
            TableChunk(
                doc_id=filing.doc_id,
                section_title="Consolidated Statements of Operations",
                caption=f"Revenue breakdown for period ending {filing.period_end}",
                headers=["Segment", "Revenue (millions)"],
                rows=_revenue_rows(filing),
                raw_text=_revenue_raw_text(filing),
            )
        )

    return repo


def _mda_text(filing: FilingDocument) -> str:
    """Generate synthetic MD&A text for a filing."""
    q_label = f"Q{filing.fiscal_quarter}" if filing.fiscal_quarter else "full year"
    if filing.fiscal_year == 2022 and filing.fiscal_quarter == 3:
        return (
            f"During {q_label} {filing.fiscal_year}, Tesla experienced significant "
            "supply chain challenges and semiconductor shortages that impacted "
            "production capacity. Despite these headwinds, total automotive revenue "
            "grew year-over-year driven by higher vehicle deliveries and increased "
            "average selling prices."
        )
    if filing.fiscal_year == 2023 and filing.fiscal_quarter == 1:
        return (
            f"In {q_label} {filing.fiscal_year}, Tesla implemented strategic price "
            "reductions across its vehicle lineup to stimulate demand and maintain "
            "market share. This pricing strategy resulted in lower gross margins "
            "compared to the prior quarter but drove record delivery volumes."
        )
    return (
        f"In {q_label} {filing.fiscal_year}, Tesla continued to expand its "
        "manufacturing capacity and delivery infrastructure. The company "
        "focused on operational efficiency and cost reduction initiatives "
        "while investing in new product development and energy storage solutions."
    )


def _risk_text(filing: FilingDocument) -> str:
    """Generate synthetic risk factors text."""
    if filing.fiscal_year == 2022:
        return (
            "Risk factors include supply chain disruptions, semiconductor shortages, "
            "raw material cost increases, and geopolitical uncertainties. Competition "
            "in the electric vehicle market has intensified with traditional automakers "
            "increasing their EV offerings."
        )
    return (
        "Key risks include increasing competition in the EV market, potential "
        "impacts of pricing strategy on margins, regulatory changes, and "
        "macroeconomic conditions affecting consumer demand. Raw material costs "
        "and supply chain reliability remain ongoing concerns."
    )


def _revenue_rows(filing: FilingDocument) -> list[list[str]]:
    """Generate synthetic revenue table rows."""
    data = {
        (2022, 1): [["Automotive", "16,861"], ["Energy", "616"], ["Services", "1,279"]],
        (2022, 2): [["Automotive", "14,602"], ["Energy", "866"], ["Services", "1,466"]],
        (2022, 3): [["Automotive", "18,692"], ["Energy", "1,117"], ["Services", "1,645"]],
        (2022, None): [["Automotive", "71,462"], ["Energy", "3,909"], ["Services", "6,091"]],
        (2023, 1): [["Automotive", "19,963"], ["Energy", "1,529"], ["Services", "1,837"]],
        (2023, 2): [["Automotive", "21,268"], ["Energy", "1,509"], ["Services", "2,150"]],
        (2023, 3): [["Automotive", "19,625"], ["Energy", "1,559"], ["Services", "2,166"]],
        (2023, None): [["Automotive", "82,419"], ["Energy", "6,035"], ["Services", "8,319"]],
    }
    return data.get((filing.fiscal_year, filing.fiscal_quarter), [["Automotive", "0"]])


def _revenue_raw_text(filing: FilingDocument) -> str:
    """Generate raw text representation of the revenue table."""
    rows = _revenue_rows(filing)
    lines = ["Segment | Revenue (millions)"]
    for row in rows:
        lines.append(" | ".join(row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Facts repository
# ---------------------------------------------------------------------------


@pytest.fixture()
def facts_repo(corpus_repo: InMemoryCorpusRepository) -> InMemoryFactsRepository:
    """Return an in-memory facts repository populated with revenue data."""
    repo = InMemoryFactsRepository()
    filings = corpus_repo.list_filings()

    revenue_data = {
        date(2022, 3, 31): 18_756.0,
        date(2022, 6, 30): 16_934.0,
        date(2022, 9, 30): 21_454.0,
        date(2022, 12, 31): 81_462.0,
        date(2023, 3, 31): 23_329.0,
        date(2023, 6, 30): 24_927.0,
        date(2023, 9, 30): 23_350.0,
        date(2023, 12, 31): 96_773.0,
    }

    gross_profit_data = {
        date(2022, 3, 31): 5_539.0,
        date(2022, 6, 30): 4_234.0,
        date(2022, 9, 30): 5_382.0,
        date(2022, 12, 31): 20_853.0,
        date(2023, 3, 31): 4_511.0,
        date(2023, 6, 30): 4_533.0,
        date(2023, 9, 30): 4_178.0,
        date(2023, 12, 31): 17_660.0,
    }

    operating_income_data = {
        date(2022, 3, 31): 3_600.0,
        date(2022, 6, 30): 2_464.0,
        date(2022, 9, 30): 3_688.0,
        date(2022, 12, 31): 13_656.0,
        date(2023, 3, 31): 2_664.0,
        date(2023, 6, 30): 2_399.0,
        date(2023, 9, 30): 1_764.0,
        date(2023, 12, 31): 8_891.0,
    }

    fcf_data = {
        date(2022, 3, 31): 2_228.0,
        date(2022, 6, 30): 621.0,
        date(2022, 9, 30): 3_297.0,
        date(2022, 12, 31): 7_566.0,
        date(2023, 3, 31): 441.0,
        date(2023, 6, 30): 1_007.0,
        date(2023, 9, 30): 848.0,
        date(2023, 12, 31): 4_358.0,
    }

    for filing in filings:
        pe = filing.period_end
        if pe in revenue_data:
            repo.upsert_fact(
                FactRecord(
                    doc_id=filing.doc_id,
                    concept="us-gaap:Revenues",
                    label="Total Revenues",
                    value=revenue_data[pe],
                    unit="USD",
                    scale=1_000_000,
                    period_start=date(pe.year, 1, 1) if filing.fiscal_quarter is None else None,
                    period_end=pe,
                )
            )
        if pe in gross_profit_data:
            repo.upsert_fact(
                FactRecord(
                    doc_id=filing.doc_id,
                    concept="us-gaap:GrossProfit",
                    label="Gross Profit",
                    value=gross_profit_data[pe],
                    unit="USD",
                    scale=1_000_000,
                    period_end=pe,
                )
            )
        if pe in operating_income_data:
            repo.upsert_fact(
                FactRecord(
                    doc_id=filing.doc_id,
                    concept="us-gaap:OperatingIncomeLoss",
                    label="Operating Income",
                    value=operating_income_data[pe],
                    unit="USD",
                    scale=1_000_000,
                    period_end=pe,
                )
            )
        if pe in fcf_data:
            repo.upsert_fact(
                FactRecord(
                    doc_id=filing.doc_id,
                    concept="custom:FreeCashFlow",
                    label="Free Cash Flow",
                    value=fcf_data[pe],
                    unit="USD",
                    scale=1_000_000,
                    period_end=pe,
                )
            )

    return repo


# ---------------------------------------------------------------------------
# Pipeline components
# ---------------------------------------------------------------------------


@pytest.fixture()
def planner() -> RuleBasedQueryPlanner:
    return RuleBasedQueryPlanner()


@pytest.fixture()
def calculator() -> StructuredCalculator:
    return StructuredCalculator()


@pytest.fixture()
def retrieval_service(
    corpus_repo: InMemoryCorpusRepository,
    facts_repo: InMemoryFactsRepository,
) -> HybridRetrievalService:
    return HybridRetrievalService(
        corpus_repo=corpus_repo,
        facts_repo=facts_repo,
        retrieval_store=None,  # lexical-only for integration tests
    )


@pytest.fixture()
def linker(
    corpus_repo: InMemoryCorpusRepository,
    facts_repo: InMemoryFactsRepository,
) -> EvidenceLinker:
    return EvidenceLinker(corpus_repo=corpus_repo, facts_repo=facts_repo)


@pytest.fixture()
def composer(
    corpus_repo: InMemoryCorpusRepository,
    facts_repo: InMemoryFactsRepository,
    calculator: StructuredCalculator,
    linker: EvidenceLinker,
) -> GroundedAnswerComposer:
    return GroundedAnswerComposer(
        corpus_repo=corpus_repo,
        facts_repo=facts_repo,
        calculator=calculator,
        linker=linker,
    )
