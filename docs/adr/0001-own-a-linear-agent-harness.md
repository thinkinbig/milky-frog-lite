# Own a linear agent harness

Milky Frog will implement a small, single-process tool-calling loop instead of depending on LangChain, LangGraph, or a general workflow engine. The MVP needs one foreground Run with explicit model, Tool, user-input, and terminal transitions; owning that loop keeps its recovery and authorization semantics visible and avoids inheriting multi-agent and graph abstractions that the product does not need.

## Consequences

The Harness is responsible for streaming, limits, cancellation, context compaction, and state transitions. Multi-agent orchestration, task graphs, background workers, and parallel Tool execution are outside the MVP, while model and Tool boundaries remain explicit enough to evolve independently.

---

# 自主实现线性 Agent Harness

Milky Frog 将实现一个小型、单进程的工具调用循环，而不依赖 LangChain、LangGraph 或通用工作流引擎。MVP 只需要一个前台 Run，以及明确的模型、Tool、用户输入和终止状态转换；自主实现该循环可以让恢复与授权语义保持清晰，同时避免引入产品并不需要的多 Agent 和图抽象。

## 影响

Harness 负责流式输出、执行限制、取消、上下文压缩和状态转换。多 Agent 编排、任务图、后台 worker 和 Tool 并行执行不属于 MVP；模型与 Tool 的边界仍需保持明确，以便两者独立演进。
