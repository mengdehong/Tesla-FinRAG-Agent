"""Rule-based query planner for financial questions.

Extracts time periods, financial metrics, scope filters, and answer
intent from a user question using pattern matching.  Implements
:class:`QueryPlanningService`.

A future version may replace or augment the rules with an LLM-based
classifier, but the typed :class:`QueryPlan` contract stays the same.
"""

from __future__ import annotations

import re
from datetime import date

from tesla_finrag.models import QueryPlan, QueryType
from tesla_finrag.services import QueryPlanningService

# ---------------------------------------------------------------------------
# Period extraction helpers
# ---------------------------------------------------------------------------

# Matches patterns like "2022 Q3", "Q3 2022", "2022Q3", "Q3-2022"
_QUARTER_RE = re.compile(
    r"""
    (?:
        (?P<year1>\d{4})\s*[-/]?\s*[Qq](?P<q1>[1-4])   # "2022 Q3"
    |
        [Qq](?P<q2>[1-4])\s*[-/]?\s*(?P<year2>\d{4})    # "Q3 2022"
    )
    """,
    re.VERBOSE,
)

# Matches patterns like "FY2022", "FY 2022", "fiscal year 2022", "full year 2022"
_FY_RE = re.compile(
    r"""
    (?:
        (?:FY|fiscal\s+year|full\s+year|annual)\s*(?P<year>\d{4})
    |
        (?P<year2>\d{4})\s+(?:annual|10-K|10K)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Standalone year mention
_YEAR_RE = re.compile(r"\b(20[12]\d)\b")

# Approximate quarter end dates for Tesla fiscal calendar
_QUARTER_END: dict[int, date] = {
    1: date(2000, 3, 31),
    2: date(2000, 6, 30),
    3: date(2000, 9, 30),
    4: date(2000, 12, 31),
}
_FY_END = date(2000, 12, 31)


def _quarter_end(year: int, quarter: int) -> date:
    """Return the period-end date for a given fiscal year and quarter."""
    template = _QUARTER_END[quarter]
    return template.replace(year=year)


def _fy_end(year: int) -> date:
    """Return the period-end date for a fiscal year."""
    return _FY_END.replace(year=year)


def extract_periods(question: str) -> list[date]:
    """Extract fiscal period end-dates mentioned in a question."""
    periods: list[date] = []
    occupied_spans: list[tuple[int, int]] = []

    for m in _QUARTER_RE.finditer(question):
        if m.group("year1"):
            year, q = int(m.group("year1")), int(m.group("q1"))
        else:
            year, q = int(m.group("year2")), int(m.group("q2"))
        periods.append(_quarter_end(year, q))
        occupied_spans.append(m.span())

    for m in _FY_RE.finditer(question):
        year_str = m.group("year") or m.group("year2")
        if year_str:
            periods.append(_fy_end(int(year_str)))
            occupied_spans.append(m.span())

    for m in _YEAR_RE.finditer(question):
        start, end = m.span()
        overlaps_existing_period = any(
            start < occupied_end and end > occupied_start
            for occupied_start, occupied_end in occupied_spans
        )
        if overlaps_existing_period:
            continue
        periods.append(_fy_end(int(m.group(1))))

    return sorted(set(periods))


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

# Canonical financial metrics and their common aliases
_METRIC_ALIASES: dict[str, list[str]] = {
    "us-gaap:Revenues": [
        "revenue",
        "revenues",
        "total revenue",
        "total revenues",
        "net revenue",
        "net revenues",
        "sales",
    ],
    "us-gaap:GrossProfit": [
        "gross profit",
        "gross margin",
    ],
    "us-gaap:OperatingIncomeLoss": [
        "operating income",
        "operating loss",
        "operating profit",
        "income from operations",
    ],
    "us-gaap:NetIncomeLoss": [
        "net income",
        "net loss",
        "net profit",
        "bottom line",
    ],
    "us-gaap:EarningsPerShareBasic": [
        "eps",
        "earnings per share",
    ],
    "us-gaap:ResearchAndDevelopmentExpense": [
        "r&d",
        "research and development",
        "r&d expense",
        "research & development",
    ],
    "us-gaap:SellingGeneralAndAdministrativeExpense": [
        "sg&a",
        "selling general and administrative",
        "selling, general and administrative",
    ],
    "us-gaap:CashAndCashEquivalentsAtCarryingValue": [
        "cash",
        "cash and cash equivalents",
        "cash position",
    ],
    "us-gaap:LongTermDebt": [
        "long-term debt",
        "long term debt",
        "total debt",
    ],
    "us-gaap:CostOfGoodsAndServicesSold": [
        "cost of revenue",
        "cost of goods sold",
        "cogs",
        "cost of sales",
        "cost of automotive revenue",
    ],
    "custom:FreeCashFlow": [
        "free cash flow",
        "fcf",
    ],
    "custom:AutomotiveRevenue": [
        "automotive revenue",
        "automotive sales",
    ],
    "custom:EnergyRevenue": [
        "energy revenue",
        "energy generation and storage revenue",
    ],
    "custom:GrossMarginPercent": [
        "gross margin %",
        "gross margin percent",
        "gross margin percentage",
    ],
    "custom:OperatingMarginPercent": [
        "operating margin",
        "operating margin %",
    ],
    "custom:CapitalExpenditure": [
        "capital expenditure",
        "capex",
        "capital expenditures",
    ],
}

# Build a reverse lookup: lowered alias -> concept name
_ALIAS_TO_CONCEPT: dict[str, str] = {}
for concept, aliases in _METRIC_ALIASES.items():
    for alias in aliases:
        _ALIAS_TO_CONCEPT[alias.lower()] = concept


def extract_metrics(question: str) -> list[str]:
    """Extract XBRL concept names from a question via alias matching.

    Processes aliases longest-first so that e.g. "free cash flow" is
    matched before the shorter "cash" alias.  Once an alias is matched
    its span is masked to prevent shorter aliases from matching within
    the same text.
    """
    lower = question.lower()
    found: list[str] = []
    # Sort aliases longest-first so longer matches take priority
    for alias in sorted(_ALIAS_TO_CONCEPT, key=len, reverse=True):
        if alias in lower:
            concept = _ALIAS_TO_CONCEPT[alias]
            if concept not in found:
                found.append(concept)
            # Mask matched span to prevent shorter overlapping aliases
            lower = lower.replace(alias, " " * len(alias))
    return found


# ---------------------------------------------------------------------------
# Query type classification
# ---------------------------------------------------------------------------

_COMPARISON_PATTERNS = re.compile(
    r"\b(compar\w*|differ\w*|chang\w*|increas\w*|decreas\w*|grew|growth|decline\w*|vs\.?|versus)\b",
    re.IGNORECASE,
)
_RANKING_PATTERNS = re.compile(
    r"\b(highest|lowest|most|least|rank\w*|top|bottom|best|worst|largest|smallest)\b",
    re.IGNORECASE,
)
_CALCULATION_PATTERNS = re.compile(
    r"\b(total\w*|sum\w*|averag\w*|margin\w*|ratio\w*|percentag\w*|percent\w*|yoy|qoq|"
    r"year.over.year|quarter.over.quarter|calculat\w*|comput\w*)\b",
    re.IGNORECASE,
)
_TABLE_PATTERNS = re.compile(
    r"\b(table|breakdown|segment\w*|line.item\w*|balance.sheet|income.statement|"
    r"cash.flow.statement)\b",
    re.IGNORECASE,
)
_NARRATIVE_PATTERNS = re.compile(
    r"\b(mention\w*|discuss\w*|describ\w*|explain\w*|stated|states|stating|"
    r"report\w*|comment\w*|narrative|risk\w*|factor\w*|outlook|challeng\w*|"
    r"supply.chain|guidance)\b",
    re.IGNORECASE,
)


def classify_query_type(question: str, metrics: list[str]) -> QueryType:
    """Classify the intent of a financial question."""
    has_calc = bool(_CALCULATION_PATTERNS.search(question))
    has_rank = bool(_RANKING_PATTERNS.search(question))
    has_compare = bool(_COMPARISON_PATTERNS.search(question))
    has_table = bool(_TABLE_PATTERNS.search(question))
    has_narrative = bool(_NARRATIVE_PATTERNS.search(question))
    has_metrics = bool(metrics)

    if (has_calc or has_rank) and has_metrics:
        return QueryType.NUMERIC_CALCULATION
    if has_table and has_metrics:
        return QueryType.TABLE_LOOKUP
    if has_narrative and not has_metrics:
        return QueryType.NARRATIVE_COMPARE
    if has_compare and has_metrics:
        return QueryType.NUMERIC_CALCULATION
    if has_narrative and has_compare:
        return QueryType.NARRATIVE_COMPARE
    if has_metrics:
        return QueryType.HYBRID_REASONING
    return QueryType.HYBRID_REASONING


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------


def extract_keywords(question: str) -> list[str]:
    """Extract important keywords for lexical search."""
    # Remove common stop words and return significant terms
    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "was",
        "were",
        "are",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "and",
        "but",
        "or",
        "not",
        "no",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "where",
        "why",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "so",
        "just",
        "about",
        "also",
        "very",
        "much",
        "many",
        "there",
        "here",
        "up",
        "out",
        "over",
    }

    tokens = re.findall(r"[a-z][a-z'&-]+", question.lower())
    keywords = [t for t in tokens if t not in stop_words and len(t) > 2]
    return keywords


# ---------------------------------------------------------------------------
# Main planner
# ---------------------------------------------------------------------------


class RuleBasedQueryPlanner(QueryPlanningService):
    """Rule-based implementation of :class:`QueryPlanningService`.

    Uses regex patterns and alias lookups to extract structured
    information from user questions.  No external API calls required.
    """

    def plan(self, question: str) -> QueryPlan:
        """Parse a question into a structured :class:`QueryPlan`."""
        periods = extract_periods(question)
        metrics = extract_metrics(question)
        query_type = classify_query_type(question, metrics)
        keywords = extract_keywords(question)

        needs_calculation = (
            query_type in (QueryType.NUMERIC_CALCULATION, QueryType.TABLE_LOOKUP)
            or bool(_CALCULATION_PATTERNS.search(question))
            or (bool(metrics) and bool(periods))  # metric + period = factual lookup
        )

        return QueryPlan(
            original_query=question,
            query_type=query_type,
            sub_questions=[question],
            retrieval_keywords=keywords,
            required_periods=periods,
            required_concepts=metrics,
            needs_calculation=needs_calculation,
        )
