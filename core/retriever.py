"""Multi-layer ContextRetriever: lexical + semantic + graph + fact search with reranking."""

import asyncio
import math
import json
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge import (
    Entity, Relationship, Claim, Memory, Hypothesis, Evidence, Embedding,
    Contradiction,
)
from config import settings
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
    """Get embeddings for multiple texts concurrently."""
    tasks = [get_embedding(text) for text in texts]
    return await asyncio.gather(*tasks)


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
        """Find embeddings similar to query vector using numpy batch computation."""
        if not query_vec:
            return []

        r = await self.db.execute(select(Embedding))
        embeddings = list(r.scalars())
        if not embeddings:
            return []

        import numpy as np

        stored_vecs = np.array([json.loads(e.vector) for e in embeddings])
        query_np = np.array(query_vec)

        # Batch cosine similarity
        dots = stored_vecs @ query_np
        mags = np.linalg.norm(stored_vecs, axis=1) * np.linalg.norm(query_np)
        with np.errstate(divide='ignore', invalid='ignore'):
            sims = np.where(mags > 0, dots / mags, 0.0)

        threshold = 0.3
        results = []
        for i, emb in enumerate(embeddings):
            if sims[i] > threshold:
                results.append({
                    "type": emb.target_type, "id": emb.target_id,
                    "score": round(float(sims[i]), 4),
                    "snippet": emb.text[:200],
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # ── Layer 3: Graph (relationship traversal) ──────────────

    @staticmethod
    def _text_relevance(text: str, terms: list[str]) -> float:
        """How many of the expanded terms appear in the text."""
        if not text or not terms:
            return 0.0
        lower = text.lower()
        hits = sum(1 for t in terms if t in lower)
        return min(1.0, hits / max(3, len(terms) * 0.2))

    async def _search_graph(self, query: str, limit: int = 10) -> list[dict]:
        """Traverse entity graph from query-matched seeds, filtering by relevance."""
        expanded = expand_query(query)
        seed_ids = set()

        # Seed: entities whose name or description contains query words
        tokens = [t for t in re.findall(r'\w+', query) if len(t) > 2]
        for token in tokens:
            pat = f"%{token}%"
            r = await self.db.execute(
                select(Entity).where(
                    Entity.name.ilike(pat) | Entity.description.ilike(pat)
                ).limit(5)
            )
            for e in r.scalars():
                if e.id not in seed_ids:
                    seed_ids.add(e.id)

        if not seed_ids:
            return []

        # BFS 1 hop only — 2 hops was too noisy
        visited = set(seed_ids)
        results = []
        hop_penalty = 0.3  # much stronger decay than 0.5

        for seed_id in seed_ids:
            r = await self.db.execute(
                select(Entity).where(Entity.id == seed_id)
            )
            seed_ent = r.scalar_one_or_none()
            if not seed_ent:
                continue

            for direction, col in [
                ("outgoing", Relationship.from_entity_id),
                ("incoming", Relationship.to_entity_id),
            ]:
                r = await self.db.execute(
                    select(Relationship).where(col == seed_ent.id)
                )
                for rel in r.scalars():
                    neighbor_id = (
                        rel.to_entity_id if direction == "outgoing"
                        else rel.from_entity_id
                    )
                    if neighbor_id in visited:
                        continue
                    visited.add(neighbor_id)

                    r2 = await self.db.execute(
                        select(Entity).where(Entity.id == neighbor_id)
                    )
                    neighbor = r2.scalar_one_or_none()
                    if not neighbor:
                        continue

                    # Require SOME text relevance to include neighbor
                    neighbor_text = f"{neighbor.name} {neighbor.description} {rel.relation_type}"
                    relevance = self._text_relevance(neighbor_text, expanded)
                    if relevance < 0.1:
                        continue  # no overlap at all — skip

                    score = hop_penalty * rel.confidence * max(relevance, 0.2)
                    results.append({
                        "type": "entity", "id": neighbor.id,
                        "name": neighbor.name,
                        "snippet": neighbor.description[:200] if neighbor.description else neighbor.name,
                        "score": score,
                        "via": seed_ent.name,
                        "relation": rel.relation_type,
                    })

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

    async def _enrich_belief(self, results: list[dict]) -> None:
        """Add effective_confidence, disputed, decay, and contradiction flags to claim results."""
        claim_ids = [r["id"] for r in results if r.get("type") == "claim"]
        if not claim_ids:
            return

        now = datetime.now(timezone.utc)

        # Batch-fetch all claims
        r = await self.db.execute(
            select(Claim).where(Claim.id.in_(claim_ids))
        )
        claim_map: dict[int, Claim] = {c.id: c for c in r.scalars()}

        # Batch-fetch active contradictions for these claims
        r = await self.db.execute(
            select(Contradiction.claim_a_id, Contradiction.claim_b_id, Contradiction.severity, Contradiction.contradiction_type).where(
                Contradiction.resolved.is_(False),
                (Contradiction.claim_a_id.in_(claim_ids) | Contradiction.claim_b_id.in_(claim_ids))
            )
        )
        contradictions: dict[int, list[dict]] = {}
        for a_id, b_id, severity, ctype in r.all():
            for cid in (a_id, b_id):
                if cid not in contradictions:
                    contradictions[cid] = []
                contradictions[cid].append({"severity": severity, "type": ctype})

        # Enrich each claim result
        for result in results:
            if result.get("type") != "claim":
                continue
            claim = claim_map.get(result["id"])
            if not claim:
                continue

            dt = claim.last_verified
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if not dt:
                dt = now
            days = (now - dt).total_seconds() / 86400
            effective = claim.confidence * math.exp(-claim.decay_rate * days)

            result["effective_confidence"] = round(effective, 4)
            result["disputed"] = claim.disputed
            result["decayed"] = effective < settings.re_verification_threshold
            result["evidence_count"] = claim.evidence_count

            if claim.id in contradictions:
                result["contradictions"] = contradictions[claim.id]

    async def retrieve(
        self,
        query: str,
        limit: int = 20,
        use_semantic: bool = True,
        include_belief: bool = False,
    ) -> dict:
        """Full multi-layer retrieval with reranking.

        Args:
            query: Search query
            limit: Max results
            use_semantic: Whether to use embedding search (slower, needs nomic-embed-text)
            include_belief: If True, claim results include effective_confidence,
                disputed flag, decay status, and active contradictions.

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

        # Enrich with belief data if requested
        if include_belief:
            await self._enrich_belief(ranked)

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
