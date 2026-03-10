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

from tesla_finrag.models import (
    AnswerShape,
    CalculationIntent,
    CalculationOperand,
    PeriodSemantics,
    QueryLanguage,
    QueryPlan,
    QueryType,
    SemanticScope,
    SubQuery,
)
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
        (?P<year2>\d{4})\s*(?:年)?\s*(?:annual|10-K|10K)
    |
        (?P<year3>\d{4})\s*(?:年)?\s*(?:财年|全年|年报|年度)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Standalone year mention.
# Uses digit-only lookarounds instead of \b because Python's Unicode regex
# classifies Chinese characters as \w, which breaks word-boundary matching
# adjacent to digits (e.g. "2023年" has no \b between "3" and "年").
_YEAR_RE = re.compile(r"(?<!\d)(20[12]\d)(?!\d)")

_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
_NUMERIC_DATE_RE = re.compile(
    r"(?:截至|截止|as\s+of\s+)?(?P<year>\d{4})\s*[年/-]\s*(?P<month>\d{1,2})\s*[月/-]\s*(?P<day>\d{1,2})\s*日?",
    re.IGNORECASE,
)
_CHINESE_YEAR_END_RE = re.compile(r"(?:截至|截止|至)?\s*(?P<year>\d{4})\s*年\s*(?:年末|年底|末|底)")
_YEAR_RANGE_RE = re.compile(
    r"""
    (?:
        (?:from\s+)?(?:FY|fiscal\s+year|full\s+year|annual)?\s*(?P<start_en>\d{4})
        \s*(?:through|to|[-–])\s*
        (?:FY|fiscal\s+year|full\s+year|annual)?\s*(?P<end_en>\d{4})
    |
        从\s*(?:FY)?(?P<start_cn>\d{4})\s*到\s*(?:FY)?(?P<end_cn>\d{4})
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)
_CHINESE_QUARTER_RE = re.compile(
    r"(?:(?P<year>\d{4})\s*年?\s*)?(?:[Qq](?P<q>[1-4])|第?(?P<cn>[一二三四1234])季度)"
)

_CHINESE_QUARTER_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
}

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


def detect_query_language(question: str) -> QueryLanguage:
    """Detect the dominant language family in a query."""
    has_cjk = bool(_CJK_CHAR_RE.search(question))
    has_latin = bool(_LATIN_CHAR_RE.search(question))
    if has_cjk and has_latin:
        return QueryLanguage.MIXED
    if has_cjk:
        return QueryLanguage.CHINESE
    return QueryLanguage.ENGLISH


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
        year_str = m.group("year") or m.group("year2") or m.group("year3")
        if year_str:
            periods.append(_fy_end(int(year_str)))
            occupied_spans.append(m.span())

    for m in _YEAR_RANGE_RE.finditer(question):
        start_year = m.group("start_en") or m.group("start_cn")
        end_year = m.group("end_en") or m.group("end_cn")
        if not start_year or not end_year:
            continue
        start = int(start_year)
        end = int(end_year)
        if start > end:
            start, end = end, start
        if end - start > 10:
            continue
        for year in range(start, end + 1):
            periods.append(_fy_end(year))
        occupied_spans.append(m.span())

    for m in _NUMERIC_DATE_RE.finditer(question):
        try:
            periods.append(
                date(
                    int(m.group("year")),
                    int(m.group("month")),
                    int(m.group("day")),
                )
            )
            occupied_spans.append(m.span())
        except ValueError:
            continue

    for m in _CHINESE_YEAR_END_RE.finditer(question):
        periods.append(_fy_end(int(m.group("year"))))
        occupied_spans.append(m.span())

    year_mentions = [
        (match.start(), int(match.group(1))) for match in re.finditer(r"(20[12]\d)", question)
    ]
    last_year: int | None = None
    for m in _CHINESE_QUARTER_RE.finditer(question):
        start, end = m.span()
        overlaps_existing_period = any(
            start < occupied_end and end > occupied_start
            for occupied_start, occupied_end in occupied_spans
        )
        if overlaps_existing_period:
            continue
        if m.group("year"):
            last_year = int(m.group("year"))
        elif last_year is None:
            for year_pos, year_value in year_mentions:
                if year_pos < start:
                    last_year = year_value
                else:
                    break
        if last_year is None:
            continue
        q_str = m.group("q")
        cn_str = m.group("cn")
        quarter = int(q_str) if q_str else _CHINESE_QUARTER_MAP.get(cn_str or "")
        if quarter is None:
            continue
        periods.append(_quarter_end(last_year, quarter))
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
        "revenue growth",
        "营收",
        "收入",
        "营业收入",
        "总营收",
        "总收入",
        "总营业收入",
    ],
    "us-gaap:GrossProfit": [
        "gross profit",
        "gross margin",
        "gross margin %",
        "gross margin percent",
        "gross margin percentage",
        "毛利润",
        "毛利",
        "毛利率",
    ],
    "us-gaap:OperatingIncomeLoss": [
        "operating income",
        "operating loss",
        "operating profit",
        "income from operations",
        "operating margin",
        "operating margin %",
        "营业利润",
        "营业收益",
        "经营利润",
        "营业利润率",
        "营业利润率%",
    ],
    "us-gaap:NetIncomeLoss": [
        "net income",
        "net loss",
        "net profit",
        "bottom line",
        "净利润",
        "净收益",
    ],
    "us-gaap:EarningsPerShareBasic": [
        "eps",
        "earnings per share",
        "每股收益",
    ],
    "us-gaap:ResearchAndDevelopmentExpense": [
        "r&d",
        "research and development",
        "r&d expense",
        "research & development",
        "研发",
        "研发费用",
        "研究与开发",
        "研究和开发",
    ],
    "us-gaap:SellingGeneralAndAdministrativeExpense": [
        "sg&a",
        "selling general and administrative",
        "selling, general and administrative",
        "销售管理费用",
        "销售、一般及行政费用",
        "管理费用",
    ],
    "us-gaap:CashAndCashEquivalentsAtCarryingValue": [
        "cash",
        "cash and cash equivalents",
        "cash position",
        "现金",
        "现金及现金等价物",
        "现金头寸",
    ],
    "us-gaap:AccountsPayableCurrent": [
        "accounts payable current",
        "accounts payable, current",
        "current accounts payable",
        "ap current",
        "accounts payable",
        "应付账款",
        "流动应付账款",
    ],
    "us-gaap:AccountsReceivableNetCurrent": [
        "accounts receivable current",
        "accounts receivable, current",
        "current accounts receivable",
        "ar current",
        "accounts receivable",
        "应收账款",
        "流动应收账款",
    ],
    "dei:EntityPublicFloat": [
        "public float",
        "entity public float",
        "company public float",
        "公众持股市值",
        "流通市值",
    ],
    "us-gaap:LongTermDebt": [
        "long-term debt",
        "long term debt",
        "total debt",
        "长期债务",
        "总债务",
    ],
    "us-gaap:CostOfGoodsAndServicesSold": [
        "cost of revenue",
        "cost of goods sold",
        "cogs",
        "cost of sales",
        "cost of automotive revenue",
        "营业成本",
        "收入成本",
        "销售成本",
        "汽车业务成本",
    ],
    "custom:FreeCashFlow": [
        "free cash flow",
        "fcf",
        "自由现金流",
    ],
    "us-gaap:NetCashProvidedByUsedInOperatingActivities": [
        "operating cash flow",
        "cash flow from operations",
        "cash from operations",
        "net cash provided by operating activities",
        "经营现金流",
        "经营活动现金流",
        "经营活动产生的现金流量净额",
    ],
    "custom:AutomotiveRevenue": [
        "automotive revenue",
        "automotive sales",
        "汽车业务营收",
        "汽车业务收入",
    ],
    "custom:EnergyRevenue": [
        "energy revenue",
        "energy generation and storage revenue",
        "能源业务营收",
        "能源业务收入",
        "储能业务营收",
    ],
    # NOTE: "gross margin %" / "operating margin %" used to map to
    # pseudo-concepts (custom:GrossMarginPercent, custom:OperatingMarginPercent)
    # that don't exist in the fact store.  These are now handled as
    # *margin detection rules* in _infer_margin_intent() which sets
    # calculation_intent=RATIO with the correct numerator/denominator concepts.
    # "gross margin" (without %) still maps to us-gaap:GrossProfit via
    # the GrossProfit aliases above.
    "custom:CapitalExpenditure": [
        "capital expenditure",
        "capex",
        "capital expenditures",
        "资本开支",
        "资本支出",
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

    Uses exact canonical concept precedence: if a longer, more specific
    alias matches first, shorter generic aliases for different concepts
    are suppressed in the matched region.
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


_NORMALIZED_TERM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"毛利率"), "gross margin"),
    (re.compile(r"营业利润率|经营利润率"), "operating margin"),
    (re.compile(r"供应链"), "supply chain"),
    (re.compile(r"风险因素?"), "risk factors"),
    (re.compile(r"宏观背景"), "macro environment"),
    (re.compile(r"挑战"), "challenge"),
    (re.compile(r"提到|描述|讨论|说明"), "discuss describe mention"),
    (re.compile(r"展望"), "outlook"),
    (re.compile(r"指引"), "guidance"),
    (re.compile(r"竞争"), "competition"),
    (re.compile(r"原材料"), "raw material"),
    (re.compile(r"物流"), "logistics"),
    (re.compile(r"半导体"), "semiconductor"),
    (re.compile(r"地缘政治"), "geopolitical"),
    (re.compile(r"中国市场"), "china market"),
    (re.compile(r"产能瓶颈"), "capacity bottleneck"),
    (re.compile(r"比较|对比|相比"), "compare"),
    (re.compile(r"趋势|如何变化|变化趋势"), "trend change"),
    (re.compile(r"同比"), "year over year growth rate compare"),
    (re.compile(r"环比"), "quarter over quarter growth rate compare"),
    (re.compile(r"增长率"), "growth rate"),
    (re.compile(r"增长|增幅|提升"), "growth compare"),
    (re.compile(r"下降|降幅|减少"), "decline compare"),
    (re.compile(r"最高|最大"), "highest"),
    (re.compile(r"最低|最小"), "lowest"),
    (re.compile(r"哪个季度"), "which quarter"),
    (re.compile(r"哪一年"), "which year"),
    (re.compile(r"计算过程|逐步|一步一步|展示步骤|详细计算"), "show step by step calculation"),
    (re.compile(r"除以|占比|比率|百分比"), "ratio percentage divided by"),
    (re.compile(r"差额|差异|相差|减去|净变化|绝对变化"), "difference subtract"),
    (re.compile(r"表格|明细|拆分"), "table breakdown"),
    (re.compile(r"资产负债表"), "balance sheet"),
    (re.compile(r"利润表"), "income statement"),
    (re.compile(r"现金流量表"), "cash flow statement"),
    (re.compile(r"10-K|年报", re.IGNORECASE), "10-k annual"),
    (re.compile(r"10-Q|季报", re.IGNORECASE), "10-q quarterly"),
]


def _ordered_unique(parts: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        normalized = part.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _extract_normalized_terms(question: str) -> list[str]:
    """Extract normalized English cue phrases from English/Chinese questions."""
    lowered = question.lower()
    terms: list[str] = []
    for pattern, replacement in _NORMALIZED_TERM_PATTERNS:
        if pattern.search(question) or replacement in lowered:
            terms.append(replacement)
    return _ordered_unique(terms)


def _build_rule_text(
    question: str,
    metrics: list[str],
    periods: list[date],
) -> str:
    """Build a normalized text surface for multilingual rule matching."""
    parts = [question.lower()]
    parts.extend(_extract_normalized_terms(question))
    parts.extend(_concept_to_human_label(concept, question=question) for concept in metrics)
    parts.extend(
        _period_label(
            period,
            classify_period_semantics(period, question),
        )
        for period in periods
    )
    return " ".join(_ordered_unique(parts))


def _build_normalized_search_text(
    question: str,
    metrics: list[str],
    periods: list[date],
    *,
    query_language: QueryLanguage,
) -> str:
    """Build normalized search text for retrieval over primarily English filings."""
    concept_labels = [_concept_to_human_label(concept, question=question) for concept in metrics]
    period_labels = [
        _period_label(period, classify_period_semantics(period, question)) for period in periods
    ]
    normalized_terms = _extract_normalized_terms(question)

    if query_language == QueryLanguage.ENGLISH:
        parts = [question.strip(), *normalized_terms, *concept_labels, *period_labels]
    else:
        parts = [*normalized_terms, *concept_labels, *period_labels]

    return " ".join(_ordered_unique(parts)).strip()


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
    rule_text = _build_rule_text(question, metrics, extract_periods(question))
    has_calc = bool(_CALCULATION_PATTERNS.search(rule_text))
    has_rank = bool(_RANKING_PATTERNS.search(rule_text))
    has_compare = bool(_COMPARISON_PATTERNS.search(rule_text))
    has_table = bool(_TABLE_PATTERNS.search(rule_text))
    has_narrative = bool(_NARRATIVE_PATTERNS.search(rule_text))
    has_metrics = bool(metrics)

    # Composite: both narrative and numeric/comparison signals present.
    # E.g. "What risk factors … and how did cost of revenue change …?"
    if has_narrative and has_metrics and (has_compare or has_calc):
        return QueryType.HYBRID_REASONING

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


def extract_keywords(
    question: str,
    metrics: list[str] | None = None,
    periods: list[date] | None = None,
) -> list[str]:
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

    current_metrics = metrics if metrics is not None else extract_metrics(question)
    current_periods = periods if periods is not None else extract_periods(question)
    keyword_surface = _build_rule_text(question, current_metrics, current_periods)
    tokens = re.findall(r"[a-z][a-z'&-]+", keyword_surface.lower())
    keywords = [t for t in tokens if t not in stop_words and len(t) > 2]
    return _ordered_unique(keywords)


# ---------------------------------------------------------------------------
# Period semantics classification
# ---------------------------------------------------------------------------


def classify_period_semantics(
    period: date,
    question: str,
) -> PeriodSemantics:
    """Classify a period-end date into its temporal semantics.

    Uses the period-end month and question context to distinguish
    annual cumulative, quarterly standalone, and other semantics.
    """
    lower = question.lower()

    # A December 31 period-end that matches FY pattern or standalone year
    if period.month == 12 and period.day == 31:
        # Check if a quarter pattern explicitly targets Q4
        q4_explicit = (
            bool(re.search(r"[Qq]4\s*[-/]?\s*" + str(period.year), lower))
            or bool(re.search(str(period.year) + r"\s*[-/]?\s*[Qq]4", lower))
            or bool(re.search(str(period.year) + r"\s*年\s*第?4季度", question))
            or "第四季度" in question
        )
        if q4_explicit:
            return PeriodSemantics.QUARTERLY_STANDALONE
        return PeriodSemantics.ANNUAL_CUMULATIVE

    # Standard quarter-end dates (March, June, September)
    if period.month in (3, 6, 9) and period.day in (30, 31):
        return PeriodSemantics.QUARTERLY_STANDALONE

    return PeriodSemantics.UNKNOWN


def build_period_semantics_map(
    periods: list[date],
    question: str,
) -> dict[str, PeriodSemantics]:
    """Build a mapping from ISO date strings to their period semantics."""
    return {p.isoformat(): classify_period_semantics(p, question) for p in periods}


# ---------------------------------------------------------------------------
# Sub-query decomposition
# ---------------------------------------------------------------------------

_COMPARISON_MULTI_PERIOD = re.compile(
    r"\b(compar\w*|differ\w*|chang\w*|versus|vs\.?|from\b.*\bto\b|between)\b",
    re.IGNORECASE,
)
_RANKING_MULTI_PERIOD = re.compile(
    r"\b(highest|lowest|most|least|rank\w*|top|bottom|best|worst|largest|smallest|"
    r"which\s+quarter|which\s+year)\b",
    re.IGNORECASE,
)


def _needs_decomposition(question: str, periods: list[date]) -> bool:
    """Determine if a question requires multi-period decomposition."""
    _ = question
    return len(periods) >= 2


def _build_sub_queries(
    question: str,
    periods: list[date],
    concepts: list[str],
    period_sem_map: dict[str, PeriodSemantics],
    *,
    query_language: QueryLanguage = QueryLanguage.ENGLISH,
    semantic_scope: SemanticScope | None = None,
) -> list[SubQuery]:
    """Build period-aware sub-queries for multi-period questions.

    Each required period gets its own sub-query so retrieval can
    apply hard scope constraints per period.
    """
    if not periods:
        return []

    sub_queries: list[SubQuery] = []
    for period in periods:
        sem = period_sem_map.get(period.isoformat(), PeriodSemantics.UNKNOWN)
        period_label = _period_label(period, sem)
        if concepts:
            concept_labels = [_concept_to_human_label(c, question=question) for c in concepts]
            text = f"{', '.join(concept_labels)} for {period_label}"
        else:
            text = f"{question} for {period_label}"
        search_text = _build_normalized_search_text(
            question,
            concepts,
            [period],
            query_language=query_language,
        )

        sub_queries.append(
            SubQuery(
                text=text,
                search_text=search_text,
                target_period=period,
                target_concepts=concepts,
                period_semantics=sem,
                semantic_scope=semantic_scope,
            )
        )
    return sub_queries


def _detect_semantic_scope(question: str) -> SemanticScope | None:
    """Infer business scope constraints such as automotive-only."""
    lower = question.lower()
    if "automotive" in lower or "汽车" in question:
        return SemanticScope.AUTOMOTIVE
    return None


def _concept_to_human_label(concept: str, *, question: str | None = None) -> str:
    """Convert an XBRL concept name to a human-readable label for retrieval.

    Uses the canonical alias list to find the best human-readable label.
    Falls back to splitting camelCase into space-separated words.
    """
    question_text = question or ""
    question_lower = question_text.lower()
    if concept == "us-gaap:CostOfGoodsAndServicesSold" and (
        "automotive" in question_lower or "汽车" in question_text
    ):
        return "cost of automotive revenue"

    # Look up in _METRIC_ALIASES for the first (most common) alias
    aliases = _METRIC_ALIASES.get(concept)
    if aliases:
        return aliases[0]

    # Fallback: extract local name and split camelCase
    label = concept.split(":")[-1] if ":" in concept else concept
    words: list[str] = []
    current: list[str] = []
    for ch in label:
        if ch.isupper() and current:
            words.append("".join(current))
            current = [ch]
        else:
            current.append(ch)
    if current:
        words.append("".join(current))
    return " ".join(words).lower()


def _period_label(period: date, semantics: PeriodSemantics) -> str:
    """Human-readable label for a period based on semantics."""
    year = period.year
    if semantics == PeriodSemantics.ANNUAL_CUMULATIVE:
        return f"FY{year}"
    month_to_q = {3: 1, 6: 2, 9: 3, 12: 4}
    quarter = month_to_q.get(period.month)
    if quarter:
        return f"Q{quarter} {year}"
    return str(period)


# ---------------------------------------------------------------------------
# Margin detection
# ---------------------------------------------------------------------------

# Patterns that indicate a *margin* query (ratio of X / Revenue)
_GROSS_MARGIN_RE = re.compile(
    r"\bgross\s+(?:profit\s+)?margin\b",
    re.IGNORECASE,
)
_OPERATING_MARGIN_RE = re.compile(
    r"\boperating\s+(?:income\s+|profit\s+)?margin\b",
    re.IGNORECASE,
)


def _infer_margin_intent(
    question: str,
    metrics: list[str],
    periods: list[date],
) -> tuple[CalculationIntent | None, list[CalculationOperand], list[str]]:
    """Detect margin queries and return (intent, operands, augmented_metrics).

    When a margin pattern is found the function:
    1. Sets ``calculation_intent`` to ``RATIO``.
    2. Builds operand list with numerator / denominator roles.
    3. Ensures both numerator and Revenue are in the metrics list.

    Returns ``(None, [], metrics)`` when no margin pattern is detected.
    """
    lower = _build_rule_text(question, metrics, periods)
    numerator_concept: str | None = None

    if _GROSS_MARGIN_RE.search(lower):
        numerator_concept = "us-gaap:GrossProfit"
    elif _OPERATING_MARGIN_RE.search(lower):
        numerator_concept = "us-gaap:OperatingIncomeLoss"

    if numerator_concept is None:
        return None, [], metrics

    denominator_concept = "us-gaap:Revenues"

    # Augment metrics so retrieval fetches both concepts
    augmented = list(metrics)
    for c in (numerator_concept, denominator_concept):
        if c not in augmented:
            augmented.append(c)

    # Multi-period ranking questions still need ratio semantics, but the
    # downstream calculation should rank the derived margin by period.
    has_rank = bool(_RANKING_PATTERNS.search(lower))
    intent = CalculationIntent.RANK if has_rank and len(periods) >= 2 else CalculationIntent.RATIO

    # Build operands — one per period (or period-agnostic if no periods)
    operands: list[CalculationOperand] = []
    if periods:
        for p in periods:
            operands.append(
                CalculationOperand(concept=numerator_concept, role="numerator", period=p)
            )
            operands.append(
                CalculationOperand(concept=denominator_concept, role="denominator", period=p)
            )
    else:
        operands.append(CalculationOperand(concept=numerator_concept, role="numerator"))
        operands.append(CalculationOperand(concept=denominator_concept, role="denominator"))

    return intent, operands, augmented


# ---------------------------------------------------------------------------
# Step-trace detection
# ---------------------------------------------------------------------------

_STEP_TRACE_RE = re.compile(
    r"\b(show\s+(?:each\s+)?step|show\s+how|step[\s-]*by[\s-]*step|"
    r"walk\s+(?:me\s+)?through|breakdown|break\s+down|"
    r"explain\s+(?:the\s+)?calculation|show\s+(?:the\s+)?(?:full\s+)?calculation)\b",
    re.IGNORECASE,
)


def _detect_step_trace(question: str) -> bool:
    """Return True when the question asks for a step-by-step answer."""
    rule_text = _build_rule_text(question, extract_metrics(question), extract_periods(question))
    return bool(_STEP_TRACE_RE.search(rule_text))


def _infer_step_trace_intent(
    question: str,
    metrics: list[str],
    periods: list[date],
    *,
    requires_step_trace: bool,
) -> tuple[CalculationIntent | None, list[CalculationOperand], list[str]]:
    """Infer explicit decomposition intent for step-trace questions.

    Currently this handles free-cash-flow decomposition so the composer can
    show ``operating cash flow - capital expenditure = free cash flow`` rather
    than falling back to a generic lookup.
    """
    if not requires_step_trace:
        return None, [], metrics

    lower = _build_rule_text(question, metrics, periods)
    is_fcf_decomposition = (
        "free cash flow" in lower
        and ("capital expend" in lower or "capex" in lower)
        and "operating cash flow" in lower
        and ("subtract" in lower or "subtraction" in lower)
    )
    if not is_fcf_decomposition:
        return None, [], metrics

    augmented = list(metrics)
    ordered_concepts = [
        "custom:FreeCashFlow",
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "custom:CapitalExpenditure",
    ]
    for concept in ordered_concepts:
        if concept not in augmented:
            augmented.append(concept)

    operands: list[CalculationOperand] = []
    operand_periods = periods or [None]
    for period in operand_periods:
        operands.append(
            CalculationOperand(
                concept="us-gaap:NetCashProvidedByUsedInOperatingActivities",
                role="minuend",
                period=period,
            )
        )
        operands.append(
            CalculationOperand(
                concept="custom:CapitalExpenditure",
                role="subtrahend",
                period=period,
            )
        )
        operands.append(
            CalculationOperand(
                concept="custom:FreeCashFlow",
                role="result",
                period=period,
            )
        )

    return CalculationIntent.STEP_TRACE, operands, augmented


# ---------------------------------------------------------------------------
# Answer shape inference
# ---------------------------------------------------------------------------


def _infer_answer_shape(
    question: str,
    periods: list[date],
    metrics: list[str],
) -> AnswerShape:
    """Infer the expected answer shape from question context.

    Rules (evaluated in priority order):
    1. Narrative + numeric/comparison signals → COMPOSITE (text + table question)
    2. Ranking keywords + multi-period or multi-metric → RANKING
    3. Comparison keywords + exactly 2 periods → COMPARISON
    4. Multiple periods (≥3) with one metric → RANKING
    5. Multiple metrics + narrative → COMPOSITE
    6. Default → SINGLE_VALUE
    """
    rule_text = _build_rule_text(question, metrics, periods)
    has_rank = bool(_RANKING_PATTERNS.search(rule_text))
    has_compare = bool(_COMPARISON_MULTI_PERIOD.search(rule_text))
    has_narrative = bool(_NARRATIVE_PATTERNS.search(rule_text))
    has_trend = "trend" in rule_text or "变化" in question or "趋势" in question
    has_split = bool(_COMPOSITE_SPLIT_RE.search(question))
    n_periods = len(periods)
    n_metrics = len(metrics)

    # Composite: narrative + numeric/comparison signals together.
    # E.g. "What risk factors … and how did cost change …?"
    if has_narrative and n_metrics >= 1 and (has_compare or n_periods >= 2 or has_split):
        return AnswerShape.COMPOSITE

    if n_periods >= 3 and n_metrics >= 1 and has_trend:
        return AnswerShape.TIME_SERIES

    # Ranking: explicit rank keywords OR ≥3 periods with a metric
    if has_rank and (n_periods >= 2 or n_metrics >= 2):
        return AnswerShape.RANKING
    if n_periods >= 3 and n_metrics >= 1:
        return AnswerShape.RANKING

    # Comparison: compare keywords with 2 periods
    if has_compare and n_periods == 2:
        return AnswerShape.COMPARISON

    # Composite: multiple metrics + narrative presence
    if n_metrics >= 2 and has_narrative:
        return AnswerShape.COMPOSITE

    return AnswerShape.SINGLE_VALUE


# ---------------------------------------------------------------------------
# Calculation intent inference
# ---------------------------------------------------------------------------

_PCT_CHANGE_RE = re.compile(
    r"\b(growth\s+rate|year[\s-]*over[\s-]*year|yoy|qoq|"
    r"quarter[\s-]*over[\s-]*quarter|percent(?:age)?\s+change|"
    r"grew|grow\b|growth|declined?\s+by)\b",
    re.IGNORECASE,
)
_RATIO_RE = re.compile(
    r"\b(margin|ratio|divided\s+by|as\s+a\s+percentage?\s+of|"
    r"percent(?:age)?\s+of)\b",
    re.IGNORECASE,
)
_DIFFERENCE_RE = re.compile(
    r"\b(differ\w*|subtract\w*|net\s+change|absolute\s+change)\b",
    re.IGNORECASE,
)
_COMPOSITE_SPLIT_RE = re.compile(
    r"(?:\s*,?\s*and\s+(?:how|what)\b|以及|并且|同时)",
    re.IGNORECASE,
)


def _infer_calculation_intent(
    question: str,
    metrics: list[str],
    periods: list[date],
    *,
    margin_intent: CalculationIntent | None,
) -> CalculationIntent | None:
    """Infer the calculation intent from question context.

    If ``margin_intent`` is already set (from margin detection),
    it takes precedence.

    Rules:
    1. margin_intent already set → return it
    2. pct_change keywords + ≥2 periods + 1 metric → PCT_CHANGE
    3. ranking keywords + ≥2 periods → RANK  (before RATIO to prevent false match)
    4. ratio keywords + ≥2 metrics → RATIO
    5. difference keywords + 2 periods + 1 metric → DIFFERENCE
    6. 1 metric + 1 period → LOOKUP
    7. No metrics → None (narrative)
    """
    if margin_intent is not None:
        return margin_intent

    n_periods = len(periods)
    n_metrics = len(metrics)
    rule_text = _build_rule_text(question, metrics, periods)

    # PCT_CHANGE: explicit keywords + multi-period + single metric
    if _PCT_CHANGE_RE.search(rule_text) and n_periods >= 2 and n_metrics >= 1:
        return CalculationIntent.PCT_CHANGE

    # RANK: ranking keywords + multi-period (checked before RATIO to avoid
    # false RATIO matches on "margin" when the real intent is ranking)
    if _RANKING_PATTERNS.search(rule_text) and n_periods >= 2:
        return CalculationIntent.RANK

    # RATIO: explicit ratio keywords + multiple metrics (non-margin)
    if _RATIO_RE.search(rule_text) and n_metrics >= 2:
        return CalculationIntent.RATIO

    # DIFFERENCE: difference keywords + exactly 2 periods
    if _DIFFERENCE_RE.search(rule_text) and n_periods == 2 and n_metrics >= 1:
        return CalculationIntent.DIFFERENCE

    # LOOKUP: simple factual retrieval (metric + period, no calc keywords)
    if n_metrics >= 1 and n_periods >= 1:
        return CalculationIntent.LOOKUP

    return None


def _build_operands_for_intent(
    intent: CalculationIntent | None,
    metrics: list[str],
    periods: list[date],
    *,
    existing_operands: list[CalculationOperand],
) -> list[CalculationOperand]:
    """Build calculation operands when not already set by margin detection.

    Returns the existing operands unchanged if they are non-empty (i.e.
    margin detection already populated them).
    """
    if existing_operands:
        return existing_operands

    if intent is None:
        return []

    operands: list[CalculationOperand] = []

    if intent == CalculationIntent.PCT_CHANGE and len(periods) >= 2 and metrics:
        concept = metrics[0]
        sorted_periods = sorted(periods)
        operands.append(CalculationOperand(concept=concept, role="base", period=sorted_periods[0]))
        operands.append(
            CalculationOperand(concept=concept, role="target", period=sorted_periods[-1])
        )

    elif intent == CalculationIntent.RATIO and len(metrics) >= 2:
        operands.append(CalculationOperand(concept=metrics[0], role="numerator"))
        operands.append(CalculationOperand(concept=metrics[1], role="denominator"))

    elif intent == CalculationIntent.DIFFERENCE and len(periods) >= 2 and metrics:
        concept = metrics[0]
        sorted_periods = sorted(periods)
        operands.append(CalculationOperand(concept=concept, role="base", period=sorted_periods[0]))
        operands.append(
            CalculationOperand(concept=concept, role="target", period=sorted_periods[-1])
        )

    elif intent == CalculationIntent.RANK and metrics:
        concept = metrics[0]
        for p in sorted(periods):
            operands.append(CalculationOperand(concept=concept, role="primary", period=p))

    elif intent == CalculationIntent.LOOKUP and metrics:
        concept = metrics[0]
        if periods:
            for p in periods:
                operands.append(CalculationOperand(concept=concept, role="primary", period=p))
        else:
            operands.append(CalculationOperand(concept=concept, role="primary"))

    return operands


def _build_composite_narrative_sub_query(
    question: str,
    periods: list[date],
    period_sem_map: dict[str, PeriodSemantics],
    *,
    query_language: QueryLanguage,
) -> SubQuery:
    """Build a narrative-only sub-query for composite questions."""
    target_period = max(periods) if periods else None
    narrative_text = question.strip()
    split_match = _COMPOSITE_SPLIT_RE.search(question)
    if split_match:
        narrative_text = question[: split_match.start()].strip()
    if narrative_text.endswith("?"):
        narrative_text = narrative_text[:-1]
    if target_period is not None and str(target_period.year) not in narrative_text:
        narrative_text = f"{narrative_text} for FY{target_period.year}"

    semantics = (
        period_sem_map.get(target_period.isoformat(), PeriodSemantics.UNKNOWN)
        if target_period is not None
        else PeriodSemantics.UNKNOWN
    )
    search_text = _build_normalized_search_text(
        narrative_text,
        [],
        [target_period] if target_period is not None else [],
        query_language=query_language,
    )
    weighted_terms = _extract_normalized_terms(question)
    if weighted_terms:
        search_text = " ".join([search_text, *weighted_terms]).strip()
    return SubQuery(
        text=narrative_text,
        search_text=search_text,
        target_period=target_period,
        target_concepts=[],
        period_semantics=semantics,
    )


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
        query_language = detect_query_language(question)
        periods = extract_periods(question)
        metrics = extract_metrics(question)
        semantic_scope = _detect_semantic_scope(question)
        query_type = classify_query_type(question, metrics)
        keywords = extract_keywords(question, metrics, periods)
        normalized_query = _build_normalized_search_text(
            question,
            metrics,
            periods,
            query_language=query_language,
        )

        needs_calculation = (
            query_type in (QueryType.NUMERIC_CALCULATION, QueryType.TABLE_LOOKUP)
            or bool(_CALCULATION_PATTERNS.search(_build_rule_text(question, metrics, periods)))
            or (bool(metrics) and bool(periods))  # metric + period = factual lookup
        )

        # Build period semantics map
        period_sem_map = build_period_semantics_map(periods, question)

        # Decompose into sub-queries for multi-period questions
        sub_queries: list[SubQuery] = []
        if _needs_decomposition(question, periods):
            sub_queries = _build_sub_queries(
                question,
                periods,
                metrics,
                period_sem_map,
                query_language=query_language,
                semantic_scope=semantic_scope,
            )

        # --- Phase B: Explicit intent inference ---

        # 1. Margin detection (may augment metrics & set RATIO intent)
        margin_intent, margin_operands, metrics = _infer_margin_intent(
            question,
            metrics,
            periods,
        )

        # 2. Step-trace detection
        requires_step_trace = _detect_step_trace(question)

        # 2a. Explicit decomposition intent for step-trace questions
        step_trace_intent, step_trace_operands, metrics = _infer_step_trace_intent(
            question,
            metrics,
            periods,
            requires_step_trace=requires_step_trace,
        )

        # 3. Answer shape inference
        answer_shape = _infer_answer_shape(question, periods, metrics)

        # For composite questions, add a narrative-only retrieval unit
        # so narrative evidence is not starved by numeric concept filters.
        if answer_shape == AnswerShape.COMPOSITE and not any(
            not sq.target_concepts for sq in sub_queries
        ):
            sub_queries.append(
                _build_composite_narrative_sub_query(
                    question,
                    periods,
                    period_sem_map,
                    query_language=query_language,
                )
            )

        # 4. Calculation intent (specialized step-trace takes precedence,
        # then margin intent, then generic inference)
        if step_trace_intent is not None:
            calculation_intent = step_trace_intent
        else:
            calculation_intent = _infer_calculation_intent(
                question,
                metrics,
                periods,
                margin_intent=margin_intent,
            )

        # 5. Build operands (specialized step-trace or margin operands take precedence)
        if step_trace_operands:
            calculation_operands = step_trace_operands
        else:
            calculation_operands = _build_operands_for_intent(
                calculation_intent,
                metrics,
                periods,
                existing_operands=margin_operands,
            )

        # If we inferred an intent, ensure needs_calculation is True
        if calculation_intent is not None:
            needs_calculation = True

        # If step trace requested, also ensure needs_calculation
        if requires_step_trace:
            needs_calculation = True

        return QueryPlan(
            original_query=question,
            query_language=query_language,
            normalized_query=normalized_query,
            query_type=query_type,
            semantic_scope=semantic_scope,
            sub_questions=[question],
            sub_queries=sub_queries,
            retrieval_keywords=keywords,
            required_periods=periods,
            period_semantics=period_sem_map,
            required_concepts=metrics,
            needs_calculation=needs_calculation,
            calculation_intent=calculation_intent,
            calculation_operands=calculation_operands,
            requires_step_trace=requires_step_trace,
            answer_shape=answer_shape,
        )
