import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime, ForeignKey,
    JSON, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from models.base import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    title = Column(String(512), nullable=False)
    source = Column(Text, nullable=False)  # HTML+Jinja2 template
    parent_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    is_template = Column(Boolean, default=False)
    tags = Column(JSON, default=list)
    user_id = Column(String(255), default="system")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    parent = relationship("Document", remote_side=[id], backref="children")
    renders = relationship("DocumentRender", back_populates="document",
                          cascade="all, delete-orphan")
    links_from = relationship("DocumentLink", foreign_keys="DocumentLink.from_id",
                             back_populates="from_doc")
    links_to = relationship("DocumentLink", foreign_keys="DocumentLink.to_id",
                            back_populates="to_doc")
    variables = relationship("DocumentVariable", back_populates="document",
                              cascade="all, delete-orphan")
    assets = relationship("DocumentAsset", back_populates="document",
                         cascade="all, delete-orphan")


class DocumentRender(Base):
    __tablename__ = "document_renders"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    render_hash = Column(String(64), nullable=False)
    rendered_html = Column(Text, nullable=False)
    variables_snapshot = Column(JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("Document", back_populates="renders")


class DocumentLink(Base):
    __tablename__ = "document_links"
    __table_args__ = (
        UniqueConstraint("from_id", "to_id", "link_type"),
    )

    id = Column(Integer, primary_key=True)
    from_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    to_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    link_type = Column(String(64), default="related")  # related, embeds, depends_on, supersedes
    context = Column(Text, default="")

    from_doc = relationship("Document", foreign_keys=[from_id],
                            back_populates="links_from")
    to_doc = relationship("Document", foreign_keys=[to_id],
                          back_populates="links_to")


class DocumentVariable(Base):
    __tablename__ = "document_variables"
    __table_args__ = (
        UniqueConstraint("document_id", "key"),
    )

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    key = Column(String(255), nullable=False)
    value = Column(Text, default="")
    value_type = Column(String(64), default="string")  # string, number, boolean, json

    document = relationship("Document", back_populates="variables")


class DocumentAsset(Base):
    __tablename__ = "document_assets"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    filename = Column(String(512), nullable=False)
    content = Column(Text, nullable=False)  # base64 encoded for binary
    mime_type = Column(String(128), default="application/octet-stream")

    document = relationship("Document", back_populates="assets")
