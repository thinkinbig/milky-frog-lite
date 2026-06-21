# Milky Frog

<p align="center">
  <img src="assets/milky-frog.png" width="320" alt="Pixel-art Milky Frog mascot">
</p>

Milky Frog (Chinese: 奶蛙) is a lightweight local coding-agent CLI. It runs one foreground task at a time,
coordinates model and Tool calls through a linear Harness, and persists an append-only Checkpoint
so interrupted Runs can be resumed safely.

> The repository provides OpenAI-compatible foreground Runs, built-in file Tools, Checkpoint-replay
> resume (`milky-frog resume`), a multi-turn interactive loop, mid-run steering on POSIX TTYs, and
> optional Langfuse observability. See [CONTEXT.md](CONTEXT.md) and [docs/adr/](docs/adr/) for
> architecture details.

## Design goals

- A small, owned agent loop instead of a general workflow engine.
- Explicit seams for model providers, Tools, and Checkpoint storage.
- Read-only lifecycle Handlers for streaming output and observability (ADR-0012).
- Typed Checkpoint events as a Pydantic discriminated union (ADR-0013).
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
uv run milky-frog
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
milky-frog                # interactive task loop
milky-frog doctor
milky-frog init [WORKSPACE]
milky-frog runs
milky-frog show RUN_ID [--json]
milky-frog run TASK
milky-frog resume RUN_ID [TASK]   # replay pending work or continue with a new turn
```

## Project layout

```text
src/milky_frog/
├── checkpoint/   # CheckpointStore seam, typed CheckpointBody (Pydantic), SQLite adapter
├── cli/          # Typer commands, HandlerFactory, MilkyFrogAdvancer
├── handlers/     # lifecycle signals, read-only HandlerRegistry (notify)
├── harness/      # Harness loop, state fold, checkpoint event factories
├── foreground.py # ForegroundRun protocol (StartRun / ResumeRun)
├── memory/       # cross-Run project knowledge seam
├── models/       # model-provider seam
├── runtime.py    # MilkyFrog: sync boundary, stdin steering
├── sandbox/      # Local Sandbox policy
├── skills/       # progressive Skill discovery and loading
├── tools/        # Tool interface, registry, built-ins
└── ui/           # Rich terminal output, RunAdvancer protocols
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
Harness 协调模型与 Tool 调用，并将 Checkpoint 保存为仅追加事件，使中断的 Run 可以安全恢复。

> 当前仓库已支持兼容 OpenAI 的前台 Run、内置文件 Tool、Checkpoint 恢复（`milky-frog resume`）、
> 多轮交互循环、POSIX TTY 上的 mid-run steering，以及可选的 Langfuse 可观测性。架构细节见
> [CONTEXT.md](CONTEXT.md) 与 [docs/adr/](docs/adr/)。

## 设计目标

- 自主维护小型 Agent 循环，不引入通用工作流引擎。
- 为模型提供方、Tool 和 Checkpoint 存储建立明确 seam。
- 只读生命周期 Handler，用于流式输出与可观测性（ADR-0012）。
- 类型化 Checkpoint 事件（Pydantic discriminated union，ADR-0013）。
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
uv run milky-frog
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
