# Account tokens as cumulative and context

A Run reports token usage as two distinct numbers rather than a single sum, because the two answer different questions and a naive sum is wrong for both.

A chat-completions Run re-sends the entire conversation on every model call, so each call's `input_tokens` already contains all prior turns. Summing `input_tokens` across calls therefore double-counts the conversation N times — it is neither what the provider bills nor the current context size.

We model usage with frozen value types in `domain.py`:

- **`TokenUsage`** — one model call: `input_tokens`, `output_tokens`, plus `cached_tokens` (subset of input served from the provider's prompt cache) and `reasoning_tokens` (subset of output spent on hidden reasoning). `total_tokens = input + output`; `recorded` is true only when the provider actually reported usage.
- **`RunUsage`** — accumulated across a Run: `cumulative` (the additive sum of every call's `TokenUsage` — what the Run is **billed** for) and `context_tokens` (the most recent call's `input_tokens` — the live conversation **footprint**, which is what matters for context-window pressure).

The Harness owns the accumulation (it owns the loop and builds `RunResult`) and exposes `RunUsage` on `RunResult`; the live UI counter is an `AfterModel` Handler (per ADR-0004), so both consume the same authoritative numbers.

## Consequences

`cumulative` is the basis for any future cost calculation; `context_tokens` is the basis for any future context-window guard. `cached_tokens` and `reasoning_tokens` are captured now even though nothing prices them yet, because the provider only reports them at call time and they are essential for accurate cost later.

Usage is reported only when `recorded` is true. Providers reached through an OpenAI-compatible `base_url` have stream usage disabled (many gateways reject `stream_options`), so they return no usage; the UI then shows **nothing** rather than a misleading zero. We deliberately do not estimate counts client-side (e.g. tiktoken), since its OpenAI-specific encodings would be inaccurate for other models — pre-flight estimation is a separate decision if it is ever needed.

When cost estimation is added, per-model pricing will come from the workspace `.milky-frog/config.toml` (a committable `[pricing."<model>"]` table of per-1M input/output/cached rates) rather than a hardcoded table that goes stale.

---

# 将 token 计为累计量与上下文量

一个 Run 以两个独立数值报告 token 用量，而非单一求和：二者回答不同的问题，而朴素求和对两者都是错的。

chat-completions 的 Run 在每次模型调用时都会重新发送整段对话，因此每次调用的 `input_tokens` 已经包含此前所有轮次。把各次调用的 `input_tokens` 相加会把对话重复计入 N 次——这既不是 provider 的计费量，也不是当前的上下文大小。

我们在 `domain.py` 中用 frozen 值类型建模：

- **`TokenUsage`**——单次模型调用：`input_tokens`、`output_tokens`，以及 `cached_tokens`（输入中由 provider prompt 缓存命中的部分）和 `reasoning_tokens`（输出中用于隐藏推理的部分）。`total_tokens = input + output`；只有 provider 真正报告了用量时 `recorded` 才为真。
- **`RunUsage`**——跨 Run 累计：`cumulative`（各次调用 `TokenUsage` 的相加之和——Run 实际**被计费**的量）与 `context_tokens`（最近一次调用的 `input_tokens`——对话的实时**占用量**，即对上下文窗口压力真正重要的数值）。

由 Harness 负责累计（它持有循环并构造 `RunResult`），并在 `RunResult` 上暴露 `RunUsage`；实时 UI 计数器是一个 `AfterModel` Handler（依 ADR-0004），二者消费同一份权威数据。

## 影响

`cumulative` 是未来成本计算的依据；`context_tokens` 是未来上下文窗口保护的依据。`cached_tokens` 与 `reasoning_tokens` 现在即予采集——尽管尚无定价使用它们——因为 provider 仅在调用时报告它们，且它们对日后精确计费至关重要。

仅当 `recorded` 为真时才报告用量。经由 OpenAI 兼容 `base_url` 接入的 provider 关闭了 stream usage（许多网关拒绝 `stream_options`），因而不返回用量；此时 UI 显示**空白**而非误导性的零。我们刻意不在客户端估算（如 tiktoken），因为其 OpenAI 专有编码对其他模型并不准确——若确有需要，预发送估算是另一项独立决策。

当加入成本估算时，按模型的定价将来自工作区 `.milky-frog/config.toml`（可提交的 `[pricing."<model>"]` 表，按每 1M input/output/cached 费率），而非会过时的硬编码表。
