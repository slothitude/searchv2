from fastapi import APIRouter, Depends
from pydantic import BaseModel
from models.base import async_session
from core.retriever import ContextRetriever

router = APIRouter()


class RetrieveRequest(BaseModel):
    query: str
    limit: int = 20
    use_semantic: bool = True


class IndexRequest(BaseModel):
    target_type: str = "all"  # all, entity, claims
    target_id: int | None = None


async def get_retriever():
    async with async_session() as db:
        yield ContextRetriever(db), db


@router.post("/retrieve")
async def retrieve(body: RetrieveRequest, deps=Depends(get_retriever)):
    retriever, _ = deps
    result = await retriever.retrieve(
        query=body.query,
        limit=body.limit,
        use_semantic=body.use_semantic,
    )
    return result


@router.post("/index")
async def index_embeddings(body: IndexRequest, deps=Depends(get_retriever)):
    """Generate embeddings for entities/claims. Runs on demand."""
    retriever, db = deps
    if body.target_type == "all":
        counts = await retriever.index_all()
        await db.commit()
        return {"indexed": counts}
    elif body.target_type == "entity" and body.target_id:
        await retriever.index_entity(body.target_id)
        await retriever.index_claims_for_entity(body.target_id)
        await db.commit()
        return {"indexed": {"entity": 1}}
    return {"error": "Provide target_type=all or target_type=entity with target_id"}


@router.get("/retrieve/status")
async def retrieval_status(deps=Depends(get_retriever)):
    """Check how many items are indexed with embeddings."""
    from sqlalchemy import func, select
    from models.knowledge import Embedding

    retriever, _ = deps
    total = await retriever.db.execute(select(func.count()).select_from(Embedding))
    by_type = await retriever.db.execute(
        select(Embedding.target_type, func.count())
        .group_by(Embedding.target_type)
    )
    return {
        "total_embeddings": total.scalar(),
        "by_type": {row[0]: row[1] for row in by_type.all()},
    }


class IngestRequest(BaseModel):
    query: str
    max_urls: int = 3
    max_results: int = 10
    classify: bool = True
    index_embeddings: bool = True


@router.post("/ingest")
async def ingest(body: IngestRequest):
    """Search→Extract→Classify→Store→Index pipeline."""
    from core.ingest import ingest
    async with async_session() as db:
        result = await ingest(
            db=db,
            query=body.query,
            max_urls=body.max_urls,
            max_results=body.max_results,
            classify=body.classify,
            index_embeddings=body.index_embeddings,
        )
        return result
