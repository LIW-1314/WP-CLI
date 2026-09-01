# PAI.md

本文件是 WpCLI Python 的项目级长期上下文，供 Agent、新线程和协作者首读。只记录跨任务稳定的
工程约定；一次性需求、临时排障结论和个人偏好不要写入本文件。

## 信息优先级

1. 代码与测试的实际行为
2. `AGENTS.md`（若后续新增）
3. 本文件 `PAI.md`
4. `README.md`
5. `docs/parity.md`

文档中的“支持”必须能在真实公开入口运行，不能只依据 README、提示词包装或未接线的内部类。

## 项目定位

- 项目名：WpCLI Python
- 定位：运行在终端中的 Python AI Agent CLI，对标 Claude Code，并与 WpCLI Java/TypeScript
  的公开能力保持一致。
- 产品要求：这是可真实使用的 CLI，不是演示项目；禁止假数据、仅做界面的空壳功能和无法运行的
  占位实现。
- Python：3.11+
- 包管理与运行：优先使用 `uv`
- 主要入口：交互式 REPL、单次 Prompt、SDK、MCP Server、Runtime API 和后台 Task Worker。

## 常用命令

```bash
uv sync --extra dev
uv run wpcli
uv run wpcli -p "解释这个仓库"
uv run wpcli --mode plan -p "先规划再执行"
uv run wpcli --mode team --worker-mode plan -p "并行审计核心模块"
uv run wpcli doctor --cwd .
```

验证命令：

```bash
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev python -m pytest
uv build
uv run wpcli --version
uv run wpcli doctor --cwd .
```

在当前环境中，测试优先使用 `uv run --extra dev python -m pytest`，不要假设裸 `pytest` 或
`uv run --extra dev pytest` 一定可用。

## 架构概览

三条 Agent 执行路径必须共享真实的工具、策略、记忆、Skill 和快照能力：

| 路径 | 主要入口 | 触发方式 |
| --- | --- | --- |
| ReAct | `agent/agent.py` | 默认交互或 `-p` |
| Plan-and-Execute | `agent/plan_execute.py` | `/plan` 或 `--mode plan` |
| Multi-Agent | `agent/orchestrator.py` | `/team` 或 `--mode team` |

核心模块：

```text
src/wpcli/
├── agent/       ReAct、Plan-and-Execute、Multi-Agent 编排
├── entrypoints/ Typer CLI 与 prompt-toolkit REPL
├── llm/         模型抽象、OpenAI-compatible 客户端、usage/cost
├── tools/       工具定义、执行器、文件与命令操作
├── policy/      PathGuard、CommandGuard、AuditLog
├── prompt/      静态 Prompt 与每请求动态上下文
├── context/     上下文预算和压缩
├── memory/      SQLite 长期记忆、召回和治理
├── skill/       builtin/user/project Skill 发现与注入
├── plan/        计划、任务和 DAG
├── mcp/         MCP Client、Server 与动态工具注册
├── runtime/     Thread/Turn API 和持久化任务队列
├── render/      Rich/纯文本终端渲染
├── snapshot/    执行前后快照与恢复
└── rag/         本地代码索引与搜索
```

代码定位优先使用 `rg`、按需读取具体文件和行段；RAG `search_code` 是语义辅助，不代替精确文本
搜索。

## 修改联动规则

- 改 CLI/斜杠命令：同步检查 `entrypoints/cli.py`、`entrypoints/repl.py`、README 和测试。
- 改 Agent 行为：同时检查 ReAct、Plan、Team 和 SDK/Runtime 等公开入口，避免只修一条路径。
- 改工具：同步检查 Tool schema、`ToolExecutor`、只读/并发/危险级别、审批策略、审计和测试。
- 改 HITL 或权限模式：同步检查审批回调、PathGuard、CommandGuard、MCP 写工具、终端状态显示和
  恢复默认策略的行为。
- 改模型协议：同步检查 factory、流式事件、tool call、reasoning、usage、价格配置和文档。
- 改 Memory/Prompt/Skill：保持静态上下文、动态召回和一次性 Skill 注入的边界清晰，防止跨 Agent
  串线。
- 改 Runtime Task：必须保留项目作用域、原子领取、租约恢复和取消后不被迟到结果覆盖等语义。
- 行为或公开能力发生变化时，更新 `README.md` 和 `docs/parity.md`，但文档不能先于实现宣称完成。

## 权限与安全

- 默认模式保留 HITL、工作区路径限制、命令守卫和审计日志。
- 交互式 `Auto (full access)` 是用户显式选择的会话级最高权限模式：不请求审批，并关闭路径与
  命令守卫；切回 `Default` 必须恢复启动时的原策略。
- 非交互入口不能因为缺少审批界面而静默放行危险操作。
- 不提交 `.env`、真实 API Key、Token、Cookie、私钥、用户级数据库或审计日志。
- 日志、异常和测试输出不得泄露凭据；新增配置对外展示时必须继续脱敏。
- 不使用破坏性 Git 命令覆盖用户改动；工作区不干净时只修改当前任务涉及的文件。

## 完成标准

- 先做针对性测试，再跑与风险相称的完整回归。
- Python 改动至少通过 Ruff 检查和格式检查；打包相关改动需要执行 `uv build`。
- CLI/TUI 行为不能只做单元测试：条件允许时还要运行真实 `wpcli` 命令或终端输入流程。
- 修复测试失败时区分本次回归与工作区原有问题；无法完成的外部依赖验证要明确说明。
- 未经用户要求，不自动提交或推送 Git。

## 已知产品边界

- 真实 LLM 调用需要有效 API Key。
- Chrome DevTools MCP 需要兼容的 Node.js、npm/npx 和 Chrome 环境。
- Runtime API 的真实 turn 执行依赖可用模型配置。
- Java 版私有微信 iLink 通道不应在缺少协议和凭据时用假实现冒充支持。

形成新的稳定工程规则时可以精炼后补充到本文件；实现细节、临时状态和长篇设计说明应放到
对应代码、测试或 `docs/`，避免项目上下文持续膨胀。

