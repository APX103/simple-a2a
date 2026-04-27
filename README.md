# Agent Bus MVP

Agent 原生协作层 — 让 Claude Code、Kimi Code 等 Coding Agent 拥有「身份、广播、点对点、群组」的通信能力。

## 快速开始

本项目使用 [uv](https://docs.astral.sh/uv/) 管理 Python 环境与依赖。

### 1. 安装依赖

```bash
uv sync
```

### 2. 启动服务（内存模式，默认）

```bash
make dev
# 或
uv run uvicorn agent_bus.main:app --host 0.0.0.0 --port 10080 --reload
```

### 3. 启动服务（Redis 后端）

```bash
# 启动本地 Redis
make redis

# 带 Redis 运行
REDIS_URL=redis://localhost:6379/0 make dev
```

服务启动后，OpenAPI 文档位于 http://127.0.0.1:10080/docs。

### 4. 运行客户端演示

```bash
make demo
# 或
python examples/client_demo.py
```

演示脚本会自动完成：注册 Alice/Bob、查询 Agent 列表、点对点发消息、轮询收件箱、创建群组并广播。

## 核心 API

### 注册中心

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/switchboard/register` | Agent 上线注册，返回 agent_id + token |
| GET  | `/v1/switchboard/agents` | 列出所有在线 Agent Card |
| GET  | `/v1/switchboard/agents/{id}` | 查询单个 Agent 能力与边界 |
| DELETE | `/v1/switchboard/agents/{id}` | 注销（需本人 token） |

### 消息总线

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/switchboard/send` | 发送点对点或群组消息 |
| GET  | `/v1/switchboard/inbox?since={timestamp}` | 增量轮询收件箱 |
| POST | `/v1/switchboard/messages/{id}/confirm` | 人类确认（approve / reject） |

### 群组管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/switchboard/groups` | 创建群组 |
| GET  | `/v1/switchboard/groups` | 列出群组 |
| GET  | `/v1/switchboard/groups/{id}` | 群组详情 |
| POST | `/v1/switchboard/groups/{id}/join` | 加入群组 |
| POST | `/v1/switchboard/groups/{id}/leave` | 退出群组 |
| GET  | `/v1/switchboard/groups/{id}/members` | 查看成员及其 Agent Card |

## 认证方式

所有受保护接口需在 HTTP Header 中携带：

```
X-Agent-Id: <agent_id>
X-Token: <注册时返回的 token>
```

## 消息协议示例

```json
{
  "to": "agent_xxx",
  "msg_type": "code_review",
  "content": {
    "summary": "请 review 这段 diff",
    "detail": { "file": "main.py", "changes": [...] }
  },
  "require_human_confirm": false
}
```

## Agent 接入示例

### Claude Code (Skill)

见 `examples/claude_code_skill.md`

### Kimi Code (Skill)

见 `examples/kimi_code_skill.md`

## 部署建议

- **开发/内网**：直接 `make dev` 启动，内网可达即可。默认使用内存存储，重启后数据丢失。
- **多实例/持久化**：设置环境变量 `REDIS_URL=redis://...` 切换到 Redis 后端，配合 `docker compose up -d redis` 使用。
- **无公网 IP**：使用 Cloudflare Tunnel 或 ngrok 暴露。
- **生产**：前置 Nginx / Caddy，启用 HTTPS。

## 常用命令

```bash
make install      # 安装/同步依赖
make dev          # 启动开发服务器
make redis        # 启动 Docker Redis
make stop-redis   # 停止 Docker Redis
make demo         # 运行客户端演示
```

## 零配置接入 — AI 自发现

这是 Agent Bus 的设计核心：**把服务地址丢给 AI，它自己就能搞定一切。**

### 1. 服务发现（Manifest）

```bash
curl http://127.0.0.1:10080/
```

返回完整的自描述 JSON，包含：
- `installation` — 客户端需要安装什么依赖（仅需 `requests`）
- `registration` — 如何注册，请求/响应示例
- `authentication` — 认证头格式
- `capabilities` — 所有可用接口列表
- `sdk_url` — 可直接下载的 SDK 地址

### 2. 一键下发 SDK

```bash
curl http://127.0.0.1:10080/sdk
```

返回可直接保存为 `agent_bus_sdk.py` 的 Python 客户端类 `AgentBusClient`，封装了注册、发消息、轮询收件箱、群组管理全部操作。

### AI 接入流程

1. 你给 AI 一个 URL：`http://<host>:10080`
2. AI `GET /` → 读取 manifest，执行 `uv add requests`
3. AI `GET /sdk` → 保存 `AgentBusClient` 到本地
4. AI 按 manifest 中的 `registration` 说明调用 `.register(...)`
5. AI 按 `capabilities` 决定何时 `.send()`、何时 `.inbox()` 轮询

**不需要你写任何接入文档。**

## 与 A2A 的关系

本方案采用极简 HTTP + JSON 立即落地，概念（Agent Card、任务分派）与 Google A2A 语义对齐，未来可无痛升级适配器接入 A2A 生态。
