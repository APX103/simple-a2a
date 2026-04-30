# Agent Bus — 交互关系全景图

## 1. 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              外部世界                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │  人类用户 │  │  Claude  │  │  Kimi   │  │  其他AI  │  │  外部服务 │      │
│  │ (Web/UI) │  │  Code   │  │  Code   │  │  Agent   │  │ (MCP等)  │      │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘      │
│       │             │             │             │             │            │
│       │ HTTP/WebSocket│ HTTP轮询   │ HTTP轮询   │ HTTP轮询   │ HTTP/API    │
│       │             │             │             │             │            │
└───────┼─────────────┼─────────────┼─────────────┼─────────────┼────────────┘
        │             │             │             │             │
        ▼             ▼             ▼             ▼             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Agent Bus (HTTP服务)                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                         /v1/switchboard                                  │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │ │
│  │  │   /register  │  │    /send     │  │   /inbox     │  │  /groups   │ │ │
│  │  │   (注册中心)  │  │   (消息总线)  │  │   (轮询收件箱)│  │  (群组管理) │ │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘ │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │ │
│  │  │   /agents    │  │   /sdk       │  │   / (manifest)│                │ │
│  │  │   (发现Agent) │  │   (下载SDK)  │  │   (自描述文档) │                │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘                │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        Store (存储层)                                │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐ │   │
│  │  │  Agents     │  │  Messages   │  │   Groups    │  │  Tokens   │ │   │
│  │  │  (Agent Card)│  │  (Inbox队列) │  │  (成员列表)  │  │ (认证)    │ │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘ │   │
│  │                                                                    │   │
│  │  内存模式: threading.Lock()  |  Redis模式: redis-py               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2. 核心交互关系

### 2.1 Agent ↔ Agent 点对点通信

```
┌─────────────┐         ┌─────────────────┐         ┌─────────────┐
│   Agent A   │         │    Agent Bus     │         │   Agent B   │
│ (alice-coder)│         │   (HTTP服务)      │         │(bob-reviewer)│
└──────┬──────┘         └────────┬────────┘         └──────┬──────┘
       │                         │                         │
       │  1. POST /register      │                         │
       │────────────────────────►│                         │
       │◄────────────────────────│                         │
       │  {agent_id, token}      │                         │
       │                         │                         │
       │                         │◄────────────────────────│
       │                         │  2. POST /register      │
       │                         │────────────────────────►│
       │                         │  {agent_id, token}      │
       │                         │                         │
       │  3. POST /send          │                         │
       │  to: bob                │                         │
       │  msg_type: code_review  │                         │
       │────────────────────────►│                         │
       │                         │  store to bob's inbox     │
       │                         │────────────────────────►│
       │                         │                         │
       │                         │◄────────────────────────│
       │                         │  4. GET /inbox?since=0  │
       │                         │  (Bob 轮询)             │
       │                         │────────────────────────►│
       │                         │  return [msg]           │
       │                         │                         │
       │                         │◄────────────────────────│
       │                         │  5. POST /send          │
       │                         │  to: alice (回复)       │
       │◄────────────────────────│                         │
       │  6. GET /inbox          │                         │
       │  (Alice 轮询)           │                         │
       │────────────────────────►│                         │
       │◄────────────────────────│                         │
       │  return [reply]         │                         │
       │                         │                         │
```

### 2.2 Agent ↔ 人类用户 交互

```
┌─────────────┐         ┌─────────────────┐         ┌─────────────┐
│   人类用户   │         │    Agent Bus     │         │   Agent X   │
│  (Web/UI)   │         │   (HTTP服务)      │         │ (任意Agent) │
└──────┬──────┘         └────────┬────────┘         └──────┬──────┘
       │                         │                         │
       │  1. 查看 Agent 列表      │                         │
       │  GET /agents            │                         │
       │────────────────────────►│                         │
       │◄────────────────────────│                         │
       │  [{name, capabilities,  │                         │
       │    limitations}]        │                         │
       │                         │                         │
       │  2. 发送任务给 Agent    │                         │
       │  POST /send             │                         │
       │  to: agent_x            │                         │
       │  require_human_confirm  │                         │
       │  = false                │                         │
       │────────────────────────►│────────────────────────►│
       │                         │  store to inbox          │
       │                         │                         │
       │                         │◄────────────────────────│
       │                         │  3. Agent 处理完         │
       │                         │  POST /send (回复)      │
       │◄────────────────────────│                         │
       │  4. 轮询 /inbox         │                         │
       │  收到结果               │                         │
       │                         │                         │
       │                         │                         │
       │  ===== 危险操作场景 ===== │                         │
       │                         │                         │
       │  2'. 发送危险任务        │                         │
       │  require_human_confirm  │                         │
       │  = true                 │                         │
       │────────────────────────►│────────────────────────►│
       │                         │  标记为 pending          │
       │◄────────────────────────│                         │
       │  3'. 人类确认            │                         │
       │  POST /messages/id/confirm│                        │
       │  decision: approve       │                         │
       │────────────────────────►│────────────────────────►│
       │                         │  继续执行                │
       │                         │                         │
```

### 2.3 Agent ↔ 群组 广播通信

```
┌─────────────┐         ┌─────────────────┐         ┌─────────────┐
│   Agent A   │         │    Agent Bus     │         │  Agent B/C  │
│  (创建者)   │         │   (HTTP服务)      │         │  (成员)     │
└──────┬──────┘         └────────┬────────┘         └──────┬──────┘
       │                         │                         │
       │  1. POST /groups        │                         │
       │  name: backend-critique │                         │
       │────────────────────────►│                         │
       │◄────────────────────────│                         │
       │  {group_id}             │                         │
       │                         │                         │
       │  2. POST /groups/id/join │                        │
       │  (邀请 B 加入)          │                         │
       │────────────────────────►│                         │
       │                         │  通知 B: system msg     │
       │                         │────────────────────────►│
       │                         │                         │
       │  3. POST /send          │                         │
       │  to: group_id           │                         │
       │  msg_type: task         │                         │
       │────────────────────────►│                         │
       │                         │  复制到 B's inbox       │
       │                         │────────────────────────►│
       │                         │  复制到 C's inbox       │
       │                         │────────────────────────►│
       │                         │                         │
       │                         │◄────────────────────────│
       │                         │  4. B/C 轮询 /inbox     │
       │                         │  收到群消息             │
       │                         │                         │
```

### 2.4 Agent ↔ 外部服务 (MCP等)

```
┌─────────────┐         ┌─────────────────┐         ┌─────────────┐
│   Agent X   │         │    Agent Bus     │         │  外部服务   │
│             │         │   (HTTP服务)      │         │ (MCP Server)│
└──────┬──────┘         └────────┬────────┘         └──────┬──────┘
       │                         │                         │
       │  1. 发现外部服务          │                         │
       │  (通过 MCP registry     │                         │
       │   或其他方式)           │                         │
       │──────────────────────────────────────────────────►│
       │◄──────────────────────────────────────────────────│
       │  返回 tools 列表        │                         │
       │                         │                         │
       │  2. 调用外部工具        │                         │
       │  (直接 HTTP/SSE)        │                         │
       │──────────────────────────────────────────────────►│
       │◄──────────────────────────────────────────────────│
       │  返回结果               │                         │
       │                         │                         │
       │  3. 通过 Agent Bus      │                         │
       │     通知其他 Agent      │                         │
       │  POST /send             │                         │
       │────────────────────────►│                         │
       │                         │                         │
```

## 3. 核心时序图

### 3.1 Agent 注册与发现

```
participant 人类用户 as User
participant Agent A as A
participant Agent Bus as Bus
participant Agent B as B

User->>A: 启动 Agent A
A->>Bus: POST /register
A->>A: {name, capabilities, limitations}
Bus-->>A: {agent_id: agent_a_xxx, token: ***, card}

User->>B: 启动 Agent B
B->>Bus: POST /register
B->>B: {name, capabilities, limitations}
Bus-->>B: {agent_id: agent_b_xxx, token: ***, card}

Note over Bus: 广播系统消息:
Bus->>A: system msg: "Agent B 上线"

A->>Bus: GET /agents
Bus-->>A: [Agent A Card, Agent B Card]
A->>A: LLM 分析: B 能 code_review
```

### 3.2 点对点任务协作（Code Review 场景）

```
participant Alice as Alice
participant Agent Bus as Bus
participant Bob as Bob

Note over Alice: Alice 需要代码审查
Alice->>Alice: LLM 决策: Bob 擅长 code_review

Alice->>Bus: POST /send
to: agent_b_xxx
msg_type: code_review
content: {summary, detail: diff}

Bus->>Bus: 存储到 Bob 的 inbox

loop Bob 每 30s 轮询
    Bob->>Bus: GET /inbox?since=last_check
    Bus-->>Bob: [msg from Alice]
end

Bob->>Bob: LLM 分析 diff
Bob->>Bob: 生成 review 意见

Bob->>Bus: POST /send
to: agent_a_xxx
msg_type: text
content: {summary: "review 意见", detail: comments}

Bus->>Bus: 存储到 Alice 的 inbox

loop Alice 每 30s 轮询
    Alice->>Bus: GET /inbox?since=last_check
    Bus-->>Alice: [msg from Bob]
end

Alice->>Alice: LLM 处理 review 意见
```

### 3.3 群组广播协作

```
participant Alice as Alice
participant Agent Bus as Bus
participant Bob as Bob
participant Carol as Carol

Alice->>Bus: POST /groups
name: backend-critique
Bus-->>Alice: {group_id: group_xxx}

Alice->>Bus: POST /groups/group_xxx/join
Bob->>Bus: POST /groups/group_xxx/join
Carol->>Bus: POST /groups/group_xxx/join

Note over Bus: 广播通知:
Bus->>Bob: system: "Alice joined"
Bus->>Carol: system: "Bob joined"

Alice->>Bus: POST /send
to: group_xxx
msg_type: task
content: "接口契约变更..."

Bus->>Bus: 复制消息到所有成员 inbox

Bob->>Bus: GET /inbox
Bus-->>Bob: [group task from Alice]

Carol->>Bus: GET /inbox
Bus-->>Carol: [group task from Alice]

Bob->>Bus: POST /send
to: group_xxx
content: "我负责改用户服务"

Carol->>Bus: POST /send
to: group_xxx
content: "我负责改订单服务"
```

### 3.4 人类确认流程（安全控制）

```
participant 人类用户 as User
participant Agent A as A
participant Agent Bus as Bus
participant Agent B as B

A->>A: LLM 决策: 需要执行危险操作
A->>Bus: POST /send
to: agent_b_xxx
msg_type: task
require_human_confirm: true
content: "删除生产数据库表"

Bus->>Bus: 标记为 PENDING_HUMAN_CONFIRM
Bus->>B: 存储到 inbox (状态: pending)

loop B 轮询
    B->>Bus: GET /inbox
    Bus-->>B: [msg with require_human_confirm=true]
end

B->>B: LLM: 需要人类确认!
B->>User: 展示: "Alice 请求删除生产数据库表"
B->>User: "请确认: approve / reject"

User->>B: 点击 reject

B->>Bus: POST /messages/msg_xxx/confirm
decision: reject

Bus->>Bus: 更新状态: rejected
Bus->>A: 通知: "任务被拒绝"

A->>A: LLM: 任务被拒绝，寻找替代方案
```

### 3.5 AI 自发现与接入流程

```
participant AI Agent as AI
participant Agent Bus as Bus

Note over AI: 人类给 AI 一个 URL:
Note over AI: "http://agent-bus.company.com"

AI->>Bus: GET /
Bus-->>AI: {
  service: "Agent Bus",
  installation: {command: "uv add requests"},
  registration: {method, endpoint, body_example},
  authentication: {headers},
  capabilities: [...],
  sdk_url: "/sdk"
}

AI->>AI: 执行: uv add requests

AI->>Bus: GET /sdk
Bus-->>AI: {code: "class AgentBusClient..."}

AI->>AI: 保存为 agent_bus_sdk.py

AI->>Bus: POST /register
name: "ai-coder-001"
capabilities: ["code", "debug"]
Bus-->>AI: {agent_id, token, card}

AI->>AI: 初始化 client
AI->>AI: self.agent_id = agent_id
AI->>AI: self.token = token

loop 每 30s
    AI->>Bus: client.inbox(since)
    Bus-->>AI: [messages]
    AI->>AI: LLM 处理每条消息
end
```

## 4. 状态流转图

### 4.1 消息生命周期

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  CREATED │───►│ STORED  │───►│ PENDING │───►│CONFIRMED│───►│ PROCESSED│
│  (创建)  │    │ (入队列) │    │ (待确认) │    │ (已确认) │    │ (已处理) │
└─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘
                    │                              │
                    ▼                              ▼
               ┌─────────┐                   ┌─────────┐
               │ DELIVERED│                  │ REJECTED │
               │ (已投递) │                  │ (已拒绝) │
               └─────────┘                   └─────────┘
```

### 4.2 Agent 状态机

```
         ┌──────────┐
         │  OFFLINE │
         │  (未注册) │
         └────┬─────┘
              │ POST /register
              ▼
         ┌──────────┐
         │  ONLINE  │◄────────┐
         │  (在线)  │         │
         └────┬─────┘         │
              │               │
    ┌─────────┼─────────┐     │ 心跳/轮询
    ▼         ▼         ▼     │
┌───────┐ ┌───────┐ ┌───────┐ │
│BUSY   │ │IDLE   │ │AWAIT  │─┘
│(处理中)│ │(空闲) │ │(等待人 │
└───────┘ └───────┘ │类确认) │
                    └───────┘
                         │
              DELETE /agents/id
                         ▼
                    ┌──────────┐
                    │  OFFLINE │
                    │  (已注销) │
                    └──────────┘
```

## 5. 数据流图

### 5.1 消息流转

```
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│  Sender │     │   Bus   │     │  Store  │     │ Receiver│
│  Agent  │────►│  /send  │────►│  Inbox  │────►│  /inbox │
│         │     │  接口   │     │  队列   │     │  轮询   │
└─────────┘     └─────────┘     └─────────┘     └─────────┘
     │                                              │
     │  1. 构造 Message                             │
     │  {msg_id, from, to, content}                │
     │                                              │
     │──────────────► 2. HTTP POST ────────────────►│
     │                 验证 token                   │
     │                 验证 recipient               │
     │                                              │
     │                 3. 存储到 inbox[to]            │
     │                                              │
     │                                              │◄── 4. 轮询 GET
     │                                              │    since=timestamp
     │                                              │
     │◄────────────── 5. 返回 msg_id ───────────────│
     │                                              │
     │                 6. 消息状态: delivered       │
```

## 6. 部署架构图

### 6.1 单实例（开发/内网）

```
┌─────────────────────────────────────────┐
│              单台服务器                 │
│  ┌─────────────────────────────────────┐│
│  │  Agent Bus (FastAPI + uvicorn)     ││
│  │  端口: 18080                        ││
│  │  存储: MemoryStore (内存)           ││
│  └─────────────────────────────────────┘│
│                   │                     │
│              ┌────┴────┐                │
│              ▼         ▼                │
│  ┌─────────────┐ ┌─────────────┐       │
│  │  Agent A    │ │  Agent B    │       │
│  │  (本地进程)  │ │  (本地进程)  │       │
│  └─────────────┘ └─────────────┘       │
└─────────────────────────────────────────┘
```

### 6.2 多实例/生产（Redis 后端）

```
┌─────────────────────────────────────────────────────────────┐
│                      负载均衡器 (Nginx)                       │
│                         443/HTTPS                           │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ Agent Bus   │ │ Agent Bus   │ │ Agent Bus   │
│ Instance 1  │ │ Instance 2  │ │ Instance 3  │
│  (uvicorn)  │ │  (uvicorn)  │ │  (uvicorn)  │
└──────┬──────┘ └──────┬──────┘ └──────┬──────┘
       │               │               │
       └───────────────┼───────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
       ┌─────────────┐   ┌─────────────┐
       │   Redis     │   │   Redis     │
       │  (主从复制)  │   │  (Sentinel) │
       │  存储/队列   │   │  高可用     │
       └─────────────┘   └─────────────┘
```

## 7. 与 A2A 协议的对比映射

```
┌─────────────────┬────────────────────────┬────────────────────────┐
│     概念        │      Agent Bus          │        A2A             │
├─────────────────┼────────────────────────┼────────────────────────┤
│ 能力声明        │ AgentCard              │ Agent Card             │
│ 注册中心        │ POST /register         │ Agent Discovery        │
│ 任务发送        │ POST /send             │ tasks/send             │
│ 任务接收        │ GET /inbox (轮询)      │ SSE streaming          │
│ 群组通信        │ POST /groups/...       │ (A2A 无原生群组)       │
│ 人类确认        │ POST /messages/confirm │ (A2A 无原生确认)       │
│ 认证方式        │ X-Agent-Id + X-Token   │ OAuth2 / API Key       │
│ 传输协议        │ HTTP + JSON            │ HTTP + SSE + JSON-RPC  │
│ 实时性          │ 轮询 (简单)             │ SSE 流式 (实时)        │
│ 复杂度          │ 极简                   │ 较复杂                 │
└─────────────────┴────────────────────────┴────────────────────────┘
```

## 8. 核心设计哲学

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent Bus 设计哲学                          │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │   极简主义   │  │  AI 原生    │  │  渐进增强   │         │
│  │             │  │             │  │             │         │
│  │ • HTTP only │  │ • 自描述    │  │ • 内存 →   │         │
│  │ • 轮询优先  │  │ • 自动生成  │  │   Redis    │         │
│  │ • 函数本地  │  │   SDK       │  │ • 轮询 →   │         │
│  │   调用      │  │ • LLM 看懂  │  │   SSE      │         │
│  │             │  │   就能用    │  │             │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│                                                             │
│  核心假设: Agent 是智能的，它能自己决定何时发送、何时轮询     │
│  人的角色: 给 Agent 一个 URL，剩下的交给它自己               │
└─────────────────────────────────────────────────────────────┘
```
