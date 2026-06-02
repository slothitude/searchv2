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

- [x] **Dual lifespan creates duplicate workers** — `app.py` and `mcp_server.py` both start queue_worker and RSS poller. Fixed: guard `start()` with `if self._task is not None: return` in both `QueueWorker` and `RSSPoller`.
- [x] **Infinite retry loop for broken articles** — `core/rss.py:ingest_article` returns False but article stays `ingested=False`. Fixed: added `retry_count` column to `RSSArticle`, `mark_ingest_failed()` increments it, marks `ingested=True` after `rss_max_retries` (default 3). `get_uningested()` filters by `retry_count < max`.

### HIGH — Performance

- [x] **Full table scan for semantic search** — `core/retriever.py:182`. Fixed: replaced Python loop with numpy batch cosine similarity (`stored_vecs @ query_np`).
- [x] **Full table scan for decay check** — `core/knowledge.py:188`. Fixed: added WHERE clause filtering claims with `last_verified` older than computed cutoff.
- [x] **`index_all` scans all entities after ingest** — `core/ingest.py:317-329`. Fixed: only indexes entities just created in this batch via `Entity.name.in_(ingested_entities)`.
- [x] **`get_embedding_batch` is sequential** — `core/retriever.py:87-93`. Fixed: uses `asyncio.gather` for concurrent embedding requests.
- [x] **N+1 queries in FeedStore.list_feeds** — `models/rss.py:51-72`. Fixed: single GROUP BY query for article counts, then join in Python.
- [x] **N+1 queries in FeedStore.list_articles** — `models/rss.py:129-159`. Fixed: single query loads all feeds, then lookup from map.

### MEDIUM — Reliability

- [x] **SQLite DB path is relative** — `config.py:11`. Fixed: `model_validator` resolves `data_dir` to absolute, `models/base.py` derives db_url from resolved path.
- [x] **No retry/backoff for failing feeds** — `core/rss.py`. Fixed: `update_feed_fetched()` auto-disables feed after `rss_disable_after_errors` (default 10) consecutive errors.
- [x] **Event bus has no cleanup for dead subscribers** — `core/events.py`. Fixed: subscribers have TTL (30 min), `publish()` calls `_cleanup_stale()` to remove expired entries.
- [ ] **Prompt injection via article content** — `core/ingest.py:53-63`. Malicious articles could manipulate LLM extraction. Sanitize or truncate aggressively.
- [x] **`export_to_tome` HTML injection** — `mcp_server.py:290-303`. Fixed: all entity/claim values wrapped with `html.escape()`.
- [x] **`_extract_title` doesn't decode HTML entities** — `core/extractor.py:75`. Fixed: uses `html.unescape()` on extracted title.

