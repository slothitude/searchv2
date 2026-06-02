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

mcp = FastMCP("SearchV2", instructions="Knowledge acquisition system with Tome vault, observation, missions, knowledge graph, and skills.")


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


if __name__ == "__main__":
    import uvicorn
    asyncio.run(init_db())
    mcp.run(transport="sse", host="0.0.0.0", port=7711)
