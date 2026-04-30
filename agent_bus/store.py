"""Store backends for Agent Bus — memory (default) or Redis."""
from __future__ import annotations

import os
import secrets
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional

from agent_bus.models import AgentCard, Group, Message, MessageContent


class BaseStore(ABC):
    """Abstract store interface."""

    @abstractmethod
    def register_agent(
        self, name: str, capabilities: List[str], limitations: List[str], announcement: str, labels: List[str] = None
    ) -> tuple[str, str, AgentCard]:
        ...

    @abstractmethod
    def get_agent(self, agent_id: str) -> Optional[AgentCard]:
        ...

    @abstractmethod
    def list_agents(self, label: Optional[str] = None) -> List[AgentCard]:
        ...

    @abstractmethod
    def verify_token(self, agent_id: str, token: str) -> bool:
        ...

    @abstractmethod
    def agent_id_from_token(self, token: str) -> Optional[str]:
        ...

    @abstractmethod
    def unregister_agent(self, agent_id: str) -> bool:
        ...

    @abstractmethod
    def touch_agent(self, agent_id: str) -> None:
        ...

    @abstractmethod
    def add_message(self, msg: Message) -> None:
        ...

    @abstractmethod
    def get_inbox(self, agent_id: str, since: Optional[datetime] = None) -> List[Message]:
        ...

    @abstractmethod
    def get_message(self, agent_id: str, msg_id: str) -> Optional[Message]:
        ...

    @abstractmethod
    def create_group(self, name: str, created_by: str) -> Group:
        ...

    @abstractmethod
    def get_group(self, group_id: str) -> Optional[Group]:
        ...

    @abstractmethod
    def list_groups(self) -> List[Group]:
        ...

    @abstractmethod
    def join_group(self, group_id: str, agent_id: str) -> bool:
        ...

    @abstractmethod
    def leave_group(self, group_id: str, agent_id: str) -> bool:
        ...


class MemoryStore(BaseStore):
    """Thread-safe in-memory store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agents: Dict[str, AgentCard] = {}
        self._tokens: Dict[str, str] = {}
        self._messages: Dict[str, List[Message]] = {}
        self._groups: Dict[str, Group] = {}
        self._counter = 0

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        ts = int(time.time())
        rand = secrets.token_hex(4)
        return f"{prefix}_{ts}_{rand}_{self._counter}"

    def register_agent(
        self, name: str, capabilities: List[str], limitations: List[str], announcement: str, labels: List[str] = None
    ) -> tuple[str, str, AgentCard]:
        with self._lock:
            agent_id = self._next_id("agent")
            token = secrets.token_urlsafe(32)
            card = AgentCard(
                agent_id=agent_id,
                name=name,
                capabilities=capabilities,
                limitations=limitations,
                announcement=announcement,
                labels=labels or [],
            )
            self._agents[agent_id] = card
            self._tokens[agent_id] = token
            self._messages[agent_id] = []
            return agent_id, token, card

    def get_agent(self, agent_id: str) -> Optional[AgentCard]:
        with self._lock:
            return self._agents.get(agent_id)

    def list_agents(self, label: Optional[str] = None) -> List[AgentCard]:
        with self._lock:
            agents = list(self._agents.values())
            if label:
                agents = [a for a in agents if label in a.labels]
            return agents

    def verify_token(self, agent_id: str, token: str) -> bool:
        with self._lock:
            return self._tokens.get(agent_id) == token

    def agent_id_from_token(self, token: str) -> Optional[str]:
        with self._lock:
            for aid, tok in self._tokens.items():
                if tok == token:
                    return aid
            return None

    def unregister_agent(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id in self._agents:
                del self._agents[agent_id]
                del self._tokens[agent_id]
                del self._messages[agent_id]
                for group in self._groups.values():
                    if agent_id in group.members:
                        group.members.remove(agent_id)
                return True
            return False

    def touch_agent(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].last_seen = datetime.now(timezone.utc)

    def add_message(self, msg: Message) -> None:
        with self._lock:
            if msg.to.startswith("agent_"):
                if msg.to in self._messages:
                    self._messages[msg.to].append(msg)
            elif msg.to.startswith("group_"):
                group = self._groups.get(msg.to)
                if group:
                    for member_id in group.members:
                        if member_id in self._messages:
                            self._messages[member_id].append(msg)

    def get_inbox(self, agent_id: str, since: Optional[datetime] = None) -> List[Message]:
        with self._lock:
            msgs = self._messages.get(agent_id, [])
            if since is None:
                return list(msgs)
            return [m for m in msgs if m.timestamp > since]

    def get_message(self, agent_id: str, msg_id: str) -> Optional[Message]:
        with self._lock:
            for m in self._messages.get(agent_id, []):
                if m.msg_id == msg_id:
                    return m
            return None

    def create_group(self, name: str, created_by: str) -> Group:
        with self._lock:
            group_id = self._next_id("group")
            group = Group(group_id=group_id, name=name, created_by=created_by, members=[created_by])
            self._groups[group_id] = group
            return group

    def get_group(self, group_id: str) -> Optional[Group]:
        with self._lock:
            return self._groups.get(group_id)

    def list_groups(self) -> List[Group]:
        with self._lock:
            return list(self._groups.values())

    def join_group(self, group_id: str, agent_id: str) -> bool:
        with self._lock:
            group = self._groups.get(group_id)
            if group and agent_id not in group.members:
                group.members.append(agent_id)
                return True
            return False

    def leave_group(self, group_id: str, agent_id: str) -> bool:
        with self._lock:
            group = self._groups.get(group_id)
            if group and agent_id in group.members:
                group.members.remove(agent_id)
                return True
            return False


class RedisStore(BaseStore):
    """Redis-backed store for multi-instance deployments."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis

        self._client = redis.from_url(redis_url, decode_responses=True)
        self._prefix = "agent_bus"

    def _key(self, *parts: str) -> str:
        return ":".join([self._prefix, *parts])

    def _next_id(self, prefix: str) -> str:
        ts = int(time.time())
        rand = secrets.token_hex(4)
        counter = self._client.incr(self._key("counter"))
        return f"{prefix}_{ts}_{rand}_{counter}"

    def register_agent(
        self, name: str, capabilities: List[str], limitations: List[str], announcement: str, labels: List[str] = None
    ) -> tuple[str, str, AgentCard]:
        agent_id = self._next_id("agent")
        token = secrets.token_urlsafe(32)
        card = AgentCard(
            agent_id=agent_id,
            name=name,
            capabilities=capabilities,
            limitations=limitations,
            announcement=announcement,
            labels=labels or [],
        )
        pipe = self._client.pipeline()
        pipe.hset(self._key("agents"), agent_id, card.model_dump_json())
        pipe.hset(self._key("tokens"), agent_id, token)
        pipe.rpush(self._key("inbox", agent_id), "[]")  # ensure key exists
        pipe.execute()
        return agent_id, token, card

    def get_agent(self, agent_id: str) -> Optional[AgentCard]:
        raw = self._client.hget(self._key("agents"), agent_id)
        if raw is None:
            return None
        return AgentCard.model_validate_json(raw)

    def list_agents(self, label: Optional[str] = None) -> List[AgentCard]:
        raw_map = self._client.hgetall(self._key("agents"))
        agents = [AgentCard.model_validate_json(v) for v in raw_map.values()]
        if label:
            agents = [a for a in agents if label in a.labels]
        return agents

    def verify_token(self, agent_id: str, token: str) -> bool:
        return self._client.hget(self._key("tokens"), agent_id) == token

    def agent_id_from_token(self, token: str) -> Optional[str]:
        mapping = self._client.hgetall(self._key("tokens"))
        for aid, tok in mapping.items():
            if tok == token:
                return aid
        return None

    def unregister_agent(self, agent_id: str) -> bool:
        if not self._client.hexists(self._key("agents"), agent_id):
            return False
        pipe = self._client.pipeline()
        pipe.hdel(self._key("agents"), agent_id)
        pipe.hdel(self._key("tokens"), agent_id)
        pipe.delete(self._key("inbox", agent_id))
        pipe.execute()
        # remove from all groups
        for group in self.list_groups():
            if agent_id in group.members:
                group.members.remove(agent_id)
                self._client.hset(self._key("groups"), group.group_id, group.model_dump_json())
        return True

    def touch_agent(self, agent_id: str) -> None:
        raw = self._client.hget(self._key("agents"), agent_id)
        if raw:
            card = AgentCard.model_validate_json(raw)
            card.last_seen = datetime.now(timezone.utc)
            self._client.hset(self._key("agents"), agent_id, card.model_dump_json())

    def add_message(self, msg: Message) -> None:
        if msg.to.startswith("agent_"):
            self._client.rpush(self._key("inbox", msg.to), msg.model_dump_json())
        elif msg.to.startswith("group_"):
            group = self.get_group(msg.to)
            if group:
                for member_id in group.members:
                    self._client.rpush(self._key("inbox", member_id), msg.model_dump_json())

    def get_inbox(self, agent_id: str, since: Optional[datetime] = None) -> List[Message]:
        raw_list = self._client.lrange(self._key("inbox", agent_id), 0, -1)
        # filter out the placeholder "[]" if present
        msgs = [Message.model_validate_json(r) for r in raw_list if r != "[]"]
        if since is None:
            return msgs
        return [m for m in msgs if m.timestamp > since]

    def get_message(self, agent_id: str, msg_id: str) -> Optional[Message]:
        for m in self.get_inbox(agent_id):
            if m.msg_id == msg_id:
                return m
        return None

    def create_group(self, name: str, created_by: str) -> Group:
        group_id = self._next_id("group")
        group = Group(group_id=group_id, name=name, created_by=created_by, members=[created_by])
        self._client.hset(self._key("groups"), group_id, group.model_dump_json())
        return group

    def get_group(self, group_id: str) -> Optional[Group]:
        raw = self._client.hget(self._key("groups"), group_id)
        if raw is None:
            return None
        return Group.model_validate_json(raw)

    def list_groups(self) -> List[Group]:
        raw_map = self._client.hgetall(self._key("groups"))
        return [Group.model_validate_json(v) for v in raw_map.values()]

    def join_group(self, group_id: str, agent_id: str) -> bool:
        raw = self._client.hget(self._key("groups"), group_id)
        if raw is None:
            return False
        group = Group.model_validate_json(raw)
        if agent_id in group.members:
            return False
        group.members.append(agent_id)
        self._client.hset(self._key("groups"), group_id, group.model_dump_json())
        return True

    def leave_group(self, group_id: str, agent_id: str) -> bool:
        raw = self._client.hget(self._key("groups"), group_id)
        if raw is None:
            return False
        group = Group.model_validate_json(raw)
        if agent_id not in group.members:
            return False
        group.members.remove(agent_id)
        self._client.hset(self._key("groups"), group_id, group.model_dump_json())
        return True


class SQLiteStore(BaseStore):
    """SQLite-backed store via SQLAlchemy."""

    def __init__(self, db_session_factory) -> None:
        self._session_factory = db_session_factory

    def _sess(self) -> "Session":
        from sqlalchemy.orm import Session
        return self._session_factory()

    @staticmethod
    def _to_card(agent) -> AgentCard:
        return AgentCard(
            agent_id=agent.agent_id,
            name=agent.name,
            capabilities=agent.capabilities or [],
            limitations=agent.limitations or [],
            labels=agent.labels or [],
            announcement=agent.announcement or "",
            online=agent.online,
            registered_at=agent.registered_at,
            last_seen=agent.last_seen,
        )

    @staticmethod
    def _json_safe(obj):
        import json
        if obj is None:
            return None
        return json.loads(json.dumps(obj, default=str))

    @staticmethod
    def _to_msg(msg) -> Message:
        return Message(
            msg_id=msg.msg_id,
            msg_type=msg.msg_type,
            from_agent=msg.from_agent,
            to=msg.to,
            content=MessageContent(summary=msg.content_summary or "", detail=msg.content_detail),
            require_human_confirm=msg.require_human_confirm,
            human_confirmed=msg.human_confirmed,
            read_at=msg.read_at,
            delivered_at=msg.delivered_at,
            timestamp=msg.timestamp,
        )

    @staticmethod
    def _to_group(group) -> Group:
        return Group(
            group_id=group.group_id,
            name=group.name,
            members=[m.agent_id for m in group.members],
            created_by=group.created_by,
            created_at=group.created_at,
        )

    def register_agent(
        self, name: str, capabilities: List[str], limitations: List[str], announcement: str, labels: List[str] = None
    ) -> tuple[str, str, AgentCard]:
        from agent_bus.db import AgentORM

        agent_id = f"agent_{int(time.time())}_{secrets.token_hex(4)}"
        token = secrets.token_urlsafe(32)
        with self._sess() as sess:
            agent = AgentORM(
                agent_id=agent_id,
                name=name,
                capabilities=capabilities or [],
                limitations=limitations or [],
                labels=labels or [],
                announcement=announcement,
                token=token,
            )
            sess.add(agent)
            sess.commit()
            return agent_id, token, self._to_card(agent)

    def get_agent(self, agent_id: str) -> Optional[AgentCard]:
        from agent_bus.db import AgentORM

        with self._sess() as sess:
            agent = sess.get(AgentORM, agent_id)
            return self._to_card(agent) if agent else None

    def list_agents(self, label: Optional[str] = None) -> List[AgentCard]:
        from agent_bus.db import AgentORM
        from sqlalchemy import select

        with self._sess() as sess:
            stmt = select(AgentORM)
            agents = sess.execute(stmt).scalars().all()
            result = [self._to_card(a) for a in agents]
            if label:
                result = [a for a in result if label in a.labels]
            return result

    def verify_token(self, agent_id: str, token: str) -> bool:
        from agent_bus.db import AgentORM

        with self._sess() as sess:
            agent = sess.get(AgentORM, agent_id)
            return agent is not None and agent.token == token

    def agent_id_from_token(self, token: str) -> Optional[str]:
        from agent_bus.db import AgentORM
        from sqlalchemy import select

        with self._sess() as sess:
            stmt = select(AgentORM).where(AgentORM.token == token)
            agent = sess.execute(stmt).scalar_one_or_none()
            return agent.agent_id if agent else None

    def unregister_agent(self, agent_id: str) -> bool:
        from agent_bus.db import AgentORM, MessageORM, GroupORM

        with self._sess() as sess:
            agent = sess.get(AgentORM, agent_id)
            if not agent:
                return False
            # remove from groups
            for group in sess.query(GroupORM).all():
                group.members = [m for m in group.members if m.agent_id != agent_id]
            # delete messages
            sess.query(MessageORM).filter(
                (MessageORM.to_agent_id == agent_id) | (MessageORM.from_agent == agent_id)
            ).delete(synchronize_session=False)
            sess.delete(agent)
            sess.commit()
            return True

    def touch_agent(self, agent_id: str) -> None:
        from agent_bus.db import AgentORM

        with self._sess() as sess:
            agent = sess.get(AgentORM, agent_id)
            if agent:
                agent.last_seen = datetime.now(timezone.utc)
                sess.commit()

    def add_message(self, msg: Message) -> None:
        from agent_bus.db import AgentORM, MessageORM, GroupORM
        from sqlalchemy import select

        with self._sess() as sess:
            delivered = datetime.now(timezone.utc)
            if msg.to.startswith("agent_"):
                agent = sess.get(AgentORM, msg.to)
                if agent:
                    m = MessageORM(
                        msg_id=msg.msg_id,
                        msg_type=msg.msg_type,
                        from_agent=msg.from_agent,
                        to=msg.to,
                        to_agent_id=msg.to,
                        content_summary=msg.content.summary,
                        content_detail=self._json_safe(msg.content.detail),
                        require_human_confirm=msg.require_human_confirm,
                        human_confirmed=msg.human_confirmed,
                        delivered_at=delivered,
                        timestamp=msg.timestamp,
                    )
                    sess.add(m)
                    sess.commit()
            elif msg.to.startswith("group_"):
                group = sess.get(GroupORM, msg.to)
                if group:
                    for member in group.members:
                        m = MessageORM(
                            msg_id=f"{msg.msg_id}_{member.agent_id}",
                            msg_type=msg.msg_type,
                            from_agent=msg.from_agent,
                            to=msg.to,
                            to_agent_id=member.agent_id,
                            content_summary=msg.content.summary,
                            content_detail=self._json_safe(msg.content.detail),
                            require_human_confirm=msg.require_human_confirm,
                            human_confirmed=msg.human_confirmed,
                            delivered_at=delivered,
                            timestamp=msg.timestamp,
                        )
                        sess.add(m)
                    sess.commit()

    def get_inbox(self, agent_id: str, since: Optional[datetime] = None, unread_only: bool = False) -> List[Message]:
        from agent_bus.db import MessageORM
        from sqlalchemy import select, and_

        with self._sess() as sess:
            stmt = select(MessageORM).where(MessageORM.to_agent_id == agent_id)
            if since is not None:
                stmt = stmt.where(MessageORM.timestamp > since)
            if unread_only:
                stmt = stmt.where(MessageORM.read_at.is_(None))
            stmt = stmt.order_by(MessageORM.timestamp)
            msgs = sess.execute(stmt).scalars().all()
            return [self._to_msg(m) for m in msgs]

    def get_message(self, agent_id: str, msg_id: str) -> Optional[Message]:
        from agent_bus.db import MessageORM

        with self._sess() as sess:
            # For group messages, the stored msg_id may have _{agent_id} suffix
            m = sess.get(MessageORM, msg_id)
            if m and m.to_agent_id == agent_id:
                return self._to_msg(m)
            # Try the suffixed version
            m2 = sess.get(MessageORM, f"{msg_id}_{agent_id}")
            if m2 and m2.to_agent_id == agent_id:
                return self._to_msg(m2)
            return None

    def mark_read(self, agent_id: str, msg_id: str) -> bool:
        from agent_bus.db import MessageORM

        with self._sess() as sess:
            m = sess.get(MessageORM, msg_id)
            if m and m.to_agent_id == agent_id:
                m.read_at = datetime.now(timezone.utc)
                sess.commit()
                return True
            return False

    def mark_all_read(self, agent_id: str) -> int:
        from agent_bus.db import MessageORM
        from sqlalchemy import update

        with self._sess() as sess:
            now = datetime.now(timezone.utc)
            result = sess.execute(
                update(MessageORM)
                .where(
                    MessageORM.to_agent_id == agent_id,
                    MessageORM.read_at.is_(None),
                )
                .values(read_at=now)
            )
            sess.commit()
            return result.rowcount

    def create_group(self, name: str, created_by: str) -> Group:
        from agent_bus.db import AgentORM, GroupORM

        group_id = f"group_{int(time.time())}_{secrets.token_hex(4)}"
        with self._sess() as sess:
            creator = sess.get(AgentORM, created_by)
            group = GroupORM(group_id=group_id, name=name, created_by=created_by)
            if creator:
                group.members.append(creator)
            sess.add(group)
            sess.commit()
            sess.refresh(group)
            return self._to_group(group)

    def get_group(self, group_id: str) -> Optional[Group]:
        from agent_bus.db import GroupORM

        with self._sess() as sess:
            group = sess.get(GroupORM, group_id)
            return self._to_group(group) if group else None

    def list_groups(self) -> List[Group]:
        from agent_bus.db import GroupORM
        from sqlalchemy import select

        with self._sess() as sess:
            stmt = select(GroupORM)
            groups = sess.execute(stmt).scalars().all()
            return [self._to_group(g) for g in groups]

    def join_group(self, group_id: str, agent_id: str) -> bool:
        from agent_bus.db import AgentORM, GroupORM

        with self._sess() as sess:
            group = sess.get(GroupORM, group_id)
            agent = sess.get(AgentORM, agent_id)
            if not group or not agent:
                return False
            if agent in group.members:
                return False
            group.members.append(agent)
            sess.commit()
            return True

    def leave_group(self, group_id: str, agent_id: str) -> bool:
        from agent_bus.db import AgentORM, GroupORM

        with self._sess() as sess:
            group = sess.get(GroupORM, group_id)
            agent = sess.get(AgentORM, agent_id)
            if not group or not agent:
                return False
            if agent not in group.members:
                return False
            group.members.remove(agent)
            sess.commit()
            return True


# Admin helpers (not part of BaseStore)
    def admin_list_messages(self, from_agent: Optional[str] = None, to: Optional[str] = None,
                            since: Optional[datetime] = None, msg_type: Optional[str] = None) -> List[Message]:
        from agent_bus.db import MessageORM
        from sqlalchemy import select

        with self._sess() as sess:
            stmt = select(MessageORM)
            if from_agent:
                stmt = stmt.where(MessageORM.from_agent == from_agent)
            if to:
                stmt = stmt.where(MessageORM.to == to)
            if since:
                stmt = stmt.where(MessageORM.timestamp > since)
            if msg_type:
                stmt = stmt.where(MessageORM.msg_type == msg_type)
            stmt = stmt.order_by(MessageORM.timestamp.desc())
            msgs = sess.execute(stmt).scalars().all()
            return [self._to_msg(m) for m in msgs]

    def admin_get_stats(self) -> dict:
        from agent_bus.db import AgentORM, MessageORM
        from sqlalchemy import func, select

        with self._sess() as sess:
            total_agents = sess.execute(select(func.count()).select_from(AgentORM)).scalar()
            online_agents = sess.execute(
                select(func.count()).select_from(AgentORM).where(AgentORM.online == True)
            ).scalar()
            total_messages = sess.execute(select(func.count()).select_from(MessageORM)).scalar()
            unread_messages = sess.execute(
                select(func.count()).select_from(MessageORM).where(MessageORM.read_at.is_(None))
            ).scalar()
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            messages_today = sess.execute(
                select(func.count()).select_from(MessageORM).where(MessageORM.timestamp >= today)
            ).scalar()
            # avg read latency in ms for messages that have been read
            avg_latency = sess.execute(
                select(func.avg(func.julianday(MessageORM.read_at) - func.julianday(MessageORM.delivered_at)) * 86400 * 1000)
                .where(MessageORM.read_at.isnot(None))
            ).scalar()
            return {
                "total_agents": total_agents or 0,
                "online_agents": online_agents or 0,
                "total_messages": total_messages or 0,
                "unread_messages": unread_messages or 0,
                "messages_today": messages_today or 0,
                "avg_read_latency_ms": round(avg_latency, 2) if avg_latency else 0,
            }

    def admin_update_agent_labels(self, agent_id: str, labels: List[str]) -> bool:
        from agent_bus.db import AgentORM

        with self._sess() as sess:
            agent = sess.get(AgentORM, agent_id)
            if not agent:
                return False
            agent.labels = labels
            sess.commit()
            return True


def get_store() -> BaseStore:
    """Factory: SQLiteStore if DATABASE_URL is set, RedisStore if REDIS_URL is set, otherwise MemoryStore."""
    from agent_bus.db import SessionLocal, create_tables

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        create_tables()
        return SQLiteStore(SessionLocal)
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return RedisStore(redis_url)
    return MemoryStore()


store = get_store()
