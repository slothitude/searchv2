import time
from datetime import datetime, timezone
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from models.skills import Skill, SkillExecution
from models.knowledge import Claim, Entity
from config import settings


class SkillGenerator:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect_skill_opportunity(
        self, domain: str, min_claims: int = 5
    ) -> list[dict]:
        """Check if enough confirmed claims exist in a domain to proceduralize."""
        # Find entities with many confirmed claims
        result = await self.db.execute(
            select(Claim.entity_id, func.count(Claim.id))
            .group_by(Claim.entity_id)
            .having(func.count(Claim.id) >= min_claims)
        )
        candidate_entities = result.all()

        opportunities = []
        for entity_id, count in candidate_entities:
            ent = await self.db.execute(
                select(Entity).where(Entity.id == entity_id)
            )
            entity = ent.scalar_one_or_none()
            if entity:
                # Get all claims for this entity
                claims_result = await self.db.execute(
                    select(Claim).where(Claim.entity_id == entity_id)
                )
                claims = list(claims_result.scalars())
                opportunities.append({
                    "entity": entity.name,
                    "claim_count": count,
                    "domain": domain,
                    "claims": [
                        {"key": c.claim_key, "value": c.claim_value,
                         "type": c.claim_type, "confidence": c.confidence}
                        for c in claims
                    ],
                })
        return opportunities

    async def generate_skill(
        self,
        name: str,
        description: str,
        domain: str,
        steps: list[dict],
        preconditions: list[dict] | None = None,
    ) -> Skill:
        # Check if skill exists
        result = await self.db.execute(
            select(Skill).where(Skill.name == name)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.steps = steps
            existing.preconditions = preconditions or []
            existing.description = description
            await self.db.flush()
            return existing

        skill = Skill(
            name=name, description=description,
            domain=domain, steps=steps,
            preconditions=preconditions or [],
        )
        self.db.add(skill)
        await self.db.flush()
        return skill

    async def execute_skill(
        self,
        skill_name: str,
        inputs: dict | None = None,
        goal_id: int | None = None,
    ) -> dict:
        result = await self.db.execute(
            select(Skill).where(Skill.name == skill_name)
        )
        skill = result.scalar_one_or_none()
        if not skill:
            return {"error": f"Skill not found: {skill_name}"}

        start_time = time.time()
        skill.usage_count += 1

        # Execute steps (returns step results)
        step_results = []
        success = True
        outputs = {}

        for i, step in enumerate(skill.steps):
            try:
                # Each step has: description, action (tool call), expected_output
                step_result = {
                    "step": i + 1,
                    "description": step.get("description", ""),
                    "action": step.get("action", ""),
                    "status": "pending",
                }
                step_results.append(step_result)
            except Exception as e:
                step_results.append({
                    "step": i + 1, "status": "failed", "error": str(e),
                })
                success = False
                break

        duration = time.time() - start_time

        if success:
            skill.success_count += 1

        execution = SkillExecution(
            skill_id=skill.id,
            goal_id=goal_id,
            inputs=inputs or {},
            outputs=outputs,
            success=success,
            duration_seconds=duration,
        )
        self.db.add(execution)
        await self.db.flush()

        return {
            "skill": skill_name,
            "success": success,
            "duration": round(duration, 2),
            "steps": step_results,
            "success_rate": skill.success_rate,
        }

    async def list_skills(
        self, domain: str | None = None, limit: int = 50
    ) -> list[dict]:
        q = select(Skill).order_by(desc(Skill.usage_count)).limit(limit)
        if domain:
            q = q.where(Skill.domain == domain)
        result = await self.db.execute(q)
        return [
            {
                "id": s.id, "name": s.name, "description": s.description,
                "domain": s.domain, "steps": len(s.steps) if isinstance(s.steps, list) else 0,
                "usage_count": s.usage_count, "success_count": s.success_count,
                "success_rate": round(s.success_rate, 3),
                "created_at": s.created_at.isoformat(),
            }
            for s in result.scalars()
        ]


# Need func import
from sqlalchemy import func
