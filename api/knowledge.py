from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from models.base import async_session
from core.knowledge import KnowledgeGraph, MemoryStore
from core.scientist import Scientist

router = APIRouter()


# --- Entity endpoints ---

class EntityCreate(BaseModel):
    name: str
    entity_type: str = "thing"
    description: str = ""


class RelationshipCreate(BaseModel):
    from_name: str
    to_name: str
    relation_type: str = "related"
    confidence: float = 0.5
    context: str = ""


class ClaimCreate(BaseModel):
    entity_name: str
    claim_type: str = "fact"
    claim_key: str
    claim_value: str
    confidence: float = 0.5
    decay_rate: float = 0.001


class MemoryCreate(BaseModel):
    content: str
    context: str = ""
    related_entities: list | None = None
    importance: float = 0.5


class EvidenceCreate(BaseModel):
    content: str
    direction: str = "supporting"
    prediction_id: int | None = None
    source_url: str = ""
    confidence_impact: float = 0.1


class HypothesisCreate(BaseModel):
    claim: str
    entity_name: str | None = None
    goal_id: int | None = None


class PredictionCreate(BaseModel):
    predictions: list[dict]


async def get_kg():
    async with async_session() as db:
        yield KnowledgeGraph(db), MemoryStore(db), Scientist(db), db


# POST /api/entities
@router.post("/entities")
async def create_entity(body: EntityCreate, deps=Depends(get_kg)):
    kg, _, _, db = deps
    entity = await kg.add_entity(body.name, body.entity_type, body.description)
    await db.commit()
    return {"id": entity.id, "name": entity.name, "type": entity.entity_type}


# GET /api/entities/search
@router.get("/entities/search")
async def search_entities(q: str, limit: int = 20, deps=Depends(get_kg)):
    kg, _, _, _ = deps
    return await kg.search_entities(q, limit=limit)


# GET /api/entities/{name}
@router.get("/entities/{name}")
async def get_entity(name: str, deps=Depends(get_kg)):
    kg, _, _, _ = deps
    full = await kg.get_entity_full(name)
    if not full:
        raise HTTPException(404, f"Entity not found: {name}")
    return full


# POST /api/relationships
@router.post("/relationships")
async def create_relationship(body: RelationshipCreate, deps=Depends(get_kg)):
    kg, _, _, db = deps
    rel = await kg.add_relationship(
        body.from_name, body.to_name, body.relation_type,
        body.confidence, body.context,
    )
    await db.commit()
    return {"id": rel.id, "from": body.from_name, "to": body.to_name,
            "type": body.relation_type, "confidence": rel.confidence}


# POST /api/claims
@router.post("/claims")
async def create_claim(body: ClaimCreate, deps=Depends(get_kg)):
    kg, _, _, db = deps
    claim = await kg.add_claim(
        body.entity_name, body.claim_type, body.claim_key,
        body.claim_value, body.confidence, body.decay_rate,
    )
    await db.commit()
    return {"id": claim.id, "entity": body.entity_name,
            "key": body.claim_key, "confidence": claim.confidence}


# GET /api/claims/decay
@router.get("/claims/decay")
async def check_decay(deps=Depends(get_kg)):
    kg, _, _, _ = deps
    return await kg.check_decay()


# --- Hypothesis endpoints ---

# POST /api/hypotheses
@router.post("/hypotheses")
async def create_hypothesis(body: HypothesisCreate, deps=Depends(get_kg)):
    _, _, scientist, db = deps
    hyp = await scientist.generate_hypothesis(
        body.claim, body.entity_name, body.goal_id,
    )
    await db.commit()
    return {"id": hyp.id, "claim": hyp.claim, "confidence": hyp.confidence}


# POST /api/hypotheses/{hyp_id}/predictions
@router.post("/hypotheses/{hyp_id}/predictions")
async def create_predictions(hyp_id: int, body: PredictionCreate, deps=Depends(get_kg)):
    _, _, scientist, db = deps
    preds = await scientist.generate_predictions(hyp_id, body.predictions)
    await db.commit()
    return {"predictions_created": len(preds)}


# POST /api/hypotheses/{hyp_id}/evidence
@router.post("/hypotheses/{hyp_id}/evidence")
async def add_evidence(hyp_id: int, body: EvidenceCreate, deps=Depends(get_kg)):
    _, _, scientist, db = deps
    ev = await scientist.add_evidence(
        hyp_id, body.content, body.direction,
        body.prediction_id, body.source_url, body.confidence_impact,
    )
    await db.commit()
    return {"id": ev.id, "direction": ev.direction}


# GET /api/hypotheses
@router.get("/hypotheses")
async def list_hypotheses(
    status: str | None = None, entity: str | None = None,
    min_confidence: float = 0.0, limit: int = 50, deps=Depends(get_kg),
):
    _, _, scientist, _ = deps
    return await scientist.list_hypotheses(
        status=status, entity_name=entity,
        min_confidence=min_confidence, limit=limit,
    )


# GET /api/hypotheses/{hyp_id}
@router.get("/hypotheses/{hyp_id}")
async def get_hypothesis(hyp_id: int, deps=Depends(get_kg)):
    _, _, scientist, _ = deps
    detail = await scientist.get_hypothesis_detail(hyp_id)
    if not detail:
        raise HTTPException(404, "Hypothesis not found")
    return detail


# GET /api/hypotheses/{hyp_id}/experiments
@router.get("/hypotheses/{hyp_id}/experiments")
async def suggest_experiments(hyp_id: int, deps=Depends(get_kg)):
    _, _, scientist, _ = deps
    return await scientist.suggest_experiments(hyp_id)


# --- Memory endpoints ---

# POST /api/memories
@router.post("/memories")
async def add_memory(body: MemoryCreate, deps=Depends(get_kg)):
    _, ms, _, db = deps
    mem = await ms.add_memory(
        body.content, body.context, body.related_entities, body.importance,
    )
    await db.commit()
    return {"id": mem.id}


# GET /api/memories/recall
@router.get("/memories/recall")
async def recall_memories(entity: str, limit: int = 10, deps=Depends(get_kg)):
    _, ms, _, _ = deps
    return await ms.recall(entity, limit=limit)


# GET /api/memories/search
@router.get("/memories/search")
async def search_memories(q: str, limit: int = 20, deps=Depends(get_kg)):
    _, ms, _, _ = deps
    return await ms.search_memories(q, limit=limit)
