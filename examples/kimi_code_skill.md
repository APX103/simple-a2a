# Kimi Code Skill — Agent Bus 接入

将以下内容保存为项目目录的 `.kimi/skills/agent_bus.json`（或按 Kimi Code 实际 Skill 格式调整），Kimi Code 将按规则自动触发 HTTP 请求。

```json
{
  "name": "agent_bus",
  "description": "接入 Agent Bus，实现与其他 Coding Agent 的原生协作（点对点、群组、能力发现）。",
  "version": "1.0.0",
  "triggers": [
    {
      "type": "session_start",
      "action": {
        "description": "注册本 Agent 到 Agent Bus",
        "http": {
          "method": "POST",
          "url": "{{AGENT_BUS_URL}}/register",
          "headers": { "Content-Type": "application/json" },
          "body": {
            "name": "{{env.AGENT_BUS_NAME:kimi-coder}}",
            "capabilities": ["code_review", "architecture_design", "debug"],
            "limitations": ["不执行危险 Shell", "不写测试用例"],
            "announcement": "我是 kimi-coder，专注代码审查与架构设计。",
            "labels": ["{{env.AGENT_BUS_LABELS:}}"]
          },
          "store_response": ["agent_id", "token"]
        }
      }
    },
    {
      "type": "poll",
      "interval": "30s",
      "action": {
        "description": "轮询收件箱并处理消息",
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
    }
  ]
}
```

**环境变量**（建议写入 `.env` 或 Kimi Code 配置）：

```bash
AGENT_BUS_URL=http://127.0.0.1:10080/v1/switchboard
AGENT_BUS_NAME=kimi-coder-bob
```
