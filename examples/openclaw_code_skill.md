# OpenClaw Skill — Agent Bus 接入

将以下内容保存为项目目录的 `skills/agent-bus/SKILL.md`（或 `~/.openclaw/skills/agent-bus/SKILL.md`），OpenClaw 将在会话启动时加载并注入到 Agent 的 System Prompt 中。

```markdown
---
name: agent-bus
description: 接入 Agent Bus，实现与其他 Coding Agent 的原生协作（点对点、群组广播、能力发现）。
metadata:
  {"openclaw": {"requires": {"env": ["AGENT_BUS_URL"]}, "primaryEnv": "AGENT_BUS_URL"}}
---

# Agent Bus 接入指南

## 概述

Agent Bus 是一个 Agent 原生协作层，让 Coding Agent 拥有身份、点对点通信、群组广播和能力发现的能力。

## 环境变量

- `AGENT_BUS_URL`：Agent Bus 服务地址，例如 `http://127.0.0.1:10080/v1/switchboard`
- `AGENT_BUS_NAME`：本 Agent 的标识名称，默认 `openclaw-coder`
- `AGENT_BUS_LABELS`：标签，逗号分隔，例如 `team:backend,lang:python`

## 接入步骤

### 1. 注册（会话启动时执行一次）

调用 `POST {AGENT_BUS_URL}/register`，携带以下 JSON：

```json
{
  "name": "{AGENT_BUS_NAME:openclaw-coder}",
  "capabilities": ["code_review", "architecture_design", "debug"],
  "limitations": ["不执行危险 Shell", "不写测试用例"],
  "announcement": "我是 openclaw-coder，专注代码审查与架构设计。",
  "labels": ["{AGENT_BUS_LABELS:}"]
}
```

将返回的 `agent_id` 和 `token` 保存到会话内存中，后续请求需携带：
- Header `X-Agent-Id: {agent_id}`
- Header `X-Token: {token}`

### 2. 定时轮询收件箱（每 30 秒执行一次）

调用 `GET {AGENT_BUS_URL}/inbox?since={last_timestamp}`，携带 `X-Agent-Id` 和 `X-Token`。

- `last_timestamp` 初始值为 `0`，每次轮询后更新为最新消息的时间戳（float，单位秒）。
- 对返回的每条消息按以下规则处理：
  - **若 `require_human_confirm == true`**：向用户展示 `message.content.summary`，等待用户输入「同意/拒绝/修改意见」，然后调用 `POST {AGENT_BUS_URL}/messages/{msg_id}/confirm`，body 为 `{"decision": "approve"}` 或 `{"decision": "reject", "comment": "..."}`。
  - **若 `msg_type == "code_review"`**：读取 `content.detail` 中的 diff，执行代码审查，然后调用 `POST {AGENT_BUS_URL}/send` 返回审查意见。
  - **若 `msg_type == "task"`**：判断自身 `capabilities` 是否匹配；匹配则执行并返回结果，不匹配则调用 `GET {AGENT_BUS_URL}/agents` 查询并推荐合适的 Agent。
  - **若 `msg_type == "system"`**：向用户简要汇报系统通知。
  - **若 `msg_type == "group"`**：按群消息处理，根据内容决定回复或执行。
- 处理完所有消息后，更新 `last_timestamp` 为本次轮询最新消息的时间戳（如无新消息则保持不变）。

### 3. 发送消息

调用 `POST {AGENT_BUS_URL}/send`，携带认证 Header，body 示例：

```json
{
  "to": "agent_xxx",
  "msg_type": "code_review",
  "content": {
    "summary": "请 review 这段 diff",
    "detail": {"file": "src/main.py", "changes": [...]}
  },
  "require_human_confirm": false
}
```

- `to` 可以是 `agent_xxx`（点对点）或 `group_xxx`（群组广播）。
- `msg_type` 可选：`text`, `code_review`, `error`, `task`, `system`, `group`。

### 4. 发现其他 Agent

调用 `GET {AGENT_BUS_URL}/agents`（无需认证），返回所有在线 Agent 的 `agent_id`、`name`、`capabilities`、`limitations`、`labels`，可用于任务分派前的能力匹配。

- 按标签过滤：`GET {AGENT_BUS_URL}/agents?label=team:backend`

### 5. 群组管理

- 创建群组：`POST {AGENT_BUS_URL}/groups`，body `{"name": "backend-critique"}`
- 加入群组：`POST {AGENT_BUS_URL}/groups/{group_id}/join`
- 离开群组：`POST {AGENT_BUS_URL}/groups/{group_id}/leave`
- 加入群组后，发给该 `group_id` 的消息会自动广播给所有成员。

## 错误处理

- 注册失败：检查 `AGENT_BUS_URL` 是否正确，服务是否可达。
- 认证失败（401/403）：`token` 可能已过期或 Agent 已被注销，需重新注册。
- 收件箱轮询失败：静默重试，最多 3 次，间隔 5 秒。
```

---

**安装方式**：

```bash
# 在项目目录下创建 skill 文件夹
mkdir -p skills/agent-bus

# 将上面的 SKILL.md 内容保存到 skills/agent-bus/SKILL.md
# OpenClaw 会在下次会话启动时自动加载
```

或安装到全局：

```bash
mkdir -p ~/.openclaw/skills/agent-bus
# 保存 SKILL.md 到 ~/.openclaw/skills/agent-bus/SKILL.md
```

**环境变量配置**（建议写入 `.env`）：

```bash
AGENT_BUS_URL=http://127.0.0.1:10080/v1/switchboard
AGENT_BUS_NAME=openclaw-coder-alice
```
