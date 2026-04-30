"""Pydantic models for Agent Bus."""
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class AgentCard(BaseModel):
    agent_id: str = Field(..., description="系统分配的唯一 ID")
    name: str = Field(..., description="Agent 标识，如 alice-coder")
    capabilities: list[str] = Field(default_factory=list, description="能力清单")
    limitations: list[str] = Field(default_factory=list, description="局限清单")
    announcement: str = Field(default="", description="一句话自我介绍")
    labels: list[str] = Field(default_factory=list, description="标签，如 team:backend, lang:python")
    online: bool = Field(default=True, description="是否在线")
    registered_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)


class MessageContent(BaseModel):
    summary: str = Field(..., description="人类可读摘要")
    detail: Any = Field(default=None, description="结构化数据，如 diff、日志、AST")


class Message(BaseModel):
    msg_id: str
    msg_type: Literal["text", "code_review", "error", "task", "system", "group"]
    from_agent: str
    to: str  # agent_id or group_id
    content: MessageContent
    require_human_confirm: bool = False
    human_confirmed: Optional[bool] = None  # None=未处理, True=同意, False=拒绝
    read_at: Optional[datetime] = None  # None=未读
    delivered_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Group(BaseModel):
    group_id: str
    name: str
    members: list[str] = Field(default_factory=list)  # agent_ids
    created_by: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RegisterRequest(BaseModel):
    name: str
    capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    announcement: str = ""
    labels: list[str] = Field(default_factory=list, description="标签，如 team:backend, lang:python")


class RegisterResponse(BaseModel):
    agent_id: str
    token: str
    card: AgentCard


class SendRequest(BaseModel):
    to: str  # agent_id or group_id
    msg_type: Literal["text", "code_review", "error", "task", "system", "group"]
    content: MessageContent
    require_human_confirm: bool = False


class SendResponse(BaseModel):
    msg_id: str
    timestamp: datetime


class CreateGroupRequest(BaseModel):
    name: str


class HumanConfirmRequest(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str = ""


class UpdateLabelsRequest(BaseModel):
    labels: list[str]
