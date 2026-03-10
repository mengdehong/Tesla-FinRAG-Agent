"""Resolve user metric mentions into XBRL concepts."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Protocol

from tesla_finrag.models import (
    ConceptCandidate,
    ConceptCatalogEntry,
    ConceptResolution,
    ConceptResolutionMethod,
)


class TextEmbeddingBackend(Protocol):
    """Small embedding contract shared with runtime providers."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


_SAFE_EQUIVALENTS: dict[str, list[str]] = {
    "us-gaap:CostOfGoodsAndServicesSold": ["us-gaap:CostOfRevenue"],
    "us-gaap:CostOfRevenue": ["us-gaap:CostOfGoodsAndServicesSold"],
}
_SAFE_EQUIVALENT_MENTION_MAP: dict[str, str] = {
    "cost of revenue": "us-gaap:CostOfGoodsAndServicesSold",
    "cost of sales": "us-gaap:CostOfGoodsAndServicesSold",
    "cogs": "us-gaap:CostOfGoodsAndServicesSold",
    "automotive sales cost": "us-gaap:CostOfGoodsAndServicesSold",
    "car sales cost": "us-gaap:CostOfGoodsAndServicesSold",
    "收入成本": "us-gaap:CostOfGoodsAndServicesSold",
    "营业成本": "us-gaap:CostOfGoodsAndServicesSold",
    "汽车业务成本": "us-gaap:CostOfGoodsAndServicesSold",
    "汽车销售成本": "us-gaap:CostOfGoodsAndServicesSold",
}

_MENTION_STOPWORDS = {"tesla", "teslas", "company", "registrant"}


def _normalize_lookup(text: str) -> str:
    cleaned = text.lower().replace("_", " ")
    cleaned = re.sub(r"['’]s\b", "", cleaned)
    # Keep Unicode word characters so Chinese aliases and mentions do not
    # collapse to an empty string and spuriously exact-match unrelated entries.
    cleaned = re.sub(r"[^\w\s:&-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _normalize_safe_equivalent_mention(text: str) -> str:
    cleaned = text.lower().replace("_", " ")
    cleaned = re.sub(r"['’]s\b", "", cleaned)
    cleaned = re.sub(r"[^\w\s:&-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w]+", _normalize_lookup(text))
        if token and token not in _MENTION_STOPWORDS
    }


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


class SemanticConceptResolver:
    """Resolve mentions with exact, lexical, semantic, and safe-equivalent fallbacks."""

    def __init__(
        self,
        entries: list[ConceptCatalogEntry],
        *,
        embedding_backend: TextEmbeddingBackend | None = None,
        top_k: int = 5,
        semantic_accept_score: float = 0.78,
        semantic_accept_gap: float = 0.05,
        calibrated: bool = False,
        calibration_version: str = "uncalibrated",
    ) -> None:
        self._entries = entries
        self._top_k = top_k
        self._embedding_backend = embedding_backend
        self._semantic_accept_score = semantic_accept_score
        self._semantic_accept_gap = semantic_accept_gap
        self._calibrated = calibrated
        self._calibration_version = calibration_version
        self._by_alias: dict[str, list[ConceptCatalogEntry]] = defaultdict(list)
        self._entry_embeddings: dict[str, list[float]] = {}
        for entry in entries:
            for alias in [entry.concept, entry.label, entry.local_name, *entry.generated_aliases]:
                normalized_alias = _normalize_lookup(alias)
                if not normalized_alias:
                    continue
                self._by_alias[normalized_alias].append(entry)

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "calibrated": self._calibrated,
            "calibration_version": self._calibration_version,
            "semantic_accept_score": self._semantic_accept_score,
            "semantic_accept_gap": self._semantic_accept_gap,
        }

    def resolve_mentions(
        self,
        mentions: list[str],
        *,
        exact_concepts: list[str] | None = None,
    ) -> list[ConceptResolution]:
        resolutions: list[ConceptResolution] = []
        exact_concepts = exact_concepts or []
        for mention in mentions:
            resolution = self.resolve_mention(mention)
            if not resolution.accepted and exact_concepts:
                for concept in exact_concepts:
                    if any(candidate.concept == concept for candidate in resolution.candidates):
                        resolution = resolution.model_copy(
                            update={
                                "accepted": True,
                                "concept": concept,
                                "method": ConceptResolutionMethod.SAFE_EQUIVALENT,
                                "confidence": max(resolution.confidence or 0.0, 0.6),
                                "rationale": (
                                    "Accepted via fallback concept already present in the "
                                    "rule-based plan."
                                ),
                            }
                        )
                        break
            resolutions.append(resolution)
        return resolutions

    def resolve_mention(self, mention: str) -> ConceptResolution:
        normalized = _normalize_lookup(mention)
        if not normalized:
            return ConceptResolution(
                mention=mention,
                method=ConceptResolutionMethod.UNRESOLVED,
                accepted=False,
                confidence=0.0,
                rationale="The normalized mention was empty after cleanup.",
            )
        forced_equivalent = self._resolve_forced_safe_equivalent(
            _normalize_safe_equivalent_mention(mention)
        )
        if forced_equivalent is not None:
            return forced_equivalent

        exact = self._resolve_exact(normalized)
        if exact is not None:
            return exact

        lexical = self._resolve_lexical(normalized)
        if lexical is not None:
            return lexical

        semantic_candidates = self._semantic_candidates(normalized)
        if semantic_candidates:
            top_1 = semantic_candidates[0]
            top_2_score = semantic_candidates[1].score if len(semantic_candidates) > 1 else 0.0
            gap = top_1.score - top_2_score

            # These thresholds are model-calibrated defaults, not model-agnostic truths.
            # If the embedding backend changes, callers should treat these values as stale
            # until calibration is rerun for the new similarity distribution.
            if (
                self._calibrated
                and top_1.score >= self._semantic_accept_score
                and gap >= self._semantic_accept_gap
            ):
                return ConceptResolution(
                    mention=mention,
                    method=ConceptResolutionMethod.SEMANTIC,
                    accepted=True,
                    concept=top_1.concept,
                    confidence=top_1.score,
                    rationale=(
                        "Accepted by calibrated semantic retrieval policy "
                        f"(gap={gap:.3f}, calibration={self._calibration_version})."
                    ),
                    candidates=semantic_candidates,
                )

            return ConceptResolution(
                mention=mention,
                method=ConceptResolutionMethod.UNRESOLVED,
                accepted=False,
                confidence=top_1.score,
                rationale=(
                    "Semantic candidates found but resolver stayed conservative because the "
                    "backend is uncalibrated or the top match was not decisive."
                ),
                candidates=semantic_candidates,
            )

        return ConceptResolution(
            mention=mention,
            method=ConceptResolutionMethod.UNRESOLVED,
            accepted=False,
            confidence=0.0,
            rationale="No exact, lexical, or semantic concept candidate was strong enough.",
        )

    def safe_equivalents_for(self, concept: str) -> list[str]:
        return list(_SAFE_EQUIVALENTS.get(concept, []))

    def _resolve_forced_safe_equivalent(self, normalized: str) -> ConceptResolution | None:
        concept = _SAFE_EQUIVALENT_MENTION_MAP.get(normalized)
        if concept is None:
            return None
        label = concept.split(":")[-1]
        for entry in self._entries:
            if entry.concept == concept:
                label = entry.label
                break
        return ConceptResolution(
            mention=normalized,
            method=ConceptResolutionMethod.SAFE_EQUIVALENT,
            accepted=True,
            concept=concept,
            confidence=1.0,
            rationale=(
                "Accepted by the curated safe-equivalent override for common cost-of-sales phrases."
            ),
            candidates=[
                ConceptCandidate(
                    concept=concept,
                    label=label,
                    score=1.0,
                    method=ConceptResolutionMethod.SAFE_EQUIVALENT,
                    rationale="Curated safe-equivalent mention mapping.",
                )
            ],
        )

    def _resolve_exact(self, normalized: str) -> ConceptResolution | None:
        entries = self._by_alias.get(normalized, [])
        if not entries:
            return None
        entry = entries[0]
        return ConceptResolution(
            mention=normalized,
            method=ConceptResolutionMethod.EXACT,
            accepted=True,
            concept=entry.concept,
            confidence=1.0,
            rationale=f"Exact match on '{normalized}'.",
            candidates=[
                ConceptCandidate(
                    concept=entry.concept,
                    label=entry.label,
                    score=1.0,
                    method=ConceptResolutionMethod.EXACT,
                    rationale="Exact alias match.",
                )
            ],
        )

    def _resolve_lexical(self, normalized: str) -> ConceptResolution | None:
        mention_tokens = _tokenize(normalized)
        if not mention_tokens:
            return None
        scored: list[tuple[float, ConceptCatalogEntry]] = []
        for entry in self._entries:
            entry_tokens = _tokenize(
                " ".join([entry.label, entry.local_name, *entry.generated_aliases])
            )
            overlap = len(mention_tokens & entry_tokens)
            if overlap == 0:
                continue
            score = overlap / max(len(mention_tokens), 1)
            scored.append((score, entry))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        top_score, top_entry = scored[0]
        candidates = [
            ConceptCandidate(
                concept=entry.concept,
                label=entry.label,
                score=score,
                method=ConceptResolutionMethod.LEXICAL,
                rationale="Token overlap against label and aliases.",
            )
            for score, entry in scored[: self._top_k]
        ]
        if top_score >= 0.8:
            return ConceptResolution(
                mention=normalized,
                method=ConceptResolutionMethod.LEXICAL,
                accepted=True,
                concept=top_entry.concept,
                confidence=top_score,
                rationale="Accepted by lexical overlap.",
                candidates=candidates,
            )
        return ConceptResolution(
            mention=normalized,
            method=ConceptResolutionMethod.UNRESOLVED,
            accepted=False,
            confidence=top_score,
            rationale="Lexical overlap found but was not decisive enough.",
            candidates=candidates,
        )

    def _semantic_candidates(self, normalized: str) -> list[ConceptCandidate]:
        if self._embedding_backend is None:
            return []
        query_embeddings = self._embedding_backend.embed_texts([normalized])
        if not query_embeddings:
            return []
        query_embedding = query_embeddings[0]

        uncached = [
            entry.embedding_text
            for entry in self._entries
            if entry.concept not in self._entry_embeddings
        ]
        if uncached:
            embedded = self._embedding_backend.embed_texts(uncached)
            for text, vector in zip(uncached, embedded, strict=False):
                for entry in self._entries:
                    if entry.embedding_text == text and entry.concept not in self._entry_embeddings:
                        self._entry_embeddings[entry.concept] = vector
                        break

        scored: list[ConceptCandidate] = []
        for entry in self._entries:
            vector = self._entry_embeddings.get(entry.concept)
            if vector is None:
                continue
            scored.append(
                ConceptCandidate(
                    concept=entry.concept,
                    label=entry.label,
                    score=_cosine_similarity(query_embedding, vector),
                    method=ConceptResolutionMethod.SEMANTIC,
                    rationale="Cosine similarity against concept embedding text.",
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: self._top_k]
