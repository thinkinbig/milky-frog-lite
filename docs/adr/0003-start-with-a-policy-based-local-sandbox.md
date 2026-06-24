# Start with a policy-based local sandbox

The MVP will provide a Local Sandbox based on workspace path validation, sensitive-file exclusions, environment filtering, and per-command approval instead of claiming host-level isolation. A Python subprocess cannot reliably prevent an arbitrary shell command from reading host files or using the network, while requiring Docker or an OS-specific isolation backend would materially increase installation and platform complexity.

## Consequences

Structured file Tools must remain inside the resolved Workspace and reject symlink escapes; shell commands always require explicit approval and receive no model API credentials. The product must state that it cannot safely execute untrusted code. A future Container Sandbox may provide hard isolation behind a separate implementation rather than silently changing the Local Sandbox guarantee.

**Implementation (ADR-0016):** the policy is enforced through `LocalExecutionBackend` (`harness/execution_backend.py`), injected via `backend_factory` on `AgentHarness` / `AgentSession`.

---

# 从策略型 Local Sandbox 开始

MVP 将基于工作区路径校验、敏感文件排除、环境变量过滤和逐条命令授权实现 Local Sandbox，而不宣称提供宿主机级隔离。Python 子进程无法可靠阻止任意 shell 命令读取宿主机文件或访问网络；要求 Docker 或特定操作系统的隔离后端，则会显著增加安装与平台复杂度。

## 影响

结构化文件 Tool 必须限制在解析后的 Workspace 内，并拒绝通过符号链接逃逸；shell 命令始终需要显式批准，且不会获得模型 API 凭据。产品必须明确说明它不能安全执行不可信代码。未来可以通过独立实现增加提供硬隔离的 Container Sandbox，而不是悄然改变 Local Sandbox 的安全承诺。

**实现（ADR-0016）：** 策略由 `LocalExecutionBackend`（`harness/execution_backend.py`）执行，通过 `AgentHarness` / `AgentSession` 的 `backend_factory` 注入。
