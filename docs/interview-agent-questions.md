# WpCLI Agent 面试题实现口径

这份文档对应文章中的 16 个问题。口径以 Python 仓库的真实实现为准，不把配置项、Prompt 包装或单元测试桩冒充完整能力。

## 1. WpCLI 项目和流程

公共入口包括交互式 REPL、一次性 CLI、SDK、Runtime API 和后台 worker。一次 ReAct 请求的主链路是：

1. 加载用户/项目/环境配置并装配内置工具与 MCP 工具。
2. 构造静态 Prompt，再按本次输入构造动态 Prompt。
3. 做相关 Skill Top-K 候选匹配和动态长期记忆召回。
4. 在 ReAct 循环中接收文本、thinking、tool call 和 usage。
5. ToolExecutor 负责并发策略、HITL、路径/命令保护和审计。
6. 工具结果回灌模型，直到模型结束或达到最大轮数。
7. 聚合历史、usage/cost，并在 run 前后创建快照。

实现入口：`src/wpcli/entrypoints/`、`src/wpcli/agent/query.py`、`src/wpcli/tools/executor.py`。

## 2. 有实现子 Agent 吗

有。`/team` 不是给普通 ReAct 加一段“你是多 Agent”的 Prompt，而是实际创建 Planner、多个 Worker 和 Reviewer：

- Planner 输出带依赖的步骤 DAG。
- 无依赖步骤进入并行 worker queue。
- Worker 使用真实工具完成步骤。
- Reviewer 输出结构化批准/问题列表。
- 未通过时有上限重试；耗尽后步骤标记 FAILED，不再伪装成完成。

实现：`src/wpcli/agent/orchestrator.py`。

## 3. 支持后台任务吗

支持。Runtime API 或 REPL 可以把 `react|plan|team` 任务写入 SQLite；`wpcli serve` 和独立的 `wpcli worker` 都能消费。

可靠性不是“开一个线程”就结束：

- `BEGIN IMMEDIATE` 原子领取，避免多个 worker 重复执行。
- 任务按项目 cwd 隔离。
- worker 持有 lease 并定期 heartbeat；崩溃后的过期任务可重新领取。
- cancel 后，迟到的 worker 结果不能把 canceled 覆盖回 completed。
- 每个任务记录 mode、attempts、worker 和 lease。

实现：`src/wpcli/runtime/tasks.py`、`src/wpcli/runtime/api.py`。

## 4. 子 Agent 也支持 Plan 模式吗

支持。Team 的每个 ExecutionStep 都有 `mode=react|plan`：

- react Worker 直接进入工具调用循环。
- plan Worker 委托独立 `PlanExecuteAgent`，由它再生成一个任务 DAG、按依赖批次执行。
- 默认最大嵌套深度为 1，避免无限递归规划。

REPL 可用 `/team --plan <task>`，一次性 CLI 可用 `--mode team --worker-mode plan`，SDK 的 `team_complete(..., worker_mode="plan")` 也能显式选择。

## 5. 子 Agent 怎么调用 Skill

每个 Worker 拿到完整 ToolRegistry，因此可以调用 `load_skill(name)`。调用链是：

1. 当前输入先得到 Skill 候选。
2. 模型判断候选是否适用并调用 `load_skill`。
3. Skill 正文写入该 Worker 独占的 `SkillContextBuffer`。
4. 工具执行后立即 drain，并附到当前 query 的下一模型轮。

关键点是“当前任务下一轮生效”和“每个并发 Worker 独占 buffer”。旧实现只在 query 开始时 drain，导致正文延迟到下一个用户请求；所有 Worker 还共用 buffer，存在串线。对应回归测试已覆盖这两个边界。

## 6. Skill 分层体系为什么这样设计

加载顺序是 builtin → user → project，同名时后层覆盖前层：

- builtin：开箱即用的产品默认能力。
- user：个人跨项目复用的工作流。
- project：依赖当前仓库事实的规则，优先级最高。

这个顺序同时解决“默认可用”“个人习惯复用”和“项目事实优先”三个问题。Skill 还带 description、version、tags 和启用状态。

实现：`src/wpcli/skill/registry.py`。

## 7. 用户输入如何匹配 Skill

采用两阶段选择：

1. Harness 用 name、description、tags 做中英文词法/字符 n-gram Top-K 召回，显式 Skill 名优先，并过滤禁用项。
2. 模型看到少量候选后做最终判断，只有适用时才调用 `load_skill` 读取正文。

这样不用把所有 Skill 正文塞进上下文，也不会只按名称排序截取前 20 个。

## 8. 有 Skill 沉淀机制吗

有，但不会静默自动写。模型发现成功流程具备稳定输入、明确步骤和复用价值时，可以调用 `save_skill` 提议沉淀：

- 选择 project 或 user scope。
- 校验安全 slug，拒绝路径穿越。
- 默认拒绝覆盖；更新必须显式 `overwrite=true`。
- `save_skill` 强制 HITL，用户批准后才写 `SKILL.md`。

所以它既不是“只能用户手写”，也不是“模型随便改自己的行为”。

## 9. 长短期记忆怎么设计

- 短期记忆：当前 session/thread 的消息、工具调用和工具结果；达到预算后把旧轮次压成滚动摘要。
- 静态长期记忆：AGENTS/PAI/custom prompt 文件，人工维护、可审查、可版本控制。
- 动态长期记忆：SQLite，按 cwd scope 隔离，记录 kind、source、importance、confidence、TTL、访问次数和内容哈希。

Runtime thread 也持久化 user/assistant 消息，不再每个 HTTP turn 都从空历史开始。

## 10. 为什么同时要静态和动态长期记忆

静态记忆适合架构约束、命令规范、安全规则等高确定性事实；它变更慢，应该让人审核并跟随仓库版本。

动态记忆适合运行中学到的偏好、纠错和决策；它变化快，必须可检索、去重、过期和淘汰。把两者混成一个“最近消息列表”，要么规范不稳定，要么动态事实无限膨胀。

## 11. 什么时候写长期记忆，怎么避免积累过快

触发原则写进了静态 Prompt 和工具描述：只保存显式“记住”、稳定偏好、长期项目约束、用户纠错或可复用决策；不保存密钥、临时状态、原始日志和不确定结论。

治理措施包括：

- 空值、非法评分和超长内容拒绝。
- NFKC/大小写/空白规范化后的 SHA-256 去重；重复保存更新原记录。
- TTL 自动清理。
- 每个 scope 有容量上限，按重要度、置信度、访问热度和时效淘汰。
- Prompt 只注入相关 Top-K，不因数据库增长而线性变长。

## 12. 模型怎么决定是否召回长期记忆

使用双通道：

- 每次请求前，Harness 先按当前问题做便宜的相关性 Top-K，作为候选注入动态 Prompt。
- 如果问题明显依赖“之前的决定/我的偏好”，但候选不足，模型可主动调用 `search_memory`，按 query 和 kind 深搜。

这比“无条件塞最近 8 条”更相关，也比每次先额外调用一个模型做路由更省成本。

## 13. 压缩机制、窗口和阈值

DeepSeek V4 Flash/Pro 当前 profile 是 1M context；`max_tokens` 是单次最大输出配置，不等于上下文窗口。

可用输入预算计算为：

```text
context_window - max_output_tokens - reserve_tokens
```

默认达到可用输入预算的 80% 触发压缩，目标压到约 55%。20% 安全区用于下一轮输出、不断增长的工具结果和无 tokenizer 估算误差。

压缩顺序：旧轮次提取式滚动摘要 → 保留最近原文 → 工具调用与 tool result 成对保留 → 仅在必要时截断超大工具输出。当前用户输入不会被静默改写；摘要只属于短期记忆，不自动写进长期库。

实现：`src/wpcli/context/manager.py`。

## 14. 动态 Prompt 和静态 Prompt

静态前缀包含身份、稳定规则、角色协议和项目指令，适合 provider 前缀缓存。

动态后缀每个请求重建，包含当前时间、cwd、模型、工具和本问题相关的长期记忆。Skill 候选也按当前输入动态生成。记忆和工具结果都标记为低信任数据，不能冒充系统规则。

实现：`src/wpcli/prompt/assembler.py`。

## 15. 模型、Token 和成本

默认底座是 `deepseek-v4-flash`，也支持自定义 OpenAI-compatible provider。内置 DeepSeek V4 价格截至 2026-07-17：

| 模型 | 上下文 | 输入 cache hit | 输入 cache miss | 输出 |
|---|---:|---:|---:|---:|
| V4 Flash | 1M | ¥0.02/M | ¥1/M | ¥2/M |
| V4 Pro | 1M | ¥0.025/M | ¥3/M | ¥6/M |

官方价格会调整，代码允许 `llm.context_window` 和 `llm.prices` 覆盖。来源：[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)。

“写一千行代码”不能只按行数精确计费。若每行约 50–100 个英文/代码字符，可先粗估约 1.5万–3万输出 Token；按 Flash 当前纯输出价约 ¥0.03–¥0.06，还要加每一轮输入、工具结果、Planner、Reviewer、失败重试等。最终必须以 API usage 为准。

WpCLI 已开启流式 usage，兼容 `choices=[]` 的 usage-only 块，并统计 input/output/cache hit/cache miss/reasoning Token；ReAct、Plan、Team、SDK 和 CLI 都聚合费用。

## 16. 平时是否真的使用 WpCLI

建议诚实回答：

> 我不是写完单测就放着。开发过程中会直接用 WpCLI 反向测试 WpCLI，包括真实终端对话、工具调用、三种运行模式和快照；但它目前也不是我所有日常编码任务的唯一工具。真实使用暴露出的 Skill 延迟注入、后台队列抢占、Runtime 无 thread 历史和 usage 漏报，正是这轮补齐的工程问题。

验收不能只说“我用过”，而要给出可复现命令：

```bash
uv run wpcli --mode react -p "只回复 WPCLI_REACT_OK" --json
uv run wpcli --mode plan -p "读取 README 并给出一句话总结" --json
uv run wpcli --mode team -p "检查 README 和 pyproject.toml" --json
```

## 验收矩阵

- Memory/Prompt/Compression：`tests/test_memory.py`、`tests/test_prompt.py`、`tests/test_context.py`
- Skill：`tests/test_skill.py`、`tests/test_query.py`
- SubAgent/Plan：`tests/test_multi_agent.py`、`tests/test_plan.py`
- Background Runtime：`tests/test_runtime.py`
- Usage/cost：`tests/test_llm_usage.py`

最终还要执行 Ruff、format、全量 pytest、build、CLI help/version/doctor，以及三个真实模型模式的 smoke。

