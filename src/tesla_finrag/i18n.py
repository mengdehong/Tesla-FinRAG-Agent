"""Internationalization (i18n) support for the Tesla FinRAG workbench.

Provides a locale dictionary and helper functions for switching between
English (``en``) and Simplified Chinese (``zh_CN``) in the Streamlit UI.

Usage in Streamlit::

    from tesla_finrag.i18n import t, SUPPORTED_LOCALES

    locale = st.session_state.get("locale", "en")
    st.header(t(locale, "app_title"))
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Supported locales
# ---------------------------------------------------------------------------

SUPPORTED_LOCALES: Final[dict[str, str]] = {
    "en": "English",
    "zh_CN": "简体中文",
}

DEFAULT_LOCALE: Final[str] = "en"

# ---------------------------------------------------------------------------
# Translation dictionary
# ---------------------------------------------------------------------------

_TRANSLATIONS: Final[dict[str, dict[str, str]]] = {
    # -- App chrome ----------------------------------------------------------
    "app_title": {
        "en": "Tesla FinRAG Evaluation Workbench",
        "zh_CN": "Tesla 金融 RAG 评估工作台",
    },
    "app_description": {
        "en": (
            "Ask financial questions about Tesla's SEC filings in the processed runtime corpus. "
            "The workbench runs the real planning, retrieval, and answer-composition "
            "services, then shows grounded answers with citations, calculation traces, "
            "and retrieval diagnostics."
        ),
        "zh_CN": (
            "针对已处理的 Tesla SEC 财报语料库提出金融问题。"
            "工作台运行真实的规划、检索和答案组合服务，"
            "然后显示带有引文、计算步骤和检索诊断信息的有据回答。"
        ),
    },
    # -- Sidebar -------------------------------------------------------------
    "sidebar_runtime": {
        "en": "Runtime",
        "zh_CN": "运行时",
    },
    "sidebar_provider": {
        "en": "Provider",
        "zh_CN": "推理后端",
    },
    "provider_local": {
        "en": "local (Ollama)",
        "zh_CN": "本地 (Ollama)",
    },
    "provider_remote": {
        "en": "remote (OpenAI-compatible)",
        "zh_CN": "远程 (OpenAI 兼容)",
    },
    "sidebar_filing_scope": {
        "en": "Filing Scope",
        "zh_CN": "财报范围",
    },
    "sidebar_fiscal_year": {
        "en": "Fiscal Year",
        "zh_CN": "财年",
    },
    "sidebar_filing_type": {
        "en": "Filing Type",
        "zh_CN": "报告类型",
    },
    "filing_type_both": {
        "en": "Both",
        "zh_CN": "全部",
    },
    "filing_type_annual": {
        "en": "10-K (Annual)",
        "zh_CN": "10-K (年报)",
    },
    "filing_type_quarterly": {
        "en": "10-Q (Quarterly)",
        "zh_CN": "10-Q (季报)",
    },
    "sidebar_quarter": {
        "en": "Quarter (for 10-Q)",
        "zh_CN": "季度 (10-Q)",
    },
    "sidebar_language": {
        "en": "Language / 语言",
        "zh_CN": "语言 / Language",
    },
    "sidebar_version": {
        "en": "Tesla FinRAG Agent v0.1.0",
        "zh_CN": "Tesla FinRAG Agent v0.1.0",
    },
    "sidebar_mode_local": {
        "en": "Mode: processed runtime + local Ollama provider",
        "zh_CN": "模式：预处理运行时 + 本地 Ollama 后端",
    },
    "sidebar_mode_remote": {
        "en": "Mode: processed runtime + remote provider",
        "zh_CN": "模式：预处理运行时 + 远程后端",
    },
    "sidebar_corpus_years": {
        "en": "Corpus years",
        "zh_CN": "语料库年份",
    },
    # -- Query input ---------------------------------------------------------
    "question_label": {
        "en": "Your question",
        "zh_CN": "您的问题",
    },
    "question_placeholder": {
        "en": "e.g. What was Tesla's total revenue in FY2023 and how did it compare to FY2022?",
        "zh_CN": "例如：Tesla 2023 财年总营收是多少？与 2022 财年相比如何？",
    },
    "run_query": {
        "en": "Run Query",
        "zh_CN": "执行查询",
    },
    "warn_select_year": {
        "en": "Select at least one fiscal year before running a query.",
        "zh_CN": "请至少选择一个财年再执行查询。",
    },
    "warn_enter_question": {
        "en": "Please enter a question before submitting.",
        "zh_CN": "请输入问题后再提交。",
    },
    "running_pipeline": {
        "en": "Running pipeline...",
        "zh_CN": "正在运行管线...",
    },
    "pipeline_error": {
        "en": "The pipeline failed while answering this question",
        "zh_CN": "管线在回答此问题时发生了错误",
    },
    "init_error": {
        "en": "Unable to initialize the pipeline",
        "zh_CN": "无法初始化管线",
    },
    # -- Answer section ------------------------------------------------------
    "answer_header": {
        "en": "Answer",
        "zh_CN": "回答",
    },
    "status_label": {
        "en": "Status",
        "zh_CN": "状态",
    },
    "confidence_label": {
        "en": "Confidence",
        "zh_CN": "置信度",
    },
    # -- Citations -----------------------------------------------------------
    "citations_header": {
        "en": "Citations",
        "zh_CN": "引文来源",
    },
    "no_citations": {
        "en": "No citations available.",
        "zh_CN": "暂无引文。",
    },
    "citation_period_end": {
        "en": "period ending",
        "zh_CN": "截止日期",
    },
    "citation_section": {
        "en": "Section",
        "zh_CN": "章节",
    },
    "citation_page": {
        "en": "Page",
        "zh_CN": "页码",
    },
    "citation_source_prefix": {
        "en": "Source",
        "zh_CN": "来源",
    },
    # -- Calculation steps ---------------------------------------------------
    "calc_steps_header": {
        "en": "Calculation Steps",
        "zh_CN": "计算步骤",
    },
    "no_calc_steps": {
        "en": "No calculation steps for this query.",
        "zh_CN": "此查询无计算步骤。",
    },
    # -- Financial charts ----------------------------------------------------
    "financial_charts_header": {
        "en": "Financial Data Trends",
        "zh_CN": "财务数据趋势",
    },
    "chart_unit_usd_millions": {
        "en": "USD (millions)",
        "zh_CN": "美元（百万）",
    },
    "chart_period": {
        "en": "Period",
        "zh_CN": "期间",
    },
    "chart_value": {
        "en": "Value",
        "zh_CN": "金额",
    },
    "chart_concept": {
        "en": "Metric",
        "zh_CN": "指标",
    },
    "no_xbrl_data": {
        "en": "No structured XBRL data available for charting.",
        "zh_CN": "无可用的 XBRL 结构化数据用于图表展示。",
    },
    # -- Retrieval debug -----------------------------------------------------
    "retrieval_debug_header": {
        "en": "Retrieval Debug",
        "zh_CN": "检索调试",
    },
    "tab_query_plan": {
        "en": "Query Plan",
        "zh_CN": "查询计划",
    },
    "tab_evidence_chunks": {
        "en": "Evidence Chunks",
        "zh_CN": "证据片段",
    },
    "tab_scores_metadata": {
        "en": "Scores & Metadata",
        "zh_CN": "评分 & 元数据",
    },
    "query_type": {
        "en": "Query type",
        "zh_CN": "查询类型",
    },
    "keywords": {
        "en": "Keywords",
        "zh_CN": "关键词",
    },
    "none_placeholder": {
        "en": "(none)",
        "zh_CN": "(无)",
    },
    "required_periods": {
        "en": "Required periods",
        "zh_CN": "所需期间",
    },
    "required_concepts": {
        "en": "Required concepts",
        "zh_CN": "所需概念",
    },
    "sub_questions": {
        "en": "Sub-questions",
        "zh_CN": "子问题",
    },
    "section_chunks_count": {
        "en": "Section chunks",
        "zh_CN": "章节片段",
    },
    "table_chunks_count": {
        "en": "Table chunks",
        "zh_CN": "表格片段",
    },
    "structured_facts_count": {
        "en": "Structured facts",
        "zh_CN": "结构化事实",
    },
    "retrieval_scores_label": {
        "en": "Retrieval scores (chunk_id -> relevance)",
        "zh_CN": "检索分数 (chunk_id -> 相关性)",
    },
    "bundle_metadata_label": {
        "en": "Bundle metadata",
        "zh_CN": "证据包元数据",
    },
    "answer_retrieval_debug_label": {
        "en": "Answer retrieval debug",
        "zh_CN": "回答检索调试",
    },
    # -- XBRL concept display labels -----------------------------------------
    "concept:us-gaap:Revenues": {
        "en": "Total Revenue",
        "zh_CN": "总营收",
    },
    "concept:us-gaap:GrossProfit": {
        "en": "Gross Profit",
        "zh_CN": "毛利润",
    },
    "concept:us-gaap:OperatingIncomeLoss": {
        "en": "Operating Income",
        "zh_CN": "营业利润",
    },
    "concept:custom:FreeCashFlow": {
        "en": "Free Cash Flow",
        "zh_CN": "自由现金流",
    },
    "concept:us-gaap:CostOfGoodsAndServicesSold": {
        "en": "Cost of Goods Sold",
        "zh_CN": "销售成本",
    },
    "concept:us-gaap:NetIncomeLoss": {
        "en": "Net Income",
        "zh_CN": "净利润",
    },
    "concept:custom:CapitalExpenditure": {
        "en": "Capital Expenditure",
        "zh_CN": "资本支出",
    },
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def t(locale: str, key: str, **kwargs: object) -> str:
    """Translate *key* to the given *locale*.

    Falls back to English when the locale or key is not found.
    Supports ``str.format`` substitution via keyword arguments.
    """
    entry = _TRANSLATIONS.get(key)
    if entry is None:
        return key
    text = entry.get(locale, entry.get("en", key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def concept_label(locale: str, concept: str) -> str:
    """Return a human-readable label for an XBRL concept, localized."""
    key = f"concept:{concept}"
    entry = _TRANSLATIONS.get(key)
    if entry is None:
        # Fallback: strip namespace prefix and title-case
        short = concept.rsplit(":", 1)[-1] if ":" in concept else concept
        # Convert CamelCase to spaced words
        import re

        return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", short)
    return entry.get(locale, entry.get("en", concept))


# ---------------------------------------------------------------------------
# LLM response language directive
# ---------------------------------------------------------------------------


def response_language_directive(locale: str) -> str | None:
    """Return a system-level instruction for forcing the LLM output language.

    Returns ``None`` for English (the default LLM output language).
    """
    if locale == "zh_CN":
        return (
            "Output answers and explicit calculation steps exclusively "
            "in Simplified Chinese (简体中文). "
            "Use Chinese financial terminology where applicable. "
            "Translate all section names, metric labels, and connective text into Chinese. "
            "Keep original English proper nouns (e.g. Tesla, SEC, XBRL) unchanged."
        )
    return None
