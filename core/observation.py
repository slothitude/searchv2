from datetime import datetime, timezone
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from models.observation import Observation, Opportunity
from config import settings


class ObservationEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def observe(
        self,
        text: str,
        source: str = "internal",
        confidence: float = 0.5,
        importance: float = 0.5,
        category: str = "general",
        raw_data: dict | None = None,
    ) -> Observation:
        obs = Observation(
            observation_text=text,
            confidence=min(1.0, max(0.0, confidence)),
            importance=min(1.0, max(0.0, importance)),
            source=source,
            category=category,
            raw_data=raw_data or {},
        )
        self.db.add(obs)
        await self.db.flush()
        return obs

    async def list_observations(
        self,
        source: str | None = None,
        category: str | None = None,
        min_importance: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        q = select(Observation).order_by(desc(Observation.created_at)).limit(limit)
        if source:
            q = q.where(Observation.source == source)
        if category:
            q = q.where(Observation.category == category)
        if min_importance > 0:
            q = q.where(Observation.importance >= min_importance)
        result = await self.db.execute(q)
        return [
            {
                "id": o.id, "text": o.observation_text,
                "confidence": o.confidence, "importance": o.importance,
                "source": o.source, "category": o.category,
                "created_at": o.created_at.isoformat(),
            }
            for o in result.scalars()
        ]

    async def identify_opportunities(
        self, observation: Observation
    ) -> list[Opportunity]:
        """2B-level: link observation to potential actions."""
        importance = observation.importance
        confidence = observation.confidence

        # Heuristic opportunity generation based on source/category
        opportunities = []
        text = observation.observation_text.lower()

        if "new" in text or "release" in text or "update" in text:
            opp = Opportunity(
                observation_id=observation.id,
                description=f"Investigate: {observation.observation_text}",
                estimated_value=importance * 100,
                estimated_cost=confidence * 10,
                urgency=importance,
                utility=importance * 100 / max(1, confidence * 10),
            )
            self.db.add(opp)
            opportunities.append(opp)

        if "error" in text or "fail" in text or "broken" in text:
            opp = Opportunity(
                observation_id=observation.id,
                description=f"Fix: {observation.observation_text}",
                estimated_value=importance * 80,
                estimated_cost=confidence * 5,
                urgency=importance * 1.5,
                utility=importance * 80 / max(1, confidence * 5),
            )
            self.db.add(opp)
            opportunities.append(opp)

        await self.db.flush()
        return opportunities


class OpportunityEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    def prioritize(self, opportunities: list[Opportunity]) -> list[Opportunity]:
        """Rank by utility = urgency * value / cost."""
        scored = []
        for opp in opportunities:
            opp.utility = opp.urgency * opp.estimated_value / max(0.1, opp.estimated_cost)
            scored.append(opp)
        scored.sort(key=lambda o: o.utility, reverse=True)
        return scored

    async def auto_accept(self, opportunity: Opportunity) -> bool:
        """Auto-accept if utility exceeds threshold."""
        opportunity.utility = (
            opportunity.urgency * opportunity.estimated_value
            / max(0.1, opportunity.estimated_cost)
        )
        if opportunity.utility >= settings.auto_accept_utility_threshold:
            opportunity.status = "accepted"
            await self.db.flush()
            return True
        return False

    async def list_opportunities(
        self,
        status: str | None = None,
        min_utility: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        q = select(Opportunity).order_by(
            desc(Opportunity.utility)
        ).limit(limit)
        if status:
            q = q.where(Opportunity.status == status)
        if min_utility > 0:
            q = q.where(Opportunity.utility >= min_utility)
        result = await self.db.execute(q)
        return [
            {
                "id": o.id, "description": o.description,
                "value": o.estimated_value, "cost": o.estimated_cost,
                "urgency": o.urgency, "utility": o.utility,
                "status": o.status, "observation_id": o.observation_id,
                "created_at": o.created_at.isoformat(),
            }
            for o in result.scalars()
        ]

    async def update_status(self, opp_id: int, status: str) -> bool:
        result = await self.db.execute(
            select(Opportunity).where(Opportunity.id == opp_id)
        )
        opp = result.scalar_one_or_none()
        if not opp:
            return False
        opp.status = status
        await self.db.flush()
        return True
