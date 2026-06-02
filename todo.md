# SearchV2 TODO

## Conceptual Fuzzy Matching / Multi-Layer Retrieval

The retrieval system should search more than just documents — it should search concepts, entities, claims, relationships, and hypotheses simultaneously.

### Three Layers of Similarity

1. **Text Similarity** — Traditional fuzzy search (Levenshtein, Jaro-Winkler, Trigram). SQLite FTS5 already handles most of this.

2. **Semantic Similarity** — Embeddings (BGE-small, Nomic Embed, Qwen Embedding). `car` ≈ `vehicle`, `router` ≈ `gateway`. Stored separately from LLM.

3. **Fact Similarity** — Graph-based. Query "supplier lookup failure" matches AliExpress claims because:
   ```
   supplier lookup → product search → dropshipping api → AliExpress
   ```
   No words overlap, but the fact graph connects them.

### Scoring Formula

```python
score = (
    0.4 * embedding_similarity +
    0.3 * graph_distance +
    0.2 * claim_overlap +
    0.1 * recency
)
```

### Concept Expansion

Before searching, expand the query:
```
"cheap supplier"
  → supplier, vendor, manufacturer, wholesaler
  → cheap, low cost, bulk, discount
```

### Architecture

```
User Query
     │
     ▼
Query Expansion
     │
     ▼
FTS5 Search ──┬── Lexical Results
               ├── Vector Results
               ├── Graph Results
               └── Fact Results
     │
     ▼
Reranker
     │
     ▼
Context Pack
     │
     ▼
LLM
```

### Implementation

```python
class ContextRetriever:
    async def retrieve(query):
        lexical = fts_search(query)
        semantic = vector_search(query)
        graph = graph_search(query)
        facts = fact_search(query)
        return rerank(lexical, semantic, graph, facts)
```

**Priority insight**: A 4B model with excellent fact-aware retrieval will outperform a 35B model with mediocre context. The retrieval system is the multiplier for every model in the ladder.

---

## From Audit — Remaining Items (2026-06-03)

### HIGH — Security

- [ ] **No authentication on MCP tools or API endpoints** — `mcp_server.py`, `app.py`. Anyone with network access to port 7711 can enqueue jobs, ingest URLs, create entities. Add auth middleware or token-based access.
- [ ] **SSRF via browse/extract/ingest tools** — `core/extractor.py:47-71`. `fetch_and_extract` follows redirects with no URL validation. Block internal IPs (`127.0.0.0/8`, `10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`).
- [ ] **Hardcoded secret key** — `config.py:57`. Default `"searchv2-dev-secret-change-me"` is well-known. Remove default or force env var.

### HIGH — Architecture

- [ ] **Dual lifespan creates duplicate workers** — `app.py` and `mcp_server.py` both start queue_worker and RSS poller. If both servers run simultaneously, duplicate job processing and duplicate RSS ingestion. Ensure only one runs at a time (check `worker._task is None` before starting, or use a singleton flag).
- [ ] **Infinite retry loop for broken articles** — `core/rss.py:ingest_article` returns False but article stays `ingested=False`. On next poll it gets re-enqueued forever. Add a retry counter (max 3 attempts), then mark as ingested to stop the loop.

### HIGH — Performance

- [ ] **Full table scan for semantic search** — `core/retriever.py:182`. Loads every embedding, deserializes JSON vectors, computes cosine similarity in Python. Won't scale past a few hundred embeddings. Switch to a vector index (numpy array, or FAISS/HNSW) or at minimum a numpy matrix for batch cosine similarity.
- [ ] **Full table scan for decay check** — `core/knowledge.py:188`. `select(Claim)` with no WHERE. Add WHERE clause on `last_verified` and pagination.
- [ ] **`index_all` scans all entities after ingest** — `core/ingest.py:317-329`. Only index entities just created in this batch, not the entire table.
- [ ] **`get_embedding_batch` is sequential** — `core/retriever.py:87-93`. N sequential HTTP requests to Ollama. Use `asyncio.gather` or Ollama's batch embedding API.
- [ ] **N+1 queries in FeedStore.list_feeds** — `models/rss.py:51-72`. Separate COUNT per feed. Use JOIN or subquery.
- [ ] **N+1 queries in FeedStore.list_articles** — `models/rss.py:129-159`. Separate query per feed_id. Use JOIN.

### MEDIUM — Reliability

- [ ] **SQLite DB path is relative** — `config.py:11`. `data/searchv2.db` depends on working directory. Resolve relative to `data_dir` setting.
- [ ] **No retry/backoff for failing feeds** — `core/rss.py`. `error_count` increments but feed never gets disabled. Auto-disable after N consecutive errors (e.g., 10).
- [ ] **Event bus has no cleanup for dead subscribers** — `core/events.py`. Dropped SSE connections leak subscriber queues forever. Add TTL or periodic cleanup.
- [ ] **Prompt injection via article content** — `core/ingest.py:53-63`. Malicious articles could manipulate LLM extraction. Sanitize or truncate aggressively.
- [ ] **`export_to_tome` HTML injection** — `mcp_server.py:290-303`. Entity/claim values interpolated raw into HTML. Use HTML escaping.
- [ ] **`_extract_title` doesn't decode HTML entities** — `core/extractor.py:75`. `&amp;` etc. appear raw. Use `html.unescape()`.

