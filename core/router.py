from datetime import datetime, timezone
from enum import Enum
from config import settings
from models.knowledge import Escalation
from core.ollama import call_ollama, call_ollama_chat
from core.cloud import call_nim


class Tier(str, Enum):
    REFLEX = "reflex"       # 0.8B - classify, observe
    ATTENTION = "attention" # 2B - plan, executive
    REASONING = "reasoning" # 4B - evaluate, judge
    ACTION = "action"       # 9B - write, synthesize
    NUCLEAR = "nuclear"     # Cloud - arbitration


TIER_MODELS = {
    Tier.REFLEX: "model_reflex",
    Tier.ATTENTION: "model_attention",
    Tier.REASONING: "model_reasoning",
    Tier.ACTION: "model_action",
}


class Router:
    """Routes tasks to appropriate model tier based on cognitive requirements."""

    def __init__(self, db=None):
        self.db = db
        self._escalation_log: list[dict] = []

    async def classify(self, text: str) -> dict:
        """Reflex tier (0.8B): classify input into category + importance."""
        prompt = f"""Classify this observation. Return JSON only.
Text: {text}
Categories: technology_update, error, opportunity, routine, user_request
Return: {{"category": "...", "importance": 0.0-1.0, "summary": "..."}}"""
        response = await call_ollama(prompt, model=getattr(settings, TIER_MODELS[Tier.REFLEX]))
        try:
            import json
            # Extract JSON from response
            start = response.index("{")
            end = response.rindex("}") + 1
            return json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError):
            return {"category": "general", "importance": 0.5, "summary": text[:100]}

    async def plan(self, goal: str, context: str = "") -> dict:
        """Attention tier (2B): decompose goal into sub-goals."""
        prompt = f"""Break this goal into actionable sub-goals. Return JSON array.
Goal: {goal}
Context: {context}
Return: [{{"description": "...", "type": "research|verify|build|test", "cost_estimate": 1-10, "info_value": 1-10}}]"""
        response = await call_ollama(
            prompt,
            model=getattr(settings, TIER_MODELS[Tier.ATTENTION]),
            max_tokens=4096,
        )
        try:
            import json
            start = response.index("[")
            end = response.rindex("]") + 1
            return {"sub_goals": json.loads(response[start:end])}
        except (ValueError, json.JSONDecodeError):
            return {"sub_goals": [{"description": goal, "type": "research", "cost_estimate": 5, "info_value": 5}]}

    async def judge(self, claim: str, evidence: str) -> dict:
        """Reasoning tier (4B): evaluate evidence against claim."""
        prompt = f"""Evaluate this evidence against the claim. Be precise.
Claim: {claim}
Evidence: {evidence}
Return JSON: {{"verdict": "supports|contradicts|neutral", "confidence": 0.0-1.0, "reasoning": "..."}}"""
        response = await call_ollama(
            prompt,
            model=getattr(settings, TIER_MODELS[Tier.REASONING]),
            max_tokens=2048,
        )
        try:
            import json
            start = response.index("{")
            end = response.rindex("}") + 1
            return json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError):
            return {"verdict": "neutral", "confidence": 0.3, "reasoning": "Failed to parse"}

    async def synthesize(self, claims: list[dict], topic: str) -> str:
        """Action tier (9B): write synthesis. NEVER plans."""
        claims_text = "\n".join(f"- [{c.get('key', '')}] {c.get('value', '')} (confidence: {c.get('confidence', 0)})" for c in claims)
        prompt = f"""Synthesize these claims into a coherent document about: {topic}
Claims:
{claims_text}
Write a comprehensive but concise document. Do not suggest further research."""
        return await call_ollama(
            prompt,
            model=getattr(settings, TIER_MODELS[Tier.ACTION]),
            max_tokens=4096,
            temperature=0.8,
        )

    async def escalate(
        self,
        from_tier: Tier,
        to_tier: Tier,
        reason: str,
        goal_id: int | None = None,
    ) -> bool:
        """Log and approve escalation."""
        escalation = {
            "from": from_tier.value,
            "to": to_tier.value,
            "reason": reason,
            "goal_id": goal_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._escalation_log.append(escalation)

        if self.db:
            from models.base import async_session
            async with async_session() as db:
                entry = Escalation(
                    from_tier=from_tier.value,
                    to_tier=to_tier.value,
                    reason=reason,
                    approved=True,
                    goal_id=goal_id,
                )
                db.add(entry)
                await db.commit()

        return True  # auto-approve for now

    async def arbitrate(
        self, position_a: str, position_b: str, context: str
    ) -> str:
        """Nuclear tier (cloud): resolve disagreement."""
        await self.escalate(Tier.REASONING, Tier.NUCLEAR,
                           f"Disagreement: {context}")
        return await call_nim(position_a, position_b, context)

    def get_escalation_log(self) -> list[dict]:
        return self._escalation_log
