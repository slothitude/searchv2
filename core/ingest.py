"""Search→Extract→Classify→Ingest→Index pipeline.

Searches SearXNG, extracts content from top URLs, uses 0.8B reflex model to
extract entities/claims, stores them in the knowledge graph, and indexes
embeddings for retrieval.
"""

import json
import re
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from core.search import search_searxng
from core.extractor import fetch_and_extract
from core.ollama import call_ollama
from core.knowledge import KnowledgeGraph, MemoryStore
from core.retriever import ContextRetriever
from config import settings

log = logging.getLogger("searchv2.ingest")


async def _extract_knowledge(text: str, source_url: str) -> list[dict]:
    """Use LLM to extract entities and claims from text. Falls back to regex extraction."""
    truncated = text[:8000]
    prompt = f"""Extract knowledge from this text. Return JSON array of objects.
For each interesting entity/concept, extract facts as claims.

Text:
{truncated}

Source: {source_url}

Return JSON: [{{"entity": "name", "type": "technology|product|person|concept|organization|error|method", "claims": [{{"key": "fact name", "value": "fact value", "confidence": 0.1-1.0}}]}}]

Only extract entities with at least one claim. Max 10 entities. Return JSON only, no explanation."""

    # Try models in order: reflex (0.8B) → reasoning (4B) → attention (2B)
    for model_attr in ("model_reflex", "model_reasoning", "model_attention"):
        model = getattr(settings, model_attr, None)
        if not model:
            continue
        try:
            import asyncio
            response = await asyncio.wait_for(
                call_ollama(prompt, model=model, max_tokens=4096, temperature=0.3),
                timeout=30,
            )
            if not response or not response.strip():
                continue
            start = response.index("[")
            end = response.rindex("]") + 1
            return json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError):
            log.debug("Model %s returned unparseable response", model)
            continue
        except TimeoutError:
            log.debug("Model %s timed out", model)
            continue

    log.warning("All models failed for %s, using regex fallback", source_url)
    return _extract_knowledge_regex(text, source_url)


def _extract_knowledge_regex(text: str, source_url: str) -> list[dict]:
    """Regex-based fallback: extract key sentences as claims about a page entity."""
    # Split into sentences, filter noise
    sentences = re.split(r'[.!?]\s+', text)
    # Skip UI boilerplate, short fragments, code blocks
    noise_markers = ("copy", "share", "subscribe", "cookie", "login", "sign up",
                     "advertisement", "related posts", "tags:", "```", "http://", "https://")
    candidates = [
        s.strip()
        for s in sentences
        if 40 < len(s.strip()) < 400
        and not s.strip().startswith((" ", "copy", "share", "the", "this", "click"))
        and not any(n in s.lower() for n in noise_markers)
    ][:8]

    # Derive entity name from URL domain
    domain = source_url.split("//")[-1].split("/")[0].replace("www.", "")
    title_match = re.search(r'^#?\s*(.+)', text, re.MULTILINE)
    entity_name = title_match.group(1).strip()[:100] if title_match else domain

    # Clean entity name — remove trailing noise
    entity_name = re.sub(r'\s*[-|]\s*.*$', '', entity_name).strip()
    if not entity_name:
        entity_name = domain

    claims = []
    for i, sentence in enumerate(candidates):
        sentence = sentence.rstrip(".,") + "."

        # Extract a meaningful key from the sentence
        # Look for patterns like "X is Y", "X uses Y", "X provides Y"
        m = re.match(
            r'(?i)(?:the\s+)?([A-Z][a-z]+(?:\s+[a-z]+){0,3})\s+'
            r'(?:is|are|uses|provides|supports|requires|offers|allows|enables|helps|can)\s+',
            sentence,
        )
        if m:
            key = m.group(1).lower().strip()
        else:
            # Fallback: first few meaningful words
            words = [w for w in sentence.split() if len(w) > 3 and w[0].islower()]
            key = "_".join(words[:3]).lower() if words else f"fact_{i}"

        claims.append({
            "key": key,
            "value": sentence,
            "confidence": 0.4,
        })

    if claims:
        return [{"entity": entity_name, "type": "document", "claims": claims}]
    return []


async def ingest(
    db: AsyncSession,
    query: str,
    max_urls: int = 3,
    max_results: int = 10,
    classify: bool = True,
    index_embeddings: bool = True,
) -> dict:
    """Full ingest pipeline: search → extract → classify → store → index.

    Args:
        db: Database session
        query: Search query
        max_urls: How many top URLs to extract content from
        max_results: How many search results to consider
        classify: Whether to classify content with 0.8B model
        index_embeddings: Whether to generate embeddings after ingest

    Returns:
        Summary of what was ingested.
    """
    kg = KnowledgeGraph(db)
    ms = MemoryStore(db)
    retriever = ContextRetriever(db)

    # Step 1: Search
    try:
        search_results = await search_searxng(query, max_results=max_results)
    except Exception as e:
        return {"error": f"Search failed: {e}"}

    if not search_results:
        return {"error": "No search results", "query": query}

    # Step 2: Extract content from top URLs
    top_urls = search_results[:max_urls]
    extracted = []
    for result in top_urls:
        url = result.get("url", "")
        if not url:
            continue
        try:
            content = await fetch_and_extract(url)
            if "error" not in content and content.get("text", "").strip():
                extracted.append(content)
                log.info("Extracted %d chars from %s", len(content.get("text", "")), url)
        except Exception as e:
            log.warning("Failed to extract %s: %s", url, e)

    if not extracted:
        return {"error": "Failed to extract content from any URL", "urls_attempted": len(top_urls)}

    # Step 3: Extract entities/claims from content
    total_entities = 0
    total_claims = 0
    ingested_entities = []
    ingested_claims = []

    for page in extracted:
        text = page.get("text", "")
        url = page.get("url", "")

        if classify:
            knowledge = await _extract_knowledge(text, url)
        else:
            # Fallback: create one entity per page with basic claims
            title = page.get("title", url)
            knowledge = [{
                "entity": title,
                "type": "document",
                "claims": [
                    {"key": "source", "value": url, "confidence": 1.0},
                    {"key": "summary", "value": text[:500], "confidence": 0.5},
                ],
            }]

        for item in knowledge:
            entity_name = item.get("entity", "").strip()
            entity_type = item.get("type", "thing")
            claims = item.get("claims", [])

            if not entity_name:
                continue

            # Create or update entity
            description = f"Source: {url}" if url else ""
            entity = await kg.add_entity(entity_name, entity_type=entity_type, description=description)
            total_entities += 1
            ingested_entities.append(entity_name)

            # Add claims
            for c in claims:
                key = c.get("key", "")
                value = c.get("value", "")
                conf = c.get("confidence", 0.5)

                if not key or not value:
                    continue

                claim = await kg.add_claim(
                    entity_name=entity_name,
                    claim_type="extracted",
                    claim_key=key,
                    claim_value=str(value)[:2000],
                    confidence=max(0.1, min(1.0, conf)),
                )
                total_claims += 1
                ingested_claims.append({"entity": entity_name, "key": key, "id": claim.id})

    # Step 4: Store episodic memory of this ingest
    await ms.add_memory(
        content=f"Ingested from query '{query}': {total_entities} entities, {total_claims} claims from {len(extracted)} sources",
        context=f"query: {query}",
        related_entities=list(set(ingested_entities[:10])),
        importance=0.7,
    )

    # Step 5: Index embeddings
    indexed = {"entities": 0, "claims": 0}
    if index_embeddings and total_entities > 0:
        # Index all entities (they were just created/updated)
        from sqlalchemy import select
        from models.knowledge import Entity

        r = await db.execute(select(Entity))
        for entity in r.scalars():
            if not await retriever.has_embedding("entity", entity.id):
                await retriever.index_entity(entity.id)
                indexed["entities"] += 1

            # Index claims for entities we just touched
            if entity.name in set(ingested_entities):
                await retriever.index_claims_for_entity(entity.id)
                indexed["claims"] += 1

    await db.commit()

    return {
        "query": query,
        "search_results": len(search_results),
        "urls_extracted": len(extracted),
        "entities_created": total_entities,
        "claims_created": total_claims,
        "embeddings_indexed": indexed,
        "entities": list(set(ingested_entities)),
    }
