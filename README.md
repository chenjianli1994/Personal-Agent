# PersonalAgent

独立 personal agent 项目。

## 后端

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe -m pytest -q
```

```powershell
.\.venv\Scripts\python.exe -m personal_agent serve --workspace . --db .personal_agent\agent.db --port 7870 --fake-llm
```

## 前端

```powershell
cd frontend
npm install
npm run build
npm run typecheck
```
