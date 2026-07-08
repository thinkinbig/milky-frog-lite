# Milky Frog

Milky Frog (Chinese: 奶蛙) is a local coding agent that completes one user goal at a time inside a workspace. This glossary defines the product language shared by the CLI and runtime.

## Language

**Run**:
The durable execution of one user goal, from its initial request until completion, failure, cancellation, or pause.
_Avoid_: Session, thread, job

**Harness**:
The runtime coordinator that advances a Run through model responses, tool calls, user input, and terminal states.
_Avoid_: Workflow engine, graph

**Workspace**:
The project directory a Run is allowed to inspect and modify.
_Avoid_: Sandbox, repository

**Tool**:
A model-invoked capability that observes or changes the Workspace or invokes an external process.
_Avoid_: Plugin, function

**Control Tool**:
A model-invoked Harness operation, such as requesting user input, loading a Skill, or proposing a Memory change. It does not directly operate on the Workspace.
_Avoid_: Business tool

**Handler**:
A callback registered on the Harness lifecycle-signal hub (`EventHub`). The Harness publishes signals; Handlers subscribe and react. Most Handlers observe only (streaming UI, Langfuse). `RunBeforeTool` and `RunBeforeStart` may return control results that influence the next Harness step. `CheckpointHandler` persists RunState at durable boundaries in response to lifecycle signals — persistence is not embedded in the Harness loop itself.
_Avoid_: Middleware, hook, intercept

**Lifecycle signal**:
An ephemeral, in-process Harness phase notification (for example `RunStarted`, `RunModelChunk`, `RunAfterModel`) published by `RunEmitter` during a Run and delivered to Handlers. Not replayed from the Checkpoint snapshot.
_Avoid_: Event (unqualified), Checkpoint

**Run notice**:
An ephemeral user-facing message during a Run (for example a model retry warning). Published on the same bus as lifecycle signals but not a lifecycle phase. Not checkpointed.
_Avoid_: Notification, toast event

**Checkpoint snapshot**:
The versioned JSON serialization of a Run's `RunState` (messages, accounting, reasoning log) stored on the `runs` row. The source of truth for resume. Distinct from lifecycle signals.
_Avoid_: Handler event, event log

**Checkpoint**:
The durable snapshot and status projection of a Run, used to resume execution without repeating completed Tools.
_Avoid_: Memory, event log

**Memory**:
User-approved, project-scoped knowledge that remains available across Runs.
_Avoid_: Checkpoint, conversation history

**Skill**:
A progressively loaded instruction bundle that gives the Agent task-specific operating knowledge without adding executable code.
_Avoid_: Plugin, Tool

**Local Sandbox**:
A policy boundary that constrains structured file operations and requires approval for shell commands, but does not isolate untrusted code from the host.
Implemented by the `Sandbox` protocol; default adapter is `LocalSandbox` (`adapters/local/`).
_Avoid_: Container, secure sandbox

**Container Sandbox**:
A Sandbox adapter that executes commands inside a container against a bind-mounted Workspace. Isolates command execution; does not isolate file access. Implemented by `DockerSandbox` (`adapters/docker/`), opt-in via `[sandbox].kind = "docker"`.
_Avoid_: DockerExecutionBackend, execution backend, secure sandbox

**Provider**:
The model vendor whose tokenizer and wire conventions a Run uses (for example `openai`, `deepseek`). Inferred from the model name and base URL, overridable via `MILKY_FROG_PROVIDER`. Selects the exact token counter, falling back to an approximate one when the provider is unknown or its tokenizer package is absent.
_Avoid_: Vendor, backend, integration

**Terminal UI**:
The consistent command-line presentation of Run state, results, errors, and empty states using styled terminal output. It is not a full-screen interactive application.
_Avoid_: TUI, web UI, frontend

---

# 奶蛙（Milky Frog）

奶蛙是一个本地代码 Agent，每次在一个工作区内完成一个用户目标。以下术语是 CLI 与运行时共同使用的项目语言。

## 术语

**Run（运行）**：
一个用户目标从初始请求到完成、失败、取消或暂停的持久化执行过程。
_避免使用_：会话、线程、作业

**Harness（执行框架）**：
负责通过模型响应、工具调用、用户输入和终止状态推进 Run 的运行时协调器。
_避免使用_：工作流引擎、图

**Workspace（工作区）**：
一个 Run 被允许检查和修改的项目目录。
_避免使用_：沙箱、代码仓库

**Tool（工具）**：
由模型调用，用于观察或修改 Workspace，或者启动外部进程的能力。
_避免使用_：插件、函数

**Control Tool（控制工具）**：
由模型调用的 Harness 操作，例如请求用户输入、加载 Skill 或提议修改 Memory。它不直接操作 Workspace。
_避免使用_：业务工具

**Handler（处理器）**：
注册到 Harness 生命周期信号中心（`EventHub`）的回调。Harness 发布信号；Handler 订阅并响应。多数 Handler 只做观察（流式 UI、Langfuse）。`RunBeforeTool` 与 `RunBeforeStart` 可返回控制结果以影响 Harness 下一步。`CheckpointHandler` 在持久化边界根据生命周期信号写入 RunState——持久化不在 Harness 循环内硬编码。
_避免使用_：Middleware、Hook、intercept

**Lifecycle signal（生命周期信号）**：
Run 进行期间由 `RunEmitter` 发布、分发给 Handler 的 Harness 阶段通知（例如 `RunStarted`、`RunModelChunk`、`RunAfterModel`）。不从 Checkpoint 快照 replay。
_避免使用_：Event（无前缀）、Checkpoint

**Run notice（运行提示）**：
Run 进行期间面向用户的临时消息（例如模型重试警告）。与生命周期信号走同一总线，但不是生命周期阶段。不写入 Checkpoint。
_避免使用_：Notification、toast 事件

**Checkpoint snapshot（Checkpoint 快照）**：
Run 的 `RunState`（messages、计数、reasoning log 等）的版本化 JSON 序列化，存于 `runs` 行。resume 的真相来源。与生命周期信号不同。
_避免使用_：Handler 事件、事件日志

**Checkpoint（检查点）**：
一个 Run 的 durable 快照与状态投影，用于恢复执行且不重复运行已完成的 Tool。
_避免使用_：Memory、事件日志

**Memory（记忆）**：
经用户批准、作用于项目并可跨 Run 使用的知识。
_避免使用_：Checkpoint、对话历史

**Skill（技能）**：
按需渐进加载的指令包，为 Agent 提供特定任务的操作知识，但不增加可执行代码。
_避免使用_：插件、Tool

**Local Sandbox（本地沙箱）**：
一种策略边界，用于限制结构化文件操作并要求用户批准 shell 命令，但不将不可信代码与宿主机隔离。
由 `Sandbox` 协议实现；默认适配器为 `LocalSandbox`（`adapters/local/`）。
_避免使用_：容器、安全沙箱

**Container Sandbox（容器沙箱）**：
在容器内针对 bind-mount 的 Workspace 执行命令的 Sandbox 适配器。隔离命令执行，不隔离文件访问。由 `DockerSandbox`（`adapters/docker/`）实现，通过 `[sandbox].kind = "docker"` 选择启用。
_避免使用_：DockerExecutionBackend、execution backend、secure sandbox

**Provider（提供方）**：
一个 Run 所用模型供应商的 tokenizer 与传输约定（例如 `openai`、`deepseek`）。由 model 名与 base URL 推断，可用 `MILKY_FROG_PROVIDER` 覆盖。用于选择精确 token 计数器；当 provider 未知或其 tokenizer 包缺失时退回近似计数。
_避免使用_：厂商、后端、集成

**Terminal UI（终端界面）**：
使用带样式的终端输出，一致地呈现 Run 状态、结果、错误和空状态；它不是全屏交互式应用。
_避免使用_：TUI、Web UI、前端
