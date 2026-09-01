# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

WpCLI Python is a real, terminal-native AI agent CLI (a Claude Code alternative) written in Python, aligned with the public capabilities of the WpCLI Java/TypeScript versions. It runs an LLM in a ReAct / Plan-and-Execute / Multi-Agent loop with local tools (files, shell, grep/glob), web tools, memory, skills, snapshots, MCP, a Runtime HTTP API, and a durable task queue. It is a working product, not a demo — no fake data, no un-wired shells.

`PAI.md` is the authoritative project-conventions doc (read it too). It contains the full modification-linkage rules, security rules, and completion criteria. `README.md` documents the user-facing feature set; `docs/parity.md` tracks parity against the Java/TS versions. When a behavior or public capability changes, update `README.md` and `docs/parity.md`, but docs must not claim completion before the code actually does it.

The codebase is bilingual: user-facing strings, prompts, and Plan/Team status text are primarily Chinese, and the system prompt instructs replies to follow the user's request language.

## Commands

Python 3.11+, managed with `uv`. Never assume bare `pytest` or `ruff` are on PATH — always go through `uv run --extra dev`.

```bash
# Install dev dependencies
uv sync --extra dev

# Run the test suite
uv run --extra dev python -m pytest

# Run a single test / filtered subset
uv run --extra dev python -m pytest tests/test_multi_agent.py
uv run --extra dev python -m pytest tests/test_multi_agent.py -k orchestrator_runs_independent_workers_in_parallel

# Lint and format (must pass before completing Python changes)
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .

# Build the wheel (required for packaging-related changes)
uv build

# Run the CLI / smoke checks
uv run wpcli --version
uv run wpcli --help
uv run wpcli doctor --cwd .
uv run wpcli -p "explain this repo"           # single-shot ReAct
uv run wpcli --mode plan -p "plan then execute"
uv run wpcli --mode team --worker-mode plan -p "parallel audit"
uv run wpcli --plain -p hello                  # plain rendering
uv run wpcli                                    # interactive REPL
```

CLI flags worth knowing: `-p/--prompt` (single-shot), `-m/--model` (model override — note `-m` is *not* mode), `--mode react|plan|team`, `--worker-mode react|plan` (team sub-agents), `--json` (machine-readable usage/cost output), `--cwd`, `--plain`. Subcommands: `doctor`, `serve`, `worker`, `mcp serve|init-chrome|list`.

## Architecture

### The streaming event protocol is the backbone

All three agent execution paths emit the same async-generator of event dicts, so the REPL, CLI, and SDK can render any mode uniformly. The event `type` key determines shape. Agent-level protocol (documented in `agent/agent.py`):

- `text_delta` / `thinking_delta` — streamed output
- `tool_call` / `tool_result` — tool invocation (name, input, result, is_error)
- `usage` — per-turn token usage
- `turn_complete` — end of one model turn
- `context_compressed` — compression happened (before/after tokens)
- `error` — fatal; raise/cancel
- `done` — terminal, carries `total_turns`, `total_tokens`, `usage`, `cost`, `messages`

The LLM client layer (`llm/base.py` `LlmClient` protocol → `llm/openai_compatible.py`) emits a lower-level stream: `message_start`, `text_delta`, `thinking_delta`, `tool_call_delta`, `message_end`, `usage`, `error`. Plan mode adds `plan_status`, `plan_created`, `plan_task_started/result/done`; Team mode runs in named phases. Helpers like `Agent.run_complete()` / `QueryEngine._complete_from_events()` collapse these events into a `QueryResult`.

**When you add a mode or change agent behavior, keep this protocol consistent across ReAct, Plan, Team, and the SDK/Runtime entry points — the codebase explicitly forbids fixing only one path.**

### Three agent execution paths

| Path | Module | Trigger |
| --- | --- | --- |
| ReAct | `agent/agent.py` (`Agent`) | default, `-p`, interactive |
| Plan-and-Execute | `agent/plan_execute.py` (`PlanExecuteAgent`) | `/plan`, `--mode plan` |
| Multi-Agent | `agent/orchestrator.py` (`AgentOrchestrator`) | `/team`, `--mode team` |

All three share the same `ToolExecutor`, `PromptAssembler`, skill buffers, snapshot service, and safety policy. The ReAct loop is the core (`Agent._run_react`): skill candidates are prepended, messages are compressed via `ContextWindowManager`, the LLM is called, tool calls are executed in one batch, results re-enter history, and the loop repeats until `stop_reason != "tool_use"`.

- **Plan**: `plan/planner.py` asks the LLM for a JSON DAG (`ExecutionPlan`/`Task` in `plan/models.py`), short-circuits simple goals into a one-task plan, then `PlanExecuteAgent` executes dependency batches in parallel, each task run through the shared `query()` ReAct helper.
- **Team**: `AgentOrchestrator` runs Planner → parallel Workers → Reviewer with bounded review retries. Workers can themselves run in `react` or nested `plan` mode (`--worker-mode`). Planner/Reviewer sub-agents call the LLM *without* tools; Workers use tools. JSON parsing tolerates fenced code blocks and has Chinese fallbacks.

`QueryEngine` (`agent/query_engine.py`) is the public facade used by the CLI, SDK (`sdk.py`), and Runtime API; `Agent` is the underlying engine.

### LLM layer

`llm/base.py` defines the `LlmClient` **protocol** (`model_name`, `provider_name`, `max_context_window`, `price_profile`, `chat()` async iterator, `calculate_cost()`). `OpenAICompatibleClient` (`llm/openai_compatible.py`) is the only concrete implementation — a streaming SSE client that parses reasoning/content/tool-call deltas and usage-only chunks. `llm/factory.py` maps provider/model → base URL + context window; `llm/pricing.py` has dated price profiles (DeepSeek V4 defaults) overridable via config; `llm/model_profiles.py` powers the interactive `/model` selector and persisted BYOK models. Adding a provider means: factory mapping + optional price profile + env-key mapping in `config.py` + docs.

### Tools, executor, and safety

- `tools/base.py`: `Tool` dataclass — `name`, `description`, `parameters` (JSON schema), `handler(payload, ToolContext)`, plus the safety flags `is_read_only`, `is_concurrency_safe`, `danger_level` ("safe|medium|high"), `requires_approval`, `timeout`. `ToolContext` carries cwd, config, `approval_callback`, and the per-agent `skill_context_buffer`.
- `tools/builtins.py`: all built-in tools (`read_file`, `write_file`, `edit_file`, `bash`/`execute_command`, `glob`/`grep` (+ aliases), `directory_tree`, `web_search`, `web_fetch`, `save_memory`, `search_memory`, `load_skill`, `save_skill`, `search_code`, `revert_turn`). New tools are registered here.
- `tools/executor.py`: `ToolExecutor.execute_all` batches read-only + concurrency-safe calls into parallel `asyncio.gather` (semaphore = `tools.max_concurrent_read`) and runs everything else sequentially. Per call it: validates input → decides approval → executes → audits. **Adding a tool means updating its schema, read-only/concurrency/danger flags, approval policy, and tests together.**
- `tools/registry.py` + `bootstrap.py`: `ToolRegistry` holds tools; `build_tool_registry()` registers built-ins then MCP tools (remote tools appear as `mcp__<server>__<tool>`).

Safety (`policy/`): `PathGuard` confines file tools to the workspace; `CommandGuard` fast-fails destructive commands; `AuditLog` appends redacted JSONL (sensitive keys masked). HITL lives in `ToolExecutor._approval_decision`: `hitl_mode` "never" auto-approves, "auto" approves safe tools without asking, otherwise the `approval_callback` decides (deny if none). The REPL's two permission modes (`PermissionModeController`, toggled with Shift+Tab) mutate the live config: "Auto (full access)" sets hitl=never + disables guards; switching back must restore the original policy. Non-interactive entries must not silently bypass approval.

### Config layering

`config.py` builds a typed `WpCliConfig` (dataclasses, `slots=True`) by deep-merging in this precedence order: built-in defaults → `~/.wpcli/config.json` → project `.wpcli/config.json` → project `.env` → CLI overrides → process environment. Provider-specific API keys (`DEEPSEEK_API_KEY`, `ZAI_API_KEY`, etc.) are resolved in `_apply_env`. Never commit `.env`, real keys, user DBs, or audit logs; `config_to_public_dict()` masks the API key for display.

### Prompt and context management

`prompt/assembler.py`: `build_static()` is a cache-friendly prefix (personality, guidelines, project instructions pulled from `AGENTS.md` / `PAI.md` / custom paths, capped ~16k chars). `build_dynamic()` is rebuilt per request (time, cwd, model, tools, Top-K recalled memories). Recalled memory and tool output are treated as untrusted data in the prompt. **Keep the static-vs-dynamic boundary intact** — it's what makes prompt caching work.

`context/manager.py`: `ContextBudget` computes `available_input_tokens = context_window - max_output_tokens - reserve_tokens`. `ContextWindowManager.prepare` deterministically compresses when tokens exceed 80% of budget (compressing to ~55%), always retaining recent messages and whole tool-call pairs (boundary aligned at a user turn), with oversized tool payload truncation as a last resort. Token estimation is a dependency-free CJK-aware heuristic (`estimate_text_tokens`).

### Memory, skills, snapshots

- **Long-term memory** (`memory/manager.py`): SQLite, per-project `scope`, entries with kind/source/importance/confidence/TTL/access_count/content_hash. `recall()` does lexical relevance ranking (CJK n-gram aware) weighted toward overlap, importance, confidence, recency, access; normalized-hash dedup, expiry purge, and quota eviction keep it bounded. `save_memory`/`search_memory` tools and `PromptAssembler` recall all use it.
- **Skills** (`skill/registry.py`): layered `builtin → user (~/.wpcli/skills) → project (.wpcli/skills)`, later layers override. Each skill is a `SKILL.md` with YAML frontmatter. Matching is lexical Top-K (`SkillMatcher`), then the model decides via `load_skill`. Loaded skill body goes into a per-agent one-shot `SkillContextBuffer` and is injected into the *next* model turn only. **Concurrent workers and parallel plan tasks must each own a separate buffer** so skills don't cross-contaminate. `save_skill` requires HITL approval and refuses overwrite unless `overwrite=true`.
- **Snapshots** (`snapshot/service.py`): pre/post-turn workspace snapshots to `~/.wpcli/snapshots/` (never into project `.git`), used by `/snapshot`, `/restore`, and the `revert_turn` tool.

### Runtime API and durable tasks

`runtime/api.py`: `RuntimeApiServer` — a `ThreadingHTTPServer` exposing `/v1/threads`, `/v1/threads/{id}/turns`, `/v1/threads/{id}/events` (SSE), `/v1/tasks`, plus cancel; API-key auth via `WPCLI_RUNTIME_API_KEY`. `runtime/tasks.py`: `DurableTaskManager` — SQLite-backed queue (`~/.wpcli/tasks/tasks.db`) with **atomic claim** (`begin immediate`), 300s lease with 30s heartbeats for crash recovery, project `scope` isolation, and cancellation-safe completion (cancel owns terminal state; a late worker result must never overwrite it). Modes: `react|plan|team`. The CLI `serve`/`worker` commands drive it.

## Testing conventions

Tests in `tests/` import `wpcli` directly and avoid real network/LLM calls:

- **Fake LLM clients** implement the `LlmClient` protocol attributes (`model_name`, `provider_name`, `max_context_window`) plus a `chat()` async generator that yields canned events, and optionally `calculate_cost()`. See `tests/test_multi_agent.py` for the pattern (`FakeTeamClient`).
- **Isolate user config** with `monkeypatch.setenv("HOME", str(tmp_path / "home"))` so config/db/audit paths land in the tmp dir.
- **Disable approvals** in tests with `config.policy.hitl_mode = "never"` unless the test is specifically about approval.
- Async code is driven with `asyncio.run(...)`; agent runs are asserted by collecting events.
- Follow the modify-linkage rules in `PAI.md`: e.g. a CLI/slash change must update both `cli.py` and `repl.py`; agent behavior changes must cover ReAct/Plan/Team and SDK/Runtime entry points; behavior/docs changes update `README.md` + `docs/parity.md`. CLI/TUI behavior should be smoke-verified with real `wpcli` runs when possible, not only unit tests.
- Do not commit or push without being asked.


