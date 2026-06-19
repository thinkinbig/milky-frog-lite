# Milky Frog

Milky Frog (Chinese: 奶蛙) is a lightweight local coding-agent CLI. It runs one foreground task at a time,
coordinates model and Tool calls through a linear Harness, and persists an append-only Checkpoint
so interrupted Runs can eventually be resumed safely.

> The repository currently contains the executable framework skeleton. `doctor`, `init`, `runs`,
> and `show` work; model-provider wiring, built-in Tools, and Checkpoint replay are the next slices.

## Design goals

- A small, owned agent loop instead of a general workflow engine.
- Explicit seams for model providers, Tools, and Checkpoint storage.
- Typed lifecycle Handlers for authorization, persistence, and observability.
- Project Skills as declarative instructions, never executable plugins.
- An honest Local Sandbox policy without claiming host-level isolation.

See the [domain glossary](CONTEXT.md) and [architecture decisions](docs/adr/) for the canonical
language and trade-offs.

## Requirements

- macOS or Linux
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
uv run milky-frog --help
uv run milky-frog doctor
```

Model configuration is read from environment variables:

```bash
export MILKY_FROG_API_KEY="..."
export MILKY_FROG_MODEL="..."
export MILKY_FROG_BASE_URL="..."  # optional
```

Initialize project-level configuration and Skills with:

```bash
uv run milky-frog init
```

This creates `.milky-frog/config.toml` and `.milky-frog/skills/`. They are intentionally safe to
commit; credentials must remain in environment variables.

## Available commands

```text
milky-frog doctor
milky-frog init [WORKSPACE]
milky-frog runs
milky-frog show RUN_ID [--json]
milky-frog run TASK       # interface present; provider wiring pending
milky-frog resume RUN_ID  # interface present; replay pending
```

## Project layout

```text
src/milky_frog/
├── checkpoint/  # append-only event persistence seam and SQLite adapter
├── cli/         # terminal command composition
├── handlers/    # typed lifecycle events and Handler registry
├── harness/     # linear Run coordinator
├── memory/      # cross-Run project knowledge seam
├── models/      # model-provider seam
├── sandbox/     # Local Sandbox policy
├── skills/      # progressive Skill discovery and loading
├── tools/       # Tool interface and registry
└── ui/          # Rich terminal output
```

## Development checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Security

The Local Sandbox constrains structured file operations and requires approval for shell commands;
it is not a security boundary for untrusted code. Sensitive files such as `.env`, private keys, and
`.git/` are denied by default. Additional project paths can be excluded in `.milkyfrogignore`.

## License

MIT

---

# 奶蛙（Milky Frog）

奶蛙是一个轻量级本地代码 Agent CLI。它每次以前台方式执行一个任务，通过线性
Harness 协调模型与 Tool 调用，并将 Checkpoint 保存为仅追加事件，使中断的 Run 最终可以安全恢复。

> 当前仓库提供可执行、可测试的框架骨架。`doctor`、`init`、`runs` 和 `show` 已可使用；模型适配、
> 内置 Tool 与 Checkpoint 恢复将在后续纵切中实现。

## 设计目标

- 自主维护小型 Agent 循环，不引入通用工作流引擎。
- 为模型提供方、Tool 和 Checkpoint 存储建立明确 seam。
- 使用类型化生命周期 Handler 实现授权、持久化与可观测性。
- 项目 Skill 仅包含声明式指令，不作为可执行插件。
- 如实定义 Local Sandbox 策略，不宣称提供宿主机级隔离。

规范术语与设计权衡参见[领域术语表](CONTEXT.md)和[架构决策](docs/adr/)。

## 环境要求

- macOS 或 Linux
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## 本地启动

```bash
uv sync
uv run milky-frog --help
uv run milky-frog doctor
```

模型配置通过环境变量提供：

```bash
export MILKY_FROG_API_KEY="..."
export MILKY_FROG_MODEL="..."
export MILKY_FROG_BASE_URL="..."  # 可选
```

初始化项目配置与 Skill 目录：

```bash
uv run milky-frog init
```

该命令会创建 `.milky-frog/config.toml` 和 `.milky-frog/skills/`。这些文件可以提交到版本库；
凭据必须只通过环境变量提供。

## 安全边界

Local Sandbox 会限制结构化文件操作，并要求用户批准 shell 命令，但它不能安全隔离不可信代码。
`.env`、私钥和 `.git/` 等敏感路径默认禁止读取；项目可通过 `.milkyfrogignore` 增加排除规则。
