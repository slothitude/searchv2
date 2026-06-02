from datetime import datetime, timezone
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from models.mission import Mission, Goal, SubGoal, Experiment, UtilityLedger


class MissionPlanner:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_mission(
        self, description: str, priority: float = 0.5, opportunity_id: int | None = None
    ) -> Mission:
        mission = Mission(
            description=description,
            priority=priority,
            opportunity_id=opportunity_id,
        )
        self.db.add(mission)
        await self.db.flush()
        return mission

    async def decompose(self, mission: Mission, goals: list[dict]) -> list[Goal]:
        """2B executive: decompose mission into goals."""
        created = []
        for g in goals:
            goal = Goal(
                mission_id=mission.id,
                description=g.get("description", ""),
                utility_score=g.get("utility", 0.0),
                budget_cents=g.get("budget_cents", 0),
            )
            self.db.add(goal)
            created.append(goal)
        await self.db.flush()
        return created

    async def plan_goal(self, goal: Goal, sub_goals: list[dict]) -> list[SubGoal]:
        """2B executive: break goal into sub-goals."""
        created = []
        for sg in sub_goals:
            sub = SubGoal(
                goal_id=goal.id,
                description=sg.get("description", ""),
                goal_type=sg.get("type", "research"),
                cost_estimate=sg.get("cost_estimate", 1.0),
                info_value=sg.get("info_value", 0.5),
                utility=sg.get("info_value", 0.5) / max(0.1, sg.get("cost_estimate", 1.0)),
                tool_plan=sg.get("tool_plan", {}),
            )
            self.db.add(sub)
            created.append(sub)
        await self.db.flush()
        return created

    async def add_experiment(
        self, sub_goal: SubGoal, description: str, method: str = "",
        hypothesis_id: int | None = None,
    ) -> Experiment:
        exp = Experiment(
            sub_goal_id=sub_goal.id,
            description=description,
            method=method,
            hypothesis_id=hypothesis_id,
        )
        self.db.add(exp)
        await self.db.flush()
        return exp

    async def complete_experiment(
        self, exp_id: int, result: str, status: str = "completed"
    ) -> bool:
        result_row = await self.db.execute(
            select(Experiment).where(Experiment.id == exp_id)
        )
        exp = result_row.scalar_one_or_none()
        if not exp:
            return False
        exp.result = result
        exp.status = status
        await self.db.flush()
        return True

    async def get_mission(self, mission_id: int) -> Mission | None:
        result = await self.db.execute(
            select(Mission).where(Mission.id == mission_id)
        )
        return result.scalar_one_or_none()

    async def get_mission_tree(self, mission_id: int) -> dict | None:
        mission = await self.get_mission(mission_id)
        if not mission:
            return None

        goals_out = []
        total_utility = 0.0
        total_spent = 0

        goals_result = await self.db.execute(
            select(Goal).where(Goal.mission_id == mission_id)
        )
        for goal in goals_result.scalars():
            sg_result = await self.db.execute(
                select(SubGoal).where(SubGoal.goal_id == goal.id)
            )
            sub_goals_out = []
            for sg in sg_result.scalars():
                exp_result = await self.db.execute(
                    select(Experiment).where(Experiment.sub_goal_id == sg.id)
                )
                experiments = [
                    {
                        "id": e.id, "description": e.description,
                        "status": e.status, "result": e.result,
                    }
                    for e in exp_result.scalars()
                ]
                sub_goals_out.append({
                    "id": sg.id, "description": sg.description,
                    "type": sg.goal_type, "utility": sg.utility,
                    "status": sg.status, "experiments": experiments,
                })
            goals_out.append({
                "id": goal.id, "description": goal.description,
                "utility": goal.utility_score, "status": goal.status,
                "spent_cents": goal.spent_cents,
                "sub_goals": sub_goals_out,
            })
            total_utility += goal.utility_score
            total_spent += goal.spent_cents

        return {
            "id": mission.id, "description": mission.description,
            "status": mission.status, "priority": mission.priority,
            "total_utility": total_utility, "total_spent_cents": total_spent,
            "goals": goals_out,
        }

    async def list_missions(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict]:
        q = select(Mission).order_by(desc(Mission.priority)).limit(limit)
        if status:
            q = q.where(Mission.status == status)
        result = await self.db.execute(q)
        return [
            {
                "id": m.id, "description": m.description,
                "status": m.status, "priority": m.priority,
                "created_at": m.created_at.isoformat(),
            }
            for m in result.scalars()
        ]


class UtilityTracker:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self, goal_id: int, operation: str,
        value_estimate: float = 0.0, cost_cents: int = 0,
    ) -> float:
        utility = value_estimate / max(1, cost_cents) if cost_cents > 0 else value_estimate

        entry = UtilityLedger(
            goal_id=goal_id,
            operation=operation,
            value_estimate=value_estimate,
            cost_cents=cost_cents,
            utility_score=utility,
        )
        self.db.add(entry)

        # Update goal spent
        result = await self.db.execute(
            select(Goal).where(Goal.id == goal_id)
        )
        goal = result.scalar_one_or_none()
        if goal:
            goal.spent_cents += cost_cents

        await self.db.flush()
        return utility

    async def get_goal_utility(self, goal_id: int) -> float:
        result = await self.db.execute(
            select(func.sum(UtilityLedger.utility_score)).where(
                UtilityLedger.goal_id == goal_id
            )
        )
        total = result.scalar()
        return float(total or 0.0)
