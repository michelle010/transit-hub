"""Application facades for deterministic candidate ranking."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.candidate import CandidateEvaluation, CandidateHub, TransferContext
from app.domain.ranking import CandidateRanker
from app.services.candidate_evaluator import CandidateEvaluator
from app.services.candidate_generator import CandidateStationGenerator


class CandidateRankingService:
    """Rank already evaluated candidates without touching providers or storage."""

    def __init__(self, ranker: CandidateRanker | None = None) -> None:
        self.ranker = ranker or CandidateRanker()

    def rank(self, candidates: Sequence[CandidateEvaluation]) -> list[CandidateEvaluation]:
        return self.ranker.rank(list(candidates))


class CandidateRecommendationService:
    """Run generation, provider evaluation and deterministic ranking as one flow."""

    def __init__(
        self,
        generator: CandidateStationGenerator,
        evaluator: CandidateEvaluator,
        ranking: CandidateRankingService | None = None,
    ) -> None:
        self.generator = generator
        self.evaluator = evaluator
        self.ranking = ranking or CandidateRankingService(evaluator.ranker)

    async def recommend(self, context: TransferContext) -> list[CandidateEvaluation]:
        candidates: list[CandidateHub] = await self.generator.generate(context)
        evaluations = await self.evaluator.evaluate(context, candidates)
        return self.ranking.rank(evaluations)


__all__ = ["CandidateRankingService", "CandidateRecommendationService"]
