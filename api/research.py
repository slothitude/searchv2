from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from models.base import async_session
from core.search import search_searxng
from core.extractor import fetch_and_extract
from core.router import Router, Tier

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    categories: str = "general"
    max_results: int = 10


class ExtractRequest(BaseModel):
    url: str


class ClassifyRequest(BaseModel):
    text: str


class PlanRequest(BaseModel):
    goal: str
    context: str = ""


class JudgeRequest(BaseModel):
    claim: str
    evidence: str


class SynthesizeRequest(BaseModel):
    claims: list[dict]
    topic: str


# POST /api/search
@router.post("/search")
async def search(body: SearchRequest):
    results = await search_searxng(
        body.query, body.categories, body.max_results
    )
    return {"results": results, "count": len(results)}


# POST /api/extract
@router.post("/extract")
async def extract(body: ExtractRequest):
    result = await fetch_and_extract(body.url)
    if "error" in result:
        raise HTTPException(502, result["error"])
    return result


# POST /api/classify
@router.post("/classify")
async def classify(body: ClassifyRequest):
    router_inst = Router()
    return await router_inst.classify(body.text)


# POST /api/plan
@router.post("/plan")
async def plan(body: PlanRequest):
    router_inst = Router()
    return await router_inst.plan(body.goal, body.context)


# POST /api/judge
@router.post("/judge")
async def judge(body: JudgeRequest):
    router_inst = Router()
    return await router_inst.judge(body.claim, body.evidence)


# POST /api/synthesize
@router.post("/synthesize")
async def synthesize(body: SynthesizeRequest):
    router_inst = Router()
    text = await router_inst.synthesize(body.claims, body.topic)
    return {"text": text}
