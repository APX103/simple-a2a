"""End-to-end smoke tests for Agent Bus backends."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

BASE_URL = "http://127.0.0.1:18087"


def api(method: str, path: str, headers: dict = None, body: dict = None):
    url = f"{BASE_URL}{path}"
    data = None
    if body:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
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


def ok(status, data, label):
    if status >= 400:
        print(f"  ❌ {label}: HTTP {status} -> {data}")
        return False
    print(f"  ✅ {label}")
    return True


def run_suite(backend_name: str, env: dict):
    print(f"\n========== Testing {backend_name} ==========")

    # 1. Start server
    env_full = {**os.environ, **env}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agent_bus.main:app", "--host", "127.0.0.1", "--port", "18087"],
        cwd="/Users/apx103/work/agent_communicator",
        env=env_full,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for server to come up
    for _ in range(30):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"{BASE_URL}/health", timeout=1)
            break
        except Exception:
            pass
    else:
        stdout, stderr = proc.communicate(timeout=2)
        print("  ❌ Server failed to start")
        if stdout:
            print("  stdout:", stdout.decode()[-500:])
        if stderr:
            print("  stderr:", stderr.decode()[-500:])
        proc.kill()
        return False

    try:
        # 2. Empty stats
        status, data = api("GET", "/admin/stats")
        if not ok(status, data, "empty stats"):
            return False
        assert data["total_agents"] == 0, f"expected 0 agents, got {data}"

        # 3. Register Alice
        status, alice = api("POST", "/v1/switchboard/register", body={
            "name": "alice",
            "capabilities": ["python"],
            "limitations": [],
            "announcement": "hi",
            "labels": ["team:backend"],
        })
        if not ok(status, alice, "register alice"):
            return False
        alice_id = alice["agent_id"]
        alice_token = alice["token"]

        # 4. Register Bob
        status, bob = api("POST", "/v1/switchboard/register", body={
            "name": "bob",
            "capabilities": ["review"],
            "limitations": [],
            "announcement": "ho",
            "labels": ["team:frontend"],
        })
        if not ok(status, bob, "register bob"):
            return False
        bob_id = bob["agent_id"]
        bob_token = bob["token"]

        # 5. List agents
        status, agents = api("GET", "/v1/switchboard/agents")
        if not ok(status, agents, "list agents"):
            return False
        assert len(agents) == 2, f"expected 2 agents, got {len(agents)}"

        # 6. Label filter
        status, filtered = api("GET", "/v1/switchboard/agents?label=team:backend")
        if not ok(status, filtered, "filter by label"):
            return False
        assert len(filtered) == 1 and filtered[0]["name"] == "alice", f"label filter wrong: {filtered}"

        # 7. Alice -> Bob P2P
        status, send_res = api("POST", "/v1/switchboard/send", headers={
            "X-Agent-Id": alice_id,
            "X-Token": alice_token,
        }, body={
            "to": bob_id,
            "msg_type": "text",
            "content": {"summary": "hello bob", "detail": {"foo": 1}},
        })
        if not ok(status, send_res, "alice sends to bob"):
            return False

        # 8. Bob inbox (auto-mark read)
        status, inbox = api("GET", "/v1/switchboard/inbox", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, inbox, "bob inbox"):
            return False
        assert len(inbox) == 1, f"expected 1 msg, got {len(inbox)}"
        msg_id = inbox[0]["msg_id"]

        # 9. Bob unread-only (should be empty after auto-read)
        status, unread = api("GET", "/v1/switchboard/inbox?unread_only=true", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, unread, "bob unread-only"):
            return False
        assert len(unread) == 0, f"expected 0 unread after auto-read, got {len(unread)}"

        # 10. Mark unread again by resetting read_at (simulate new msg)
        # Send another message
        status, _ = api("POST", "/v1/switchboard/send", headers={
            "X-Agent-Id": alice_id,
            "X-Token": alice_token,
        }, body={
            "to": bob_id,
            "msg_type": "code_review",
            "content": {"summary": "review this", "detail": None},
        })
        if not ok(status, _, "alice sends 2nd msg"):
            return False

        # 11. unread_only should show 1
        status, unread2 = api("GET", "/v1/switchboard/inbox?unread_only=true", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, unread2, "bob unread-only after 2nd msg"):
            return False
        assert len(unread2) == 1, f"expected 1 unread, got {len(unread2)}"

        # 12. mark-all-read
        status, mar = api("POST", "/v1/switchboard/messages/read-all", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, mar, "mark all read"):
            return False
        assert mar.get("marked_count", 0) >= 1, f"expected marked_count >= 1, got {mar}"

        # 13. Human confirm flow
        status, _ = api("POST", "/v1/switchboard/send", headers={
            "X-Agent-Id": alice_id,
            "X-Token": alice_token,
        }, body={
            "to": bob_id,
            "msg_type": "task",
            "content": {"summary": "deploy to prod?", "detail": None},
            "require_human_confirm": True,
        })
        if not ok(status, _, "send human-confirm msg"):
            return False

        status, inbox3 = api("GET", "/v1/switchboard/inbox", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        confirm_msg = [m for m in inbox3 if m.get("require_human_confirm")][0]
        status, conf = api("POST", f"/v1/switchboard/messages/{confirm_msg['msg_id']}/confirm", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        }, body={"decision": "approve", "comment": "go ahead"})
        if not ok(status, conf, "human confirm"):
            return False
        assert conf["human_confirmed"] is True, f"expected approved, got {conf}"

        # 14. Group flow
        status, grp = api("POST", "/v1/switchboard/groups", headers={
            "X-Agent-Id": alice_id,
            "X-Token": alice_token,
        }, body={"name": "backend-critique"})
        if not ok(status, grp, "create group"):
            return False
        group_id = grp["group_id"]

        status, _ = api("POST", f"/v1/switchboard/groups/{group_id}/join", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, _, "bob joins group"):
            return False

        status, _ = api("POST", "/v1/switchboard/send", headers={
            "X-Agent-Id": alice_id,
            "X-Token": alice_token,
        }, body={
            "to": group_id,
            "msg_type": "task",
            "content": {"summary": "group task", "detail": None},
        })
        if not ok(status, _, "group broadcast"):
            return False

        status, bob_inbox = api("GET", "/v1/switchboard/inbox", headers={
            "X-Agent-Id": bob_id,
            "X-Token": bob_token,
        })
        if not ok(status, bob_inbox, "bob inbox after group msg"):
            return False
        group_msgs = [m for m in bob_inbox if m["msg_type"] == "task" and m["to"] == group_id]
        assert len(group_msgs) >= 1, f"expected bob to receive group msg, got {bob_inbox}"

        # 15. Admin APIs
        status, stats = api("GET", "/admin/stats")
        if not ok(status, stats, "admin stats"):
            return False
        assert stats["total_agents"] == 2, f"stats wrong: {stats}"
        assert stats["total_messages"] >= 5, f"expected >=5 messages, got {stats}"

        status, admin_agents = api("GET", "/admin/agents")
        if not ok(status, admin_agents, "admin agents"):
            return False
        assert len(admin_agents) == 2, f"expected 2 admin agents, got {len(admin_agents)}"
        assert "unread_count" in admin_agents[0], f"missing unread_count: {admin_agents[0].keys()}"

        status, admin_msgs = api("GET", "/admin/messages")
        if not ok(status, admin_msgs, "admin messages"):
            return False
        assert len(admin_msgs) >= 5, f"expected >=5 admin messages, got {len(admin_msgs)}"

        # 16. Patch label
        status, patch = api("PATCH", f"/admin/agents/{alice_id}", body={"labels": ["team:backend", "lang:python"]})
        if not ok(status, patch, "patch alice labels"):
            return False
        assert patch["labels"] == ["team:backend", "lang:python"], f"patch wrong: {patch}"

        status, alice_check = api("GET", f"/v1/switchboard/agents/{alice_id}")
        if not ok(status, alice_check, "get alice after patch"):
            return False
        assert "lang:python" in alice_check["labels"], f"label not persisted: {alice_check}"

        # 17. Unregister Bob
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

        # 18. Front-end page
        req = urllib.request.Request(f"{BASE_URL}/admin-page/")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read().decode("utf-8")
                assert "Agent Bus Dashboard" in html, "Dashboard title missing"
                print("  ✅ admin-page HTML")
        except Exception as e:
            print(f"  ❌ admin-page HTML: {e}")
            return False

        print(f"\n🎉 {backend_name} ALL PASSED")
        return True

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    import json

    results = []

    # Test MemoryStore
    results.append(("MemoryStore", run_suite("MemoryStore", {})))

    # Clean MongoDB before testing
    print("\n🧹 Cleaning MongoDB test data...")
    from pymongo import MongoClient
    client = MongoClient("mongodb://localhost:27017")
    client.drop_database("agent_bus")
    client.close()

    # Test MongoDB
    results.append(("MongoDB", run_suite("MongoDB", {"MONGODB_URL": "mongodb://localhost:27017"})))

    print("\n========== SUMMARY ==========")
    all_pass = True
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"  {status}: {name}")
        if not passed:
            all_pass = False

    sys.exit(0 if all_pass else 1)
