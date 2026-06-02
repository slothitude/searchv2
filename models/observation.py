from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey, JSON
)
from sqlalchemy.orm import relationship
from models.base import Base


class Observation(Base):
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True)
    observation_text = Column(Text, nullable=False)
    confidence = Column(Float, default=0.5)
    importance = Column(Float, default=0.5)
    source = Column(String(128), default="internal")  # filesystem, rss, git, tome, budget, user
    category = Column(String(128), default="general")
    raw_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    opportunities = relationship("Opportunity", back_populates="observation")


class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True)
    observation_id = Column(Integer, ForeignKey("observations.id"), nullable=True)
    description = Column(Text, nullable=False)
    estimated_value = Column(Float, default=0.0)
    estimated_cost = Column(Float, default=1.0)
    urgency = Column(Float, default=0.5)
    utility = Column(Float, default=0.0)  # value * importance / cost
    status = Column(String(32), default="new")  # new, accepted, deferred, expired
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    observation = relationship("Observation", back_populates="opportunities")
