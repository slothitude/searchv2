Here's the enhanced plan. The core insight: HTML+Jinja2 isn't just a rendering format — it's the \*\*semantic substrate\*\* of the entire intelligence stack. Every document becomes both human-readable and machine-parsable through structured metadata, embedded schemas, and rich visual templates.Here's the enhanced spec, with \*\*HTML as the rich dual-layer substrate\*\* — human-readable and agent-readable simultaneously.



\---



\## SearchV2 — Enhanced Plan



\### The Core Upgrade: HTML Is the Protocol



The original spec treats Tome as a document store. The enhanced version treats \*\*HTML+Jinja2 as the universal semantic layer\*\* — every piece of knowledge, every claim, every entity becomes an HTML document with embedded machine-readable structure. Agents read the same HTML humans read, via `data-\*` attributes, `<meta>` tags, JSON-LD `<script type="application/ld+json">`, and microdata. No separate serialization format. One source of truth.



\---



\### Phase 1: Tome — Enhanced HTML Document Templates



Every document class gets a dedicated Jinja2 base template with a \*\*canonical visual layer\*\* and \*\*embedded agent schema\*\*.



\*\*Document type hierarchy:\*\*



```

BaseDocument

├── EntityDocument        → entity profile pages

├── HypothesisDocument    → claim + evidence + confidence meter

├── MissionDocument       → mission dashboard with progress

├── SkillDocument         → executable workflow with step runner

├── MemoryDocument        → episodic record with timeline

└── ObservationDocument   → signal log with source provenance

```



\*\*Template anatomy for `EntityDocument`:\*\*



```html

<!DOCTYPE html>

<html lang="en" data-entity-type="{{ entity.type }}" data-entity-id="{{ entity.id }}">

<head>

&#x20; <meta name="entity-confidence" content="{{ entity.confidence }}">

&#x20; <meta name="last-verified" content="{{ entity.last\_verified }}">

&#x20; <meta name="decay-rate" content="{{ entity.decay\_rate }}">

&#x20; <script type="application/ld+json">

&#x20; {

&#x20;   "@context": "https://schema.org",

&#x20;   "@type": "Thing",

&#x20;   "name": "{{ entity.name }}",

&#x20;   "description": "{{ entity.description }}",

&#x20;   "additionalProperty": \[

&#x20;     {% for claim in entity.claims %}

&#x20;     {

&#x20;       "@type": "PropertyValue",

&#x20;       "name": "{{ claim.key }}",

&#x20;       "value": "{{ claim.value }}",

&#x20;       "additionalType": "Claim",

&#x20;       "certainty": {{ claim.confidence }}

&#x20;     }{% if not loop.last %},{% endif %}

&#x20;     {% endfor %}

&#x20;   ]

&#x20; }

&#x20; </script>

</head>

<body>

&#x20; <!-- VISUAL LAYER: Human readable -->

&#x20; <header class="entity-header">

&#x20;   <h1>{{ entity.name }}</h1>

&#x20;   <div class="confidence-bar" 

&#x20;        data-value="{{ entity.confidence }}"

&#x20;        style="--fill: {{ (entity.confidence \* 100)|round }}%">

&#x20;     <span>{{ (entity.confidence \* 100)|round }}% confidence</span>

&#x20;   </div>

&#x20;   <div class="decay-indicator {% if entity.effective\_confidence < 0.5 %}stale{% endif %}">

&#x20;     Last verified: {{ entity.last\_verified | timeago }}

&#x20;   </div>

&#x20; </header>



&#x20; <!-- Claims table with inline agent metadata -->

&#x20; <section class="claims" itemscope itemtype="https://schema.org/ItemList">

&#x20;   {% for claim in entity.claims %}

&#x20;   <div class="claim-row" 

&#x20;        itemprop="itemListElement"

&#x20;        data-claim-key="{{ claim.key }}"

&#x20;        data-claim-confidence="{{ claim.confidence }}"

&#x20;        data-claim-evidence-count="{{ claim.evidence\_count }}"

&#x20;        data-disputed="{{ claim.disputed | lower }}">

&#x20;     <span class="claim-key">{{ claim.key }}</span>

&#x20;     <span class="claim-value">{{ claim.value }}</span>

&#x20;     <meter value="{{ claim.confidence }}" min="0" max="1" 

&#x20;            class="inline-confidence"></meter>

&#x20;   </div>

&#x20;   {% endfor %}

&#x20; </section>



&#x20; <!-- Relationship graph (visual + queryable) -->

&#x20; <section class="relationships" data-center-entity="{{ entity.name }}">

&#x20;   {% for rel in entity.relationships %}

&#x20;   <a class="rel-edge" 

&#x20;      href="/docs/{{ rel.target\_slug }}"

&#x20;      data-relation="{{ rel.relation\_type }}"

&#x20;      data-confidence="{{ rel.confidence }}">

&#x20;     <span class="rel-arrow">{{ entity.name }} → {{ rel.relation\_type }} → {{ rel.target }}</span>

&#x20;   </a>

&#x20;   {% endfor %}

&#x20; </section>

</body>

</html>

```



\*\*Agent reads this with standard HTML parsing — no secondary API needed.\*\* `document.querySelectorAll('\[data-claim-key]')` extracts all claims. `<script type="application/ld+json">` gives the full graph in one parse. The visual confidence bar and the machine-readable `<meter>` are the same data rendered twice.



\---



\### Phase 2 Enhancement: Observation Documents



Observations get stored as lightweight HTML snippets with full provenance embedded:



```html

<article class="observation"

&#x20;        data-source="{{ obs.source }}"

&#x20;        data-confidence="{{ obs.confidence }}"

&#x20;        data-importance="{{ obs.importance }}"

&#x20;        data-category="{{ obs.category }}"

&#x20;        data-created="{{ obs.created\_at.isoformat() }}">

&#x20; <p class="obs-text">{{ obs.observation\_text }}</p>

&#x20; <footer>

&#x20;   <span class="source-badge">{{ obs.source }}</span>

&#x20;   <meter value="{{ obs.confidence }}" min="0" max="1" title="Confidence"></meter>

&#x20;   <meter value="{{ obs.importance }}" min="0" max="1" title="Importance"></meter>

&#x20; </footer>

</article>

```



The Opportunity Engine queries these HTML fragments directly via FTS5 on their rendered text, and reads structured scores from `data-\*` attributes — \*\*no join across tables needed\*\*.



\---



\### Phase 3 Enhancement: Mission Dashboard Template



Mission documents become \*\*live dashboards\*\* rendered by the template engine:



```html

<!-- MissionDocument.html.j2 -->

<article class="mission-dashboard"

&#x20;        data-mission-id="{{ mission.id }}"

&#x20;        data-status="{{ mission.status }}"

&#x20;        data-utility="{{ mission.utility\_score }}">



&#x20; <h1>{{ mission.description }}</h1>



&#x20; <!-- Progress ring: visual + accessible -->

&#x20; <div class="progress-ring" 

&#x20;      role="progressbar"

&#x20;      aria-valuenow="{{ mission.completion\_pct }}"

&#x20;      style="--pct: {{ mission.completion\_pct }}">

&#x20;   {{ mission.completion\_pct }}%

&#x20; </div>



&#x20; <!-- Goal tree — each node is agent-queryable -->

&#x20; {% for goal in mission.goals %}

&#x20; <section class="goal-block"

&#x20;          data-goal-id="{{ goal.id }}"

&#x20;          data-status="{{ goal.status }}"

&#x20;          data-budget-cents="{{ goal.budget\_cents }}"

&#x20;          data-spent-cents="{{ goal.spent\_cents }}"

&#x20;          data-utility="{{ goal.utility\_score }}">

&#x20;   <h2>{{ goal.description }}</h2>

&#x20;   <div class="budget-bar" style="--spent: {{ goal.spent\_pct }}%"></div>



&#x20;   {% for sub in goal.sub\_goals %}

&#x20;   <div class="sub-goal"

&#x20;        data-type="{{ sub.type }}"

&#x20;        data-status="{{ sub.status }}"

&#x20;        data-tool-plan="{{ sub.tool\_plan | tojson | e }}"

&#x20;        data-info-value="{{ sub.info\_value }}">

&#x20;     {{ sub.description }}

&#x20;   </div>

&#x20;   {% endfor %}

&#x20; </section>

&#x20; {% endfor %}

</article>

```



The `data-tool-plan` attribute contains the full JSON tool execution plan. An agent reading this document can extract the plan, check status, and resume interrupted work — \*\*mission state lives in Tome, not just the DB\*\*.



\---



\### Phase 4 Enhancement: Hypothesis Documents — Scientific Paper Format



```html

<!-- HypothesisDocument renders like a micro-paper -->

<article class="hypothesis-paper"

&#x20;        data-hypothesis-id="{{ h.id }}"

&#x20;        data-status="{{ h.status }}"

&#x20;        data-confidence="{{ h.confidence }}">



&#x20; <header>

&#x20;   <h1 class="claim">{{ h.claim }}</h1>

&#x20;   <div class="confidence-gauge" data-value="{{ h.confidence }}">

&#x20;     <label>Confidence: {{ (h.confidence \* 100)|round }}%</label>

&#x20;     <progress value="{{ h.confidence }}" max="1"></progress>

&#x20;   </div>

&#x20; </header>



&#x20; <section class="predictions">

&#x20;   <h2>Predictions</h2>

&#x20;   {% for p in h.predictions %}

&#x20;   <div class="prediction"

&#x20;        data-prediction-id="{{ p.id }}"

&#x20;        data-status="{{ p.status }}">

&#x20;     <span class="pred-status-icon">

&#x20;       {% if p.status == 'confirmed' %}✓

&#x20;       {% elif p.status == 'refuted' %}✗

&#x20;       {% else %}?{% endif %}

&#x20;     </span>

&#x20;     {{ p.prediction\_text }}

&#x20;   </div>

&#x20;   {% endfor %}

&#x20; </section>



&#x20; <section class="evidence-list">

&#x20;   <h2>Evidence</h2>

&#x20;   {% for e in h.evidence %}

&#x20;   <blockquote class="evidence-item"

&#x20;               data-direction="{{ e.direction }}"

&#x20;               data-confidence-impact="{{ e.confidence\_impact }}"

&#x20;               cite="{{ e.source\_url }}">

&#x20;     {{ e.content }}

&#x20;     <footer>

&#x20;       <a href="{{ e.source\_url }}">Source</a>

&#x20;       <span class="impact-badge {{ 'positive' if e.direction == 'supporting' else 'negative' }}">

&#x20;         {{ '+' if e.direction == 'supporting' else '−' }}{{ (e.confidence\_impact \* 100)|round }}%

&#x20;       </span>

&#x20;     </footer>

&#x20;   </blockquote>

&#x20;   {% endfor %}

&#x20; </section>

</article>

```



Agents scrape this with `querySelectorAll('\[data-direction="supporting"]')` to count supporting vs refuting evidence, extract confidence impact without hitting the DB.



\---



\### Phase 5 Enhancement: Skill Documents — Executable Runbooks



```html

<!-- SkillDocument renders as a runbook with embedded execution metadata -->

<article class="skill-runbook"

&#x20;        data-skill-id="{{ skill.id }}"

&#x20;        data-domain="{{ skill.domain }}"

&#x20;        data-success-rate="{{ skill.success\_rate }}"

&#x20;        data-usage-count="{{ skill.usage\_count }}">



&#x20; <header>

&#x20;   <h1>{{ skill.name }}</h1>

&#x20;   <p>{{ skill.description }}</p>

&#x20;   <div class="skill-stats">

&#x20;     <span data-metric="success-rate">{{ (skill.success\_rate \* 100)|round }}% success</span>

&#x20;     <span data-metric="usage-count">{{ skill.usage\_count }} executions</span>

&#x20;   </div>

&#x20; </header>



&#x20; <section class="preconditions">

&#x20;   <h2>Preconditions</h2>

&#x20;   {% for pre in skill.preconditions %}

&#x20;   <div class="precondition" data-key="{{ pre.key }}" data-required="{{ pre.required }}">

&#x20;     {{ pre.description }}

&#x20;   </div>

&#x20;   {% endfor %}

&#x20; </section>



&#x20; <ol class="procedure-steps">

&#x20;   {% for step in skill.steps %}

&#x20;   <li class="step"

&#x20;       data-step-index="{{ loop.index0 }}"

&#x20;       data-tool="{{ step.tool }}"

&#x20;       data-params="{{ step.params | tojson | e }}"

&#x20;       data-expected-output="{{ step.expected\_output }}">

&#x20;     <h3>{{ step.title }}</h3>

&#x20;     <p>{{ step.description }}</p>

&#x20;     {% if step.code %}

&#x20;     <pre><code data-lang="{{ step.lang }}">{{ step.code }}</code></pre>

&#x20;     {% endif %}

&#x20;   </li>

&#x20;   {% endfor %}

&#x20; </ol>

</article>

```



The `execute\_skill` MCP tool reads this document, extracts `data-tool` and `data-params` from each step, and runs them in sequence — \*\*the skill definition and its execution spec are one document\*\*.



\---



\### New: Tome Visual Themes per Document Type



Each document type gets a visual personality via CSS custom properties injected by Jinja2:



| Type | Color scheme | Layout | Special widget |

|---|---|---|---|

| `EntityDocument` | Teal accent | Two-column: claims + graph | Confidence decay sparkline |

| `HypothesisDocument` | Purple accent | Scientific paper | Evidence balance bar |

| `MissionDocument` | Amber accent | Dashboard grid | Utility / budget gauges |

| `SkillDocument` | Blue accent | Runbook | Step executor widget |

| `MemoryDocument` | Coral accent | Timeline | Related entity links |

| `ObservationDocument` | Gray accent | Log entry | Source provenance badge |



\---



\### New: `TomeVault.export\_knowledge()` — Full Graph to HTML



A new method that renders the entire knowledge graph as a \*\*navigable HTML site\*\*:



```python

async def export\_knowledge(root\_entity: str) -> str:

&#x20;   """

&#x20;   Walk entity graph from root. For each entity:

&#x20;   1. Render EntityDocument with all claims + relationships

&#x20;   2. Embed JSON-LD for the full subgraph

&#x20;   3. Generate index page with FTS search

&#x20;   4. Return as zip or serve via /graph/ route

&#x20;   """

```



Output: a static HTML site where agents can navigate the knowledge graph via `<a>` tags and humans can browse it visually. Same file. No API layer needed for offline agent reasoning.



\---



\### New: Agent-Readable Index Document



```html

<!-- /docs/index — machine-first index of the entire Tome -->

<meta name="tome-stats" 

&#x20;     content='{"doc\_count": 1420, "entity\_count": 304, "skill\_count": 18}'>

<script type="application/ld+json">

{

&#x20; "@context": "https://schema.org",

&#x20; "@type": "DataCatalog",

&#x20; "hasPart": \[

&#x20;   {% for doc in documents %}

&#x20;   {

&#x20;     "@type": "Dataset",

&#x20;     "identifier": "{{ doc.slug }}",

&#x20;     "name": "{{ doc.title }}",

&#x20;     "additionalType": "{{ doc.doc\_type }}",

&#x20;     "dateModified": "{{ doc.updated\_at.isoformat() }}"

&#x20;   }{% if not loop.last %},{% endif %}

&#x20;   {% endfor %}

&#x20; ]

}

</script>

```



The `tome\_search` MCP tool hits FTS5. The `tome\_get` MCP tool returns this raw HTML. Agents parse both.



\---



\### Updated Project Structure



```

searchv2/

├── templates/

│   ├── base.html.j2             ← base with CSS vars + agent meta

│   ├── documents/

│   │   ├── entity.html.j2

│   │   ├── hypothesis.html.j2

│   │   ├── mission.html.j2

│   │   ├── skill.html.j2

│   │   ├── memory.html.j2

│   │   └── observation.html.j2

│   ├── components/

│   │   ├── confidence\_bar.html.j2

│   │   ├── evidence\_balance.html.j2

│   │   ├── entity\_graph.html.j2

│   │   └── decay\_indicator.html.j2

│   └── index.html.j2            ← agent-readable catalog

├── static/

│   ├── tome.css                 ← visual layer per doc type

│   └── tome.js                  ← optional: confidence decay viz

```



\---



\### Updated `TomeVault` Core



```python

class TomeVault:

&#x20;   # Original

&#x20;   async def create\_document(slug, title, source, variables=None, parent\_id=None)

&#x20;   async def render\_document(slug) -> str

&#x20;   async def search\_documents(query) -> list

&#x20;   async def get\_graph(slug) -> dict

&#x20;   async def list\_documents(tag=None, parent=None)



&#x20;   # New

&#x20;   async def create\_typed\_document(slug, doc\_type, entity\_data) -> str

&#x20;       # Select template by doc\_type, inject entity\_data, store rendered source

&#x20;   async def extract\_agent\_data(slug) -> dict

&#x20;       # Parse rendered HTML, extract all data-\* and JSON-LD → structured dict

&#x20;   async def export\_knowledge(root\_entity) -> str

&#x20;       # Walk graph, render all linked entities as navigable HTML site

&#x20;   async def get\_index() -> str

&#x20;       # Render machine-first catalog document

&#x20;   async def sync\_from\_db(entity\_id, doc\_type)

&#x20;       # On DB write, re-render Tome document (cache invalidation hook)

```



\---



\### Updated MCP Tool Set (28 tools)



Add three new Tome tools:



| Tool | Purpose |

|---|---|

| `tome\_extract` | Parse HTML doc → structured `data-\*` + JSON-LD dict for agents |

| `tome\_export\_graph` | Walk entity graph → static HTML site |

| `tome\_index` | Get machine-readable catalog of all documents |



\---



\### Verification Additions



8\. \*\*HTML dual-layer\*\*: Create entity → render → verify `data-\*` attributes match DB values → verify JSON-LD is valid → verify FTS5 hits on claim text

9\. \*\*Agent read\*\*: `tome\_extract` on a `HypothesisDocument` → returns confidence, predictions, evidence direction without any DB query

10\. \*\*Export\*\*: `export\_knowledge('AliExpress')` → static HTML site with full subgraph, navigable by `<a>` tags



\---



The fundamental shift: every piece of intelligence SearchV2 accumulates — every observation, claim, hypothesis, skill — exists as a \*\*beautiful HTML page that an agent can also read programmatically\*\*. Tome isn't a rendering layer on top of the database. It's the intelligence layer itself.

