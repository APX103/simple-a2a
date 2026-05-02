"""End-to-end smoke tests for Agent Bus backends."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

BASE_URL = "http://127.0.0.1:18087"
WEBHOOK_URL = "http://127.0.0.1:18088"
ADMIN_TOKEN = "test-admin-token-12345"
PROJECT_ROOT = str(Path(__file__).parent.parent)


def api(method: str, path: str, headers: dict = None, body: dict = None):
    url = f"{BASE_URL}{path}"
    data = None
    if body:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if path.startswith("/admin/"):
        req.add_header("X-Admin-Token", ADMIN_TOKEN)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body_text)
        except Exception:
            return e.code, {"raw": body_text}


def webhook_api(method: str, path: str, body: dict = None):
    url = f"{WEBHOOK_URL}{path}"
    data = None
    if body:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body_text)
        except Exception:
            return e.code, {"raw": body_text}


def ok(status, data, label):
    if status >= 400:
        print(f"  ❌ {label}: HTTP {status} -> {data}")
        return False
    print(f"  ✅ {label}")
    return True


WEBHOOK_APP = '''
from fastapi import FastAPI, Request
import uvicorn

app = FastAPI()
_events = []
_status_code = 200

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    _events.append({"headers": dict(request.headers), "body": body})
    from fastapi.responses import JSONResponse
    return JSONResponse(content={"received": True}, status_code=_status_code)

@app.get("/events")
async def events():
    return {"events": _events}

@app.get("/config")
async def config(code: int):
    global _status_code
    _status_code = code
    return {"status_code": _status_code}

@app.post("/reset")
async def reset():
    global _events
    _events = []
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18088, log_level="warning")
'''


def run_suite(backend_name: str, env: dict):
    print(f"\n========== Testing {backend_name} ==========")

    env_full = {**os.environ, **env, "AGENT_BUS_ADMIN_TOKEN": ADMIN_TOKEN}

    # Write webhook receiver temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(WEBHOOK_APP)
        webhook_path = f.name

    proc = None
    webhook_proc = None

    try:
        # Start webhook receiver
        webhook_proc = subprocess.Popen(
            [sys.executable, webhook_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for webhook server
        for _ in range(50):
            time.sleep(0.2)
            try:
                urllib.request.urlopen(f"{WEBHOOK_URL}/events", timeout=1)
                break
            except Exception:
                pass
        else:
            print("  ❌ Webhook server failed to start")
            return False

        # Start main server
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "agent_bus.main:app", "--host", "127.0.0.1", "--port", "18087"],
            cwd=PROJECT_ROOT,
            env=env_full,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for server to come up
        for _ in range(50):
            time.sleep(0.2)
            try:
                urllib.request.urlopen(f"{BASE_URL}/v1/switchboard/health", timeout=1)
                break
            except Exception:
                pass
        else:
            print("  ❌ Server failed to start")
            try:
                stdout, stderr = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                stdout, stderr = b"", b""
            if stdout:
                print("  stdout:", stdout.decode()[-800:])
            if stderr:
                print("  stderr:", stderr.decode()[-800:])
            return False

        # Reset webhook state
        webhook_api("POST", "/reset")

        # 1. Empty stats
        status, data = api("GET", "/admin/stats")
        if not ok(status, data, "empty stats"):
            return False
        assert data["total_agents"] == 0, f"expected 0 agents, got {data}"

        # 2. Register Alice (no webhook)
        status, alice = api("POST", "/v1/switchboard/register", body={
            "name": "alice",
            "capabilities": ["python"],
            "limitations": [],
            "announcement": "hi",
            "labels": ["team:backend"],
        })
        if not ok(status, alice, "register alice (no webhook)"):
            return False
        alice_id = alice["agent_id"]
        alice_token = alice["token"]
        assert alice["card"].get("webhook") is None

        # 3. Register Bob (with webhook)
        status, bob = api("POST", "/v1/switchboard/register", body={
            "name": "bob",
            "capabilities": ["review"],
            "limitations": [],
            "announcement": "ho",
            "labels": ["team:frontend"],
            "webhook": {
                "url": f"{WEBHOOK_URL}/webhook",
                "token": "whk_bob_secret",
                "auth_scheme": "header_token",
                "enabled": True,
            },
            "delivery_preference": "both",
        })
        if not ok(status, bob, "register bob (with webhook)"):
            return False
        bob_id = bob["agent_id"]
        bob_token = bob["token"]
        assert bob["card"]["webhook"]["url"] == f"{WEBHOOK_URL}/webhook"
        assert bob["card"]["delivery_preference"] == "both"

        # 4. Verify webhook management endpoints
        status, wh = api("GET", "/v1/switchboard/webhook", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, wh, "get bob webhook"):
            return False
        assert wh["webhook"]["url"] == f"{WEBHOOK_URL}/webhook"

        # 5. SSE streaming test
        sse_events = []
        sse_conn = None

        def read_sse():
            nonlocal sse_conn
            try:
                sse_conn = http.client.HTTPConnection("127.0.0.1", 18087)
                sse_conn.request(
                    "GET", "/v1/switchboard/stream",
                    headers={"X-Agent-Id": bob_id, "X-Token": bob_token},
                )
                resp = sse_conn.getresponse()
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    if line.startswith(b"data: "):
                        sse_events.append(json.loads(line[6:].decode()))
            except Exception as e:
                print(f"  SSE reader error: {e}")
            finally:
                if sse_conn:
                    sse_conn.close()

        sse_thread = threading.Thread(target=read_sse, daemon=True)
        sse_thread.start()
        # Wait for SSE connection to be established (poll health endpoint)
        for _ in range(20):
            time.sleep(0.2)
            _, h = api("GET", "/v1/switchboard/health")
            if h.get("sse_connections", 0) >= 1:
                break
        else:
            print("  ⚠️ SSE connection not detected in health, continuing anyway")
        time.sleep(0.5)

        # 6. Alice -> Bob P2P with Push + SSE
        status, send_res = api("POST", "/v1/switchboard/send", headers={
            "X-Agent-Id": alice_id,
            "X-Token": alice_token,
        }, body={
            "to": bob_id,
            "msg_type": "text",
            "content": {"summary": "hello bob via push", "detail": {"foo": 1}},
        })
        if not ok(status, send_res, "alice sends to bob (push)"):
            return False
        assert "push" in send_res.get("delivery_channels", []), f"expected push in channels, got {send_res}"
        msg_id = send_res["msg_id"]

        # Give SSE some time to receive
        time.sleep(1.0)

        # 7. Verify SSE received the message in real-time
        assert len(sse_events) >= 1, f"expected SSE events, got {sse_events}"
        sse_body = sse_events[0]
        assert sse_body["event"] == "message.received"
        assert sse_body["message"]["msg_id"] == msg_id

        # Close SSE connection gracefully
        if sse_conn:
            sse_conn.close()

        # 8. Verify webhook also received the push
        time.sleep(1.5)  # wait for webhook push scheduler
        status, wh_events = webhook_api("GET", "/events")
        if not ok(status, wh_events, "webhook events"):
            return False
        events = wh_events.get("events", [])
        assert len(events) >= 1, f"expected webhook events, got {events}"
        push_body = events[0]["body"]
        assert push_body["event"] == "message.received"
        assert push_body["message"]["msg_id"] == msg_id
        # Verify auth header was sent
        assert events[0]["headers"].get("x-webhook-token") == "whk_bob_secret"

        # 9. Verify delivery status
        status, delivery = api("GET", f"/admin/delivery?agent_id={bob_id}&msg_id={msg_id}")
        if not ok(status, delivery, "delivery status after push"):
            return False
        records = delivery.get("records", [])
        assert len(records) == 1, f"expected 1 delivery record, got {records}"
        assert records[0]["status"] == "delivered", f"expected delivered, got {records[0]}"
        assert records[0]["channel"] == "push"

        # 10. Bob inbox — Pull fallback still works
        status, inbox = api("GET", "/v1/switchboard/inbox", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, inbox, "bob inbox after push"):
            return False
        assert len(inbox) >= 1, f"expected at least 1 msg in inbox, got {inbox}"
        inbox_msg_ids = [m["msg_id"] for m in inbox]
        assert msg_id in inbox_msg_ids, f"msg {msg_id} not in inbox {inbox_msg_ids}"

        # 11. Reset webhook to fail (500)
        webhook_api("GET", "/config?code=500")
        webhook_api("POST", "/reset")

        # 12. Alice -> Bob again (Push will fail)
        status, send_res2 = api("POST", "/v1/switchboard/send", headers={
            "X-Agent-Id": alice_id,
            "X-Token": alice_token,
        }, body={
            "to": bob_id,
            "msg_type": "code_review",
            "content": {"summary": "review this", "detail": None},
        })
        if not ok(status, send_res2, "alice sends 2nd msg (push fail)"):
            return False
        msg_id2 = send_res2["msg_id"]

        # Wait for retries (backoff: 2s + 4s + ...), give enough time
        time.sleep(15)

        # 13. Verify webhook received attempts
        status, wh_events2 = webhook_api("GET", "/events")
        if not ok(status, wh_events2, "webhook events after failures"):
            return False
        fail_events = [e for e in wh_events2.get("events", []) if e["body"]["message"]["msg_id"] == msg_id2]
        assert len(fail_events) >= 1, f"expected at least 1 failed push attempt, got {fail_events}"

        # 14. Verify message moved to DLQ
        status, dlq = api("GET", f"/admin/dlq?agent_id={bob_id}")
        if not ok(status, dlq, "dlq after push failures"):
            return False
        dlq_items = dlq.get("items", [])
        dlq_msg_ids = [item["original_msg"]["msg_id"] for item in dlq_items]
        assert msg_id2 in dlq_msg_ids, f"msg {msg_id2} not in DLQ {dlq_msg_ids}"

        # 15. Bob can still pull the failed message from inbox
        status, inbox2 = api("GET", "/v1/switchboard/inbox", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, inbox2, "bob inbox after push fail"):
            return False
        inbox2_msg_ids = [m["msg_id"] for m in inbox2]
        assert msg_id2 in inbox2_msg_ids, f"msg {msg_id2} not in inbox {inbox2_msg_ids}"

        # 16. Restore webhook to success, retry DLQ manually
        webhook_api("GET", "/config?code=200")
        status, retry_res = api("POST", f"/admin/dlq/{msg_id2}/retry?agent_id={bob_id}")
        if not ok(status, retry_res, "retry dlq"):
            return False

        # Wait for retry push
        time.sleep(2.5)

        # 17. Verify DLQ retry succeeded
        status, wh_events3 = webhook_api("GET", "/events")
        if not ok(status, wh_events3, "webhook events after dlq retry"):
            return False
        retry_events = [e for e in wh_events3.get("events", []) if e["body"]["message"]["msg_id"] == msg_id2]
        assert len(retry_events) >= 1, f"expected DLQ retry push, got {retry_events}"

        # 18. Delete webhook, verify Bob becomes pull-only
        status, _ = api("DELETE", "/v1/switchboard/webhook", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, _, "delete bob webhook"):
            return False

        status, wh_after = api("GET", "/v1/switchboard/webhook", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, wh_after, "get bob webhook after delete"):
            return False
        assert wh_after["webhook"] is None

        # 19. Send to Bob (now pull-only)
        status, send_res3 = api("POST", "/v1/switchboard/send", headers={
            "X-Agent-Id": alice_id,
            "X-Token": alice_token,
        }, body={
            "to": bob_id,
            "msg_type": "task",
            "content": {"summary": "task after webhook delete", "detail": None},
        })
        if not ok(status, send_res3, "alice sends to bob (pull only)"):
            return False
        assert "pull" in send_res3.get("delivery_channels", []), f"expected pull in channels, got {send_res3}"

        # 20. Admin stats still work
        status, stats = api("GET", "/admin/stats")
        if not ok(status, stats, "admin stats"):
            return False
        assert stats["total_agents"] == 2, f"stats wrong: {stats}"

        # 21. Unregister Bob
        status, _ = api("DELETE", f"/v1/switchboard/agents/{bob_id}", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, _, "unregister bob"):
            return False

        status, agents_after = api("GET", "/v1/switchboard/agents")
        if not ok(status, agents_after, "agents after unregister"):
            return False
        assert len(agents_after) == 1, f"expected 1 agent after unregister, got {len(agents_after)}"

        print(f"\n🎉 {backend_name} ALL PASSED")
        return True

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        webhook_proc.terminate()
        try:
            webhook_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            webhook_proc.kill()
        os.unlink(webhook_path)


def test_redis_backend():
    """Pytest entry point for Redis backend E2E test."""
    # Clean Redis db 1 before testing
    try:
        import redis
        r = redis.from_url("redis://localhost:6379/1", decode_responses=True)
        r.flushdb()
        r.close()
    except Exception as e:
        pytest.skip(f"Redis not available: {e}")

    passed = run_suite("RedisStore", {
        "REDIS_URL": "redis://localhost:6379/1",
        "AGENT_BUS_PUSH_ENABLED": "true",
    })
    assert passed, "RedisStore E2E tests failed"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v", "-s"]))
