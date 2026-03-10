"""Build a searchable concept catalog from SEC companyfacts data."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tesla_finrag.models import ConceptCatalogEntry

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_COMPANYFACTS = _PROJECT_ROOT / "data" / "raw" / "companyfacts.json"

_CURATED_ALIASES: dict[str, list[str]] = {
    "us-gaap:Revenues": ["revenue", "revenues", "total revenue", "sales", "营收", "收入"],
    "us-gaap:GrossProfit": ["gross profit", "gross margin", "毛利润", "毛利", "毛利率"],
    "us-gaap:OperatingIncomeLoss": [
        "operating income",
        "operating profit",
        "operating margin",
        "营业利润",
        "营业利润率",
    ],
    "us-gaap:CostOfRevenue": [
        "cost of revenue",
        "cost of revenues",
        "cost of automotive revenue",
        "汽车销售成本",
    ],
    "us-gaap:ResearchAndDevelopmentExpense": [
        "research and development",
        "r&d",
        "研发费用",
    ],
    "us-gaap:CashAndCashEquivalentsAtCarryingValue": [
        "cash and cash equivalents",
        "cash position",
        "现金及现金等价物",
    ],
    "custom:FreeCashFlow": ["free cash flow", "fcf", "自由现金流"],
    "custom:CapitalExpenditure": ["capital expenditure", "capex", "资本开支"],
}


def default_companyfacts_path() -> Path:
    """Return the repo-local Tesla companyfacts source."""
    return _DEFAULT_COMPANYFACTS


def _camel_case_aliases(local_name: str) -> list[str]:
    tokens = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", local_name).split()
    lowered = " ".join(token.lower() for token in tokens).strip()
    aliases = [lowered] if lowered else []
    if lowered.endswith("s"):
        aliases.append(lowered[:-1])
    else:
        aliases.append(f"{lowered}s")
    return [alias for alias in aliases if alias]


def _label_aliases(label: str) -> list[str]:
    lowered = label.lower().strip()
    aliases = {lowered}
    aliases.add(lowered.replace(",", ""))
    aliases.add(lowered.replace(" and ", " & "))
    aliases.add(lowered.replace("&", "and"))
    return [alias for alias in aliases if alias]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", value.strip().lower())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def build_companyfacts_catalog(
    companyfacts_path: Path | None = None,
    *,
    namespaces: tuple[str, ...] = ("us-gaap", "dei"),
) -> list[ConceptCatalogEntry]:
    """Build catalog entries from SEC companyfacts.

    The resulting catalog is intentionally lightweight and can be embedded or
    vectorized with different backends. Semantic acceptance thresholds are
    handled downstream by a model-calibrated policy rather than by the catalog.
    """

    path = companyfacts_path or _DEFAULT_COMPANYFACTS
    data = json.loads(path.read_text(encoding="utf-8"))

    entries: list[ConceptCatalogEntry] = []
    all_facts = data.get("facts", {})
    for namespace in namespaces:
        ns_facts = all_facts.get(namespace, {})
        for local_name, concept_data in sorted(ns_facts.items()):
            concept = f"{namespace}:{local_name}"
            label = concept_data.get("label") or local_name
            description = concept_data.get("description") or ""
            aliases = _dedupe(
                [
                    *_label_aliases(label),
                    *_camel_case_aliases(local_name),
                    *_CURATED_ALIASES.get(concept, []),
                ]
            )
            entries.append(
                ConceptCatalogEntry(
                    concept=concept,
                    label=label,
                    description=description,
                    namespace=namespace,
                    local_name=local_name,
                    generated_aliases=aliases,
                    embedding_text=" | ".join(
                        part for part in [concept, label, description, " | ".join(aliases)] if part
                    ),
                )
            )

    entries.extend(
        [
            ConceptCatalogEntry(
                concept="custom:FreeCashFlow",
                label="Free Cash Flow",
                description="Derived as operating cash flow minus capital expenditure.",
                namespace="custom",
                local_name="FreeCashFlow",
                generated_aliases=_dedupe(_CURATED_ALIASES["custom:FreeCashFlow"]),
                embedding_text=(
                    "custom:FreeCashFlow | Free Cash Flow | "
                    "operating cash flow minus capital expenditure"
                ),
            ),
            ConceptCatalogEntry(
                concept="custom:CapitalExpenditure",
                label="Capital Expenditure",
                description="Derived from payments to acquire property, plant, and equipment.",
                namespace="custom",
                local_name="CapitalExpenditure",
                generated_aliases=_dedupe(_CURATED_ALIASES["custom:CapitalExpenditure"]),
                embedding_text=(
                    "custom:CapitalExpenditure | Capital Expenditure | "
                    "payments to acquire property plant and equipment"
                ),
            ),
        ]
    )
    return entries
