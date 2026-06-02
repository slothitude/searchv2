import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from jinja2 import Environment, BaseLoader, StrictUndefined, FileSystemLoader, ChoiceLoader
from jinja2.sandbox import ImmutableSandboxedEnvironment

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from models.tome import (
    Document, DocumentRender, DocumentLink, DocumentVariable, DocumentAsset
)

MAX_EMBED_DEPTH = 5

WHITELISTED_FILTERS = frozenset({
    "embed", "link", "include_asset", "now",
})


def _compute_render_hash(source: str, variables: dict) -> str:
    raw = source + json.dumps(variables, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _build_sandbox() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(undefined=StrictUndefined)
    return env


class TomeVault:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_document(
        self,
        slug: str,
        title: str,
        source: str,
        variables: dict | None = None,
        parent_id: int | None = None,
        is_template: bool = False,
        tags: list | None = None,
        user_id: str = "system",
    ) -> Document:
        doc = Document(
            slug=slug, title=title, source=source,
            parent_id=parent_id, is_template=is_template,
            tags=tags or [], user_id=user_id,
        )
        self.db.add(doc)
        await self.db.flush()

        if variables:
            for k, v in variables.items():
                var = DocumentVariable(
                    document_id=doc.id, key=k, value=v,
                    value_type="json" if isinstance(v, (dict, list)) else
                              "number" if isinstance(v, (int, float)) else
                              "boolean" if isinstance(v, bool) else "string",
                )
                self.db.add(var)

        return doc

    async def get_document(self, slug: str) -> Document | None:
        result = await self.db.execute(
            select(Document).where(Document.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_document_by_id(self, doc_id: int) -> Document | None:
        result = await self.db.execute(
            select(Document).where(Document.id == doc_id)
        )
        return result.scalar_one_or_none()

    async def update_document(self, slug: str, **kwargs) -> Document | None:
        doc = await self.get_document(slug)
        if not doc:
            return None
        for k, v in kwargs.items():
            if hasattr(doc, k):
                setattr(doc, k, v)
        doc.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return doc

    async def delete_document(self, slug: str) -> bool:
        doc = await self.get_document(slug)
        if not doc:
            return False
        await self.db.delete(doc)
        await self.db.flush()
        return True

    async def render_document(self, slug: str, depth: int = 0) -> dict:
        doc = await self.get_document(slug)
        if not doc:
            return {"error": f"Document not found: {slug}"}

        variables = await self._get_variables(doc.id)
        render_hash = _compute_render_hash(doc.source, variables)

        # Check cache
        cached = await self._get_cached_render(doc.id, render_hash)
        if cached:
            return {"slug": slug, "html": cached, "cached": True}

        # Render
        html = await self._render_source(doc.source, variables, depth)
        rendered_html = html

        # Cache
        render = DocumentRender(
            document_id=doc.id, render_hash=render_hash,
            rendered_html=rendered_html,
            variables_snapshot=variables,
        )
        self.db.add(render)
        await self.db.flush()

        return {"slug": slug, "html": rendered_html, "cached": False}

    async def _get_variables(self, doc_id: int) -> dict:
        result = await self.db.execute(
            select(DocumentVariable).where(DocumentVariable.document_id == doc_id)
        )
        variables = {}
        for var in result.scalars():
            val = var.value
            if var.value_type == "number":
                try:
                    val = float(val) if "." in val else int(val)
                except ValueError:
                    pass
            elif var.value_type == "boolean":
                val = val.lower() == "true"
            elif var.value_type == "json":
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
            variables[var.key] = val
        return variables

    async def _get_cached_render(self, doc_id: int, render_hash: str) -> str | None:
        result = await self.db.execute(
            select(DocumentRender).where(
                DocumentRender.document_id == doc_id,
                DocumentRender.render_hash == render_hash,
            )
        )
        cached = result.scalar_one_or_none()
        return cached.rendered_html if cached else None

    async def _render_source(
        self, source: str, variables: dict, depth: int
    ) -> str:
        if depth >= MAX_EMBED_DEPTH:
            return f"<!-- embed depth exceeded -->{source}"

        env = _build_sandbox()
        template = env.from_string(source)

        embed_fn = _make_embed_fn(self, depth)
        link_fn = _make_link_fn(self)

        context = {**variables, "embed": embed_fn, "link": link_fn, "now": datetime.now}
        return template.render(**context)

    async def search_documents(self, query: str, limit: int = 20) -> list[dict]:
        # FTS5-like search via simple ILIKE (SQLite FTS can be added later)
        pattern = f"%{query}%"
        result = await self.db.execute(
            select(Document)
            .where(
                Document.title.ilike(pattern) | Document.source.ilike(pattern)
            )
            .order_by(Document.updated_at.desc())
            .limit(limit)
        )
        docs = result.scalars().all()
        return [
            {"slug": d.slug, "title": d.title, "tags": d.tags,
             "updated_at": d.updated_at.isoformat()}
            for d in docs
        ]

    async def list_documents(
        self,
        tag: str | None = None,
        parent: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        q = select(Document).order_by(Document.updated_at.desc()).limit(limit)
        if parent:
            parent_doc = await self.get_document(parent)
            if parent_doc:
                q = q.where(Document.parent_id == parent_doc.id)
        if tag:
            q = q.where(Document.tags.contains(tag))

        result = await self.db.execute(q)
        docs = result.scalars().all()
        return [
            {"slug": d.slug, "title": d.title, "tags": d.tags,
             "is_template": d.is_template,
             "updated_at": d.updated_at.isoformat()}
            for d in docs
        ]

    async def link_documents(
        self,
        from_slug: str,
        to_slug: str,
        link_type: str = "related",
        context: str = "",
    ) -> DocumentLink | None:
        from_doc = await self.get_document(from_slug)
        to_doc = await self.get_document(to_slug)
        if not from_doc or not to_doc:
            return None

        link = DocumentLink(
            from_id=from_doc.id, to_id=to_doc.id,
            link_type=link_type, context=context,
        )
        self.db.add(link)
        await self.db.flush()
        return link

    async def get_graph(self, slug: str) -> dict:
        doc = await self.get_document(slug)
        if not doc:
            return {"error": f"Document not found: {slug}"}

        # Outgoing links
        out_result = await self.db.execute(
            select(DocumentLink).where(DocumentLink.from_id == doc.id)
        )
        # Incoming links
        in_result = await self.db.execute(
            select(DocumentLink).where(DocumentLink.to_id == doc.id)
        )

        outgoing = []
        for link in out_result.scalars():
            target = await self.get_document_by_id(link.to_id)
            if target:
                outgoing.append({
                    "slug": target.slug, "title": target.title,
                    "link_type": link.link_type, "context": link.context,
                })

        incoming = []
        for link in in_result.scalars():
            source = await self.get_document_by_id(link.from_id)
            if source:
                incoming.append({
                    "slug": source.slug, "title": source.title,
                    "link_type": link.link_type, "context": link.context,
                })

        return {
            "slug": slug,
            "title": doc.title,
            "outgoing": outgoing,
            "incoming": incoming,
        }

    async def set_variables(self, slug: str, variables: dict) -> list[DocumentVariable]:
        doc = await self.get_document(slug)
        if not doc:
            return []

        result = []
        for k, v in variables.items():
            existing = await self.db.execute(
                select(DocumentVariable).where(
                    DocumentVariable.document_id == doc.id,
                    DocumentVariable.key == k,
                )
            )
            var = existing.scalar_one_or_none()
            val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            vtype = "json" if isinstance(v, (dict, list)) else \
                    "number" if isinstance(v, (int, float)) else \
                    "boolean" if isinstance(v, bool) else "string"

            if var:
                var.value = val_str
                var.value_type = vtype
            else:
                var = DocumentVariable(
                    document_id=doc.id, key=k, value=val_str, value_type=vtype,
                )
                self.db.add(var)
            result.append(var)

        await self.db.flush()
        return result

    async def add_asset(
        self, slug: str, filename: str, content: str, mime_type: str = "application/octet-stream"
    ) -> DocumentAsset | None:
        doc = await self.get_document(slug)
        if not doc:
            return None
        asset = DocumentAsset(
            document_id=doc.id, filename=filename,
            content=content, mime_type=mime_type,
        )
        self.db.add(asset)
        await self.db.flush()
        return asset

    async def get_assets(self, slug: str) -> list[dict]:
        doc = await self.get_document(slug)
        if not doc:
            return []
        result = await self.db.execute(
            select(DocumentAsset).where(DocumentAsset.document_id == doc.id)
        )
        return [
            {"id": a.id, "filename": a.filename, "mime_type": a.mime_type}
            for a in result.scalars()
        ]

    # ── Typed document creation ──────────────────────────────

    DOC_TYPE_TEMPLATES = {
        "entity": "documents/entity.html.j2",
        "hypothesis": "documents/hypothesis.html.j2",
        "mission": "documents/mission.html.j2",
        "skill": "documents/skill.html.j2",
        "memory": "documents/memory.html.j2",
        "observation": "documents/observation.html.j2",
    }

    async def create_typed_document(
        self, slug: str, doc_type: str, entity_data: dict,
        title: str = "",
    ) -> Document:
        """Create a document from a typed template with agent-readable metadata."""
        if doc_type not in self.DOC_TYPE_TEMPLATES:
            raise ValueError(f"Unknown doc_type: {doc_type}. Must be one of: {list(self.DOC_TYPE_TEMPLATES.keys())}")

        template_path = self.DOC_TYPE_TEMPLATES[doc_type]
        templates_dir = Path(__file__).parent.parent / "templates"

        env = _build_typed_env(templates_dir)
        try:
            template = env.get_template(template_path)
        except Exception:
            # Fallback to raw string rendering
            template = env.from_string(str(entity_data))

        html = template.render(**entity_data, now=datetime.now)
        title = title or entity_data.get("name", entity_data.get("title", slug))

        return await self.create_document(
            slug=slug, title=title, source=html,
            tags=[doc_type], is_template=False,
        )

    async def extract_agent_data(self, slug: str) -> dict:
        """Parse rendered HTML and extract all data-* attributes and JSON-LD."""
        result = await self.render_document(slug)
        if "error" in result:
            return {"error": result["error"]}
        html = result["html"]

        return _parse_html_agent_data(html)

    async def export_knowledge(self, root_entity: str) -> str:
        """Walk entity graph from root, render all linked entities as navigable HTML."""
        from core.knowledge import KnowledgeGraph
        kg = KnowledgeGraph(self.db)
        full = await kg.get_entity_full(root_entity)
        if not full:
            return f"<!-- Entity not found: {root_entity} -->"

        # Render root entity as typed document
        entity_data = {
            "entity": full,
            "doc_type": "entity",
            "last_verified": datetime.now(timezone.utc).isoformat(),
            "confidence": max(
                (c.get("effective_confidence", c.get("confidence", 0.5))
                 for c in full.get("claims", [])),
                default=0.5,
            ),
            "decay_rate": 0.001,
        }
        try:
            await self.create_typed_document(
                slug=root_entity.lower().replace(" ", "-"),
                doc_type="entity",
                entity_data=entity_data,
                title=f"Entity: {root_entity}",
            )
        except Exception:
            pass  # template not found

        # Walk relationships and export linked entities
        for rel in full.get("relationships", []):
            target_name = rel.get("target") or rel.get("source")
            if target_name:
                try:
                    sub_full = await kg.get_entity_full(target_name)
                    if sub_full:
                        await self.create_typed_document(
                            slug=target_name.lower().replace(" ", "-"),
                            doc_type="entity",
                            entity_data={
                                "entity": sub_full,
                                "doc_type": "entity",
                                "last_verified": datetime.now(timezone.utc).isoformat(),
                                "confidence": 0.5,
                                "decay_rate": 0.001,
                            },
                            title=f"Entity: {target_name}",
                        )
                except Exception:
                    continue

        return f"Exported graph from: {root_entity}"

    async def get_index(self) -> str:
        """Render machine-first catalog document of all documents."""
        docs = await self.list_documents(limit=500)
        # Count by type from tags
        type_counts = {}
        for d in docs:
            for tag in (d.get("tags") or []):
                type_counts[tag] = type_counts.get(tag, 0) + 1

        templates_dir = Path(__file__).parent.parent / "templates"
        env = _build_typed_env(templates_dir)
        try:
            template = env.get_template("index.html.j2")
        except Exception:
            return "<html><body><h1>SearchV2 Tome Index</h1><p>Templates not found</p></body></html>"

        return template.render(
            documents=docs,
            stats={
                "doc_count": len(docs),
                "entity_count": type_counts.get("entity", 0),
                "skill_count": type_counts.get("skill", 0),
            },
        )

    async def sync_from_db(self, entity_id: int, doc_type: str = "entity"):
        """Re-render Tome document when DB data changes (cache invalidation hook)."""
        from core.knowledge import KnowledgeGraph
        kg = KnowledgeGraph(self.db)
        entity = await kg.get_entity_by_id(entity_id)
        if not entity:
            return

        full = await kg.get_entity_full(entity.name)
        if not full:
            return

        slug = entity.name.lower().replace(" ", "-")
        existing = await self.get_document(slug)
        if not existing:
            return

        entity_data = {
            "entity": full,
            "doc_type": doc_type,
            "last_verified": datetime.now(timezone.utc).isoformat(),
            "confidence": 0.5,
            "decay_rate": 0.001,
        }
        try:
            templates_dir = Path(__file__).parent.parent / "templates"
            env = _build_typed_env(templates_dir)
            template_path = self.DOC_TYPE_TEMPLATES[doc_type]
            template = env.get_template(template_path)
            html = template.render(**entity_data, now=datetime.now)
            existing.source = html
            existing.updated_at = datetime.now(timezone.utc)
            await self.db.flush()
        except Exception:
            pass  # template not found


def _make_embed_fn(vault: TomeVault, depth: int):
    async def embed(slug: str, **extra_vars) -> str:
        doc = await vault.get_document(slug)
        if not doc:
            return f'<div class="embed-error">Not found: {slug}</div>'
        variables = await vault._get_variables(doc.id)
        if extra_vars:
            variables = {**variables, **extra_vars}
        return await vault._render_source(doc.source, variables, depth + 1)
    return embed


def _make_link_fn(vault: TomeVault):
    def link(slug: str, title: str | None = None) -> str:
        display = title or slug
        return f'<a href="/docs/{slug}" class="tome-link">{display}</a>'
    return link


def _build_typed_env(templates_dir: Path) -> ImmutableSandboxedEnvironment:
    """Build Jinja2 env that loads from template directory."""
    try:
        loader = ChoiceLoader([
            FileSystemLoader(str(templates_dir)),
            FileSystemLoader(str(templates_dir / "documents")),
            FileSystemLoader(str(templates_dir / "components")),
        ])
        return ImmutableSandboxedEnvironment(loader=loader, undefined=StrictUndefined)
    except Exception:
        return ImmutableSandboxedEnvironment(undefined=StrictUndefined)


def _parse_html_agent_data(html: str) -> dict:
    """Extract data-* attributes and JSON-LD from rendered HTML."""
    data = {}

    # Extract all data-* attributes from the root article element
    article_match = re.search(r'<article\s+([^>]*?)>', html)
    if article_match:
        attrs = article_match.group(1)
        for m in re.finditer(r'data-([\w-]+)="([^"]*)"', attrs):
            key = m.group(1)
            val = m.group(2)
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass
            data[key] = val

    # Extract <meta> tags
    for m in re.finditer(r'<meta\s+name="([^"]+)"\s+content="([^"]*)"', html):
        data[m.group(1)] = m.group(2)

    # Extract JSON-LD
    ld_match = re.search(
        r'<script\s+type="application/ld\+json">(.*?)</script>',
        html, re.DOTALL
    )
    if ld_match:
        try:
            data["jsonld"] = json.loads(ld_match.group(1))
        except json.JSONDecodeError:
            pass

    # Extract meter values (claims, confidence)
    meters = re.findall(r'<meter\s+value="([^"]*)"', html)
    if meters:
        data["meters"] = [float(v) for v in meters if v]

    # Extract claim rows
    claims = []
    for m in re.finditer(r'class="claim-row"([^>]*)>(.*?)</div>', html, re.DOTALL):
        row_attrs = m.group(1)
        row_content = m.group(2)
        claim = {}
        for cm in re.finditer(r'data-([\w-]+)="([^"]*)"', row_attrs):
            claim[cm.group(1)] = cm.group(2)
        # Extract text content
        texts = re.findall(r'class="claim-(key|value)"[^>]*>(.*?)</span>', row_content)
        for t_type, t_val in texts:
            claim[t_type] = t_val.strip()
        if claim:
            claims.append(claim)
    if claims:
        data["claims"] = claims

    return data
