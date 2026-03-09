from datetime import date
from tesla_finrag.models import FactRecord, PeriodSemantics
from tesla_finrag.calculation.calculator import StructuredCalculator, CalcOp

def _make_fact(concept: str, value: float, period: date) -> FactRecord:
    from test_provider import _mock_embedding_response # dummy
    from tesla_finrag.models import DocumentReference
    import uuid
    return FactRecord(
        id=str(uuid.uuid4()),
        concept=concept,
        value=value,
        scale=1.0,
        unit="USD",
        period_start=None,
        period_end=period,
        is_instant=False,
        label=concept,
        source_doc=DocumentReference(id="test", title="test", doc_type="test", year=2023)
    )

def test_margin_direction():
    calc = StructuredCalculator()
    p = date(2023, 12, 31)
    facts = [
        _make_fact("us-gaap:Revenues", 1000.0, p),
        _make_fact("us-gaap:GrossProfit", 250.0, p),
    ]
    
    # "us-gaap:GrossProfit / us-gaap:Revenues"
    ratio, trace = calc.calculate("ratio(us-gaap:GrossProfit, us-gaap:Revenues)", facts)
    assert ratio == 0.25 # 250 / 1000
    
    # If builder builds concept_a / concept_b:
    ratio2, _ = calc._simple_ratio(facts, "us-gaap:GrossProfit", "us-gaap:Revenues")
    assert ratio2 == 0.25
    

