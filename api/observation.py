from fastapi import APIRouter, Depends
from pydantic import BaseModel
from models.base import async_session
from core.observation import ObservationEngine, OpportunityEngine

router = APIRouter()


class ObserveRequest(BaseModel):
    text: str
    source: str = "internal"
    confidence: float = 0.5
    importance: float = 0.5
    category: str = "general"


class OpportunityStatusUpdate(BaseModel):
    status: str


async def get_engines():
    async with async_session() as db:
        yield ObservationEngine(db), OpportunityEngine(db), db


# POST /api/observe
@router.post("/observe")
async def observe(body: ObserveRequest, engines=Depends(get_engines)):
    obs_engine, _, db = engines
    obs = await obs_engine.observe(
        text=body.text, source=body.source,
        confidence=body.confidence, importance=body.importance,
        category=body.category,
    )
    # Auto-identify opportunities
    opps = await obs_engine.identify_opportunities(obs)
    await db.commit()
    return {
        "id": obs.id, "text": obs.observation_text,
        "confidence": obs.confidence, "importance": obs.importance,
        "opportunities_found": len(opps),
    }


# GET /api/observations
@router.get("/observations")
async def list_observations(
    source: str | None = None,
    category: str | None = None,
    min_importance: float = 0.0,
    limit: int = 50,
    engines=Depends(get_engines),
):
    obs_engine, _, _ = engines
    return await obs_engine.list_observations(
        source=source, category=category, min_importance=min_importance, limit=limit
    )


# GET /api/opportunities
@router.get("/opportunities")
async def list_opportunities(
    status: str | None = None,
    min_utility: float = 0.0,
    limit: int = 50,
    engines=Depends(get_engines),
):
    _, opp_engine, _ = engines
    return await opp_engine.list_opportunities(
        status=status, min_utility=min_utility, limit=limit
    )


# PATCH /api/opportunities/{opp_id}
@router.patch("/opportunities/{opp_id}")
async def update_opportunity(
    opp_id: int, body: OpportunityStatusUpdate, engines=Depends(get_engines)
):
    _, opp_engine, db = engines
    ok = await opp_engine.update_status(opp_id, body.status)
    await db.commit()
    if not ok:
        return {"error": "Opportunity not found"}
    return {"updated": True}
