from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from models.base import async_session
from core.tome import TomeVault

router = APIRouter()


class DocCreate(BaseModel):
    slug: str
    title: str
    source: str
    variables: dict | None = None
    parent_id: int | None = None
    is_template: bool = False
    tags: list | None = None


class DocUpdate(BaseModel):
    title: str | None = None
    source: str | None = None
    tags: list | None = None


class VariablesUpdate(BaseModel):
    variables: dict


class AssetUpload(BaseModel):
    filename: str
    content: str
    mime_type: str = "application/octet-stream"


class LinkCreate(BaseModel):
    from_slug: str
    to_slug: str
    link_type: str = "related"
    context: str = ""


async def get_vault() -> TomeVault:
    async with async_session() as db:
        yield TomeVault(db)


# POST /api/docs
@router.post("/docs")
async def create_doc(body: DocCreate, vault: TomeVault = Depends(get_vault)):
    existing = await vault.get_document(body.slug)
    if existing:
        raise HTTPException(409, f"Document already exists: {body.slug}")
    doc = await vault.create_document(
        slug=body.slug, title=body.title, source=body.source,
        variables=body.variables, parent_id=body.parent_id,
        is_template=body.is_template, tags=body.tags,
    )
    await vault.db.commit()
    return {"slug": doc.slug, "title": doc.title, "created": True}


# GET /api/docs
@router.get("/docs")
async def list_docs(
    tag: str | None = None,
    parent: str | None = None,
    limit: int = 50,
    vault: TomeVault = Depends(get_vault),
):
    docs = await vault.list_documents(tag=tag, parent=parent, limit=limit)
    return docs


# GET /api/docs/search
@router.get("/docs/search")
async def search_docs(q: str, limit: int = 20, vault: TomeVault = Depends(get_vault)):
    return await vault.search_documents(q, limit=limit)


# GET /api/docs/{slug}
@router.get("/docs/{slug}")
async def get_doc(slug: str, vault: TomeVault = Depends(get_vault)):
    doc = await vault.get_document(slug)
    if not doc:
        raise HTTPException(404, f"Document not found: {slug}")
    return {"slug": doc.slug, "title": doc.title, "source": doc.source,
            "tags": doc.tags, "is_template": doc.is_template}


# PATCH /api/docs/{slug}
@router.patch("/docs/{slug}")
async def update_doc(
    slug: str, body: DocUpdate, vault: TomeVault = Depends(get_vault)
):
    updates = body.model_dump(exclude_none=True)
    doc = await vault.update_document(slug, **updates)
    if not doc:
        raise HTTPException(404, f"Document not found: {slug}")
    await vault.db.commit()
    return {"slug": doc.slug, "title": doc.title, "updated": True}


# DELETE /api/docs/{slug}
@router.delete("/docs/{slug}")
async def delete_doc(slug: str, vault: TomeVault = Depends(get_vault)):
    deleted = await vault.delete_document(slug)
    if not deleted:
        raise HTTPException(404, f"Document not found: {slug}")
    await vault.db.commit()
    return {"deleted": True}


# GET /api/docs/{slug}/render
@router.get("/docs/{slug}/render")
async def render_doc(slug: str, vault: TomeVault = Depends(get_vault)):
    result = await vault.render_document(slug)
    await vault.db.commit()
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# GET /api/docs/{slug}/graph
@router.get("/docs/{slug}/graph")
async def get_graph(slug: str, vault: TomeVault = Depends(get_vault)):
    return await vault.get_graph(slug)


# POST /api/docs/{slug}/variables
@router.post("/docs/{slug}/variables")
async def set_variables(
    slug: str, body: VariablesUpdate, vault: TomeVault = Depends(get_vault)
):
    doc = await vault.get_document(slug)
    if not doc:
        raise HTTPException(404, f"Document not found: {slug}")
    await vault.set_variables(slug, body.variables)
    await vault.db.commit()
    return {"updated": True}


# POST /api/docs/{slug}/assets
@router.post("/docs/{slug}/assets")
async def upload_asset(
    slug: str, body: AssetUpload, vault: TomeVault = Depends(get_vault)
):
    asset = await vault.add_asset(
        slug, body.filename, body.content, body.mime_type
    )
    if not asset:
        raise HTTPException(404, f"Document not found: {slug}")
    await vault.db.commit()
    return {"id": asset.id, "filename": asset.filename}


# GET /api/docs/{slug}/assets
@router.get("/docs/{slug}/assets")
async def list_assets(slug: str, vault: TomeVault = Depends(get_vault)):
    return await vault.get_assets(slug)


# POST /api/docs/link
@router.post("/docs/link")
async def link_docs(body: LinkCreate, vault: TomeVault = Depends(get_vault)):
    link = await vault.link_documents(
        body.from_slug, body.to_slug, body.link_type, body.context
    )
    if not link:
        raise HTTPException(404, "Source or target document not found")
    await vault.db.commit()
    return {"linked": True, "type": body.link_type}
