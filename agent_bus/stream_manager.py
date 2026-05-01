"""SSE Stream Manager — real-time message streaming for connected Agents."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, List, Optional, Set

from agent_bus.models import Message

logger = logging.getLogger("agent_bus.stream_manager")


class StreamManager:
    """Manages SSE connections per Agent."""

    def __init__(self) -> None:
        # agent_id -> set of asyncio.Queue
        self._queues: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, agent_id: str) -> asyncio.Queue:
        """Create a new SSE connection queue for an agent."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._queues.setdefault(agent_id, set()).add(queue)
        logger.info("SSE connect: %s (queues=%d)", agent_id, len(self._queues.get(agent_id, set())))
        return queue

    async def disconnect(self, agent_id: str, queue: asyncio.Queue) -> None:
        """Remove an SSE connection queue."""
        async with self._lock:
            queues = self._queues.get(agent_id)
            if queues:
                queues.discard(queue)
                if not queues:
                    self._queues.pop(agent_id, None)
        logger.info("SSE disconnect: %s", agent_id)

    async def publish(self, agent_id: str, data: dict) -> int:
        """Publish a message to all connected SSE clients for an agent.
        Returns number of clients that received the message.
        """
        async with self._lock:
            queues = list(self._queues.get(agent_id, set()))

        count = 0
        for q in queues:
            try:
                q.put_nowait(data)
                count += 1
            except asyncio.QueueFull:
                logger.warning("SSE queue full for agent %s, dropping message", agent_id)
        return count

    async def publish_message(self, msg: Message) -> None:
        """Publish a Message to the recipient's SSE subscribers."""
        payload = {
            "event": "message.received",
            "message": msg.model_dump(mode="json"),
        }
        await self.publish(msg.to, payload)

    def has_subscribers(self, agent_id: str) -> bool:
        """Check if an agent has active SSE connections."""
        queues = self._queues.get(agent_id)
        return bool(queues) if queues else False

    async def stats(self) -> dict:
        """Return connection stats."""
        async with self._lock:
            return {
                "total_agents": len(self._queues),
                "total_connections": sum(len(qs) for qs in self._queues.values()),
            }


# Global instance
_stream_manager: Optional[StreamManager] = None


def get_stream_manager() -> StreamManager:
    global _stream_manager
    if _stream_manager is None:
        _stream_manager = StreamManager()
    return _stream_manager
