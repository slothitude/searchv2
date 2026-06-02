"""SearchV2 MCP Server — ~25 tools for Claude Code integration."""

import asyncio
import json
from mcp.server.fastmcp import FastMCP

from models.base import async_session, init_db
from core.tome import TomeVault
from core.observation import ObservationEngine, OpportunityEngine
from core.mission_planner import MissionPlanner
from core.scientist import Scientist
from core.knowledge import KnowledgeGraph, MemoryStore
from core.skills import SkillGenerator
from core.search import search_searxng
from core.extractor import fetch_and_extract
from core.router import Router, Tier
from core.retriever import ContextRetriever
from core.ingest import ingest
from core.queue import worker as queue_worker
from core.rss import store as rss_store, fetch_all, ingest_uningested, poller as rss_poller
from models.queue import QueueStore

mcp = FastMCP("SearchV2", instructions="Knowledge acquisition system with Tome vault, observation, missions, knowledge graph, multi-layer retrieval, skills, and RSS feeds.")


# ── Tome (5 tools) ──────────────────────────────────────────

@mcp.tool()
async def tome_create(slug: str, title: str, source: str, variables: str = "{}",
                      tags: str = "[]") -> str:
    """Create a document in the Tome vault. Source is HTML+Jinja2."""
    async with async_session() as db:
        vault = TomeVault(db)
        doc = await vault.create_document(
            slug=slug, title=title, source=source,
            variables=json.loads(variables), tags=json.loads(tags),
        )
        await db.commit()
        return f"Created: {doc.slug}"


@mcp.tool()
async def tome_render(slug: str) -> str:
    """Render a Tome document (cached)."""
    async with async_session() as db:
        vault = TomeVault(db)
        result = await vault.render_document(slug)
        await db.commit()
        if "error" in result:
            return result["error"]
        return f"Rendered: {slug} (cached={result.get('cached', False)})"


@mcp.tool()
async def tome_search(query: str, limit: int = 20) -> str:
    """Search Tome documents by text."""
    async with async_session() as db:
        vault = TomeVault(db)
        docs = await vault.search_documents(query, limit)
        if not docs:
            return "No results"
        return json.dumps(docs, indent=2)


@mcp.tool()
async def tome_link(from_slug: str, to_slug: str, link_type: str = "related",
                   context: str = "") -> str:
    """Link two Tome documents."""
    async with async_session() as db:
        vault = TomeVault(db)
        link = await vault.link_documents(from_slug, to_slug, link_type, context)
        await db.commit()
        if link:
            return f"Linked: {from_slug} → {to_slug} ({link_type})"
        return "Error: document not found"


@mcp.tool()
async def tome_get(slug: str) -> str:
    """Get a Tome document with its graph."""
    async with async_session() as db:
        vault = TomeVault(db)
        doc = await vault.get_document(slug)
        if not doc:
            return f"Not found: {slug}"
        graph = await vault.get_graph(slug)
        return json.dumps({"slug": doc.slug, "title": doc.title,
                          "tags": doc.tags, "graph": graph}, indent=2)


@mcp.tool()
async def tome_extract(slug: str) -> str:
    """Parse a Tome document's HTML and extract all data-* attributes and JSON-LD for agents."""
    async with async_session() as db:
        vault = TomeVault(db)
        data = await vault.extract_agent_data(slug)
        await db.commit()
        return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def tome_export_graph(root_entity: str) -> str:
    """Export entity graph from root as navigable HTML documents in Tome."""
    async with async_session() as db:
        vault = TomeVault(db)
        result = await vault.export_knowledge(root_entity)
        await db.commit()
        return result


@mcp.tool()
async def tome_index() -> str:
    """Get machine-readable catalog of all Tome documents."""
    async with async_session() as db:
        vault = TomeVault(db)
        html = await vault.get_index()
        return html


# ── Observation (2 tools) ─────────────────────────────────────

@mcp.tool()
async def list_observations(source: str = "", min_importance: float = 0.0,
                            limit: int = 50) -> str:
    """List observations. Filter by source and importance."""
    async with async_session() as db:
        engine = ObservationEngine(db)
        obs = await engine.list_observations(
            source=source or None, min_importance=min_importance, limit=limit,
        )
        return json.dumps(obs, indent=2) if obs else "No observations"


@mcp.tool()
async def list_opportunities(status: str = "", min_utility: float = 0.0,
                             limit: int = 50) -> str:
    """List opportunities. Filter by status and utility score."""
    async with async_session() as db:
        engine = OpportunityEngine(db)
        opps = await engine.list_opportunities(
            status=status or None, min_utility=min_utility, limit=limit,
        )
        return json.dumps(opps, indent=2) if opps else "No opportunities"


# ── Mission (3 tools) ────────────────────────────────────────

@mcp.tool()
async def create_mission(description: str, priority: float = 0.5) -> str:
    """Create a new mission."""
    async with async_session() as db:
        planner = MissionPlanner(db)
        mission = await planner.create_mission(description, priority)
        await db.commit()
        return f"Mission #{mission.id}: {description}"


@mcp.tool()
async def mission_status(mission_id: int) -> str:
    """Get full mission tree with goals, sub-goals, experiments."""
    async with async_session() as db:
        planner = MissionPlanner(db)
        tree = await planner.get_mission_tree(mission_id)
        if not tree:
            return f"Mission not found: {mission_id}"
        return json.dumps(tree, indent=2, default=str)


@mcp.tool()
async def execute_mission(mission_id: int) -> str:
    """Execute a mission: list active missions with status."""
    async with async_session() as db:
        planner = MissionPlanner(db)
        missions = await planner.list_missions(limit=20)
        return json.dumps(missions, indent=2, default=str)


# ── Scientist (4 tools) ──────────────────────────────────────

@mcp.tool()
async def list_hypotheses(status: str = "", min_confidence: float = 0.0,
                          limit: int = 50) -> str:
    """List hypotheses with confidence scores."""
    async with async_session() as db:
        scientist = Scientist(db)
        hyps = await scientist.list_hypotheses(
            status=status or None, min_confidence=min_confidence, limit=limit,
        )
        return json.dumps(hyps, indent=2, default=str) if hyps else "No hypotheses"


@mcp.tool()
async def evaluate_hypothesis(hypothesis_id: int) -> str:
    """Get hypothesis detail with evidence and predictions."""
    async with async_session() as db:
        scientist = Scientist(db)
        detail = await scientist.get_hypothesis_detail(hypothesis_id)
        if not detail:
            return f"Hypothesis not found: {hypothesis_id}"
        return json.dumps(detail, indent=2, default=str)


@mcp.tool()
async def list_predictions(hypothesis_id: int) -> str:
    """List predictions for a hypothesis and suggest experiments."""
    async with async_session() as db:
        scientist = Scientist(db)
        detail = await scientist.get_hypothesis_detail(hypothesis_id)
        if not detail:
            return f"Hypothesis not found: {hypothesis_id}"
        preds = detail.get("predictions", [])
        experiments = await scientist.suggest_experiments(hypothesis_id)
        return json.dumps({"predictions": preds, "suggested_experiments": experiments},
                         indent=2, default=str)


@mcp.tool()
async def suggest_experiments(hypothesis_id: int) -> str:
    """Suggest what experiments to run for a hypothesis."""
    async with async_session() as db:
        scientist = Scientist(db)
        suggestions = await scientist.suggest_experiments(hypothesis_id)
        return json.dumps(suggestions, indent=2) if suggestions else "No suggestions"


# ── Knowledge (4 tools) ─────────────────────────────────────

@mcp.tool()
async def get_entity(name: str) -> str:
    """Get entity with relationships, claims, and decay status."""
    async with async_session() as db:
        kg = KnowledgeGraph(db)
        full = await kg.get_entity_full(name)
        if not full:
            return f"Entity not found: {name}"
        return json.dumps(full, indent=2, default=str)


@mcp.tool()
async def find_conflicts() -> str:
    """Find claims with decayed confidence needing re-verification."""
    async with async_session() as db:
        kg = KnowledgeGraph(db)
        decayed = await kg.check_decay()
        return json.dumps(decayed, indent=2, default=str) if decayed else "No decayed claims"


@mcp.tool()
async def latest(query: str = "", hours: int = 72, limit: int = 20) -> str:
    """Get recently ingested/updated entities and their latest claims. Use for 'latest news', 'latest AI stuff', etc. Optionally filter by query text. Default: last 72 hours."""
    async with async_session() as db:
        kg = KnowledgeGraph(db)
        results = await kg.latest(query=query or None, hours=hours, limit=limit)
        if not results:
            return f"No recent knowledge{f' matching \"{query}\"' if query else ''}"
        return json.dumps(results, indent=2, default=str)


@mcp.tool()
async def recall_memories(entity: str, limit: int = 10) -> str:
    """Recall episodic memories related to an entity."""
    async with async_session() as db:
        ms = MemoryStore(db)
        memories = await ms.recall(entity, limit)
        return json.dumps(memories, indent=2, default=str) if memories else "No memories"


@mcp.tool()
async def export_to_tome(entity_name: str, slug: str = "") -> str:
    """Export an entity's knowledge to the Tome vault."""
    async with async_session() as db:
        kg = KnowledgeGraph(db)
        vault = TomeVault(db)
        full = await kg.get_entity_full(entity_name)
        if not full:
            return f"Entity not found: {entity_name}"

        slug = slug or entity_name.lower().replace(" ", "-")
        html = f"<h1>{full['name']}</h1>\n"
        html += f"<p>Type: {full['type']}</p>\n"
        if full.get('description'):
            html += f"<p>{full['description']}</p>\n"
        html += "<h2>Claims</h2>\n<ul>\n"
        for c in full.get('claims', []):
            html += f"<li>{c['key']}: {c['value']} (confidence: {c.get('effective_confidence', c['confidence']):.2f})</li>\n"
        html += "</ul>\n<h2>Relationships</h2>\n<ul>\n"
        for r in full.get('relationships', []):
            if r['direction'] == 'outgoing':
                html += f"<li>{r['type']} → {r['target']}</li>\n"
            else:
                html += f"<li>{r['source']} → {r['type']}</li>\n"
        html += "</ul>"

        doc = await vault.create_document(slug=slug, title=f"Entity: {entity_name}", source=html)
        await db.commit()
        return f"Exported to Tome: {slug}"


# ── Skills (2 tools) ─────────────────────────────────────────

@mcp.tool()
async def list_skills(domain: str = "", limit: int = 50) -> str:
    """List available skills with success rates."""
    async with async_session() as db:
        gen = SkillGenerator(db)
        skills = await gen.list_skills(domain=domain or None, limit=limit)
        return json.dumps(skills, indent=2, default=str) if skills else "No skills"


@mcp.tool()
async def execute_skill(skill_name: str, inputs: str = "{}") -> str:
    """Execute a skill with given inputs."""
    async with async_session() as db:
        gen = SkillGenerator(db)
        result = await gen.execute_skill(skill_name, json.loads(inputs))
        await db.commit()
        return json.dumps(result, indent=2, default=str)


# ── Utility (1 tool) ─────────────────────────────────────────

@mcp.tool()
async def query_utility(goal_id: int) -> str:
    """Query utility score for a goal."""
    async with async_session() as db:
        from core.mission_planner import UtilityTracker
        tracker = UtilityTracker(db)
        utility = await tracker.get_goal_utility(goal_id)
        return f"Goal #{goal_id} utility: {utility:.2f}"


# ── Router (1 tool) ─────────────────────────────────────────

@mcp.tool()
async def route(task: str, context: str = "") -> str:
    """Route a task to the appropriate model tier. Returns classification and plan."""
    router = Router()
    classification = await router.classify(task)
    if classification.get("importance", 0) > 0.7:
        plan = await router.plan(task, context)
        return json.dumps({"classification": classification, "plan": plan}, indent=2)
    return json.dumps({"classification": classification}, indent=2)


# ── Search (3 tools) ─────────────────────────────────────────

@mcp.tool()
async def search(query: str, categories: str = "general", max_results: int = 10) -> str:
    """Search the web via SearXNG."""
    results = await search_searxng(query, categories, max_results)
    if not results:
        return "No results"
    return json.dumps(results, indent=2)

@mcp.tool()
async def browse(url: str) -> str:
    """Browse a URL and extract its text content."""
    result = await fetch_and_extract(url)
    if "error" in result:
        return f"Error: {result['error']}"
    return json.dumps({"title": result.get("title", ""),
                      "text": result.get("text", "")[:5000]}, indent=2)

@mcp.tool()
async def extract(url: str) -> str:
    """Extract text from a URL."""
    result = await fetch_and_extract(url)
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("text", "")[:10000]


# ── Ingest (1 tool) ─────────────────────────────────────────

@mcp.tool()
async def search_ingest(query: str, max_urls: int = 3, classify: bool = True,
                        index_embeddings: bool = True) -> str:
    """Search the web, extract content, create entities/claims, and index embeddings. Full search→ingest→index pipeline."""
    async with async_session() as db:
        result = await ingest(
            db=db, query=query, max_urls=max_urls,
            classify=classify, index_embeddings=index_embeddings,
        )
        return json.dumps(result, indent=2, default=str)


# ── Retriever (3 tools) ─────────────────────────────────────

@mcp.tool()
async def retrieve(query: str, limit: int = 10, use_semantic: bool = True) -> str:
    """Multi-layer conceptual search across entities, claims, documents, and hypotheses. Combines lexical, semantic (embeddings), graph (relationship traversal), and fact search with reranking. use_semantic=false for instant results without LLM calls."""
    async with async_session() as db:
        retriever = ContextRetriever(db)
        result = await retriever.retrieve(query, limit=limit, use_semantic=use_semantic)
        return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def index_embeddings(target_type: str = "all", target_id: int = 0) -> str:
    """Generate embeddings for entities and claims using nomic-embed-text. Run after adding new knowledge. target_type: 'all' (everything), 'entity' (specific entity by target_id + its claims)."""
    async with async_session() as db:
        retriever = ContextRetriever(db)
        if target_type == "all":
            counts = await retriever.index_all()
            await db.commit()
            return json.dumps(counts, indent=2)
        elif target_type == "entity" and target_id:
            await retriever.index_entity(target_id)
            await retriever.index_claims_for_entity(target_id)
            await db.commit()
            return f"Indexed entity {target_id} and its claims"
        return "Provide target_type='all' or target_type='entity' with target_id"


@mcp.tool()
async def embedding_status() -> str:
    """Check how many entities and claims have embedding vectors."""
    async with async_session() as db:
        from sqlalchemy import func, select
        from models.knowledge import Embedding
        retriever = ContextRetriever(db)
        total = await db.execute(select(func.count()).select_from(Embedding))
        by_type = await db.execute(
            select(Embedding.target_type, func.count())
            .group_by(Embedding.target_type)
        )
        return json.dumps({
            "total": total.scalar(),
            "by_type": {row[0]: row[1] for row in by_type.all()},
        }, indent=2)


# ── Queue (4 tools) ─────────────────────────────────────────

_store = QueueStore()


@mcp.tool()
async def queue_search(query: str, categories: str = "general",
                       max_results: int = 10, priority: float = 0.5) -> str:
    """Enqueue a search job. Returns job ID immediately — job runs in background."""
    job = await _store.enqueue("search", query,
                               params={"categories": categories, "max_results": max_results},
                               priority=priority)
    return json.dumps({"job_id": job.id, "status": "queued", "query": query})


@mcp.tool()
async def queue_ingest(query: str, max_urls: int = 3, classify: bool = True,
                       index_embeddings: bool = True, priority: float = 0.5) -> str:
    """Enqueue a full ingest job (search→extract→classify→store→index). Returns job ID immediately."""
    job = await _store.enqueue("ingest", query,
                               params={"max_urls": max_urls, "classify": classify,
                                       "index_embeddings": index_embeddings},
                               priority=priority)
    return json.dumps({"job_id": job.id, "status": "queued", "query": query})


@mcp.tool()
async def queue_status(job_id: int = 0) -> str:
    """List all jobs or get specific job status/result."""
    if job_id:
        job = await _store.get_job(job_id)
        if not job:
            return f"Job not found: {job_id}"
        return json.dumps({
            "id": job.id,
            "job_type": job.job_type,
            "query": job.query,
            "status": job.status,
            "result": json.loads(job.result) if job.result else None,
            "error": job.error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }, indent=2, default=str)
    jobs = await _store.list_jobs()
    return json.dumps(jobs, indent=2, default=str)


@mcp.tool()
async def queue_cancel(job_id: int) -> str:
    """Cancel a pending job."""
    ok = await _store.cancel_job(job_id)
    if ok:
        return f"Cancelled job #{job_id}"
    return f"Cannot cancel job #{job_id} (not pending or not found)"


# ── RSS (3 tools) ─────────────────────────────────────────

@mcp.tool()
async def rss_feeds() -> str:
    """List configured RSS feeds with last-fetch time and article count."""
    feeds = await rss_store.list_feeds()
    return json.dumps(feeds, indent=2)


@mcp.tool()
async def rss_refresh(feed_name: str = "") -> str:
    """Trigger manual fetch of one feed or all feeds. Returns article counts."""
    if feed_name:
        feed = await rss_store.get_feed_by_name(feed_name)
        if not feed:
            return f"Feed not found: {feed_name}"
        # Fetch just this feed by temporarily disabling others
        from core.rss import fetch_feed
        articles = await fetch_feed(feed.url)
        new_count = 0
        for a in articles:
            if a.get("title") and a.get("url"):
                is_new = await rss_store.save_article(
                    feed.id, a["title"], a["url"],
                    summary=a.get("summary", ""),
                    published_at=a.get("published_at"),
                )
                if is_new:
                    new_count += 1
        await rss_store.update_feed_fetched(feed.id)
        result = {"feed": feed_name, "total": len(articles), "new": new_count}
    else:
        result = await fetch_all()
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def rss_articles(feed_name: str = "", limit: int = 20) -> str:
    """List recent RSS articles, optionally filtered by feed."""
    articles = await rss_store.list_articles(feed_name=feed_name, limit=limit)
    return json.dumps(articles, indent=2, default=str) if articles else "No articles"


if __name__ == "__main__":
    import sys
    asyncio.run(init_db())
    asyncio.run(queue_worker.start())
    asyncio.run(rss_poller.start())

    # stdio when spawned by Claude Code, streamable-http when run standalone
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = 7711
        mcp.run(transport="streamable-http")
