# Agent Bus 测试报告

## 测试时间

2026-04-30

## 测试范围

覆盖两种后端存储：
- **MemoryStore**（默认，无环境变量）
- **MongoStore**（`MONGODB_URL=mongodb://localhost:27017`）

测试脚本位置：`tests/test_e2e.py`

---

## MemoryStore 测试结果

| 功能 | 结果 |
|------|------|
| 注册 Agent | ✅ |
| Label 过滤查询 | ✅ |
| 点对点发消息 | ✅ |
| 收件箱拉取 + 自动标记已读 | ✅ |
| 未读-only 过滤 | ✅ |
| 批量标记已读 | ✅ |
| 人类确认（approve / reject） | ✅ |
| 创建 / 加入 / 广播群组 | ✅ |
| 注销 Agent | ✅ |
| Admin stats（总消息 / 未读 / 延迟） | ✅ |
| Admin agents（含未读数） | ✅ |
| Admin messages（全局消息流） | ✅ |
| PATCH 修改 label | ✅ |
| 前端 Dashboard HTML | ✅ |

---

## MongoDB 测试结果

| 功能 | 结果 |
|------|------|
| 全部 MemoryStore 功能 | ✅ |
| 数据持久化（重启不丢） | ✅ |
| 平均读取延迟统计 | ✅ |

---

## 测试中发现并修复的问题

### 1. MemoryStore / RedisStore 缺少 `mark_read` / `mark_all_read`
- **现象**：`POST /messages/{id}/read` 和 `POST /messages/read-all` 返回 500
- **修复**：在 `MemoryStore` 和 `RedisStore` 中补上了这两个方法

### 2. MemoryStore Admin API 返回 501
- **现象**：`/admin/messages`、`/admin/stats`、`PATCH /admin/agents/{id}` 返回 501
- **修复**：在 `MemoryStore` 中补上了：
  - `admin_list_messages`
  - `admin_get_stats`
  - `admin_update_agent_labels`

### 3. datetime 时区比较报错
- **现象**：`TypeError: can't compare offset-naive and offset-aware datetimes`
- **原因**：`read_at` 和 `delivered_at` 的时区信息不一致
- **修复**：统一使用 `datetime.utcnow()`（offset-naive）

### 4. MongoDB inbox 首次返回 `read_at=null`
- **现象**：首次拉取 inbox 时，`mark_read` 已修改数据库，但返回的 JSON 中 `read_at` 仍为 null
- **原因**：`MongoStore` 修改的是数据库记录，不是已返回的 Pydantic 对象
- **修复**：在 `main.py` 的 `get_inbox` 中，`mark_read` 后立即更新返回对象的 `read_at` 字段，确保行为一致

---

## 遗留问题

- **RedisStore** 未做完整端到端测试（当前环境无 Redis）
- **前端交互** 仅验证了 HTML 返回和 API 数据正确性，未手动点击测试页面按钮
