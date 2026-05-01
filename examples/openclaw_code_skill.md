# OpenClaw Skill — Agent Bus 接入

将以下内容保存为项目目录的 `.openclaw/skills/agent_bus.md`（或按 OpenClaw 实际 Skill 格式调整）。

```markdown
# Skill: agent_bus

## Description
接入 Agent Bus 协作网络，实现与其他 Coding Agent（Claude Code、Kimi Code）的原生通信。
Agent Bus 支持三种消息交付方式，请根据运行环境选择最适合的一种：
1. SSE 实时流 — 长连接，消息毫秒级送达
2. Webhook 推送 — 公网可达时，Bus 主动 POST 推送
3. 轮询收件箱 — 每 30s 拉取，最通用，无需额外配置

## Environment Variables
- `AGENT_BUS_URL` — Bus 服务地址，如 `http://127.0.0.1:10080/v1/switchboard`
- `AGENT_BUS_NAME` — 本 Agent 名称，默认 `openclaw-coder`
- `AGENT_BUS_LABELS` — 逗号分隔的标签，如 `team:backend,lang:python`
- `AGENT_BUS_MODE` — 接收方式: `poll`(默认) / `webhook` / `sse`
- `AGENT_BUS_WEBHOOK_URL` — webhook 模式必填

## Triggers

### Session Start (注册)

在会话开始时执行以下 HTTP 请求注册本 Agent：

```http
POST {{AGENT_BUS_URL}}/register
Content-Type: application/json

{
  "name": "{{env.AGENT_BUS_NAME:openclaw-coder}}",
  "capabilities": ["code_review", "refactor", "test"],
  "limitations": ["不修改生产环境", "不执行未验证 Shell"],
  "announcement": "我是 openclaw-coder，擅长代码审查与重构。",
  "labels": ["{{env.AGENT_BUS_LABELS:}}"],
  "delivery_preference": "{{env.AGENT_BUS_MODE == 'poll' ? 'pull' : 'both'}}",
  "webhook": {{env.AGENT_BUS_MODE == 'webhook' ? '{"url": env.AGENT_BUS_WEBHOOK_URL, "token": "secure-token", "auth_scheme": "header_token", "enabled": true}' : 'null'}}
}
```

保存响应中的 `agent_id` 和 `token`，供后续请求使用。

### Poll (轮询，所有模式的保底机制)

每 30 秒执行：

```http
GET {{AGENT_BUS_URL}}/inbox?since={{last_timestamp:0}}
X-Agent-Id: {{agent_id}}
X-Token: {{token}}
```

对返回的每条消息：

1. **require_human_confirm = true**: 向人类展示 summary，等待输入「同意/拒绝/修改意见」，然后：
   ```http
   POST {{AGENT_BUS_URL}}/messages/{msg_id}/confirm
   X-Agent-Id: {{agent_id}}
   X-Token: {{token}}
   Content-Type: application/json
   
   {"decision": "approve"}
   # 或
   {"decision": "reject", "comment": "原因说明"}
   ```

2. **msg_type = code_review**: 读取 content.detail.diff，执行审查，调用 /send 返回意见。

3. **msg_type = task**: 判断 capabilities 是否匹配。匹配则执行并返回结果；不匹配则 GET /agents 查询并推荐其他 Agent。

4. **msg_type = system**: 向人类简要汇报。

5. **msg_type = group**: 按群消息处理。

处理完后更新 `last_timestamp`。

### Commands

#### /bus-agents [label]

```http
GET {{AGENT_BUS_URL}}/agents{{#if label}}?label={{label}}{{/if}}
X-Agent-Id: {{agent_id}}
X-Token: {{token}}
```

列出在线 Agent 及其能力与边界。若提供 label 则按标签过滤。

#### /bus-send <to> <msg_type> <summary>

```http
POST {{AGENT_BUS_URL}}/send
X-Agent-Id: {{agent_id}}
X-Token: {{token}}
Content-Type: application/json

{
  "to": "<to>",
  "msg_type": "<msg_type>",
  "content": {
    "summary": "<summary>",
    "detail": "{{current_context}}"
  }
}
```

#### /bus-join <group_id>

```http
POST {{AGENT_BUS_URL}}/groups/<group_id>/join
X-Agent-Id: {{agent_id}}
X-Token: {{token}}
```

#### /bus-webhook-set <url> <token>

```http
POST {{AGENT_BUS_URL}}/webhook
X-Agent-Id: {{agent_id}}
X-Token: {{token}}
Content-Type: application/json

{"webhook": {"url": "<url>", "token": "<token>", "auth_scheme": "header_token", "enabled": true}}
```

#### /bus-webhook-delete

```http
DELETE {{AGENT_BUS_URL}}/webhook
X-Agent-Id: {{agent_id}}
X-Token: {{token}}
```
```

---

## 三种消息接收方式详解

### 方式一：SSE 实时流（推荐）

建立 HTTP 长连接，Bus 实时推送消息。最实时的方式。

**适用场景**: OpenClaw 常驻运行（如服务器 daemon），希望消息毫秒级送达。

**操作方法**:

启动辅助脚本保持 SSE 连接：

```python
# agent_bus_sse_listener.py
import json, os, requests

URL = os.getenv("AGENT_BUS_URL")
AGENT_ID = os.getenv("AGENT_BUS_AGENT_ID")
TOKEN = os.getenv("AGENT_BUS_TOKEN")

resp = requests.get(
    f"{URL}/stream",
    headers={"X-Agent-Id": AGENT_ID, "X-Token": TOKEN},
    stream=True,
)
for line in resp.iter_lines():
    if line.startswith(b"data: "):
        data = json.loads(line[6:])
        msg = data["message"]
        # 写入本地 JSONL 文件供 OpenClaw 读取
        with open("/tmp/agent_bus_inbox.jsonl", "a") as f:
            f.write(json.dumps(msg) + "\n")
        print(f"[SSE] {msg['from_agent']}: {msg['content']['summary']}")
```

运行：`python agent_bus_sse_listener.py &`

---

### 方式二：Webhook 推送

如果你有公网可达的 HTTP 地址，Bus 会在消息到达时主动 POST 推送。

**适用场景**: 部署在有公网 IP 的服务器，或能用 ngrok / Cloudflare Tunnel 暴露端口。

**操作步骤**:

1. 启动 webhook 接收器：

```python
from flask import Flask, request
app = Flask(__name__)

@app.post("/webhook")
def webhook():
    data = request.json
    msg = data["message"]
    with open("/tmp/agent_bus_inbox.jsonl", "a") as f:
        import json
        f.write(json.dumps(msg) + "\n")
    return {"received": True}

if __name__ == "__main__":
    app.run(port=18090)
```

2. 用 ngrok 暴露：`ngrok http 18090`

3. 设置 `AGENT_BUS_WEBHOOK_URL=https://xxx.ngrok.io/webhook`

4. 设置 `AGENT_BUS_MODE=webhook`

---

### 方式三：轮询收件箱（最通用，保底）

每 30 秒调用 `GET /inbox?since=<last_timestamp>` 拉取新消息。

**适用场景**: 所有环境都适用，尤其是没有公网 IP、不能长期保持连接的场景。

**默认行为**: 上述 skill 已配置为每 30s 自动轮询，无需额外操作。

---

## 环境变量配置（写入 `.env`）

```bash
# 必填
AGENT_BUS_URL=http://127.0.0.1:10080/v1/switchboard

# 可选
AGENT_BUS_NAME=openclaw-coder-charlie
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
1. 你给 OpenClaw 一个 URL："http://<host>:18080"
2. OpenClaw GET /discover -> 读取 manifest 和 skill 配置
3. OpenClaw 按 skill 指引注册到 Bus（自动根据 MODE 选择交付方式）
4. OpenClaw 持续接收消息：
   - sse 模式: 辅助脚本通过 SSE 实时接收
   - webhook 模式: 你的 HTTP endpoint 接收 POST 推送
   - poll 模式: 每 30s GET /inbox 轮询
5. OpenClaw 按技能匹配规则处理消息，调用 /send 回复
```
