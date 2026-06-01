# Agent_O

Agent_O 是一套面向珠宝门店的智能培训与经营辅助系统。项目把门店知识库、智能陪练、考试评估、成长计划、经营分析、Agent 协同编排和 EVO 自我进化机制整合到同一套前后端系统中，用于辅助新人培养、销售话术统一、门店经营复盘和项目演示答辩。

## 功能概览

- 在岗助手：支持产品知识、销售话术、异议处理、合规边界等门店场景问答。
- 知识问答：基于知识库内容生成结构化回答，适合新人学习和标准口径查询。
- 智能陪练：模拟顾客对话，提供逐轮反馈、导师金句、结果评估和能力更新。
- 考试中心：支持动态考核、标准试卷生成、试卷评阅和阶段晋级解锁。
- 成长计划：围绕 14 天成长路径、阶段任务、能力画像和成长轨迹形成训练闭环。
- 经营看板：辅助店长查看门店风险、经营指标、库存、复购和排班协同问题。
- 一句话查询：将自然语言问题解析为经营查询或分析建议。
- Agent 协同：可视化展示不同业务 Agent 与 Dify 工作流之间的调用关系。
- EVO 进化：沉淀语义记忆、反思经验和技能规则，用于持续优化系统能力。

## 技术栈

- 后端：FastAPI、SQLAlchemy、SQLite、PyJWT、python-dotenv
- 前端：原生 HTML/CSS/JavaScript
- 智能工作流：Dify 工作流与知识库 API
- 测试：Pytest、Node.js 脚本、Playwright
- 静态资源：Three.js、ECharts、Flatpickr、Tailwind frozen CSS

## 目录结构

```text
Agent_O/
├── backend/        # FastAPI 后端、业务路由、数据库模型、测试
├── frontend/       # 前端页面、样式、交互脚本、前端测试
├── workflow/       # Dify 工作流导出文件
├── 知识库/          # 门店知识库资料
├── PRD/            # 产品、技术、UI 和系统说明文档
└── README.md
```

## 本地运行

### 1. 准备后端环境

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

编辑 `backend/.env`，填入 Dify、知识库、JWT 等必要配置。不要把真实 `.env` 提交到 Git。

### 2. 启动后端

```powershell
cd backend
.\.venv\Scripts\activate
python main.py
```

默认服务地址由 `backend/.env` 中的 `UVICORN_HOST` 和 `UVICORN_PORT` 控制。前端静态页面会由后端挂载到 `/frontend/`。

### 3. 安装前端测试依赖

```powershell
cd frontend
npm install
```

前端是静态页面，不依赖构建步骤。安装依赖主要用于运行 Playwright / Node.js 回归测试。

## 测试

后端测试：

```powershell
cd backend
.\.venv\Scripts\activate
pytest
```

前端测试：

```powershell
cd frontend
npm test
```

## 配置与安全

仓库已忽略以下本地运行文件：

- `backend/.env`
- SQLite 数据库文件：`*.db`、`*.db-shm`、`*.db-wal`
- 日志、缓存、临时目录、Python 字节码
- `frontend/node_modules`
- 开发检查截图

如果真实密钥曾经被提交到公开仓库，请立即到对应平台轮换 API Key，并根据需要重写 Git 历史。

## 说明文档

更多产品和系统说明见：

- `PRD/`
- `知识库/`
- `workflow/`
