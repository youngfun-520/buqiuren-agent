# 不求人｜FastAPI + React 产品工程

本工程从已验收的 Notebook 版真实智能体逻辑拆分而来，保留：

- `new_buqiuren_session`
- `run_buqiuren`
- `session.ask`
- `session.choose`
- 统一 `frontend_payload`：`session_id / type / message / timeline / quick_replies / card / actions / sources`
- Firecrawl MCP-only，不提供 REST fallback
- 追问 slot 合并逻辑
- 居住证、公积金、工资拖欠三个案例不同结果

## 目录

```text
backend/
  app/main.py
  app/api/chat.py
  app/core/config.py
  app/agent/session.py
  app/agent/workflow.py
  app/agent/providers/firecrawl_mcp.py
  app/agent/llm.py
  app/agent/schemas.py
  app/storage/guide_store.py
  data/buqiuren_production_guide_kb.json
  requirements.txt
  .env.example
frontend/
  package.json
  index.html
  src/main.tsx
  src/App.tsx
  src/api.ts
  src/types.ts
  src/components/ChatWindow.tsx
  src/components/Timeline.tsx
  src/components/ServiceCard.tsx
  src/components/QuickReplies.tsx
  src/components/Sources.tsx
  src/styles.css
```

## 安装后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

## 配置 `.env`

填写 MiniMax 与 Firecrawl MCP：

```bash
MINIMAX_API_KEY=...
MINIMAX_BASE_URL=...
MINIMAX_MODEL=...

SEARCH_PROVIDER=firecrawl_mcp
FIRECRAWL_API_KEY=...
FIRECRAWL_API_URL=http://176.126.87.5:3002
FIRECRAWL_MCP_COMMAND=npx
FIRECRAWL_MCP_ARGS=-y firecrawl-mcp
```

说明：本工程不读取、不打包你的真实 `.env`。压缩包里只包含 `.env.example`。

## 启动后端

```bash
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## 安装并启动前端

```bash
cd frontend
npm install
npm run dev
```

默认前端请求 `http://127.0.0.1:8000`。如需修改：

```bash
echo 'VITE_API_BASE=http://127.0.0.1:8000' > .env.local
```

## API

### `GET /health`

返回服务状态。

### `POST /chat`

```json
{
  "session_id": "可选",
  "message": "深圳居住证怎么办？"
}
```

### `POST /choose`

```json
{
  "session_id": "...",
  "reply": "首次办理"
}
```

所有响应都统一返回：

```json
{
  "session_id": "...",
  "type": "...",
  "message": "...",
  "timeline": [],
  "quick_replies": [],
  "card": null,
  "actions": [],
  "sources": []
}
```

## 三个验收案例

### 1. 居住证

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"深圳居住证怎么办？"}'
```

预期：返回 `clarification_required`，quick reply 包含 `首次办理`。

继续：

```bash
curl -s -X POST http://127.0.0.1:8000/choose \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"上一步返回的 session_id","reply":"首次办理"}'
```

预期：不能 error，优先返回 `service_card`。

### 2. 公积金

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"深圳公积金怎么提取？"}'
```

预期：返回 `clarification_required`，quick reply 包含 `租房提取`，不会返回居住证。

继续选择 `租房提取` 后，预期返回公积金相关 `service_card`。

### 3. 工资拖欠

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"公司拖欠工资怎么办？"}'
```

预期：返回工资拖欠投诉相关结果，不能返回居住证/公积金。

## 生产说明

- 当前会话存储是进程内 `SESSION_STORE`，后续可替换 Redis。
- 当前知识库使用本地 JSON，路径由 `BUQIUREN_KB_PATH` 控制。
- 本地 KB 命中时不会触发 Firecrawl；缺失事项才会触发 MCP 搜索、抓取与 LLM 抽取。
- 后端对外隐藏 traceback；`DEV_MODE=true` 仅用于本地调试。
