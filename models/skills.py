from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey, JSON
)
from sqlalchemy.orm import relationship
from models.base import Base


class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, default="")
    domain = Column(String(128), default="general")
    steps = Column(JSON, default=list)  # ordered list of step dicts
    preconditions = Column(JSON, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    usage_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)

    executions = relationship("SkillExecution", back_populates="skill",
                             cascade="all, delete-orphan")

    @property
    def success_rate(self) -> float:
        if self.usage_count == 0:
            return 0.0
        return self.success_count / self.usage_count


class SkillExecution(Base):
    __tablename__ = "skill_executions"

    id = Column(Integer, primary_key=True)
    skill_id = Column(Integer, ForeignKey("skills.id"), nullable=False)
    goal_id = Column(Integer, nullable=True)
    inputs = Column(JSON, default=dict)
    outputs = Column(JSON, default=dict)
    success = Column(Boolean, default=False)
    duration_seconds = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    skill = relationship("Skill", back_populates="executions")
