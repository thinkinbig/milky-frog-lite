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
A typed callback registered for a Harness lifecycle event. Handlers enforce cross-cutting behavior such as authorization, checkpointing, and observability.
_Avoid_: Middleware, hook

**Checkpoint**:
The durable event history and current state of a Run, used to resume execution without repeating completed Tools.
_Avoid_: Memory, snapshot

**Memory**:
User-approved, project-scoped knowledge that remains available across Runs.
_Avoid_: Checkpoint, conversation history

**Skill**:
A progressively loaded instruction bundle that gives the Agent task-specific operating knowledge without adding executable code.
_Avoid_: Plugin, Tool

**Local Sandbox**:
A policy boundary that constrains structured file operations and requires approval for shell commands, but does not isolate untrusted code from the host.
_Avoid_: Container, secure sandbox

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
注册到 Harness 生命周期事件的类型化回调。Handler 用于实现授权、checkpoint 和可观测性等横切行为。
_避免使用_：Middleware、Hook

**Checkpoint（检查点）**：
一个 Run 的持久化事件历史和当前状态，用于恢复执行且不重复运行已完成的 Tool。
_避免使用_：Memory、快照

**Memory（记忆）**：
经用户批准、作用于项目并可跨 Run 使用的知识。
_避免使用_：Checkpoint、对话历史

**Skill（技能）**：
按需渐进加载的指令包，为 Agent 提供特定任务的操作知识，但不增加可执行代码。
_避免使用_：插件、Tool

**Local Sandbox（本地沙箱）**：
一种策略边界，用于限制结构化文件操作并要求用户批准 shell 命令，但不将不可信代码与宿主机隔离。
_避免使用_：容器、安全沙箱
