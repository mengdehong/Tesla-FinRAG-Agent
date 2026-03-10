from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

from tesla_finrag.agent import FinancialQaAgent
from tesla_finrag.answer.composer import GroundedAnswerComposer
from tesla_finrag.concepts.catalog import build_companyfacts_catalog
from tesla_finrag.concepts.resolver import SemanticConceptResolver
from tesla_finrag.evaluation.workbench import FilingScope, ProviderMode, WorkbenchPipeline
from tesla_finrag.models import (
    AgentEventType,
    AnswerPayload,
    AnswerShape,
    AnswerStatus,
    ConceptCatalogEntry,
    EvidenceBundle,
    FactRecord,
    FilingDocument,
    FilingType,
    QueryLanguage,
    QueryPlan,
    SectionChunk,
    TableChunk,
)
from tesla_finrag.planning import FastPathPlanner, LLMQueryPlanner, RuleBasedQueryPlanner
from tesla_finrag.planning.query_planner import detect_query_language
from tesla_finrag.retrieval import InMemoryCorpusRepository, InMemoryFactsRepository
from tesla_finrag.settings import AppSettings


class _FakeEmbeddingBackend:
    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._mapping.get(text, [0.0, 0.0]) for text in texts]


class _FakeProvider:
    def __init__(self, structured_response: dict[str, object] | None = None) -> None:
        self._structured_response = structured_response or {}
        self.info = type(
            "Info",
            (),
            {
                "provider_name": "fake",
                "provider_mode": "local",
                "answer_model": "fake-model",
                "embedding_model": "fake-embedding",
                "base_url": None,
            },
        )()

    def generate_structured_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, object] | None = None,
    ) -> dict[str, object]:
        _ = system_prompt, user_prompt, json_schema
        if "value and label" in system_prompt.lower():
            return {"value": 42, "label": "Mystery Metric"}
        return dict(self._structured_response)

    def generate_grounded_answer(
        self,
        question: str,
        evidence: str,
        calculation_trace: list[str] | None = None,
        response_language: str | None = None,
    ) -> str:
        _ = question, evidence, calculation_trace, response_language
        return "Fake narrated answer."


class _StaticPlanner:
    def __init__(self, plan: QueryPlan) -> None:
        self._plan = plan

    def plan(self, question: str) -> QueryPlan:
        _ = question
        return self._plan


def test_semantic_resolver_stays_conservative_when_uncalibrated() -> None:
    entries = [
        ConceptCatalogEntry(
            concept="us-gaap:CostOfRevenue",
            label="Cost of Revenue",
            namespace="us-gaap",
            local_name="CostOfRevenue",
            generated_aliases=["cost of revenue"],
            embedding_text="cost of revenue",
        ),
        ConceptCatalogEntry(
            concept="us-gaap:Revenues",
            label="Revenues",
            namespace="us-gaap",
            local_name="Revenues",
            generated_aliases=["revenue"],
            embedding_text="revenue",
        ),
    ]
    backend = _FakeEmbeddingBackend(
        {
            "cost of automotive revenue": [1.0, 0.0],
            "cost of revenue": [1.0, 0.0],
            "revenue": [0.0, 1.0],
        }
    )
    resolver = SemanticConceptResolver(
        entries,
        embedding_backend=backend,
        calibrated=False,
    )

    resolution = resolver.resolve_mention("cost of automotive revenue")

    assert resolution.accepted is False
    assert resolution.method.value == "unresolved"
    assert resolution.candidates[0].concept == "us-gaap:CostOfRevenue"


def test_llm_query_planner_falls_back_when_confidence_is_low() -> None:
    resolver = SemanticConceptResolver(build_companyfacts_catalog(), calibrated=False)
    planner = LLMQueryPlanner(
        provider=_FakeProvider(
            {
                "metric_mentions": ["automotive cost"],
                "planner_confidence": 0.2,
            }
        ),
        concept_resolver=resolver,
        settings=AppSettings(
            planner_mode="llm_fallback",
            planner_min_confidence=0.65,
            _env_file=None,  # type: ignore[call-arg]
        ),
    )

    plan = planner.plan("How did cost of automotive revenue change between FY2022 and FY2023?")

    assert plan.planner_mode == "llm_fallback"
    assert "us-gaap:CostOfGoodsAndServicesSold" in plan.required_concepts


def test_llm_query_planner_coerces_dict_mentions_and_named_confidence() -> None:
    resolver = SemanticConceptResolver(build_companyfacts_catalog(), calibrated=False)
    planner = LLMQueryPlanner(
        provider=_FakeProvider(
            {
                "metric_mentions": {
                    "public_float": "Tesla's public float",
                    "accounts_payable_current": "Accounts Payable Current",
                },
                "required_periods": ["2024-12-31"],
                "planner_confidence": "High",
            }
        ),
        concept_resolver=resolver,
        settings=AppSettings(
            planner_mode="llm_fallback",
            planner_min_confidence=0.65,
            _env_file=None,  # type: ignore[call-arg]
        ),
    )

    plan = planner.plan("What was Tesla's public float according to the 2024 annual filing?")

    assert plan.planner_mode == "llm"
    assert plan.planner_confidence == 0.9
    assert "public float" in [mention.lower() for mention in plan.metric_mentions]
    assert "dei:EntityPublicFloat" in plan.required_concepts


def test_semantic_resolver_ignores_tesla_possessive_noise() -> None:
    resolver = SemanticConceptResolver(build_companyfacts_catalog(), calibrated=False)

    resolution = resolver.resolve_mention("Tesla's public float")

    assert resolution.accepted is True
    assert resolution.concept == "dei:EntityPublicFloat"


def test_semantic_resolver_force_maps_cost_of_revenue_mentions_to_cogs() -> None:
    resolver = SemanticConceptResolver(build_companyfacts_catalog(), calibrated=False)

    english_resolution = resolver.resolve_mention("cost of revenue")
    chinese_resolution = resolver.resolve_mention("营业成本")
    unresolved_resolution = resolver.resolve_mention("供应链风险")

    assert english_resolution.accepted is True
    assert english_resolution.concept == "us-gaap:CostOfGoodsAndServicesSold"
    assert chinese_resolution.accepted is True
    assert chinese_resolution.concept == "us-gaap:CostOfGoodsAndServicesSold"
    assert unresolved_resolution.accepted is False
    assert unresolved_resolution.concept is None


def test_llm_query_planner_appends_narrative_subquery_for_composite_questions() -> None:
    resolver = SemanticConceptResolver(build_companyfacts_catalog(), calibrated=False)
    planner = LLMQueryPlanner(
        provider=_FakeProvider(
            {
                "metric_mentions": ["accounts payable current"],
                "required_periods": ["2024-12-31"],
                "answer_shape": "composite",
                "planner_confidence": 0.9,
            }
        ),
        concept_resolver=resolver,
        settings=AppSettings(_env_file=None),  # type: ignore[call-arg]
    )

    plan = planner.plan(
        "What geopolitical risks did Tesla mention in 2024, "
        "and what was accounts payable current at year-end?"
    )

    assert plan.answer_shape.value == "composite"
    assert any(not sub_query.target_concepts for sub_query in plan.sub_queries)


def test_llm_query_planner_keeps_rule_concepts_for_composite_when_llm_drifted() -> None:
    resolver = SemanticConceptResolver(build_companyfacts_catalog(), calibrated=False)
    planner = LLMQueryPlanner(
        provider=_FakeProvider(
            {
                "metric_mentions": ["cash"],
                "required_periods": ["2022-12-31", "2023-12-31"],
                "answer_shape": "composite",
                "planner_confidence": 0.9,
            }
        ),
        concept_resolver=resolver,
        settings=AppSettings(_env_file=None),  # type: ignore[call-arg]
    )

    plan = planner.plan(
        "2023年10-K中，特斯拉提到了哪些供应链风险？FY2022到FY2023汽车销售成本如何变化？"
    )

    assert plan.answer_shape == AnswerShape.COMPOSITE
    assert "us-gaap:CostOfGoodsAndServicesSold" in plan.required_concepts
    assert "us-gaap:CashAndCashEquivalentsAtCarryingValue" not in plan.required_concepts


def test_llm_query_planner_overrides_query_language_from_detected_question_language() -> None:
    resolver = SemanticConceptResolver(build_companyfacts_catalog(), calibrated=False)
    planner = LLMQueryPlanner(
        provider=_FakeProvider(
            {
                "metric_mentions": ["gross profit", "revenue"],
                "required_periods": ["2023-12-31"],
                "answer_shape": "single_value",
                "planner_confidence": 0.9,
            }
        ),
        concept_resolver=resolver,
        settings=AppSettings(_env_file=None),  # type: ignore[call-arg]
    )

    plan = planner.plan("特斯拉FY2023的毛利率是多少？请展示毛利润除以总营收的计算过程。")

    assert plan.query_language == detect_query_language(plan.original_query)


def test_fast_path_planner_skips_llm_for_simple_rule_hit() -> None:
    llm_planner = MagicMock()
    planner = FastPathPlanner(
        rule_planner=RuleBasedQueryPlanner(),
        llm_planner=llm_planner,
    )

    plan = planner.plan("What was Tesla's total revenue in FY2023?")

    assert plan.planner_mode == "rule_fast_path"
    llm_planner.plan.assert_not_called()


def test_rule_planner_marks_split_narrative_numeric_question_as_composite() -> None:
    planner = RuleBasedQueryPlanner()

    plan = planner.plan(
        "What geopolitical risks did Tesla mention in 2024, "
        "and what was accounts payable current at year-end?"
    )

    assert plan.answer_shape.value == "composite"
    narrative_sub_queries = [
        sub_query for sub_query in plan.sub_queries if not sub_query.target_concepts
    ]
    assert narrative_sub_queries
    assert "geopolitical" in narrative_sub_queries[0].search_text.lower()


def test_grounded_answer_composer_adds_geopolitical_summary_for_composite_risk_questions() -> None:
    corpus_repo = InMemoryCorpusRepository()
    facts_repo = InMemoryFactsRepository()
    filing = FilingDocument(
        filing_type=FilingType.ANNUAL,
        period_end=date(2024, 12, 31),
        fiscal_year=2024,
        fiscal_quarter=None,
        accession_number="0000950170-2024-12",
        filed_at=date(2025, 1, 29),
        source_path="data/raw/Tesla_2024_全年_10-K.pdf",
    )
    corpus_repo.upsert_filing(filing)
    risk_chunk = SectionChunk(
        doc_id=filing.doc_id,
        section_title="Item 1A. RISK FACTORS",
        text=(
            "Changes in trade policy, tariffs, export controls and other restrictions "
            "may affect our global supply chain cost structure and demand."
        ),
        token_count=40,
        page_number=12,
    )
    corpus_repo.upsert_section_chunk(risk_chunk)
    fact = FactRecord(
        fact_id=uuid4(),
        doc_id=filing.doc_id,
        concept="us-gaap:AccountsPayableCurrent",
        label="Accounts Payable, Current",
        value=12474000000,
        unit="USD",
        scale=1,
        period_start=None,
        period_end=date(2024, 12, 31),
        is_instant=True,
    )
    facts_repo.upsert_fact(fact)
    composer = GroundedAnswerComposer(corpus_repo=corpus_repo, facts_repo=facts_repo)
    plan = QueryPlan(
        original_query=(
            "What geopolitical risks did Tesla mention in 2024, "
            "and what was accounts payable current at year-end?"
        ),
        query_language=QueryLanguage.ENGLISH,
        answer_shape=AnswerShape.COMPOSITE,
        required_periods=[date(2024, 12, 31)],
        period_semantics={"2024-12-31": "instant"},
        required_concepts=["us-gaap:AccountsPayableCurrent"],
        needs_calculation=False,
    )
    bundle = EvidenceBundle(
        plan_id=plan.plan_id,
        section_chunks=[risk_chunk],
        facts=[fact],
    )

    answer = composer.answer(plan, bundle)

    assert "geopolitical risks mentioned include" in answer.answer_text.lower()


def test_workbench_prefers_local_answer_when_remote_drops_query_cues() -> None:
    plan = QueryPlan(
        original_query="比较特斯拉FY2022和FY2023的总营收，同比增长率是多少？",
        query_language=QueryLanguage.MIXED,
        normalized_query="compare 总营收 growth rate",
        required_concepts=["us-gaap:Revenues"],
    )
    local_answer = AnswerPayload(
        plan_id=plan.plan_id,
        status=AnswerStatus.OK,
        answer_text="根据 Tesla SEC 财报：\n总营收结果：\n结果: 18.80",
    )

    assert (
        WorkbenchPipeline._should_use_local_answer(
            plan,
            EvidenceBundle(plan_id=plan.plan_id),
            "The revenue increased by 18.80%.",
            local_answer,
        )
        is True
    )


def test_financial_qa_agent_halts_without_repeating_same_action() -> None:
    corpus_repo = InMemoryCorpusRepository()
    facts_repo = InMemoryFactsRepository()
    filing = FilingDocument(
        filing_type=FilingType.ANNUAL,
        period_end=date(2023, 12, 31),
        fiscal_year=2023,
        fiscal_quarter=None,
        accession_number="0000950170-2023-12",
        filed_at=date(2024, 1, 15),
        source_path="data/raw/Tesla_2023_全年_10-K.pdf",
    )
    corpus_repo.upsert_filing(filing)
    plan = QueryPlan(
        original_query="What was the mystery metric in FY2023?",
        required_periods=[date(2023, 12, 31)],
        required_concepts=["custom:MysteryMetric"],
        alternative_concepts=[],
        needs_calculation=True,
        planner_mode="rule",
    )
    agent = FinancialQaAgent(
        planner=_StaticPlanner(plan),
        corpus_repo=corpus_repo,
        facts_repo=facts_repo,
        settings=AppSettings(
            agent_max_iterations=3,
            enable_llm_table_extraction=False,
            _env_file=None,  # type: ignore[call-arg]
        ),
    )

    _, _, answer = agent.run(plan.original_query)

    assert answer.status in (AnswerStatus.INSUFFICIENT_EVIDENCE, AnswerStatus.OK)
    assert len(answer.retrieval_debug["agent_attempted_signatures"]) == 1
    assert answer.retrieval_debug["agent_halt_reason"] == "exhausted"


def test_financial_qa_agent_finalizes_after_last_iteration_repair() -> None:
    corpus_repo = InMemoryCorpusRepository()
    facts_repo = InMemoryFactsRepository()
    filing = FilingDocument(
        filing_type=FilingType.ANNUAL,
        period_end=date(2023, 12, 31),
        fiscal_year=2023,
        fiscal_quarter=None,
        accession_number="0000950170-2023-12",
        filed_at=date(2024, 1, 15),
        source_path="data/raw/Tesla_2023_全年_10-K.pdf",
    )
    corpus_repo.upsert_filing(filing)
    corpus_repo.upsert_table_chunk(
        TableChunk(
            doc_id=filing.doc_id,
            section_title="Balance Sheet",
            table_markdown="Concept | Value\nMystery Metric | 42",
            raw_text="Mystery Metric 42",
            page_number=12,
        )
    )
    plan = QueryPlan(
        original_query="What was the mystery metric in FY2023?",
        required_periods=[date(2023, 12, 31)],
        required_concepts=["custom:MysteryMetric"],
        alternative_concepts=["custom:FallbackMetric"],
        needs_calculation=True,
        planner_mode="llm",
    )
    agent = FinancialQaAgent(
        planner=_StaticPlanner(plan),
        corpus_repo=corpus_repo,
        facts_repo=facts_repo,
        provider=_FakeProvider(),
        settings=AppSettings(
            agent_max_iterations=3,
            enable_llm_table_extraction=True,
            _env_file=None,  # type: ignore[call-arg]
        ),
    )

    _, _, answer = agent.run(plan.original_query)

    assert answer.retrieval_debug["agent_halt_reason"] == "success"
    assert "custom:FallbackMetric" in answer.retrieval_debug["retrieved_fact_concepts"]


def test_financial_qa_agent_relaxes_period_for_dei_concepts() -> None:
    corpus_repo = InMemoryCorpusRepository()
    facts_repo = InMemoryFactsRepository()
    filing = FilingDocument(
        filing_type=FilingType.ANNUAL,
        period_end=date(2024, 6, 28),
        fiscal_year=2024,
        fiscal_quarter=None,
        accession_number="0000950170-2024-06",
        filed_at=date(2025, 1, 30),
        source_path="data/raw/Tesla_2024_10-K.pdf",
    )
    corpus_repo.upsert_filing(filing)
    facts_repo.upsert_fact(
        FactRecord(
            fact_id=uuid4(),
            doc_id=filing.doc_id,
            concept="dei:EntityPublicFloat",
            label="Entity Public Float",
            value=550170000000,
            unit="USD",
            scale=1,
            period_start=None,
            period_end=date(2024, 6, 28),
            is_instant=True,
        )
    )
    plan = QueryPlan(
        original_query="What was Tesla's public float according to the 2024 annual filing?",
        required_periods=[date(2024, 12, 31)],
        required_concepts=["dei:EntityPublicFloat"],
        needs_calculation=True,
        planner_mode="llm",
    )
    agent = FinancialQaAgent(
        planner=_StaticPlanner(plan),
        corpus_repo=corpus_repo,
        facts_repo=facts_repo,
        settings=AppSettings(
            agent_max_iterations=3,
            enable_llm_table_extraction=False,
            _env_file=None,  # type: ignore[call-arg]
        ),
    )

    _, _, answer = agent.run(plan.original_query)

    assert answer.retrieval_debug["agent_halt_reason"] == "success"
    assert "dei:EntityPublicFloat" in answer.retrieval_debug["retrieved_fact_concepts"]


def test_workbench_pipeline_run_stream_emits_agent_events(tmp_path) -> None:
    corpus_repo = InMemoryCorpusRepository()
    facts_repo = InMemoryFactsRepository()
    filing = FilingDocument(
        filing_type=FilingType.ANNUAL,
        period_end=date(2023, 12, 31),
        fiscal_year=2023,
        fiscal_quarter=None,
        accession_number="0000950170-2023-12",
        filed_at=date(2024, 1, 15),
        source_path="data/raw/Tesla_2023_全年_10-K.pdf",
    )
    corpus_repo.upsert_filing(filing)
    provider = _FakeProvider({"metric_mentions": ["revenue"], "planner_confidence": 0.1})
    indexing_provider = MagicMock()
    indexing_provider.info.embedding_model = "fake-embedding"
    indexing_provider.info.provider_name = "fake"
    indexing_provider.info.base_url = None
    indexing_provider.embed_texts.return_value = [[0.0, 0.0]]
    pipeline = WorkbenchPipeline(
        corpus_repo=corpus_repo,
        facts_repo=facts_repo,
        provider_mode=ProviderMode.LOCAL,
        provider=provider,
        indexing_provider=indexing_provider,
    )

    events = list(
        pipeline.run_stream(
            "What was Tesla's total revenue in FY2023?",
            scope=FilingScope(),
        )
    )

    assert events
    assert events[0].event_type == AgentEventType.PLAN_CREATED
    assert events[-1].event_type == AgentEventType.HALTED
