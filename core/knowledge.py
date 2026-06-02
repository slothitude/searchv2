import math
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from models.knowledge import (
    Entity, Relationship, Claim, Memory, Escalation
)
from config import settings


class KnowledgeGraph:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add_entity(
        self, name: str, entity_type: str = "thing", description: str = ""
    ) -> Entity:
        existing = await self.get_entity(name)
        now = datetime.now(timezone.utc)
        if existing:
            if description and not existing.description:
                existing.description = description
            existing.updated_at = now
            await self.db.flush()
            return existing

        entity = Entity(name=name, entity_type=entity_type, description=description)
        self.db.add(entity)
        await self.db.flush()
        return entity

    async def get_entity(self, name: str) -> Entity | None:
        result = await self.db.execute(
            select(Entity).where(Entity.name == name)
        )
        return result.scalar_one_or_none()

    async def get_entity_by_id(self, entity_id: int) -> Entity | None:
        result = await self.db.execute(
            select(Entity).where(Entity.id == entity_id)
        )
        return result.scalar_one_or_none()

    async def add_relationship(
        self,
        from_name: str,
        to_name: str,
        relation_type: str = "related",
        confidence: float = 0.5,
        context: str = "",
    ) -> Relationship | None:
        from_ent = await self.add_entity(from_name)
        to_ent = await self.add_entity(to_name)

        # Check for existing
        result = await self.db.execute(
            select(Relationship).where(
                Relationship.from_entity_id == from_ent.id,
                Relationship.to_entity_id == to_ent.id,
                Relationship.relation_type == relation_type,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Boost confidence with new evidence
            existing.confidence = min(1.0, existing.confidence * 0.7 + confidence * 0.3)
            existing.evidence_count += 1
            await self.db.flush()
            return existing

        rel = Relationship(
            from_entity_id=from_ent.id,
            to_entity_id=to_ent.id,
            relation_type=relation_type,
            confidence=confidence,
            context=context,
        )
        self.db.add(rel)
        await self.db.flush()
        return rel

    async def add_claim(
        self,
        entity_name: str,
        claim_type: str,
        claim_key: str,
        claim_value: str,
        confidence: float = 0.5,
        decay_rate: float = 0.001,
    ) -> Claim | None:
        # Reject claims below minimum confidence threshold
        if confidence < settings.min_claim_confidence:
            return None

        entity = await self.add_entity(entity_name)

        existing = await self.db.execute(
            select(Claim).where(
                Claim.entity_id == entity.id,
                Claim.claim_key == claim_key,
            )
        )
        claim = existing.scalar_one_or_none()

        if claim:
            # Update with new evidence
            claim.confidence = min(1.0, claim.confidence * 0.6 + confidence * 0.4)
            claim.claim_value = claim_value
            claim.evidence_count += 1
            claim.last_verified = datetime.now(timezone.utc)
            claim.updated_at = datetime.now(timezone.utc)
        else:
            claim = Claim(
                entity_id=entity.id,
                claim_type=claim_type,
                claim_key=claim_key,
                claim_value=claim_value,
                confidence=confidence,
                decay_rate=decay_rate,
            )
            self.db.add(claim)

        await self.db.flush()
        return claim

    async def get_entity_full(self, name: str) -> dict | None:
        entity = await self.get_entity(name)
        if not entity:
            return None

        # Relationships
        from_rels = await self.db.execute(
            select(Relationship).where(Relationship.from_entity_id == entity.id)
        )
        to_rels = await self.db.execute(
            select(Relationship).where(Relationship.to_entity_id == entity.id)
        )

        relationships = []
        for r in from_rels.scalars():
            target = await self.get_entity_by_id(r.to_entity_id)
            if target:
                relationships.append({
                    "direction": "outgoing", "target": target.name,
                    "type": r.relation_type, "confidence": r.confidence,
                })
        for r in to_rels.scalars():
            source = await self.get_entity_by_id(r.from_entity_id)
            if source:
                relationships.append({
                    "direction": "incoming", "source": source.name,
                    "type": r.relation_type, "confidence": r.confidence,
                })

        # Claims with effective confidence (decay)
        claims = await self.db.execute(
            select(Claim).where(Claim.entity_id == entity.id)
        )
        claims_out = []
        now = datetime.now(timezone.utc)
        for c in claims.scalars():
            dt = c.last_verified
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if not dt:
                dt = now
            days = (now - dt).total_seconds() / 86400
            effective = c.confidence * math.exp(-c.decay_rate * days)
            claims_out.append({
                "id": c.id, "type": c.claim_type, "key": c.claim_key,
                "value": c.claim_value, "confidence": c.confidence,
                "effective_confidence": effective,
                "evidence_count": c.evidence_count,
                "last_verified": c.last_verified.isoformat(),
                "decayed": effective < settings.re_verification_threshold,
            })

        return {
            "name": entity.name, "type": entity.entity_type,
            "description": entity.description,
            "relationships": relationships,
            "claims": claims_out,
        }

    async def check_decay(self) -> list[dict]:
        """Find claims needing re-verification."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(select(Claim))
        decayed = []
        for claim in result.scalars():
            dt = claim.last_verified
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if not dt:
                dt = now
            days = (now - dt).total_seconds() / 86400
            effective = claim.confidence * math.exp(-claim.decay_rate * days)
            if effective < settings.re_verification_threshold:
                entity = await self.get_entity_by_id(claim.entity_id)
                decayed.append({
                    "claim_id": claim.id,
                    "entity": entity.name if entity else "unknown",
                    "key": claim.claim_key,
                    "value": claim.claim_value,
                    "base_confidence": claim.confidence,
                    "effective_confidence": effective,
                    "days_since_verified": days,
                })
        return decayed

    async def search_entities(self, query: str, limit: int = 20) -> list[dict]:
        pattern = f"%{query}%"
        result = await self.db.execute(
            select(Entity)
            .where(Entity.name.ilike(pattern) | Entity.description.ilike(pattern))
            .limit(limit)
        )
        return [
            {"id": e.id, "name": e.name, "type": e.entity_type,
             "description": e.description}
            for e in result.scalars()
        ]

    async def latest(self, query: str = "", hours: int = 72, limit: int = 20) -> list[dict]:
        """Get recently updated entities and their latest claims.

        Args:
            query: optional text filter (ilike on entity name / claim key+value)
            hours: how far back to look (default 72h)
            limit: max entities to return
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Build base query for entities updated recently
        ent_q = (
            select(Entity)
            .where(Entity.updated_at >= cutoff)
            .order_by(desc(Entity.updated_at))
            .limit(limit * 3)  # over-fetch, filter after claim join
        )
        entities = (await self.db.execute(ent_q)).scalars().all()

        if query:
            pattern = f"%{query}%"
            entities = [
                e for e in entities
                if query.lower() in e.name.lower()
                or (e.description and query.lower() in e.description.lower())
            ]

        entities = entities[:limit]

        # Batch-fetch claims for these entities
        out = []
        for e in entities:
            claims_q = (
                select(Claim)
                .where(Claim.entity_id == e.id)
                .order_by(desc(Claim.updated_at))
                .limit(5)
            )
            claims = (await self.db.execute(claims_q)).scalars().all()

            claim_dicts = []
            for c in claims:
                if query and query.lower() not in c.claim_key.lower() and query.lower() not in c.claim_value.lower():
                    continue
                claim_dicts.append({
                    "id": c.id,
                    "key": c.claim_key,
                    "value": c.claim_value[:200],
                    "confidence": c.confidence,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                })

            if claim_dicts or not query:
                out.append({
                    "entity": e.name,
                    "type": e.entity_type,
                    "description": e.description[:200] if e.description else "",
                    "updated_at": e.updated_at.isoformat() if e.updated_at else None,
                    "claims": claim_dicts,
                })

        return out


class MemoryStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add_memory(
        self,
        content: str,
        context: str = "",
        related_entities: list | None = None,
        importance: float = 0.5,
    ) -> Memory:
        mem = Memory(
            content=content,
            context=context,
            related_entities=related_entities or [],
            importance=importance,
        )
        self.db.add(mem)
        await self.db.flush()
        return mem

    async def recall(
        self, entity_name: str, limit: int = 10
    ) -> list[dict]:
        result = await self.db.execute(
            select(Memory)
            .where(Memory.related_entities.contains(entity_name))
            .order_by(desc(Memory.importance), desc(Memory.created_at))
            .limit(limit)
        )
        return [
            {
                "id": m.id, "content": m.content, "context": m.context,
                "importance": m.importance, "entities": m.related_entities,
                "created_at": m.created_at.isoformat(),
            }
            for m in result.scalars()
        ]

    async def search_memories(self, query: str, limit: int = 20) -> list[dict]:
        pattern = f"%{query}%"
        result = await self.db.execute(
            select(Memory)
            .where(Memory.content.ilike(pattern) | Memory.context.ilike(pattern))
            .order_by(desc(Memory.created_at))
            .limit(limit)
        )
        return [
            {
                "id": m.id, "content": m.content, "context": m.context,
                "importance": m.importance, "entities": m.related_entities,
                "created_at": m.created_at.isoformat(),
            }
            for m in result.scalars()
        ]
