"""Tesla FinRAG Evaluation Workbench.

Launch locally with::

    uv run streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tesla_finrag.evaluation import FilingScope, ProviderMode, get_workbench_pipeline
from tesla_finrag.evaluation.answer_rendering import render_answer_segments
from tesla_finrag.models import AnswerStatus, FilingType

st.set_page_config(page_title="Tesla FinRAG Workbench", layout="wide")


def _dedupe_column_names(headers: list[str]) -> list[str]:
    """Make table headers Arrow-safe for Streamlit dataframe rendering."""
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for index, header in enumerate(headers, start=1):
        base = (header or "").strip() or f"Column {index}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        deduped.append(base if count == 0 else f"{base} ({count + 1})")
    return deduped


def _table_dataframe(headers: list[str], rows: list[list[str]]) -> pd.DataFrame:
    """Build a dataframe that tolerates duplicate or blank extracted headers."""
    return pd.DataFrame(rows, columns=_dedupe_column_names(headers))


def _resolve_filing_type(label: str) -> FilingType | None:
    if "10-K" in label:
        return FilingType.ANNUAL
    if "10-Q" in label:
        return FilingType.QUARTERLY
    return None


def _quarter_options(quarters: list[int]) -> list[str]:
    return [f"Q{quarter}" for quarter in quarters]


with st.sidebar:
    st.header("Runtime")
    selected_provider = st.radio(
        "Provider",
        options=[mode.value for mode in ProviderMode],
        format_func=lambda value: (
            "local (Ollama)"
            if value == ProviderMode.LOCAL.value
            else "remote (OpenAI-compatible)"
        ),
        index=0,
    )

try:
    pipeline = get_workbench_pipeline(provider_mode=ProviderMode(selected_provider))
except Exception as exc:  # pragma: no cover - exercised in the UI
    from tesla_finrag.guidance import format_corpus_guidance
    from tesla_finrag.runtime import ProcessedCorpusError

    st.title("Tesla FinRAG Evaluation Workbench")
    if isinstance(exc, ProcessedCorpusError):
        st.error(format_corpus_guidance(exc))
    else:
        st.error(f"Unable to initialize the pipeline: {exc}")
    st.stop()

available_years = pipeline.available_years
available_quarters = pipeline.available_quarters
quarter_labels = _quarter_options(available_quarters)

with st.sidebar:
    st.header("Filing Scope")

    fiscal_years: list[int] = st.multiselect(
        "Fiscal Year",
        options=available_years,
        default=available_years[-2:] if len(available_years) > 1 else available_years,
    )

    filing_type_option: str = st.selectbox(
        "Filing Type",
        options=["Both", "10-K (Annual)", "10-Q (Quarterly)"],
        index=0,
    )
    selected_filing_type = _resolve_filing_type(filing_type_option)

    selected_quarter_labels: list[str] = st.multiselect(
        "Quarter (for 10-Q)",
        options=quarter_labels,
        default=quarter_labels,
        disabled=selected_filing_type == FilingType.ANNUAL,
    )

    st.divider()
    st.caption("Tesla FinRAG Agent v0.1.0")
    st.caption(
        "Mode: processed runtime + "
        + ("local Ollama provider" if selected_provider == "local" else "remote provider")
    )
    st.caption(f"Corpus years: {', '.join(str(year) for year in available_years)}")

st.title("Tesla FinRAG Evaluation Workbench")
st.markdown(
    "Ask financial questions about Tesla's SEC filings in the processed runtime corpus. "
    "The workbench runs the real planning, retrieval, and answer-composition "
    "services, then shows grounded answers with citations, calculation traces, "
    "and retrieval diagnostics."
)

question = st.text_area(
    "Your question",
    placeholder="e.g. What was Tesla's total revenue in FY2023 and how did it compare to FY2022?",
    height=100,
)

run_clicked = st.button("Run Query", type="primary", width="stretch")

if run_clicked and question.strip():
    if not fiscal_years:
        st.warning("Select at least one fiscal year before running a query.")
        st.stop()

    quarter_numbers = tuple(
        int(label.removeprefix("Q")) for label in selected_quarter_labels if label.startswith("Q")
    )
    scope = FilingScope(
        fiscal_years=tuple(sorted(fiscal_years)),
        filing_type=selected_filing_type,
        quarters=(quarter_numbers if selected_filing_type == FilingType.QUARTERLY else ()),
    )

    try:
        with st.spinner("Running pipeline..."):
            plan, bundle, answer = pipeline.run(question.strip(), scope=scope)
    except Exception as exc:  # pragma: no cover - exercised in the UI
        st.error(f"The pipeline failed while answering this question: {exc}")
        st.stop()

    st.subheader("Answer")

    status_colors: dict[AnswerStatus, str] = {
        AnswerStatus.OK: "green",
        AnswerStatus.INSUFFICIENT_EVIDENCE: "orange",
        AnswerStatus.CALCULATION_ERROR: "red",
        AnswerStatus.OUT_OF_SCOPE: "gray",
    }
    color = status_colors.get(answer.status, "gray")
    confidence = f"{answer.confidence:.0%}" if answer.confidence is not None else "N/A"

    col_status, col_confidence = st.columns(2)
    with col_status:
        st.markdown(f"**Status:** :{color}[{answer.status.value}]")
    with col_confidence:
        st.markdown(f"**Confidence:** {confidence}")

    render_answer_segments(
        answer.answer_text,
        markdown_renderer=st.markdown,
        latex_renderer=st.latex,
        plain_text_renderer=st.text,
    )

    with st.expander("Citations", expanded=True):
        if answer.citations:
            for idx, citation in enumerate(answer.citations, start=1):
                st.markdown(
                    f"**[{idx}]** {citation.filing_type.value} "
                    f"- period ending {citation.period_end}"
                )
                st.caption(citation.excerpt)
        else:
            st.info("No citations available.")

    with st.expander("Calculation Steps"):
        if answer.calculation_trace:
            for step in answer.calculation_trace:
                st.markdown(f"- {step}")
        else:
            st.info("No calculation steps for this query.")

    with st.expander("Retrieval Debug"):
        tab_plan, tab_chunks, tab_debug = st.tabs(
            ["Query Plan", "Evidence Chunks", "Scores & Metadata"]
        )

        with tab_plan:
            st.markdown(f"**Query type:** `{plan.query_type.value}`")
            st.markdown(f"**Keywords:** {', '.join(plan.retrieval_keywords) or '(none)'}")
            if plan.required_periods:
                st.markdown(
                    "**Required periods:** "
                    + ", ".join(str(period) for period in plan.required_periods)
                )
            if plan.required_concepts:
                st.markdown("**Required concepts:** " + ", ".join(plan.required_concepts))
            if plan.sub_questions:
                st.markdown("**Sub-questions:**")
                for sub_question in plan.sub_questions:
                    st.markdown(f"- {sub_question}")

        with tab_chunks:
            st.markdown(f"**Section chunks:** {len(bundle.section_chunks)}")
            for chunk in bundle.section_chunks:
                with st.container(border=True):
                    st.caption(f"Section: {chunk.section_title} | Page {chunk.page_number}")
                    display_text = chunk.text[:500]
                    if len(chunk.text) > 500:
                        display_text += "..."
                    st.text(display_text)

            st.markdown(f"**Table chunks:** {len(bundle.table_chunks)}")
            for chunk in bundle.table_chunks:
                with st.container(border=True):
                    st.caption(f"Table: {chunk.caption} | Section: {chunk.section_title}")
                    if chunk.headers and chunk.rows:
                        st.dataframe(
                            _table_dataframe(chunk.headers, chunk.rows),
                            width="stretch",
                        )

            st.markdown(f"**Structured facts:** {len(bundle.facts)}")
            for fact in bundle.facts:
                period_label = (
                    f"{fact.period_start} to {fact.period_end}"
                    if fact.period_start
                    else str(fact.period_end)
                )
                st.markdown(f"- `{fact.concept}` = {fact.value:,.2f} {fact.unit} ({period_label})")

        with tab_debug:
            st.markdown("**Retrieval scores** (chunk_id -> relevance):")
            st.json(bundle.retrieval_scores)
            st.markdown("**Bundle metadata:**")
            st.json(bundle.metadata)
            st.markdown("**Answer retrieval debug:**")
            st.json(answer.retrieval_debug)

elif run_clicked:
    st.warning("Please enter a question before submitting.")
