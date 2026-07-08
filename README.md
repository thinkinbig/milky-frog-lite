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

## Containerized execution (opt-in)

By default Milky Frog runs Tools on the host under a path-deny policy. To run
`bash` and post-edit verification commands inside a container instead, add to
`.milky-frog/config.toml`:

```toml
[sandbox]
kind = "docker"
image = "python:3.12-bookworm"
workspace_mount = "/mnt/workspace"   # optional; must live under /mnt
```

Requires the `docker` CLI on `PATH` and a running daemon — `milky-frog doctor`
checks both.

How it works: your workspace is bind-mounted into a container that is created
on first use and reused for the rest of the session, then removed on exit.
File Tools (`read_file`, `write_file`, `edit_file`, `grep`, `list_dir`) keep
reading and writing on the host — the bind mount means both sides see the same
files.

**Your image must carry your toolchain.** Post-edit verification
(`[verification].commands`, on by default) runs through the same Sandbox as
`bash`, so it executes *inside the container*. The default commands are
`uv run ruff check .` and `uv run pytest -q`; a stock `python:3.12-bookworm`
has no `uv`, so every edit would report a failure. Either build an image with
your tools installed, or set `[verification].commands` to commands the image
can actually run.

**Host build artifacts do not travel.** `.venv`, `node_modules`, `target/` and
friends live in the workspace, so the bind mount carries them into the
container — but they were built for your host's OS and architecture. A macOS
`.venv/bin/python` is a symlink to a macOS interpreter and is simply broken
inside a Linux container. Build them in the container, or keep them out of the
workspace.

Caveats:

- **File access is not isolated.** A process in the container can reach every
  file in the bind-mounted workspace. This isolates *command execution*, not
  the workspace.
- Host `HOME` / `PATH` / `SHELL` are not forwarded into the container. Names
  listed in `env_allowlist_extra` are.
- A `bash` timeout kills the host-side `docker exec`; the process inside the
  container may keep running until the container is removed at session exit.
- MCP servers still run on the host, not in the container.

## Security

Shell commands require your approval before they run, and structured file operations are
constrained by a local policy. Sensitive paths such as `.env`, private keys, and `.git/`
are denied by default; add more exclusions in a `.milkyfrogignore` file.

This is a usage policy, not a security boundary — it does not safely isolate untrusted
code. Only run Milky Frog against code and commands you trust. Setting
`[sandbox].kind = "docker"` moves *command execution* into a container, but the workspace
is bind-mounted and stays reachable from inside it.

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

## 容器化执行（可选）

奶蛙默认在宿主机上执行 Tool，并受路径拒绝策略约束。若希望改为在容器内执行 `bash`
与编辑后的校验命令，请在 `.milky-frog/config.toml` 中加入：

```toml
[sandbox]
kind = "docker"
image = "python:3.12-bookworm"
workspace_mount = "/mnt/workspace"   # 可选；必须位于 /mnt 之下
```

需要 `PATH` 上有 `docker` CLI 且守护进程正在运行——`milky-frog doctor` 会同时检查这两项。

工作方式：Workspace 会以 bind mount 挂载进容器；容器在首次使用时创建，并在整个
session 内复用，退出时移除。文件类 Tool（`read_file`、`write_file`、`edit_file`、
`grep`、`list_dir`）仍在宿主机上读写——bind mount 保证两侧看到的是同一批文件。

**镜像必须自带你的工具链。** 编辑后的校验（`[verification].commands`，默认开启）
与 `bash` 走同一个 Sandbox，因此它**在容器内执行**。默认命令是
`uv run ruff check .` 和 `uv run pytest -q`；而原版 `python:3.12-bookworm` 里没有 `uv`，
于是每次编辑都会报失败。要么构建一个装好工具的镜像，要么把 `[verification].commands`
改成镜像里真的跑得动的命令。

**宿主机构建产物不能直接复用。** `.venv`、`node_modules`、`target/` 都在 Workspace 里，
bind mount 会把它们带进容器 —— 但它们是为宿主机的操作系统与架构构建的。macOS 的
`.venv/bin/python` 是一个指向 macOS 解释器的符号链接，在 Linux 容器里就是一条断链。
请在容器内构建它们，或把它们移出 Workspace。

注意事项：

- **文件访问并未隔离。** 容器内的进程可以访问 bind-mount 的整个 Workspace。
  被隔离的是*命令执行*，不是 Workspace 本身。
- 宿主机的 `HOME` / `PATH` / `SHELL` 不会传入容器；`env_allowlist_extra` 中列出的
  变量名会传入。
- `bash` 超时只会杀掉宿主机侧的 `docker exec`；容器内的进程可能一直运行到
  session 退出、容器被移除为止。
- MCP server 仍运行在宿主机上，而不在容器内。

## 安全边界

shell 命令在执行前需要你的批准，结构化文件操作受本地策略约束。`.env`、私钥和 `.git/`
等敏感路径默认禁止访问；可在 `.milkyfrogignore` 文件中添加更多排除规则。

这是一项使用策略，而非安全隔离边界——它无法安全隔离不可信代码。请仅在你信任的代码与命令上运行奶蛙。
设置 `[sandbox].kind = "docker"` 会把*命令执行*移入容器，但 Workspace 是 bind-mount 进去的，
在容器内依然可以访问。
