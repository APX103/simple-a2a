"""
Agent Bus 客户端示例 — 演示 Agent 如何注册、发消息、轮询收件箱、使用群组。
可直接运行: python examples/client_demo.py
"""
from __future__ import annotations

import os
import sys
import time

import requests

BASE_URL = os.getenv("AGENT_BUS_URL", "http://127.0.0.1:10080/v1/switchboard")


def register(name: str, capabilities: list[str], limitations: list[str], announcement: str, labels: list[str] = None) -> dict:
    r = requests.post(f"{BASE_URL}/register", json={
        "name": name,
        "capabilities": capabilities,
        "limitations": limitations,
        "announcement": announcement,
        "labels": labels or [],
    })
    r.raise_for_status()
    return r.json()


def send_msg(token: str, agent_id: str, to: str, msg_type: str, summary: str, detail=None, require_human_confirm: bool = False):
    r = requests.post(f"{BASE_URL}/send", json={
        "to": to,
        "msg_type": msg_type,
        "content": {"summary": summary, "detail": detail},
        "require_human_confirm": require_human_confirm,
    }, headers={"X-Agent-Id": agent_id, "X-Token": token})
    r.raise_for_status()
    return r.json()


def poll_inbox(token: str, agent_id: str, since: float = 0) -> list[dict]:
    r = requests.get(f"{BASE_URL}/inbox", params={"since": since}, headers={"X-Agent-Id": agent_id, "X-Token": token})
    r.raise_for_status()
    return r.json()


def create_group(token: str, agent_id: str, name: str) -> dict:
    r = requests.post(f"{BASE_URL}/groups", json={"name": name}, headers={"X-Agent-Id": agent_id, "X-Token": token})
    r.raise_for_status()
    return r.json()


def join_group(token: str, agent_id: str, group_id: str):
    r = requests.post(f"{BASE_URL}/groups/{group_id}/join", headers={"X-Agent-Id": agent_id, "X-Token": token})
    r.raise_for_status()
    return r.json()


def list_agents() -> list[dict]:
    r = requests.get(f"{BASE_URL}/agents")
    r.raise_for_status()
    return r.json()


def main():
    print("=== Agent Bus 客户端演示 ===\n")

    # 1. Alice 注册
    print("[1/6] Alice 注册...")
    alice = register(
        name="alice-coder",
        capabilities=["python_debug", "refactor", "code_review"],
        limitations=["不修改生产环境", "不处理前端 CSS"],
        announcement="我是 alice-coder，擅长 Python 调试与重构，遇到代码审查可以找我，但我不会碰前端样式。",
        labels=["team:backend", "lang:python"],
    )
    print(f"  -> agent_id={alice['agent_id']}, token=***")

    # 2. Bob 注册
    print("[2/6] Bob 注册...")
    bob = register(
        name="bob-reviewer",
        capabilities=["code_review", "architecture_design"],
        limitations=["不执行危险 Shell", "不写测试用例"],
        announcement="我是 bob-reviewer，专注代码审查与架构设计，遇到高风险变更可以找我，但我不写测试。",
        labels=["team:backend", "lang:typescript"],
    )
    print(f"  -> agent_id={bob['agent_id']}, token=***")

    # 3. Alice 查看在线 Agent 列表（含能力边界）
    print("[3/6] 查看在线 Agent 列表...")
    agents = list_agents()
    for a in agents:
        print(f"  - {a['name']} ({a['agent_id']}): capabilities={a['capabilities']}, limitations={a['limitations']}")

    # 4. Alice 给 Bob 发点对点消息（结构化 code_review）
    print("[4/6] Alice 发送 code_review 请求给 Bob...")
    diff = {
        "file": "app/main.py",
        "changes": [
            {"line": 42, "type": "add", "content": "    validate_input(data)"},
            {"line": 43, "type": "remove", "content": "    pass"},
        ]
    }
    send_msg(
        token=alice["token"],
        agent_id=alice["agent_id"],
        to=bob["agent_id"],
        msg_type="code_review",
        summary="请 review 这段新增输入校验的 diff",
        detail=diff,
        require_human_confirm=False,
    )
    print("  -> 已发送")

    # 5. Bob 轮询收件箱
    print("[5/6] Bob 轮询 inbox...")
    time.sleep(0.2)
    msgs = poll_inbox(bob["token"], bob["agent_id"], since=0)
    for m in msgs:
        print(f"  [{m['msg_type']}] from={m['from_agent']} | summary={m['content']['summary']}")
        if m["content"].get("detail"):
            print(f"    detail={m['content']['detail']}")

    # 6. 创建群组并广播消息
    print("[6/6] 创建 backend-critique 群组并广播...")
    group = create_group(alice["token"], alice["agent_id"], "backend-critique")
    print(f"  -> group_id={group['group_id']}")
    join_group(bob["token"], bob["agent_id"], group["group_id"])
    print("  -> Bob 加入群组")

    send_msg(
        token=alice["token"],
        agent_id=alice["agent_id"],
        to=group["group_id"],
        msg_type="task",
        summary="接口契约变更通知: /api/v1/users 返回值增加 avatar 字段",
        detail={"endpoint": "/api/v1/users", "added_field": "avatar"},
    )
    print("  -> 群消息已广播")

    time.sleep(0.2)
    bob_msgs = poll_inbox(bob["token"], bob["agent_id"], since=0)
    group_msgs = [m for m in bob_msgs if m.get("msg_type") == "task" and m.get("to") == group["group_id"]]
    for m in group_msgs:
        print(f"  [group task] summary={m['content']['summary']}")

    print("\n=== 演示完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
