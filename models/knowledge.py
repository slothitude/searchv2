import math
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey, JSON, Boolean
)
from sqlalchemy.orm import relationship
from models.base import Base


class Entity(Base):
    __tablename__ = "entities"

    id = Column(Integer, primary_key=True)
    name = Column(String(512), unique=True, nullable=False, index=True)
    entity_type = Column(String(128), default="thing")  # organization, technology, person, concept, service
    description = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    relationships_from = relationship("Relationship", foreign_keys="Relationship.from_entity_id", back_populates="from_entity")
    relationships_to = relationship("Relationship", foreign_keys="Relationship.to_entity_id", back_populates="to_entity")
    claims = relationship("Claim", back_populates="entity")


class Relationship(Base):
    __tablename__ = "relationships"

    id = Column(Integer, primary_key=True)
    from_entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False)
    to_entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False)
    relation_type = Column(String(128), default="related")  # depends_on, owns, has, located_in, uses, part_of
    confidence = Column(Float, default=0.5)
    evidence_count = Column(Integer, default=1)
    context = Column(Text, default="")

    from_entity = relationship("Entity", foreign_keys=[from_entity_id], back_populates="relationships_from")
    to_entity = relationship("Entity", foreign_keys=[to_entity_id], back_populates="relationships_to")


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id = Column(Integer, primary_key=True)
    goal_id = Column(Integer, nullable=True)
    entity_id = Column(Integer, ForeignKey("entities.id"), nullable=True)
    claim = Column(Text, nullable=False)
    status = Column(String(32), default="active")  # active, confirmed, refuted, superseded
    confidence = Column(Float, default=0.0)
    superseded_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entity = relationship("Entity")
    predictions = relationship("Prediction", back_populates="hypothesis", cascade="all, delete-orphan")
    evidence_items = relationship("Evidence", back_populates="hypothesis", cascade="all, delete-orphan")


class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True)
    hypothesis_id = Column(Integer, ForeignKey("hypotheses.id"), nullable=False)
    prediction_text = Column(Text, nullable=False)
    expected_evidence = Column(Text, default="")
    status = Column(String(32), default="pending")  # pending, confirmed, refuted
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    hypothesis = relationship("Hypothesis", back_populates="predictions")


class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(Integer, primary_key=True)
    hypothesis_id = Column(Integer, ForeignKey("hypotheses.id"), nullable=False)
    prediction_id = Column(Integer, nullable=True)
    direction = Column(String(16), default="supporting")  # supporting, contradicting, neutral
    content = Column(Text, nullable=False)
    source_url = Column(Text, default="")
    confidence_impact = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    hypothesis = relationship("Hypothesis", back_populates="evidence_items")


class Claim(Base):
    __tablename__ = "claims"

    id = Column(Integer, primary_key=True)
    entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False)
    claim_type = Column(String(128), default="fact")  # fact, capability, limitation, status
    claim_key = Column(String(512), nullable=False)
    claim_value = Column(Text, nullable=False)
    confidence = Column(Float, default=0.5)
    evidence_count = Column(Integer, default=1)
    last_verified = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    decay_rate = Column(Float, default=0.001)  # per day
    disputed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    entity = relationship("Entity", back_populates="claims")


class Memory(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    context = Column(Text, default="")
    related_entities = Column(JSON, default=list)
    importance = Column(Float, default=0.5)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(Integer, primary_key=True)
    target_type = Column(String(32), nullable=False)  # entity, claim, document, hypothesis, memory
    target_id = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)  # the text that was embedded
    vector = Column(Text, nullable=False)  # JSON array of floats
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Contradiction(Base):
    __tablename__ = "contradictions"

    id = Column(Integer, primary_key=True)
    entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False)
    claim_a_id = Column(Integer, ForeignKey("claims.id"), nullable=False)
    claim_b_id = Column(Integer, ForeignKey("claims.id"), nullable=False)
    contradiction_type = Column(String(32), default="semantic")  # key_collision, value_conflict, semantic
    severity = Column(Float, default=0.5)
    resolved = Column(Boolean, default=False)
    winner_claim_id = Column(Integer, nullable=True)
    method = Column(String(32), default="")  # evidence, last_verified, confidence, llm_judge
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entity = relationship("Entity")
    claim_a = relationship("Claim", foreign_keys=[claim_a_id])
    claim_b = relationship("Claim", foreign_keys=[claim_b_id])


class PropagationLog(Base):
    __tablename__ = "propagation_log"

    id = Column(Integer, primary_key=True)
    source_entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False)
    target_entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False)
    relationship_id = Column(Integer, nullable=True)
    relation_type = Column(String(128), default="related")
    delta = Column(Float, default=0.0)
    reason = Column(String(256), default="")
    sweep_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    source_entity = relationship("Entity", foreign_keys=[source_entity_id])
    target_entity = relationship("Entity", foreign_keys=[target_entity_id])


class Escalation(Base):
    __tablename__ = "escalations"

    id = Column(Integer, primary_key=True)
    from_tier = Column(String(32), nullable=False)  # reflex, attention, reasoning, action, nuclear
    to_tier = Column(String(32), nullable=False)
    reason = Column(Text, default="")
    approved = Column(Boolean, default=False)
    cost_estimate = Column(Float, default=0.0)
    goal_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
