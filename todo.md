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
