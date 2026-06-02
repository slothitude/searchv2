import math
from datetime import datetime, timezone
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from models.knowledge import (
    Hypothesis, Prediction, Evidence, Entity, Claim
)
from config import settings


class Scientist:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def generate_hypothesis(
        self,
        claim: str,
        entity_name: str | None = None,
        goal_id: int | None = None,
    ) -> Hypothesis:
        entity_id = None
        if entity_name:
            result = await self.db.execute(
                select(Entity).where(Entity.name == entity_name)
            )
            ent = result.scalar_one_or_none()
            if ent:
                entity_id = ent.id

        hyp = Hypothesis(
            claim=claim,
            entity_id=entity_id,
            goal_id=goal_id,
            confidence=0.1,  # starts low, builds with evidence
        )
        self.db.add(hyp)
        await self.db.flush()
        return hyp

    async def generate_predictions(
        self, hypothesis_id: int, predictions: list[dict]
    ) -> list[Prediction]:
        created = []
        for p in predictions:
            pred = Prediction(
                hypothesis_id=hypothesis_id,
                prediction_text=p.get("text", ""),
                expected_evidence=p.get("evidence", ""),
            )
            self.db.add(pred)
            created.append(pred)
        await self.db.flush()
        return created

    async def add_evidence(
        self,
        hypothesis_id: int,
        content: str,
        direction: str = "supporting",
        prediction_id: int | None = None,
        source_url: str = "",
        confidence_impact: float = 0.1,
    ) -> Evidence:
        ev = Evidence(
            hypothesis_id=hypothesis_id,
            prediction_id=prediction_id,
            direction=direction,
            content=content,
            source_url=source_url,
            confidence_impact=confidence_impact,
        )
        self.db.add(ev)
        await self.db.flush()

        # Update hypothesis confidence (Bayesian-lite)
        await self._update_confidence(hypothesis_id)
        return ev

    async def _update_confidence(self, hypothesis_id: int) -> float:
        result = await self.db.execute(
            select(Hypothesis).where(Hypothesis.id == hypothesis_id)
        )
        hyp = result.scalar_one_or_none()
        if not hyp:
            return 0.0

        ev_result = await self.db.execute(
            select(Evidence).where(Evidence.hypothesis_id == hypothesis_id)
        )
        evidences = list(ev_result.scalars())

        if not evidences:
            return hyp.confidence

        # Simple Bayesian update
        supporting = sum(e.confidence_impact for e in evidences if e.direction == "supporting")
        contradicting = sum(e.confidence_impact for e in evidences if e.direction == "contradicting")

        # Sigmoid-like update
        log_odds = math.log(max(0.01, hyp.confidence / max(0.01, 1 - hyp.confidence)))
        log_odds += supporting - contradicting
        hyp.confidence = 1.0 / (1.0 + math.exp(-log_odds))
        hyp.confidence = max(0.0, min(1.0, hyp.confidence))

        # Update predictions based on evidence
        for ev in evidences:
            if ev.prediction_id:
                pred_result = await self.db.execute(
                    select(Prediction).where(Prediction.id == ev.prediction_id)
                )
                pred = pred_result.scalar_one_or_none()
                if pred and pred.status == "pending":
                    if ev.direction == "supporting" and ev.confidence_impact > 0.3:
                        pred.status = "confirmed"
                    elif ev.direction == "contradicting" and ev.confidence_impact > 0.3:
                        pred.status = "refuted"

        # Update hypothesis status
        all_preds = await self.db.execute(
            select(Prediction).where(Prediction.hypothesis_id == hypothesis_id)
        )
        preds = list(all_preds.scalars())
        if preds and all(p.status in ("confirmed", "refuted") for p in preds):
            confirmed = sum(1 for p in preds if p.status == "confirmed")
            hyp.status = "confirmed" if confirmed > len(preds) / 2 else "refuted"

        await self.db.flush()
        return hyp.confidence

    async def list_hypotheses(
        self,
        status: str | None = None,
        entity_name: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        q = select(Hypothesis).order_by(desc(Hypothesis.confidence)).limit(limit)
        if status:
            q = q.where(Hypothesis.status == status)
        if min_confidence > 0:
            q = q.where(Hypothesis.confidence >= min_confidence)
        if entity_name:
            result = await self.db.execute(
                select(Entity).where(Entity.name == entity_name)
            )
            ent = result.scalar_one_or_none()
            if ent:
                q = q.where(Hypothesis.entity_id == ent.id)

        result = await self.db.execute(q)
        out = []
        for h in result.scalars():
            entity_name_out = None
            if h.entity_id:
                ent = await self.db.execute(
                    select(Entity).where(Entity.id == h.entity_id)
                )
                e = ent.scalar_one_or_none()
                if e:
                    entity_name_out = e.name

            out.append({
                "id": h.id, "claim": h.claim, "status": h.status,
                "confidence": round(h.confidence, 4),
                "entity": entity_name_out,
                "created_at": h.created_at.isoformat(),
            })
        return out

    async def get_hypothesis_detail(self, hyp_id: int) -> dict | None:
        result = await self.db.execute(
            select(Hypothesis).where(Hypothesis.id == hyp_id)
        )
        hyp = result.scalar_one_or_none()
        if not hyp:
            return None

        preds = await self.db.execute(
            select(Prediction).where(Prediction.hypothesis_id == hyp_id)
        )
        evs = await self.db.execute(
            select(Evidence).where(Evidence.hypothesis_id == hyp_id)
        )

        return {
            "id": hyp.id, "claim": hyp.claim, "status": hyp.status,
            "confidence": round(hyp.confidence, 4),
            "predictions": [
                {"id": p.id, "text": p.prediction_text,
                 "status": p.status, "evidence": p.expected_evidence}
                for p in preds.scalars()
            ],
            "evidence": [
                {"id": e.id, "content": e.content, "direction": e.direction,
                 "source": e.source_url, "impact": e.confidence_impact}
                for e in evs.scalars()
            ],
        }

    async def suggest_experiments(self, hyp_id: int) -> list[dict]:
        """4B judge: suggest what to investigate next for a hypothesis."""
        hyp = await self.db.execute(
            select(Hypothesis).where(Hypothesis.id == hyp_id)
        )
        h = hyp.scalar_one_or_none()
        if not h:
            return []

        pending_preds = await self.db.execute(
            select(Prediction).where(
                Prediction.hypothesis_id == hyp_id,
                Prediction.status == "pending",
            )
        )
        suggestions = []
        for p in pending_preds.scalars():
            suggestions.append({
                "prediction": p.prediction_text,
                "expected_evidence": p.expected_evidence,
                "suggested_actions": [
                    f"Search for evidence: {p.expected_evidence}",
                    f"Browse relevant sources for: {p.prediction_text}",
                ],
            })
        return suggestions
