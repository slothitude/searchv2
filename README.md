# SearchV2 — Knowledge Acquisition System

A continuously improving knowledge organism. Observes, identifies opportunities, plans missions, generates hypotheses, accumulates knowledge, learns procedures, and decays stale information.

## Pipeline

```
Observation → Opportunity → Mission → Goal → Hypothesis → Prediction → Evidence → Claim → Knowledge → Capability → Skill
```

## Stack

FastAPI + SQLite (FTS5) + Jinja2 + Ollama + SearXNG + FastMCP

## Model Ladder

| Model | Tier | Role |
|-------|------|------|
| Qwen3.5 0.8B | Reflex | Always-on daemon, observation, classify |
| Qwen3.5 2B | Attention | Executive function, planning, escalation |
| Qwen3.5 4B | Reasoning | Evaluate evidence, hypothesis, tool selection |
| Qwen3.5 9B | Action | Synthesize, write (NEVER plans) |
| NVIDIA NIM | Nuclear | Cloud arbitration for disagreements |

**Rules:** 9B never plans. No model calls above it directly. 0.8B is a daemon. Cloud = disagreement resolver only.

## Quick Start

```bash
docker compose up --build
# Open http://localhost:7710
```

Or locally:
```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 7710
```

## Two Storage Systems

1. **Tome** — HTML+Jinja2 document vault with typed templates (entity, hypothesis, mission, skill, memory, observation). Every document is both human-readable and machine-parsable via `data-*` attributes and JSON-LD.
2. **Memory** — Separate from knowledge. Episodic recall ("last time we researched X, Y happened").

## API

### Tome
- `POST/GET/PATCH/DELETE /api/docs` — CRUD documents
- `GET /api/docs/{slug}/render` — Render with Jinja2 (cached)
- `GET /api/docs/search?q=` — FTS5 search
- `GET /api/docs/{slug}/graph` — Document link graph
- `POST /api/docs/link` — Link two documents
- `POST /api/docs/{slug}/variables` — Set Jinja2 variables
- `POST /api/docs/{slug}/assets` — Upload binary assets

### Research
- `POST /api/search` — SearXNG web search
- `POST /api/extract` — URL → text extraction
- `POST /api/classify` — 0.8B reflex classification
- `POST /api/plan` — 2B goal decomposition
- `POST /api/judge` — 4B evidence evaluation
- `POST /api/synthesize` — 9B document synthesis

### Knowledge Graph
- `POST /api/entities` — Create entity
- `GET /api/entities/{name}` — Full entity with claims, relationships, decay
- `POST /api/relationships` — Link entities
- `POST /api/claims` — Add claims with confidence + decay
- `GET /api/claims/decay` — Find decayed claims needing re-verification
- `POST /api/hypotheses` — Create hypothesis
- `POST /api/hypotheses/{id}/predictions` — Add predictions
- `POST /api/hypotheses/{id}/evidence` — Add evidence
- `GET /api/hypotheses/{id}/experiments` — Suggest experiments

### Missions
- `POST /api/missions` — Create mission
- `GET /api/missions/{id}` — Full mission tree
- `POST /api/missions/{id}/decompose` — Break into goals
- `POST /api/goals/{id}/plan` — Break into sub-goals
- `POST /api/subgoals/{id}/experiments` — Add experiment

### Skills
- Auto-generated from 5+ confirmed claims in a domain
- Executable workflows with success rate tracking

### Events
- `GET /api/events/stream` — SSE event stream
- `POST /api/events/publish` — Publish event

### Retriever
- `POST /api/retrieve` — Multi-layer conceptual search
- `POST /api/index` — Generate embeddings for entities/claims
- `GET /api/retrieve/status` — Check embedding coverage

## ContextRetriever

Multi-layer retrieval that searches more than documents — it searches concepts, entities, claims, relationships, and hypotheses simultaneously.

### Architecture

```
User Query
     │
     ▼
Query Expansion (synonym groups)
     │
     ▼
┌──────────────────────────┐
│ FTS5 Lexical (ILIKE)     │  ← 0.25 weight
│ Semantic (embeddings)     │  ← 0.35 weight
│ Graph (BFS 2-hop)        │  ← 0.25 weight
│ Fact (claim overlap)      │  ← 0.15 weight
└──────────────────────────┘
     │
     ▼
Reranker → Context Pack
```

### Four Layers

**Lexical** — ILIKE text search across entities, claims, memories, hypotheses. Matches expanded query terms via synonym groups (e.g. "DS" → "dropshipping", "error" → "failure" → "bug" → "fault").

**Semantic** — 768-dim embeddings via `nomic-embed-text` (Ollama). Cosine similarity against stored entity and claim vectors. Threshold: 0.3.

**Graph** — BFS traversal from query-matched entities, 2 hops deep. Follows relationships to discover connected entities even when no words overlap.

**Fact** — Claim key/value overlap scoring using expanded query terms. Also returns claims from entities adjacent to matched entities.

### Concept Expansion

Queries are expanded with synonym groups before searching:

```
"cheap supplier"
  → supplier, vendor, manufacturer, wholesaler, provider, seller
  → cheap, low cost, bulk, discount, budget, affordable, inexpensive
```

This lets queries like `"DS search permission"` match claims about `dropshipping API authentication`.

### Scoring

```python
score = 0.25 * lexical + 0.35 * semantic + 0.25 * graph + 0.15 * fact
```

Semantic has the highest weight because embeddings capture conceptual meaning that text and graph alone miss. A 4B model with excellent fact-aware retrieval outperforms a 35B model with mediocre context.

### Usage

```bash
# Fast path (no LLM call, lexical+graph+fact only)
curl -X POST http://localhost:7710/api/retrieve \
  -d '{"query": "supplier lookup failure", "use_semantic": false}'

# Full retrieval (includes embedding similarity)
curl -X POST http://localhost:7710/api/retrieve \
  -d '{"query": "cheap supplier", "use_semantic": true}'

# Generate embeddings for all entities and claims
curl -X POST http://localhost:7710/api/index \
  -d '{"target_type": "all"}'
```

## MCP Tools (28)

| Category | Tools |
|----------|-------|
| Tome (8) | `tome_create`, `tome_render`, `tome_search`, `tome_link`, `tome_get`, `tome_extract`, `tome_export_graph`, `tome_index` |
| Observation (2) | `list_observations`, `list_opportunities` |
| Mission (3) | `create_mission`, `mission_status`, `execute_mission` |
| Scientist (4) | `list_hypotheses`, `evaluate_hypothesis`, `list_predictions`, `suggest_experiments` |
| Knowledge (4) | `get_entity`, `find_conflicts`, `recall_memories`, `export_to_tome` |
| Skills (2) | `list_skills`, `execute_skill` |
| Utility (1) | `query_utility` |
| Router (1) | `route` |
| Search (3) | `search`, `browse`, `extract` |

## Configuration

All via `SEARCHV2_` prefix environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARCHV2_PORT` | 7710 | HTTP port |
| `SEARCHV2_OLLAMA_BASE_URL` | `http://100.84.161.63:11434` | Ollama API |
| `SEARCHV2_SEARXNG_URL` | `http://100.84.161.63:8888` | SearXNG |
| `SEARCHV2_MODEL_REFLEX` | `qwen3.5:0.8b` | 0.8B model |
| `SEARCHV2_MODEL_ATTENTION` | `qwen3.5:2b` | 2B model |
| `SEARCHV2_MODEL_REASONING` | `qwen3.5:4b` | 4B model |
| `SEARCHV2_MODEL_ACTION` | `qwen3.5:9b` | 9B model |
| `SEARCHV2_NIM_API_KEY` | | NVIDIA NIM key (cloud tier) |
| `SEARCHV2_AUTO_ACCEPT_UTILITY_THRESHOLD` | 10.0 | Auto-accept opportunities |
| `SEARCHV2_RE_VERIFICATION_THRESHOLD` | 0.5 | Re-verify decayed claims |
| `SEARCHV2_SKILL_DETECTION_THRESHOLD` | 5 | Claims needed for skill generation |

## Confidence Decay

```python
effective_confidence = base_confidence * exp(-decay_rate * days_since_verified)
# When effective_confidence < threshold → schedule re-verification
```

## Nuclear Arbitration

4B and 9B disagree → logged escalation → NVIDIA NIM arbitrates → final decision with reasoning.
