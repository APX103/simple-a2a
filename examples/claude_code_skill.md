# Claude Code Skill — Agent Bus 接入

将以下内容保存为项目根目录的 `.claude/skills/agent_bus.yaml`，Claude Code 将在适当上下文自动执行注册与通信。

```yaml
name: agent_bus
description: |
  将当前 Claude Code 实例接入 Agent Bus，使其具备与其他 Agent（如 Kimi Code）
  点对点通信、群组广播、能力查询的能力。
triggers:
  - event: session_start
    action: |
      调用 POST {{AGENT_BUS_URL}}/register 注册本 Agent，携带：
      - name: 从环境变量 AGENT_BUS_NAME 读取，默认 "claude-coder"
      - capabilities: ["code_review", "refactor", "debug", "test"]
      - limitations: ["不修改生产环境", "不执行未经验证的 Shell"]
      - announcement: "我是 claude-coder，擅长代码审查与重构。"
      将返回的 agent_id 和 token 存入内存，供后续请求使用。

  - schedule: every 30s
    action: |
      调用 GET {{AGENT_BUS_URL}}/inbox?since=<last_timestamp> 轮询新消息。
      对每条消息：
      1. 若 require_human_confirm=true，向人类展示 summary 并等待 approve/reject。
      2. 若 msg_type=code_review，读取 content.detail 中的 diff，执行审查并调用 /send 返回意见。
      3. 若 msg_type=task，判断 capabilities 是否匹配：匹配则执行并返回结果；不匹配则回复 limitation 并推荐其他 Agent（查询 /agents 列表）。
      4. 若 msg_type=system，向人类简要汇报。
      更新 last_timestamp 为最新消息的时间戳。

  - command: /bus-send <agent_id> <msg_type> <summary>
    action: |
      构造 JSON 消息，调用 POST {{AGENT_BUS_URL}}/send，将 summary 和当前上下文作为 detail 发送给目标 Agent。

  - command: /bus-join <group_id>
    action: |
      调用 POST {{AGENT_BUS_URL}}/groups/<group_id>/join 加入群组。

  - command: /bus-agents
    action: |
      调用 GET {{AGENT_BUS_URL}}/agents，列出所有在线 Agent 及其能力与边界，向人类汇报。
```

**环境变量配置**（建议写入 `.env`）：

```bash
AGENT_BUS_URL=http://127.0.0.1:10080/v1/switchboard
AGENT_BUS_NAME=claude-coder-alice
```
