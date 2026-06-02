"""Multi-layer ContextRetriever: lexical + semantic + graph + fact search with reranking."""

import math
import json
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge import (
    Entity, Relationship, Claim, Memory, Hypothesis, Evidence, Embedding
)
from core.ollama import call_ollama


# ── Synonym maps for concept expansion ──────────────────────

SYNONYM_GROUPS = [
    {"supplier", "vendor", "manufacturer", "wholesaler", "provider", "seller"},
    {"cheap", "low cost", "low-cost", "budget", "discount", "bulk", "inexpensive", "affordable"},
    {"search", "lookup", "find", "query", "fetch", "retrieve", "discover"},
    {"error", "failure", "broken", "fault", "exception", "bug", "issue", "problem"},
    {"api", "endpoint", "interface", "service", "rest", "graphql"},
    {"authentication", "auth", "login", "credential", "permission", "access"},
    {"dropshipping", "ds", "drop ship", "drop-ship"},
    {"product", "item", "sku", "goods", "merchandise"},
    {"market", "marketplace", "store", "shop", "platform", "catalog"},
    {"fast", "quick", "rapid", "speedy", "performant", "efficient"},
]

WORD_TO_SYNONYMS: dict[str, set[str]] = {}
for group in SYNONYM_GROUPS:
    for word in group:
        WORD_TO_SYNONYMS[word.lower()] = group - {word}


def expand_query(query: str) -> list[str]:
    """Expand query terms with synonyms. Returns list of expanded terms."""
    tokens = re.findall(r'\w+', query.lower())
    expanded = list(tokens)
    for token in tokens:
        syns = WORD_TO_SYNONYMS.get(token, set())
        expanded.extend(syns)
    return list(set(expanded))


def expand_query_text(query: str) -> str:
    """Return the original query plus synonym-expanded terms as a search string."""
    expanded = expand_query(query)
    return " ".join(expanded)


# ── Cosine similarity ────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Embedding via Ollama ────────────────────────────────────

async def get_embedding(text: str) -> list[float]:
    """Get embedding vector from Ollama nomic-embed-text."""
    import httpx
    from config import settings
    payload = {
        "model": "nomic-embed-text",
        "prompt": text,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/embeddings",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json().get("embedding", [])


async def get_embedding_batch(texts: list[str]) -> list[list[float]]:
    """Get embeddings for multiple texts sequentially."""
    vectors = []
    for text in texts:
        vec = await get_embedding(text)
        vectors.append(vec)
    return vectors


# ── Scoring weights ─────────────────────────────────────────

WEIGHTS = {
    "lexical": 0.25,
    "semantic": 0.35,
    "graph": 0.25,
    "fact": 0.15,
}


class ContextRetriever:
    """Multi-layer retrieval combining lexical, semantic, graph, and fact search."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Layer 1: Lexical (FTS5-like via ILIKE) ──────────────

    async def _search_lexical(self, query: str, limit: int = 20) -> list[dict]:
        """Search entities, documents, claims, hypotheses, memories by text."""
        pattern = f"%{query}%"
        results = []

        # Entities
        r = await self.db.execute(
            select(Entity).where(
                Entity.name.ilike(pattern) | Entity.description.ilike(pattern)
            ).limit(limit)
        )
        for e in r.scalars():
            results.append({
                "type": "entity", "id": e.id, "name": e.name,
                "snippet": e.description[:200] if e.description else e.name,
            })

        # Claims
        r = await self.db.execute(
            select(Claim).where(
                Claim.claim_key.ilike(pattern) | Claim.claim_value.ilike(pattern)
            ).limit(limit)
        )
        for c in r.scalars():
            results.append({
                "type": "claim", "id": c.id,
                "snippet": f"{c.claim_key}: {c.claim_value[:200]}",
                "confidence": c.confidence,
            })

        # Memories
        r = await self.db.execute(
            select(Memory).where(
                Memory.content.ilike(pattern) | Memory.context.ilike(pattern)
            ).limit(limit)
        )
        for m in r.scalars():
            results.append({
                "type": "memory", "id": m.id,
                "snippet": m.content[:200],
                "importance": m.importance,
            })

        # Hypotheses
        r = await self.db.execute(
            select(Hypothesis).where(
                Hypothesis.claim.ilike(pattern)
            ).limit(limit)
        )
        for h in r.scalars():
            results.append({
                "type": "hypothesis", "id": h.id,
                "snippet": h.claim[:200],
                "confidence": h.confidence,
            })

        return results

    # ── Layer 2: Semantic (embedding similarity) ──────────────

    async def _search_semantic(
        self, query: str, query_vec: list[float], limit: int = 20
    ) -> list[dict]:
        """Find embeddings similar to query vector."""
        if not query_vec:
            return []

        results = []
        r = await self.db.execute(select(Embedding))
        for emb in r.scalars():
            stored_vec = json.loads(emb.vector)
            sim = cosine_similarity(query_vec, stored_vec)
            if sim > 0.3:  # threshold
                results.append({
                    "type": emb.target_type, "id": emb.target_id,
                    "score": sim,
                    "snippet": emb.text[:200],
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # ── Layer 3: Graph (relationship traversal) ──────────────

    async def _search_graph(self, query: str, limit: int = 20) -> list[dict]:
        """Traverse entity graph starting from query-matched entities."""
        # Find entities that match query terms
        pattern = f"%{query}%"
        r = await self.db.execute(
            select(Entity).where(
                Entity.name.ilike(pattern) | Entity.description.ilike(pattern)
            ).limit(10)
        )
        seed_entities = list(r.scalars())

        if not seed_entities:
            # Try matching individual query words
            tokens = re.findall(r'\w+', query)
            for token in tokens:
                pat = f"%{token}%"
                r = await self.db.execute(
                    select(Entity).where(Entity.name.ilike(pat)).limit(5)
                )
                seed_entities.extend(r.scalars())
            seed_entities = list({e.id: e for e in seed_entities}.values())

        if not seed_entities:
            return []

        # BFS 2 hops
        visited = set(e.id for e in seed_entities)
        results = []
        queue = list(seed_entities)

        for _ in range(2):  # 2 hops
            next_queue = []
            for entity in queue:
                # Outgoing
                r = await self.db.execute(
                    select(Relationship).where(
                        Relationship.from_entity_id == entity.id
                    )
                )
                for rel in r.scalars():
                    if rel.to_entity_id not in visited:
                        visited.add(rel.to_entity_id)
                        target = await self.db.execute(
                            select(Entity).where(Entity.id == rel.to_entity_id)
                        )
                        target_ent = target.scalar_one_or_none()
                        if target_ent:
                            hop_score = 1.0 if entity.id in {e.id for e in seed_entities} else 0.5
                            results.append({
                                "type": "entity", "id": target_ent.id,
                                "name": target_ent.name,
                                "snippet": target_ent.description[:200] if target_ent.description else target_ent.name,
                                "score": hop_score * rel.confidence,
                                "via": entity.name,
                                "relation": rel.relation_type,
                            })
                            next_queue.append(target_ent)
                # Incoming
                r = await self.db.execute(
                    select(Relationship).where(
                        Relationship.to_entity_id == entity.id
                    )
                )
                for rel in r.scalars():
                    if rel.from_entity_id not in visited:
                        visited.add(rel.from_entity_id)
                        source = await self.db.execute(
                            select(Entity).where(Entity.id == rel.from_entity_id)
                        )
                        source_ent = source.scalar_one_or_none()
                        if source_ent:
                            hop_score = 1.0 if entity.id in {e.id for e in seed_entities} else 0.5
                            results.append({
                                "type": "entity", "id": source_ent.id,
                                "name": source_ent.name,
                                "snippet": source_ent.description[:200] if source_ent.description else source_ent.name,
                                "score": hop_score * rel.confidence,
                                "via": entity.name,
                                "relation": rel.relation_type,
                            })
                            next_queue.append(source_ent)
            queue = next_queue
            if not queue:
                break

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:limit]

    # ── Layer 4: Fact (claim graph overlap) ─────────────────

    async def _search_facts(self, query: str, limit: int = 20) -> list[dict]:
        """Search claims by key/value overlap and entity proximity."""
        expanded = expand_query(query)
        results = []
        seen = set()

        # Direct claim match
        for term in expanded:
            pattern = f"%{term}%"
            r = await self.db.execute(
                select(Claim).where(
                    Claim.claim_key.ilike(pattern) | Claim.claim_value.ilike(pattern)
                ).limit(5)
            )
            for c in r.scalars():
                key = ("claim", c.id)
                if key not in seen:
                    seen.add(key)
                    # Overlap score: how many expanded terms match
                    text = f"{c.claim_key} {c.claim_value}".lower()
                    overlap = sum(1 for t in expanded if t in text)
                    overlap_score = min(1.0, overlap / max(1, len(expanded)))
                    results.append({
                        "type": "claim", "id": c.id,
                        "snippet": f"{c.claim_key}: {c.claim_value[:200]}",
                        "score": overlap_score * c.confidence,
                        "entity_id": c.entity_id,
                    })

        # Entity-adjacent claims: find entities matching query, return their claims
        for term in expanded[:10]:  # limit to prevent too many queries
            pattern = f"%{term}%"
            r = await self.db.execute(
                select(Entity).where(Entity.name.ilike(pattern)).limit(3)
            )
            for entity in r.scalars():
                r2 = await self.db.execute(
                    select(Claim).where(Claim.entity_id == entity.id).limit(10)
                )
                for c in r2.scalars():
                    key = ("claim", c.id)
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "type": "claim", "id": c.id,
                            "snippet": f"{c.claim_key}: {c.claim_value[:200]}",
                            "score": c.confidence * 0.8,  # adjacent = slightly lower
                            "entity_id": c.entity_id,
                            "entity_name": entity.name,
                        })

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:limit]

    # ── Reranker ────────────────────────────────────────────

    def _rerank(
        self,
        lexical: list[dict],
        semantic: list[dict],
        graph: list[dict],
        facts: list[dict],
    ) -> list[dict]:
        """Merge and rerank results from all layers."""
        merged: dict[str, dict] = {}  # key = "type:id"

        # Lexical
        for r in lexical:
            key = f"{r['type']}:{r['id']}"
            if key not in merged:
                merged[key] = {**r, "scores": {}}
            merged[key]["scores"]["lexical"] = 1.0

        # Semantic
        for r in semantic:
            key = f"{r['type']}:{r['id']}"
            if key not in merged:
                merged[key] = {**r, "scores": {}}
            merged[key]["scores"]["semantic"] = r.get("score", 0.5)

        # Graph
        for r in graph:
            key = f"{r['type']}:{r['id']}"
            if key not in merged:
                merged[key] = {**r, "scores": {}}
            merged[key]["scores"]["graph"] = r.get("score", 0.5)
            if "via" in r:
                merged[key]["via"] = r["via"]
                merged[key]["relation"] = r["relation"]

        # Facts
        for r in facts:
            key = f"{r['type']}:{r['id']}"
            if key not in merged:
                merged[key] = {**r, "scores": {}}
            merged[key]["scores"]["fact"] = r.get("score", 0.5)

        # Calculate combined scores
        scored = []
        for key, item in merged.items():
            s = item["scores"]
            combined = (
                WEIGHTS["lexical"] * s.get("lexical", 0.0)
                + WEIGHTS["semantic"] * s.get("semantic", 0.0)
                + WEIGHTS["graph"] * s.get("graph", 0.0)
                + WEIGHTS["fact"] * s.get("fact", 0.0)
            )

            # Recency bonus for claims with recent verification
            recency_bonus = 0.0
            if item.get("type") == "claim" and "confidence" in item:
                recency_bonus = 0.0  # handled via effective_confidence in the data

            final_score = combined + recency_bonus

            result = {
                "type": item["type"],
                "id": item["id"],
                "score": round(final_score, 4),
                "snippet": item.get("snippet", ""),
                "layers": list(s.keys()),
            }
            if "via" in item:
                result["via"] = item["via"]
                result["relation"] = item["relation"]
            if "entity_name" in item:
                result["entity_name"] = item["entity_name"]
            if "confidence" in item:
                result["confidence"] = item["confidence"]

            scored.append(result)

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    # ── Embedding store ──────────────────────────────────────

    async def store_embedding(
        self, target_type: str, target_id: int, text: str, vector: list[float]
    ) -> Embedding:
        """Store an embedding vector."""
        emb = Embedding(
            target_type=target_type,
            target_id=target_id,
            text=text,
            vector=json.dumps(vector),
        )
        self.db.add(emb)
        await self.db.flush()
        return emb

    async def get_embedding(
        self, target_type: str, target_id: int
    ) -> Embedding | None:
        r = await self.db.execute(
            select(Embedding).where(
                Embedding.target_type == target_type,
                Embedding.target_id == target_id,
            )
        )
        return r.scalar_one_or_none()

    async def has_embedding(self, target_type: str, target_id: int) -> bool:
        r = await self.db.execute(
            select(func.count()).select_from(Embedding).where(
                Embedding.target_type == target_type,
                Embedding.target_id == target_id,
            )
        )
        return r.scalar() > 0

    async def index_entity(self, entity_id: int) -> None:
        """Generate and store embedding for an entity."""
        r = await self.db.execute(
            select(Entity).where(Entity.id == entity_id)
        )
        entity = r.scalar_one_or_none()
        if not entity:
            return

        text = f"{entity.name} {entity.description}"
        vec = await get_embedding(text)
        await self.store_embedding("entity", entity_id, text, vec)

    async def index_claims_for_entity(self, entity_id: int) -> None:
        """Generate and store embeddings for all claims of an entity."""
        r = await self.db.execute(
            select(Claim).where(Claim.entity_id == entity_id)
        )
        for claim in r.scalars():
            if not await self.has_embedding("claim", claim.id):
                text = f"{claim.claim_key}: {claim.claim_value}"
                vec = await get_embedding(text)
                await self.store_embedding("claim", claim.id, text, vec)

    async def index_all(self) -> dict:
        """Index all entities and claims with embeddings."""
        counts = {"entities": 0, "claims": 0}

        r = await self.db.execute(select(Entity))
        for entity in r.scalars():
            if not await self.has_embedding("entity", entity.id):
                await self.index_entity(entity.id)
                counts["entities"] += 1

        r = await self.db.execute(select(Claim))
        for claim in r.scalars():
            if not await self.has_embedding("claim", claim.id):
                text = f"{claim.claim_key}: {claim.claim_value}"
                vec = await get_embedding(text)
                await self.store_embedding("claim", claim.id, text, vec)
                counts["claims"] += 1

        await self.db.flush()
        return counts

    # ── Main retrieve ────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        limit: int = 20,
        use_semantic: bool = True,
    ) -> dict:
        """Full multi-layer retrieval with reranking.

        Args:
            query: Search query
            limit: Max results
            use_semantic: Whether to use embedding search (slower, needs nomic-embed-text)

        Returns:
            Dict with results, expansion info, and per-layer counts.
        """
        expanded_terms = expand_query(query)
        expanded_query = " ".join(expanded_terms)

        # Layer 1: Lexical (always on, fast)
        lexical = await self._search_lexical(query, limit)
        # Also search with expanded terms
        if expanded_query != query:
            lexical_expanded = await self._search_lexical(expanded_query, limit)
            # Merge, dedup
            seen = {(r["type"], r["id"]) for r in lexical}
            for r in lexical_expanded:
                if (r["type"], r["id"]) not in seen:
                    lexical.append(r)

        # Layer 2: Semantic (optional, needs embedding model)
        semantic = []
        query_vec = None
        if use_semantic:
            try:
                query_vec = await get_embedding(query)
                semantic = await self._search_semantic(query, query_vec, limit)
            except Exception:
                pass  # embedding model unavailable

        # Layer 3: Graph
        graph = await self._search_graph(query, limit)

        # Layer 4: Facts
        facts = await self._search_facts(query, limit)

        # Rerank
        ranked = self._rerank(lexical, semantic, graph, facts)
        ranked = ranked[:limit]

        return {
            "query": query,
            "expanded_terms": expanded_terms,
            "results": ranked,
            "layers": {
                "lexical": len(lexical),
                "semantic": len(semantic),
                "graph": len(graph),
                "fact": len(facts),
            },
        }
