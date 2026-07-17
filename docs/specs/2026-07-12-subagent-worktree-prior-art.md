# 调研笔记：业界 subagent × git worktree 隔离方案对比

- **日期：** 2026-07-12
- **目的：** 为 `docs/adr/0018-subagent-worktree-isolation.md` 的"参考的业界实现"
  一节提供修正依据，供 team 讨论是否需要更新该 ADR。
- **背景：** ADR-0018 定稿时把 Codex CLI 列为"worktree + 一个 worktree 挂一个容器"
  的代表案例；重新核查 Codex 源码和官方文档后发现这个归因不准确，顺带调研了
  Claude Code、Cursor、Pi Agent 三个更贴切的案例，以及 DeerFlow 在"多个
  sub-agent 同时改一份文件"场景下的实际行为（结论：没有真正解决，是绕开）。

## 1. Codex CLI —— ADR 现有归因需要修正

- **官方 subagent 功能**（`[agents]`，`~/.codex/agents/*.toml` / `.codex/agents/*.toml`
  配置）：subagent 是同一 workspace 里的 **agent thread**，不新建 git worktree，
  **继承父 session 的 sandbox 策略**（可按 agent 覆盖 `sandbox_mode`）。并发/嵌套
  限制是纯配置：`max_threads`（默认 6）、`max_depth`（默认 1）、
  `job_max_runtime_seconds`。结果以**汇总摘要**回传，没有 diff/分支需要合并。
- **worktree + Codex 是社区用法，不是 subagent 编排器自带的机制**：人工
  `git worktree add` 出多个目录，每个目录里各起一个独立顶层 `codex` CLI 会话
  （进程级并行），编排器本身不创建/清理这些 worktree。
- 在 `openai/codex` 仓库 `codex-rs/` 下搜索 `worktree add`，命中的都是**检测**
  自己是否从 linked worktree 启动（config/hooks 解析、`git_info.rs`、Windows
  sandbox 路径白名单），没有"为 subagent 创建 worktree"的代码路径。
- **结论：** ADR-0018 里 Codex 那一行的"隔离单位=git worktree，一个 worktree
  挂一个容器"应删除或改写，不能作为设计依据引用。

## 2. Claude Code —— 更贴切的 prior art（官方文档 `code.claude.com/docs/en/worktrees`）

- 自定义 subagent frontmatter 加 `isolation: worktree`，每次调用**自动创建临时
  worktree**；跑完**没有改动就自动删除**，**有改动则保留 worktree + 分支**，
  交给上层决定要不要看/合并——与 ADR-0018 里 `finalize_worktree` 的
  "干净自动清理、有改动保留分支"设计几乎一致。
- 支持 `worktree.baseRef = "head"`：worktree 可以从本地当前 HEAD（带未推送提交）
  切出，适合"在进行中的工作基础上隔离 subagent"的场景。
- 运行中对 worktree 执行 `git worktree lock`，防止并发清理扫到正在用的 worktree；
  按 `cleanupPeriodDays` 定期清理老旧且干净的 worktree。
- **没有强制容器化**：纯文件系统层面隔离，不像 ADR-0018 的 `write` 档位那样
  强制 Docker bind-mount，默认信任本机执行。

## 3. Cursor（3.0 起，官方文档 `cursor.com/docs/configuration/worktrees`）

- `/worktree` 或聊天里选 "Worktree" 作为 agent location：底层 `git worktree add`
  + 新分支 + 独立 agent 进程，最多 8 个并行。
- **收尾方式和 Claude Code 不同**：不是"自动检测干净与否"，而是人工二选一——
  点 "Apply" 才合并回工作分支，否则直接丢弃 worktree 和分支。
- 云端 "background agents" 额外给每个 worktree 配一个独立 Firecracker microVM，
  形成"worktree（文件级隔离）+ microVM（执行级隔离）"两层结构，和 ADR-0018
  "worktree + Docker bind-mount"分层思路一致。

## 4. Pi Agent（pi.dev，`pi.dev/packages/@tintinweb/pi-subagents` 等）

- 同样是 `isolation: "worktree"` 配置，且明确是 **strict guarantee, not a hint**：
  worktree 创建失败就直接报错，**不会静默退化成不隔离运行**——和 ADR-0018
  第 0 点"`write` 档位没配 Docker 就直接拒绝，不做静默降级"的原则完全一致，
  可作为第二个官方案例引用。
- 隔离 agent 拿到完整仓库副本，自己提交，branch 从 agent 的 HEAD 切出，跑完
  保留分支交给上层决定，不自动合并。
- 社区扩展 `pasky/pi-side-agents`：每个子 agent 各起一个 tmux window + 一个
  git worktree，人可以随时接管——可观测性做到了 tmux 层，而不是
  checkpoint/EventHub 层。
- 事件总线 `pi.events` 发 `subagents:created/started/completed/failed/steered/compacted`
  等生命周期事件，与 ADR-0018 "Lifecycle signal" lane 的设计目标一致。

## 5. DeerFlow（ByteDance）—— ADR 原判断（Docker、不用 worktree）成立；
   新增结论：**没有真正解决"多 sub-agent 改同一文件"的合并问题**

- 隔离单位确认是 Docker 容器 + 独立文件系统（"Every task runs inside an
  isolated Docker container with a complete filesystem"），不用 git worktree——
  ADR-0018 原表格这条判断不用改。
- **默认互相隔离**：每个 sub-agent 任务是独立 sandbox，正常情况下不共享文件，
  "两个 agent 改同一个文件"场景很少发生。
- **真要共享一个 sandbox 时，靠的是加锁串行，不是合并**（`backend/AGENTS.md`
  原文）：
  > Gate check + tool execution are serialized per (thread, path), so
  > same-turn parallel writes cannot reuse one stale mark.
  > `str_replace` serializes read-modify-write per (sandbox.id, path).

  只保证写入操作本身的原子性/顺序性（不会脏读脏写），**后写覆盖先写
  （last-write-wins）**，没有任何 diff/patch 层面的语义合并或冲突检测。
- **"结果统一"发生在结果层，不是文件层**：Lead Agent / Orchestrator 的
  "synthesizes everything into a coherent output" 指的是把各 sub-agent 返回的
  **结构化文本结果**拼成最终报告——为"深度研究、多方查资料再汇总成报告"这类
  任务设计的，不是为"多个 sub-agent 协作改同一个代码仓库"设计的。
- **结论：** DeerFlow 没有 git 那种"改动即 diff/commit，可审阅、可合并"的语义。
  如果我们的 `subagent` 工具以后要支持"多个并发 write 档位 sub-agent、且可能
  touch 到重叠文件"的场景，这是一个需要显式设计的缺口——目前 ADR-0018
  "v1 只允许一层嵌套、单个 worktree"的范围还没触发这个问题，但应在
  ADR 的"未来工作"或"被否决的方案"里补一句，说明"多个并发 write 档位
  subagent 共享/重叠文件"目前 out of scope，防止日后有人误以为已经处理。

## 待讨论 / 待决定

1. 是否更新 ADR-0018 的"参考的业界实现"表格：删除/改写 Codex 那一行，
   补上 Claude Code、Cursor、Pi Agent 三行。
2. 是否在 ADR-0018 补一条"未来工作"或"已知缺口"：多个并发 write 档位
   subagent 若 touch 到重叠文件，目前没有合并/冲突检测机制（v1 靠"单层嵌套、
   单个 worktree"结构性避免，不是主动解决）。
3. Cursor 的"人工 Apply/丢弃二选一"和 Claude Code/Pi Agent 的"自动检测干净
   与否"两种收尾策略，我们已经选的是后者（`finalize_worktree`）——是否要在
   ADR 里补一句明确对比，说明为什么选这条而不是前者。
