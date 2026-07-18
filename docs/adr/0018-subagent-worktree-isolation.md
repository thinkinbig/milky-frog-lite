# ADR-0018：`subagent` 工具 —— 用 git worktree 隔离的嵌套 Run

- **状态：** 已接受并实现（Accepted，2026-07-11）
- **相关文档：** `docs/adr/0013-handler-design.md`（本 ADR 沿用的"按生命周期拆分 seam"思路）、
  `core/sandbox.py`（本设计复用而非替代的 Sandbox 策略边界）。

## 背景（Context）

Milky Frog 目前一次只跑一个线性的前台 Run（`AgentHarness.run` /
`AgentLoop.advance`）。代码库里**完全没有"派生子任务"的概念**——在
`domain/`、`harness/`、`events/` 里搜索 "subagent" / "spawn" / "delegate" /
"child run" 都没有任何结果。

我们希望模型能把一个子任务（比如"调研一下 X，回来汇报"，或者"做一次隔离的修改，
我再决定要不要保留"）交给一个**嵌套 Run**去执行，这个嵌套 Run 需要：

- 独立运行，有自己的对话记录（transcript）、模型调用预算、独立的 Checkpoint 记录
  （这样它自己也能被检查/恢复）；
- 不能在编辑文件时破坏父 Run 正在使用的工作目录；
- 把结果（如果做了修改，还要包括改动"存在哪里"）以普通 `ToolResult` 的形式
  返回给父 Run。

Git 的 `worktree` 是天然合适的隔离手段：它能给嵌套 Run 一个真实、独立的工作目录
（从同一份 `.git` 历史签出）——不需要完整 clone，不需要 push/pull 来回同步，
父 Run 在嵌套 Run 跑完后可以直接查看 diff。

### 本设计复用的现有机制

- `Sandbox` 协议（`core/sandbox.py`）+ `SandboxFactory`——已经是"在某个
  workspace 里执行命令"的策略边界。`[sandbox].kind`（`ProjectConfig.sandbox.kind`，
  `project.py:123`）已经通过 `make_sandbox_factory()`（`core/runtime/assemble.py:28-46`）
  按 workspace 选择 `LocalSandbox` 还是 `DockerSandbox`。worktree 路径本质上
  就是另一个 workspace，所以嵌套 Run 直接复用同一个 factory 就能"顺带"获得
  沙箱隔离。
- `AgentHarness.run(RunRequest)`（`harness/harness.py:72-94`）已经会生成新的
  `run_id`、初始化 `RunState`、驱动 `AgentLoop.advance` 跑到结束——这正是
  嵌套 Run 需要的形状。`RunRequest`（`domain/run.py:36-41`）字段是
  `prompt`、`workspace`、`max_model_calls`、`cancellation`、`skill_content`，
  这个类型不需要改动。
- `run_local_command`（`adapters/local/command.py:46-90`）已经封装了
  `asyncio.create_subprocess_shell`，处理超时和进程组——正好是执行
  `git worktree add` / `git worktree remove` 需要的原语。

### 目前缺失的部分

- `Tool.execute(context, input)`（`harness/tools/base.py:42-47`）拿到的只是
  `ToolContext`（run_id、workspace、cancellation、sandbox、token_counter、
  search_prefix），没有能力构造一个新的 `AgentHarness`，因为所需的原料
  （`Settings`、model、`EventHub`、`CheckpointStore`、`ToolRegistry`）只存在于
  `app/session.py`（`AgentSession.__aenter__`，通过 `make_agent_harness`）。
- `ToolResult`（`domain/tools.py:25-28`）只有 `content: str`、`is_error: bool`、
  `display_content: str | None`——没有结构化的 metadata 字段。worktree 路径
  和分支名只能编码进文本里。

## 决策（Decision）

### 0. 能力档位在子任务启动时一次性决定：只读档位不建 worktree，可写档位强制 Docker + worktree

设计过程中发现，"要不要隔离"和"要不要能写"必须绑在一起决定，不能分开打补丁：
只读子任务不碰磁盘写入，起 worktree 纯属浪费；可写子任务如果只靠 worktree、
不配容器隔离，`bash` 工具一条 `cd ../.. && ...` 就能绕开 worktree 边界，因为
`LocalSandbox.run_command`（`adapters/local/sandbox.py:76-89`）只设置了子进程的
`cwd` 和环境变量白名单，**没有任何操作系统级别的强制**——真正的路径边界检查
（`resolve()`，`sandbox.py:44-53`）只覆盖 `read_file`/`write_file`/`edit_file`
这些结构化工具，`bash` 完全绕开这层检查。

因此 `SubagentInput` 增加一个 `capability: Literal["read_only", "write"]`
字段（默认 `"read_only"`），两条路径互斥：

| 档位 | 工具集 | Worktree | Sandbox | 审批策略 |
|---|---|---|---|---|
| `read_only`（默认） | 只读内建工具：`read_file`/`grep`/`list_dir`/`fetch`/`web_search` | **不创建**——直接在父 workspace 里跑嵌套 Run | 复用父 Run 的 sandbox 实例 | 不涉及审批（只读工具默认不需要） |
| `write` | 全部内建工具（除 `subagent` 自身） | **强制创建** | **强制 `DockerSandboxFactory`**，且必须指向 worktree 路径，而不是父 workspace 根目录；若 `[sandbox].kind != "docker"`，`subagent` 工具直接拒绝这次调用并在 `ToolResult.is_error=True` 里说明原因 | 该嵌套 Run 用独立的、默认 `auto_approve()` 的 `SessionToolPolicy` 实例（见第 4 点），不触发交互式审批 |

`write` 档位强制 Docker 而不是"能配就配"：这一约束参考了 Codex CLI 的
"Docker + worktree 标准组合"（一个 worktree 对应一个容器的 bind mount，
容器之间互相看不到彼此），以及项目里 `DockerSandbox` 自身文档写明的边界——
`adapters/docker/sandbox.py:1-13` 明确说 `LocalSandbox` 是"a policy boundary,
**not host isolation**"，只有 Docker bind-mount 才是"real process isolation"。
给 `DockerSandboxFactory` 传入的 `workspace` 参数必须是 **worktree 路径**而不是
父仓库根目录——bind mount 只暴露传入的那一个目录（`docker/sandbox.py:137-138`
的 `-v f"{workspace}:{self._workspace_mount}"`），这样容器里除了这个 worktree
什么都看不见，才是真正的边界。

两个从 Codex 的既有实现里学到的具体坑，一并作为 `create_worktree` 的实现约束：

1. **挂载时不能把父仓库的 `.git` 目录一起挂进容器。** linked worktree 的
   `.git` 是一个指向父仓库 `.git/worktrees/<name>/` 的**指针文件**，不是目录；
   如果挂载范围不小心覆盖了它，会破坏 worktree 本身的隔离机制。
2. **必须显式验证/锁定容器内进程的 cwd 停在 worktree 内**，防止 `git switch`/
   `git checkout`（尤其是不带路径限定的调用）意外改动到共享 `.git` 元数据里
   记录的 HEAD，从而殃及父仓库当前签出的分支——这是社区里已知会踩的坑
   （Claude Code 自己的 `isolation:worktree` 就报过这个问题）。

只读档位不用管上述任何一条：因为它压根不创建 worktree、不跑 `bash`，没有
"越界"这个风险面。

### 1. `subagent` 工具在构造时注入一个 runner，而不是自己去拼装 harness

```python
# harness/tools/builtins/subagent.py
class SubagentInput(BaseModel):
    prompt: str
    max_model_calls: int | None = None

class SubagentRunner(Protocol):
    """在隔离的 git worktree 里跑一个嵌套 Run；由 AgentSession 持有和构造。"""
    async def __call__(self, prompt: str, max_model_calls: int | None) -> SubagentOutcome: ...

class SubagentTool:
    name = "subagent"
    def __init__(self, runner: SubagentRunner) -> None:
        self._runner = runner

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        ...
```

这个模式沿用现有的 `Sandbox` / `SandboxFactory` 注入方式（
`AgentHarness.__init__(..., sandbox_factory: SandboxFactory = LocalSandbox)`），
而不是发明一套新的装配机制。`AgentSession.__aenter__` 是唯一同时握有全部原料
（`Settings`、`hub`、`checkpoints`、`sandbox_factory`、model、token counter）的地方，
所以由它来构造这个闭包，再把它连同其他内建工具一起传给 `default_tools()`。

### 2. Worktree 生命周期是一个新的、职责很窄的 seam（只在 `write` 档位启用）

```python
# harness/subagent_worktree.py
async def create_worktree(sandbox: Sandbox, base_workspace: Path, run_id: str) -> Path: ...
async def finalize_worktree(sandbox: Sandbox, worktree_path: Path, branch: str) -> WorktreeOutcome: ...
```

- `create_worktree` 用**父 Run** 的 sandbox 执行
  `git worktree add <tmp_path> -b subagent/<run_id>`（父 workspace 就是 git 根目录）。
- 嵌套 Run 跑完后，`finalize_worktree` 执行 `git status --porcelain`：
  - **干净（无改动）** → `git worktree remove <tmp_path>` 自动清理，不留状态——
    这和已有文档里 `Agent` 工具 `isolation:worktree` 的行为一致（无改动时自动清理）。
  - **有改动** → 保留 worktree 和分支，交给父 Run 决定要不要查看/合并。清理是
    后续动作（可以是单独的 `cleanup_worktree` 工具，或手动 `git worktree remove`），
    不做自动清理——未经确认就删掉嵌套 Run 未审查的改动，属于本项目行为准则里的
    "破坏性操作"，不应该自动发生。

### 3. 嵌套 Run 复用 `AgentHarness.run`；`write` 档位强制通过 Docker factory 上沙箱

runner 的执行逻辑（以 `write` 档位为例）：

```python
if capability == "write" and session.sandbox_kind != "docker":
    return ToolResult(content="subagent write capability requires [sandbox].kind = \"docker\"", is_error=True)

worktree = await create_worktree(parent_sandbox, parent_workspace, run_id=uuid4().hex)
nested_sandbox_factory = session.docker_sandbox_factory  # 显式指向 worktree，不是父 workspace
result = await nested_harness.run(RunRequest(prompt=prompt, workspace=worktree, max_model_calls=max_model_calls or DEFAULT_SUBAGENT_MAX_CALLS))
outcome = await finalize_worktree(parent_sandbox, worktree, branch)
```

`read_only` 档位没有 `worktree`/`sandbox_kind` 这两行，直接
`nested_harness.run(RunRequest(prompt=prompt, workspace=parent_workspace, ...))`，
复用父 Run 已有的 sandbox 实例。

`nested_harness` 是**第二个 `AgentHarness` 实例**，与父 Run 共享
`checkpoints`/`hub`/model/token-counter，但用的 `ToolRegistry` 排除了
`subagent` 本身（见下一条）。不是复用同一个 harness 实例——`AgentHarness`
没有"当前 workspace 覆盖"这个概念，`RunRequest.workspace` 本身就已经是
每次调用传入的。

### 4. 防递归：从嵌套 Run 的注册表里排除 `subagent`，而不是靠 `SessionToolPolicy`

`SessionToolPolicy`（`core/session_tool_policy.py:15-65`）是 session 级别、
可变的共享状态（`harness.py:69`）——如果在这里禁用 `subagent`，也会连带影响
父 Run 本身。所以改为：`AgentSession` 在构造时建**两个 `ToolRegistry` 实例**：
一个是正常的（包含全部内建工具，给顶层 Run 用），一个是 `nested_tools`
（内建工具减去 `subagent`，只给 `SubagentRunner` 闭包使用）。v1 版本只允许
一层嵌套，靠这个结构上的排除来保证，不需要额外的深度计数器。

同样的"不共享 session 级可变状态"原则也适用于审批策略：`write` 档位的嵌套
`AgentHarness` 用一个**新构造的、独立的 `SessionToolPolicy` 实例**（不是
`session.policy`），并在构造后立刻调用 `.auto_approve()`。这不是绕过审批，
而是把"要不要交互式审批"这件事从"逐工具运行时询问"挪到"启动子任务前的一次性
档位选择"——现有审批机制（`ToolStepExecutor` 命中 `NEEDS_APPROVAL` 时，
`AgentLoop.advance` 会直接**结束整个 Run** 并返回 `RunStatus.WAITING_FOR_APPROVAL`，
见 `events/tool_step.py:44-72`）是按 `run_id` 设计的整 Run 级暂停，UI 只监听
当前前台 Run 的 `run_id`；嵌套 Run 的 `run_id` 对 UI 不可见，如果嵌套 Run 命中
`NEEDS_APPROVAL`，`nested_harness.run()` 会返回一个没有任何人能 `respond_approval`
的挂起状态，`subagent` 工具的 `execute()` 会**永久卡住**，而不是"退化成不安全但至少能跑"。
所以为嵌套 Run 关闭交互式审批不是可选项，是让这条路径不卡死的必要条件；真正的
安全把关落在"强制 Docker + worktree 的结构性边界"和"跑完后的强制 diff 审阅"
这两层上（对应第 0 点和第 2 点）。

### 5. `ToolResult` 用文本编码路径/分支信息，不新增字段

不给 `ToolResult` 加新的 metadata 字段。`subagent` 工具把确定性的文本块
写进 `content`：

```
Subagent finished (run_id=<id>, worktree=<path>, branch=subagent/<id>)
<result summary>
```

如果是"干净并已自动清理"的情况，就省略 worktree/branch 那一行。这样
`ToolResult` 保持不变——加一个"结构化 metadata"字段的改动面更大（每个工具、
每个渲染层都要跟着改），目前只有这一个工具需要，不值得为此改类型。

## 参考的业界实现（Prior art）

在敲定"能力分档 + Docker 强制"这个方案之前，调研了两个可比的多 Agent 项目，
两者选了不同的路，正好印证了我们按场景做的取舍：

| | **Codex CLI** | **DeerFlow**（ByteDance） | **本 ADR** |
|---|---|---|---|
| 隔离单位 | git worktree，一个 worktree 挂进一个容器 | 每任务一个全新容器 + 独立文件系统（不用 git worktree，文件是拷贝/上传进去的） | 与 Codex 一致：worktree + Docker，只在 `write` 档位启用 |
| 结构性边界 | `workspace-write` 沙箱：OS 级强制（macOS Seatbelt / Linux Landlock-seccomp），bind mount 只暴露一个 worktree | Docker 容器边界，每任务独立文件系统 | `DockerSandbox` bind mount，只挂 worktree 目录（`docker/sandbox.py:137-138`） |
| 运行中要不要问人 | `--ask-for-approval never` 可关，配合沙箱边界仍安全 | 提供可配置"审批闸门"，官方建议生产环境开启（"纯策略预授权目前还不够成熟"） | 关闭（嵌套 Run 独立 `SessionToolPolicy.auto_approve()`），因为现有审批机制是整
 Run 级暂停，嵌套 Run 里卡住会无法恢复 |
| 结果落地前的把关 | 无强制 diff 步骤，靠沙箱边界兜底 | 靠实时审批闸门做把关，不是事后 diff | 强制：`write` 档位跑完不自动清理 worktree，改动以分支形式保留，交给父 Run/用户审阅后再决定是否合并 |
| 为什么这么选 | 纯代码协作场景，多个子任务要在同一份 git 历史上并行推进 | 长周期研究/生成场景（跑脚本、装包、出报告），子任务之间通常不需要共享同一份 git 历史 | 和 Codex 场景一致：milky-frog 是在用户现有仓库里做代码修改的子任务，必须共享同一份 `.git` 历史 |

两个从 Codex 实现里学到、已经写进第 0 点的具体坑：挂载时排除父仓库
`.git` 目录（linked worktree 的 `.git` 是指针文件，覆盖会破坏隔离）；
以及显式锁定容器内 cwd，防止 `git switch`/`checkout` 误改共享的 HEAD
——这是 Claude Code 自己的 `isolation:worktree` 已经报过的已知问题
（`anthropics/claude-code#55708`），提前在实现约束里规避。

## 影响（Consequences）

- 嵌套 Run 是一等公民：独立 checkpoint、独立可恢复（`milky-frog resume <nested_run_id>`
  不需要改动就能用），和父 Run 一样出现在同一个 `EventHub` 流里（Langfuse/UI 都能看到）。
- `read_only` 档位直接使用父 Workspace，但没有写入、编辑或 shell Tool；`write`
  档位使用共享对象数据库、独立签出文件的 git worktree，并额外获得容器命令执行
  边界，而不只是应用层的路径检查。
- `write` 档位强制要求项目配置 `[sandbox].kind = "docker"`；未配置 Docker 的
  项目只能用 `read_only` 档位的 `subagent`，这是一个明确的功能降级而不是静默
  的安全降级——`subagent` 工具会直接返回错误说明原因，不会退化成"用
  `LocalSandbox` 凑合跑一个不安全的可写子任务"。
- 新增一个用户可见的风险：`write` 档位子任务跑完后留下的"脏" worktree 会
  一直占磁盘，直到用户（或未来的 `cleanup_worktree` 工具）手动清理。v1 阶段
  可以接受——符合"不擅自销毁未审查的工作成果"这个原则。

## 被否决的方案（Rejected alternatives）

- **用子进程跑一个嵌套的 `milky-frog` CLI 调用。** 否决：会失去进程内共享的
  `EventHub`/Checkpoint/token-counter 装配（子进程方式拿不到这些），还要
  额外处理进程生命周期（启动、超时、取消时 kill），而这些 `AgentLoop.advance`
  对父 Run 来说已经解决了。
- **用普通子目录隔离，不新建 git 分支。** 否决：无法防止父 Run 自己并发编辑
  同一批文件，而且用户也没有一个"分支"作为整体单位去 merge/diff/丢弃。
- **靠 `SessionToolPolicy` 来控制递归。** 否决：这个 policy 是共享、可变、
  session 级别的状态（`harness.py:69`）；在这里禁用 `subagent` 也会连带
  影响父 Run 本身对该工具的使用。
- **给 `ToolResult` 加一个结构化的 `metadata: dict` 字段。** 暂缓：从原则上讲
  是对的，但这个改动会影响每一个工具和每一个 UI 渲染层，而目前只有这一个
  工具需要结构化输出。等第二个工具也有类似需求时再重新考虑。
- **不分档位，`subagent` 一律给全部工具 + worktree。** 否决：只读子任务不写
  磁盘，起 worktree 没有任何安全收益，纯粹浪费一次 `git worktree add`/`remove`
  和一条分支；先做出来会显得功能自洽，实际上是"隔离了写入但又不让写"的空转
  设计（详见调研过程中被指出的这个矛盾）。
- **`write` 档位允许退化到 `LocalSandbox`（如果项目没配 Docker）。** 否决：
  `LocalSandbox.run_command` 只设置 `cwd` 和环境变量白名单，对 `bash` 命令
  没有任何操作系统级强制，`cd ../..` 就能绕开 worktree 边界——这种情况下
  "有隔离"只是错觉，比明确报错更危险。宁可让 `subagent` 直接拒绝可写请求，
  也不做静默降级。
- **保留逐工具交互式审批，只是想办法让嵌套 Run 的审批请求也弹到 UI 上。**
  否决（v1）：现有审批机制按 `run_id` 设计成"整 Run 暂停 + 外部调用
  `respond_approval` 恢复"，UI 目前只感知前台 Run 的 `run_id`；要把嵌套 Run
  的挂起状态也路由到 UI，需要改 UI 感知多 `run_id`、改恢复流程识别"这是谁的
  子任务"——改动面明显大于本 ADR 的范围，值得单独一个 ADR 去做，不在这次
  一起解决。

## 更新记录（2026-07-12）：合并确认走确定性审批，而非指望模型提起

第 2 点原本只规定"有改动就保留 worktree，清理是后续手动动作"，没有规定"要不要
合并"这件事怎么被人看到。实践中发现，只把 worktree/branch 信息写进
`ToolResult.content` 不够——这依赖模型在下一轮对话里主动提起，模型可能不提，
用户也可能没注意到那一句话，事后也没有任何提醒机制。

补充决策：`subagent` 工具在 `worktree_kept=True` 时，除了原有的文本前缀，还在
`ToolResult` 上设置一个新增的 `follow_up: FollowUpCall | None` 字段
（`domain/tools.py`）——这正是本 ADR 第 5 点"等第二个工具也有类似需求时再重新
考虑"那句话预告的第二个需求。`AgentLoop.advance`（`events/loop.py`）在折叠工具
执行结果时，看到 `follow_up` 就合成一个新的 `ToolCall`（新建的 `merge_worktree`
工具，`harness/tools/builtins/merge_worktree.py`，`requires_approval` 恒为
`True`），追加进 transcript，再和当前批次里其他需要审批的调用一起走**已有的**
`NEEDS_APPROVAL` → `RunStatus.WAITING_FOR_APPROVAL` 暂停路径。

这样"要不要合并"变成一个**由 harness 保证会发生的暂停**，不依赖模型文本生成：
- 不需要新 `RunStatus`、新 lifecycle signal、新 TUI 组件——`unmatched_tool_calls`
  只看最后一条 assistant 消息的 `tool_calls`，不关心这条消息是模型生成的还是
  `append_synthetic_tool_call`（`harness/state.py`）合成的，所以现有的
  `respond_approval`/`respond_approvals`、checkpoint 持久化/恢复、TUI
  `ApprovalPrompt` 全部原样复用。
- 用户批准后，`merge_worktree` 执行 `git merge --no-ff`；冲突时中止合并、
  worktree 原样保留（`merge_and_remove_worktree`，`harness/subagent_worktree.py`），
  不做任何自动冲突解决，和"不擅自处理未审查改动"的原则一致。
- 用户拒绝，或合并成功后，worktree 生命周期和第 2 点原有决策衔接：拒绝则
  worktree 照旧保留等下次处理；合并成功后 `merge_worktree` 自己负责
  `git worktree remove`，不再需要单独的 `cleanup_worktree` 工具。

### 更新记录（2026-07-14）：合并冲突时，确定性地建议派一个 integrator subagent

`merge_worktree` 遇到真实内容冲突（`git merge --no-ff` 非零退出，不是
超时/进程启动失败之类的管道故障）时，只报告一句错误文本不够——"怎么解决冲突"
需要模型去理解双方改动、给出方案，这是内容生成任务，没法像"要不要合并"那样
靠结构性暂停强制保证发生。但"要不要委托一个 subagent 去处理这个冲突"仍然是
一个二选一，可以复用同一套机制。

`harness/subagent_worktree.py` 新增 `MergeConflictError(SubagentWorktreeError)`，
只在真实冲突时抛出（携带 `worktree`/`branch`），和"合并命令本身跑不起来"这类
纯管道故障区分开。`MergeWorktreeTool.execute()` 捕获到 `MergeConflictError` 时，
在返回的 `ToolResult` 上再设置一次 `follow_up`——这次目标工具是 `subagent`
（`capability="write"`），prompt 里带上冲突分支名和被保留的 worktree 路径，
让被派去的 subagent 能在自己的隔离 worktree 里 `git diff`/检查冲突、解决、提交。
`subagent` 工具本身早已是 `requires_approval=True`，所以这个建议一样会经过
`NEEDS_APPROVAL` 暂停，用户批准后才会真的派出这个"integrator"——没有引入任何
新组件或新机制，`follow_up` 字段单纯多了第二个生产者（`subagent` 和现在的
`merge_worktree` 冲突分支）。这个 integrator 自己产生的改动一样会命中
`worktree_kept=True` → 一样触发 `merge_worktree` 确定性暂停，整条链路对它自动
生效。

顺带修了一个真实的既有 gap：`format_approval_message` 的通用兜底预览
（`events/emitter.py` 的 `_tool_arg_preview`）此前只认 `path`/`pattern`/`target`/
`url`/`command` 几个参数键，`subagent` 调用的 `prompt` 从未被显示过——用户批准
一次 `subagent`（无论是最初的委派还是这次的 integrator 建议）时看不到具体在
委派什么。加了 `prompt` 进这个键列表。

### 更新记录（2026-07-18）：Worktree 提供子 Workspace，Sandbox 与其组合

最初实现把 read-only Harness、write Harness、Worktree、Container Sandbox 和 git
mount 全部内联在 `AgentSession` 的资源启动方法中。功能正确，但让 composition root
知道了每一种 nested Run 的实现细节，并重复传递 model、Checkpoint、EventHub、
context、token counter 和 retry 配置。新增 Run 变体时，这些装配参数和生命周期很
容易分叉。

补充并澄清以下关系：

```text
parent Run / parent Workspace
  → provision child Workspace (git worktree)
  → compose a Sandbox for that child Workspace
  → assemble and run the nested Harness
  → close Sandbox resources
  → finalize or preserve the worktree
```

- 父子关系属于 **Run / Workspace**。write nested Run 的 Workspace 由 git worktree
  提供；read-only nested Run 则复用父 Workspace。
- Worktree 不是 Sandbox 的上级、下级或另一种 Sandbox。它是提供子 Workspace 的
  具体机制；Sandbox 是绑定到该 Workspace 的执行策略，两者在 nested Run 的装配点
  组合。
- `HarnessAssembly` 冻结一个 `AgentSession` 内所有 Harness 共享的装配原料；不同
  Run 变体只选择 Tool registry、approval 模式和必要时的 Sandbox factory。
- `HarnessRuntime` 是 `AgentSession` 持有的 foreground Harness 与可热更新 Tool
  registry；它不暴露仅供测试使用的 nested runner。
- `SubagentRuntime` 实现 `SubagentRunner` interface，完整拥有 read-only/write
  nested Run 的配置校验、Worktree 创建、Container Sandbox + git mount、临时
  Harness、Sandbox 关闭和 Worktree 收尾。`AgentSession` 不再知道这些细节。
- write nested Run 被取消或抛出异常时，仍先关闭其 Container Sandbox；干净
  Worktree 被移除，有未审查改动的 Worktree 被提交并保留，原始异常不被清理失败
  覆盖。

这个调整不改变本 ADR 已有的用户行为：write 仍强制 Container Sandbox，nested
Run 仍不能递归，Checkpoint/生命周期 signal 仍共享，产生改动后仍确定性触发
`merge_worktree` approval follow-up。

## 参见

- `core/sandbox.py`、`adapters/local/sandbox.py`、`adapters/docker/sandbox.py`
  —— 本设计原样复用的 Sandbox seam。
- `harness/harness.py` —— `AgentHarness.run`，嵌套 Run 复用的形状。
- `core/runtime/assemble.py`、`core/runtime/subagent.py` —— 共享 Harness 装配与
  nested Run composition；`AgentSession` 只持有装配结果和 session 资源生命周期。
- `events/loop.py`（`AgentLoop._execute_decided_batch`）、`harness/state.py`
  （`append_synthetic_tool_call`）、`harness/tools/builtins/merge_worktree.py`
  —— 上面"更新记录"里合并确认机制的实现。
