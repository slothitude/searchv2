# SearchV2 — Knowledge Acquisition System

A continuously improving knowledge organism. Observes, identifies opportunities, plans missions, generates hypotheses, accumulates knowledge, learns procedures, and decays stale information. Feeds itself from Australian news via RSS and autonomously searches for topics it discovers.

## Pipeline

```
Observation → Opportunity → Mission → Goal → Hypothesis → Prediction → Evidence → Claim → Knowledge → Capability → Skill
```

## Stack

FastAPI + SQLite (FTS5) + Jinja2 + Ollama + SearXNG + feedparser + FastMCP

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
pip install -r requirements.txt
uvicorn app:app --reload --port 7710
```

The MCP server auto-starts via stdio when Claude Code connects.

## Two Storage Systems

1. **Tome** — HTML+Jinja2 document vault with typed templates (entity, hypothesis, mission, skill, memory, observation). Every document is both human-readable and machine-parsable via `data-*` attributes and JSON-LD.
2. **Memory** — Separate from knowledge. Episodic recall ("last time we researched X, Y happened").

## RSS News Pipeline

SearchV2 continuously ingests Australian news via RSS.

### Feeds

10 Australian sources polled every hour:

| Feed | Source |
|------|--------|
| ABC News, ABC Just In | abc.net.au |
| Guardian Australia | theguardian.com/au |
| Google News AU | news.google.com (AU) |
| SBS News, 9News, 7News | Australian broadcasters |
| BBC Australia | bbci.co.uk |
| Crikey, Perth Now | Independent |

### Self-Learning Loop

```
RSS articles arrive (titles)
     │
     ▼
0.8B extracts topic keywords from titles
     │
     ▼
Follow-up web searches for interesting topics
     │
     ▼
Full ingest pipeline (extract → classify → store → embed)
     │
     ▼
Knowledge grows based on what the news is talking about
```

The system discovers what matters and researches it — no human queries needed.

## Background Queue

Long-running jobs run asynchronously in a persistent queue (stored in SQLite).

### Job Types

| Job | Trigger | Pipeline |
|-----|---------|----------|
| `search` | `queue_search` | SearXNG search, return results |
| `ingest` | `queue_ingest` | Full search→extract→classify→store→index |
| `rss_ingest` | Auto / `rss_refresh` | Extract article text → ingest into knowledge graph |
| `rss_search` | Auto | Topic extraction from article titles → follow-up searches |

### Usage

```bash
# Fire-and-forget ingest
queue_ingest(query="Australian housing policy")

# Check status
queue_status(job_id=123)

# Cancel a job
queue_cancel(job_id=123)
```

## Security

| Feature | Implementation |
|---------|---------------|
| **API Authentication** | Bearer token on all endpoints (`Authorization: Bearer <key>`) |
| **SSRF Protection** | URL validation blocks private IPs, localhost, link-local ranges (RFC 1918) |
| **Secret Key** | Auto-generated 64-char hex on first run, persisted to `data/.secret_key` |
| **Prompt Injection** | Regex sanitization + `===BEGIN/END ARTICLE===` delimiters |
| **HTML Escaping** | All user-generated content escaped in Tome exports |
| **Event Bus TTL** | Stale subscribers cleaned up after 30 minutes |

## API

All endpoints require `Authorization: Bearer <token>`.

### Tome
- `POST/GET/PATCH/DELETE /api/docs` — CRUD documents
- `GET /api/docs/{slug}/render` — Render with Jinja2 (cached)
- `GET /api/docs/search?q=` — FTS5 search
- `GET /api/docs/{slug}/graph` — Document link graph
- `POST /api/docs/link` — Link two documents
- `POST /api/docs/{slug}/variables` — Set Jinja2 variables
- `POST /api/docs/{slug}/assets` — Upload binary assets

### Research
- `POST /api/search` — SearXNG web search (engine-pinned to Google/DuckDuckGo/Bing)
- `POST /api/extract` — URL → text extraction (SSRF-protected)
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

### RSS
- `GET /api/rss/feeds` — List feeds with article counts
- `POST /api/rss/refresh` — Trigger fetch (one feed or all)
- `GET /api/rss/articles` — List recent articles

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

### Ingest
- `POST /api/ingest` — Search→Extract→Classify→Store→Index pipeline

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

**Semantic** — 768-dim embeddings via `nomic-embed-text` (Ollama). Batch cosine similarity with numpy. Threshold: 0.3.

**Graph** — BFS traversal from query-matched entities, 2 hops deep. Follows relationships to discover connected entities even when no words overlap.

**Fact** — Claim key/value overlap scoring using expanded query terms. Also returns claims from entities adjacent to matched entities.

### Scoring

```python
score = 0.25 * lexical + 0.35 * semantic + 0.25 * graph + 0.15 * fact
```

## Ingest Pipeline

Automated search→extract→classify→store→index pipeline that makes any web search retrievable.

### Flow

```
Query
  │
  ▼
SearXNG (pinned engines: Google, DuckDuckGo, Bing)
  │
  ▼
Extract top N URLs (fetch_and_extract, SSRF-protected)
  │
  ▼
Model probe → 0.8B Reflex (first responding model wins)
  ├─ Success: structured entities + claims from LLM
  └─ All fail: regex fallback (sentence extraction)
  │
  ▼
KnowledgeGraph (add_entity, add_claim)
  │
  ▼
MemoryStore (episodic record of ingest)
  │
  ▼
Embeddings (nomic-embed-text) → retrievable via ContextRetriever
```

### Usage

```bash
# Full pipeline (search + extract + classify + embed)
search_ingest(query="Raspberry Pi AI inference", max_urls=3)

# Background ingest
queue_ingest(query="fastapi best practices", max_urls=3)

# RSS refresh
rss_refresh()          # all feeds
rss_refresh("ABC News")  # single feed
```

## MCP Tools (40)

| Category | Tools |
|----------|-------|
| Tome (8) | `tome_create`, `tome_render`, `tome_search`, `tome_link`, `tome_get`, `tome_extract`, `tome_export_graph`, `tome_index` |
| Observation (2) | `list_observations`, `list_opportunities` |
| Mission (3) | `create_mission`, `mission_status`, `execute_mission` |
| Scientist (4) | `list_hypotheses`, `evaluate_hypothesis`, `list_predictions`, `suggest_experiments` |
| Knowledge (5) | `get_entity`, `find_conflicts`, `latest`, `recall_memories`, `export_to_tome` |
| Skills (2) | `list_skills`, `execute_skill` |
| Utility (2) | `query_utility`, `route` |
| Search (3) | `search`, `browse`, `extract` |
| Ingest (1) | `search_ingest` |
| Retriever (3) | `retrieve`, `index_embeddings`, `embedding_status` |
| Queue (4) | `queue_search`, `queue_ingest`, `queue_status`, `queue_cancel` |
| RSS (3) | `rss_feeds`, `rss_refresh`, `rss_articles` |

## MCP Setup

Claude Code auto-spawns the server via stdio transport:

```bash
claude mcp add -s user searchv2 -- C:/Python313/python.exe /path/to/searchv2/mcp_server.py --stdio
```

Standalone HTTP mode (port 7711):

```bash
python mcp_server.py  # no --stdio flag
```

## Configuration

All via `SEARCHV2_` prefix environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARCHV2_PORT` | 7710 | HTTP port |
| `SEARCHV2_SECRET_KEY` | auto-generated | Bearer token for API auth |
| `SEARCHV2_OLLAMA_BASE_URL` | `http://100.84.161.63:11434` | Ollama API |
| `SEARCHV2_SEARXNG_URL` | `http://100.84.161.63:8888` | SearXNG |
| `SEARCHV2_SEARXNG_ENGINES` | `google,duckduckgo,bing` | Pinned search engines |
| `SEARCHV2_MODEL_REFLEX` | `qwen3.5:0.8b` | 0.8B model |
| `SEARCHV2_MODEL_ATTENTION` | `qwen3.5:2b` | 2B model |
| `SEARCHV2_MODEL_REASONING` | `qwen3.5:4b` | 4B model |
| `SEARCHV2_MODEL_ACTION` | `qwen3.5:9b` | 9B model |
| `SEARCHV2_NIM_API_KEY` | | NVIDIA NIM key (cloud tier) |
| `SEARCHV2_AUTO_ACCEPT_UTILITY_THRESHOLD` | 10.0 | Auto-accept opportunities |
| `SEARCHV2_RE_VERIFICATION_THRESHOLD` | 0.5 | Re-verify decayed claims |
| `SEARCHV2_SKILL_DETECTION_THRESHOLD` | 5 | Claims needed for skill generation |
| `SEARCHV2_RSS_POLL_INTERVAL` | 3600 | RSS poll interval (seconds) |
| `SEARCHV2_RSS_MAX_RETRIES` | 3 | Failed article retry limit |
| `SEARCHV2_RSS_DISABLE_AFTER_ERRORS` | 10 | Consecutive errors before feed disabled |
| `SEARCHV2_QUEUE_POLL_INTERVAL` | 2.0 | Queue poll interval (seconds) |

## Confidence Decay

```python
effective_confidence = base_confidence * exp(-decay_rate * days_since_verified)
# When effective_confidence < threshold → schedule re-verification
```

## Nuclear Arbitration

4B and 9B disagree → logged escalation → NVIDIA NIM arbitrates → final decision with reasoning.
