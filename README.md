# Milky Frog

<p align="center">
  <img src="assets/milky-frog.png" width="320" alt="Pixel-art Milky Frog mascot">
</p>

Milky Frog (Chinese: 奶蛙) is a lightweight local coding-agent CLI. It runs one task at
a time in the foreground, uses built-in tools to read and edit files and run shell
commands, and saves a snapshot after each step so an interrupted task can be resumed.

Works with any OpenAI-compatible model provider.

## Requirements

- macOS or Linux
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
uv run milky-frog doctor    # verify configuration without a model request
uv run milky-frog           # start the interactive task loop
```

Configure your model with environment variables (a `.env` file in the current
directory also works):

```bash
export MILKY_FROG_API_KEY="..."           # required
export MILKY_FROG_MODEL="..."             # required
export MILKY_FROG_BASE_URL="..."          # optional, for OpenAI-compatible providers
export MILKY_FROG_PROVIDER="..."          # optional, openai/deepseek; inferred from the model by default
```

For exact token counting, install the matching optional dependency —
`uv sync --extra openai-tokenizer` (tiktoken) or `--extra deepseek-tokenizer`
(tokenizers); without it an approximate counter is used.

Optionally create project-level configuration and a Skills directory:

```bash
uv run milky-frog init
```

This writes `.milky-frog/config.toml` and `.milky-frog/skills/`. Both are safe to commit;
credentials must stay in environment variables and never be committed.

## Commands

```text
milky-frog                    # interactive task loop
milky-frog doctor             # check configuration without a model request
milky-frog init [WORKSPACE]   # create project config and Skills directory
milky-frog run TASK           # start a single foreground task
milky-frog runs               # list recent runs
milky-frog show RUN_ID        # show a run (add --json for raw output)
milky-frog resume [RUN_ID]    # resume an interrupted run
milky-frog prune              # remove old runs (add --dry-run to preview)
```

## Security

Shell commands require your approval before they run, and structured file operations are
constrained by a local policy. Sensitive paths such as `.env`, private keys, and `.git/`
are denied by default; add more exclusions in a `.milkyfrogignore` file.

This is a usage policy, not a security boundary — it does not safely isolate untrusted
code. Only run Milky Frog against code and commands you trust.

## License

MIT

---

# 奶蛙（Milky Frog）

奶蛙是一个轻量级本地代码 Agent CLI。它每次在前台执行一个任务，使用内置工具读取与编辑文件、
运行 shell 命令，并在每一步后保存快照，使中断的任务可以安全恢复。

兼容任意 OpenAI 接口的模型提供方。

## 环境要求

- macOS 或 Linux
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## 本地启动

```bash
uv sync
uv run milky-frog doctor    # 不发起模型请求即可校验配置
uv run milky-frog           # 启动交互式任务循环
```

通过环境变量配置模型（也可使用当前目录下的 `.env` 文件）：

```bash
export MILKY_FROG_API_KEY="..."           # 必填
export MILKY_FROG_MODEL="..."             # 必填
export MILKY_FROG_BASE_URL="..."          # 可选，用于兼容 OpenAI 的提供方
export MILKY_FROG_PROVIDER="..."          # 可选，openai/deepseek；默认按 model 名推断
```

需要精确 token 计数时，按 provider 安装对应可选依赖——
`uv sync --extra openai-tokenizer`（tiktoken）或 `--extra deepseek-tokenizer`
（tokenizers）；不安装则使用近似计数。

可选：初始化项目级配置与 Skill 目录：

```bash
uv run milky-frog init
```

该命令会创建 `.milky-frog/config.toml` 和 `.milky-frog/skills/`。这些文件可以提交到版本库；
凭据必须只通过环境变量提供，切勿提交。

## 命令

```text
milky-frog                    # 交互式任务循环
milky-frog doctor             # 不发起模型请求即可校验配置
milky-frog init [WORKSPACE]   # 创建项目配置与 Skill 目录
milky-frog run TASK           # 启动单个前台任务
milky-frog runs               # 列出最近的 Run
milky-frog show RUN_ID        # 查看某个 Run（加 --json 输出原始数据）
milky-frog resume [RUN_ID]    # 恢复中断的 Run
milky-frog prune              # 清理旧的 Run（加 --dry-run 预览）
```

## 安全边界

shell 命令在执行前需要你的批准，结构化文件操作受本地策略约束。`.env`、私钥和 `.git/`
等敏感路径默认禁止访问；可在 `.milkyfrogignore` 文件中添加更多排除规则。

这是一项使用策略，而非安全隔离边界——它无法安全隔离不可信代码。请仅在你信任的代码与命令上运行奶蛙。
