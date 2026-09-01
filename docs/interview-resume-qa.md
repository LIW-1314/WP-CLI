# WpCLI 简历版面试问答（按简历条目准备）

> 本文档把简历上的每一条拆成「面试官会问什么 + 怎么答」，答案全部对齐 `src/wpcli/` 的真实实现。
> 先读第一部分「简历与实现的核对」——有 3 处描述和代码不一致，面试官深挖时可能戳穿，必须先想清楚口径。

---

## 0. 简历与实现的核对（必读）

简历里写的东西 90% 都能从代码里找到依据，但有 3 处和真实实现**不完全一致**，是最大的追问风险点：

| 简历说法 | 真实实现 | 风险与建议 |
|---|---|---|
| 技术栈/召回写 **BM25 + 向量相似度** | ✅ `rag/code_index.py` 现在有**真 BM25**：`code_chunks` 存逐行文档、`code_terms` 存词项 df、`code_stats` 存 N/avgdl，检索时按 `idf·tf·(k1+1)/(tf+k1·(1-b+b·dl/avgdl))`（k1=1.5, b=0.75）打分，token 级匹配（`parse` 不匹配 `parse_request`，子串检索交给 `grep`）。**但**：`memory/manager.py` 的长期记忆召回仍是**加权词法打分**（0.72 词法重叠 + 0.12 重要度 + 0.08 置信度 + 0.06 时效 + 0.02 访问热度，CJK n-gram），不是 BM25；**「向量相似度」全项目都没有** | 低风险，但仍有两点要诚实：① 简历写「向量」如果面试官问「用的什么 embedding」就答不上来——建议把「向量相似度」从简历删掉或改成「关键词/词法检索」；② BM25 只覆盖**代码检索**，长期记忆召回是自研加权词法打分，别把两条混为一谈 |
| 记忆分类写 **「对话、事实、摘要、工具结果 4 类」** | 真实分层是：短期会话（`Agent.history` 消息，含工具调用/工具结果）+ 静态长期（`AGENTS.md`/`PAI.md`）+ 动态长期（SQLite，`kind` 是 `fact/preference/constraint/correction/decision`）+ 压缩摘要（会话内滚动摘要）。工具结果属于短期会话消息，没有独立存储 | 可以这样圆：把「4 类」解释成「4 种数据形态」，然后落到真实结构；面试时主动说清楚 SQLite 里真实存的是 5 种 kind |
| 内置工具写 **15 个** | 实际 17 个唯一工具、20 个注册名（`glob_files`/`grep_code`/`execute_command` 是别名） | 面试说「17 个唯一、含别名共 20」更准确；简历建议改 17 |
| 「文件修改必须人工确认」 | `write_file`/`edit_file` 是 `danger_level=medium` 但 `requires_approval=False`，默认 `hitl_mode=auto` 时**不弹确认**；真正默认要确认的是 `bash`/`execute_command`/`revert_turn`/`save_skill`（`requires_approval=True`） | 口径改成：「高危命令与恢复操作默认必须确认；文件写改默认放行、可切 `hitl_mode=always` 全量确认」 |
| 「启动恢复」归在记忆条目 | 真正的租约恢复在**后台任务队列**（`runtime/tasks.py` lease/heartbeat/requeue），不是记忆 | 简历这句移到任务队列那条讲，或者删掉 |

其余都对得上：20 轮上限 ✓、统一 5 家 Provider 的 OpenAI-compatible 适配 ✓、Prefix Cache ✓、httpx 流式 ✓、SQLite ✓、Prompt-Toolkit ✓、Command/Path Guard ✓、Audit Log ✓、80%→55% 压缩 ✓、去重 ✓。

---

## 1. 开放题：介绍你这个项目

**答法（30 秒版）：**
> 这是一个对标 Claude Code 的终端 Coding Agent，Python 实现。用户用自然语言下指令，它通过 ReAct 循环自主调用工具——读文件、改代码、跑命令、联网搜索——最后给出结果。三条执行路径：默认 ReAct、Plan-and-Execute（先生成 DAG 再按依赖并行执行）、Multi-Agent（Planner 分工 → 并行 Worker → Reviewer 验收）。上层套了 MCP、长期记忆、上下文压缩、安全审批、快照和 Runtime API。底层是统一的 OpenAI-compatible Provider 适配层，用 httpx 流式接 5 家模型，并对 DeepSeek 做了 Prefix Cache 和百万 Token 长上下文适配。

**面试官大概率追问：**「哪部分是你自己写的？」——所有核心都是本项目实现的：适配层、ReAct 循环、工具调度、记忆召回、压缩器、任务队列、HITL 审批链。没有接入任何 Agent 框架（不是 LangChain/LlamaIndex），LLM 调用是直接用 httpx 写 SSE 解析。这是最大的加分点，要主动强调。

---

## 2. 按简历条目逐条展开

### 2.1 「统一 Model Provider 适配层」

**Q1. 你是怎么统一 5 家模型的？为什么能统一？**
> 这 5 家（DeepSeek、OpenAI、GLM、Kimi、Step）都兼容 OpenAI 的 `/chat/completions` 协议，所以只写一个 `OpenAICompatibleClient`。差异集中在三处，用配置表收敛：① Base URL（`factory.py` 的 `PROVIDER_BASE_URLS` 映射）；② 上下文窗口（`MODEL_CONTEXT_WINDOWS`）；③ API Key 环境变量名（`config.py` 里按 provider 匹配 `DEEPSEEK_API_KEY`/`ZAI_API_KEY`/`STEP_API_KEY`/`KIMI_API_KEY`）。请求体、SSE 解析、usage 统计、成本计算全部复用一份代码。对不兼容的私有协议，用 `WPCLI_BASE_URL` + `openai-compatible` provider 兜底。

**Q2. 流式请求怎么写的？异步体现在哪？**
> `httpx.AsyncClient` + `client.stream("POST", url, json=payload)` 发起 SSE 流，用 `response.aiter_text()` 按 `data:` 行增量解析。整个 `chat()` 是 `AsyncIterator`，消费方（ReAct 循环）边收边 yield，不用等完整响应。SSE 里把增量拆成几种事件：
> - `delta.reasoning_content` → `thinking_delta`
> - `delta.content` → `text_delta`
> - `delta.tool_calls` → `tool_call_delta`（按 `index` 累加 name 和 arguments 片段）
> - `finish_reason` → `message_end`（把 `tool_calls`/`length` 映射成 `tool_use`/`max_tokens`）
> - usage-only 块（`choices=[]`）→ `usage`
>
> 因为一次 ReAct 里可能连续调好几次 LLM，全是 `async for`，等待 I/O 时事件循环可以并发跑工具、跑其他 worker。

**Q3. 异常处理怎么做的？**
> `chat()` 里按异常类型 yield 一个 `error` 事件而不是抛出裸异常，让上层统一渲染/退出：超时 → 提示网络与重试；HTTP 非 2xx → 提示检查 API Key、模型权限、余额、服务状态；连接失败 → 提示检查网络/代理。`ToolExecutor` 也保证工具异常不外泄给调用方，转成 `ToolResult(is_error=True)` 回灌模型。

**Q4. DeepSeek 的 Prefix Cache 和百万 Token 上下文具体做了什么？**
> 三层：① `factory.py` 给 DeepSeek 模型配 1M context window，并把 `prompt_cache=True` 打开；② 请求开 `stream_options.include_usage`，解析 `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`，在 `/usage`、`--json` 里能看到命中/未命中和对应成本；③ Prompt 设计上刻意把**静态前缀**（身份、规则、项目指令）和**每请求重建的动态后缀**分开，静态部分不随用户输入变化，前缀缓存命中率高。价格上 cache hit 远低于 miss（Flash ¥0.02/M vs ¥1/M），所以命中直接省钱。

**Q5. 为什么不用现成 SDK（openai/anthropic）？**
> 一是这几家都是 OpenAI 兼容协议，一个自研适配层能覆盖全部；二是需要拿到流式中间事件（thinking、tool_call delta、usage）做 Agent 循环，自研能完全控制事件协议；三是可控的异常/重试策略。可补充：项目目标是对标 Claude Code，需要一个 Provider 无关的抽象，`LlmClient` 用 Python Protocol 定义，测试里用 Fake client 实现同接口。

### 2.2 「ReAct Agent Runtime」

**Q6. ReAct 循环的完整流程？**
> 核心在 `agent/query.py` 的 `query()`（`Agent._run_react` 同构）。每轮：① 压缩检查（超预算则压缩历史）；② 调 `llm.chat()`，流式收文本/思考/工具调用增量；③ 如果没有工具调用且 `stop_reason != "tool_use"` 就结束；④ 把本轮的 tool calls 交给 `ToolExecutor.execute_all` 批量执行；⑤ 把 assistant 消息和每条 tool 结果按 `tool_call_id` 回灌消息列表，进入下一轮；⑥ 到达 `max_turns=20` 强停。每轮 `usage` 累加，最后 yield `done`（含 total_tokens/cost）。

**Q7. 工具怎么描述给模型？「统一管理 15 个工具」的机制是什么？**
> `Tool` 是数据类，带 `name/description/parameters(JSON Schema)/handler` 和四个安全属性。`definition()` 把每个工具转成 OpenAI function calling 格式，`tool_defs = registry.definitions()` 每次随请求发给模型。内置工具 17 个唯一、含别名共 20 个注册名：文件类（read/write/edit/list_dir/glob/grep/directory_tree/get_file_info）、命令（bash）、联网（web_search/web_fetch）、记忆（save/search_memory）、技能（load/save_skill）、代码检索（search_code）、快照回滚（revert_turn）。MCP 工具运行时动态注册为 `mcp__<server>__<tool>`。

**Q8. 工具调度器怎么做到「并行读、串行写」？**
> 每个 `Tool` 标了 `is_read_only` 和 `is_concurrency_safe`。`ToolExecutor.execute_all` 先分流：只读且并发安全的一批用 `asyncio.gather` 并行执行，并用 `Semaphore(config.tools.max_concurrent_read)`（默认 4）限并发；其余（写文件、bash 等）严格串行，避免竞态。模型一次发多个工具调用时，读操作并行、写操作顺序执行，显著降低总延迟。测试 `tests/test_tools.py` 覆盖了分组逻辑。

**Q9. 20 轮上限哪来的？会不会不够用？**
> `Agent` 默认 `max_turns=20`，每个子 Agent 的 worker 是 8 轮、plan 任务也是 8 轮。正常任务 3~6 轮（一次工具调用一次模型轮）。用 20 兜底是防止模型陷入无意义循环烧 token；到达上限直接结束并输出当前结果。可提一句这是「能配的参数」（构造函数参数），不是写死的。

### 2.3 「三层 Memory + 召回 + 压缩」

**Q10. 三层记忆分别是什么、存哪、生命周期？**
> ① 短期：当前 session 的 `Agent.history`（消息、工具调用、工具结果），会话结束即散；② 静态长期：`AGENTS.md`/`PAI.md`/`.wpcli/PAI.md` 等文件，人工维护、跟随仓库版本、可审查；③ 动态长期：SQLite（`~/.wpcli/memory.db`），按项目 cwd scope 隔离，每条带 kind/source/importance/confidence/TTL/访问次数/内容哈希，跨 session 持久。压缩产生的滚动摘要只属于短期会话，**不会**自动晋升为长期记忆（设计上防污染）。

**Q11. 动态长期记忆怎么写、怎么召回？（这是面试官必问）**
> 写：`save_memory` 工具，入参含 content/kind/importance/confidence/expires_at。写入端校验空值、长度上限、评分 0~1；NFKC+大小写+空白规范化后算 SHA-256，**同内容去重**（重复保存更新旧记录、重要度取 max）；TTL 到期自动清理；每个 scope 有容量上限，超限按「重要度→置信度→访问热度→时效」淘汰最不值钱的。召回归约成一个**加权词法相关度** `score = 0.72·词法重叠 + 0.12·重要度 + 0.08·置信度 + 0.06·时效 + 0.02·访问热度`，词法重叠对英文取词、对中文取单字和二元组，天然支持中英混合。每次请求先自动召回 Top-K 注入动态 Prompt（标注为低信任数据），候选不足时模型可主动 `search_memory` 深搜。
>
> ⚠️ 这里如果面试官顺着简历问「BM25 实现细节」，要说清楚两条路是**分开的**：长期记忆召回是**自研加权词法打分**（上面这个公式，没有引入向量/第三方检索库）；真正的 BM25 在**代码检索**（`rag/code_index.py`，词频/逆文档频率/文档长度归一化，见 2.2 节对应 Q）。别把「记忆召回」说成 BM25。

**Q12. 为什么召回要「权重里带重要度/置信度/时效/访问热度」？**
> 纯词法匹配召回的是「像」，但长期记忆里「用户纠错 > 一般事实」——同样相关，纠错信息更重要。所以把内容本身的重要度/置信度纳入打分，再叠加时效衰减（30 天半衰）和访问热度，让「越常用越靠前、越久远越靠后」，避免低质量旧记录长期霸榜。这是纯关键词检索做不到的排序信号。

**Q13. 上下文压缩器怎么做到不破坏工具调用对？**
> `ContextWindowManager`。预算 = `context_window − max_output_tokens − reserve(1024)`，达到预算 80% 触发、压到约 55%。关键细节：**切分点对齐到 user 轮**——从候选切分位置向前找最近的 user 消息，这样一条 assistant 的工具调用和它所有 tool result 必然留在同一边，不会被切成两半；旧轮次压成提取式滚动摘要（`<conversation-summary>`，标记为低信任数据），最近消息保原文；只有 tool 结果超大时才做兜底截断（4000 字符）。因为**没有 tokenizer**，用自研估算：CJK 字符≈1 token，其余≈3 字符/token，保守留余量。

**Q14. 「Token Budget 控制容量」具体数字？**
> `ContextBudget.available_input_tokens = context_window − max_output_tokens − reserve_tokens`。DeepSeek V4 Flash 是 1M 窗口，`max_tokens=8192`，reserve 1024，所以可用输入预算约 99 万 token。到 80% 触发压缩、压到 55%，留 20% 安全区给下一轮输出、增长中的工具结果和估算误差。

### 2.4 「Agent 安全执行机制」

**Q15. 审批流（HITL）的完整链路？**
> `ToolExecutor._approval_decision`：`hitl_mode=never` → 直接放行；`auto` 且工具 `requires_approval=False` → 放行；否则走 `approval_callback`。REPL 里回调是交互式 `Approve? [y/n/a/s]`——y 放行、n 拒绝、a 进入 Auto 模式、s 跳过。非交互入口没有回调时**默认 deny**（宁可拒绝不静默放行）。需要审批的工具：`bash`/`execute_command`、`revert_turn`、`save_skill`（都标 `requires_approval=True`）。被拒工具返回 `ToolResult(is_error=True)` 回灌模型，模型能看到「被审批策略拒绝」。

**Q16. Command Guard 和 Path Guard 分别挡什么？**
> `CommandGuard`：执行 bash 前先跑，两层——配置黑名单子串（`sudo`、`rm -rf /`、`mkfs`、`shutdown` 等）+ 正则模式（`rm -rf /`、`rm -rf ~`、`dd if=/dev/zero`、fork 炸弹等），命中直接抛 `CommandPolicyError`，**先于** HITL 拦截，避免「危险命令也弹确认」。`PathGuard`：文件类工具把相对路径解析到 workspace root 后做 `resolve().relative_to(root)` 校验，越界（`../`、绝对路径逃逸）抛 `PathPolicyError`。两者都是「默认开启、可在 Auto 模式关闭、切回 Default 恢复原策略」。

**Q17. Audit Log 记了什么、怎么防泄漏？**
> 每个非只读工具调用后追加一条 JSONL（`~/.wpcli/audit.jsonl`）：时间戳、工具名、输入、outcome（allow/deny/error）、approver、cwd。`_redact` 会把 key 含 `token/key/password/secret/authorization/bearer` 的字段打成 `***`，防止把 API Key 写进日志。REPL 里 `/audit` 可查，默认给最近 20 条。

**Q18. REPL 的权限模式（Default / Auto）怎么实现的？**
> `PermissionModeController` 保存启动时的 hitl/path_guard/command_guard 三份原值。Shift+Tab 切到 `Auto (full access)` 时把 `hitl=never`、两个 guard 关掉；再切回 Default 时**用保存的原值恢复**，而不是硬编码「恢复默认」——保证用户自定义的启动策略不被破坏。`test_permission_mode.py` 覆盖了这个「恢复原策略」语义。

### 2.5 简历提过但没展开的能力（被问到要能接住）

**Q19. MCP 是怎么接的？WpCLI 自己也能当 MCP server？**
> Client 端用官方 `mcp` SDK，支持 stdio 和 Streamable HTTP 两种 transport；`McpClientManager.load_tools()` 把远端工具动态注册成 `mcp__<server>__<tool>` 进同一套 ToolRegistry，所以 ReAct/Plan/Team 都能直接调。Server 端 `wpcli mcp serve --transport stdio|http` 把内置工具以 MCP 协议暴露出去。还有个 `mcp init-chrome` 一键写 Chrome DevTools MCP 配置。

**Q20. Plan 模式为什么是 DAG？**
> Planner 让模型只输出 JSON：`{"tasks":[{"id","description","type","dependencies"}]}`。`ExecutionPlan.compute_execution_order` 用 DFS 拓扑排序，能**检测环**（有环直接失败）；`execution_batches` 把无依赖的任务分成可并行批次。`PlanExecuteAgent` 按批次执行：单任务直接跑，多任务 `asyncio.create_task` 并行。简单目标（<=30 字且含「列出/读取/执行」等信号）不走模型，直接生成单任务最小计划，省一次调用。

**Q21. Team 模式怎么验收，失败了怎么办？**
> Planner 出 DAG → 无依赖步骤进并行 worker 队列（`asyncio.Queue` 限流）→ 每个 Worker 完成步骤 → Reviewer 输出 `{"approved": true|false, "issues":[]}`。不通过且有重试次数（默认 2）时，把 reviewer 的 issues 拼进上下文让 worker 重做；重试耗尽仍不通过 → 步骤标 `FAILED`，最终报告明确「部分完成、有失败步骤」，**不会伪装成全部完成**。解析容忍代码块包裹和中文关键词（`通过/未通过`）。

**Q22. 后台任务队列的可靠性怎么保证？**
> `DurableTaskManager`（SQLite，`~/.wpcli/tasks/tasks.db`）：`BEGIN IMMEDIATE` 原子领取，多 worker 不会抢同一任务；任务按项目 cwd scope 隔离；worker 领取后持 300s lease，每 30s heartbeat 续租，崩溃后租约过期任务被 `requeue_expired` 重新入队；**cancel 语义**——cancel 把任务置为终态 `canceled`，worker 执行中每轮检查 `is_canceled`，迟到结果用「只更新 running 状态」的 SQL 保证**不能覆盖 canceled/completed**。这就是简历里「启动恢复/崩溃恢复」的真正落点。

**Q23. Runtime API 是什么？**
> 一个 `ThreadingHTTPServer`，提供 `/v1/threads`（有历史的对话线程）、`/v1/threads/{id}/turns`（执行一轮）、`/v1/threads/{id}/events`（SSE 事件流）、`/v1/tasks`（提交 react|plan|team 后台任务）。`x-api-key`/`Bearer` 鉴权，`WPCLI_RUNTIME_API_KEY` 必填。目的：让外部系统能接入 WpCLI 的线程、事件和后台任务能力。

**Q24. 快照和图片输入是什么？**
> 快照：每次 Agent run 前后（`pre-turn`/`post-turn`）把 workspace 存到 `~/.wpcli/snapshots/`（不进项目 .git），支持 `/restore` 和 `revert_turn` 工具回滚。图片：prompt 里 `@image:path` 解析，本地图压缩缩放、透明底铺白，转 data URL；模型不支持多模态时自动降级成文本元信息，不会把不支持的 payload 发给模型。

---

## 3. 大概率被深挖的「陷阱」题

**Q25. 没有 tokenizer，怎么估算 token 数？误差怎么办？**
> `context/manager.py` 的 `estimate_text_tokens`：CJK 每字符 ≈1 token，非 CJK 按 3 字符/token 向上取整。这是保守估算，靠两层兜底：reserve 1024 的固定余量 + 80%→55% 的压缩区间把误差吃进安全区。诚实说这是工程权衡——避免引入 `tiktoken` 等重量级依赖、又要离线可用。

**Q26. 并发工具执行时，结果怎么保证和工具调用对应？**
> 每个工具调用的 SSE 增量带 `id` 和 `index`，`_merge_tool_delta` 按 index 聚合 name/arguments；`execute_all` 返回的 `ToolResult.tool_use_id` 与调用 id 一一对应，回灌消息时用 `tool_call_id` 匹配，OpenAI 协议要求 tool 消息必须用 `tool_call_id` 关联到对应的 assistant tool_call，顺序错乱会导致模型端校验失败。

**Q27. 怎么防止多 Agent 之间的状态串线？（这个项目专门踩过坑）**
> 每个 Worker / 每个并行 Plan 任务持有**独立** `SkillContextBuffer`；`load_skill` 的正文只进入该 buffer，工具执行后立刻 drain 并注入**当前请求的下一轮**。历史上一版是所有 Worker 共用一个 buffer、且在请求开始时才 drain，导致并行 Worker 互相读到对方的技能、且技能延迟到下一个用户请求才生效——这轮专门修掉并用回归测试锁住。这句话是面试里很有分量的「我踩过并修好了」素材。

**Q28. 配置优先级那么多层，怎么不失控？**
> `config.py` 用「默认 → 用户 → 项目 → 项目 .env → CLI → 进程 env」的**逐层深合并**（`_deep_merge`），低层不覆盖高层的值；env 映射集中在 `_apply_env`，按 provider 解析专属 Key。所有配置收敛成一个类型化 `WpCliConfig`（dataclass），下游只认这一个对象，新增配置加一个字段即可。

**Q29. 为什么用 SQLite 而不是 MySQL/Redis？**
> 单机本地工具、单写入方、并发量低，SQLite 足够且零部署；`BEGIN IMMEDIATE` + busy_timeout 处理并发写入；文件即数据库，天然持久化、可备份、可随项目迁移。Redis 的缓存价值这里由 Prefix Cache + 内存会话替代。

**Q30. 异步和线程混合（Runtime server 用 ThreadingHTTPServer，内部却 `asyncio.run`）不冲突吗？**
> 每个 HTTP 请求在独立线程里 `asyncio.run` 起一个事件循环跑一次 turn/task，互不阻塞；后台 worker 也是每个 worker 线程一个 loop。线程模型选 Threading 是为了简单（每个请求独立循环，不共享事件循环、无跨线程协程状态），任务队列的原子性靠 SQLite 事务保证而不是靠线程同步。诚实承认：高并发吞吐不如纯异步方案，但对外是本地开发工具、并发量低，简单性优先。

---

## 4. 行为/开放问题

**Q31. 这个项目里最难的技术点？**
> 推荐口径：不是「某个库不会用」，而是**状态和并发**——三层记忆的召回/去重/淘汰、压缩器对齐工具调用对、多 Agent 的 skill 串线、后台任务的原子领取与取消保护。这些是「看起来简单、深挖全是边界」的问题。挑 1~2 个展开讲，能体现工程判断。

**Q32. 如果重做，会怎么改进？**
> 给 3 个具体方向：① 召回替换/补上真 BM25（或向量索引）并做评测集；② 引入真实 tokenizer（tiktoken）替代估算；③ 支持多模态模型时按 provider 动态探测能力而不是字符串启发式；④ 给 Runtime API 加 SSE 长连接/WebSocket 替代轮询；⑤ 用真正的异步 HTTP server（如 Starlette）替换 ThreadingHTTPServer。

**Q33. 你从项目里学到了什么？**
> 推荐口径（真实且不空泛）：协议设计上「所有模式统一事件流」让 UI 层零改动复用；安全上「非交互入口默认 deny」比放行更稳；可靠性上「取消必须拥有终态」避免迟到结果覆盖；还有**文档先行会害死人**——README 声称支持而代码没接线的功能，这轮全部按「能跑通才算支持」重新验收。

---

## 5. 一句话自我介绍（项目部分，可背）

> 我用 Python 从零实现了一个终端 Coding Agent（对标 Claude Code）：自己用 httpx 写了统一的多 Provider 流式适配层（DeepSeek/OpenAI/GLM/Kimi/Step），自己实现 ReAct 循环和工具调度（并行读、串行写、HITL 审批、审计），设计了长期记忆（SQLite、词法召回、去重、TTL）和边界感知的上下文压缩，还实现了 Plan/Team 两种并行执行模式和可恢复的后台任务队列。没有依赖任何 Agent 框架，核心路径都有测试覆盖。

---

## 6. 建议带的「自查命令」（面试官可能让你演示）

```bash
uv run wpcli doctor --cwd .                     # 环境健康检查
uv run wpcli -p "只回复 WPCLI_OK" --json       # ReAct + usage/cost 输出
uv run wpcli --mode plan -p "读取 README 并总结" --json
uv run wpcli --mode team --worker-mode plan -p "检查 pyproject.toml 和 README" --json
uv run --extra dev python -m pytest              # 全量测试
uv run --extra dev python -m pytest tests/test_runtime.py -k cancellation  # 挑一个可靠性单测讲
```

真实 LLM 调用需要有效 API Key；若现场没 Key，用 `tests/` 里的 Fake client 讲单测即可。


