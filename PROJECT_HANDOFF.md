# PersonalAgent 项目交接文档

本文档用于在新对话中快速让协作者理解 `E:\codex_pro\PersonalAgent` 当前状态、运行方式、关键模块与注意事项。

## 1. 项目是什么

`PersonalAgent` 是一个已经从 `AspiceAgent` 独立拆分出来的单用户本地智能助手项目。

当前项目形态：

- 后端：`personal_agent/`，基于 FastAPI
- 前端：`frontend/`，基于 React + Vite
- 数据库：本地 SQLite，默认位于 `.personal_agent/agent.db`
- 运行模式：支持真实 LLM，也支持 `--fake-llm` 做离线冒烟和测试

这个仓库现在应当按“独立项目”理解，不再依赖 `AspiceAgent` 里的 `aspice_platform` 包。

## 2. 当前迁移状态

截至 2026-06-17，当前仓库内和本项目直接相关的拆分工作已经完成：

- `personal_agent/**` 不再 import `aspice_platform`
- LLM 网关已改为 personal-only 命名和环境变量
- 已有独立 `init_db`，能自行初始化数据库
- personal draft、knowledge、codebase、learning、tool audit 等核心链路可独立运行
- 前端已独立 build 通过
- fake-llm 服务冒烟通过
- 测试通过：`60 passed`

Git 里最近两个关键提交：

1. `6350982` `完成个人Agent独立数据库裁剪与LLM平台残留清理`
2. `0a5a9f5` `完成个人Agent尾项收尾并通过前后端最终验收`

远端仓库：

- [chenjianli1994/Personal-Agent](https://github.com/chenjianli1994/Personal-Agent)

## 3. 当前目录结构

```text
E:\codex_pro\PersonalAgent
├─ personal_agent/          # 后端主代码
├─ frontend/                # 前端代码
├─ knowledge/               # 启动时导入的知识/模板资产
├─ tests/                   # personal 项目测试集
├─ sample_project/          # 示例输入与产物样例
├─ .venv/                   # 当前仓库自己的 Python 虚拟环境
├─ .personal_agent/         # 本地运行时数据目录
├─ pyproject.toml           # Python 包与入口定义
├─ README.md
└─ PROJECT_HANDOFF.md       # 本文档
```

## 4. 关键入口

### 4.1 后端应用入口

- 应用工厂：`personal_agent/app.py`
- CLI：`personal_agent/cli.py`
- 运行时总控：`personal_agent/runtime.py`
- 路由注册：`personal_agent/routes.py`

最关键的调用链：

`cli.py` -> `app.py` -> `bootstrap.py` -> `routes.py` -> `runtime.py` / feature modules -> `personal_agent/core/**`

### 4.2 数据库入口

- 数据库初始化：`personal_agent/core/database.py`
- 启动时初始化位置：`personal_agent/bootstrap.py`

`init_db(db_path)` 现在是独立项目自己的 DB 初始化入口。

### 4.3 LLM 入口

- 网关：`personal_agent/core/llm_gateway.py`
- 管理配置：`personal_agent/core/llm_admin.py`
- fake provider：`personal_agent/core/fake_llm_provider.py`

核心环境变量前缀统一为：

- `PERSONAL_AGENT_LLM_PROVIDER`
- `PERSONAL_AGENT_LLM_MODEL`
- 以及各 provider 对应 API key

### 4.4 前端入口

- 前端主入口：`frontend/src/main.tsx`
- 应用入口：`frontend/src/App.tsx`
- personal UI：`frontend/src/personal/PersonalAgentApp.tsx`

## 5. 现在怎么启动

### 5.1 后端安装

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[dev]
```

如果 `.venv` 不存在，可先创建：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .[dev]
```

### 5.2 后端运行

推荐离线冒烟命令：

```powershell
.\.venv\Scripts\python.exe -m personal_agent serve --workspace . --db .personal_agent\agent.db --port 7870 --fake-llm
```

服务关键接口：

- `GET /api/personal/context`
- `POST /api/personal/chat/turn`
- `POST /api/personal/artifacts/propose`
- `POST /api/personal/patch/propose`

### 5.3 前端运行

```powershell
cd frontend
npm install
npm run dev
```

默认开发地址：

- [http://127.0.0.1:5173](http://127.0.0.1:5173)

### 5.4 前端构建

```powershell
cd frontend
npm run build
```

## 6. 当前验证基线

### 6.1 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

当前基线结果：

- `60 passed`

说明：

- `tests/conftest.py` 会自动注入 `PERSONAL_AGENT_LLM_PROVIDER=fake`
- 因此默认测试不依赖外部 LLM 服务

### 6.2 服务冒烟

已验证以下命令可通过：

```powershell
.\.venv\Scripts\python.exe -m personal_agent serve --workspace . --db .personal_agent\agent.db --port 7870 --fake-llm
```

并确认：

- `GET /api/personal/context` 返回 `200`

### 6.3 前端构建

已验证：

```powershell
cd frontend
npm run build
```

当前结果：构建成功。  
存在 Vite chunk size warning，但不阻塞构建。

## 7. 关键设计约束

后续协作时请默认遵守下面这些约束：

1. `personal_agent/**` 不应重新引入 `aspice_platform` 依赖
2. 用户可见文案默认使用中文
3. personal draft 与正式发布记录要继续保持边界，不要把个人草稿路径重新做成平台发布链路
4. `patch_propose` 当前默认语义是 personal candidate draft，不应无意回退成平台 `artifacts` 默认写入
5. 数据库变更优先在 `personal_agent/core/database.py` 统一维护，不要在业务代码里散落 schema 假设

## 8. 重要模块速览

### 8.1 对话与意图

- `personal_agent/runtime.py`
- `personal_agent/intent_router.py`
- `personal_agent/policy_guard.py`
- `personal_agent/context_builder.py`

职责：

- 组织会话上下文
- 做 personal 意图路由
- 控制是否允许生成草稿、修订、代码 patch、学习反馈

### 8.2 草稿与产物

- `personal_agent/artifact_generation.py`
- `personal_agent/artifact_drafts.py`
- `personal_agent/artifact_export.py`
- `personal_agent/artifact_quality.py`

职责：

- 生成 personal 文档草稿
- 管理草稿 revision
- 导出 md/docx/xlsx/diff
- 质量门检查

### 8.3 知识与学习

- `personal_agent/knowledge_learning.py`
- `personal_agent/learning_reflector.py`
- `personal_agent/skill_reflector.py`
- `personal_agent/skill_runtime.py`
- `personal_agent/skill_registry.py`

职责：

- 从输入材料和会话中形成知识/候选记忆
- 形成技能更新候选
- 在会话内暂时遵循候选规则，明确批准后再沉淀为长期规则

### 8.4 代码库能力

- `personal_agent/core/codebase/`
- `personal_agent/core/tool_registry.py`

职责：

- 建索引、符号检索、影响分析
- patch propose / validate / apply
- build / test / static analysis 的白名单执行

## 9. 新对话建议直接带上的上下文

如果以后在新对话里继续这个项目，建议直接把下面这段发给协作者：

```text
项目路径：E:\codex_pro\PersonalAgent

这是一个已从 AspiceAgent 独立拆出的 PersonalAgent 项目。
当前状态：
- personal_agent/** 已无 aspice_platform import
- 独立 DB / 独立前端 / 独立打包入口已完成
- 当前仓库自带 .venv，可直接运行
- pytest 基线为 60 passed
- fake-llm 冒烟命令：
  .\.venv\Scripts\python.exe -m personal_agent serve --workspace . --db .personal_agent\agent.db --port 7870 --fake-llm

请先阅读：
1. PROJECT_HANDOFF.md
2. README.md
3. personal_agent/app.py
4. personal_agent/runtime.py
5. personal_agent/routes.py
6. personal_agent/core/database.py

除非我明确要求，否则不要把 aspice_platform 依赖重新引回这个仓库。
```

## 10. 当前已知非阻塞事项

1. 前端 build 存在 chunk size warning
   - 不影响当前交付
   - 后续可考虑做按路由或按模块拆包

2. 测试运行时会看到 `fastapi.testclient` 的 deprecation warning
   - 当前不阻塞
   - 后续升级依赖时可以统一处理

3. 仓库根目录当前可能存在本地未跟踪文件 `CLAUDE.md`
   - 这是本地协作辅助文件，不属于本次核心交付基线
   - 后续处理前先确认是否要纳入版本控制

## 11. 一句话结论

这是一个已经完成独立拆分并经过后端测试、前端构建、fake-llm 冒烟验证的本地 PersonalAgent 项目；新对话接手时，应把它当成独立仓库继续演进，而不是 `AspiceAgent` 的子模块。
