# Keep Skills declarative and non-executable

Skills will be progressively loaded `SKILL.md` instruction bundles, not Python plugins or containers for custom Tools. This preserves a clear security and extension boundary: repository content may influence model behavior, but it cannot gain in-process execution merely by being discovered as a Skill.

## Consequences

The model initially receives only Skill names and descriptions, then uses the `load_skill` Control Tool to request full content. Project Skills override same-named user Skills, cannot weaken system policy, and are loaded at most once per Run. Package installation, dependency resolution, marketplaces, and Skill-defined executable Tools remain outside the MVP.

---

# 保持 Skill 的声明式与不可执行性

Skill 将是渐进加载的 `SKILL.md` 指令包，而不是 Python 插件或自定义 Tool 的容器。这样可以维持清晰的安全与扩展边界：仓库内容可以影响模型行为，但不能仅因被识别为 Skill 就获得进程内执行能力。

## 影响

模型最初只接收 Skill 的名称和描述，之后通过 `load_skill` Control Tool 请求完整内容。项目级 Skill 覆盖同名用户级 Skill，不能削弱系统策略，并且每个 Skill 在一次 Run 中最多加载一次。包安装、依赖解析、市场以及由 Skill 定义的可执行 Tool 不属于 MVP。
