"""Store backends for Agent Bus — memory (default), Redis, or MongoDB."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional

from agent_bus.models import (
    AgentCard,
    DeadLetterMessage,
    DeliveryRecord,
    Group,
    Message,
    MessageContent,
    WebhookConfig,
)

# ---------- Config ----------
QUEUE_MAXLEN = int(os.getenv("AGENT_BUS_QUEUE_MAXLEN", "1000"))
DLQ_MAXLEN = int(os.getenv("AGENT_BUS_DLQ_MAXLEN", "500"))
PUSH_MAX_RETRY = int(os.getenv("AGENT_BUS_PUSH_MAX_RETRY", "3"))


class BaseStore(ABC):
    """Abstract store interface."""

    @abstractmethod
    def register_agent(
        self, name: str, capabilities: List[str], limitations: List[str], announcement: str, labels: List[str] = None, webhook: Optional[WebhookConfig] = None, delivery_preference: str = "both"
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
    def get_inbox(self, agent_id: str, since: Optional[datetime] = None, unread_only: bool = False) -> List[Message]:
        ...

    @abstractmethod
    def get_message(self, agent_id: str, msg_id: str) -> Optional[Message]:
        ...

    @abstractmethod
    def mark_read(self, agent_id: str, msg_id: str) -> bool:
        ...

    @abstractmethod
    def mark_all_read(self, agent_id: str) -> int:
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

    # ---------- Webhook & Delivery (new) ----------

    @abstractmethod
    def set_agent_webhook(self, agent_id: str, config: Optional[WebhookConfig]) -> bool:
        ...

    @abstractmethod
    def get_agent_webhook(self, agent_id: str) -> Optional[WebhookConfig]:
        ...

    @abstractmethod
    def delete_agent_webhook(self, agent_id: str) -> bool:
        ...

    @abstractmethod
    def get_delivery_record(self, msg_id: str, agent_id: str) -> Optional[DeliveryRecord]:
        ...

    @abstractmethod
    def set_delivery_status(self, msg_id: str, agent_id: str, channel: str, status: str, attempts: int = 0, last_error: Optional[str] = None) -> None:
        ...

    @abstractmethod
    def update_delivery_pulled(self, msg_id: str, agent_id: str) -> None:
        ...

    @abstractmethod
    def update_delivery_confirmed(self, msg_id: str, agent_id: str) -> None:
        ...

    @abstractmethod
    def list_pending_push(self, before: float) -> List[tuple[str, str]]:
        """Return list of (msg_id, agent_id) whose push scheduling time <= before."""
        ...

    @abstractmethod
    def schedule_push(self, msg_id: str, agent_id: str, retry_at: float) -> None:
        ...

    @abstractmethod
    def remove_pending_push(self, msg_id: str, agent_id: str) -> None:
        ...

    @abstractmethod
    def add_dlq(self, dlq_msg: DeadLetterMessage) -> None:
        ...

    @abstractmethod
    def list_dlq(self, agent_id: Optional[str] = None) -> List[DeadLetterMessage]:
        ...

    @abstractmethod
    def retry_dlq(self, msg_id: str, agent_id: str) -> bool:
        ...


# =============================================================================
# MemoryStore
# =============================================================================

class MemoryStore(BaseStore):
    """Thread-safe in-memory store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agents: Dict[str, AgentCard] = {}
        self._tokens: Dict[str, str] = {}
        self._messages: Dict[str, List[Message]] = {}
        self._groups: Dict[str, Group] = {}
        self._counter = 0
        # new
        self._delivery: Dict[str, DeliveryRecord] = {}
        self._pending_push: List[tuple[float, str, str]] = []  # (retry_at, msg_id, agent_id)
        self._dlq: Dict[str, List[DeadLetterMessage]] = {}

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        ts = int(time.time())
        rand = secrets.token_hex(4)
        return f"{prefix}_{ts}_{rand}_{self._counter}"

    def register_agent(
        self, name: str, capabilities: List[str], limitations: List[str], announcement: str, labels: List[str] = None, webhook: Optional[WebhookConfig] = None, delivery_preference: str = "both"
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
                webhook=webhook,
                delivery_preference=delivery_preference,  # type: ignore[arg-type]
            )
            self._agents[agent_id] = card
            self._tokens[agent_id] = token
            self._messages[agent_id] = []
            self._dlq[agent_id] = []
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
                self._dlq.pop(agent_id, None)
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

    def get_inbox(self, agent_id: str, since: Optional[datetime] = None, unread_only: bool = False) -> List[Message]:
        with self._lock:
            msgs = self._messages.get(agent_id, [])
            if since is None:
                result = list(msgs)
            else:
                result = [m for m in msgs if m.timestamp > since]
            if unread_only:
                result = [m for m in result if m.read_at is None]
            return result

    def get_message(self, agent_id: str, msg_id: str) -> Optional[Message]:
        with self._lock:
            for m in self._messages.get(agent_id, []):
                if m.msg_id == msg_id:
                    return m
            return None

    def mark_read(self, agent_id: str, msg_id: str) -> bool:
        with self._lock:
            for m in self._messages.get(agent_id, []):
                if m.msg_id == msg_id and m.read_at is None:
                    m.read_at = datetime.utcnow()
                    return True
            return False

    def mark_all_read(self, agent_id: str) -> int:
        with self._lock:
            count = 0
            for m in self._messages.get(agent_id, []):
                if m.read_at is None:
                    m.read_at = datetime.utcnow()
                    count += 1
            return count

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

    # ---------- Webhook & Delivery ----------

    def set_agent_webhook(self, agent_id: str, config: Optional[WebhookConfig]) -> bool:
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return False
            agent.webhook = config
            return True

    def get_agent_webhook(self, agent_id: str) -> Optional[WebhookConfig]:
        with self._lock:
            agent = self._agents.get(agent_id)
            return agent.webhook if agent else None

    def delete_agent_webhook(self, agent_id: str) -> bool:
        return self.set_agent_webhook(agent_id, None)

    def get_delivery_record(self, msg_id: str, agent_id: str) -> Optional[DeliveryRecord]:
        with self._lock:
            return self._delivery.get(f"{msg_id}:{agent_id}")

    def set_delivery_status(self, msg_id: str, agent_id: str, channel: str, status: str, attempts: int = 0, last_error: Optional[str] = None) -> None:
        with self._lock:
            key = f"{msg_id}:{agent_id}"
            existing = self._delivery.get(key)
            if existing:
                existing.channel = channel  # type: ignore[assignment]
                existing.status = status  # type: ignore[assignment]
                existing.attempts = attempts
                if last_error is not None:
                    existing.last_error = last_error
                if status == "delivered":
                    existing.pushed_at = datetime.now(timezone.utc)
            else:
                self._delivery[key] = DeliveryRecord(
                    msg_id=msg_id,
                    agent_id=agent_id,
                    channel=channel,  # type: ignore[arg-type]
                    status=status,  # type: ignore[arg-type]
                    attempts=attempts,
                    last_error=last_error,
                    pushed_at=datetime.now(timezone.utc) if status == "delivered" else None,
                )

    def update_delivery_pulled(self, msg_id: str, agent_id: str) -> None:
        with self._lock:
            key = f"{msg_id}:{agent_id}"
            rec = self._delivery.get(key)
            if rec:
                rec.status = "pulled"  # type: ignore[assignment]
                rec.pulled_at = datetime.now(timezone.utc)
            else:
                self._delivery[key] = DeliveryRecord(
                    msg_id=msg_id, agent_id=agent_id, channel="pull", status="pulled", pulled_at=datetime.now(timezone.utc)
                )

    def update_delivery_confirmed(self, msg_id: str, agent_id: str) -> None:
        with self._lock:
            key = f"{msg_id}:{agent_id}"
            rec = self._delivery.get(key)
            if rec:
                rec.confirmed_at = datetime.now(timezone.utc)

    def list_pending_push(self, before: float) -> List[tuple[str, str]]:
        with self._lock:
            result = []
            for retry_at, msg_id, agent_id in self._pending_push:
                if retry_at <= before:
                    result.append((msg_id, agent_id))
            # clean up fetched items
            self._pending_push = [(r, m, a) for r, m, a in self._pending_push if r > before]
            return result

    def schedule_push(self, msg_id: str, agent_id: str, retry_at: float) -> None:
        with self._lock:
            # remove old entry if exists
            self._pending_push = [(r, m, a) for r, m, a in self._pending_push if not (m == msg_id and a == agent_id)]
            self._pending_push.append((retry_at, msg_id, agent_id))
            self._pending_push.sort(key=lambda x: x[0])

    def remove_pending_push(self, msg_id: str, agent_id: str) -> None:
        with self._lock:
            self._pending_push = [(r, m, a) for r, m, a in self._pending_push if not (m == msg_id and a == agent_id)]

    def add_dlq(self, dlq_msg: DeadLetterMessage) -> None:
        with self._lock:
            self._dlq.setdefault(dlq_msg.agent_id, []).append(dlq_msg)

    def list_dlq(self, agent_id: Optional[str] = None) -> List[DeadLetterMessage]:
        with self._lock:
            if agent_id:
                return list(self._dlq.get(agent_id, []))
            result = []
            for items in self._dlq.values():
                result.extend(items)
            return result

    def retry_dlq(self, msg_id: str, agent_id: str) -> bool:
        with self._lock:
            items = self._dlq.get(agent_id, [])
            for i, item in enumerate(items):
                if item.original_msg.msg_id == msg_id:
                    # move back to pending push
                    self.schedule_push(msg_id, agent_id, time.time())
                    items.pop(i)
                    return True
            return False

    # ---------- Admin helpers ----------

    def admin_list_messages(self, from_agent: Optional[str] = None, to: Optional[str] = None,
                            since: Optional[datetime] = None, msg_type: Optional[str] = None) -> List[Message]:
        with self._lock:
            all_msgs: List[Message] = []
            for msgs in self._messages.values():
                all_msgs.extend(msgs)
            seen = set()
            result = []
            for m in all_msgs:
                if m.msg_id in seen:
                    continue
                seen.add(m.msg_id)
                if from_agent and m.from_agent != from_agent:
                    continue
                if to and m.to != to:
                    continue
                if since and m.timestamp <= since:
                    continue
                if msg_type and m.msg_type != msg_type:
                    continue
                result.append(m)
            result.sort(key=lambda x: x.timestamp, reverse=True)
            return result

    def admin_get_stats(self) -> dict:
        with self._lock:
            total_agents = len(self._agents)
            online_agents = sum(1 for a in self._agents.values() if a.online)
            seen = set()
            total_messages = 0
            unread_messages = 0
            messages_today = 0
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            latency_sum = 0.0
            read_count = 0
            for msgs in self._messages.values():
                for m in msgs:
                    if m.msg_id in seen:
                        continue
                    seen.add(m.msg_id)
                    total_messages += 1
                    if m.read_at is None:
                        unread_messages += 1
                    if m.timestamp >= today:
                        messages_today += 1
                    if m.read_at and m.delivered_at:
                        latency_sum += (m.read_at - m.delivered_at).total_seconds() * 1000
                        read_count += 1
            return {
                "total_agents": total_agents,
                "online_agents": online_agents,
                "total_messages": total_messages,
                "unread_messages": unread_messages,
                "messages_today": messages_today,
                "avg_read_latency_ms": round(latency_sum / read_count, 2) if read_count else 0,
            }

    def admin_update_agent_labels(self, agent_id: str, labels: List[str]) -> bool:
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return False
            agent.labels = labels
            return True


# =============================================================================
# RedisStore — Stream-based
# =============================================================================

class RedisStore(BaseStore):
    """Redis-backed store using Redis Streams for bounded message queues."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis

        self._client = redis.from_url(redis_url, decode_responses=True)
        self._prefix = "agent_bus"
        self._maxlen = QUEUE_MAXLEN

    def _key(self, *parts: str) -> str:
        return ":".join([self._prefix, *parts])

    def _next_id(self, prefix: str) -> str:
        ts = int(time.time())
        rand = secrets.token_hex(4)
        counter = self._client.incr(self._key("counter"))
        return f"{prefix}_{ts}_{rand}_{counter}"

    def register_agent(
        self, name: str, capabilities: List[str], limitations: List[str], announcement: str, labels: List[str] = None, webhook: Optional[WebhookConfig] = None, delivery_preference: str = "both"
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
            webhook=webhook,
            delivery_preference=delivery_preference,  # type: ignore[arg-type]
        )
        pipe = self._client.pipeline()
        pipe.hset(self._key("agents"), agent_id, card.model_dump_json())
        pipe.hset(self._key("tokens"), agent_id, token)
        # ensure stream exists (XADD creates it automatically, but we may want to set maxlen on first use)
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
        pipe.delete(self._key("stream", agent_id))
        pipe.delete(self._key("dlq", agent_id))
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
        now = datetime.now(timezone.utc)
        msg_data = msg.model_dump_json()

        def _add_to_agent(agent_id: str) -> None:
            # Write to bounded stream
            self._client.xadd(
                self._key("stream", agent_id),
                {"data": msg_data, "msg_id": msg.msg_id},
                maxlen=self._maxlen,
                approximate=True,
            )
            # Record delivery status
            self.set_delivery_status(msg.msg_id, agent_id, "push" if self._should_push(agent_id) else "pull", "pending")
            # Schedule push if applicable
            if self._should_push(agent_id):
                self.schedule_push(msg.msg_id, agent_id, time.time())

        if msg.to.startswith("agent_"):
            _add_to_agent(msg.to)
        elif msg.to.startswith("group_"):
            group = self.get_group(msg.to)
            if group:
                for member_id in group.members:
                    _add_to_agent(member_id)

    def _should_push(self, agent_id: str) -> bool:
        agent = self.get_agent(agent_id)
        if not agent or not agent.webhook or not agent.webhook.enabled:
            return False
        if agent.delivery_preference == "pull":
            return False
        return True

    def get_inbox(self, agent_id: str, since: Optional[datetime] = None, unread_only: bool = False) -> List[Message]:
        stream_key = self._key("stream", agent_id)
        # XRANGE from the beginning (or since timestamp)
        if since is not None:
            start_ms = int(since.timestamp() * 1000)
            # "0-0" is a reserved ID; use "-" for the beginning of time
            min_id = f"{start_ms}-0" if start_ms > 0 else "-"
            entries = self._client.xrange(stream_key, min=min_id, max="+")
        else:
            entries = self._client.xrange(stream_key, min="-", max="+")

        msgs = []
        for entry_id, fields in entries:
            raw = fields.get("data", "{}")
            try:
                m = Message.model_validate_json(raw)
                msgs.append(m)
            except Exception:
                continue

        if unread_only:
            msgs = [m for m in msgs if m.read_at is None]
        return msgs

    def get_message(self, agent_id: str, msg_id: str) -> Optional[Message]:
        for m in self.get_inbox(agent_id):
            if m.msg_id == msg_id:
                return m
        return None

    def mark_read(self, agent_id: str, msg_id: str) -> bool:
        # Streams are append-only; we cannot modify entries in place.
        # We track read status in a separate hash or simply acknowledge via delivery record.
        # For simplicity in this POC, we update the delivery record and return True
        # if the message exists in the agent's stream.
        if self.get_message(agent_id, msg_id):
            self.update_delivery_pulled(msg_id, agent_id)
            return True
        return False

    def mark_all_read(self, agent_id: str) -> int:
        unread = self.get_inbox(agent_id, unread_only=True)
        for m in unread:
            self.update_delivery_pulled(m.msg_id, agent_id)
        return len(unread)

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

    # ---------- Webhook & Delivery ----------

    def set_agent_webhook(self, agent_id: str, config: Optional[WebhookConfig]) -> bool:
        raw = self._client.hget(self._key("agents"), agent_id)
        if raw is None:
            return False
        card = AgentCard.model_validate_json(raw)
        card.webhook = config
        self._client.hset(self._key("agents"), agent_id, card.model_dump_json())
        return True

    def get_agent_webhook(self, agent_id: str) -> Optional[WebhookConfig]:
        agent = self.get_agent(agent_id)
        return agent.webhook if agent else None

    def delete_agent_webhook(self, agent_id: str) -> bool:
        return self.set_agent_webhook(agent_id, None)

    def get_delivery_record(self, msg_id: str, agent_id: str) -> Optional[DeliveryRecord]:
        raw = self._client.hget(self._key("delivery"), f"{msg_id}:{agent_id}")
        if raw is None:
            return None
        return DeliveryRecord.model_validate_json(raw)

    def set_delivery_status(self, msg_id: str, agent_id: str, channel: str, status: str, attempts: int = 0, last_error: Optional[str] = None) -> None:
        key = f"{msg_id}:{agent_id}"
        existing = self.get_delivery_record(msg_id, agent_id)
        if existing:
            existing.channel = channel  # type: ignore[assignment]
            existing.status = status  # type: ignore[assignment]
            existing.attempts = attempts
            if last_error is not None:
                existing.last_error = last_error
            if status == "delivered":
                existing.pushed_at = datetime.now(timezone.utc)
        else:
            existing = DeliveryRecord(
                msg_id=msg_id,
                agent_id=agent_id,
                channel=channel,  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                attempts=attempts,
                last_error=last_error,
                pushed_at=datetime.now(timezone.utc) if status == "delivered" else None,
            )
        self._client.hset(self._key("delivery"), key, existing.model_dump_json())

    def update_delivery_pulled(self, msg_id: str, agent_id: str) -> None:
        key = f"{msg_id}:{agent_id}"
        rec = self.get_delivery_record(msg_id, agent_id)
        if rec:
            rec.status = "pulled"  # type: ignore[assignment]
            rec.pulled_at = datetime.now(timezone.utc)
        else:
            rec = DeliveryRecord(
                msg_id=msg_id, agent_id=agent_id, channel="pull", status="pulled", pulled_at=datetime.now(timezone.utc)
            )
        self._client.hset(self._key("delivery"), key, rec.model_dump_json())

    def update_delivery_confirmed(self, msg_id: str, agent_id: str) -> None:
        key = f"{msg_id}:{agent_id}"
        rec = self.get_delivery_record(msg_id, agent_id)
        if rec:
            rec.confirmed_at = datetime.now(timezone.utc)
            self._client.hset(self._key("delivery"), key, rec.model_dump_json())

    def list_pending_push(self, before: float) -> List[tuple[str, str]]:
        """Return (msg_id, agent_id) whose scheduled time <= before."""
        entries = self._client.zrangebyscore(self._key("pending_push"), 0, before, withscores=False)
        result = []
        for entry in entries:
            parts = entry.split(":")
            if len(parts) == 2:
                result.append((parts[0], parts[1]))
        # Remove fetched entries
        if entries:
            self._client.zrem(self._key("pending_push"), *entries)
        return result

    def schedule_push(self, msg_id: str, agent_id: str, retry_at: float) -> None:
        member = f"{msg_id}:{agent_id}"
        self._client.zadd(self._key("pending_push"), {member: retry_at})

    def remove_pending_push(self, msg_id: str, agent_id: str) -> None:
        member = f"{msg_id}:{agent_id}"
        self._client.zrem(self._key("pending_push"), member)

    def add_dlq(self, dlq_msg: DeadLetterMessage) -> None:
        self._client.xadd(
            self._key("dlq", dlq_msg.agent_id),
            {"data": dlq_msg.model_dump_json()},
            maxlen=DLQ_MAXLEN,
            approximate=True,
        )

    def list_dlq(self, agent_id: Optional[str] = None) -> List[DeadLetterMessage]:
        if agent_id:
            entries = self._client.xrange(self._key("dlq", agent_id), min="-", max="+")
            result = []
            for entry_id, fields in entries:
                raw = fields.get("data", "{}")
                try:
                    result.append(DeadLetterMessage.model_validate_json(raw))
                except Exception:
                    continue
            return result
        else:
            result = []
            for key in self._client.scan_iter(match=self._key("dlq", "*")):
                entries = self._client.xrange(key, min="-", max="+")
                for entry_id, fields in entries:
                    raw = fields.get("data", "{}")
                    try:
                        result.append(DeadLetterMessage.model_validate_json(raw))
                    except Exception:
                        continue
            return result

    def retry_dlq(self, msg_id: str, agent_id: str) -> bool:
        # Find in DLQ stream and remove it (by reading and rewriting without it)
        dlq_key = self._key("dlq", agent_id)
        entries = self._client.xrange(dlq_key, min="-", max="+")
        target_entry_id = None
        for entry_id, fields in entries:
            raw = fields.get("data", "{}")
            try:
                item = DeadLetterMessage.model_validate_json(raw)
                if item.original_msg.msg_id == msg_id:
                    target_entry_id = entry_id
                    break
            except Exception:
                continue
        if target_entry_id:
            self._client.xdel(dlq_key, target_entry_id)
            self.schedule_push(msg_id, agent_id, time.time())
            return True
        return False

    # ---------- Admin helpers ----------

    def admin_list_messages(self, from_agent: Optional[str] = None, to: Optional[str] = None,
                            since: Optional[datetime] = None, msg_type: Optional[str] = None) -> List[Message]:
        all_msgs: List[Message] = []
        for key in self._client.scan_iter(match=self._key("stream", "*")):
            entries = self._client.xrange(key, min="-", max="+")
            for entry_id, fields in entries:
                raw = fields.get("data", "{}")
                try:
                    m = Message.model_validate_json(raw)
                    all_msgs.append(m)
                except Exception:
                    continue
        seen = set()
        result = []
        for m in all_msgs:
            if m.msg_id in seen:
                continue
            seen.add(m.msg_id)
            if from_agent and m.from_agent != from_agent:
                continue
            if to and m.to != to:
                continue
            if since and m.timestamp <= since:
                continue
            if msg_type and m.msg_type != msg_type:
                continue
            result.append(m)
        result.sort(key=lambda x: x.timestamp, reverse=True)
        return result

    def admin_get_stats(self) -> dict:
        total_agents = len(self.list_agents())
        online_agents = sum(1 for a in self.list_agents() if a.online)
        total_messages = 0
        unread_messages = 0
        messages_today = 0
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        for key in self._client.scan_iter(match=self._key("stream", "*")):
            entries = self._client.xrange(key, min="-", max="+")
            seen = set()
            for entry_id, fields in entries:
                raw = fields.get("data", "{}")
                try:
                    m = Message.model_validate_json(raw)
                    if m.msg_id in seen:
                        continue
                    seen.add(m.msg_id)
                    total_messages += 1
                    if m.read_at is None:
                        unread_messages += 1
                    if m.timestamp >= today:
                        messages_today += 1
                except Exception:
                    continue

        # avg latency from delivery records
        latency_sum = 0.0
        read_count = 0
        delivery_map = self._client.hgetall(self._key("delivery"))
        for raw in delivery_map.values():
            try:
                rec = DeliveryRecord.model_validate_json(raw)
                if rec.pulled_at and rec.pushed_at:
                    latency_sum += (rec.pulled_at - rec.pushed_at).total_seconds() * 1000
                    read_count += 1
            except Exception:
                continue

        return {
            "total_agents": total_agents,
            "online_agents": online_agents,
            "total_messages": total_messages,
            "unread_messages": unread_messages,
            "messages_today": messages_today,
            "avg_read_latency_ms": round(latency_sum / read_count, 2) if read_count else 0,
        }

    def admin_update_agent_labels(self, agent_id: str, labels: List[str]) -> bool:
        raw = self._client.hget(self._key("agents"), agent_id)
        if raw is None:
            return False
        card = AgentCard.model_validate_json(raw)
        card.labels = labels
        self._client.hset(self._key("agents"), agent_id, card.model_dump_json())
        return True


# =============================================================================
# MongoStore
# =============================================================================

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
        self._delivery = self._db["delivery"]
        self._dlq = self._db["dlq"]
        self._pending_push = self._db["pending_push"]
        # Ensure indexes
        self._agents.create_index("agent_id", unique=True)
        self._messages.create_index([("to_agent_id", 1), ("timestamp", -1)])
        self._groups.create_index("group_id", unique=True)
        self._delivery.create_index([("msg_id", 1), ("agent_id", 1)], unique=True)
        self._pending_push.create_index("retry_at")

    def _next_id(self, prefix: str) -> str:
        ts = int(time.time())
        rand = secrets.token_hex(4)
        return f"{prefix}_{ts}_{rand}"

    @staticmethod
    def _card_from_doc(doc: dict) -> AgentCard:
        webhook = None
        if doc.get("webhook"):
            webhook = WebhookConfig.model_validate(doc["webhook"])
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
            webhook=webhook,
            delivery_preference=doc.get("delivery_preference", "both"),
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
        self, name: str, capabilities: List[str], limitations: List[str], announcement: str, labels: List[str] = None, webhook: Optional[WebhookConfig] = None, delivery_preference: str = "both"
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
            "webhook": webhook.model_dump() if webhook else None,
            "delivery_preference": delivery_preference,
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
        self._delivery.delete_many({"agent_id": agent_id})
        self._dlq.delete_many({"agent_id": agent_id})
        self._pending_push.delete_many({"agent_id": agent_id})
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
            self.set_delivery_status(msg.msg_id, msg.to, "push" if self._should_push(msg.to) else "pull", "pending")
            if self._should_push(msg.to):
                self.schedule_push(msg.msg_id, msg.to, time.time())
        elif msg.to.startswith("group_"):
            group = self._groups.find_one({"group_id": msg.to})
            if group:
                docs = []
                for member_id in group.get("members", []):
                    docs.append({**base_doc, "msg_id": f"{msg.msg_id}_{member_id}", "to_agent_id": member_id})
                if docs:
                    self._messages.insert_many(docs)
                for member_id in group.get("members", []):
                    self.set_delivery_status(msg.msg_id, member_id, "push" if self._should_push(member_id) else "pull", "pending")
                    if self._should_push(member_id):
                        self.schedule_push(msg.msg_id, member_id, time.time())

    def _should_push(self, agent_id: str) -> bool:
        agent = self.get_agent(agent_id)
        if not agent or not agent.webhook or not agent.webhook.enabled:
            return False
        if agent.delivery_preference == "pull":
            return False
        return True

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
        if result.modified_count > 0:
            self.update_delivery_pulled(msg_id, agent_id)
            return True
        return False

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

    # ---------- Webhook & Delivery ----------

    def set_agent_webhook(self, agent_id: str, config: Optional[WebhookConfig]) -> bool:
        result = self._agents.update_one(
            {"agent_id": agent_id},
            {"$set": {"webhook": config.model_dump() if config else None}}
        )
        return result.modified_count > 0

    def get_agent_webhook(self, agent_id: str) -> Optional[WebhookConfig]:
        doc = self._agents.find_one({"agent_id": agent_id}, {"webhook": 1})
        if doc and doc.get("webhook"):
            return WebhookConfig.model_validate(doc["webhook"])
        return None

    def delete_agent_webhook(self, agent_id: str) -> bool:
        return self.set_agent_webhook(agent_id, None)

    def get_delivery_record(self, msg_id: str, agent_id: str) -> Optional[DeliveryRecord]:
        doc = self._delivery.find_one({"msg_id": msg_id, "agent_id": agent_id})
        if doc:
            return DeliveryRecord.model_validate(doc)
        return None

    def set_delivery_status(self, msg_id: str, agent_id: str, channel: str, status: str, attempts: int = 0, last_error: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc)
        update = {
            "$set": {
                "msg_id": msg_id,
                "agent_id": agent_id,
                "channel": channel,
                "status": status,
                "attempts": attempts,
                "last_error": last_error,
            }
        }
        if status == "delivered":
            update["$set"]["pushed_at"] = now
        self._delivery.update_one(
            {"msg_id": msg_id, "agent_id": agent_id},
            update,
            upsert=True,
        )

    def update_delivery_pulled(self, msg_id: str, agent_id: str) -> None:
        self._delivery.update_one(
            {"msg_id": msg_id, "agent_id": agent_id},
            {"$set": {"status": "pulled", "pulled_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    def update_delivery_confirmed(self, msg_id: str, agent_id: str) -> None:
        self._delivery.update_one(
            {"msg_id": msg_id, "agent_id": agent_id},
            {"$set": {"confirmed_at": datetime.now(timezone.utc)}},
        )

    def list_pending_push(self, before: float) -> List[tuple[str, str]]:
        docs = self._pending_push.find({"retry_at": {"$lte": before}})
        result = [(d["msg_id"], d["agent_id"]) for d in docs]
        if result:
            self._pending_push.delete_many({"retry_at": {"$lte": before}})
        return result

    def schedule_push(self, msg_id: str, agent_id: str, retry_at: float) -> None:
        self._pending_push.update_one(
            {"msg_id": msg_id, "agent_id": agent_id},
            {"$set": {"msg_id": msg_id, "agent_id": agent_id, "retry_at": retry_at}},
            upsert=True,
        )

    def remove_pending_push(self, msg_id: str, agent_id: str) -> None:
        self._pending_push.delete_one({"msg_id": msg_id, "agent_id": agent_id})

    def add_dlq(self, dlq_msg: DeadLetterMessage) -> None:
        self._dlq.insert_one(dlq_msg.model_dump())

    def list_dlq(self, agent_id: Optional[str] = None) -> List[DeadLetterMessage]:
        query = {}
        if agent_id:
            query["agent_id"] = agent_id
        docs = self._dlq.find(query).sort("entered_dlq_at", -1)
        return [DeadLetterMessage.model_validate(d) for d in docs]

    def retry_dlq(self, msg_id: str, agent_id: str) -> bool:
        result = self._dlq.delete_one({"original_msg.msg_id": msg_id, "agent_id": agent_id})
        if result.deleted_count > 0:
            self.schedule_push(msg_id, agent_id, time.time())
            return True
        return False

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


# =============================================================================
# Factory
# =============================================================================

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
