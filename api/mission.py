from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from models.base import async_session
from core.mission_planner import MissionPlanner, UtilityTracker

router = APIRouter()


class MissionCreate(BaseModel):
    description: str
    priority: float = 0.5
    opportunity_id: int | None = None


class GoalDecompose(BaseModel):
    goals: list[dict]


class SubGoalPlan(BaseModel):
    sub_goals: list[dict]


class ExperimentCreate(BaseModel):
    description: str
    method: str = ""
    hypothesis_id: int | None = None


class ExperimentComplete(BaseModel):
    result: str
    status: str = "completed"


async def get_planner():
    async with async_session() as db:
        yield MissionPlanner(db), UtilityTracker(db), db


# POST /api/missions
@router.post("/missions")
async def create_mission(body: MissionCreate, deps=Depends(get_planner)):
    planner, _, db = deps
    mission = await planner.create_mission(
        body.description, body.priority, body.opportunity_id
    )
    await db.commit()
    return {"id": mission.id, "description": mission.description}


# GET /api/missions
@router.get("/missions")
async def list_missions(
    status: str | None = None, limit: int = 50, deps=Depends(get_planner)
):
    planner, _, _ = deps
    return await planner.list_missions(status=status, limit=limit)


# GET /api/missions/{mission_id}
@router.get("/missions/{mission_id}")
async def get_mission(mission_id: int, deps=Depends(get_planner)):
    planner, _, _ = deps
    tree = await planner.get_mission_tree(mission_id)
    if not tree:
        raise HTTPException(404, "Mission not found")
    return tree


# POST /api/missions/{mission_id}/decompose
@router.post("/missions/{mission_id}/decompose")
async def decompose_mission(
    mission_id: int, body: GoalDecompose, deps=Depends(get_planner)
):
    planner, _, db = deps
    mission = await planner.get_mission(mission_id)
    if not mission:
        raise HTTPException(404, "Mission not found")
    goals = await planner.decompose(mission, body.goals)
    await db.commit()
    return {"goals_created": len(goals)}


# POST /api/goals/{goal_id}/plan
@router.post("/goals/{goal_id}/plan")
async def plan_goal(
    goal_id: int, body: SubGoalPlan, deps=Depends(get_planner)
):
    planner, _, db = deps
    from models.mission import Goal
    from sqlalchemy import select
    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(404, "Goal not found")
    sub_goals = await planner.plan_goal(goal, body.sub_goals)
    await db.commit()
    return {"sub_goals_created": len(sub_goals)}


# POST /api/subgoals/{sg_id}/experiments
@router.post("/subgoals/{sg_id}/experiments")
async def add_experiment(
    sg_id: int, body: ExperimentCreate, deps=Depends(get_planner)
):
    planner, _, db = deps
    from models.mission import SubGoal
    from sqlalchemy import select
    result = await db.execute(select(SubGoal).where(SubGoal.id == sg_id))
    sg = result.scalar_one_or_none()
    if not sg:
        raise HTTPException(404, "SubGoal not found")
    exp = await planner.add_experiment(sg, body.description, body.method, body.hypothesis_id)
    await db.commit()
    return {"id": exp.id, "status": exp.status}


# POST /api/experiments/{exp_id}/complete
@router.post("/experiments/{exp_id}/complete")
async def complete_experiment(
    exp_id: int, body: ExperimentComplete, deps=Depends(get_planner)
):
    planner, _, db = deps
    ok = await planner.complete_experiment(exp_id, body.result, body.status)
    await db.commit()
    if not ok:
        raise HTTPException(404, "Experiment not found")
    return {"completed": True}
