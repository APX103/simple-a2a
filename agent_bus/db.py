"""SQLAlchemy ORM models for Agent Bus SQLite backend."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    create_engine,
    select,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

Base = declarative_base()

# Many-to-many: groups <-> agents
group_members = Table(
    "group_members",
    Base.metadata,
    Column("group_id", String, ForeignKey("groups.group_id"), primary_key=True),
    Column("agent_id", String, ForeignKey("agents.agent_id"), primary_key=True),
)


class AgentORM(Base):
    __tablename__ = "agents"

    agent_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    capabilities = Column(JSON, default=list)
    limitations = Column(JSON, default=list)
    labels = Column(JSON, default=list)
    announcement = Column(String, default="")
    online = Column(Boolean, default=True)
    registered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    token = Column(String, nullable=False)

    inbox = relationship("MessageORM", back_populates="recipient", foreign_keys="MessageORM.to_agent_id")
    created_groups = relationship("GroupORM", back_populates="creator")


class MessageORM(Base):
    __tablename__ = "messages"

    msg_id = Column(String, primary_key=True)
    msg_type = Column(String, nullable=False)
    from_agent = Column(String, nullable=False)
    to = Column(String, nullable=False)  # agent_id or group_id
    to_agent_id = Column(String, ForeignKey("agents.agent_id"), nullable=True)  # FK when to is an agent
    content_summary = Column(String, default="")
    content_detail = Column(JSON, nullable=True)
    require_human_confirm = Column(Boolean, default=False)
    human_confirmed = Column(Boolean, nullable=True)
    read_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    recipient = relationship("AgentORM", back_populates="inbox", foreign_keys=[to_agent_id])


class GroupORM(Base):
    __tablename__ = "groups"

    group_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    created_by = Column(String, ForeignKey("agents.agent_id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    creator = relationship("AgentORM", back_populates="created_groups")
    members = relationship("AgentORM", secondary=group_members)


# ---------- Engine & Session ----------

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agent_bus.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db_session() -> Session:
    return SessionLocal()
