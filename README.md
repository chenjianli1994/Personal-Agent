# PersonalAgent

单用户、本地优先的 PersonalAgent 项目。后端使用 FastAPI，前端使用 React + Vite，数据默认写入本地 SQLite。

新会话接手请先读：

- `PROJECT_HANDOFF.md`
- `AGENTS.md`
- `CLAUDE.md`

## 后端

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe -m pytest -q
```

```powershell
.\.venv\Scripts\python.exe -m personal_agent serve --workspace . --db .personal_agent\agent.db --port 7870
```

## 前端

```powershell
cd frontend
npm install
npm run build
npm run typecheck
```

## 验证状态

当前结果：

- `.\.venv\Scripts\python.exe -m pytest -q` -> `94 passed`
- `.\.venv\Scripts\python.exe -m pytest tests\test_personal_memory_recall.py -q` -> `29 passed`
- `.\.venv\Scripts\python.exe -m pytest tests\test_personal_forbidden_scan.py -q` -> `2 passed`
- `frontend` 下 `npm run typecheck` 通过

## 文档与边界

- `personal_agent/content_guard.py` 是旧流程禁用词与退休输入键的唯一来源。
- 不提交 `.personal_agent/`、`.env`、`.venv/`、`frontend/node_modules/`、`frontend/dist/`。
- 运行态数据默认位于 `.personal_agent/agent.db`。
