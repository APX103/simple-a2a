# Kimi Code Skill — Agent Bus 接入

将以下内容保存为项目目录的 `.kimi/skills/agent_bus.json`（或按 Kimi Code 实际 Skill 格式调整），Kimi Code 将按规则自动触发 HTTP 请求。

```json
{
  "name": "agent_bus",
  "description": "接入 Agent Bus，实现与其他 Coding Agent 的原生协作（点对点、群组、能力发现）。支持三种消息接收方式：SSE 实时流、Webhook 推送、轮询收件箱。",
  "version": "1.1.0",
  "env": {
    "AGENT_BUS_URL": "Agent Bus 服务地址，例如 http://127.0.0.1:10080/v1/switchboard",
    "AGENT_BUS_NAME": "本 Agent 的标识名称，默认 kimi-coder",
    "AGENT_BUS_LABELS": "标签，逗号分隔，例如 team:backend,lang:python",
    "AGENT_BUS_MODE": "消息接收方式: poll(轮询,默认) / webhook(推送) / sse(实时流)",
    "AGENT_BUS_WEBHOOK_URL": "webhook 模式必填 — 你的公网回调地址"
  },
  "triggers": [
    {
      "type": "session_start",
      "action": {
        "description": "注册本 Agent 到 Agent Bus，根据 MODE 选择交付方式",
        "http": {
          "method": "POST",
          "url": "{{AGENT_BUS_URL}}/register",
          "headers": { "Content-Type": "application/json" },
          "body": {
            "name": "{{env.AGENT_BUS_NAME:kimi-coder}}",
            "capabilities": ["code_review", "architecture_design", "debug"],
            "limitations": ["不执行危险 Shell", "不写测试用例"],
            "announcement": "我是 kimi-coder，专注代码审查与架构设计。",
            "labels": ["{{env.AGENT_BUS_LABELS:}}"],
            "delivery_preference": "{{#if (eq env.AGENT_BUS_MODE 'poll')}}pull{{else}}both{{/if}}",
            "webhook": "{{#if (eq env.AGENT_BUS_MODE 'webhook')}}{'url': env.AGENT_BUS_WEBHOOK_URL, 'token': 'secure-token', 'auth_scheme': 'header_token', 'enabled': true}{{else}}null{{/if}}"
          },
          "store_response": ["agent_id", "token"]
        }
      }
    },
    {
      "type": "poll",
      "interval": "30s",
      "action": {
        "description": "轮询收件箱并处理消息（所有模式通用的保底机制）",
        "http": {
          "method": "GET",
          "url": "{{AGENT_BUS_URL}}/inbox?since={{stored.last_timestamp:0}}",
          "headers": {
            "X-Agent-Id": "{{stored.agent_id}}",
            "X-Token": "{{stored.token}}"
          }
        },
        "on_success": {
          "iterate": "messages",
          "steps": [
            {
              "if": "message.require_human_confirm == true",
              "then": "向用户展示 message.content.summary，等待用户输入「同意/拒绝/修改意见」，然后调用 POST {{AGENT_BUS_URL}}/messages/{{message.msg_id}}/confirm"
            },
            {
              "if": "message.msg_type == 'code_review'",
              "then": "读取 content.detail 的 diff，执行审查，调用 /send 返回 review 结果"
            },
            {
              "if": "message.msg_type == 'task'",
              "then": "判断 capabilities 是否匹配；匹配则执行并返回结果，不匹配则调用 /agents 查询并推荐合适 Agent"
            },
            {
              "if": "message.msg_type == 'system'",
              "then": "向用户简要汇报系统通知"
            },
            {
              "if": "message.msg_type == 'group'",
              "then": "按群消息处理"
            },
            {
              "always": "更新 stored.last_timestamp = message.timestamp"
            }
          ]
        }
      }
    },
    {
      "type": "command",
      "pattern": "/bus-agents [label]",
      "action": {
        "description": "列出在线 Agent，可按标签过滤",
        "http": {
          "method": "GET",
          "url": "{{AGENT_BUS_URL}}/agents{{#if args.label}}?label={{args.label}}{{/if}}"
        }
      }
    },
    {
      "type": "command",
      "pattern": "/bus-send <to> <msg_type> <summary>",
      "action": {
        "description": "向指定 Agent 或群组发送消息",
        "http": {
          "method": "POST",
          "url": "{{AGENT_BUS_URL}}/send",
          "headers": {
            "Content-Type": "application/json",
            "X-Agent-Id": "{{stored.agent_id}}",
            "X-Token": "{{stored.token}}"
          },
          "body": {
            "to": "{{args.to}}",
            "msg_type": "{{args.msg_type}}",
            "content": {
              "summary": "{{args.summary}}",
              "detail": "{{context.current_task}}"
            }
          }
        }
      }
    },
    {
      "type": "command",
      "pattern": "/bus-join <group_id>",
      "action": {
        "http": {
          "method": "POST",
          "url": "{{AGENT_BUS_URL}}/groups/{{args.group_id}}/join",
          "headers": {
            "X-Agent-Id": "{{stored.agent_id}}",
            "X-Token": "{{stored.token}}"
          }
        }
      }
    },
    {
      "type": "command",
      "pattern": "/bus-webhook-set <url> <token>",
      "action": {
        "description": "设置 webhook 推送回调",
        "http": {
          "method": "POST",
          "url": "{{AGENT_BUS_URL}}/webhook",
          "headers": {
            "Content-Type": "application/json",
            "X-Agent-Id": "{{stored.agent_id}}",
            "X-Token": "{{stored.token}}"
          },
          "body": {
            "webhook": {
              "url": "{{args.url}}",
              "token": "{{args.token}}",
              "auth_scheme": "header_token",
              "enabled": true
            }
          }
        }
      }
    },
    {
      "type": "command",
      "pattern": "/bus-webhook-delete",
      "action": {
        "description": "删除 webhook，退回纯轮询",
        "http": {
          "method": "DELETE",
          "url": "{{AGENT_BUS_URL}}/webhook",
          "headers": {
            "X-Agent-Id": "{{stored.agent_id}}",
            "X-Token": "{{stored.token}}"
          }
        }
      }
    }
  ]
}
```

---

## 三种消息接收方式详解

### 方式一：SSE 实时流（推荐）

建立 HTTP 长连接，消息毫秒级送达。最实时的方式。

**适用场景**: Kimi Code 常驻运行（如在服务器上作为 daemon），希望消息实时到达。

**操作方法**:

启动一个辅助脚本保持 SSE 连接：

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
        # 写入本地 JSONL 文件供 Kimi Code 读取
        with open("/tmp/agent_bus_inbox.jsonl", "a") as f:
            f.write(json.dumps(msg) + "\n")
        print(f"[SSE] {msg['from_agent']}: {msg['content']['summary']}")
```

运行：`python agent_bus_sse_listener.py &`

然后在 Kimi Code 的轮询逻辑中，额外检查 `/tmp/agent_bus_inbox.jsonl` 文件即可。

---

### 方式二：Webhook 推送

如果你有公网可达的 HTTP 地址，Bus 会在消息到达时主动 POST 推送。

**适用场景**: 部署在有公网 IP 的服务器，或能用 ngrok / Cloudflare Tunnel 暴露端口。

**操作步骤**:

1. 启动 webhook 接收器（示例 FastAPI）：

```python
from fastapi import FastAPI, Request
app = FastAPI()

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    msg = data["message"]
    with open("/tmp/agent_bus_inbox.jsonl", "a") as f:
        import json
        f.write(json.dumps(msg) + "\n")
    return {"received": True}
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
AGENT_BUS_NAME=kimi-coder-bob
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
1. 你给 Kimi Code 一个 URL："http://<host>:18080"
2. Kimi Code GET /discover -> 读取 manifest 和 skill 配置
3. Kimi Code 按 skill 指引注册到 Bus（自动根据 MODE 选择交付方式）
4. Kimi Code 持续接收消息：
   - sse 模式: 辅助脚本通过 SSE 实时接收
   - webhook 模式: 你的 HTTP endpoint 接收 POST 推送
   - poll 模式: 每 30s GET /inbox 轮询
5. Kimi Code 按技能匹配规则处理消息，调用 /send 回复
```
