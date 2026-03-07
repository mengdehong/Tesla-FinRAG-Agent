"""Tesla FinRAG Evaluation Workbench.

Launch locally with::

    uv run streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tesla_finrag.evaluation import FilingScope, get_workbench_pipeline
from tesla_finrag.models import AnswerStatus, FilingType

st.set_page_config(page_title="Tesla FinRAG Workbench", layout="wide")


def _resolve_filing_type(label: str) -> FilingType | None:
    if "10-K" in label:
        return FilingType.ANNUAL
    if "10-Q" in label:
        return FilingType.QUARTERLY
    return None


def _quarter_options(quarters: list[int]) -> list[str]:
    return [f"Q{quarter}" for quarter in quarters]


try:
    pipeline = get_workbench_pipeline()
except Exception as exc:  # pragma: no cover - exercised in the UI
    st.title("Tesla FinRAG Evaluation Workbench")
    st.error(f"Unable to initialize the local demo pipeline: {exc}")
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
    st.caption("Mode: demo corpus + real pipeline")
    st.caption(f"Corpus years: {', '.join(str(year) for year in available_years)}")

st.title("Tesla FinRAG Evaluation Workbench")
st.markdown(
    "Ask financial questions about Tesla's SEC filings in the local demo corpus. "
    "The workbench runs the real planning, retrieval, and answer-composition "
    "services, then shows grounded answers with citations, calculation traces, "
    "and retrieval diagnostics."
)

question = st.text_area(
    "Your question",
    placeholder="e.g. What was Tesla's total revenue in FY2023 and how did it compare to FY2022?",
    height=100,
)

run_clicked = st.button("Run Query", type="primary", use_container_width=True)

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

    st.markdown(answer.answer_text)

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
                            pd.DataFrame(chunk.rows, columns=chunk.headers),
                            use_container_width=True,
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
