"""Push Delivery Engine — asynchronously delivers messages to Agent webhooks."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from agent_bus.models import DeadLetterMessage, Message, WebhookConfig
from agent_bus.store import BaseStore

logger = logging.getLogger("agent_bus.push_engine")

# Config from env
PUSH_ENABLED = os.getenv("AGENT_BUS_PUSH_ENABLED", "true").lower() in ("1", "true", "yes")
PUSH_WORKERS = int(os.getenv("AGENT_BUS_PUSH_WORKERS", "10"))
PUSH_TIMEOUT = float(os.getenv("AGENT_BUS_PUSH_TIMEOUT", "10"))
PUSH_MAX_RETRY = int(os.getenv("AGENT_BUS_PUSH_MAX_RETRY", "3"))
PUSH_BACKOFF_BASE = float(os.getenv("AGENT_BUS_PUSH_BACKOFF_BASE", "2"))


class PushDeliveryEngine:
    """Background engine that pushes messages to Agent webhooks."""

    def __init__(
        self,
        store: BaseStore,
        max_workers: int = PUSH_WORKERS,
        timeout: float = PUSH_TIMEOUT,
        max_retry: int = PUSH_MAX_RETRY,
        backoff_base: float = PUSH_BACKOFF_BASE,
    ) -> None:
        self.store = store
        self.max_workers = max_workers
        self.timeout = timeout
        self.max_retry = max_retry
        self.backoff_base = backoff_base

        self._client: Optional[httpx.AsyncClient] = None
        self._scheduler_task: Optional[asyncio.Task] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._shutdown = False
        self._inflight: set[tuple[str, str]] = set()
        self._inflight_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not PUSH_ENABLED:
            logger.info("Push delivery is disabled (AGENT_BUS_PUSH_ENABLED=false).")
            return
        if self._scheduler_task is not None:
            logger.warning("PushDeliveryEngine already started.")
            return
        logger.info("Starting PushDeliveryEngine (workers=%d, timeout=%.1fs, max_retry=%d)",
                    self.max_workers, self.timeout, self.max_retry)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))
        self._semaphore = asyncio.Semaphore(self.max_workers)
        self._shutdown = False
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        if not PUSH_ENABLED:
            return
        logger.info("Stopping PushDeliveryEngine...")
        self._shutdown = True
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        # Wait for in-flight tasks with a timeout
        if self._inflight_tasks:
            logger.info("Waiting for %d in-flight push tasks...", len(self._inflight_tasks))
            _, pending = await asyncio.wait(self._inflight_tasks, timeout=self.timeout + 5)
            for task in pending:
                task.cancel()
        if self._client:
            await self._client.aclose()
        self._scheduler_task = None
        self._inflight.clear()
        self._inflight_tasks.clear()
        logger.info("PushDeliveryEngine stopped.")

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    async def _scheduler_loop(self) -> None:
        """Poll pending_push queue and dispatch workers."""
        while not self._shutdown:
            try:
                now = time.time()
                pending = self.store.list_pending_push(before=now)
                if pending:
                    logger.debug("Scheduler: %d pending push jobs", len(pending))
                for msg_id, agent_id in pending:
                    if self._shutdown:
                        break
                    key = (msg_id, agent_id)
                    if key in self._inflight:
                        continue
                    self._inflight.add(key)
                    task = asyncio.create_task(self._dispatch(msg_id, agent_id))
                    self._inflight_tasks.add(task)
                    task.add_done_callback(lambda t, k=key: (self._inflight.discard(k), self._inflight_tasks.discard(t)))
            except Exception as exc:
                logger.exception("Scheduler loop error: %s", exc)
            await asyncio.sleep(1.0)

    async def _dispatch(self, msg_id: str, agent_id: str) -> None:
        """Dispatch a single push job with concurrency limit."""
        if self._semaphore is None or self._client is None:
            return
        try:
            async with self._semaphore:
                await self._run_push(msg_id, agent_id)
        except Exception as exc:
            logger.exception("Dispatch error for msg=%s agent=%s: %s", msg_id, agent_id, exc)

    # ------------------------------------------------------------------
    # Push logic
    # ------------------------------------------------------------------

    async def _run_push(self, msg_id: str, agent_id: str) -> None:
        agent = self.store.get_agent(agent_id)
        if not agent or not agent.webhook or not agent.webhook.enabled:
            logger.debug("Agent %s has no webhook; skipping push.", agent_id)
            self.store.remove_pending_push(msg_id, agent_id)
            return

        webhook: WebhookConfig = agent.webhook
        msg = self.store.get_message(agent_id, msg_id)
        if not msg:
            logger.warning("Message %s not found for agent %s; dropping push job.", msg_id, agent_id)
            self.store.remove_pending_push(msg_id, agent_id)
            return

        # Fetch current delivery record to know attempts
        record = self.store.get_delivery_record(msg_id, agent_id)
        attempts = record.attempts if record else 0

        success, error_detail = await self._push_one(msg, webhook)

        if success:
            logger.info("Push succeeded: msg=%s -> agent=%s (attempt=%d)", msg_id, agent_id, attempts + 1)
            self.store.set_delivery_status(
                msg_id, agent_id, "push", "delivered",
                attempts=attempts + 1,
            )
            self.store.remove_pending_push(msg_id, agent_id)
            return

        # Failure handling
        attempts += 1
        last_error = error_detail or f"HTTP push failed after {attempts} attempt(s)"
        logger.warning("Push failed: msg=%s -> agent=%s (attempt=%d/%d) error=%s",
                       msg_id, agent_id, attempts, self.max_retry, last_error)

        if attempts >= self.max_retry:
            logger.error("Push max retry exceeded for msg=%s -> agent=%s; moving to DLQ.", msg_id, agent_id)
            self.store.set_delivery_status(
                msg_id, agent_id, "push", "failed",
                attempts=attempts, last_error=last_error,
            )
            self.store.remove_pending_push(msg_id, agent_id)
            self.store.add_dlq(DeadLetterMessage(
                original_msg=msg,
                agent_id=agent_id,
                reason="max_retry_exceeded",
                failed_attempts=attempts,
            ))
            return

        # Schedule retry with exponential backoff
        backoff = self.backoff_base ** attempts
        retry_at = time.time() + backoff
        self.store.set_delivery_status(
            msg_id, agent_id, "push", "pending",
            attempts=attempts, last_error=last_error,
        )
        self.store.schedule_push(msg_id, agent_id, retry_at)
        logger.info("Push retry scheduled: msg=%s -> agent=%s in %.1fs", msg_id, agent_id, backoff)

    async def _push_one(self, msg: Message, webhook: WebhookConfig) -> tuple[bool, str]:
        """Execute single HTTP POST to webhook URL. Returns (success, error_detail)."""
        if self._client is None:
            return False, "Client not initialized"

        payload = {
            "event": "message.received",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": msg.model_dump(mode="json"),
        }

        headers = {
            "Content-Type": "application/json",
            "X-Agent-Bus-Version": "1.2.0",
        }
        if webhook.auth_scheme == "bearer" and webhook.token:
            headers["Authorization"] = f"Bearer {webhook.token}"
        elif webhook.auth_scheme == "header_token" and webhook.token:
            headers["X-Webhook-Token"] = webhook.token

        try:
            resp = await self._client.post(str(webhook.url), json=payload, headers=headers)
            if 200 <= resp.status_code < 300:
                return True, ""
            error = f"HTTP {resp.status_code}"
            logger.warning("Webhook returned HTTP %d for %s", resp.status_code, webhook.url)
            return False, error
        except httpx.TimeoutException:
            logger.warning("Webhook timeout for %s", webhook.url)
            return False, "Timeout"
        except httpx.ConnectError as exc:
            logger.warning("Webhook connection error for %s: %s", webhook.url, exc)
            return False, f"ConnectError: {exc}"
        except httpx.NetworkError as exc:
            logger.warning("Webhook network error for %s: %s", webhook.url, exc)
            return False, f"NetworkError: {exc}"
        except Exception as exc:
            logger.warning("Webhook unexpected error for %s: %s", webhook.url, exc)
            return False, f"Unexpected: {exc}"


# Global instance (initialized in main.py lifespan)
_push_engine: Optional[PushDeliveryEngine] = None


def get_push_engine() -> Optional[PushDeliveryEngine]:
    return _push_engine


def set_push_engine(engine: Optional[PushDeliveryEngine]) -> None:
    global _push_engine
    _push_engine = engine
