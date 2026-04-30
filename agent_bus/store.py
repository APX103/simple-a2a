"""Store backends for Agent Bus — memory (default) or Redis."""
from __future__ import annotations

import os
import secrets
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional

from agent_bus.models import AgentCard, Group, Message


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


def get_store() -> BaseStore:
    """Factory: returns RedisStore if REDIS_URL is set, otherwise MemoryStore."""
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return RedisStore(redis_url)
    return MemoryStore()


store = get_store()
