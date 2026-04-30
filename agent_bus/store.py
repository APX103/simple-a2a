"""Store backends for Agent Bus — memory (default), Redis, or MongoDB."""
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


class MongoStore(BaseStore):
    """MongoDB-backed store."""

    def __init__(self, mongo_url: str = "mongodb://localhost:27017", db_name: str = "agent_bus") -> None:
        from pymongo import MongoClient
        from pymongo.server_api import ServerApi

        self._client = MongoClient(mongo_url, server_api=ServerApi('1'))
        self._db = self._client[db_name]
        self._agents = self._db["agents"]
        self._messages = self._db["messages"]
        self._groups = self._db["groups"]
        # Ensure indexes
        self._agents.create_index("agent_id", unique=True)
        self._messages.create_index([("to_agent_id", 1), ("timestamp", -1)])
        self._groups.create_index("group_id", unique=True)

    def _next_id(self, prefix: str) -> str:
        ts = int(time.time())
        rand = secrets.token_hex(4)
        return f"{prefix}_{ts}_{rand}"

    @staticmethod
    def _card_from_doc(doc: dict) -> AgentCard:
        return AgentCard(
            agent_id=doc["agent_id"],
            name=doc["name"],
            capabilities=doc.get("capabilities", []),
            limitations=doc.get("limitations", []),
            labels=doc.get("labels", []),
            announcement=doc.get("announcement", ""),
            online=doc.get("online", True),
            registered_at=doc.get("registered_at", datetime.utcnow()),
            last_seen=doc.get("last_seen", datetime.utcnow()),
        )

    @staticmethod
    def _msg_from_doc(doc: dict) -> Message:
        return Message(
            msg_id=doc["msg_id"],
            msg_type=doc["msg_type"],
            from_agent=doc["from_agent"],
            to=doc["to"],
            content=MessageContent(summary=doc.get("content_summary", ""), detail=doc.get("content_detail")),
            require_human_confirm=doc.get("require_human_confirm", False),
            human_confirmed=doc.get("human_confirmed"),
            read_at=doc.get("read_at"),
            delivered_at=doc.get("delivered_at", datetime.utcnow()),
            timestamp=doc.get("timestamp", datetime.utcnow()),
        )

    @staticmethod
    def _group_from_doc(doc: dict) -> Group:
        return Group(
            group_id=doc["group_id"],
            name=doc["name"],
            members=doc.get("members", []),
            created_by=doc["created_by"],
            created_at=doc.get("created_at", datetime.utcnow()),
        )

    def register_agent(
        self, name: str, capabilities: List[str], limitations: List[str], announcement: str, labels: List[str] = None
    ) -> tuple[str, str, AgentCard]:
        agent_id = self._next_id("agent")
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        doc = {
            "agent_id": agent_id,
            "name": name,
            "capabilities": capabilities or [],
            "limitations": limitations or [],
            "labels": labels or [],
            "announcement": announcement,
            "online": True,
            "registered_at": now,
            "last_seen": now,
            "token": token,
        }
        self._agents.insert_one(doc)
        return agent_id, token, self._card_from_doc(doc)

    def get_agent(self, agent_id: str) -> Optional[AgentCard]:
        doc = self._agents.find_one({"agent_id": agent_id})
        return self._card_from_doc(doc) if doc else None

    def list_agents(self, label: Optional[str] = None) -> List[AgentCard]:
        query = {}
        if label:
            query["labels"] = label
        docs = self._agents.find(query)
        return [self._card_from_doc(d) for d in docs]

    def verify_token(self, agent_id: str, token: str) -> bool:
        doc = self._agents.find_one({"agent_id": agent_id, "token": token})
        return doc is not None

    def agent_id_from_token(self, token: str) -> Optional[str]:
        doc = self._agents.find_one({"token": token})
        return doc["agent_id"] if doc else None

    def unregister_agent(self, agent_id: str) -> bool:
        result = self._agents.delete_one({"agent_id": agent_id})
        if result.deleted_count == 0:
            return False
        self._messages.delete_many({"to_agent_id": agent_id})
        self._messages.delete_many({"from_agent": agent_id})
        self._groups.update_many({}, {"$pull": {"members": agent_id}})
        return True

    def touch_agent(self, agent_id: str) -> None:
        self._agents.update_one(
            {"agent_id": agent_id},
            {"$set": {"last_seen": datetime.now(timezone.utc)}}
        )

    def add_message(self, msg: Message) -> None:
        delivered = datetime.now(timezone.utc)
        base_doc = {
            "msg_id": msg.msg_id,
            "msg_type": msg.msg_type,
            "from_agent": msg.from_agent,
            "to": msg.to,
            "content_summary": msg.content.summary,
            "content_detail": msg.content.detail,
            "require_human_confirm": msg.require_human_confirm,
            "human_confirmed": msg.human_confirmed,
            "read_at": None,
            "delivered_at": delivered,
            "timestamp": msg.timestamp,
        }
        if msg.to.startswith("agent_"):
            doc = {**base_doc, "to_agent_id": msg.to}
            self._messages.insert_one(doc)
        elif msg.to.startswith("group_"):
            group = self._groups.find_one({"group_id": msg.to})
            if group:
                docs = []
                for member_id in group.get("members", []):
                    docs.append({**base_doc, "msg_id": f"{msg.msg_id}_{member_id}", "to_agent_id": member_id})
                if docs:
                    self._messages.insert_many(docs)

    def get_inbox(self, agent_id: str, since: Optional[datetime] = None, unread_only: bool = False) -> List[Message]:
        query: dict = {"to_agent_id": agent_id}
        if since is not None:
            query["timestamp"] = {"$gt": since}
        if unread_only:
            query["read_at"] = None
        docs = self._messages.find(query).sort("timestamp", 1)
        return [self._msg_from_doc(d) for d in docs]

    def get_message(self, agent_id: str, msg_id: str) -> Optional[Message]:
        doc = self._messages.find_one({"msg_id": msg_id, "to_agent_id": agent_id})
        if doc:
            return self._msg_from_doc(doc)
        doc = self._messages.find_one({"msg_id": f"{msg_id}_{agent_id}", "to_agent_id": agent_id})
        if doc:
            return self._msg_from_doc(doc)
        return None

    def mark_read(self, agent_id: str, msg_id: str) -> bool:
        result = self._messages.update_one(
            {"msg_id": msg_id, "to_agent_id": agent_id},
            {"$set": {"read_at": datetime.now(timezone.utc)}}
        )
        return result.modified_count > 0

    def mark_all_read(self, agent_id: str) -> int:
        result = self._messages.update_many(
            {"to_agent_id": agent_id, "read_at": None},
            {"$set": {"read_at": datetime.now(timezone.utc)}}
        )
        return result.modified_count

    def create_group(self, name: str, created_by: str) -> Group:
        group_id = self._next_id("group")
        now = datetime.now(timezone.utc)
        doc = {
            "group_id": group_id,
            "name": name,
            "created_by": created_by,
            "created_at": now,
            "members": [created_by],
        }
        self._groups.insert_one(doc)
        return self._group_from_doc(doc)

    def get_group(self, group_id: str) -> Optional[Group]:
        doc = self._groups.find_one({"group_id": group_id})
        return self._group_from_doc(doc) if doc else None

    def list_groups(self) -> List[Group]:
        docs = self._groups.find()
        return [self._group_from_doc(d) for d in docs]

    def join_group(self, group_id: str, agent_id: str) -> bool:
        result = self._groups.update_one(
            {"group_id": group_id},
            {"$addToSet": {"members": agent_id}}
        )
        return result.modified_count > 0

    def leave_group(self, group_id: str, agent_id: str) -> bool:
        result = self._groups.update_one(
            {"group_id": group_id},
            {"$pull": {"members": agent_id}}
        )
        return result.modified_count > 0

    # ---------- Admin helpers ----------

    def admin_list_messages(self, from_agent: Optional[str] = None, to: Optional[str] = None,
                            since: Optional[datetime] = None, msg_type: Optional[str] = None) -> List[Message]:
        query: dict = {}
        if from_agent:
            query["from_agent"] = from_agent
        if to:
            query["to"] = to
        if since:
            query["timestamp"] = {"$gt": since}
        if msg_type:
            query["msg_type"] = msg_type
        docs = self._messages.find(query).sort("timestamp", -1)
        return [self._msg_from_doc(d) for d in docs]

    def admin_get_stats(self) -> dict:
        from bson import CodecOptions

        total_agents = self._agents.count_documents({})
        online_agents = self._agents.count_documents({"online": True})
        total_messages = self._messages.count_documents({})
        unread_messages = self._messages.count_documents({"read_at": None})
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        messages_today = self._messages.count_documents({"timestamp": {"$gte": today}})

        # avg latency for read messages (ms)
        pipeline = [
            {"$match": {"read_at": {"$ne": None}}},
            {"$project": {"latency": {"$subtract": [{"$toLong": "$read_at"}, {"$toLong": "$delivered_at"}]}}},
            {"$group": {"_id": None, "avg_latency": {"$avg": "$latency"}}},
        ]
        result = list(self._messages.aggregate(pipeline))
        avg_latency_ms = result[0]["avg_latency"] if result else 0

        return {
            "total_agents": total_agents,
            "online_agents": online_agents,
            "total_messages": total_messages,
            "unread_messages": unread_messages,
            "messages_today": messages_today,
            "avg_read_latency_ms": round(avg_latency_ms, 2) if avg_latency_ms else 0,
        }

    def admin_update_agent_labels(self, agent_id: str, labels: List[str]) -> bool:
        result = self._agents.update_one(
            {"agent_id": agent_id},
            {"$set": {"labels": labels}}
        )
        return result.modified_count > 0


def get_store() -> BaseStore:
    """Factory: MongoStore if MONGODB_URL is set, RedisStore if REDIS_URL is set, otherwise MemoryStore."""
    mongo_url = os.getenv("MONGODB_URL")
    if mongo_url:
        return MongoStore(mongo_url)
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return RedisStore(redis_url)
    return MemoryStore()


store = get_store()
