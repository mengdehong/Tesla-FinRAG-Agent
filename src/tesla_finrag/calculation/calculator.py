"""Structured financial calculator.

Performs aggregation, period-over-period change, and ranking operations
over :class:`FactRecord` collections.  Implements :class:`CalculationService`.

All arithmetic runs in dedicated code — the language model never invents
the numbers.

Includes derived-period logic (e.g. Q4 = FY - Q1 - Q2 - Q3) and
period-compatibility validation to prevent mixing incompatible
period semantics in calculations.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from tesla_finrag.models import FactRecord, PeriodSemantics
from tesla_finrag.services import CalculationService


class CalcOp(StrEnum):
    """Supported calculation operations."""

    SUM = "sum"
    AVERAGE = "average"
    MAX = "max"
    MIN = "min"
    CHANGE = "change"
    PERCENT_CHANGE = "percent_change"
    RATIO = "ratio"
    DIFFERENCE = "difference"
    RANK = "rank"


class PeriodIncompatibleError(ValueError):
    """Raised when a calculation would mix incompatible period semantics."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


# ---------------------------------------------------------------------------
# Period-compatibility helpers
# ---------------------------------------------------------------------------

# Approximate quarter-end months for standard fiscal calendar
_QUARTER_END_MONTHS = {3, 6, 9}
_FY_END_MONTH = 12


def classify_fact_period(fact: FactRecord) -> PeriodSemantics:
    """Classify a fact's period semantics from its date metadata.

    Rules:
    - instant facts -> INSTANT
    - period_end in December with period_start in January -> ANNUAL_CUMULATIVE
    - period_end in a standard quarter-end month -> QUARTERLY_STANDALONE
    - otherwise -> UNKNOWN
    """
    if fact.is_instant:
        return PeriodSemantics.INSTANT
    if fact.period_end.month == _FY_END_MONTH and fact.period_end.day == 31:
        if fact.period_start and fact.period_start.month == 1 and fact.period_start.day == 1:
            return PeriodSemantics.ANNUAL_CUMULATIVE
        if fact.period_start and fact.period_start.month == 10 and fact.period_start.day == 1:
            return PeriodSemantics.DERIVED_STANDALONE
        # Even without explicit period_start, a 12/31 fact from a 10-K
        # is likely annual cumulative
        return PeriodSemantics.ANNUAL_CUMULATIVE
    if fact.period_end.month in _QUARTER_END_MONTHS:
        return PeriodSemantics.QUARTERLY_STANDALONE
    return PeriodSemantics.UNKNOWN


def are_periods_compatible(
    sem_a: PeriodSemantics,
    sem_b: PeriodSemantics,
) -> bool:
    """Check if two period semantics are compatible for comparison/arithmetic.

    Same semantics are always compatible except UNKNOWN, which is treated
    as ambiguous and therefore incompatible with arithmetic. Annual vs
    quarterly is NOT compatible for direct comparison.
    """
    if sem_a == PeriodSemantics.UNKNOWN or sem_b == PeriodSemantics.UNKNOWN:
        return False
    if {
        sem_a,
        sem_b,
    } == {
        PeriodSemantics.QUARTERLY_STANDALONE,
        PeriodSemantics.DERIVED_STANDALONE,
    }:
        return True
    return sem_a == sem_b


def derive_standalone_quarter(
    concept: str,
    target_year: int,
    target_quarter: int,
    facts: list[FactRecord],
) -> tuple[float | None, list[str]]:
    """Derive a standalone quarter value from FY and other quarters.

    For Q4: Q4 = FY - Q1 - Q2 - Q3
    For other quarters that lack direct data: not yet supported.

    Returns:
        (derived_value, trace) or (None, trace) if derivation fails.
    """
    if target_quarter != 4:
        return None, [f"Derived-period logic only supports Q4; got Q{target_quarter}"]

    # Find FY value
    fy_end = date(target_year, 12, 31)
    fy_facts = [f for f in facts if f.concept == concept and f.period_end == fy_end]
    if not fy_facts:
        return None, [f"No FY{target_year} value found for {concept}"]

    fy_val = fy_facts[0].value * fy_facts[0].scale
    trace = [f"Deriving Q4 {target_year} for {concept}:"]
    trace.append(f"  FY{target_year}: {fy_val:,.2f}")

    # Find Q1, Q2, Q3
    quarter_ends = {
        1: date(target_year, 3, 31),
        2: date(target_year, 6, 30),
        3: date(target_year, 9, 30),
    }
    q_vals: dict[int, float] = {}
    for q, pe in quarter_ends.items():
        q_facts = [f for f in facts if f.concept == concept and f.period_end == pe]
        if not q_facts:
            return None, trace + [f"  Missing Q{q} {target_year} — cannot derive Q4"]
        q_vals[q] = q_facts[0].value * q_facts[0].scale
        trace.append(f"  Q{q} {target_year}: {q_vals[q]:,.2f}")

    derived = fy_val - sum(q_vals.values())
    trace.append(
        f"  Q4 = FY - Q1 - Q2 - Q3 = {fy_val:,.2f} - "
        f"{q_vals[1]:,.2f} - {q_vals[2]:,.2f} - {q_vals[3]:,.2f} = {derived:,.2f}"
    )
    return derived, trace


class StructuredCalculator(CalculationService):
    """Explicit financial calculator for deterministic numeric reasoning.

    Replaces free-form model arithmetic with structured operations that
    record every step in a human-readable trace.
    """

    # ------------------------------------------------------------------
    # CalculationService interface
    # ------------------------------------------------------------------

    def calculate(
        self,
        expression: str,
        facts: list[FactRecord],
    ) -> tuple[float, list[str]]:
        """Evaluate ``expression`` against ``facts``.

        The expression is a simple DSL:
        - ``sum(<concept>)``
        - ``avg(<concept>)``
        - ``max(<concept>)``
        - ``min(<concept>)``
        - ``change(<concept>, <period1>, <period2>)``
        - ``pct_change(<concept>, <period1>, <period2>)``
        - ``ratio(<concept_a>, <concept_b>, <period>)``
        - ``<concept_a> / <concept_b>`` (simple ratio shorthand)
        - ``<concept_a> - <concept_b>`` (simple difference shorthand)

        For simple concept lookups, the expression is just the concept name.

        Returns:
            (result, trace) where trace lists the arithmetic steps.
        """
        expression = expression.strip()

        if expression.startswith("sum("):
            concept = expression[4:].rstrip(")")
            return self.aggregate(facts, concept, CalcOp.SUM)
        if expression.startswith("avg("):
            concept = expression[4:].rstrip(")")
            return self.aggregate(facts, concept, CalcOp.AVERAGE)
        if expression.startswith("max("):
            concept = expression[4:].rstrip(")")
            return self.aggregate(facts, concept, CalcOp.MAX)
        if expression.startswith("min("):
            concept = expression[4:].rstrip(")")
            return self.aggregate(facts, concept, CalcOp.MIN)
        if expression.startswith("change(") or expression.startswith("pct_change("):
            return self._parse_change_expr(expression, facts)
        if expression.startswith("ratio("):
            return self._parse_ratio_expr(expression, facts)
        if " / " in expression:
            parts = expression.split(" / ", 1)
            return self._simple_ratio(facts, parts[0].strip(), parts[1].strip())
        if " - " in expression:
            parts = expression.split(" - ", 1)
            return self._simple_difference(facts, parts[0].strip(), parts[1].strip())

        # Default: look up a single concept value
        matching = [f for f in facts if f.concept == expression]
        if not matching:
            return 0.0, [f"No facts found for concept '{expression}'"]
        fact = matching[0]
        val = fact.value * fact.scale
        return val, [f"{fact.label} = {val:,.2f} {fact.unit} (period ending {fact.period_end})"]

    # ------------------------------------------------------------------
    # Public calculation methods
    # ------------------------------------------------------------------

    def aggregate(
        self,
        facts: list[FactRecord],
        concept: str,
        operation: CalcOp,
    ) -> tuple[float, list[str]]:
        """Aggregate values for a concept across all matching facts.

        Args:
            facts: Available fact records.
            concept: XBRL concept name to aggregate.
            operation: SUM, AVERAGE, MAX, or MIN.

        Returns:
            (result, trace) tuple.
        """
        matching = [f for f in facts if f.concept == concept]
        if not matching:
            return 0.0, [f"No facts found for concept '{concept}'"]

        values = [(f.value * f.scale, f.period_end, f.label) for f in matching]
        trace: list[str] = []

        if operation == CalcOp.SUM:
            result = sum(v for v, _, _ in values)
            trace.append(f"Sum of {concept}:")
            for val, period, label in values:
                trace.append(f"  + {val:,.2f} (period ending {period})")
            trace.append(f"  = {result:,.2f}")
        elif operation == CalcOp.AVERAGE:
            result = sum(v for v, _, _ in values) / len(values)
            trace.append(f"Average of {concept} across {len(values)} periods:")
            for val, period, label in values:
                trace.append(f"  {val:,.2f} (period ending {period})")
            trace.append(f"  = {result:,.2f}")
        elif operation == CalcOp.MAX:
            result = max(v for v, _, _ in values)
            best = max(values, key=lambda x: x[0])
            trace.append(f"Max of {concept}: {result:,.2f} (period ending {best[1]})")
        elif operation == CalcOp.MIN:
            result = min(v for v, _, _ in values)
            worst = min(values, key=lambda x: x[0])
            trace.append(f"Min of {concept}: {result:,.2f} (period ending {worst[1]})")
        else:
            return 0.0, [f"Unsupported aggregation: {operation}"]

        return result, trace

    def period_over_period(
        self,
        facts: list[FactRecord],
        concept: str,
        period_a: date,
        period_b: date,
        *,
        as_percent: bool = False,
        validate_semantics: bool = True,
    ) -> tuple[float, list[str]]:
        """Compute change from period_a to period_b for a concept.

        Args:
            facts: Available fact records.
            concept: XBRL concept to compare.
            period_a: Earlier (base) period end date.
            period_b: Later (comparison) period end date.
            as_percent: If True, return percentage change.
            validate_semantics: If True, reject incompatible period semantics.

        Returns:
            (change_value, trace) tuple.

        Raises:
            PeriodIncompatibleError: If period semantics are incompatible.
        """
        val_a = self._lookup_value(facts, concept, period_a)
        val_b = self._lookup_value(facts, concept, period_b)

        if val_a is None or val_b is None:
            missing = []
            if val_a is None:
                missing.append(f"period {period_a}")
            if val_b is None:
                missing.append(f"period {period_b}")
            return 0.0, [f"Missing {concept} for: {', '.join(missing)}"]

        # Validate period-semantic compatibility
        if validate_semantics:
            fact_a = self._lookup_fact(facts, concept, period_a)
            fact_b = self._lookup_fact(facts, concept, period_b)
            if fact_a and fact_b:
                sem_a = classify_fact_period(fact_a)
                sem_b = classify_fact_period(fact_b)
                if not are_periods_compatible(sem_a, sem_b):
                    raise PeriodIncompatibleError(
                        f"Cannot compare {concept}: {sem_a.value} ({period_a}) "
                        f"vs {sem_b.value} ({period_b})",
                        details={
                            "concept": concept,
                            "period_a": str(period_a),
                            "period_b": str(period_b),
                            "semantics_a": sem_a.value,
                            "semantics_b": sem_b.value,
                        },
                    )

        change = val_b - val_a
        trace = [
            f"{concept} at {period_a}: {val_a:,.2f}",
            f"{concept} at {period_b}: {val_b:,.2f}",
            f"Change: {val_b:,.2f} - {val_a:,.2f} = {change:,.2f}",
        ]

        if as_percent:
            if val_a == 0:
                return 0.0, trace + ["Cannot compute percentage: base value is 0"]
            pct = (change / abs(val_a)) * 100
            trace.append(f"Percentage change: {pct:,.2f}%")
            return pct, trace

        return change, trace

    def rank(
        self,
        facts: list[FactRecord],
        concept: str,
        *,
        descending: bool = True,
    ) -> tuple[float, list[str]]:
        """Rank periods by a concept value, return the top value.

        Args:
            facts: Available fact records.
            concept: XBRL concept to rank by.
            descending: If True, highest value first.

        Returns:
            (top_value, trace) with full ranking in trace.
        """
        matching = [f for f in facts if f.concept == concept]
        if not matching:
            return 0.0, [f"No facts found for concept '{concept}'"]

        ranked = sorted(
            matching,
            key=lambda f: f.value * f.scale,
            reverse=descending,
        )

        order = "highest to lowest" if descending else "lowest to highest"
        trace = [f"Ranking {concept} ({order}):"]
        for i, fact in enumerate(ranked, 1):
            val = fact.value * fact.scale
            trace.append(f"  {i}. {val:,.2f} {fact.unit} (period ending {fact.period_end})")

        top_val = ranked[0].value * ranked[0].scale
        return top_val, trace

    def compute_ratio(
        self,
        facts: list[FactRecord],
        numerator_concept: str,
        denominator_concept: str,
        period: date | None = None,
    ) -> tuple[float, list[str]]:
        """Compute a ratio between two concepts.

        Args:
            facts: Available fact records.
            numerator_concept: XBRL concept for the numerator.
            denominator_concept: XBRL concept for the denominator.
            period: If given, restrict to this period end date.

        Returns:
            (ratio, trace) tuple.
        """
        num = self._lookup_value(facts, numerator_concept, period)
        den = self._lookup_value(facts, denominator_concept, period)

        if num is None:
            return 0.0, [f"Numerator not found: {numerator_concept}"]
        if den is None:
            return 0.0, [f"Denominator not found: {denominator_concept}"]
        if den == 0:
            return 0.0, [f"Denominator is zero: {denominator_concept}"]

        ratio = num / den
        trace = [
            f"{numerator_concept}: {num:,.2f}",
            f"{denominator_concept}: {den:,.2f}",
            f"Ratio: {num:,.2f} / {den:,.2f} = {ratio:,.4f}",
        ]
        return ratio, trace

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _lookup_value(
        self,
        facts: list[FactRecord],
        concept: str,
        period: date | None = None,
    ) -> float | None:
        """Look up a single scaled value for a concept and optional period."""
        for fact in facts:
            if fact.concept != concept:
                continue
            if period is not None and fact.period_end != period:
                continue
            return fact.value * fact.scale
        return None

    def _lookup_fact(
        self,
        facts: list[FactRecord],
        concept: str,
        period: date | None = None,
    ) -> FactRecord | None:
        """Look up a single fact record for a concept and optional period."""
        for fact in facts:
            if fact.concept != concept:
                continue
            if period is not None and fact.period_end != period:
                continue
            return fact
        return None

    def _parse_change_expr(
        self, expression: str, facts: list[FactRecord]
    ) -> tuple[float, list[str]]:
        """Parse ``change(concept, period1, period2)`` expressions."""
        as_percent = expression.startswith("pct_change(")
        inner = expression.split("(", 1)[1].rstrip(")")
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) < 3:
            return 0.0, [f"Invalid change expression: {expression}"]
        concept = parts[0]
        try:
            period_a = date.fromisoformat(parts[1])
            period_b = date.fromisoformat(parts[2])
        except ValueError:
            return 0.0, [f"Invalid date format in: {expression}"]
        return self.period_over_period(facts, concept, period_a, period_b, as_percent=as_percent)

    def _parse_ratio_expr(
        self, expression: str, facts: list[FactRecord]
    ) -> tuple[float, list[str]]:
        """Parse ``ratio(concept_a, concept_b, period)`` expressions."""
        inner = expression[6:].rstrip(")")
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) < 2:
            return 0.0, [f"Invalid ratio expression: {expression}"]
        num_concept = parts[0]
        den_concept = parts[1]
        period = None
        if len(parts) >= 3:
            try:
                period = date.fromisoformat(parts[2])
            except ValueError:
                pass
        return self.compute_ratio(facts, num_concept, den_concept, period)

    def _simple_ratio(
        self, facts: list[FactRecord], concept_a: str, concept_b: str
    ) -> tuple[float, list[str]]:
        """Handle ``concept_a / concept_b`` shorthand."""
        return self.compute_ratio(facts, concept_a, concept_b)

    def _simple_difference(
        self, facts: list[FactRecord], concept_a: str, concept_b: str
    ) -> tuple[float, list[str]]:
        """Handle ``concept_a - concept_b`` shorthand."""
        val_a = self._lookup_value(facts, concept_a)
        val_b = self._lookup_value(facts, concept_b)
        if val_a is None:
            return 0.0, [f"Not found: {concept_a}"]
        if val_b is None:
            return 0.0, [f"Not found: {concept_b}"]
        diff = val_a - val_b
        trace = [
            f"{concept_a}: {val_a:,.2f}",
            f"{concept_b}: {val_b:,.2f}",
            f"Difference: {val_a:,.2f} - {val_b:,.2f} = {diff:,.2f}",
        ]
        return diff, trace
