"""Agent Bus MVP — FastAPI service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from agent_bus.models import (
    AgentCard,
    CreateGroupRequest,
    Group,
    HumanConfirmRequest,
    Message,
    RegisterRequest,
    RegisterResponse,
    SendRequest,
    SendResponse,
)
from agent_bus.store import store

app = FastAPI(title="Agent Bus", version="1.0.0", description="Agent 原生协作层")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix="/v1/switchboard")


# ---------- Auth dependency ----------

async def require_agent(x_agent_id: Annotated[Optional[str], Header()] = None,
                        x_token: Annotated[Optional[str], Header()] = None) -> str:
    if not x_agent_id or not x_token:
        raise HTTPException(status_code=401, detail="Missing X-Agent-Id or X-Token header")
    if not store.verify_token(x_agent_id, x_token):
        raise HTTPException(status_code=403, detail="Invalid token")
    store.touch_agent(x_agent_id)
    return x_agent_id


AgentId = Annotated[str, Depends(require_agent)]


# ---------- Registry ----------

@router.post("/register", response_model=RegisterResponse)
async def register(req: RegisterRequest):
    agent_id, token, card = store.register_agent(
        name=req.name,
        capabilities=req.capabilities,
        limitations=req.limitations,
        announcement=req.announcement,
    )
    # broadcast system message to all other agents
    for other in store.list_agents():
        if other.agent_id == agent_id:
            continue
        store.add_message(Message(
            msg_id=f"sys_{datetime.now(timezone.utc).timestamp()}",
            msg_type="system",
            from_agent="system",
            to=other.agent_id,
            content={"summary": f"Agent 上线: {card.name}", "detail": card.model_dump()},
        ))
    return RegisterResponse(agent_id=agent_id, token=token, card=card)


@router.get("/agents", response_model=list[AgentCard])
async def list_agents():
    return store.list_agents()


@router.get("/agents/{agent_id}", response_model=AgentCard)
async def get_agent(agent_id: str):
    card = store.get_agent(agent_id)
    if not card:
        raise HTTPException(status_code=404, detail="Agent not found")
    return card


@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, auth: AgentId):
    # agents can only unregister themselves
    if auth != agent_id:
        raise HTTPException(status_code=403, detail="Can only unregister yourself")
    if store.unregister_agent(agent_id):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Agent not found")


# ---------- Message Bus ----------

@router.post("/send", response_model=SendResponse)
async def send_message(req: SendRequest, from_agent: AgentId):
    msg_id = f"msg_{datetime.now(timezone.utc).timestamp()}"
    msg = Message(
        msg_id=msg_id,
        msg_type=req.msg_type,
        from_agent=from_agent,
        to=req.to,
        content=req.content,
        require_human_confirm=req.require_human_confirm,
    )

    # validate recipient
    if req.to.startswith("agent_"):
        if not store.get_agent(req.to):
            raise HTTPException(status_code=404, detail="Recipient agent not found")
    elif req.to.startswith("group_"):
        if not store.get_group(req.to):
            raise HTTPException(status_code=404, detail="Group not found")
    else:
        raise HTTPException(status_code=400, detail="Invalid recipient ID")

    store.add_message(msg)
    return SendResponse(msg_id=msg_id, timestamp=msg.timestamp)


@router.get("/inbox", response_model=list[Message])
async def get_inbox(
    agent: AgentId,
    since: Annotated[Optional[float], Query()] = None,
):
    since_dt = datetime.utcfromtimestamp(since) if since is not None else None
    return store.get_inbox(agent, since=since_dt)


@router.post("/messages/{msg_id}/confirm")
async def human_confirm(msg_id: str, req: HumanConfirmRequest, agent: AgentId):
    msg = store.get_message(agent, msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found in your inbox")
    msg.human_confirmed = req.decision == "approve"
    return {
        "ok": True,
        "msg_id": msg_id,
        "human_confirmed": msg.human_confirmed,
        "comment": req.comment,
    }


# ---------- Groups ----------

@router.post("/groups", response_model=Group)
async def create_group(req: CreateGroupRequest, creator: AgentId):
    return store.create_group(name=req.name, created_by=creator)


@router.get("/groups", response_model=list[Group])
async def list_groups():
    return store.list_groups()


@router.get("/groups/{group_id}", response_model=Group)
async def get_group(group_id: str):
    group = store.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.post("/groups/{group_id}/join")
async def join_group(group_id: str, agent: AgentId):
    group = store.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if agent in group.members:
        raise HTTPException(status_code=400, detail="Already a member")
    store.join_group(group_id, agent)
    # notify existing members
    for member_id in group.members:
        if member_id == agent:
            continue
        store.add_message(Message(
            msg_id=f"sys_{datetime.now(timezone.utc).timestamp()}",
            msg_type="system",
            from_agent="system",
            to=member_id,
            content={"summary": f"Agent {agent} joined group {group.name}", "detail": {"group_id": group_id, "agent_id": agent}},
        ))
    return {"ok": True, "group_id": group_id}


@router.post("/groups/{group_id}/leave")
async def leave_group(group_id: str, agent: AgentId):
    group = store.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if agent not in group.members:
        raise HTTPException(status_code=400, detail="Not a member")
    store.leave_group(group_id, agent)
    return {"ok": True, "group_id": group_id}


@router.get("/groups/{group_id}/members", response_model=list[AgentCard])
async def get_group_members(group_id: str):
    group = store.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return [store.get_agent(mid) for mid in group.members if store.get_agent(mid)]


# ---------- Health ----------

SDK_CODE = '''\
"""Auto-generated Agent Bus SDK — save as agent_bus_sdk.py and import."""
from __future__ import annotations

import os
from typing import Any, Optional

import requests


class AgentBusClient:
    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = base_url or os.getenv("AGENT_BUS_URL", "http://127.0.0.1:10080/v1/switchboard")
        self.agent_id: Optional[str] = None
        self.token: Optional[str] = None

    def register(self, name: str, capabilities: list[str], limitations: list[str], announcement: str) -> dict:
        r = requests.post(f"{self.base_url}/register", json={
            "name": name,
            "capabilities": capabilities,
            "limitations": limitations,
            "announcement": announcement,
        })
        r.raise_for_status()
        data = r.json()
        self.agent_id = data["agent_id"]
        self.token = data["token"]
        return data

    def _headers(self) -> dict[str, str]:
        if not self.agent_id or not self.token:
            raise RuntimeError("Not registered. Call register() first.")
        return {"X-Agent-Id": self.agent_id, "X-Token": self.token}

    def send(self, to: str, msg_type: str, summary: str, detail: Any = None, require_human_confirm: bool = False) -> dict:
        r = requests.post(f"{self.base_url}/send", json={
            "to": to,
            "msg_type": msg_type,
            "content": {"summary": summary, "detail": detail},
            "require_human_confirm": require_human_confirm,
        }, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def inbox(self, since: float = 0) -> list[dict]:
        r = requests.get(f"{self.base_url}/inbox", params={"since": since}, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def agents(self) -> list[dict]:
        r = requests.get(f"{self.base_url}/agents")
        r.raise_for_status()
        return r.json()

    def create_group(self, name: str) -> dict:
        r = requests.post(f"{self.base_url}/groups", json={"name": name}, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def join_group(self, group_id: str) -> dict:
        r = requests.post(f"{self.base_url}/groups/{group_id}/join", headers=self._headers())
        r.raise_for_status()
        return r.json()
'''


@app.get("/")
async def root():
    return {
        "service": "Agent Bus",
        "version": "1.0.0",
        "description": "Agent 原生协作层 — 让 Coding Agent 拥有身份、广播、点对点、群组的通信能力",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
        "api_base": "/v1/switchboard",
        "installation": {
            "package_manager": "uv",
            "command": "uv add requests",
            "note": "客户端仅需 requests 库即可完整接入",
        },
        "registration": {
            "method": "POST",
            "endpoint": "/v1/switchboard/register",
            "headers": {"Content-Type": "application/json"},
            "body_example": {
                "name": "your-agent-name",
                "capabilities": ["code_review", "debug"],
                "limitations": ["不修改生产环境"],
                "announcement": "一句话自我介绍",
            },
            "response_example": {
                "agent_id": "agent_xxx",
                "token": "xxx",
                "card": {},
            },
        },
        "authentication": {
            "type": "header",
            "headers": {
                "X-Agent-Id": "{{agent_id}}",
                "X-Token": "{{token}}",
            },
        },
        "capabilities": [
            {"name": "发现 Agent", "method": "GET", "endpoint": "/v1/switchboard/agents"},
            {"name": "点对点发消息", "method": "POST", "endpoint": "/v1/switchboard/send"},
            {"name": "群组广播", "method": "POST", "endpoint": "/v1/switchboard/send"},
            {"name": "轮询收件箱", "method": "GET", "endpoint": "/v1/switchboard/inbox"},
            {"name": "创建/管理群组", "method": "POST/GET", "endpoint": "/v1/switchboard/groups"},
            {"name": "人类确认消息", "method": "POST", "endpoint": "/v1/switchboard/messages/{msg_id}/confirm"},
        ],
        "sdk_url": "/sdk",
    }


@app.get("/sdk")
async def sdk():
    return {"filename": "agent_bus_sdk.py", "language": "python", "code": SDK_CODE}


@router.get("/health")
async def health():
    return {"status": "ok", "agents": len(store.list_agents()), "groups": len(store.list_groups())}


app.include_router(router)
