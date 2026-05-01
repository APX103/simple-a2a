# Claude Code Skill — Agent Bus 接入

将以下内容保存为项目根目录的 `.claude/skills/agent_bus.yaml`，Claude Code 将在适当上下文自动执行注册与通信。

```yaml
name: agent_bus
version: "1.1.0"
description: |
  将当前 Claude Code 实例接入 Agent Bus 协作网络，使其具备与其他 Agent（如 Kimi Code）
  点对点通信、群组广播、能力发现的能力。
  
  Agent Bus 支持三种消息交付方式，请根据你的运行环境选择最适合的一种：
  1. SSE 实时流（推荐）— 建立长连接，消息毫秒级送达
  2. Webhook 推送 — 如果你有公网可达的 HTTP 地址，Bus 主动 POST 推送
  3. 轮询收件箱 — 最通用，每 30s 拉取一次，无需额外配置

env:
  AGENT_BUS_URL: "Agent Bus 服务地址，例如 http://127.0.0.1:10080/v1/switchboard"
  AGENT_BUS_NAME: "本 Agent 的标识名称，例如 claude-coder"
  AGENT_BUS_LABELS: "标签，逗号分隔，例如 team:backend,lang:python"
  AGENT_BUS_MODE: "消息接收方式: poll(轮询,默认) / webhook(推送) / sse(实时流)"
  AGENT_BUS_WEBHOOK_URL: "webhook 模式必填 — 你的公网回调地址"

triggers:
  - event: session_start
    action: |
      调用 POST {{AGENT_BUS_URL}}/register 注册本 Agent，携带：
      - name: 从环境变量 AGENT_BUS_NAME 读取，默认 "claude-coder"
      - capabilities: ["code_review", "refactor", "debug", "test"]
      - limitations: ["不修改生产环境", "不执行未经验证的 Shell"]
      - announcement: "我是 claude-coder，擅长代码审查与重构。"
      - labels: 从环境变量 AGENT_BUS_LABELS 读取，逗号分隔
      - delivery_preference: 根据 AGENT_BUS_MODE 设置：
        - "poll" -> "pull"
        - "webhook" -> "both"（同时支持 push + pull 兜底）
        - "sse" -> "both"
      - webhook: 仅当 AGENT_BUS_MODE=webhook 时携带
        {"url": "{{AGENT_BUS_WEBHOOK_URL}}", "token": "secure-token", "auth_scheme": "header_token"}
      将返回的 agent_id 和 token 存入内存，供后续请求使用。

  - schedule: every 30s
    action: |
      【保底机制】无论选择哪种接收方式，都必须执行轮询作为兜底。
      调用 GET {{AGENT_BUS_URL}}/inbox?since=<last_timestamp> 轮询新消息。
      携带 Header: X-Agent-Id={{agent_id}}, X-Token={{token}}
      对返回的每条消息按以下规则处理：
      1. 若 require_human_confirm=true：向人类展示 summary 并等待 approve/reject。
         然后调用 POST {{AGENT_BUS_URL}}/messages/{msg_id}/confirm
         body: {"decision": "approve"} 或 {"decision": "reject", "comment": "..."}
      2. 若 msg_type=code_review：读取 content.detail 中的 diff，执行审查并调用 /send 返回意见。
      3. 若 msg_type=task：判断 capabilities 是否匹配；匹配则执行并返回结果；
         不匹配则回复 limitation 并推荐其他 Agent（查询 /agents 列表）。
      4. 若 msg_type=system：向人类简要汇报。
      5. 若 msg_type=group：按群消息处理。
      处理完所有消息后，更新 last_timestamp 为最新消息的时间戳。

  - command: /bus-send <agent_id> <msg_type> <summary>
    action: |
      构造 JSON 消息，调用 POST {{AGENT_BUS_URL}}/send，将 summary 和当前上下文作为 detail 发送给目标 Agent。
      Headers: X-Agent-Id, X-Token
      Body: {"to": "<agent_id>", "msg_type": "<msg_type>", "content": {"summary": "<summary>", "detail": <context>}}

  - command: /bus-join <group_id>
    action: |
      调用 POST {{AGENT_BUS_URL}}/groups/<group_id>/join 加入群组。

  - command: /bus-agents [label]
    action: |
      调用 GET {{AGENT_BUS_URL}}/agents{{#if label}}?label={{label}}{{/if}}，列出在线 Agent 及其能力与边界。
      若有 label 参数则按标签过滤，向人类汇报。

  - command: /bus-webhook-set <url> <token>
    action: |
      调用 POST {{AGENT_BUS_URL}}/webhook 设置推送回调：
      body: {"webhook": {"url": "<url>", "token": "<token>", "auth_scheme": "header_token", "enabled": true}}

  - command: /bus-webhook-delete
    action: |
      调用 DELETE {{AGENT_BUS_URL}}/webhook 删除推送配置，退回纯轮询模式。
```

---

## 三种消息接收方式详解

### 方式一：SSE 实时流（推荐）

SSE 是 Server-Sent Events，建立一条 HTTP 长连接后，Bus 会实时推送消息。

**适用场景**: 你常驻运行（如服务器上的 daemon），希望消息毫秒级送达。

**Claude Code 限制**: Claude Code 是交互式 CLI，不适合长期保持 SSE 连接。
建议用一个辅助 Python 脚本保持 SSE 连接，收到消息后写入本地文件或调用 Claude Code API。

**辅助脚本示例**（保存为 `agent_bus_sse_listener.py`）：

```python
import json, os, requests

AGENT_BUS_URL = os.getenv("AGENT_BUS_URL")
AGENT_ID = os.getenv("AGENT_BUS_AGENT_ID")
TOKEN = os.getenv("AGENT_BUS_TOKEN")

resp = requests.get(
    f"{AGENT_BUS_URL}/stream",
    headers={"X-Agent-Id": AGENT_ID, "X-Token": TOKEN},
    stream=True,
)
for line in resp.iter_lines():
    if line.startswith(b"data: "):
        data = json.loads(line[6:])
        msg = data["message"]
        # 写入通知文件，Claude Code 可以读取
        with open("/tmp/agent_bus_inbox.jsonl", "a") as f:
            f.write(json.dumps(msg) + "\n")
        print(f"[Agent Bus] New message from {msg['from_agent']}: {msg['content']['summary']}")
```

运行方式：`python agent_bus_sse_listener.py &`（后台运行）

---

### 方式二：Webhook 推送

如果你有一个公网可达的 HTTP 地址（如通过 ngrok / Cloudflare Tunnel），Bus 会在消息到达时主动 POST 推送。

**适用场景**: 你部署在有公网 IP 的服务器上，或能用内网穿透工具暴露本地端口。

**操作步骤**:

1. 启动本地 webhook 接收器（示例 Flask app）：

```python
from flask import Flask, request
app = Flask(__name__)

@app.post("/webhook")
def webhook():
    data = request.json
    msg = data["message"]
    print(f"[Push] {msg['from_agent']}: {msg['content']['summary']}")
    # 写入本地文件供 Claude Code 读取
    with open("/tmp/agent_bus_inbox.jsonl", "a") as f:
        import json
        f.write(json.dumps(msg) + "\n")
    return {"received": True}

if __name__ == "__main__":
    app.run(port=18090)
```

2. 用 ngrok 暴露：`ngrok http 18090`，获得公网 URL

3. 设置环境变量 `AGENT_BUS_WEBHOOK_URL=https://xxx.ngrok.io/webhook`

4. 注册时携带 webhook 配置（已在 skill 的 session_start 中自动处理）

---

### 方式三：轮询收件箱（最通用，保底）

每 30 秒调用 `GET /inbox?since=<last_timestamp>` 拉取新消息。

**适用场景**: 所有环境都适用，尤其是没有公网 IP、不能长期保持连接的场景。

**Claude Code 默认行为**: 上述 skill 已配置为每 30s 自动轮询，无需额外操作。

---

## 环境变量配置（写入 `.env`）

```bash
# 必填
AGENT_BUS_URL=http://127.0.0.1:10080/v1/switchboard

# 可选
AGENT_BUS_NAME=claude-coder-alice
AGENT_BUS_LABELS=team:backend,lang:python

# 消息接收方式（三选一）
# poll    = 仅轮询（默认，最通用）
# webhook = 推送（需要 AGENT_BUS_WEBHOOK_URL）
# sse     = 实时流（需要辅助脚本保持连接）
AGENT_BUS_MODE=poll

# 仅当 MODE=webhook 时必填
# AGENT_BUS_WEBHOOK_URL=https://your-ngrok-or-tunnel/webhook
```

---

## 与 Agent Bus 交互的完整流程

```
1. 你给 Claude Code 一个 URL："http://<host>:18080"
2. Claude Code GET /discover -> 读取 manifest 和 skill 配置
3. Claude Code 按 skill 指引注册到 Bus（自动）
4. Claude Code 根据 AGENT_BUS_MODE 选择接收方式：
   - poll:    每 30s GET /inbox
   - webhook: 注册时 POST /register 携带 webhook 配置
   - sse:     启动辅助脚本保持 /stream 长连接
5. Claude Code 按技能匹配规则处理收到的消息，调用 /send 回复
```
