"""Tesla FinRAG Evaluation Workbench.

Launch locally with::

    uv run streamlit run app.py

Supports English / Simplified Chinese bilingual UI via the sidebar
language switcher. When the locale is set to ``zh_CN``, the pipeline
injects a ``response_language`` directive so the LLM generates answers
in Chinese.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px  # type: ignore[import-untyped]
import streamlit as st

from tesla_finrag.evaluation import FilingScope, ProviderMode, get_workbench_pipeline
from tesla_finrag.evaluation.answer_rendering import render_answer_segments
from tesla_finrag.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    concept_label,
    response_language_directive,
    t,
)
from tesla_finrag.models import AnswerStatus, EvidenceBundle, FilingType

st.set_page_config(page_title="Tesla FinRAG Workbench", layout="wide")

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
if "locale" not in st.session_state:
    st.session_state.locale = DEFAULT_LOCALE


def _locale() -> str:
    return st.session_state.locale


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Sidebar: language switcher (topmost)
# ---------------------------------------------------------------------------

with st.sidebar:
    locale_options = list(SUPPORTED_LOCALES.keys())
    locale_labels = list(SUPPORTED_LOCALES.values())
    current_idx = locale_options.index(_locale()) if _locale() in locale_options else 0

    selected_locale = st.radio(
        "\U0001f310 " + t(_locale(), "sidebar_language"),
        options=locale_options,
        format_func=lambda k: SUPPORTED_LOCALES.get(k, k),
        index=current_idx,
        key="locale_radio",
        horizontal=True,
    )
    if selected_locale != st.session_state.locale:
        st.session_state.locale = selected_locale
        st.rerun()

    st.divider()

# ---------------------------------------------------------------------------
# Sidebar: runtime provider
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header(t(_locale(), "sidebar_runtime"))
    selected_provider = st.radio(
        t(_locale(), "sidebar_provider"),
        options=[mode.value for mode in ProviderMode],
        format_func=lambda value: (
            t(_locale(), "provider_local")
            if value == ProviderMode.LOCAL.value
            else t(_locale(), "provider_remote")
        ),
        index=0,
    )

# ---------------------------------------------------------------------------
# Pipeline initialization
# ---------------------------------------------------------------------------

try:
    pipeline = get_workbench_pipeline(provider_mode=ProviderMode(selected_provider))
except Exception as exc:  # pragma: no cover - exercised in the UI
    from tesla_finrag.guidance import format_corpus_guidance
    from tesla_finrag.runtime import ProcessedCorpusError

    st.title(t(_locale(), "app_title"))
    if isinstance(exc, ProcessedCorpusError):
        st.error(format_corpus_guidance(exc))
    else:
        st.error(f"{t(_locale(), 'init_error')}: {exc}")
    st.stop()

available_years = pipeline.available_years
available_quarters = pipeline.available_quarters
quarter_labels = _quarter_options(available_quarters)

# ---------------------------------------------------------------------------
# Sidebar: filing scope
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header(t(_locale(), "sidebar_filing_scope"))

    fiscal_years: list[int] = st.multiselect(
        t(_locale(), "sidebar_fiscal_year"),
        options=available_years,
        default=available_years[-2:] if len(available_years) > 1 else available_years,
    )

    filing_type_choices = [
        t(_locale(), "filing_type_both"),
        t(_locale(), "filing_type_annual"),
        t(_locale(), "filing_type_quarterly"),
    ]
    filing_type_option: str = st.selectbox(
        t(_locale(), "sidebar_filing_type"),
        options=filing_type_choices,
        index=0,
    )
    selected_filing_type = _resolve_filing_type(filing_type_option)

    selected_quarter_labels: list[str] = st.multiselect(
        t(_locale(), "sidebar_quarter"),
        options=quarter_labels,
        default=quarter_labels,
        disabled=selected_filing_type == FilingType.ANNUAL,
    )

    st.divider()
    st.caption(t(_locale(), "sidebar_version"))
    st.caption(
        t(_locale(), "sidebar_mode_local")
        if selected_provider == "local"
        else t(_locale(), "sidebar_mode_remote")
    )
    st.caption(
        f"{t(_locale(), 'sidebar_corpus_years')}: "
        f"{', '.join(str(year) for year in available_years)}"
    )

# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------

st.title(t(_locale(), "app_title"))
st.markdown(t(_locale(), "app_description"))

question = st.text_area(
    t(_locale(), "question_label"),
    placeholder=t(_locale(), "question_placeholder"),
    height=100,
)

run_clicked = st.button(t(_locale(), "run_query"), type="primary", use_container_width=True)


# ---------------------------------------------------------------------------
# Citation rendering helpers
# ---------------------------------------------------------------------------


def _render_citation_card(
    idx: int,
    filing_type_value: str,
    period_end: object,
    excerpt: str,
    section_title: str | None = None,
    page_number: int | None = None,
) -> None:
    """Render a single citation with rich source coordinates."""
    loc = _locale()

    # Build source reference line like:
    # Source: [Tesla 2023 10-K, Item 7: MD&A, Page 43]
    parts = [f"Tesla {period_end} {filing_type_value}"]
    if section_title:
        parts.append(f"{t(loc, 'citation_section')}: {section_title}")
    if page_number is not None:
        parts.append(f"{t(loc, 'citation_page')} {page_number}")

    source_ref = ", ".join(parts)

    st.markdown(f"**[{idx}]** {t(loc, 'citation_source_prefix')}: [{source_ref}]")
    if excerpt:
        st.caption(excerpt)


# ---------------------------------------------------------------------------
# XBRL trend chart rendering
# ---------------------------------------------------------------------------


def _render_xbrl_charts(bundle: EvidenceBundle) -> None:
    """Render multi-year trend charts for XBRL facts in the evidence bundle."""
    loc = _locale()

    if not bundle.facts:
        st.info(t(loc, "no_xbrl_data"))
        return

    # Group facts by concept, then deduplicate by period_end.
    # The same period's fact often appears as comparative data across multiple
    # filings (e.g. FY2023 revenue re-stated in both the 2024 and 2025 10-K).
    # We keep only the first (chronologically earliest) occurrence per period
    # so each fiscal year renders exactly one bar.
    concept_data: dict[str, list[dict[str, object]]] = {}
    for fact in bundle.facts:
        concept_data.setdefault(fact.concept, []).append(
            {
                "period_end": str(fact.period_end),
                "value": fact.value * fact.scale / 1_000_000,  # Convert to millions
                "label": concept_label(loc, fact.concept),
                "unit": fact.unit,
            }
        )

    for concept, records in concept_data.items():
        if len(records) < 1:
            continue

        # Sort by period_end then deduplicate, keeping the first entry per period.
        records_sorted = sorted(records, key=lambda r: r["period_end"])
        seen_periods: set[str] = set()
        deduped_records: list[dict[str, object]] = []
        for r in records_sorted:
            period_key = str(r["period_end"])
            if period_key not in seen_periods:
                seen_periods.add(period_key)
                # Derive a human-readable fiscal year label (e.g. "FY2023").
                fy_label = f"FY{period_key[:4]}"
                deduped_records.append({**r, "fiscal_year": fy_label})

        df = pd.DataFrame(deduped_records)
        df = df.sort_values("fiscal_year")

        display_label = concept_label(loc, concept)
        chart_title = f"{display_label} ({t(loc, 'chart_unit_usd_millions')})"

        fig = px.bar(
            df,
            x="fiscal_year",
            y="value",
            title=chart_title,
            labels={
                "fiscal_year": t(loc, "chart_period"),
                "value": t(loc, "chart_value"),
            },
            color_discrete_sequence=["#e63946"],
            text="value",
        )
        fig.update_traces(
            texttemplate="%{text:,.1f}",
            textposition="outside",
        )
        fig.update_layout(
            xaxis_title=t(loc, "chart_period"),
            xaxis={"type": "category"},
            yaxis_title=f"{t(loc, 'chart_value')} ({t(loc, 'chart_unit_usd_millions')})",
            height=350,
            margin={"t": 40, "b": 40, "l": 60, "r": 20},
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key=f"chart_{concept}")


# ---------------------------------------------------------------------------
# Query execution and result display
# ---------------------------------------------------------------------------

if run_clicked and question.strip():
    if not fiscal_years:
        st.warning(t(_locale(), "warn_select_year"))
        st.stop()

    quarter_numbers = tuple(
        int(label.removeprefix("Q")) for label in selected_quarter_labels if label.startswith("Q")
    )
    scope = FilingScope(
        fiscal_years=tuple(sorted(fiscal_years)),
        filing_type=selected_filing_type,
        quarters=(quarter_numbers if selected_filing_type == FilingType.QUARTERLY else ()),
    )

    # Inject response language directive when locale != English
    lang_directive = response_language_directive(_locale())

    try:
        with st.spinner(t(_locale(), "running_pipeline")):
            plan, bundle, answer = pipeline.run(
                question.strip(),
                scope=scope,
                response_language=lang_directive,
            )
    except Exception as exc:  # pragma: no cover - exercised in the UI
        st.error(f"{t(_locale(), 'pipeline_error')}: {exc}")
        st.stop()

    # -- Answer section ------------------------------------------------------
    st.subheader(t(_locale(), "answer_header"))

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
        st.markdown(f"**{t(_locale(), 'status_label')}:** :{color}[{answer.status.value}]")
    with col_confidence:
        st.markdown(f"**{t(_locale(), 'confidence_label')}:** {confidence}")

    render_answer_segments(
        answer.answer_text,
        markdown_renderer=st.markdown,
        latex_renderer=st.latex,
        plain_text_renderer=st.text,
    )

    # -- XBRL Financial Trend Charts -----------------------------------------
    if bundle.facts:
        with st.expander(t(_locale(), "financial_charts_header"), expanded=True):
            _render_xbrl_charts(bundle)

    # -- Citations -----------------------------------------------------------
    with st.expander(t(_locale(), "citations_header"), expanded=True):
        if answer.citations:
            for idx, citation in enumerate(answer.citations, start=1):
                _render_citation_card(
                    idx=idx,
                    filing_type_value=citation.filing_type.value,
                    period_end=citation.period_end,
                    excerpt=citation.excerpt,
                    section_title=getattr(citation, "section_title", None),
                    page_number=getattr(citation, "page_number", None),
                )
        else:
            st.info(t(_locale(), "no_citations"))

    # -- Calculation steps ---------------------------------------------------
    with st.expander(t(_locale(), "calc_steps_header")):
        if answer.calculation_trace:
            for step in answer.calculation_trace:
                st.markdown(f"- {step}")
        else:
            st.info(t(_locale(), "no_calc_steps"))

    # -- Retrieval debug -----------------------------------------------------
    with st.expander(t(_locale(), "retrieval_debug_header")):
        loc = _locale()
        tab_plan, tab_chunks, tab_debug = st.tabs(
            [
                t(loc, "tab_query_plan"),
                t(loc, "tab_evidence_chunks"),
                t(loc, "tab_scores_metadata"),
            ]
        )

        with tab_plan:
            st.markdown(f"**{t(loc, 'query_type')}:** `{plan.query_type.value}`")
            st.markdown(
                f"**{t(loc, 'keywords')}:** "
                f"{', '.join(plan.retrieval_keywords) or t(loc, 'none_placeholder')}"
            )
            if plan.required_periods:
                st.markdown(
                    f"**{t(loc, 'required_periods')}:** "
                    + ", ".join(str(period) for period in plan.required_periods)
                )
            if plan.required_concepts:
                st.markdown(
                    f"**{t(loc, 'required_concepts')}:** "
                    + ", ".join(f"{concept_label(loc, c)} (`{c}`)" for c in plan.required_concepts)
                )
            if plan.sub_questions:
                st.markdown(f"**{t(loc, 'sub_questions')}:**")
                for sub_question in plan.sub_questions:
                    st.markdown(f"- {sub_question}")

        with tab_chunks:
            st.markdown(f"**{t(loc, 'section_chunks_count')}:** {len(bundle.section_chunks)}")
            for chunk in bundle.section_chunks:
                with st.container(border=True):
                    page_label = (
                        f" | {t(loc, 'citation_page')} {chunk.page_number}"
                        if chunk.page_number
                        else ""
                    )
                    st.caption(f"{t(loc, 'citation_section')}: {chunk.section_title}{page_label}")
                    display_text = chunk.text[:500]
                    if len(chunk.text) > 500:
                        display_text += "..."
                    st.text(display_text)

            st.markdown(f"**{t(loc, 'table_chunks_count')}:** {len(bundle.table_chunks)}")
            for chunk in bundle.table_chunks:
                with st.container(border=True):
                    st.caption(
                        f"Table: {chunk.caption} | "
                        f"{t(loc, 'citation_section')}: {chunk.section_title}"
                    )
                    if chunk.headers and chunk.rows:
                        st.dataframe(
                            _table_dataframe(chunk.headers, chunk.rows),
                            use_container_width=True,
                        )

            st.markdown(f"**{t(loc, 'structured_facts_count')}:** {len(bundle.facts)}")
            for fact in bundle.facts:
                period_label = (
                    f"{fact.period_start} to {fact.period_end}"
                    if fact.period_start
                    else str(fact.period_end)
                )
                display_label = concept_label(loc, fact.concept)
                st.markdown(
                    f"- {display_label} (`{fact.concept}`) = "
                    f"{fact.value:,.2f} {fact.unit} ({period_label})"
                )

        with tab_debug:
            st.markdown(f"**{t(loc, 'retrieval_scores_label')}:**")
            st.json(bundle.retrieval_scores)
            st.markdown(f"**{t(loc, 'bundle_metadata_label')}:**")
            st.json(bundle.metadata)
            st.markdown(f"**{t(loc, 'answer_retrieval_debug_label')}:**")
            st.json(answer.retrieval_debug)

elif run_clicked:
    st.warning(t(_locale(), "warn_enter_question"))
