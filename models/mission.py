from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey, JSON, Boolean
)
from sqlalchemy.orm import relationship
from models.base import Base


class Mission(Base):
    __tablename__ = "missions"

    id = Column(Integer, primary_key=True)
    description = Column(Text, nullable=False)
    status = Column(String(32), default="active")  # active, paused, completed, cancelled
    priority = Column(Float, default=0.5)
    opportunity_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    goals = relationship("Goal", back_populates="mission", cascade="all, delete-orphan")


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True)
    mission_id = Column(Integer, ForeignKey("missions.id"), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(32), default="pending")  # pending, active, completed, blocked
    utility_score = Column(Float, default=0.0)
    budget_cents = Column(Integer, default=0)
    spent_cents = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    mission = relationship("Mission", back_populates="goals")
    sub_goals = relationship("SubGoal", back_populates="goal", cascade="all, delete-orphan")


class SubGoal(Base):
    __tablename__ = "sub_goals"

    id = Column(Integer, primary_key=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False)
    description = Column(Text, nullable=False)
    goal_type = Column(String(64), default="research")  # research, verify, build, test
    cost_estimate = Column(Float, default=1.0)
    info_value = Column(Float, default=0.5)
    utility = Column(Float, default=0.0)
    tool_plan = Column(JSON, default=dict)
    result_summary = Column(Text, default="")
    status = Column(String(32), default="pending")  # pending, active, completed, blocked
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    goal = relationship("Goal", back_populates="sub_goals")
    experiments = relationship("Experiment", back_populates="sub_goal",
                              cascade="all, delete-orphan")


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(Integer, primary_key=True)
    sub_goal_id = Column(Integer, ForeignKey("sub_goals.id"), nullable=False)
    description = Column(Text, nullable=False)
    hypothesis_id = Column(Integer, nullable=True)
    method = Column(Text, default="")
    result = Column(Text, default="")
    status = Column(String(32), default="pending")  # pending, running, completed, failed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    sub_goal = relationship("SubGoal", back_populates="experiments")


class UtilityLedger(Base):
    __tablename__ = "utility_ledger"

    id = Column(Integer, primary_key=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False)
    operation = Column(String(128), nullable=False)
    value_estimate = Column(Float, default=0.0)
    cost_cents = Column(Integer, default=0)
    utility_score = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
